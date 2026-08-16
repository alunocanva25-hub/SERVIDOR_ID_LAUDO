from __future__ import annotations

from datetime import datetime
from pathlib import Path
import base64
import json
import os
import sqlite3
import subprocess
import sys

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from storage_backend import (
    APP_NAME, APP_VERSION, STATUS_AGUARDANDO, STATUS_CRIADO, STATUS_DEVOLVIDO,
    STATUS_PRONTO, STATUS_RASCUNHO, STATUS_REVISAO, app_data_dir, delete_record,
    export_bridge, get_record, list_records, save_record, update_record_status,
    list_app_users, save_app_user, delete_app_user, get_app_setting, set_app_setting,
    list_notifications, backend_info, bootstrap_admin_info, get_app_user, bind_auth_profile,
    mark_password_changed, set_app_user_active, require_password_change_by_email,
    archive_notification, register_push_device, unregister_push_devices, list_push_tokens_for_record,
    list_panel_users, create_panel_assignment, get_panel_assignment, list_panel_assignments, update_panel_assignment,
    add_audit_event, list_audit_events, STATUS_AGUARDANDO_BAIXA, STATUS_BAIXADO, STATUS_CORRECAO_PDF,
)
import auth_service
import push_service

RESOURCE_BASE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
STATIC_DIR = RESOURCE_BASE / "static"
BUNDLED_CATALOG_DB = RESOURCE_BASE / "database" / "dados_criacao_laudos.db"
CATALOG_CONFIG = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ID LAUDO" / "catalog_source.txt"
USE_POSTGRES_CATALOG = bool(str(os.environ.get("DATABASE_URL") or "").strip()) and str(os.environ.get("ID_LAUDO_CATALOG_BACKEND") or "").strip().lower() == "postgres"
if USE_POSTGRES_CATALOG:
    import postgres_catalog as pg_catalog

def catalog_db_path() -> Path:
    env_path = clean(os.environ.get("ID_CAMPS_CATALOG_DB", ""))
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p
    try:
        if CATALOG_CONFIG.exists():
            raw = clean(CATALOG_CONFIG.read_text(encoding="utf-8"))
            if raw:
                p = Path(raw)
                if p.exists():
                    return p
    except Exception:
        pass
    return BUNDLED_CATALOG_DB

app = FastAPI(title=APP_NAME, version=APP_VERSION)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def clean(value) -> str:
    return str(value or "").strip()


def _runtime_backend_info() -> dict:
    info = dict(backend_info())
    ac = auth_service.config_status()
    if ac.get("requested"):
        info["auth"] = "ATIVO" if ac.get("enabled") else "CONFIGURAR"
    else:
        info["auth"] = "DESATIVADO"
    info["push"] = "FCM_ATIVO" if push_service.configured() else "CONFIGURAR"
    return info


def _bearer_token(request: Request) -> str:
    raw = clean(request.headers.get("Authorization"))
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return ""


def _profile_public(profile: dict | None) -> dict:
    p = dict(profile or {})
    return {
        "id": p.get("id"),
        "nome": p.get("nome") or "",
        "usuario": p.get("usuario") or "",
        "email": p.get("email") or "",
        "perfil": p.get("perfil") or "OPERADOR",
        "ativo": bool(p.get("ativo")),
        "must_change_password": bool(p.get("must_change_password")),
        "is_system_admin": bool(p.get("is_system_admin")),
    }


def _resolve_profile_from_access_token(access_token: str) -> tuple[dict, dict]:
    user = auth_service.get_user(access_token)
    uid = clean(user.get("id"))
    email = clean(user.get("email")).lower()
    metadata = dict(user.get("user_metadata") or {})
    profile = bind_auth_profile(uid, email, clean(metadata.get("name")))
    if not profile:
        raise auth_service.AuthServiceError("Usuário não autorizado no ID LAUDO.", 403)
    if not bool(profile.get("ativo")):
        raise auth_service.AuthServiceError("Usuário suspenso. Procure um administrador.", 403)
    return user, profile


def _require_admin(request: Request) -> dict:
    profile = getattr(request.state, "profile", None) or {}
    if clean(profile.get("perfil")).upper() != "ADMIN":
        raise HTTPException(403, "Acesso permitido somente para ADMIN.")
    return profile


def _require_roles(request: Request, *roles: str) -> dict:
    profile = getattr(request.state, "profile", None) or {}
    allowed = {clean(r).upper() for r in roles if clean(r)}
    if clean(profile.get("perfil")).upper() not in allowed:
        raise HTTPException(403, "Seu perfil não possui permissão para esta ação.")
    return profile


def _audit(request: Request, action: str, *, entity_type: str = "", entity_id: object = "", numero_laudo: str = "", details: dict | None = None):
    try:
        return add_audit_event(getattr(request.state, "profile", None) or {}, action, entity_type=entity_type, entity_id=entity_id, numero_laudo=numero_laudo, details=details or {})
    except Exception:
        return None


