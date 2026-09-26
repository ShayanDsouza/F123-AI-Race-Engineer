"""Entry script for the PyInstaller bundle (backend/cli.py uses package-relative
imports, so it cannot be run as a top-level script)."""
import sys

from backend.cli import main

sys.exit(main())
