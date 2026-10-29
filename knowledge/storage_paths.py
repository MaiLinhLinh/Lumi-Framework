"""Resolve persistent knowledge storage independently of the application code."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STORAGE_ROOT = APP_ROOT.parents[1] / "knowledge_storage"


def storage_path(variable: str, default: Path) -> Path:
    """Read app-local configuration lazily; explicit process settings win.

    Relative configured paths are anchored to the app, never the shell cwd.
    Importing this module does not open or create a database.
    """
    load_dotenv(APP_ROOT / ".env", override=False)
    value = os.environ.get(variable, "").strip()
    path = Path(value).expanduser() if value else default
    return (path if path.is_absolute() else APP_ROOT / path).resolve()