def _record_scope(request: Request) -> tuple[int | None, bool]:
    profile = getattr(request.state, "profile", None) or {}
    if not profile:
        return None, True
    return (int(profile.get("id")) if profile.get("id") else None, clean(profile.get("perfil")).upper() == "ADMIN")


PUBLIC_API_PATHS = {
    "/api/health", "/api/auth/config", "/api/auth/login", "/api/auth/refresh",
    "/api/auth/forgot-password", "/api/auth/recovery/verify",
}


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API_PATHS or not auth_service.auth_requested():
        return await call_next(request)
    if not auth_service.auth_configured():
        return JSONResponse({"detail": "Login obrigatório, mas as chaves do Supabase Auth ainda não foram informadas no Render."}, status_code=503)
    token = _bearer_token(request)
    if not token:
        return JSONResponse({"detail": "Sessão necessária."}, status_code=401)
    try:
        user, profile = _resolve_profile_from_access_token(token)
    except auth_service.AuthServiceError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)
    request.state.auth_user = user
    request.state.profile = profile
    request.state.access_token = token
    return await call_next(request)


def catalog_connect():
    db = catalog_db_path()
    if not db.exists():
        raise HTTPException(500, "Banco de cadastros não encontrado.")
    con = sqlite3.connect(db, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def list_models(search: str = "", limit: int = 500) -> list[dict]:
    if USE_POSTGRES_CATALOG:
        return pg_catalog.list_models(search, limit)
    with catalog_connect() as con:
        params = []
        sql = "SELECT * FROM cadastro_modelos"
        if clean(search):
            sql += " WHERE UPPER(modelo) LIKE ? OR UPPER(fabricante) LIKE ?"
            term = f"%{clean(search).upper()}%"
            params.extend([term, term])
        sql += " ORDER BY modelo, fabricante LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        return [dict(r) for r in con.execute(sql, params).fetchall()]


def list_observations() -> list[dict]:
    if USE_POSTGRES_CATALOG:
        return pg_catalog.list_observations()
    with catalog_connect() as con:
        rows = con.execute("SELECT id, TRIM(COALESCE(observacao,'')) observacao, TRIM(COALESCE(conclusao,'')) conclusao FROM cadastro_observacoes WHERE TRIM(COALESCE(observacao,''))<>'' ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def list_observation_portarias() -> list[str]:
    if USE_POSTGRES_CATALOG:
        return pg_catalog.list_observation_portarias()
    with catalog_connect() as con:
        rows = con.execute("SELECT TRIM(COALESCE(conclusao,'')) texto FROM cadastro_observacoes WHERE TRIM(COALESCE(conclusao,''))<>'' ORDER BY id").fetchall()
    seen = set()
    out = []
    for row in rows:
        text = clean(row["texto"])
        key = text.upper()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def list_people() -> dict[str, list[str]]:
    if USE_POSTGRES_CATALOG:
        return pg_catalog.list_people()
    result: dict[str, list[str]] = {}
    with catalog_connect() as con:
        rows = con.execute("SELECT categoria, TRIM(nome) nome FROM cadastro_pessoas WHERE TRIM(COALESCE(nome,''))<>'' ORDER BY categoria,nome").fetchall()
    for row in rows:
        result.setdefault(clean(row["categoria"]), []).append(clean(row["nome"]))
    return result


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/password-reset")
def password_reset_page():
    # Rota dedicada para o retorno do e-mail de recuperação do Supabase.
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/api/health")
def health():
    info = _runtime_backend_info()
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION, "backend": info}


@app.get("/api/backend-info")
def api_backend_info():
    return {"ok": True, **_runtime_backend_info(), "catalog": "POSTGRESQL" if USE_POSTGRES_CATALOG else "SQLITE"}


def _apply_emergency_password_reset() -> dict:
    """Aplica uma redefinição emergencial uma única vez por token definido no Render.

    Nenhum segredo é retornado pela API. Troque o token para solicitar uma nova execução.
    """
    token = clean(os.environ.get("ID_LAUDO_EMERGENCY_RESET_TOKEN"))
    email = clean(os.environ.get("ID_LAUDO_EMERGENCY_RESET_EMAIL")).lower()
    password = str(os.environ.get("ID_LAUDO_EMERGENCY_RESET_PASSWORD") or "")
    if not token and not email and not password:
        return {"requested": False, "applied": False}
    if not token or not email or len(password) < 8:
        return {"requested": True, "applied": False, "reason": "incomplete_environment"}
    marker_key = "auth.emergency_reset_token"
    if str(get_app_setting(marker_key, "") or "") == token:
        return {"requested": True, "applied": False, "reason": "already_applied"}
    if not auth_service.admin_configured():
        return {"requested": True, "applied": False, "reason": "admin_api_not_configured"}
    try:
        auth_service.admin_force_password_by_email(email, password)
        require_password_change_by_email(email)
        set_app_setting(marker_key, token)
        return {"requested": True, "applied": True}
    except auth_service.AuthServiceError as exc:
        return {"requested": True, "applied": False, "reason": str(exc)}


@app.get("/api/auth/config")
def auth_config():
    status = auth_service.config_status()
    emergency_reset = _apply_emergency_password_reset()
    admin = bootstrap_admin_info()
    bootstrap_auth = {"ready": False, "reason": "not_requested"}
    if status.get("requested") and status.get("admin_api"):
        admin_password = clean(os.environ.get("ID_LAUDO_BOOTSTRAP_ADMIN_PASSWORD"))
        bootstrap_auth = auth_service.bootstrap_primary_admin(
            clean(os.environ.get("ID_LAUDO_BOOTSTRAP_ADMIN_EMAIL") or "dayvisant4@gmail.com"),
            admin_password,
        )
        if bootstrap_auth.get("ready") and bootstrap_auth.get("auth_user_id"):
            bind_auth_profile(
                bootstrap_auth.get("auth_user_id"),
                bootstrap_auth.get("email"),
                "Administrador principal",
            )
            admin = bootstrap_admin_info()
    return {
        "ok": True,
        **status,
        "admin_ready": bool(admin.get("auth_linked")) or bool(bootstrap_auth.get("ready")),
        "password_reset": bool(status.get("configured")),
        # Publishable key e segura para o cliente. A V32 usa o Supabase JS apenas
        # para processar a sessao temporaria do link padrao de recuperacao.
        "supabase_publishable_key": auth_service.public_key() if status.get("configured") else "",
        "recovery_redirect": auth_service.reset_redirect_url(),
        "initial_admin": {"configured": bool(admin.get("configured")), "perfil": "ADMIN"},
        "bootstrap_reason": bootstrap_auth.get("reason", "") if not bootstrap_auth.get("ready") else "",
        "emergency_reset": emergency_reset,
    }


@app.post("/api/auth/login")
def auth_login(payload: dict = Body(...)):
    if not auth_service.auth_enabled():
        raise HTTPException(503, "Supabase Auth ainda não está configurado no Render.")
    try:
        session = auth_service.sign_in(clean(payload.get("email")), str(payload.get("password") or ""))
        _, profile = _resolve_profile_from_access_token(clean(session.get("access_token")))
    except auth_service.AuthServiceError as exc:
        message = str(exc)
        if exc.status_code in {400, 401}:
            message = "E-mail ou senha inválidos."
        raise HTTPException(exc.status_code, message)
    try:
        add_audit_event(profile, "LOGIN", entity_type="SESSAO", details={"client": clean(payload.get("client")) or "ID LAUDO"})
    except Exception:
        pass
    return {
        "ok": True,
        "session": {
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "expires_in": session.get("expires_in"),
            "expires_at": session.get("expires_at"),
            "token_type": session.get("token_type") or "bearer",
        },
        "user": _profile_public(profile),
    }


@app.post("/api/auth/refresh")
def auth_refresh(payload: dict = Body(...)):
    try:
        session = auth_service.refresh_session(clean(payload.get("refresh_token")))
        _, profile = _resolve_profile_from_access_token(clean(session.get("access_token")))
    except auth_service.AuthServiceError as exc:
        raise HTTPException(exc.status_code, "Sessão expirada. Entre novamente.")
    return {
        "ok": True,
        "session": {
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "expires_in": session.get("expires_in"),
            "expires_at": session.get("expires_at"),
            "token_type": session.get("token_type") or "bearer",
        },
        "user": _profile_public(profile),
    }


@app.get("/api/auth/me")
def auth_me(request: Request):
    return {"ok": True, "user": _profile_public(getattr(request.state, "profile", None))}


@app.post("/api/auth/forgot-password")
def auth_forgot_password(payload: dict = Body(...)):
    email = clean(payload.get("email")).lower()
    if not email:
        raise HTTPException(400, "Informe o e-mail.")
    try:
        auth_service.send_password_reset(email)
    except auth_service.AuthServiceError as exc:
        if exc.status_code >= 500:
            raise HTTPException(exc.status_code, str(exc))
    return {"ok": True, "message": "Se o e-mail estiver cadastrado, você receberá as instruções para redefinir a senha."}


@app.post("/api/auth/recovery/verify")
def auth_recovery_verify(payload: dict = Body(...)):
    """Compatibilidade legada V31 para links TokenHash ja emitidos."""
    token_hash = clean(payload.get("token_hash"))
    try:
        session = auth_service.verify_recovery_token_hash(token_hash)
    except auth_service.AuthServiceError as exc:
        raise HTTPException(exc.status_code, str(exc))
    return {
        "ok": True,
        "session": {
            "access_token": session.get("access_token"),
            "refresh_token": session.get("refresh_token"),
            "expires_in": session.get("expires_in"),
            "expires_at": session.get("expires_at"),
            "token_type": session.get("token_type") or "bearer",
        },
    }


@app.post("/api/auth/change-password")
def auth_change_password(request: Request, payload: dict = Body(...)):
    new_password = str(payload.get("password") or "")
    try:
        auth_service.update_password(getattr(request.state, "access_token", ""), new_password)
        profile = getattr(request.state, "profile", None) or {}
        if profile.get("id"):
            mark_password_changed(int(profile["id"]))
        auth_user = getattr(request.state, "auth_user", None) or {}
        if auth_user.get("id") and auth_service.admin_configured():
            try:
                auth_service.admin_update_user(str(auth_user["id"]), must_change=False)
            except Exception:
                pass
    except auth_service.AuthServiceError as exc:
        raise HTTPException(exc.status_code, str(exc))
    _audit(request, "ALTEROU_SENHA", entity_type="CONTA")
    return {"ok": True}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    _audit(request, "LOGOUT", entity_type="SESSAO")
    try:
        auth_service.sign_out(getattr(request.state, "access_token", ""))
    except auth_service.AuthServiceError:
        # O app limpa a sessão local mesmo se a sessão remota já tiver expirado.
        pass
    return {"ok": True}


@app.get("/api/statuses")
def statuses():
    return {
        "ok": True,
        "items": [
            {"id": STATUS_RASCUNHO, "label": "Rascunho"},
            {"id": STATUS_PRONTO, "label": "Pronto para ID CAMPS"},
            {"id": STATUS_AGUARDANDO, "label": "Aguardando revisão"},
            {"id": STATUS_REVISAO, "label": "Em revisão"},
            {"id": STATUS_DEVOLVIDO, "label": "Devolvido para correção"},
            {"id": STATUS_CRIADO, "label": "Laudo criado"},
            {"id": STATUS_AGUARDANDO_BAIXA, "label": "Aguardando baixa", "scope": "PAINEL"},
            {"id": STATUS_BAIXADO, "label": "Baixado", "scope": "PAINEL"},
            {"id": STATUS_CORRECAO_PDF, "label": "Correção de PDF", "scope": "PAINEL"},
        ],
    }


@app.get("/api/bootstrap")
def bootstrap():
    models = list_models()
    observations = list_observations()
    portarias = list_observation_portarias()
    people = list_people()
    manufacturers = sorted({clean(m.get("fabricante")) for m in models if clean(m.get("fabricante"))})
    return {
        "ok": True,
        "app": APP_NAME,
        "version": APP_VERSION,
        "year": datetime.now().year,
        "models": models,
        "observations": observations,
        "observation_portarias": portarias,
        "people": people,
        "manufacturers": manufacturers,
        "counts": {"models": len(models), "observations": len(observations), "people": sum(len(v) for v in people.values())},
        "data_dir": str(app_data_dir()),
        "source": ("ID CAMPS • POSTGRESQL" if USE_POSTGRES_CATALOG else f"ID CAMPS • {catalog_db_path().name}" + (" • SINCRONIZADO" if catalog_db_path() != BUNDLED_CATALOG_DB else " • BASE LOCAL")),
        "backend": _runtime_backend_info(),
    }


@app.get("/api/models")
def models(search: str = ""):
    return {"ok": True, "items": list_models(search)}


@app.get("/api/observations")
def observations():
    return {"ok": True, "items": list_observations()}


@app.get("/api/records")
def records(request: Request):
    profile_id, include_all = _record_scope(request)
    return {"ok": True, "items": list_records(profile_id=profile_id, include_all=include_all)}

@app.get("/api/notifications")
def api_notifications(request: Request):
    _require_admin(request)
    return {"ok": True, "items": list_notifications()}


@app.delete("/api/notifications/{notification_id}")
def api_notification_delete(notification_id: int, request: Request):
    _require_admin(request)
    if not archive_notification(notification_id):
        raise HTTPException(404, "Notificação não encontrada.")
    return {"ok": True}


@app.get("/api/push/config")
def api_push_config(request: Request):
    # Somente configuração pública. Nenhuma credencial administrativa é exposta.
    cfg = push_service.client_config()
    return {"ok": True, "enabled": push_service.configured(), **cfg}


@app.post("/api/push/register")
def api_push_register(request: Request, payload: dict = Body(...)):
    profile = getattr(request.state, "profile", None) or {}
    profile_id = profile.get("id")
    if not profile_id:
        raise HTTPException(401, "Sessão necessária para registrar notificações.")
    token = clean(payload.get("token"))
    if not token:
        raise HTTPException(400, "Token FCM não informado.")
    try:
        item = register_push_device(int(profile_id), token, clean(payload.get("device_name") or "Android"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": item, "push": push_service.status()}


@app.post("/api/push/unregister")
def api_push_unregister(request: Request, payload: dict = Body(default={})):
    profile = getattr(request.state, "profile", None) or {}
    profile_id = profile.get("id")
    if not profile_id:
        raise HTTPException(401, "Sessão necessária.")
    removed = unregister_push_devices(int(profile_id), clean(payload.get("token")))
    return {"ok": True, "updated": removed}


@app.get("/api/config")
def config_get(request: Request):
    profile = getattr(request.state, "profile", None) or {}
    is_admin = clean(profile.get("perfil")).upper() == "ADMIN"
    return {
        "ok": True,
        "users": list_app_users() if is_admin else [],
        "form_visibility": get_app_setting("form_visibility", {}),
        "backend": _runtime_backend_info(),
        "primary_admin": bootstrap_admin_info() if is_admin else {"configured": True},
        "current_user": _profile_public(profile),
        "permissions": {
            "admin": is_admin,
            "database": is_admin,
            "audit": is_admin,
            "operate": clean(profile.get("perfil")).upper() in {"ADMIN", "OPERADOR"},
            "function": clean(profile.get("perfil")).upper() == "FUNCAO",
        },
    }


@app.post("/api/config/users")
def config_user_save(request: Request, payload: dict = Body(...)):
    _require_admin(request)
    data = dict(payload.get("data") or {})
    user_id = payload.get("id")
    email = clean(data.get("email")).lower()
    if not email:
        raise HTTPException(400, "E-mail é obrigatório para usuários com login online.")
    existing_profile = get_app_user(int(user_id)) if user_id else None
    auth_user_id = clean((existing_profile or {}).get("auth_user_id"))
    created_auth_id = ""
    reset_sent = False
    try:
        if auth_service.auth_enabled():
            if not auth_service.admin_configured():
                raise auth_service.AuthServiceError("Chave secreta do Supabase não configurada no Render.", 503)
            if auth_user_id:
                auth_service.admin_update_user(
                    auth_user_id, email=email, name=clean(data.get("nome")), role=clean(data.get("perfil") or "OPERADOR")
                )
            else:
                existing_auth = auth_service.admin_find_user_by_email(email)
                if existing_auth:
                    auth_user_id = clean(existing_auth.get("id"))
                    auth_service.admin_update_user(
                        auth_user_id, name=clean(data.get("nome")), role=clean(data.get("perfil") or "OPERADOR"), must_change=True
                    )
                else:
                    temp = auth_service.random_temporary_password()
                    created = auth_service.admin_create_user(
                        email, temp, clean(data.get("nome")), clean(data.get("perfil") or "OPERADOR"), must_change=True
                    )
                    auth_user_id = clean(created.get("id"))
                    created_auth_id = auth_user_id
            data["auth_user_id"] = auth_user_id
            data["must_change_password"] = bool((existing_profile or {}).get("must_change_password", True)) if user_id else True
        item = save_app_user(data, user_id=user_id)
        if not user_id and auth_service.auth_enabled():
            try:
                auth_service.send_password_reset(email)
                reset_sent = True
            except auth_service.AuthServiceError:
                reset_sent = False
    except ValueError as exc:
        if created_auth_id:
            try: auth_service.admin_delete_user(created_auth_id, soft=True)
            except Exception: pass
        raise HTTPException(400, str(exc))
    except auth_service.AuthServiceError as exc:
        raise HTTPException(exc.status_code, str(exc))
    _audit(request, "EDITOU_USUARIO" if user_id else "CRIOU_USUARIO", entity_type="USUARIO", entity_id=item.get("id") or user_id or "", details={"nome": item.get("nome"), "email": item.get("email"), "perfil": item.get("perfil")})
    return {"ok": True, "item": item, "reset_sent": reset_sent}


@app.post("/api/config/users/{user_id}/suspend")
def config_user_suspend(user_id: int, request: Request, payload: dict = Body(...)):
    _require_admin(request)
    row = get_app_user(user_id)
    if not row:
        raise HTTPException(404, "Usuário não encontrado.")
    suspended = bool(payload.get("suspended", True))
    auth_user_id = clean(row.get("auth_user_id"))
    try:
        if auth_user_id and auth_service.admin_configured():
            auth_service.admin_set_suspended(auth_user_id, suspended)
        item = set_app_user_active(user_id, not suspended)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except auth_service.AuthServiceError as exc:
        raise HTTPException(exc.status_code, str(exc))
    _audit(request, "SUSPENDEU_USUARIO" if suspended else "REATIVOU_USUARIO", entity_type="USUARIO", entity_id=user_id, details={"nome": row.get("nome"), "email": row.get("email")})
    return {"ok": True, "item": item}


@app.post("/api/config/users/{user_id}/reset-password")
def config_user_reset_password(user_id: int, request: Request):
    _require_admin(request)
    row = get_app_user(user_id)
    if not row:
        raise HTTPException(404, "Usuário não encontrado.")
    email = clean(row.get("email"))
    if not email:
        raise HTTPException(400, "Este usuário não possui e-mail cadastrado.")
    try:
        auth_service.send_password_reset(email)
    except auth_service.AuthServiceError as exc:
        raise HTTPException(exc.status_code, str(exc))
    _audit(request, "SOLICITOU_RESET_SENHA", entity_type="USUARIO", entity_id=user_id, details={"nome": row.get("nome"), "email": email})
    return {"ok": True, "message": "E-mail de redefinição solicitado."}


@app.delete("/api/config/users/{user_id}")
def config_user_delete(user_id: int, request: Request):
    _require_admin(request)
    row = get_app_user(user_id)
    if not row:
        raise HTTPException(404, "Usuário não encontrado.")
    auth_user_id = clean(row.get("auth_user_id"))
    try:
        if auth_user_id and auth_service.admin_configured():
            auth_service.admin_delete_user(auth_user_id, soft=True)
        deleted = delete_app_user(user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except auth_service.AuthServiceError as exc:
        raise HTTPException(exc.status_code, str(exc))
    if not deleted:
        raise HTTPException(404, "Usuário não encontrado.")
    _audit(request, "EXCLUIU_USUARIO", entity_type="USUARIO", entity_id=user_id, details={"nome": row.get("nome"), "email": row.get("email"), "perfil": row.get("perfil")})
    return {"ok": True}


@app.post("/api/config/form-visibility")
def config_form_visibility(request: Request, payload: dict = Body(...)):
    _require_admin(request)
    value = dict(payload.get("value") or {})
    set_app_setting("form_visibility", value)
    return {"ok": True, "value": value}



@app.get("/api/records/{record_id}")
def record(record_id: int, request: Request):
    profile_id, include_all = _record_scope(request)
    item = get_record(record_id, profile_id=profile_id, include_all=include_all)
    if not item:
        raise HTTPException(404, "Espelho não encontrado.")
    return {"ok": True, "item": item}


@app.post("/api/records/{record_id}/status")
def record_status(record_id: int, request: Request, payload: dict = Body(...)):
    _require_roles(request, "ADMIN", "OPERADOR")
    status = clean(payload.get("status")).upper()
    message = clean(payload.get("message"))
    item = update_record_status(
        record_id,
        status,
        message,
        clean(payload.get("remote_laudo_numero")),
    )
    if not item:
        raise HTTPException(404, "Espelho não encontrado.")

    push_result = {"attempted": 0, "sent": 0, "failed": 0, "configured": push_service.configured()}
    _audit(request, "ALTEROU_STATUS_APP", entity_type="ESPELHO", entity_id=record_id, numero_laudo=str(item.get("numero_laudo") or ""), details={"status": status, "message": message})
    if status in {STATUS_DEVOLVIDO, STATUS_CRIADO}:
        try:
            tokens = list_push_tokens_for_record(record_id)
            push_result = push_service.send_status_push(
                tokens, status=status, record=item, message=message
            )
        except Exception as exc:
            # A atualização do laudo nunca deve falhar apenas porque o push ficou indisponível.
            push_result = {"attempted": 0, "sent": 0, "failed": 0, "configured": push_service.configured(), "error": str(exc)}
    return {"ok": True, "item": item, "push": push_result}


@app.post("/api/records/save")
def save(request: Request, payload: dict = Body(...)):
    data = dict(payload.get("data") or {})
    record_id = payload.get("id")
    profile_id, include_all = _record_scope(request)
    status = STATUS_RASCUNHO
    if record_id:
        current = get_record(int(record_id), profile_id=profile_id, include_all=include_all)
        if not current:
            raise HTTPException(404, "Espelho não encontrado ou sem permissão para editar.")
        if current.get("status") == STATUS_DEVOLVIDO:
            status = STATUS_DEVOLVIDO
    return {"ok": True, "item": save_record(data, record_id=record_id, status=status, created_by_profile_id=profile_id), "backend": _runtime_backend_info()}


@app.delete("/api/records/{record_id}")
def remove(record_id: int, request: Request):
    profile_id, include_all = _record_scope(request)
    if not delete_record(record_id, profile_id=profile_id, include_all=include_all):
        raise HTTPException(404, "Espelho não encontrado ou sem permissão para excluir.")
    return {"ok": True}


@app.post("/api/export")
def export(request: Request, payload: dict = Body(...)):
    data = dict(payload.get("data") or {})
    required = ["numero_laudo", "instalacao", "numero_serie", "modelo"]
    missing = [name for name in required if not clean(data.get(name))]
    if missing:
        raise HTTPException(400, "Campos obrigatórios: Nº do Laudo, Instalação, Nº do Medidor/Série e Modelo.")
    profile_id, include_all = _record_scope(request)
    if payload.get("id") and not get_record(int(payload.get("id")), profile_id=profile_id, include_all=include_all):
        raise HTTPException(404, "Espelho não encontrado ou sem permissão para finalizar.")
    return {"ok": True, **export_bridge(data, record_id=payload.get("id"), created_by_profile_id=profile_id), "backend": _runtime_backend_info()}


# ---------------------------------------------------------------------------
# V1.0.0.37 — fluxo do PAINEL (ADMIN / OPERADOR / FUNCAO)
# ---------------------------------------------------------------------------
@app.get("/api/panel/app-record/{record_id}")
def panel_app_record(record_id: int, request: Request):
    _require_roles(request, "ADMIN", "OPERADOR")
    item = get_record(record_id, include_all=True)
    if not item:
        raise HTTPException(404, "Espelho não encontrado.")
    return {"ok": True, "item": item}


@app.get("/api/panel/app-inbox")
def panel_app_inbox(request: Request):
    profile = _require_roles(request, "ADMIN", "OPERADOR")
    records = list_records(limit=500, include_all=True)
    notifications = [n for n in list_notifications(limit=500) if str(n.get("status") or "").upper() != "EXCLUIDA"]
    return {"ok": True, "records": records, "notifications": notifications, "user": _profile_public(profile)}


@app.get("/api/panel/users")
def panel_users(request: Request, role: str = ""):
    profile = _require_roles(request, "ADMIN", "OPERADOR", "FUNCAO")
    role_u = clean(role).upper()
    if role_u and role_u not in {"ADMIN", "OPERADOR", "FUNCAO"}:
        raise HTTPException(400, "Perfil inválido.")
    # OPERADOR normalmente envia para FUNCAO; FUNCAO devolve para OPERADOR/ADMIN.
    roles = [role_u] if role_u else None
    rows = list_panel_users(active_only=True, roles=roles)
    return {"ok": True, "items": [_profile_public(r) for r in rows], "current_user": _profile_public(profile)}


@app.get("/api/panel/inbox")
def panel_inbox(request: Request, limit: int = 500):
    profile = _require_roles(request, "ADMIN", "OPERADOR", "FUNCAO")
    items = list_panel_assignments(profile_id=int(profile.get("id")), role=clean(profile.get("perfil")), limit=limit)
    return {"ok": True, "items": items, "current_user": _profile_public(profile)}


@app.post("/api/panel/send")
def panel_send(request: Request, payload: dict = Body(...)):
    profile = _require_roles(request, "ADMIN", "OPERADOR")
    target_id = int(payload.get("target_profile_id") or 0)
    target = next((u for u in list_panel_users(active_only=True) if int(u.get("id") or 0) == target_id), None)
    if not target:
        raise HTTPException(404, "Usuário de destino não encontrado.")
    if clean(profile.get("perfil")).upper() == "OPERADOR" and clean(target.get("perfil")).upper() != "FUNCAO":
        raise HTTPException(403, "OPERADOR pode enviar laudos apenas para usuários FUNCAO.")
    docs = payload.get("documents") if isinstance(payload.get("documents"), list) else []
    if not docs:
        raise HTTPException(400, "Nenhum laudo foi enviado.")
    saved=[]
    for doc in docs[:100]:
        if not isinstance(doc, dict):
            continue
        raw = clean(doc.get("pdf_base64"))
        try:
            pdf_bytes = base64.b64decode(raw, validate=True)
        except Exception:
            raise HTTPException(400, f"PDF inválido: {clean(doc.get('filename')) or 'arquivo'}")
        try:
            item = create_panel_assignment(
                pdf_bytes=pdf_bytes,
                filename=clean(doc.get("filename")) or "LAUDO.pdf",
                numero_laudo=clean(doc.get("numero_laudo")),
                numero_serie=clean(doc.get("numero_serie")),
                assigned_to_profile_id=target_id,
                assigned_by_profile_id=int(profile.get("id")),
                owner_profile_id=int(profile.get("id")),
                source_record_id=int(doc.get("source_record_id")) if str(doc.get("source_record_id") or "").isdigit() else None,
                source_local_id=int(doc.get("source_local_id")) if str(doc.get("source_local_id") or "").isdigit() else None,
                metadata_json=dict(doc.get("metadata") or {}),
                document_uid=clean(doc.get("document_uid")),
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        saved.append(item)
        _audit(request, "ENVIOU_PARA_FUNCAO", entity_type="LAUDO_PAINEL", entity_id=item.get("id"), numero_laudo=item.get("numero_laudo") or "", details={"destino_id": target_id, "destino_nome": target.get("nome"), "arquivo": item.get("filename")})
    return {"ok": True, "items": saved, "target": _profile_public(target)}


def _can_access_assignment(profile: dict, item: dict) -> bool:
    role = clean(profile.get("perfil")).upper()
    pid = int(profile.get("id") or 0)
    if role == "ADMIN":
        return True
    if role == "FUNCAO":
        return int(item.get("assigned_to_profile_id") or 0) == pid
    if role == "OPERADOR":
        return pid in {int(item.get("assigned_to_profile_id") or 0), int(item.get("assigned_by_profile_id") or 0), int(item.get("owner_profile_id") or 0)}
    return False


@app.get("/api/panel/assignments/{assignment_id}/download")
def panel_assignment_download(assignment_id: int, request: Request):
    profile = _require_roles(request, "ADMIN", "OPERADOR", "FUNCAO")
    item = get_panel_assignment(assignment_id)
    if not item or not _can_access_assignment(profile, item):
        raise HTTPException(404, "Laudo não encontrado para este usuário.")
    data = item.get("pdf_data") or b""
    if not data:
        raise HTTPException(404, "PDF não disponível.")
    filename = clean(item.get("filename")) or f"LAUDO_{assignment_id}.pdf"
    safe = filename.replace('"','').replace('\r','').replace('\n','')
    _audit(request, "BAIXOU_PDF", entity_type="LAUDO_PAINEL", entity_id=assignment_id, numero_laudo=item.get("numero_laudo") or "", details={"arquivo": filename})
    return Response(content=data, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{safe}"'})


@app.post("/api/panel/assignments/{assignment_id}/downloaded")
def panel_assignment_downloaded(assignment_id: int, request: Request):
    profile = _require_roles(request, "ADMIN", "FUNCAO")
    item = get_panel_assignment(assignment_id)
    if not item or not _can_access_assignment(profile, item):
        raise HTTPException(404, "Laudo não encontrado para este usuário.")
    updated = update_panel_assignment(assignment_id, status=STATUS_BAIXADO)
    _audit(request, "MARCOU_BAIXADO", entity_type="LAUDO_PAINEL", entity_id=assignment_id, numero_laudo=(updated or item).get("numero_laudo") or "")
    return {"ok": True, "item": updated}


@app.post("/api/panel/assignments/{assignment_id}/return")
def panel_assignment_return(assignment_id: int, request: Request, payload: dict = Body(...)):
    profile = _require_roles(request, "ADMIN", "FUNCAO")
    item = get_panel_assignment(assignment_id)
    if not item or not _can_access_assignment(profile, item):
        raise HTTPException(404, "Laudo não encontrado para este usuário.")
    reason = clean(payload.get("message"))
    if not reason:
        raise HTTPException(400, "Informe o motivo da correção.")
    target_id = int(payload.get("target_profile_id") or item.get("owner_profile_id") or item.get("assigned_by_profile_id") or 0)
    target = next((u for u in list_panel_users(active_only=True, roles=["ADMIN","OPERADOR"]) if int(u.get("id") or 0) == target_id), None)
    if not target:
        raise HTTPException(404, "OPERADOR/ADMIN de destino não encontrado.")
    updated = update_panel_assignment(assignment_id, status=STATUS_CORRECAO_PDF, assigned_to_profile_id=target_id, correction_message=reason)
    _audit(request, "DEVOLVEU_PARA_CORRECAO_PDF", entity_type="LAUDO_PAINEL", entity_id=assignment_id, numero_laudo=(updated or item).get("numero_laudo") or "", details={"motivo": reason, "destino_id": target_id, "destino_nome": target.get("nome")})
    return {"ok": True, "item": updated, "target": _profile_public(target)}


@app.post("/api/panel/assignments/{assignment_id}/resend")
def panel_assignment_resend(assignment_id: int, request: Request, payload: dict = Body(...)):
    profile = _require_roles(request, "ADMIN", "OPERADOR")
    item = get_panel_assignment(assignment_id)
    if not item or not _can_access_assignment(profile, item):
        raise HTTPException(404, "Laudo não encontrado para este usuário.")
    target_id = int(payload.get("target_profile_id") or 0)
    target = next((u for u in list_panel_users(active_only=True, roles=["FUNCAO"]) if int(u.get("id") or 0) == target_id), None)
    if not target:
        raise HTTPException(404, "Usuário FUNCAO de destino não encontrado.")
    pdf_bytes = None
    raw = clean(payload.get("pdf_base64"))
    if raw:
        try: pdf_bytes = base64.b64decode(raw, validate=True)
        except Exception: raise HTTPException(400, "PDF inválido.")
    try:
        updated = update_panel_assignment(assignment_id, status=STATUS_AGUARDANDO_BAIXA, assigned_to_profile_id=target_id, assigned_by_profile_id=int(profile.get("id")), correction_message="", pdf_bytes=pdf_bytes, filename=clean(payload.get("filename")) or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    _audit(request, "REENVIOU_PARA_FUNCAO", entity_type="LAUDO_PAINEL", entity_id=assignment_id, numero_laudo=(updated or item).get("numero_laudo") or "", details={"destino_id": target_id, "destino_nome": target.get("nome")})
    return {"ok": True, "item": updated, "target": _profile_public(target)}


@app.post("/api/audit/log")
def audit_log(request: Request, payload: dict = Body(...)):
    profile = _require_roles(request, "ADMIN", "OPERADOR", "FUNCAO")
    event = add_audit_event(profile, clean(payload.get("action")) or "ACAO", entity_type=clean(payload.get("entity_type")), entity_id=payload.get("entity_id") or "", numero_laudo=clean(payload.get("numero_laudo")), details=dict(payload.get("details") or {}))
    return {"ok": True, "item": event}


@app.get("/api/audit")
def audit_list(request: Request, limit: int = 500, profile_id: int = 0, action: str = "", numero_laudo: str = ""):
    _require_admin(request)
    items = list_audit_events(limit=limit, profile_id=profile_id or None, action=action, numero_laudo=numero_laudo)
    return {"ok": True, "items": items, "users": [_profile_public(u) for u in list_panel_users(active_only=False)]}


@app.post("/api/open-outbox")
def open_outbox():
    folder = app_data_dir() / "ENVIAR_ID_CAMPS"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as exc:
        raise HTTPException(500, f"Não foi possível abrir a pasta: {exc}")
    return {"ok": True, "path": str(folder)}
