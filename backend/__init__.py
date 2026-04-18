"""Package init — loads the project's .env file so running a plain
`uvicorn backend.main:app --reload` picks up CLERK_* (and any other env
variable you put in .env) without the caller having to remember
`--env-file .env`.

This MUST run before any sibling module is imported, because `auth.py`
reads env vars at module-load time — this __init__.py runs first by
virtue of being the package entry point.
"""
from __future__ import annotations

from pathlib import Path

try:
    from dotenv import load_dotenv

    # Project root is the parent of this package dir.
    _env_path = Path(__file__).resolve().parent.parent / ".env"
    if _env_path.exists():
        # override=False so variables already set in the real environment
        # (e.g. by Docker / K8s / App Runner) still win — the .env file is
        # only a fallback for local development.
        load_dotenv(_env_path, override=False)
except ImportError:
    # python-dotenv isn't installed — that's fine, the user can still set
    # env vars manually or via `uvicorn --env-file`.
    pass
