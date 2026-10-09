"""Cached evidence commands use isolated synthetic state, not public quotas."""
import contextlib
import io
import json
import unittest
from unittest.mock import patch
from helpers import scratch_dir
from avarice.cli import main
from avarice.storage.db import Storage


class EvidenceCliTests(unittest.TestCase):
    def test_cached_risk_research_and_status_need_no_network(self):
        path=scratch_dir()/"evidence-cli.sqlite3"
        check=dict(chain="base",token_address="synthetic-token",checked_at="2026-10-08T10:00:00+00:00",
                   provider="synthetic-test",status="unsafe",reasons=["synthetic-risk"],
                   checks={"is_honeypot":1},missing_fields=[])
        with Storage(path) as store:
            store.record_security(check)
        for command in ("security","research","status"):
            with self.subTest(command=command), patch("urllib.request.OpenerDirector.open") as request:
                stdout,stderr=io.StringIO(),io.StringIO()
                with contextlib.redirect_stdout(stdout),contextlib.redirect_stderr(stderr):
                    try:
                        code=main(["--db",str(path),"--json",command])
                    except SystemExit as exc:
                        self.fail(f"Cached evidence command missing: {command} ({exc.code})")
                self.assertEqual(code,0,stderr.getvalue())
                output=json.loads(stdout.getvalue())
                request.assert_not_called()
                if command=="security":
                    self.assertEqual(output,[check])
                elif command=="research":
                    self.assertEqual(output["recommendation"],"insufficient_evidence")
                    self.assertEqual(output["labelled"],0)
                    self.assertEqual(output["pending"],0)
                else:
                    self.assertEqual(output["cash_usd"],50)
                    self.assertEqual(output["research"]["labelled"],0)
