from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import re
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, JSON, MetaData, String, Table, Text, Uuid,
    create_engine, delete, desc, insert, select, update
)
from sqlalchemy.exc import IntegrityError

APP_NAME = "ID LAUDO"
APP_VERSION = "1.0.0.22"

STATUS_RASCUNHO = "RASCUNHO"
STATUS_PRONTO = "PRONTO_PARA_ID_CAMPS"
STATUS_AGUARDANDO = "AGUARDANDO_REVISAO"
STATUS_REVISAO = "EM_REVISAO"
STATUS_DEVOLVIDO = "DEVOLVIDO"
STATUS_CRIADO = "LAUDO_CRIADO"
VALID_STATUSES = {
    STATUS_RASCUNHO, STATUS_PRONTO, STATUS_AGUARDANDO,
    STATUS_REVISAO, STATUS_DEVOLVIDO, STATUS_CRIADO,
}


def _database_url() -> str:
    raw = str(os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        raise RuntimeError("DATABASE_URL não configurada.")
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    if raw.startswith("postgresql://"):
        raw = "postgresql+psycopg://" + raw[len("postgresql://"):]
    return raw


engine = create_engine(
    _database_url(),
    pool_pre_ping=True,
    pool_recycle=300,
    future=True,
)
metadata = MetaData()

espelhos = Table(
    "espelhos", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("status", String(40), nullable=False, default=STATUS_RASCUNHO),
    Column("numero_laudo", String(80)),
    Column("ano", String(10)),
    Column("tipo", String(10)),
    Column("instalacao", String(100)),
    Column("numero_serie", String(120)),
    Column("modelo", String(180)),
    Column("payload_json", JSON, nullable=False),
    Column("export_path", Text, nullable=False, default=""),
    Column("bridge_id", String(100), nullable=False, default=""),
    Column("status_message", Text, nullable=False, default=""),
    Column("status_updated_at", DateTime(timezone=True)),
    Column("remote_laudo_numero", String(100), nullable=False, default=""),
    Column("created_by_profile_id", Integer, nullable=True),
)

profiles = Table(
    "profiles", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("auth_user_id", Uuid(as_uuid=False), unique=True, nullable=True),
    Column("nome", String(180), nullable=False),
    Column("usuario", String(120), nullable=False, unique=True),
    Column("perfil", String(30), nullable=False, default="OPERADOR"),
    Column("ativo", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

app_settings = Table(
    "app_settings", metadata,
    Column("key", String(160), primary_key=True),
    Column("value_json", JSON, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

notifications = Table(
    "notifications", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("espelho_id", Integer, nullable=True),
    Column("tipo", String(60), nullable=False, default="LAUDO"),
    Column("status", String(40), nullable=False, default="NOVO"),
    Column("titulo", String(220), nullable=False, default=""),
    Column("mensagem", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("read_at", DateTime(timezone=True), nullable=True),
)

catalog_models = Table(
    "catalog_models", metadata,
    Column("id", Integer, primary_key=True),
    Column("fabricante", String(180)),
    Column("modelo", String(180), nullable=False),
    Column("portaria", Text),
    Column("classe", String(80)),
    Column("elementos", String(80)),
    Column("corrente_nominal", String(80)),
    Column("corrente_maxima", String(80)),
    Column("tensao_nominal", String(80)),
    Column("frequencia", String(80)),
    Column("constante", String(80)),
    Column("portaria_rtm", Text),
)

catalog_observations = Table(
    "catalog_observations", metadata,
    Column("id", Integer, primary_key=True),
    Column("observacao", Text),
    Column("conclusao", Text),
)

catalog_people = Table(
    "catalog_people", metadata,
    Column("id", Integer, primary_key=True),
    Column("categoria", String(120)),
    Column("nome", String(180)),
)


def now_dt() -> datetime:
    return datetime.now().astimezone()


def now_iso() -> str:
    return now_dt().isoformat(timespec="seconds")


def app_data_dir() -> Path:
    p = Path(os.environ.get("TMPDIR", "/tmp")) / "ID_LAUDO"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_db() -> Path:
    metadata.create_all(engine)
    return app_data_dir()


def normalize_status(status: object) -> str:
    value = str(status or STATUS_RASCUNHO).strip().upper()
    if value == "PRONTO":
        return STATUS_PRONTO
    return value if value in VALID_STATUSES else STATUS_RASCUNHO


def new_bridge_id() -> str:
    return f"IDL-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6].upper()}"


def _summary(payload: dict) -> tuple[str, ...]:
    return (
        str(payload.get("numero_laudo") or "").strip(),
        str(payload.get("ano") or "").strip(),
        str(payload.get("tipo") or "NR").strip().upper(),
        str(payload.get("instalacao") or "").strip(),
        str(payload.get("numero_serie") or "").strip(),
        str(payload.get("modelo") or "").strip(),
    )


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    d = dict(row._mapping if hasattr(row, "_mapping") else row)
    d["status"] = normalize_status(d.get("status"))
    payload = d.pop("payload_json", {})
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    d["payload"] = payload or {}
    for k in ("created_at", "updated_at", "status_updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat(timespec="seconds")
    return d


def save_record(data: dict, record_id: int | None = None, status: str = STATUS_RASCUNHO,
                export_path: str = "", bridge_id: str = "", status_message: str | None = None,
                remote_laudo_numero: str | None = None) -> dict:
    ensure_db()
    status = normalize_status(status)
    n, ano, tipo, inst, serie, modelo = _summary(data)
    now = now_dt()
    with engine.begin() as con:
        current = None
        if record_id:
            current = con.execute(select(espelhos).where(espelhos.c.id == int(record_id))).first()
        if current:
            cur = dict(current._mapping)
            values = {
                "updated_at": now, "status": status, "numero_laudo": n, "ano": ano,
                "tipo": tipo, "instalacao": inst, "numero_serie": serie, "modelo": modelo,
                "payload_json": data,
                "bridge_id": bridge_id or str(cur.get("bridge_id") or ""),
                "status_message": str(cur.get("status_message") or "") if status_message is None else str(status_message or ""),
                "status_updated_at": now,
                "remote_laudo_numero": str(cur.get("remote_laudo_numero") or "") if remote_laudo_numero is None else str(remote_laudo_numero or ""),
            }
            if export_path:
                values["export_path"] = export_path
            con.execute(update(espelhos).where(espelhos.c.id == int(record_id)).values(**values))
            rid = int(record_id)
        else:
            result = con.execute(insert(espelhos).values(
                created_at=now, updated_at=now, status=status, numero_laudo=n, ano=ano, tipo=tipo,
                instalacao=inst, numero_serie=serie, modelo=modelo, payload_json=data,
                export_path=export_path or "", bridge_id=bridge_id or "",
                status_message=str(status_message or ""), status_updated_at=now,
                remote_laudo_numero=str(remote_laudo_numero or ""),
            ).returning(espelhos.c.id))
            rid = int(result.scalar_one())
        row = con.execute(select(espelhos).where(espelhos.c.id == rid)).first()
    return _row_to_dict(row)


def list_records(limit: int = 100) -> list[dict]:
    ensure_db()
    with engine.connect() as con:
        rows = con.execute(select(espelhos).order_by(desc(espelhos.c.updated_at), desc(espelhos.c.id)).limit(max(1, min(500, int(limit))))).all()
    return [_row_to_dict(r) for r in rows]


def get_record(record_id: int) -> dict | None:
    ensure_db()
    with engine.connect() as con:
        row = con.execute(select(espelhos).where(espelhos.c.id == int(record_id))).first()
    return _row_to_dict(row) if row else None


def delete_record(record_id: int) -> bool:
    ensure_db()
    with engine.begin() as con:
        result = con.execute(delete(espelhos).where(espelhos.c.id == int(record_id)))
    return bool(result.rowcount)


def update_record_status(record_id: int, status: str, message: str = "", remote_laudo_numero: str = "") -> dict | None:
    current = get_record(record_id)
    if not current:
        return None
    payload = dict(current.get("payload") or {})
    normalized = normalize_status(status)
    now = now_dt()
    bridge = payload.setdefault("_bridge", {})
    bridge["status"] = normalized
    bridge["status_updated_at"] = now.isoformat(timespec="seconds")
    if message:
        bridge["status_message"] = message
    if remote_laudo_numero:
        bridge["remote_laudo_numero"] = remote_laudo_numero
    with engine.begin() as con:
        con.execute(update(espelhos).where(espelhos.c.id == int(record_id)).values(
            status=normalized, status_message=str(message or ""), remote_laudo_numero=str(remote_laudo_numero or ""),
            status_updated_at=now, updated_at=now, payload_json=payload,
        ))
        row = con.execute(select(espelhos).where(espelhos.c.id == int(record_id))).first()
    return _row_to_dict(row)


def list_app_users() -> list[dict]:
    ensure_db()
    with engine.connect() as con:
        rows = con.execute(select(profiles).order_by(profiles.c.nome, profiles.c.id)).all()
    out = []
    for r in rows:
        d = dict(r._mapping)
        d["ativo"] = 1 if d.get("ativo") else 0
        for k in ("created_at", "updated_at"):
            if isinstance(d.get(k), datetime): d[k] = d[k].isoformat(timespec="seconds")
        out.append(d)
    return out


def save_app_user(data: dict, user_id: int | None = None) -> dict:
    ensure_db()
    nome = str(data.get("nome") or "").strip()
    usuario = str(data.get("usuario") or "").strip()
    perfil = str(data.get("perfil") or "OPERADOR").strip().upper()
    ativo = bool(data.get("ativo", True))
    if not nome or not usuario:
        raise ValueError("Nome e usuário são obrigatórios.")
    if perfil not in {"ADMIN", "OPERADOR"}:
        perfil = "OPERADOR"
    now = now_dt()
    try:
        with engine.begin() as con:
            current = con.execute(select(profiles).where(profiles.c.id == int(user_id))).first() if user_id else None
            if current:
                con.execute(update(profiles).where(profiles.c.id == int(user_id)).values(
                    nome=nome, usuario=usuario, perfil=perfil, ativo=ativo, updated_at=now
                ))
                rid = int(user_id)
            else:
                rid = int(con.execute(insert(profiles).values(
                    nome=nome, usuario=usuario, perfil=perfil, ativo=ativo, created_at=now, updated_at=now
                ).returning(profiles.c.id)).scalar_one())
            row = con.execute(select(profiles).where(profiles.c.id == rid)).first()
        d = dict(row._mapping)
        d["ativo"] = 1 if d.get("ativo") else 0
        for k in ("created_at", "updated_at"):
            if isinstance(d.get(k), datetime): d[k] = d[k].isoformat(timespec="seconds")
        return d
    except IntegrityError as exc:
        raise ValueError("Já existe um usuário com esse login.") from exc


def delete_app_user(user_id: int) -> bool:
    ensure_db()
    with engine.begin() as con:
        result = con.execute(delete(profiles).where(profiles.c.id == int(user_id)))
    return bool(result.rowcount)


def get_app_setting(key: str, default=None):
    ensure_db()
    with engine.connect() as con:
        row = con.execute(select(app_settings.c.value_json).where(app_settings.c.key == str(key))).first()
    return row[0] if row else default


def set_app_setting(key: str, value) -> None:
    ensure_db()
    now = now_dt()
    with engine.begin() as con:
        exists = con.execute(select(app_settings.c.key).where(app_settings.c.key == str(key))).first()
        if exists:
            con.execute(update(app_settings).where(app_settings.c.key == str(key)).values(value_json=value, updated_at=now))
        else:
            con.execute(insert(app_settings).values(key=str(key), value_json=value, updated_at=now))


def safe_component(value: object, fallback: str = "SEM-DADO") -> str:
    text = str(value or "").strip()
    text = re.sub(r'[<>:"/\\|?*]+', '-', text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text or fallback


def export_bridge(data: dict, record_id: int | None = None) -> dict:
    payload = dict(data or {})
    existing = get_record(int(record_id)) if record_id else None
    bridge_id = str((existing or {}).get("bridge_id") or "") or new_bridge_id()
    bridge = dict(payload.get("_bridge") or {})
    bridge.update({
        "id": bridge_id, "app": APP_NAME, "version": APP_VERSION,
        "schema": "id-laudo-bridge/3", "target": "ID CAMPS LAUDOS",
        "created_at": bridge.get("created_at") or now_iso(), "updated_at": now_iso(),
        "status": STATUS_AGUARDANDO, "status_label": "Aguardando revisão",
        "transport": "POSTGRESQL",
    })
    payload["_bridge"] = bridge
    saved = save_record(payload, record_id=record_id, status=STATUS_AGUARDANDO, bridge_id=bridge_id)
    tipo = safe_component(payload.get("tipo") or "NR")
    nr = safe_component(payload.get("numero_laudo") or "____")
    ano = safe_component(payload.get("ano") or datetime.now().year)
    virtual_name = f"IDLAUDO_{tipo}-{nr}-{ano}_{bridge_id}.json"
    # Cria a notificação central que o Painel poderá consumir na próxima etapa.
    with engine.begin() as con:
        existing_notif = con.execute(select(notifications.c.id).where(
            notifications.c.espelho_id == int(saved["id"]), notifications.c.status == "NOVO"
        )).first()
        if not existing_notif:
            con.execute(insert(notifications).values(
                espelho_id=int(saved["id"]), tipo="LAUDO", status="NOVO",
                titulo=f"Novo laudo {tipo}-{nr}", mensagem="Enviado pelo ID LAUDO para revisão.", created_at=now_dt()
            ))
    return {"record": saved, "filename": virtual_name, "path": "POSTGRESQL/SUPABASE", "bridge_id": bridge_id}


def backend_info() -> dict:
    ensure_db()
    url = str(os.environ.get("DATABASE_URL") or "")
    host = "PostgreSQL"
    if "supabase" in url.lower(): host = "Supabase PostgreSQL"
    return {"mode": "ONLINE", "database": host, "persistent": True, "auth": "PREPARADO", "notifications": True}
