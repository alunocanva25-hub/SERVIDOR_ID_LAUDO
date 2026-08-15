from __future__ import annotations
import os

ONLINE_DATABASE = bool(str(os.environ.get("DATABASE_URL") or "").strip())

if ONLINE_DATABASE:
    from postgres_storage import *  # noqa: F401,F403
else:
    from storage import *  # noqa: F401,F403

    def backend_info() -> dict:
        return {
            "mode": "LOCAL",
            "database": "SQLite",
            "persistent": True,
            "auth": "ADIADO",
            "notifications": False,
        }
