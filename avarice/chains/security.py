"""Anonymous exact-chain/token GoPlus snapshots, never issuer certification.

EVM_REQUIRED is a conservative paper-model gate: explicit zero danger flags,
verified source, non-proxy, recorded DEX, and known finite zero buy/sell/transfer
fees. Positive taxes are blocked because the simulator has no token-tax model.
SOLANA_REQUIRED gates mint/transfer controls and extension configuration only;
default state 1 is initialized, 2 is frozen. Only explicit {} fees / [] hooks
establish an unconfigured feature. Optional fields are reported, not invented.
Results may be wrong or change: no sell simulation, liquidity-lock or rug proof.
Create one TokenSecurity per scan; its lazy HTTP client shares a <=20s lifetime.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.parse import parse_qsl, quote, urlsplit

from avarice.chains.public import HttpClient, ProviderError


class GoPlusHttpClient(HttpClient):
    """Only anonymous GoPlus GETs; inherited process-global resource guards."""

    ALLOWED_HOSTS = frozenset({"api.gopluslabs.io"})
    MAX_ATTEMPTS = 1  # Quota-conservative: an outage/rate limit is unknown, not retried into a pass.

    def __init__(self, settings, **hooks):
        super().__init__(settings, **hooks)
        budget = min(self.budget, 20.0)
        self.deadline -= self.budget - budget  # Keep the original construction start time.
        self.budget = budget
        self.timeout = min(self.timeout, budget)

    def _read_body(self, response, request_deadline):
        body = super()._read_body(response, request_deadline)
        def unique(items):
            values = {}
            for key, value in items:
                if key in values:
                    raise ValueError("ambiguous duplicate GoPlus JSON field")
                if isinstance(value, Decimal) and value != 0 and float(value) == 0:
                    raise ValueError("nonzero GoPlus JSON number underflows to zero")
                values[key] = value
            return values
        json.loads(body, object_pairs_hook=unique, parse_float=Decimal)
        return body

    def _validate_url(self, url):
        super()._validate_url(url)
        try:
            parts = urlsplit(url)
            query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
            if parts.path == "/api/v1/supported_chains" and query == [("show_type", "token_security")]:
                return
            paths = {"/api/v1/solana/token_security": "solana",
                     **{"/api/v1/token_security/" + value: chain for chain, value in CHAIN_IDS.items() if chain != "solana"}}
            chain = paths.get(parts.path)
            if (chain is None or len(query) != 1 or query[0][0] != "contract_addresses"
                    or not _valid_address(chain, query[0][1])):
                raise ValueError("only exact-token or supported-chain anonymous queries are allowed")
        except ValueError as exc:
            raise ProviderError(str(exc)) from exc


# All these fields must be explicitly known for a limited EVM pass.
EVM_REQUIRED_FLAGS = ("is_honeypot", "cannot_buy", "cannot_sell_all", "transfer_pausable",
                      "is_blacklisted", "is_whitelisted", "is_mintable", "hidden_owner",
                      "owner_change_balance", "slippage_modifiable", "personal_slippage_modifiable",
                      "can_take_back_ownership", "external_call", "selfdestruct")
EVM_OPTIONAL_FLAGS = ("anti_whale_modifiable", "trading_cooldown", "honeypot_with_same_creator")
EVM_EXPECTED = {**dict.fromkeys(EVM_REQUIRED_FLAGS + EVM_OPTIONAL_FLAGS, 0),
                "is_open_source": 1, "is_proxy": 0, "is_in_dex": 1}
EVM_TAXES = ("buy_tax", "sell_tax", "transfer_tax")
EVM_REQUIRED = EVM_REQUIRED_FLAGS + ("is_open_source", "is_proxy", "is_in_dex") + EVM_TAXES
LIMITS = ("Provider snapshot only: evidence may be wrong or change; no buy/sell simulation, "
          "issuer authenticity, liquidity lock or rug-proof assurance.")


CHAIN_IDS = {"solana": "solana", "base": "8453", "robinhood": "4663", "ethereum": "1", "bsc": "56"}
BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _valid_address(chain, address):
    if not isinstance(address, str):
        return False
    if chain != "solana":
        return re.fullmatch(r"0x[0-9a-fA-F]{40}", address) is not None
    if not 32 <= len(address) <= 44 or any(character not in BASE58 for character in address):
        return False
    number = 0
    for character in address:
        number = number * 58 + BASE58.index(character)
    return len(address) - len(address.lstrip("1")) + (number.bit_length() + 7) // 8 == 32


def _flag(value, allowed=(0, 1)):
    if type(value) is int and value in allowed:
        return value
    if type(value) is str and value in tuple(str(item) for item in allowed):
        return int(value)
    return None


def _tax(value):
    # Decimal prevents tiny positive/negative taxes from underflowing to zero.
    if type(value) not in (int, float, str):
        return None
    text = str(value)
    if len(text) > 128 or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", text) is None:
        return None
    try:
        number = Decimal(text)
        if number.is_finite() and number >= 0:
            return 0 if number == 0 else str(number)
    except (InvalidOperation, ValueError, OverflowError):
        pass
    return None


def _same_address(chain, first, second):
    return isinstance(first, str) and (first == second if chain == "solana" else first.lower() == second.lower())


def _token_evidence(document, chain, address):
    if not isinstance(document, dict) or type(document.get("code")) is not int or document["code"] != 1:
        raise ValueError("missing or unsuccessful GoPlus envelope")
    tokens = document.get("result")
    if not isinstance(tokens, dict) or len(tokens) != 1:
        raise ValueError("empty or ambiguous token result")
    key, evidence = next(iter(tokens.items()))
    if not _same_address(chain, key, address) or not isinstance(evidence, dict) or not evidence:
        raise ValueError("wrong token or empty evidence")
    for item in (document, evidence):
        if "chain_id" in item and (type(item["chain_id"]) not in (str, int) or str(item["chain_id"]) != CHAIN_IDS[chain]):
            raise ValueError("response chain ID mismatch")
        if "chain" in item and item["chain"] != chain:
            raise ValueError("response chain mismatch")
        for field in ("contract_address", "token_address", "address"):
            if field in item and not _same_address(chain, item[field], address):
                raise ValueError("response token identity mismatch")
    return evidence


SOLANA_CONTROLS = ("balance_mutable_authority", "closable", "default_account_state_upgradable",
                   "freezable", "mintable", "transfer_fee_upgradable", "transfer_hook_upgradable")
SOLANA_REQUIRED = SOLANA_CONTROLS + ("non_transferable", "default_account_state", "transfer_fee", "transfer_hook")
SOLANA_OPTIONAL = ("metadata_mutable", "trusted_token")


def _solana(evidence, result):
    checks = result["checks"]
    for field in SOLANA_CONTROLS + ("metadata_mutable",):
        raw = evidence.get(field)
        value = _flag(raw.get("status")) if isinstance(raw, dict) else None
        if value == 0 and (not isinstance(raw.get("authority", []), list) or raw.get("authority", [])):
            value = None  # An explicitly disabled flag with active/invalid authorities is ambiguous.
        checks[field] = value
    checks["trusted_token"] = _flag(evidence.get("trusted_token"))
    checks["non_transferable"] = _flag(evidence.get("non_transferable"))
    checks["default_account_state"] = _flag(evidence.get("default_account_state"), (0, 1, 2))
    for field, shape in (("transfer_fee", dict), ("transfer_hook", list)):
        raw = evidence.get(field)
        checks[field] = ("configured" if raw else shape()) if isinstance(raw, shape) else None
    result["missing_fields"].extend(field for field, value in checks.items() if value is None)
    dangers = [field + "=1: active mint/transfer authority or restriction"
               for field in SOLANA_CONTROLS + ("non_transferable",) if checks[field] == 1]
    if checks["default_account_state"] == 2:
        dangers.append("default_account_state=2: newly created token accounts default to frozen")
    dangers.extend(field + ": configured feature blocked for this model"
                   for field in ("transfer_fee", "transfer_hook") if checks[field] == "configured")
    if dangers:
        result["status"] = "unsafe"
        result["reasons"].extend(dangers)
    elif not set(SOLANA_REQUIRED).intersection(result["missing_fields"]) and checks["default_account_state"] == 1:
        result["status"] = "limited_checks_passed"
        result["reasons"].append("Solana mint/transfer authority flags only")
    required_missing = [field for field in SOLANA_REQUIRED if field in result["missing_fields"]]
    if required_missing:
        result["reasons"].append("Missing or malformed required checks: " + ", ".join(required_missing))
    if checks["default_account_state"] == 0:
        result["reasons"].append("default_account_state=0: account initialization evidence unavailable")
    result["reasons"].append(LIMITS)
    return result


class TokenSecurity:
    """Read-only risk screening; an empty response never establishes anything."""

    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    def check(self, pool, now=None):
        raw_chain = getattr(pool, "chain", None)
        chain = getattr(raw_chain, "value", raw_chain)
        chain = chain if isinstance(chain, str) else None
        address = getattr(pool, "token_address", None)
        result = {"status": "unknown", "chain": chain,
                  "token_address": address if isinstance(address, str) else None,
                  "checked_at": datetime.now(timezone.utc).isoformat(),
                  "provider": "goplus", "reasons": [], "checks": {}, "missing_fields": []}
        if now is not None:
            try:
                timestamp = now if isinstance(now, datetime) else datetime.fromisoformat(now)
                if timestamp.utcoffset() is None:
                    raise ValueError("check timestamp must have a timezone")
                result["checked_at"] = timestamp.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                result["missing_fields"].append("checked_at")
                result["reasons"].append("Invalid check timestamp; no provider query")
                return result
        if chain not in CHAIN_IDS:
            result["missing_fields"].append("chain")
            result["reasons"].append("Unsupported chain; no ticker/name fallback")
            return result
        if not _valid_address(chain, address):
            result["missing_fields"].append("token_address")
            result["reasons"].append("Invalid exact token address; not repaired or queried")
            return result
        path = "solana/token_security" if chain == "solana" else "token_security/" + CHAIN_IDS[chain]
        try:
            if self.client is None:
                self.client = GoPlusHttpClient(self.settings)
            document = self.client.get("https://api.gopluslabs.io/api/v1/" + path + "?contract_addresses="
                                       + quote(address, safe=""))
            evidence = _token_evidence(document, chain, address)
        except Exception as exc:
            result["missing_fields"].append("provider_response")
            status = getattr(exc, "status_code", None)
            detail = f"HTTP {status}" if type(status) is int else type(exc).__name__
            result["reasons"].append("GoPlus unavailable or invalid token response: " + detail)
            return result
        if chain == "solana":
            return _solana(evidence, result)
        for field in (*EVM_EXPECTED, *EVM_TAXES):
            value = _tax(evidence.get(field)) if field in EVM_TAXES else _flag(evidence.get(field))
            result["checks"][field] = value
            if value is None:
                result["missing_fields"].append(field)
        dangers = [field + "=1: explicit provider risk/control flag" for field in EVM_REQUIRED_FLAGS + EVM_OPTIONAL_FLAGS
                   if result["checks"].get(field) == 1]
        dangers.extend(field + ">0: blocked for this model; token-tax accounting is not implemented"
                       for field in EVM_TAXES if result["checks"][field] not in (None, 0))
        if dangers:
            result["status"] = "unsafe"
            result["reasons"].extend(dangers)
        elif (not set(EVM_REQUIRED).intersection(result["missing_fields"])
                and all(result["checks"][field] == expected for field, expected in EVM_EXPECTED.items()
                        if field in EVM_REQUIRED)
                and all(result["checks"][field] == 0 for field in EVM_TAXES)):
            result["status"] = "limited_checks_passed"
        required_missing = [field for field in EVM_REQUIRED if field in result["missing_fields"]]
        if required_missing:
            result["reasons"].append("Missing or malformed required checks: " + ", ".join(required_missing))
        for field in ("is_open_source", "is_proxy", "is_in_dex"):
            if result["checks"][field] is not None and result["checks"][field] != EVM_EXPECTED[field]:
                result["reasons"].append(field + ": source verification, non-proxy or DEX evidence gap")
        if any(field in result["missing_fields"] for field in EVM_OPTIONAL_FLAGS):
            result["reasons"].append("Optional checks unavailable; see missing_fields")
        result["reasons"].append(LIMITS)
        return result
