from __future__ import annotations

import os
import re
import secrets
from urllib.parse import urlparse

import httpx


class AuthServiceError(RuntimeError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _clean(value) -> str:
    return str(value or "").strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _clean(os.environ.get(name, ""))
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "sim", "on"}


def _infer_project_ref() -> str:
    explicit = _clean(os.environ.get("SUPABASE_PROJECT_REF"))
    if explicit:
        return explicit
    raw = _clean(os.environ.get("DATABASE_URL"))
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        username = parsed.username or ""
        if username.startswith("postgres."):
            return username.split(".", 1)[1]
    except Exception:
        pass
    match = re.search(r"postgres\.([a-z0-9]+)", raw, flags=re.I)
    return match.group(1) if match else ""


def supabase_url() -> str:
    url = _clean(os.environ.get("SUPABASE_URL"))
    if url:
        return url.rstrip("/")
    ref = _infer_project_ref()
    return f"https://{ref}.supabase.co" if ref else ""


def public_key() -> str:
    return _clean(
        os.environ.get("SUPABASE_PUBLISHABLE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
    )


def secret_key() -> str:
    return _clean(
        os.environ.get("SUPABASE_SECRET_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )


def auth_requested() -> bool:
    # V1.0.0.26: em PostgreSQL/Render o login é obrigatório (fail-closed).
    # Isso evita que uma variável antiga ID_LAUDO_AUTH_ENABLED=false, herdada das versões
    # anteriores ao login, libere o aplicativo sem autenticação.
    if bool(_clean(os.environ.get("DATABASE_URL"))):
        return True
    return _flag("ID_LAUDO_AUTH_ENABLED", default=False)


def auth_configured() -> bool:
    return bool(supabase_url() and public_key())


def admin_configured() -> bool:
    return bool(supabase_url() and secret_key())


def auth_enabled() -> bool:
    return auth_requested() and auth_configured()


def _valid_public_app_url(value: str) -> str:
    raw = _clean(value)
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    host = (parsed.hostname or "").lower()
    # Nunca aceite links do painel administrativo do Render como URL pública do app.
    if host in {"dashboard.render.com", "render.com", "www.render.com"}:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}".rstrip("/")


def public_app_url() -> str:
    # V1.0.0.35: em produção, o próprio Render informa a URL pública correta.
    # Preferimos RENDER_EXTERNAL_URL para evitar que uma URL do dashboard seja
    # cadastrada por engano em ID_LAUDO_PUBLIC_URL.
    candidates = [
        os.environ.get("RENDER_EXTERNAL_URL"),
        os.environ.get("ID_LAUDO_PUBLIC_URL"),
        "https://id-laudo-api.onrender.com",
    ]
    for candidate in candidates:
        valid = _valid_public_app_url(candidate)
        if valid:
            return valid
    return "https://id-laudo-api.onrender.com"


def reset_redirect_url() -> str:
    # Fluxo V30: Supabase -> HTTPS público do Render /password-reset -> APK.
    # Usar uma rota dedicada impede que o usuário caia na tela inicial.
    explicit = _valid_public_app_url(os.environ.get("ID_LAUDO_RESET_REDIRECT_URL"))
    if explicit:
        if urlparse(explicit).path.rstrip("/") in {"", "/"}:
            return explicit.rstrip("/") + "/password-reset"
        return explicit.rstrip("/")
    return public_app_url().rstrip("/") + "/password-reset"


def _headers(key: str, token: str = "") -> dict[str, str]:
    h = {"apikey": key, "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _json_or_text(resp: httpx.Response):
    try:
        return resp.json()
    except Exception:
        return {"message": resp.text or "Falha no serviço de autenticação."}


def _message(data, fallback: str) -> str:
    if isinstance(data, dict):
        return _clean(data.get("msg") or data.get("message") or data.get("error_description") or data.get("error")) or fallback
    return fallback


def _request(method: str, path: str, *, key: str, token: str = "", json_data=None, params=None, timeout: float = 15.0):
    if not supabase_url() or not key:
        raise AuthServiceError("Supabase Auth ainda não está configurado no servidor.", 503)
    url = supabase_url() + path
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            resp = client.request(method, url, headers=_headers(key, token), json=json_data, params=params)
    except httpx.HTTPError as exc:
        raise AuthServiceError(f"Falha de comunicação com Supabase Auth: {exc}", 503) from exc
    data = _json_or_text(resp)
    if resp.status_code >= 400:
        raise AuthServiceError(_message(data, "Falha na autenticação."), resp.status_code)
    return data


def sign_in(email: str, password: str) -> dict:
    email = _clean(email).lower()
    if not email or not password:
        raise AuthServiceError("Informe e-mail e senha.", 400)
    return _request(
        "POST", "/auth/v1/token",
        key=public_key(),
        params={"grant_type": "password"},
        json_data={"email": email, "password": password},
    )


def refresh_session(refresh_token: str) -> dict:
    if not _clean(refresh_token):
        raise AuthServiceError("Sessão expirada.", 401)
    return _request(
        "POST", "/auth/v1/token",
        key=public_key(),
        params={"grant_type": "refresh_token"},
        json_data={"refresh_token": refresh_token},
    )


def get_user(access_token: str) -> dict:
    if not _clean(access_token):
        raise AuthServiceError("Sessão não informada.", 401)
    return _request("GET", "/auth/v1/user", key=public_key(), token=access_token)


def sign_out(access_token: str) -> None:
    """Revoga a sessão no Supabase Auth; o cliente também apaga seus tokens locais."""
    if not _clean(access_token):
        return
    _request("POST", "/auth/v1/logout", key=public_key(), token=access_token)


def update_password(access_token: str, new_password: str) -> dict:
    if len(str(new_password or "")) < 8:
        raise AuthServiceError("A nova senha precisa ter pelo menos 8 caracteres.", 400)
    return _request(
        "PUT", "/auth/v1/user", key=public_key(), token=access_token,
        json_data={"password": new_password},
    )


def send_password_reset(email: str) -> None:
    email = _clean(email).lower()
    if not email:
        raise AuthServiceError("Informe o e-mail.", 400)
    _request(
        "POST", "/auth/v1/recover", key=public_key(),
        params={"redirect_to": reset_redirect_url()},
        json_data={"email": email},
    )


def verify_recovery_token_hash(token_hash: str) -> dict:
    """Troca o TokenHash do e-mail de recuperação por uma sessão Supabase.

    Compatibilidade legada da V31. A V32 nao depende de TokenHash nem de
    template personalizado; o fluxo principal usa o retorno padrao do Supabase
    e detectSessionInUrl=true no cliente.
    """
    token_hash = _clean(token_hash)
    if not token_hash:
        raise AuthServiceError("Link de recuperação inválido. Solicite um novo e-mail.", 400)
    data = _request(
        "POST", "/auth/v1/verify", key=public_key(),
        json_data={"token_hash": token_hash, "type": "recovery"},
    )
    if not isinstance(data, dict) or not _clean(data.get("access_token")):
        raise AuthServiceError("Não foi possível validar o link de recuperação.", 400)
    return data


def _admin_request(method: str, path: str, *, json_data=None, params=None):
    return _request(method, path, key=secret_key(), token=secret_key(), json_data=json_data, params=params)


def admin_list_users() -> list[dict]:
    data = _admin_request("GET", "/auth/v1/admin/users", params={"page": 1, "per_page": 1000})
    if isinstance(data, dict):
        return list(data.get("users") or [])
    return []


def admin_find_user_by_email(email: str) -> dict | None:
    target = _clean(email).lower()
    if not target:
        return None
    for user in admin_list_users():
        if _clean(user.get("email")).lower() == target:
            return user
    return None


def admin_create_user(email: str, password: str, name: str, role: str, *, must_change: bool = True) -> dict:
    email = _clean(email).lower()
    role = _clean(role).upper() or "OPERADOR"
    if not email:
        raise AuthServiceError("E-mail é obrigatório para criar o acesso.", 400)
    if len(str(password or "")) < 8:
        raise AuthServiceError("A senha temporária precisa ter pelo menos 8 caracteres.", 400)
    payload = {
        "email": email,
        "password": password,
        "email_confirm": True,
        "user_metadata": {"name": _clean(name)},
        "app_metadata": {"id_laudo_role": role, "must_change_password": bool(must_change)},
    }
    return _admin_request("POST", "/auth/v1/admin/users", json_data=payload)


def admin_update_user(auth_user_id: str, *, email: str | None = None, name: str | None = None,
                      role: str | None = None, password: str | None = None,
                      must_change: bool | None = None) -> dict:
    payload: dict = {}
    if email is not None:
        payload["email"] = _clean(email).lower()
        payload["email_confirm"] = True
    if password is not None:
        payload["password"] = password
    if name is not None:
        payload["user_metadata"] = {"name": _clean(name)}
    if role is not None or must_change is not None:
        app_meta = {}
        if role is not None:
            app_meta["id_laudo_role"] = _clean(role).upper()
        if must_change is not None:
            app_meta["must_change_password"] = bool(must_change)
        payload["app_metadata"] = app_meta
    if not payload:
        return {}
    return _admin_request("PUT", f"/auth/v1/admin/users/{auth_user_id}", json_data=payload)


def admin_set_suspended(auth_user_id: str, suspended: bool) -> dict:
    return _admin_request(
        "PUT", f"/auth/v1/admin/users/{auth_user_id}",
        json_data={"ban_duration": "876000h" if suspended else "none"},
    )


def admin_delete_user(auth_user_id: str, *, soft: bool = True) -> None:
    _admin_request(
        "DELETE", f"/auth/v1/admin/users/{auth_user_id}",
        json_data={"should_soft_delete": bool(soft)},
    )


def admin_force_password_by_email(email: str, password: str) -> dict:
    email = _clean(email).lower()
    if not email:
        raise AuthServiceError("Informe o e-mail do usuário.", 400)
    if len(str(password or "")) < 8:
        raise AuthServiceError("A senha temporária precisa ter pelo menos 8 caracteres.", 400)
    user = admin_find_user_by_email(email)
    if not user:
        raise AuthServiceError("Usuário não encontrado no Supabase Auth.", 404)
    updated = admin_update_user(str(user.get("id")), password=str(password))
    return updated or user


def random_temporary_password() -> str:
    # Senha desconhecida pelo administrador; o usuário define a própria pelo e-mail de redefinição.
    return "IdL!" + secrets.token_urlsafe(18)


_BOOTSTRAP_ATTEMPTED = False
_BOOTSTRAP_RESULT: dict = {"ready": False, "reason": "not_attempted"}


def bootstrap_primary_admin(email: str, password: str, name: str = "Administrador principal") -> dict:
    global _BOOTSTRAP_ATTEMPTED, _BOOTSTRAP_RESULT
    if _BOOTSTRAP_ATTEMPTED:
        return dict(_BOOTSTRAP_RESULT)
    _BOOTSTRAP_ATTEMPTED = True
    if not auth_requested():
        _BOOTSTRAP_RESULT = {"ready": False, "reason": "auth_disabled"}
        return dict(_BOOTSTRAP_RESULT)
    if not admin_configured():
        _BOOTSTRAP_RESULT = {"ready": False, "reason": "missing_secret_key"}
        return dict(_BOOTSTRAP_RESULT)
    try:
        existing = admin_find_user_by_email(email)
        created = False
        if not existing:
            if len(password or "") < 8:
                raise AuthServiceError("Defina ID_LAUDO_BOOTSTRAP_ADMIN_PASSWORD com pelo menos 8 caracteres.", 503)
            existing = admin_create_user(email, password, name, "ADMIN", must_change=True)
            created = True
        else:
            # Garante metadados administrativos sem alterar a senha de um usuário já existente.
            existing = admin_update_user(str(existing.get("id")), name=name, role="ADMIN") or existing
        _BOOTSTRAP_RESULT = {
            "ready": True,
            "auth_user_id": str(existing.get("id") or ""),
            "email": _clean(existing.get("email") or email).lower(),
            "created": created,
        }
    except AuthServiceError as exc:
        _BOOTSTRAP_RESULT = {"ready": False, "reason": str(exc), "status_code": exc.status_code}
    return dict(_BOOTSTRAP_RESULT)


def config_status() -> dict:
    return {
        "requested": auth_requested(),
        "configured": auth_configured(),
        "enabled": auth_enabled(),
        "admin_api": admin_configured(),
        "supabase_url": supabase_url() if auth_configured() else "",
        "public_app_url": public_app_url(),
        "reset_redirect": reset_redirect_url() if auth_configured() else "",
        "render_external_url": _clean(os.environ.get("RENDER_EXTERNAL_URL")),
    }
