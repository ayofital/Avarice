"""Offline GoPlus contracts; every token and payload here is SYNTHETIC."""
import copy
import importlib
import io
import json
import threading
from email.message import Message
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, build_opener
from urllib.response import addinfourl

from avarice.chains.public import HttpClient, ProviderError
from avarice.core.models import Chain


EVM = "0x" + "aB" * 20
SOLANA = "1" * 31 + "2"  # Synthetic base58-encoded 32-byte public key.
NOW = "2026-10-08T09:00:00Z"


def settings(**changes):
    return SimpleNamespace(**{"http_timeout_seconds": 15.0,
                              "http_min_interval_seconds": 6.5,
                              "scan_budget_seconds": 150.0, **changes})


def pool(chain=Chain.BASE, address=EVM):
    return SimpleNamespace(chain=chain, token_address=address, symbol="SYN")


def synthetic_evm(**changes):
    """Synthetic complete zero-risk-flag/zero-tax evidence, not a real token."""
    flags = ("is_honeypot", "cannot_buy", "cannot_sell_all", "transfer_pausable",
             "is_blacklisted", "is_whitelisted", "is_mintable", "hidden_owner",
             "owner_change_balance", "slippage_modifiable", "personal_slippage_modifiable",
             "can_take_back_ownership", "external_call", "selfdestruct")
    return {**dict.fromkeys(flags, "0"), "is_open_source": "1", "is_proxy": "0",
            "is_in_dex": "1", "buy_tax": "0", "sell_tax": "0", "transfer_tax": "0", **changes}


SOLANA_CONTROLS = ("balance_mutable_authority", "closable", "default_account_state_upgradable",
                   "freezable", "mintable", "transfer_fee_upgradable", "transfer_hook_upgradable")


def synthetic_solana(**changes):
    """Synthetic Solana authority-extension flags, not a honeypot simulation."""
    return {**{field: {"authority": [], "status": "0"} for field in SOLANA_CONTROLS},
            "default_account_state": "1", "non_transferable": "0", "transfer_fee": {},
            "transfer_hook": [], **changes}


def envelope(address, evidence):
    return {"code": 1, "result": {address: evidence}}


class SyntheticClient:
    def __init__(self, *documents):
        self.documents = list(documents)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        document = self.documents.pop(0)
        if isinstance(document, Exception):
            raise document
        return copy.deepcopy(document)


def security_module(test):
    try:
        return importlib.import_module("avarice.chains.security")
    except ImportError as exc:
        test.fail("Missing importable anonymous risk screener: " + str(exc))


