"""Module entrypoint enabling `python -m arbiter`."""

from __future__ import annotations

from arbiter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
