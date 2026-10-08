"""Isolated test state; never touch the user's portfolio or delete files."""
import os
from pathlib import Path
import sys
import tempfile


def scratch_dir():
    configured = os.environ.get("AVARICE_TEST_TMPDIR")
    root = Path(configured) if configured else (
        Path.home() / "AppData/Local/hermes/cache/scratch" if sys.platform == "win32"
        else Path(tempfile.gettempdir())
    )
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="avarice-test-", dir=root))
