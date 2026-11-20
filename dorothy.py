#!/usr/bin/env python3
"""Dorothy — run from any working directory with Python 3.10+."""
from pathlib import Path
from dorothy_lib.ui import main

if __name__ == '__main__':
    raise SystemExit(main(Path(__file__).resolve().parent))
