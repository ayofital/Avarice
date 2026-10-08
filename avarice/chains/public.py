"""Read-only public market observations; no credentials or execution APIs."""
from datetime import datetime
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import json
import math
import queue
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from avarice.core.models import Chain, Pool


class ProviderError(Exception):
    """A provider boundary failure; never a fabricated market observation."""

    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        # Even same-host redirects would bypass global pacing/deadlines.
        return None


class HttpClient:
    """Anonymous GETs with process-global pacing and a bounded scan lifetime.

    Create one client per scan and share it across chain adapters. The deadline
    starts at construction, never renews per request, and is capped at 150s.
    At most three daemon workers can linger in uninterruptible OS network calls;
    caller deadlines remain bounded and late responses are closed, not returned.
    The optional transport/clock hooks are for deterministic offline tests.
    """

    ALLOWED_HOSTS = frozenset({"api.geckoterminal.com", "api.dexscreener.com"})
    MAX_RESPONSE_BYTES = 2097152
    MAX_ATTEMPTS = 3
    _request_slots = threading.BoundedSemaphore(3)
    _rate_lock = threading.Lock()
    _last_started = None
    _last_interval = 0.0

    def __init__(self, settings, *, opener=None, clock=None, sleeper=None):
        try:
            self.timeout = _number(settings.http_timeout_seconds, "http_timeout_seconds")
            self.interval = _number(settings.http_min_interval_seconds, "http_min_interval_seconds")
            self.budget = _number(settings.scan_budget_seconds, "scan_budget_seconds")
            if min(self.timeout, self.interval, self.budget) <= 0:
                raise ValueError("HTTP limits must be positive")
        except (AttributeError, ValueError, TypeError, OverflowError) as exc:
            raise ProviderError(str(exc)) from exc
        self.errors = []
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleeper if sleeper is not None else time.sleep
        self.budget = min(self.budget, 150.0)
        self.deadline = self._clock() + self.budget
        self._opener = opener if opener is not None else build_opener(ProxyHandler({}), _NoRedirect())

    def get(self, url) -> dict | list:
        """Return real provider JSON or raise ProviderError; never invent data."""
        self._validate_url(url)
        for attempt in range(self.MAX_ATTEMPTS):
            retry_after = 0.0
            try:
                return self._get_once(url)
            except HTTPError as exc:
                status = exc.code
                retry_after = self._retry_after(exc.headers.get("Retry-After") if exc.headers else None)
                exc.close()
                message = f"public provider HTTP {status}"
                if status not in (408, 429, 500, 502, 503, 504) or attempt == self.MAX_ATTEMPTS - 1:
                    raise ProviderError(message, status_code=status) from exc
            except (URLError, OSError, HTTPException) as exc:
                message = f"public provider transport failure: {exc}"
                if attempt == self.MAX_ATTEMPTS - 1:
                    raise ProviderError(message) from exc
            self.errors.append(message + f"; retry {attempt + 1}")
            delay = max(2.0 * 2 ** attempt, retry_after)
            if delay >= self._remaining():
                raise ProviderError("scan budget exhausted by retry backoff")
            self._sleep(delay)
        raise ProviderError("public provider retry limit reached")

    def _retry_after(self, value):
        if value is None:
            return 0.0
        try:
            return _number(value, "Retry-After")
        except (ValueError, TypeError, OverflowError):
            try:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    return 0.0
                return max(0.0, parsed.timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                return 0.0

    def _get_once(self, url):
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "Avarice/0.1 public-read-only"})
        request_deadline = min(self.deadline, self._clock() + self.timeout)
        slots = HttpClient._request_slots
        if not slots.acquire(timeout=self._request_remaining(request_deadline)):
            raise TimeoutError("HTTP worker capacity unavailable before deadline")
        completed = queue.Queue(maxsize=1)

        def work():
            try:
                completed.put_nowait((True, self._read_response(request, request_deadline)))
            except Exception as exc:
                # Close HTTP error bodies even if the caller's deadline has passed.
                if isinstance(exc, HTTPError):
                    exc.close()
                completed.put_nowait((False, exc))
            finally:
                slots.release()

        # A daemon bounds caller latency even when the OS resolver ignores timeouts.
        # Late responses still enter the context manager and are closed on expiry.
        try:
            threading.Thread(target=work, name="avarice-public-http", daemon=True).start()
        except RuntimeError as exc:
            slots.release()
            raise ProviderError("could not start bounded public HTTP worker") from exc
        try:
            success, result = completed.get(timeout=self._request_remaining(request_deadline))
        except queue.Empty as exc:
            raise TimeoutError("HTTP request deadline exhausted while provider blocked") from exc
        if not success:
            raise result
        self._request_remaining(request_deadline)
        return result

    def _read_response(self, request, request_deadline):
        self._pace(request_deadline)
        try:
            with self._opener.open(request, timeout=self._request_remaining(request_deadline)) as response:
                if response.status != 200:
                    raise HTTPError(request.full_url, response.status, "non-success provider response", response.headers, None)
                length = response.headers.get("Content-Length")
                if length is not None:
                    if int(length) < 0:
                        raise ValueError("negative Content-Length")
                    if int(length) > self.MAX_RESPONSE_BYTES:
                        raise ProviderError("response size exceeds byte limit")
                body = self._read_body(response, request_deadline)
                result = json.loads(body, parse_float=lambda value: _number(value, "JSON number", signed=True),
                                    parse_constant=lambda value: _number(value, "JSON constant"))
                if not isinstance(result, (dict, list)):
                    raise ValueError("JSON root must be an object or array")
                self._remaining()
                self._request_remaining(request_deadline)
                return result
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ProviderError("invalid provider JSON or response metadata: " + str(exc)) from exc

    def _request_remaining(self, request_deadline):
        remaining = min(self._remaining(), request_deadline - self._clock())
        if remaining <= 0:
            raise TimeoutError("HTTP request deadline exhausted")
        return remaining

    def _read_body(self, response, request_deadline):
        body = bytearray()
        read_once = getattr(response, "read1", response.read)
        while True:
            timeout = self._request_remaining(request_deadline)
            # urllib HTTPResponse exposes its socket through the buffered reader.
            sock = getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
            if sock is not None:
                sock.settimeout(timeout)
            chunk = read_once(min(65536, self.MAX_RESPONSE_BYTES + 1 - len(body)))
            self._request_remaining(request_deadline)
            if not chunk:
                return bytes(body)
            body.extend(chunk)
            if len(body) > self.MAX_RESPONSE_BYTES:
                raise ProviderError("response size exceeds byte limit")

    def _remaining(self):
        remaining = self.deadline - self._clock()
        if remaining <= 0:
            raise ProviderError("scan budget/deadline exhausted")
        return remaining

    def _pace(self, request_deadline):
        # Use the base class, not type(self), so subclasses share one limit.
        if not HttpClient._rate_lock.acquire(timeout=self._request_remaining(request_deadline)):
            raise TimeoutError("request deadline exhausted waiting for global rate limit")
        try:
            if HttpClient._last_started is not None:
                wait = HttpClient._last_started + max(self.interval, HttpClient._last_interval) - self._clock()
                if wait > 0:
                    if wait >= self._request_remaining(request_deadline):
                        raise TimeoutError("request deadline exhausted by global request pacing")
                    self._sleep(wait)
            self._request_remaining(request_deadline)
            HttpClient._last_started = self._clock()
            HttpClient._last_interval = self.interval
        finally:
            HttpClient._rate_lock.release()

    def _validate_url(self, url):
        try:
            if not isinstance(url, str) or any(character.isspace() or ord(character) < 32 for character in url):
                raise ValueError("invalid URL characters")
            parts = urlsplit(url)
            if (parts.scheme != "https" or parts.hostname not in self.ALLOWED_HOSTS
                    or parts.username is not None or parts.password is not None
                    or parts.port not in (None, 443) or parts.fragment):
                raise ValueError("only anonymous HTTPS public-provider URLs are allowed")
        except ValueError as exc:
            raise ProviderError(str(exc)) from exc


