from __future__ import annotations

import json
import os
from typing import Iterable

try:
    import firebase_admin
    from firebase_admin import credentials, messaging
except Exception:  # pragma: no cover - servidor continua funcional sem push
    firebase_admin = None
    credentials = None
    messaging = None


def _clean(value) -> str:
    return str(value or "").strip()


def client_config() -> dict:
    """Configuração pública usada pelo APK para inicializar o Firebase em runtime."""
    return {
        "api_key": _clean(os.environ.get("FIREBASE_API_KEY")),
        "app_id": _clean(os.environ.get("FIREBASE_APP_ID")),
        "project_id": _clean(os.environ.get("FIREBASE_PROJECT_ID")),
        "sender_id": _clean(os.environ.get("FIREBASE_SENDER_ID")),
    }


def client_configured() -> bool:
    cfg = client_config()
    return all(cfg.values())


def _service_account_info() -> dict:
    raw = _clean(os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"))
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def admin_configured() -> bool:
    info = _service_account_info()
    return bool(info.get("project_id") and info.get("client_email") and info.get("private_key"))


def configured() -> bool:
    return bool(client_configured() and admin_configured() and firebase_admin and messaging and credentials)


def status() -> dict:
    cfg = client_config()
    return {
        "enabled": configured(),
        "client_configured": client_configured(),
        "admin_configured": admin_configured(),
        "project_id": cfg.get("project_id", "") if client_configured() else "",
    }


def _ensure_app():
    if not configured():
        return None
    try:
        return firebase_admin.get_app()
    except Exception:
        info = _service_account_info()
        cred = credentials.Certificate(info)
        return firebase_admin.initialize_app(cred, {"projectId": info.get("project_id")})


def send_status_push(tokens: Iterable[str], *, status: str, record: dict, message: str = "") -> dict:
    """Envia push para os dispositivos do técnico responsável pelo espelho."""
    clean_tokens = []
    seen = set()
    for token in tokens or []:
        t = _clean(token)
        if t and t not in seen:
            seen.add(t)
            clean_tokens.append(t)
    if not clean_tokens:
        return {"attempted": 0, "sent": 0, "failed": 0, "configured": configured()}
    if not configured():
        return {"attempted": len(clean_tokens), "sent": 0, "failed": len(clean_tokens), "configured": False}

    _ensure_app()
    status_u = _clean(status).upper()
    nr = _clean(record.get("numero_laudo")) or "—"
    tipo = _clean(record.get("tipo")) or "NR"
    if status_u == "DEVOLVIDO":
        title = "Laudo devolvido para correção"
        body = _clean(message) or f"O laudo {tipo}-{nr} precisa de correção."
    elif status_u == "LAUDO_CRIADO":
        title = "Laudo criado"
        body = _clean(message) or f"O laudo {tipo}-{nr} foi criado no ID CAMPS."
    else:
        title = "Atualização do laudo"
        body = _clean(message) or f"O status do laudo {tipo}-{nr} foi atualizado."

    data = {
        "type": "LAUDO_STATUS",
        "status": status_u,
        "record_id": str(record.get("id") or ""),
        "numero_laudo": nr,
        "tipo_laudo": tipo,
        "message": body,
    }

    sent = 0
    failed = 0
    errors = []
    for token in clean_tokens:
        try:
            msg = messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
                data=data,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id="id_laudo_status",
                        icon="ic_notification",
                        sound="default",
                    ),
                ),
            )
            messaging.send(msg)
            sent += 1
        except Exception as exc:
            failed += 1
            errors.append(str(exc))
    return {
        "attempted": len(clean_tokens),
        "sent": sent,
        "failed": failed,
        "configured": True,
        "errors": errors[:3],
    }
