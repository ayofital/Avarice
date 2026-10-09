"""Isolated SQLite concurrency; do not inspect the deployed portfolio."""
import threading
import unittest
from helpers import scratch_dir
from avarice.storage.db import Storage


class StorageConcurrencyTests(unittest.TestCase):
    def test_cached_account_reader_does_not_wait_for_scan_writer(self):
        path=scratch_dir()/"reader-writer.sqlite3"
        done=threading.Event()
        result=[]
        def reader():
            try:
                with Storage(path) as cached:
                    result.append(cached.load_portfolio().cash_usd)
            except Exception as exc:
                result.append(type(exc).__name__)
            finally:
                done.set()
        with Storage(path) as writer:
            with writer.atomic():
                writer.set_state("synthetic-uncommitted-scan",{})
                thread=threading.Thread(target=reader)
                thread.start()
                read_during_scan=done.wait(1)
            thread.join(5)
        self.assertTrue(read_during_scan,"Cached report readers must not wait behind the network-held scan transaction")
        self.assertEqual(result,[50])
