"""Cached report only: no network, LLM, bot token or direct message sending."""
from pathlib import Path
import sys


def run(project_root):
    sys.path.insert(0, str(Path(project_root).resolve()))
    from avarice.cli import main
    return main(["report"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python cron_report.py PATH_TO_AVARICE")
    raise SystemExit(run(sys.argv[1]))
