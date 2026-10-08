"""Public-provider contracts using explicitly synthetic fixtures, never live data."""
import copy
import importlib
import io
import json
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from email.message import Message
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

from avarice.core.models import Chain


def synthetic_pool_document(chain=Chain.SOLANA, address=None):
    network = "eth" if chain is Chain.ETHEREUM else chain.value
    evm = chain is not Chain.SOLANA
    address = address or ("0x" + "Ab" * 20 if evm else "B" * 43)
    token = "0x" + "Cd" * 20 if evm else "C" * 43
    quote = "0x" + "Ef" * 20 if evm else "D" * 43
    base_id, quote_id = network + "_" + token, network + "_" + quote
    return {
        "data": [{
            "id": network + "_" + address, "type": "pool",
            "attributes": {
                "address": address, "name": "SYN / QUOTE",
                "base_token_price_usd": "1.25", "reserve_in_usd": "1200.0",
                "volume_usd": {"h24": "2500.0"},
                "pool_created_at": "2026-10-08T09:00:00Z",
                "transactions": {"m5": {"buys": 7, "sells": 3}},
                "price_change_percentage": {"m5": "-1.5"},
            },
            "relationships": {
                "base_token": {"data": {"id": base_id, "type": "token"}},
                "quote_token": {"data": {"id": quote_id, "type": "token"}},
                "dex": {"data": {"id": "synthetic-dex", "type": "dex"}},
            },
        }],
        "included": [
            {"id": base_id, "type": "token", "attributes": {"address": token, "symbol": "SYN"}},
            {"id": quote_id, "type": "token", "attributes": {"address": quote, "symbol": "QUOTE"}},
            {"id": "synthetic-dex", "type": "dex", "attributes": {"name": "Synthetic DEX"}},
        ],
    }


class SyntheticClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.urls = []
        self.errors = []

    def get(self, url):
        self.urls.append(url)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return copy.deepcopy(result)


def public_module(test):
    try:
        return importlib.import_module("avarice.chains.public")
    except ImportError as exc:
        test.fail("Missing importable stdlib public adapter: " + str(exc))


def synthetic_trade(pool, event_id="synthetic-event-1", side="buy"):
    return {
        "id": event_id, "type": "trade",
        "attributes": {
            "tx_hash": "synthetic-transaction", "block_timestamp": "2026-10-08T09:15:00Z",
            "tx_from_address": "W" * 43 if pool.chain is Chain.SOLANA else "0x" + "aB" * 20,
            "kind": side,
            "from_token_address": pool.quote_address if side == "buy" else pool.token_address,
            "to_token_address": pool.token_address if side == "buy" else pool.quote_address,
            "from_token_amount": "100" if side == "buy" else "80",
            "to_token_amount": "80" if side == "buy" else "100",
            "price_from_in_usd": "1" if side == "buy" else "1.25",
            "price_to_in_usd": "1.25" if side == "buy" else "1",
            "volume_in_usd": "100",
        },
    }


class SyntheticResponse(io.BytesIO):
    def __init__(self, body=b"{}", headers=None, status=200):
        super().__init__(body)
        self.headers = headers or {}
        self.status = status


class SyntheticOpener:
    def __init__(self, *responses, clock=None):
        self.responses = list(responses)
        self.calls = []
        self.clock = clock

    def open(self, request, timeout):
        self.calls.append((request, timeout, self.clock() if self.clock else None))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SyntheticClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def http_settings(**values):
    return SimpleNamespace(**{
        "http_timeout_seconds": 10.0, "http_min_interval_seconds": 2.2,
        "scan_budget_seconds": 150.0, **values,
    })


