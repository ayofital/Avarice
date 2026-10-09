"""SQLite state scoped to Avarice; atomic writes, no record deletion."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from avarice.core.models import Pool, VirtualPortfolio, utcnow


class Storage:
    def __init__(self, db_path, capital=50.0):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        required = {"avarice_state", "avarice_pools", "avarice_events", "avarice_scans", "avarice_security_checks"}
        existing = {row[0] for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(existing):
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS avarice_state(name TEXT PRIMARY KEY, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS avarice_pools(
                chain TEXT, address TEXT, payload TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
                PRIMARY KEY(chain, address));
            CREATE TABLE IF NOT EXISTS avarice_events(
                event_id TEXT PRIMARY KEY, chain TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS avarice_scans(
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS avarice_security_checks(
                chain TEXT NOT NULL, token_address TEXT NOT NULL,
                checked_at TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(chain,token_address,checked_at));
        """)
        if self.get_state("portfolio") is None:
            initial = json.dumps(VirtualPortfolio.new(capital).to_dict(), allow_nan=False)
            self.conn.execute("INSERT OR IGNORE INTO avarice_state(name,payload) VALUES ('portfolio',?)", (initial,))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    @contextmanager
    def atomic(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def get_state(self, name, default=None):
        row = self.conn.execute("SELECT payload FROM avarice_state WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, name, value):
        self.conn.execute("""INSERT INTO avarice_state(name,payload) VALUES (?,?)
            ON CONFLICT(name) DO UPDATE SET payload=excluded.payload""",
            (name, json.dumps(value, allow_nan=False)))

    def load_portfolio(self):
        return VirtualPortfolio.from_dict(self.get_state("portfolio"))

    def save_portfolio(self, portfolio):
        self.set_state("portfolio", portfolio.to_dict())

    def save_pool(self, pool):
        timestamp = utcnow()
        cursor = self.conn.execute("""INSERT OR IGNORE INTO avarice_pools
            (chain,address,payload,first_seen,last_seen) VALUES (?,?,?,?,?)""",
            (*pool.key, json.dumps(pool.to_dict(), allow_nan=False), timestamp, timestamp))
        new = cursor.rowcount == 1
        if not new:
            self.conn.execute("UPDATE avarice_pools SET payload=?,last_seen=? WHERE chain=? AND address=?",
                              (json.dumps(pool.to_dict(), allow_nan=False), timestamp, *pool.key))
        return new

    def pools(self, chain=None):
        if chain:
            rows = self.conn.execute("SELECT payload FROM avarice_pools WHERE chain=?", (chain,))
        else:
            rows = self.conn.execute("SELECT payload FROM avarice_pools")
        return [Pool.from_dict(json.loads(row[0])) for row in rows]

    def record_event(self, chain, event):
        cursor = self.conn.execute("INSERT OR IGNORE INTO avarice_events(event_id,chain,payload) VALUES (?,?,?)",
                                   (chain + ":" + event["id"], chain, json.dumps(event, allow_nan=False)))
        return cursor.rowcount == 1

    def record_security(self, check):
        self.conn.execute("""INSERT OR IGNORE INTO avarice_security_checks
            (chain,token_address,checked_at,payload) VALUES (?,?,?,?)""",
            (check["chain"], check["token_address"], check["checked_at"],
             json.dumps(check, allow_nan=False)))

    def latest_security(self, chain, token_address):
        row = self.conn.execute("""SELECT payload FROM avarice_security_checks
            WHERE chain=? AND token_address=? ORDER BY checked_at DESC LIMIT 1""",
            (chain, token_address)).fetchone()
        return json.loads(row[0]) if row else None

    def security_checks(self):
        rows = self.conn.execute("""SELECT c.payload FROM avarice_security_checks c
            WHERE c.checked_at=(SELECT max(p.checked_at) FROM avarice_security_checks p
                WHERE p.chain=c.chain AND p.token_address=c.token_address)
            ORDER BY c.chain,c.token_address""")
        return [json.loads(row[0]) for row in rows]

    def save_scan(self, result):
        self.conn.execute("INSERT INTO avarice_scans(timestamp,payload) VALUES (?,?)",
                          (result["timestamp"], json.dumps(result, allow_nan=False)))

    def latest_scan(self):
        row = self.conn.execute("SELECT payload FROM avarice_scans ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None