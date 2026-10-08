"""Copy under the active Hermes scripts directory; repository path is deployment-specific."""
from pathlib import Path
import sys


def run(project_root):
    sys.path.insert(0, str(Path(project_root).resolve()))
    from avarice.cli import main
    return main(["--json", "scan"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python cron_scan.py PATH_TO_AVARICE")
    raise SystemExit(run(sys.argv[1]))
