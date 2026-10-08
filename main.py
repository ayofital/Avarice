"""Compatibility entry point. Prefer python -m avarice."""
from avarice.cli import main

if __name__ == "__main__":
    raise SystemExit(main())