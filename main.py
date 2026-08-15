from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import sqlite3
import subprocess
import sys

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from storage_backend import (
    APP_NAME, APP_VERSION, STATUS_AGUARDANDO, STATUS_CRIADO, STATUS_DEVOLVIDO,
    STATUS_PRONTO, STATUS_RASCUNHO, STATUS_REVISAO, app_data_dir, delete_record,
    export_bridge, get_record, list_records, save_record, update_record_status,
    list_app_users, save_app_user, delete_app_user, get_app_setting, set_app_setting,
    backend_info, bootstrap_admin_info,
)

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


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(STATIC_DIR / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def sw():
    return FileResponse(STATIC_DIR / "sw.js", media_type="application/javascript")


@app.get("/api/health")
def health():
    info = backend_info()
    return {"ok": True, "app": APP_NAME, "version": APP_VERSION, "backend": info}


@app.get("/api/backend-info")
def api_backend_info():
    return {"ok": True, **backend_info(), "catalog": "POSTGRESQL" if USE_POSTGRES_CATALOG else "SQLITE"}


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
        "backend": backend_info(),
    }


@app.get("/api/models")
def models(search: str = ""):
    return {"ok": True, "items": list_models(search)}


@app.get("/api/observations")
def observations():
    return {"ok": True, "items": list_observations()}


@app.get("/api/records")
def records():
    return {"ok": True, "items": list_records()}

@app.get("/api/config")
def config_get():
    return {
        "ok": True,
        "users": list_app_users(),
        "form_visibility": get_app_setting("form_visibility", {}),
        "backend": backend_info(),
        "primary_admin": bootstrap_admin_info(),
    }


@app.post("/api/config/users")
def config_user_save(payload: dict = Body(...)):
    try:
        item = save_app_user(dict(payload.get("data") or {}), user_id=payload.get("id"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "item": item}


@app.delete("/api/config/users/{user_id}")
def config_user_delete(user_id: int):
    try:
        deleted = delete_app_user(user_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not deleted:
        raise HTTPException(404, "Usuário não encontrado.")
    return {"ok": True}


@app.post("/api/config/form-visibility")
def config_form_visibility(payload: dict = Body(...)):
    value = dict(payload.get("value") or {})
    set_app_setting("form_visibility", value)
    return {"ok": True, "value": value}



@app.get("/api/records/{record_id}")
def record(record_id: int):
    item = get_record(record_id)
    if not item:
        raise HTTPException(404, "Espelho não encontrado.")
    return {"ok": True, "item": item}


@app.post("/api/records/{record_id}/status")
def record_status(record_id: int, payload: dict = Body(...)):
    # Endpoint preparado para o futuro módulo NOTIFICAÇÕES do ID CAMPS Laudos.
    item = update_record_status(
        record_id,
        clean(payload.get("status")),
        clean(payload.get("message")),
        clean(payload.get("remote_laudo_numero")),
    )
    if not item:
        raise HTTPException(404, "Espelho não encontrado.")
    return {"ok": True, "item": item}


@app.post("/api/records/save")
def save(payload: dict = Body(...)):
    data = dict(payload.get("data") or {})
    record_id = payload.get("id")
    status = STATUS_RASCUNHO
    if record_id:
        current = get_record(int(record_id))
        if current and current.get("status") == STATUS_DEVOLVIDO:
            # Mantém o aviso de correção até o técnico finalizar e reenviar.
            status = STATUS_DEVOLVIDO
    return {"ok": True, "item": save_record(data, record_id=record_id, status=status)}


@app.delete("/api/records/{record_id}")
def remove(record_id: int):
    if not delete_record(record_id):
        raise HTTPException(404, "Espelho não encontrado.")
    return {"ok": True}


@app.post("/api/export")
def export(payload: dict = Body(...)):
    data = dict(payload.get("data") or {})
    required = ["numero_laudo", "instalacao", "numero_serie", "modelo"]
    missing = [name for name in required if not clean(data.get(name))]
    if missing:
        raise HTTPException(400, "Campos obrigatórios: Nº do Laudo, Instalação, Nº do Medidor/Série e Modelo.")
    return {"ok": True, **export_bridge(data, record_id=payload.get("id"))}


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
