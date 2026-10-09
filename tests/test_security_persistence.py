"""Risk evidence history is scoped, persistent and non-destructive."""
import unittest
from helpers import scratch_dir
from avarice.storage.db import Storage


class SecurityPersistenceTests(unittest.TestCase):
    def test_security_history_retains_old_evidence_and_is_chain_qualified(self):
        db=scratch_dir()/"security.sqlite3"
        first=dict(chain="base",token_address="synthetic-token",checked_at="2026-10-08T10:00:00+00:00",
                   status="limited_checks_passed",provider="synthetic-test",reasons=[])
        second={**first,"checked_at":"2026-10-08T10:10:00+00:00","status":"unsafe","reasons":["honeypot_flag"]}
        with Storage(db) as store:
            store.record_security(first)
            store.record_security(first)
            store.record_security(second)
            self.assertEqual(store.latest_security("base","synthetic-token")["status"],"unsafe")
            self.assertIsNone(store.latest_security("ethereum","synthetic-token"))
            self.assertEqual(store.conn.execute("SELECT count(*) FROM avarice_security_checks").fetchone()[0],2)
            self.assertEqual(store.load_portfolio().cash_usd,50)
        with Storage(db) as store:
            self.assertEqual(store.latest_security("base","synthetic-token"),second)
            self.assertEqual(store.load_portfolio().cash_usd,50)