def _number(value, field, *, signed=False, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{field}: missing or invalid number")
    result = float(value)
    if not math.isfinite(result) or (not signed and result < 0):
        raise ValueError(f"{field}: nonfinite or negative number")
    if integer and not result.is_integer():
        raise ValueError(f"{field}: not an integer")
    return int(result) if integer else result


class PublicMarketAdapter:
    """GeckoTerminal pools and recent observed swaps, not wallet history/safety.

    Discovery reads the first new-pools page only. DexScreener token profiles
    are intentionally not treated as newly created pairs or fallback launches.
    Malformed rows are excluded with provider/chain diagnostics in errors.
    """

    def __init__(self, chain: Chain, client):
        self.chain = chain
        self.client = client
        self.network = "eth" if chain is Chain.ETHEREUM else chain.value
        self.base_url = "https://api.geckoterminal.com/api/v2/networks/" + self.network
        self.errors = client.errors

    def new_pools(self) -> list[Pool]:
        """Return valid pools with actual provider birth dates, never inferred ones."""
        document = self.client.get(self.base_url + "/new_pools?include=base_token,quote_token,dex")
        rows = self._document_data(document, many=True)
        included = self._included(document)
        pools = []
        seen = set()
        for index, row in enumerate(rows):
            try:
                pool = self._parse_pool(row, included)
                if pool.created_at is None:
                    raise ValueError("missing or invalid pool_created_at; not a new launch")
                identity = pool.address if self.chain is Chain.SOLANA else pool.address.lower()
                if identity not in seen:
                    seen.add(identity)
                    pools.append(pool)
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
                self.errors.append(f"geckoterminal {self.chain.value} pool row {index}: {exc}")
        return pools

    def _document_data(self, document, *, many):
        if not isinstance(document, dict) or "data" not in document or document.get("errors"):
            raise ProviderError(f"geckoterminal {self.chain.value}: invalid provider envelope")
        data = document["data"]
        if (many and not isinstance(data, list)) or (not many and data is not None and not isinstance(data, dict)):
            raise ProviderError(f"geckoterminal {self.chain.value}: invalid data shape")
        return data

    def _included(self, document):
        included = document.get("included", [])
        if not isinstance(included, list):
            raise ProviderError(f"geckoterminal {self.chain.value}: invalid included resources")
        try:
            return {item["id"]: item for item in included}
        except (TypeError, KeyError) as exc:
            raise ProviderError("geckoterminal: malformed included resources") from exc

    def _parse_pool(self, row, included) -> Pool:
        if row["type"] != "pool":
            raise ValueError("resource is not a pool")
        attrs = row["attributes"]
        self._validate_address(attrs["address"], pool=True)
        if not self._same_address(row["id"], self.network + "_" + attrs["address"]):
            raise ValueError("pool network/address identity mismatch")
        relationships = row["relationships"]
        base = self._token(relationships["base_token"]["data"], included)
        quote = self._token(relationships["quote_token"]["data"], included)
        transactions = attrs.get("transactions", {}).get("m5", {})
        created = attrs.get("pool_created_at")
        try:
            parsed = datetime.fromisoformat(created)
            if parsed.tzinfo is None:
                created = None
        except (TypeError, ValueError):
            created = None
        return Pool(
            chain=self.chain, address=attrs["address"], token_address=base["address"],
            quote_address=quote["address"], symbol=base.get("symbol", "UNK"),
            price_usd=_number(attrs.get("base_token_price_usd"), "price"),
            liquidity_usd=_number(attrs.get("reserve_in_usd"), "liquidity"),
            volume_24h_usd=_number(attrs.get("volume_usd", {}).get("h24", 0), "volume"),
            created_at=created, source="geckoterminal",
            dex=relationships.get("dex", {}).get("data", {}).get("id", "unknown"),
            buys_5m=_number(transactions.get("buys", 0), "buys", integer=True),
            sells_5m=_number(transactions.get("sells", 0), "sells", integer=True),
            price_change_5m_pct=_number(attrs.get("price_change_percentage", {}).get("m5", 0), "price change", signed=True),
            safety_status="unverified",
        )

    def _same_address(self, first, second):
        return first == second if self.chain is Chain.SOLANA else first.lower() == second.lower()

    def _validate_address(self, address, *, pool=False):
        pattern = r"[1-9A-HJ-NP-Za-km-z]{32,44}" if self.chain is Chain.SOLANA else (
            r"0x(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})" if pool else r"0x[0-9a-fA-F]{40}"
        )
        if not isinstance(address, str) or re.fullmatch(pattern, address) is None:
            raise ValueError("invalid pool address" if pool else "invalid token/wallet address")

    def _token(self, relationship, included):
        resource = included[relationship["id"]]
        if relationship.get("type") != "token" or resource.get("type") != "token":
            raise ValueError("non-token relationship")
        attrs = resource["attributes"]
        self._validate_address(attrs["address"])
        if not self._same_address(relationship["id"], self.network + "_" + attrs["address"]):
            raise ValueError("token network/address identity mismatch")
        return attrs

    def trades(self, pool: Pool) -> list[dict]:
        """Normalize only swaps affecting the tracked token; dedupe by event ID."""
        try:
            if pool.chain != self.chain:
                raise ValueError("trade pool belongs to another chain")
            self._validate_address(pool.address, pool=True)
            self._validate_address(pool.token_address)
            self._validate_address(pool.quote_address)
        except (AttributeError, ValueError) as exc:
            raise ProviderError(str(exc)) from exc
        document = self.client.get(self.base_url + "/pools/" + pool.address + "/trades")
        rows = self._document_data(document, many=True)
        trades = []
        seen = set()
        for index, row in enumerate(rows):
            try:
                event = self._parse_trade(row, pool)
                if event["id"] not in seen:
                    seen.add(event["id"])
                    trades.append(event)
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
                self.errors.append(f"geckoterminal {self.chain.value} trade row {index}: {exc}")
        return trades

    def _parse_trade(self, row, pool):
        if row["type"] != "trade":
            raise ValueError("resource is not a trade")
        attrs = row["attributes"]
        for field in ("tx_from_address", "from_token_address", "to_token_address"):
            self._validate_address(attrs[field])
        bought = self._same_address(attrs["to_token_address"], pool.token_address)
        sold = self._same_address(attrs["from_token_address"], pool.token_address)
        if bought == sold:
            raise ValueError("trade does not unambiguously affect the tracked token")
        if attrs.get("kind") not in ("buy", "sell"):
            raise ValueError("not a swap trade")
        for value in (row["id"], attrs["tx_hash"]):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("missing event/transaction identifier")
        if datetime.fromisoformat(attrs["block_timestamp"]).tzinfo is None:
            raise ValueError("trade timestamp must have a timezone")
        side = "buy" if bought else "sell"
        leg = "to" if side == "buy" else "from"
        quantity = _number(attrs[leg + "_token_amount"], "quantity")
        price = _number(attrs["price_" + leg + "_in_usd"], "trade price")
        if quantity <= 0 or price <= 0:
            raise ValueError("trade quantity/price must be positive")
        return {
            "id": row["id"], "timestamp": attrs["block_timestamp"], "tx_hash": attrs["tx_hash"],
            "wallet": attrs["tx_from_address"], "chain": self.chain.value,
            "token_address": pool.token_address, "pool_address": pool.address, "side": side,
            "quantity": quantity,
            "usd_value": _number(attrs["volume_in_usd"], "trade value"),
            "price_usd": price,
            "provider": "geckoterminal",
        }

    def pool(self, address: str) -> Pool | None:
        """Quote this exact chain/pool; missing or invalid quotes stay unavailable."""
        try:
            self._validate_address(address, pool=True)
        except ValueError as exc:
            raise ProviderError(str(exc)) from exc
        try:
            document = self.client.get(self.base_url + "/pools/" + address + "?include=base_token,quote_token,dex")
        except ProviderError as exc:
            if exc.status_code != 404:
                raise
            self.errors.append(f"geckoterminal {self.chain.value} pool {address}: HTTP 404, quote unavailable")
            return None
        row = self._document_data(document, many=False)
        if row is None:
            return None
        included = self._included(document)
        try:
            result = self._parse_pool(row, included)
            if not self._same_address(result.address, address):
                raise ValueError("requested pool address mismatch")
            return result
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            self.errors.append(f"geckoterminal {self.chain.value} pool {address}: {exc}")
            return None