class TokenSecurityTests(unittest.TestCase):
    def test_complete_evm_evidence_is_only_a_limited_pass(self):
        module = security_module(self)
        client = SyntheticClient(envelope(EVM, synthetic_evm()))
        result = module.TokenSecurity(settings(), client=client).check(pool(), now=NOW)
        self.assertEqual(result["status"], "limited_checks_passed")
        self.assertEqual(result["checks"]["is_open_source"], 1)
        self.assertEqual(result["checks"]["is_honeypot"], 0)
        self.assertEqual(result["checks"]["buy_tax"], 0)
        self.assertIn("trading_cooldown", result["missing_fields"])
        self.assertTrue(any("snapshot" in reason.lower() for reason in result["reasons"]))
        self.assertTrue(any("issuer authenticity" in reason.lower() for reason in result["reasons"]))
        self.assertTrue(any("simulation" in reason.lower() for reason in result["reasons"]))
        json.dumps(result, allow_nan=False)

    def screen(self, evidence, *, chain=Chain.BASE, address=EVM, now=NOW):
        module = security_module(self)
        client = SyntheticClient(envelope(address, evidence))
        try:
            return module.TokenSecurity(settings(), client=client).check(pool(chain, address), now=now)
        except Exception as exc:
            self.fail("Malformed evidence must return unknown, not raise: " + repr(exc))

    def test_malformed_required_flags_never_become_known_zero(self):
        for field in (key for key in synthetic_evm() if not key.endswith("_tax")):
            for invalid in (None, "", False, True, 0.0, 1.0, "0.0", "false", -1, 2, [], {}):
                with self.subTest(field=field, value=invalid):
                    result = self.screen(synthetic_evm(**{field: invalid}))
                    self.assertEqual(result["status"], "unknown")
                    self.assertIn(field, result["missing_fields"])
                    self.assertIsNone(result["checks"][field])
                    json.dumps(result, allow_nan=False)

    def test_missing_or_invalid_taxes_never_become_zero(self):
        for field in ("buy_tax", "sell_tax", "transfer_tax"):
            for invalid in (None, "", False, True, "NaN", "Infinity", float("nan"),
                            float("inf"), "-0.01", -1, "-1e-1000", [], {}):
                with self.subTest(field=field, value=invalid):
                    result = self.screen(synthetic_evm(**{field: invalid}))
                    self.assertEqual(result["status"], "unknown")
                    self.assertIn(field, result["missing_fields"])
                    self.assertIsNone(result["checks"][field])
                    json.dumps(result, allow_nan=False)
            incomplete = synthetic_evm()
            incomplete.pop(field)
            self.assertEqual(self.screen(incomplete)["status"], "unknown")

    def test_explicit_evm_danger_overrides_other_missing_fields(self):
        module = security_module(self)
        for field in module.EVM_REQUIRED_FLAGS + module.EVM_OPTIONAL_FLAGS:
            for one in (1, "1"):
                with self.subTest(field=field, value=one):
                    evidence = synthetic_evm(**{field: one})
                    evidence.pop("sell_tax")
                    result = self.screen(evidence)
                    self.assertEqual(result["status"], "unsafe")
                    self.assertEqual(result["checks"][field], 1)
                    self.assertIn("sell_tax", result["missing_fields"])
                    self.assertTrue(any(field in reason for reason in result["reasons"]))

    def test_positive_token_taxes_block_this_model_without_honeypot_claim(self):
        for field in ("buy_tax", "sell_tax", "transfer_tax"):
            for positive in ("0.01", 0.1, "1e-1000", "1e1000"):
                with self.subTest(field=field, tax=positive):
                    evidence = synthetic_evm(**{field: positive})
                    evidence.pop("is_mintable")
                    result = self.screen(evidence)
                    self.assertEqual(result["status"], "unsafe")
                    reasons = " ".join(result["reasons"]).lower()
                    self.assertIn(field, reasons)
                    self.assertIn("tax accounting", reasons)
                    self.assertNotIn("honeypot", reasons)
                    json.dumps(result, allow_nan=False)

    def test_queries_the_exact_chain_without_ticker_fallback(self):
        module = security_module(self)
        paths = {Chain.BASE: "8453", Chain.ROBINHOOD: "4663", Chain.ETHEREUM: "1", Chain.BSC: "56"}
        client = SyntheticClient(*(envelope(EVM, synthetic_evm()) for _ in paths))
        screener = module.TokenSecurity(settings(), client=client)
        for chain, chain_id in paths.items():
            with self.subTest(chain=chain):
                result = screener.check(pool(chain), now=NOW)
                self.assertEqual(result["chain"], chain.value)
                self.assertEqual(result["token_address"], EVM)
                self.assertEqual(urlsplit(client.urls[-1]).path, "/api/v1/token_security/" + chain_id)
        other = "0x" + "Cd" * 20
        client = SyntheticClient(envelope(EVM, synthetic_evm(is_honeypot="1")),
                                 envelope(other, synthetic_evm()))
        screener = module.TokenSecurity(settings(), client=client)
        self.assertEqual(screener.check(pool(), now=NOW)["status"], "unsafe")
        self.assertEqual(screener.check(pool(address=other), now=NOW)["status"], "limited_checks_passed")
        self.assertEqual([parse_qs(urlsplit(url).query)["contract_addresses"][0] for url in client.urls],
                         [EVM, other])

    def test_invalid_addresses_are_preserved_and_rejected_before_query(self):
        module = security_module(self)
        cases = [(Chain.BASE, value) for value in ("0X" + "a" * 40, " " + EVM, EVM + " ",
                 "0x" + "a" * 39, "0x" + "a" * 64, EVM + "?a=1", EVM + "#x", "PANDA")]
        cases += [(Chain.SOLANA, value) for value in ("B" * 32, "z" * 44, "1" * 33,
                  "0" + "B" * 43, "O" * 43, SOLANA + " ", "SYN")]
        for chain, address in cases:
            with self.subTest(chain=chain, address=address):
                client = SyntheticClient(envelope(address, synthetic_evm()))
                result = module.TokenSecurity(settings(), client=client).check(pool(chain, address), now=NOW)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["token_address"], address)
                self.assertIn("token_address", result["missing_fields"])
                self.assertEqual(client.urls, [])

    def test_unsupported_or_unnormalized_chain_is_unknown_without_query(self):
        module = security_module(self)
        for chain in ("near", "BASE", "8453", "base ", None, True):
            with self.subTest(chain=chain):
                client = SyntheticClient(envelope(EVM, synthetic_evm()))
                try:
                    result = module.TokenSecurity(settings(), client=client).check(pool(chain), now=NOW)
                except Exception as exc:
                    self.fail("Unknown chain must fail closed: " + repr(exc))
                self.assertEqual(result["status"], "unknown")
                self.assertIn("chain", result["missing_fields"])
                self.assertEqual(client.urls, [])

    def screen_document(self, document, *, chain=Chain.BASE, address=EVM):
        module = security_module(self)
        try:
            return module.TokenSecurity(settings(), client=SyntheticClient(document)).check(pool(chain, address), now=NOW)
        except Exception as exc:
            self.fail("Invalid provider response must fail closed, not raise: " + repr(exc))

    def test_envelope_and_exact_token_identity_are_required(self):
        other = "0x" + "cD" * 20
        good = synthetic_evm()
        documents = [None, [], {}, {"result": {EVM: good}},
                     *(dict(code=code, result={EVM: good}) for code in (0, True, "1", 1.0, None)),
                     {"code": 1, "result": None}, {"code": 1, "result": []},
                     {"code": 1, "result": {}}, envelope(EVM, []), envelope(EVM, None),
                     envelope(other, {**good, "token_symbol": "SYN"}),
                     {"code": 1, "result": {EVM: good, EVM.lower(): good}},
                     {"code": 1, "result": {EVM: good, other: good}},
                     {**envelope(EVM, good), "chain_id": "4663"},
                     envelope(EVM, {**good, "chain_id": "4663"}),
                     envelope(EVM, {**good, "chain": "robinhood"}),
                     envelope(EVM, {**good, "contract_address": other})]
        for document in documents:
            with self.subTest(document=document):
                result = self.screen_document(document)
                self.assertEqual(result["status"], "unknown")
                self.assertIn("provider_response", result["missing_fields"])
                self.assertEqual(result["checks"], {})

    def test_evm_result_key_matching_ignores_case_but_preserves_input(self):
        result = self.screen_document(envelope(EVM.lower(), synthetic_evm()))
        self.assertEqual(result["status"], "limited_checks_passed")
        self.assertEqual(result["token_address"], EVM)

    def test_provider_errors_and_rate_limits_remain_unknown(self):
        from avarice.chains.public import ProviderError
        for error in (ProviderError("SYNTHETIC HTTP 429", status_code=429),
                      ProviderError("SYNTHETIC outage"), TimeoutError("SYNTHETIC timeout"),
                      OSError("SYNTHETIC transport"), ValueError("SYNTHETIC bad JSON")):
            with self.subTest(error=type(error).__name__):
                result = self.screen_document(error)
                self.assertEqual(result["status"], "unknown")
                self.assertIn("provider_response", result["missing_fields"])
                if getattr(error, "status_code", None) == 429:
                    self.assertTrue(any("429" in reason for reason in result["reasons"]))

    def test_unverified_source_proxy_and_no_dex_are_explicit_unknown_gaps(self):
        for field, value in (("is_open_source", "0"), ("is_proxy", "1"), ("is_in_dex", "0")):
            with self.subTest(field=field):
                result = self.screen(synthetic_evm(**{field: value}))
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["checks"][field], int(value))
                self.assertTrue(any(field in reason for reason in result["reasons"]))
        for field in synthetic_evm():
            with self.subTest(missing=field):
                incomplete = synthetic_evm()
                incomplete.pop(field)
                result = self.screen(incomplete)
                self.assertEqual(result["status"], "unknown")
                self.assertIn(field, result["missing_fields"])
                self.assertTrue(any(field in reason for reason in result["reasons"]))

    def test_solana_initialized_default_state_allows_only_authority_limited_pass(self):
        module = security_module(self)
        client = SyntheticClient(envelope(SOLANA, synthetic_solana()))
        result = module.TokenSecurity(settings(), client=client).check(pool(Chain.SOLANA, SOLANA), now=NOW)
        self.assertEqual(result["status"], "limited_checks_passed")
        self.assertEqual(result["checks"]["default_account_state"], 1)
        self.assertEqual(result["checks"]["freezable"], 0)
        self.assertEqual(result["checks"]["transfer_fee"], {})
        self.assertEqual(result["checks"]["transfer_hook"], [])
        self.assertIn("metadata_mutable", result["missing_fields"])
        self.assertIn("trusted_token", result["missing_fields"])
        self.assertTrue(any("authority" in reason.lower() for reason in result["reasons"]))
        self.assertTrue(any("simulation" in reason.lower() for reason in result["reasons"]))
        self.assertEqual(urlsplit(client.urls[0]).path, "/api/v1/solana/token_security")
        self.assertEqual(parse_qs(urlsplit(client.urls[0]).query), {"contract_addresses": [SOLANA]})
        json.dumps(result, allow_nan=False)

    def test_active_solana_authority_controls_override_incomplete_evidence(self):
        for field in SOLANA_CONTROLS + ("non_transferable",):
            with self.subTest(field=field):
                active = "1" if field == "non_transferable" else {"status": "1", "authority": []}
                evidence = synthetic_solana(**{field: active})
                evidence.pop("transfer_hook")
                result = self.screen(evidence, chain=Chain.SOLANA, address=SOLANA)
                self.assertEqual(result["status"], "unsafe")
                self.assertIn("transfer_hook", result["missing_fields"])
                self.assertTrue(any(field in reason for reason in result["reasons"]))

    def test_solana_frozen_default_state_two_is_not_initialized_one(self):
        frozen = self.screen(synthetic_solana(default_account_state="2"), chain=Chain.SOLANA, address=SOLANA)
        self.assertEqual(frozen["status"], "unsafe")
        self.assertTrue(any("frozen" in reason.lower() for reason in frozen["reasons"]))
        uninitialized = self.screen(synthetic_solana(default_account_state="0"), chain=Chain.SOLANA, address=SOLANA)
        self.assertEqual(uninitialized["status"], "unknown")
        self.assertTrue(any("default_account_state" in reason for reason in uninitialized["reasons"]))

    def test_configured_solana_fees_and_hooks_block_this_model(self):
        for field, value in (("transfer_fee", {"fee_percent": "0"}),
                             ("transfer_hook", ["SYNTHETIC program"]),
                             ("transfer_hook", [{}])):
            with self.subTest(field=field, value=value):
                evidence = synthetic_solana(**{field: value})
                evidence.pop("mintable")
                result = self.screen(evidence, chain=Chain.SOLANA, address=SOLANA)
                self.assertEqual(result["status"], "unsafe")
                self.assertTrue(any(field in reason for reason in result["reasons"]))
                json.dumps(result, allow_nan=False)

    def test_absent_or_ambiguous_solana_fields_never_establish_no_feature(self):
        for field in synthetic_solana():
            with self.subTest(missing=field):
                evidence = synthetic_solana()
                evidence.pop(field)
                result = self.screen(evidence, chain=Chain.SOLANA, address=SOLANA)
                self.assertEqual(result["status"], "unknown")
                self.assertIn(field, result["missing_fields"])
                self.assertTrue(any(field in reason for reason in result["reasons"]))
        for field in SOLANA_CONTROLS:
            for value in (None, "0", {}, {"status": ""}, {"status": False}, {"status": 0.0},
                          {"status": "0", "authority": ["SYN"]}, {"status": "0", "authority": None}):
                with self.subTest(field=field, value=value):
                    result = self.screen(synthetic_solana(**{field: value}), chain=Chain.SOLANA, address=SOLANA)
                    self.assertEqual(result["status"], "unknown")
                    self.assertIn(field, result["missing_fields"])
        for field, value in (("transfer_fee", []), ("transfer_fee", None), ("transfer_hook", {}),
                             ("transfer_hook", None), ("non_transferable", False),
                             ("default_account_state", True), ("default_account_state", "3")):
            with self.subTest(field=field, value=value):
                result = self.screen(synthetic_solana(**{field: value}), chain=Chain.SOLANA, address=SOLANA)
                self.assertEqual(result["status"], "unknown")
                self.assertIn(field, result["missing_fields"])

    def test_solana_result_identity_is_case_sensitive(self):
        address = "1" * 31 + "A"
        result = self.screen_document(envelope(address.lower(), synthetic_solana()),
                                      chain=Chain.SOLANA, address=address)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["token_address"], address)
        self.assertEqual(result["checks"], {})

    def test_solana_trust_and_metadata_do_not_prove_issuer_or_override_missing_flags(self):
        evidence = synthetic_solana(metadata_mutable={"status": "1"}, trusted_token=1,
                                    metadata={"name": "SYNTHETIC", "symbol": "SYN"})
        result = self.screen(evidence, chain=Chain.SOLANA, address=SOLANA)
        self.assertEqual(result["status"], "limited_checks_passed")
        self.assertIn("issuer authenticity", " ".join(result["reasons"]))
        evidence.pop("mintable")
        result = self.screen(evidence, chain=Chain.SOLANA, address=SOLANA)
        self.assertEqual(result["status"], "unknown")

    def test_check_timestamp_accepts_aware_datetimes_and_normalizes_utc(self):
        for now in ("2026-10-08T10:00:00+01:00",
                    datetime(2026, 10, 8, 10, tzinfo=timezone(timedelta(hours=1)))):
            with self.subTest(now=now):
                result = self.screen(synthetic_evm(), now=now)
                self.assertEqual(result["status"], "limited_checks_passed")
                self.assertEqual(result["checked_at"], "2026-10-08T09:00:00+00:00")

    def test_invalid_or_underflowing_check_timestamp_is_unknown_before_query(self):
        module = security_module(self)
        for now in ("2026-10-08T09:00:00", "", "invalid", True, False, 0,
                    "0001-01-01T00:00:00+01:00", datetime(2026, 10, 8),
                    datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1)))):
            with self.subTest(now=now):
                client = SyntheticClient(envelope(EVM, synthetic_evm()))
                try:
                    result = module.TokenSecurity(settings(), client=client).check(pool(), now=now)
                except Exception as exc:
                    self.fail("Invalid timestamp must fail closed: " + repr(exc))
                self.assertEqual(result["status"], "unknown")
                self.assertIn("checked_at", result["missing_fields"])
                self.assertEqual(datetime.fromisoformat(result["checked_at"]).utcoffset(), timedelta(0))
                self.assertEqual(client.urls, [])

    def test_malformed_pool_or_nonstring_token_is_unknown_without_query(self):
        module = security_module(self)
        candidates = [None, SimpleNamespace(), SimpleNamespace(chain=Chain.BASE)]
        candidates += [pool(address=address) for address in (None, True, 1, float("nan"), [], {}, object())]
        for candidate in candidates:
            with self.subTest(pool=repr(candidate)):
                client = SyntheticClient()
                try:
                    result = module.TokenSecurity(settings(), client=client).check(candidate, now=NOW)
                except Exception as exc:
                    self.fail("Malformed pool must fail closed: " + repr(exc))
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(client.urls, [])
                json.dumps(result, allow_nan=False)

    def test_empty_token_evidence_is_unknown_with_exact_identity(self):
        module = security_module(self)
        client = SyntheticClient({"code": 1, "result": {EVM: {}}})
        result = module.TokenSecurity(settings(), client=client).check(pool(), now=NOW)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["provider"], "goplus")
        self.assertEqual(result["chain"], "base")
        self.assertEqual(result["token_address"], EVM)
        self.assertEqual(result["checked_at"], "2026-10-08T09:00:00+00:00")
        for name, expected in (("reasons", list), ("checks", dict), ("missing_fields", list)):
            self.assertIsInstance(result[name], expected)
        json.dumps(result, allow_nan=False)
        url = urlsplit(client.urls[0])
        self.assertEqual(url.netloc, "api.gopluslabs.io")
        self.assertEqual(url.path, "/api/v1/token_security/8453")
        self.assertEqual(parse_qs(url.query), {"contract_addresses": [EVM]})


class SyntheticClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class SyntheticResponse(io.BytesIO):
    def __init__(self, body=b"{}", headers=None):
        super().__init__(body)
        self.headers = headers or {}
        self.status = 200


class SyntheticOpener:
    def __init__(self, *responses, clock=None):
        self.responses = list(responses)
        self.clock = clock
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout, self.clock() if self.clock else None))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SecurityHttpTests(unittest.TestCase):
    URL = "https://api.gopluslabs.io/api/v1/token_security/8453?contract_addresses=" + EVM

    def setUp(self):
        reset = patch.multiple(HttpClient, _last_started=None, _last_interval=0.0)
        reset.start()
        self.addCleanup(reset.stop)

    def transport(self):
        module = security_module(self)
        self.assertTrue(hasattr(module, "GoPlusHttpClient"), "Missing bounded anonymous GoPlus transport")
        return module.GoPlusHttpClient

    def test_transport_inherits_limits_without_widening_market_host_allowlist(self):
        cls = self.transport()
        self.assertTrue(issubclass(cls, HttpClient))
        self.assertEqual(cls.ALLOWED_HOSTS, frozenset({"api.gopluslabs.io"}))
        self.assertEqual(HttpClient.ALLOWED_HOSTS, frozenset({"api.geckoterminal.com", "api.dexscreener.com"}))
        self.assertIs(cls._request_slots, HttpClient._request_slots)
        self.assertIs(cls._rate_lock, HttpClient._rate_lock)
        clock = SyntheticClock()
        opener = SyntheticOpener(SyntheticResponse(), clock=clock)
        client = cls(settings(http_timeout_seconds=100), opener=opener, clock=clock, sleeper=clock.sleep)
        self.assertEqual(client.budget, 20)
        self.assertLessEqual(client.timeout, 20)
        self.assertEqual(client.get(self.URL), {})
        self.assertGreater(opener.calls[0][1], 0)
        self.assertLessEqual(opener.calls[0][1], 20)
        request = opener.calls[0][0]
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.get_header("Authorization"))

    def test_transport_rejects_unsafe_urls_and_credential_queries_before_open(self):
        cls = self.transport()
        opener = SyntheticOpener(*(SyntheticResponse() for _ in range(20)))
        client = cls(settings(), opener=opener)
        for url in (self.URL.replace("https:", "http:"), self.URL.replace("api.gopluslabs.io", "api.dexscreener.com"),
                    self.URL.replace("api.gopluslabs.io", "api.gopluslabs.io.evil.invalid"),
                    self.URL.replace("api.gopluslabs.io", "user:secret@api.gopluslabs.io"),
                    self.URL.replace("api.gopluslabs.io", "api.gopluslabs.io:444"), self.URL + "#x",
                    self.URL + "&access_token=SYNTHETIC", self.URL + "&api_key=SYNTHETIC",
                    self.URL + "&contract_addresses=" + EVM, self.URL + " ",
                    "https://api.gopluslabs.io/unrelated", "https://127.0.0.1/"):
            with self.subTest(url=url), self.assertRaises(ProviderError):
                client.get(url)
        self.assertEqual(opener.calls, [])
        with self.assertRaises(ProviderError):
            HttpClient(settings(), opener=opener).get(self.URL)

    def test_transport_limit_validation_does_not_hide_invalid_values_by_capping(self):
        cls = self.transport()
        for field in ("scan_budget_seconds", "http_timeout_seconds", "http_min_interval_seconds"):
            for value in (0, -1, True, False, "NaN", float("inf"), None):
                with self.subTest(field=field, value=value), self.assertRaises(ProviderError):
                    cls(settings(**{field: value}), opener=SyntheticOpener())
        client = cls(settings(scan_budget_seconds=3, http_timeout_seconds=9), opener=SyntheticOpener())
        self.assertEqual(client.budget, 3)
        self.assertLessEqual(client.timeout, 3)

    def test_screener_is_lazy_and_one_deadline_covers_every_check(self):
        module = security_module(self)
        cls = self.transport()
        clock = SyntheticClock()
        opener = SyntheticOpener(SyntheticResponse(json.dumps(envelope(EVM, synthetic_evm())).encode()), clock=clock)
        made = []
        def create(limits):
            made.append(cls(limits, opener=opener, clock=clock, sleeper=clock.sleep))
            return made[-1]
        with patch.object(module, "GoPlusHttpClient", side_effect=create):
            screener = module.TokenSecurity(settings())
            self.assertEqual(made, [])
            self.assertEqual(screener.check(pool(address="SYN"), now=NOW)["status"], "unknown")
            self.assertEqual(made, [])
            self.assertEqual(screener.check(pool(), now=NOW)["status"], "limited_checks_passed")
            self.assertEqual(len(made), 1)
            clock.now += 21
            self.assertEqual(screener.check(pool(), now=NOW)["status"], "unknown")
            self.assertEqual(len(made), 1)
            self.assertEqual(len(opener.calls), 1)

    def test_pacing_is_shared_with_market_requests(self):
        cls = self.transport()
        clock = SyntheticClock()
        opener = SyntheticOpener(SyntheticResponse(), SyntheticResponse(), clock=clock)
        market = HttpClient(settings(), opener=opener, clock=clock, sleeper=clock.sleep)
        risk = cls(settings(), opener=opener, clock=clock, sleeper=clock.sleep)
        market.get("https://api.dexscreener.com/token-profiles/latest/v1")
        risk.get(self.URL)
        self.assertEqual(opener.calls[1][2] - opener.calls[0][2], 6.5)

    def test_real_urllib_redirect_handling_is_inherited(self):
        cls = self.transport()
        public = importlib.import_module("avarice.chains.public")
        for target in (self.URL, "https://evil.invalid/"):
            with self.subTest(target=target), patch.object(HttpClient, "_last_started", None):
                requests = []
                class SyntheticRedirect(HTTPSHandler):
                    def https_open(self, request):
                        requests.append(request.full_url)
                        headers = Message()
                        headers["Location"] = target
                        response = addinfourl(io.BytesIO(b"{}"), headers, request.full_url, code=302)
                        response.msg = "SYNTHETIC redirect"
                        return response
                with patch.object(public, "build_opener", side_effect=lambda *handlers: build_opener(*handlers, SyntheticRedirect())):
                    clock = SyntheticClock()
                    client = cls(settings(), clock=clock, sleeper=clock.sleep)
                    with self.assertRaises(ProviderError) as caught:
                        client.get(self.URL)
                    self.assertEqual(caught.exception.status_code, 302)
                    self.assertEqual(requests, [self.URL])

    def test_duplicate_json_evidence_is_unknown_not_last_value_wins(self):
        module = security_module(self)
        cls = self.transport()
        clean = json.dumps(synthetic_evm())
        ambiguous = clean.replace('"is_honeypot": "0"', '"is_honeypot": "1", "is_honeypot": "0"')
        body = ('{"code":1,"result":{"' + EVM + '":' + ambiguous + '}}').encode()
        opener = SyntheticOpener(SyntheticResponse(body))
        client = cls(settings(), opener=opener)
        result = module.TokenSecurity(settings(), client=client).check(pool(), now=NOW)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("provider_response", result["missing_fields"])
        self.assertEqual(result["checks"], {})
        self.assertEqual(len(opener.calls), 1)

    def test_nonzero_json_tax_underflow_is_unknown_not_zero(self):
        module = security_module(self)
        cls = self.transport()
        for tax in ("1e-1000", "-1e-1000"):
            with self.subTest(tax=tax), patch.object(HttpClient, "_last_started", None):
                document = json.dumps(envelope(EVM, synthetic_evm()))
                body = document.replace('"buy_tax": "0"', '"buy_tax": ' + tax).encode()
                client = cls(settings(), opener=SyntheticOpener(SyntheticResponse(body)))
                result = module.TokenSecurity(settings(), client=client).check(pool(), now=NOW)
                self.assertEqual(result["status"], "unknown")
                self.assertIn("provider_response", result["missing_fields"])

    def test_http_429_is_unknown_after_one_anonymous_attempt(self):
        module = security_module(self)
        cls = self.transport()
        clock = SyntheticClock()
        body = io.BytesIO(b"SYNTHETIC rate limit")
        error = HTTPError(self.URL, 429, "SYNTHETIC rate limit", {}, body)
        opener = SyntheticOpener(error, SyntheticResponse(json.dumps(envelope(EVM, synthetic_evm())).encode()), clock=clock)
        client = cls(settings(), opener=opener, clock=clock, sleeper=clock.sleep)
        result = module.TokenSecurity(settings(), client=client).check(pool(), now=NOW)
        self.assertEqual(result["status"], "unknown")
        self.assertTrue(any("429" in reason for reason in result["reasons"]))
        self.assertEqual(len(opener.calls), 1)
        self.assertTrue(body.closed)

    def test_mixed_market_and_security_lingering_workers_share_capacity(self):
        cls = self.transport()
        release = threading.Event()
        finished = threading.Condition()
        state = {"calls": [], "closed": 0}
        class SyntheticLateResponse(SyntheticResponse):
            def close(self):
                if not self.closed:
                    super().close()
                    with finished:
                        state["closed"] += 1
                        finished.notify_all()
        class SyntheticBlockedOpener:
            def open(self, request, timeout):
                with finished:
                    state["calls"].append(request.full_url)
                release.wait(timeout=2.0)
                return SyntheticLateResponse()
        limits = settings(http_timeout_seconds=0.02, scan_budget_seconds=0.03, http_min_interval_seconds=0.001)
        try:
            for transport in (HttpClient, cls, HttpClient, cls):
                client = transport(limits, opener=SyntheticBlockedOpener())
                url = self.URL if transport is cls else "https://api.dexscreener.com/token-profiles/latest/v1"
                with self.assertRaises(ProviderError):
                    client.get(url)
            self.assertEqual(len(state["calls"]), 3)
            self.assertTrue(any("gopluslabs" in url for url in state["calls"]))
        finally:
            release.set()
            with finished:
                self.assertTrue(finished.wait_for(lambda: state["closed"] == len(state["calls"]), timeout=1.0))

    def test_response_byte_bound_is_inherited(self):
        cls = self.transport()
        response = SyntheticResponse(headers={"Content-Length": str(HttpClient.MAX_RESPONSE_BYTES + 1)})
        opener = SyntheticOpener(response)
        client = cls(settings(), opener=opener)
        with self.assertRaisesRegex(ProviderError, "byte limit"):
            client.get(self.URL)
        self.assertEqual(len(opener.calls), 1)
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