class HttpContractTests(unittest.TestCase):
    def setUp(self):
        module = public_module(self)
        if hasattr(module, "HttpClient"):
            reset = patch.multiple(module.HttpClient, _last_started=None, _last_interval=0.0, create=True)
            reset.start()
            self.addCleanup(reset.stop)

    def test_http_pacing_occurs_when_worker_starts_not_when_it_is_scheduled(self):
        module = public_module(self)
        clock = SyntheticClock()
        opener = SyntheticOpener(SyntheticResponse(), SyntheticResponse(), clock=clock)
        one = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
        two = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
        real_thread = threading.Thread
        waiting = threading.Event()
        release = threading.Event()
        counter = {"created": 0}
        errors = []
        def delayed_factory(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            counter["created"] += 1
            if counter["created"] == 1:
                start = worker.start
                def delayed_start():
                    waiting.set()
                    release.wait(timeout=1.0)
                    start()
                worker.start = delayed_start
            return worker
        def first_caller():
            try:
                one.get("https://api.geckoterminal.com/api/v2/networks")
            except Exception as exc:
                errors.append(exc)
        caller = real_thread(target=first_caller)
        with patch.object(module.threading, "Thread", side_effect=delayed_factory):
            caller.start()
            try:
                self.assertTrue(waiting.wait(timeout=1.0))
                self.assertEqual(two.get("https://api.dexscreener.com/token-profiles/latest/v1"), {})
            finally:
                release.set()
                caller.join(timeout=1.0)
        self.assertFalse(caller.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(opener.calls), 2)
        self.assertAlmostEqual(opener.calls[1][2] - opener.calls[0][2], 2.2)

    def test_http_timed_out_workers_are_globally_bounded(self):
        module = public_module(self)
        release = threading.Event()
        finished = threading.Condition()
        state = {"calls": 0, "closed": 0}
        class SyntheticLingeringResponse(SyntheticResponse):
            def close(self):
                if not self.closed:
                    super().close()
                    with finished:
                        state["closed"] += 1
                        finished.notify_all()
        class SyntheticLingeringOpener:
            def open(self, request, timeout):
                with finished:
                    state["calls"] += 1
                release.wait(timeout=2.0)
                return SyntheticLingeringResponse()
        try:
            for _ in range(4):
                client = module.HttpClient(http_settings(http_timeout_seconds=0.02, scan_budget_seconds=0.03, http_min_interval_seconds=0.001), opener=SyntheticLingeringOpener())
                with self.assertRaises(module.ProviderError):
                    client.get("https://api.geckoterminal.com/api/v2/networks")
            self.assertEqual(state["calls"], 3, "More than three lingering provider workers were started")
        finally:
            release.set()
            with finished:
                self.assertTrue(finished.wait_for(lambda: state["closed"] == state["calls"], timeout=1.0))

    def test_http_deadline_bounds_blocking_dns_or_headers_and_closes_late_response(self):
        module = public_module(self)
        release = threading.Event()
        closed = threading.Event()
        class SyntheticLateResponse(SyntheticResponse):
            def close(self):
                super().close()
                closed.set()
        response = SyntheticLateResponse()
        class SyntheticBlockedOpener:
            def __init__(self):
                self.calls = 0
            def open(self, request, timeout):
                self.calls += 1
                release.wait(timeout=1.0)  # An OS resolver can ignore socket timeouts.
                return response
        opener = SyntheticBlockedOpener()
        client = module.HttpClient(http_settings(http_timeout_seconds=0.02, scan_budget_seconds=0.03), opener=opener)
        started = time.monotonic()
        try:
            with self.assertRaises(module.ProviderError):
                client.get("https://api.geckoterminal.com/api/v2/networks")
            self.assertLess(time.monotonic() - started, 0.3)
            self.assertEqual(opener.calls, 1)
        finally:
            release.set()
            self.assertTrue(closed.wait(timeout=1.0), "Late response was not closed")

    def test_http_streaming_reads_refresh_socket_timeout_to_scan_deadline(self):
        module = public_module(self)
        clock = SyntheticClock()
        timeouts = []
        class SyntheticSocket:
            def settimeout(self, seconds):
                timeouts.append(seconds)
        class SyntheticDrippingResponse(SyntheticResponse):
            def __init__(self):
                super().__init__(b"{}")
                self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SyntheticSocket()))
                self.chunks = [b"{", b"}", b""]
            def read(self, size=-1):
                # One large buffered read would overrun an entire scan budget.
                clock.now += 9
                return b"{}"
            def read1(self, size=-1):
                self.assert_bounded_size(size)
                delay = min(3.0, timeouts[-1] if timeouts else 3.0)
                clock.now += delay
                if delay < 3:
                    raise TimeoutError("synthetic slow stream")
                return self.chunks.pop(0)
            def assert_bounded_size(self, size):
                if not 0 < size <= 65536:
                    raise AssertionError("unbounded streaming read")
        response = SyntheticDrippingResponse()
        opener = SyntheticOpener(response, clock=clock)
        client = module.HttpClient(http_settings(scan_budget_seconds=5), opener=opener, clock=clock, sleeper=clock.sleep)
        with self.assertRaises(module.ProviderError):
            client.get("https://api.geckoterminal.com/api/v2/networks")
        self.assertLessEqual(clock.now, 1005.0)
        self.assertEqual(timeouts, [5.0, 2.0])
        self.assertTrue(response.closed)
        self.assertEqual(len(opener.calls), 1)

    def test_http_ignores_ambient_proxy_credentials(self):
        module = public_module(self)
        requests = []
        class SyntheticAnonymousTransport(HTTPSHandler):
            def https_open(self, request):
                requests.append(request)
                response = addinfourl(io.BytesIO(b"{}"), Message(), request.full_url, code=200)
                response.msg = "Synthetic public response"
                return response
        with patch("urllib.request.getproxies", return_value={"https": "http://synthetic-user:synthetic-secret@proxy.invalid:8080"}), patch.object(module, "build_opener", side_effect=lambda *handlers: build_opener(*handlers, SyntheticAnonymousTransport())):
            clock = SyntheticClock()
            client = module.HttpClient(http_settings(), clock=clock, sleeper=clock.sleep)
            self.assertEqual(client.get("https://api.geckoterminal.com/api/v2/networks"), {})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].host, "api.geckoterminal.com")
        self.assertFalse(requests[0].has_header("Proxy-authorization"))
        self.assertFalse(requests[0].has_header("Authorization"))
        self.assertFalse(requests[0].has_header("Cookie"))

    def test_real_urllib_redirects_cannot_escape_allowlist_or_pacing(self):
        module = public_module(self)
        for target in ("https://evil.invalid/", "https://api.geckoterminal.com/other"):
            with self.subTest(target=target), patch.object(module.HttpClient, "_last_started", None):
                requests = []
                class SyntheticRedirectTransport(HTTPSHandler):
                    def https_open(self, request):
                        requests.append(request.full_url)
                        headers = Message()
                        status = 302 if len(requests) == 1 else 200
                        if status == 302:
                            headers["Location"] = target
                        response = addinfourl(io.BytesIO(b"{}"), headers, request.full_url, code=status)
                        response.msg = "Synthetic redirect fixture"
                        return response
                with patch.object(module, "build_opener", side_effect=lambda *handlers: build_opener(*handlers, SyntheticRedirectTransport())):
                    clock = SyntheticClock()
                    client = module.HttpClient(http_settings(), clock=clock, sleeper=clock.sleep)
                    with self.assertRaises(module.ProviderError) as raised:
                        client.get("https://api.geckoterminal.com/api/v2/networks")
                    self.assertEqual(raised.exception.status_code, 302)
                    self.assertEqual(requests, ["https://api.geckoterminal.com/api/v2/networks"])

    def test_http_non_success_status_is_never_parsed_as_a_quote(self):
        module = public_module(self)
        for status in (201, 302, 400, 401, 403, 404, 501):
            with self.subTest(status=status), patch.object(module.HttpClient, "_last_started", None):
                clock = SyntheticClock()
                response = SyntheticResponse(b"{}", status=status)
                opener = SyntheticOpener(response, clock=clock)
                client = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
                with self.assertRaises(module.ProviderError) as raised:
                    client.get("https://api.geckoterminal.com/api/v2/networks")
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(len(opener.calls), 1)
                self.assertTrue(response.closed)

    def test_http_429_honors_retry_after_without_exceeding_deadline(self):
        module = public_module(self)
        dated = format_datetime(datetime.fromtimestamp(1010, timezone.utc), usegmt=True)
        for retry_after, expected in (("7", 7.0), (dated, 10.0), ("NaN", 2.2), ("-1", 2.2)):
            with self.subTest(retry_after=retry_after), patch.object(module.HttpClient, "_last_started", None), patch.object(module.time, "time", return_value=1000):
                clock = SyntheticClock()
                opener = SyntheticOpener(
                    HTTPError("https://api.geckoterminal.com/", 429, "synthetic-rate-limit", {"Retry-After": retry_after}, io.BytesIO()),
                    SyntheticResponse(), clock=clock,
                )
                client = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
                self.assertEqual(client.get("https://api.geckoterminal.com/api/v2/networks"), {})
                self.assertAlmostEqual(opener.calls[1][2] - opener.calls[0][2], expected)
        clock = SyntheticClock()
        opener = SyntheticOpener(HTTPError("https://api.geckoterminal.com/", 429, "synthetic-rate-limit", {"Retry-After": "200"}, io.BytesIO()), clock=clock)
        client = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
        with self.assertRaisesRegex(module.ProviderError, "budget"):
            client.get("https://api.geckoterminal.com/api/v2/networks")
        self.assertEqual(len(opener.calls), 1)
        self.assertTrue(all(delay < 150 for delay in clock.sleeps))

    def test_http_retries_transient_errors_with_bounded_backoff(self):
        module = public_module(self)
        self.assertTrue(hasattr(module.HttpClient, "MAX_ATTEMPTS"), "Missing bounded retries")
        self.assertEqual(module.HttpClient.MAX_ATTEMPTS, 3)
        clock = SyntheticClock()
        failed_body = io.BytesIO(b"synthetic-error")
        opener = SyntheticOpener(
            HTTPError("https://api.geckoterminal.com/", 503, "synthetic-unavailable", {}, failed_body),
            URLError("synthetic-network-timeout"), SyntheticResponse(b'{"recovered": true}'), clock=clock,
        )
        client = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
        try:
            result = client.get("https://api.geckoterminal.com/api/v2/networks")
        except Exception as exc:
            self.fail("Transient error was not retried: " + str(exc))
        self.assertEqual(result, {"recovered": True})
        self.assertTrue(failed_body.closed)
        self.assertEqual(len(opener.calls), 3)
        self.assertGreaterEqual(opener.calls[1][2] - opener.calls[0][2], 2.2 - 1e-9)
        self.assertGreaterEqual(opener.calls[2][2] - opener.calls[1][2], 4.0)
        errors = [TimeoutError("synthetic-timeout") for _ in range(3)]
        exhausted = SyntheticOpener(*errors, clock=clock)
        client = module.HttpClient(http_settings(), opener=exhausted, clock=clock, sleeper=clock.sleep)
        with self.assertRaises(module.ProviderError):
            client.get("https://api.dexscreener.com/token-profiles/latest/v1")
        self.assertEqual(len(exhausted.calls), 3)

    def test_http_rejects_invalid_json_without_fabricating_or_retrying(self):
        module = public_module(self)
        for body, headers in ((b"not-json", {}), (b'{"x": NaN}', {}), (b'{"x": Infinity}', {}),
                              (b'{"x": 1e999}', {}), (b"null", {}), (b'"string"', {}),
                              (b"true", {}), (b"{}", {"Content-Length": "invalid"}),
                              (b"{}", {"Content-Length": "-1"})):
            # Each case is an independent synthetic scan, with its own clock epoch.
            with self.subTest(body=body, headers=headers), patch.object(module.HttpClient, "_last_started", None):
                clock = SyntheticClock()
                response = SyntheticResponse(body, headers=headers)
                opener = SyntheticOpener(response, clock=clock)
                client = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
                with self.assertRaises(Exception) as raised:
                    client.get("https://api.geckoterminal.com/api/v2/networks")
                self.assertIsInstance(raised.exception, module.ProviderError)
                self.assertTrue(response.closed)
                self.assertEqual(len(opener.calls), 1)

    def test_http_response_size_is_bounded_before_and_during_read(self):
        module = public_module(self)
        self.assertTrue(hasattr(module.HttpClient, "MAX_RESPONSE_BYTES"), "Missing response byte limit")
        limit = module.HttpClient.MAX_RESPONSE_BYTES
        self.assertGreater(limit, 0)
        self.assertLessEqual(limit, 2097152)
        for headers in ({"Content-Length": str(limit + 1)}, {}):
            with self.subTest(headers=headers):
                clock = SyntheticClock()
                response = SyntheticResponse(b"{}" + b" " * limit, headers=headers)
                opener = SyntheticOpener(response, clock=clock)
                client = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
                with self.assertRaisesRegex(module.ProviderError, "size"):
                    client.get("https://api.geckoterminal.com/api/v2/networks")
                self.assertTrue(response.closed)
                self.assertEqual(len(opener.calls), 1)

    def test_http_scan_deadline_caps_all_requests_and_pacing_at_150_seconds(self):
        module = public_module(self)
        clock = SyntheticClock()
        opener = SyntheticOpener(SyntheticResponse(), clock=clock)
        client = module.HttpClient(http_settings(scan_budget_seconds=1000), opener=opener, clock=clock, sleeper=clock.sleep)
        self.assertTrue(hasattr(client, "deadline"), "Missing total scan deadline")
        self.assertEqual(client.deadline - clock(), 150.0)
        clock.now += 149.0
        self.assertEqual(client.get("https://api.geckoterminal.com/api/v2/networks"), {})
        self.assertEqual(opener.calls[0][1], 1.0)
        # A request that cannot fit even the shared pacing delay never starts.
        with self.assertRaises(module.ProviderError):
            client.get("https://api.dexscreener.com/token-profiles/latest/v1")
        self.assertEqual(clock.sleeps, [])
        clock.now += 1.0
        with self.assertRaises(module.ProviderError):
            client.get("https://api.geckoterminal.com/api/v2/networks")
        self.assertEqual(len(opener.calls), 1)
        shorter = module.HttpClient(http_settings(scan_budget_seconds=5), opener=SyntheticOpener(), clock=clock, sleeper=clock.sleep)
        self.assertEqual(shorter.deadline - clock(), 5.0)

    def test_http_rate_limit_is_shared_across_clients_and_hosts(self):
        module = public_module(self)
        clock = SyntheticClock()
        opener = SyntheticOpener(SyntheticResponse(), SyntheticResponse(), SyntheticResponse(), clock=clock)
        try:
            one = module.HttpClient(http_settings(), opener=opener, clock=clock, sleeper=clock.sleep)
            two = module.HttpClient(http_settings(http_min_interval_seconds=0.1), opener=opener, clock=clock, sleeper=clock.sleep)
        except TypeError as exc:
            self.fail("No testable shared global request pacing: " + str(exc))
        one.get("https://api.geckoterminal.com/api/v2/networks")
        two.get("https://api.dexscreener.com/token-profiles/latest/v1")
        one.get("https://api.geckoterminal.com/api/v2/networks")
        self.assertEqual(len(opener.calls), 3)
        for earlier, later in zip(opener.calls, opener.calls[1:]):
            self.assertAlmostEqual(later[2] - earlier[2], 2.2)
        self.assertEqual(len(clock.sleeps), 2)

    def test_http_rejects_nonfinite_or_nonpositive_limits(self):
        module = public_module(self)
        for field in ("http_timeout_seconds", "http_min_interval_seconds", "scan_budget_seconds"):
            for value in (float("nan"), float("inf"), 0, -1, True, "not-a-number"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(module.ProviderError):
                        module.HttpClient(http_settings(**{field: value}), opener=SyntheticOpener())

    def test_http_allowlist_rejects_unsafe_urls_before_network_access(self):
        module = public_module(self)
        urls = (
            "http://api.geckoterminal.com/api/v2/networks", "https://localhost/", "file:///synthetic.json",
            "https://api.geckoterminal.com.evil.invalid/", "https://api.dexscreener.com@evil.invalid/",
            "https://user:secret@api.geckoterminal.com/", "https://api.geckoterminal.com:444/",
            "https://api.geckoterminal.com./", "https://api.geckoterminal.com/#fragment",
            " https://api.geckoterminal.com/", "https://api.geckoterminal.com/\npath",
        )
        for url in urls:
            with self.subTest(url=url):
                opener = SyntheticOpener(SyntheticResponse())
                with self.assertRaises(module.ProviderError):
                    module.HttpClient(http_settings(), opener=opener).get(url)
                self.assertEqual(opener.calls, [])
        opener = SyntheticOpener(SyntheticResponse(b"[]"))
        self.assertEqual(module.HttpClient(http_settings(), opener=opener).get("https://api.dexscreener.com/token-profiles/latest/v1"), [])

    def test_http_get_returns_parsed_json_using_finite_timeout(self):
        module = public_module(self)
        self.assertTrue(hasattr(module, "HttpClient"), "Missing stdlib HTTP client")
        for document in ({"data": []}, [{"synthetic": True}]):
            with self.subTest(document=document):
                opener = SyntheticOpener(SyntheticResponse(json.dumps(document).encode()))
                client = module.HttpClient(http_settings(), opener=opener)
                result = client.get("https://api.geckoterminal.com/api/v2/networks/solana/new_pools")
                self.assertEqual(result, document)
                request, timeout, _ = opener.calls[0]
                self.assertEqual(request.get_method(), "GET")
                self.assertEqual(request.get_header("Accept"), "application/json")
                self.assertGreater(timeout, 0)
                self.assertLessEqual(timeout, 10.0)
                self.assertEqual(client.errors, [])


class TradeContractTests(unittest.TestCase):
    def test_trades_reject_wrong_chain_or_invalid_pool_before_request(self):
        module = public_module(self)
        original = module.PublicMarketAdapter(Chain.BASE, SyntheticClient(synthetic_pool_document(Chain.BASE))).new_pools()[0]
        for field, value in (("chain", Chain.SOLANA), ("address", "invalid/../pool"),
                             ("token_address", "0x" + "12" * 32), ("quote_address", "bad!")):
            with self.subTest(field=field):
                pool = copy.deepcopy(original)
                setattr(pool, field, value)
                client = SyntheticClient()
                with self.assertRaises(Exception) as raised:
                    module.PublicMarketAdapter(Chain.BASE, client).trades(pool)
                self.assertIsInstance(raised.exception, module.ProviderError)
                self.assertEqual(client.urls, [])

    def test_trade_dedup_uses_event_id_not_transaction_hash(self):
        module = public_module(self)
        for chain in (Chain.SOLANA, Chain.BASE):
            with self.subTest(chain=chain.value):
                client = SyntheticClient(synthetic_pool_document(chain))
                adapter = module.PublicMarketAdapter(chain, client)
                pool = adapter.new_pools()[0]
                one = synthetic_trade(pool, "synthetic-log-1")
                two = synthetic_trade(pool, "synthetic-log-2", "sell")
                invalid = copy.deepcopy(one)
                invalid["attributes"]["to_token_amount"] = None
                if chain is Chain.BASE:
                    one["attributes"]["to_token_address"] = pool.token_address.lower()
                client.responses.append({"data": [invalid, one, two, one, two]})
                self.assertEqual([event["id"] for event in adapter.trades(pool)], ["synthetic-log-1", "synthetic-log-2"])

    def test_trades_skip_invalid_or_unrelated_rows_with_diagnostics(self):
        module = public_module(self)
        for chain in (Chain.SOLANA, Chain.BASE):
            with self.subTest(chain=chain.value):
                client = SyntheticClient(synthetic_pool_document(chain))
                adapter = module.PublicMarketAdapter(chain, client)
                pool = adapter.new_pools()[0]
                valid = synthetic_trade(pool)
                bad = []
                for key, value in (("to_token_amount", "NaN"), ("to_token_amount", "0"),
                                   ("volume_in_usd", "Infinity"), ("price_to_in_usd", None),
                                   ("price_to_in_usd", "-1"), ("block_timestamp", "bad-date"),
                                   ("tx_from_address", "0x" + "12" * 32), ("tx_hash", None),
                                   ("kind", "transfer")):
                    row = copy.deepcopy(valid)
                    row["attributes"][key] = value
                    bad.append(row)
                unrelated = copy.deepcopy(valid)
                unrelated["attributes"]["to_token_address"] = pool.quote_address
                unrelated["attributes"]["from_token_address"] = pool.quote_address
                no_id = copy.deepcopy(valid)
                no_id.pop("id")
                bad.extend([unrelated, no_id, None])
                client.responses.append({"data": [*bad, valid]})
                try:
                    trades = adapter.trades(pool)
                except Exception as exc:
                    self.fail("Bad provider trade aborted healthy trades: " + str(exc))
                self.assertEqual(len(trades), 1)
                self.assertEqual(len(adapter.errors), len(bad))

    def test_trades_normalize_actual_token_legs_and_wallets(self):
        module = public_module(self)
        self.assertTrue(callable(getattr(module.PublicMarketAdapter, "trades", None)), "Missing wallet-bearing trade interface")
        for chain in (Chain.SOLANA, Chain.BASE, Chain.ROBINHOOD):
            with self.subTest(chain=chain.value):
                document = synthetic_pool_document(chain)
                client = SyntheticClient(document)
                adapter = module.PublicMarketAdapter(chain, client)
                pool = adapter.new_pools()[0]
                buy = synthetic_trade(pool)
                sell = synthetic_trade(pool, "synthetic-event-2", "sell")
                # Provider kind is not authoritative when the tracked token is the other leg.
                sell["attributes"]["kind"] = "buy"
                client.responses.append({"data": [buy, sell]})
                trades = adapter.trades(pool)
                self.assertEqual(len(trades), 2)
                for event, side in zip(trades, ("buy", "sell")):
                    self.assertEqual(set(event), {"id", "timestamp", "tx_hash", "wallet", "chain", "token_address", "pool_address", "side", "quantity", "usd_value", "price_usd", "provider"})
                    self.assertEqual(event["id"], "synthetic-event-1" if side == "buy" else "synthetic-event-2")
                    self.assertEqual(event["timestamp"], "2026-10-08T09:15:00Z")
                    self.assertEqual(event["tx_hash"], "synthetic-transaction")
                    self.assertEqual(event["wallet"], buy["attributes"]["tx_from_address"])
                    self.assertEqual(event["chain"], chain.value)
                    self.assertEqual(event["token_address"], pool.token_address)
                    self.assertEqual(event["pool_address"], pool.address)
                    self.assertEqual(event["side"], side)
                    self.assertEqual((event["quantity"], event["usd_value"], event["price_usd"]), (80.0, 100.0, 1.25))
                    self.assertEqual(event["provider"], "geckoterminal")
                self.assertEqual(client.urls[-1], f"https://api.geckoterminal.com/api/v2/networks/{chain.value}/pools/{pool.address}/trades")


class CompatibilityContractTests(unittest.TestCase):
    def test_legacy_adapters_are_stdlib_public_read_only_wrappers(self):
        module = public_module(self)
        try:
            SolanaAdapter = importlib.import_module("avarice.chains.solana").SolanaAdapter
            EVMAdapter = importlib.import_module("avarice.chains.evm").EVMAdapter
        except ImportError as exc:
            self.fail("Legacy adapter still eagerly requires a paid/optional dependency: " + str(exc))
        client = SyntheticClient(synthetic_pool_document())
        solana = SolanaAdapter(client=client)
        self.assertIsInstance(solana, module.PublicMarketAdapter)
        self.assertIs(solana.chain, Chain.SOLANA)
        self.assertEqual(len(solana.new_pools()), 1)
        self.assertIs(solana.client, client)
        for chain in (Chain.BASE, Chain.ROBINHOOD, Chain.ETHEREUM, Chain.BSC):
            adapter = EVMAdapter(chain, client=SyntheticClient(synthetic_pool_document(chain)))
            self.assertIsInstance(adapter, module.PublicMarketAdapter)
            self.assertIs(adapter.chain, chain)
            self.assertEqual(len(adapter.new_pools()), 1)
        with self.assertRaises(ValueError):
            EVMAdapter(Chain.SOLANA, client=SyntheticClient())
        self.assertIsInstance(SolanaAdapter().client, module.HttpClient)
        self.assertIsInstance(EVMAdapter(Chain.ROBINHOOD).client, module.HttpClient)
        chains = importlib.import_module("avarice.chains")
        for name in ("PublicMarketAdapter", "HttpClient", "ProviderError"):
            self.assertIs(getattr(chains, name), getattr(module, name))
        self.assertFalse(hasattr(solana, "check_honeypot"))
        self.assertFalse(hasattr(solana, "get_wallet_metrics"))


class PoolContractTests(unittest.TestCase):
    def test_provider_resources_must_be_declared_pool_or_trade_types(self):
        module = public_module(self)
        for kind in (None, "token", "trade"):
            with self.subTest(pool_type=kind):
                document = synthetic_pool_document()
                document["data"][0]["type"] = kind
                adapter = module.PublicMarketAdapter(Chain.SOLANA, SyntheticClient(document))
                self.assertEqual(adapter.new_pools(), [])
                self.assertTrue(adapter.errors)
        client = SyntheticClient(synthetic_pool_document())
        adapter = module.PublicMarketAdapter(Chain.SOLANA, client)
        pool = adapter.new_pools()[0]
        row = synthetic_trade(pool)
        row["type"] = "pool"
        client.responses.append({"data": [row]})
        self.assertEqual(adapter.trades(pool), [])
        self.assertTrue(adapter.errors)

    def test_pool_not_found_is_missing_but_provider_outage_is_not_hidden(self):
        module = public_module(self)
        address = "0x" + "Ab" * 20
        not_found = module.ProviderError("synthetic missing pool", status_code=404)
        adapter = module.PublicMarketAdapter(Chain.BASE, SyntheticClient(not_found))
        try:
            self.assertIsNone(adapter.pool(address))
        except module.ProviderError as exc:
            self.fail("Known missing pool was not represented as None: " + str(exc))
        self.assertTrue(adapter.errors)
        failed = module.ProviderError("synthetic outage", status_code=503)
        with self.assertRaises(module.ProviderError) as raised:
            module.PublicMarketAdapter(Chain.BASE, SyntheticClient(failed)).pool(address)
        self.assertIs(raised.exception, failed)
        for price in (None, "NaN", "Infinity"):
            with self.subTest(price=price):
                document = synthetic_pool_document(Chain.BASE)
                document["data"] = document["data"][0]
                document["data"]["attributes"]["base_token_price_usd"] = price
                adapter = module.PublicMarketAdapter(Chain.BASE, SyntheticClient(document))
                self.assertIsNone(adapter.pool(address))
                self.assertTrue(adapter.errors)

    def test_invalid_provider_envelopes_raise_explicit_provider_errors(self):
        module = public_module(self)
        for document in ([], {}, {"data": None}, {"data": {}}, {"data": [], "included": "bad"}):
            with self.subTest(document=document):
                adapter = module.PublicMarketAdapter(Chain.SOLANA, SyntheticClient(document))
                with self.assertRaises(Exception) as raised:
                    adapter.new_pools()
                self.assertIsInstance(raised.exception, module.ProviderError)
        client = SyntheticClient(synthetic_pool_document())
        adapter = module.PublicMarketAdapter(Chain.SOLANA, client)
        pool = adapter.new_pools()[0]
        for document in ([], {}, {"data": None}, {"data": {}}):
            with self.subTest(trades=document):
                client.responses.append(document)
                with self.assertRaises(Exception) as raised:
                    adapter.trades(pool)
                self.assertIsInstance(raised.exception, module.ProviderError)
        for document in ([], {}, {"errors": [{"status": 503}]}):
            with self.subTest(quote=document):
                with self.assertRaises(Exception) as raised:
                    module.PublicMarketAdapter(Chain.SOLANA, SyntheticClient(document)).pool(pool.address)
                self.assertIsInstance(raised.exception, module.ProviderError)

    def test_new_pools_deduplicate_valid_chain_pool_identity(self):
        module = public_module(self)
        for chain in (Chain.SOLANA, Chain.BASE):
            with self.subTest(chain=chain.value):
                document = synthetic_pool_document(chain)
                row = document["data"][0]
                invalid = copy.deepcopy(row)
                invalid["attributes"]["base_token_price_usd"] = None
                alternate_case = copy.deepcopy(row)
                alternate_case["attributes"]["address"] = row["attributes"]["address"].lower()
                alternate_case["id"] = chain.value + "_" + alternate_case["attributes"]["address"]
                document["data"] = [invalid, row, row, alternate_case]
                result = module.PublicMarketAdapter(chain, SyntheticClient(document)).new_pools()
                self.assertEqual(len(result), 2 if chain is Chain.SOLANA else 1)
                self.assertEqual(result[0].address, row["attributes"]["address"])

    def test_address_validation_precedes_requests_and_preserves_valid_ids(self):
        module = public_module(self)
        self.assertTrue(hasattr(module, "ProviderError"), "Missing provider boundary exception")
        for chain, address in ((Chain.BASE, "0x" + "12" * 19), (Chain.BASE, "0x" + "zz" * 20),
                               (Chain.BASE, " 0x" + "12" * 20), (Chain.SOLANA, "invalid/../pool"),
                               (Chain.SOLANA, "0" * 43)):
            with self.subTest(chain=chain.value, address=address):
                client = SyntheticClient()
                with self.assertRaises(module.ProviderError):
                    module.PublicMarketAdapter(chain, client).pool(address)
                self.assertEqual(client.urls, [])
        v4_address = "0x" + "Ab" * 32
        document = synthetic_pool_document(Chain.BASE, v4_address)
        document["data"] = document["data"][0]
        result = module.PublicMarketAdapter(Chain.BASE, SyntheticClient(document)).pool(v4_address)
        self.assertEqual(result.address, v4_address)
        for chain in (Chain.SOLANA, Chain.BASE):
            for bad_kind in ("pool", "token", "foreign-token", "relationship-type"):
                with self.subTest(chain=chain.value, bad_kind=bad_kind):
                    document = synthetic_pool_document(chain)
                    if bad_kind == "pool":
                        document["data"][0]["attributes"]["address"] = "bad!"
                        document["data"][0]["id"] = chain.value + "_bad!"
                    elif bad_kind == "token":
                        document["included"][0]["attributes"]["address"] = "0x" + "12" * 32
                    elif bad_kind == "foreign-token":
                        document["included"][0]["id"] = "other_" + document["included"][0]["attributes"]["address"]
                        document["data"][0]["relationships"]["base_token"]["data"]["id"] = document["included"][0]["id"]
                    else:
                        document["data"][0]["relationships"]["base_token"]["data"]["type"] = "pool"
                    adapter = module.PublicMarketAdapter(chain, SyntheticClient(document))
                    self.assertEqual(adapter.new_pools(), [])
                    self.assertTrue(adapter.errors)

    def test_pool_quotes_only_the_exact_chain_and_pool(self):
        module = public_module(self)
        self.assertTrue(callable(getattr(module.PublicMarketAdapter, "pool", None)), "Missing exact pool quote interface")
        for chain in Chain:
            with self.subTest(chain=chain.value):
                document = synthetic_pool_document(chain)
                document["data"] = document["data"][0]
                address = document["data"]["attributes"]["address"]
                client = SyntheticClient(document)
                adapter = module.PublicMarketAdapter(chain, client)
                pool = adapter.pool(address)
                self.assertIsNotNone(pool)
                self.assertEqual((pool.chain, pool.address), (chain, address))
                network = "eth" if chain is Chain.ETHEREUM else chain.value
                self.assertEqual(client.urls, [
                    f"https://api.geckoterminal.com/api/v2/networks/{network}/pools/{address}?include=base_token,quote_token,dex"
                ])
                for wrong in ("other_chain_" + address, network + "_" + ("E" * 43 if chain is Chain.SOLANA else "0x" + "12" * 20)):
                    bad = copy.deepcopy(document)
                    bad["data"]["id"] = wrong
                    self.assertIsNone(module.PublicMarketAdapter(chain, SyntheticClient(bad)).pool(address))
        solana = synthetic_pool_document()
        solana["data"] = solana["data"][0]
        self.assertIsNone(module.PublicMarketAdapter(Chain.SOLANA, SyntheticClient(solana)).pool(("B" * 43).lower()))
        unknown_birth = synthetic_pool_document(Chain.BASE)
        unknown_birth["data"] = unknown_birth["data"][0]
        unknown_birth["data"]["attributes"]["pool_created_at"] = "bad-date"
        unknown_birth["data"]["attributes"]["reserve_in_usd"] = "0"
        pool = module.PublicMarketAdapter(Chain.BASE, SyntheticClient(unknown_birth)).pool("0x" + "ab" * 20)
        self.assertIsNotNone(pool)
        self.assertEqual(pool.liquidity_usd, 0.0)
        self.assertIsNone(pool.created_at)
        self.assertIsNone(module.PublicMarketAdapter(Chain.BASE, SyntheticClient({"data": None})).pool("0x" + "ab" * 20))

    def test_new_pools_require_actual_valid_birth_dates(self):
        module = public_module(self)
        for created in (None, "", "yesterday", "2026-99-01T00:00:00Z", "2026-10-08", "2026-10-08T09:00:00"):
            with self.subTest(created=created):
                document = synthetic_pool_document()
                document["data"][0]["attributes"]["pool_created_at"] = created
                adapter = module.PublicMarketAdapter(Chain.SOLANA, SyntheticClient(document))
                self.assertEqual(adapter.new_pools(), [])
                self.assertTrue(adapter.errors)

    def test_new_pools_isolate_invalid_rows_with_diagnostics(self):
        module = public_module(self)
        document = synthetic_pool_document()
        good = document["data"][0]
        mutations = [
            ("base_token_price_usd", None), ("base_token_price_usd", "NaN"),
            ("base_token_price_usd", "Infinity"), ("reserve_in_usd", "-1"),
            ("reserve_in_usd", None), ("volume_usd", {"h24": "NaN"}),
            ("transactions", {"m5": {"buys": 1.5}}),
            ("price_change_percentage", {"m5": "-Infinity"}),
        ]
        bad_rows = []
        for key, value in mutations:
            row = copy.deepcopy(good)
            row["attributes"][key] = value
            bad_rows.append(row)
        missing_relationship = copy.deepcopy(good)
        missing_relationship["relationships"]["base_token"]["data"]["id"] = "missing"
        document["data"] = [*bad_rows, missing_relationship, "malformed", good]
        client = SyntheticClient(document)
        adapter = module.PublicMarketAdapter(Chain.SOLANA, client)
        try:
            pools = adapter.new_pools()
        except Exception as exc:
            self.fail("One bad provider row aborted the scan: " + str(exc))
        self.assertEqual(len(pools), 1)
        self.assertEqual(len(adapter.errors), len(bad_rows) + 2)
        self.assertIs(adapter.errors, client.errors)
        self.assertTrue(all("geckoterminal" in message and "solana" in message for message in adapter.errors))

    def test_new_pools_resolve_relationships_to_included_tokens(self):
        module = public_module(self)
        client = SyntheticClient(synthetic_pool_document())
        pools = module.PublicMarketAdapter(Chain.SOLANA, client).new_pools()
        self.assertEqual(len(pools), 1)
        pool = pools[0]
        self.assertEqual(pool.token_address, "C" * 43)
        self.assertEqual(pool.quote_address, "D" * 43)
        self.assertEqual(pool.symbol, "SYN")
        self.assertEqual(pool.address, "B" * 43)
        self.assertEqual(pool.price_usd, 1.25)
        self.assertEqual(pool.liquidity_usd, 1200.0)
        self.assertEqual(pool.volume_24h_usd, 2500.0)
        self.assertEqual(pool.created_at, "2026-10-08T09:00:00Z")
        self.assertNotEqual(pool.observed_at, pool.created_at)
        self.assertEqual(pool.source, "geckoterminal")
        self.assertEqual(pool.dex, "synthetic-dex")
        self.assertEqual(pool.safety_status, "unverified")
        self.assertEqual((pool.buys_5m, pool.sells_5m, pool.price_change_5m_pct), (7, 3, -1.5))
        self.assertEqual(client.urls, [
            "https://api.geckoterminal.com/api/v2/networks/solana/new_pools?include=base_token,quote_token,dex"
        ])


if __name__ == "__main__":
    unittest.main()
