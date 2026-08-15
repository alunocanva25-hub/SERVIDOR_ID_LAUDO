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
            "auth": "DESATIVADO",
            "notifications": False,
        }

    def get_app_user(user_id: int):
        users = list_app_users()
        return next((u for u in users if int(u.get("id", -1)) == int(user_id)), None)

    def bind_auth_profile(auth_user_id: str, email: str, nome: str = ""):
        return None

    def mark_password_changed(profile_id: int) -> None:
        return None

    def set_app_user_active(user_id: int, active: bool):
        user = get_app_user(user_id)
        if not user:
            return None
        user["ativo"] = bool(active)
        return save_app_user(user, user_id=user_id)
