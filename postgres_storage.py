from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import re
import sqlite3
import uuid

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, JSON, LargeBinary, MetaData, String, Table, Text, Uuid,
    create_engine, delete, desc, insert, select, update, text, func
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert

APP_NAME = "ID LAUDO"
APP_VERSION = "1.0.0.38"

STATUS_RASCUNHO = "RASCUNHO"
STATUS_PRONTO = "PRONTO_PARA_ID_CAMPS"
STATUS_AGUARDANDO = "AGUARDANDO_REVISAO"
STATUS_REVISAO = "EM_REVISAO"
STATUS_DEVOLVIDO = "DEVOLVIDO"
STATUS_CRIADO = "LAUDO_CRIADO"
STATUS_AGUARDANDO_BAIXA = "AGUARDANDO_BAIXA"
STATUS_BAIXADO = "BAIXADO"
STATUS_CORRECAO_PDF = "CORRECAO_PDF"
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
    Column("email", String(320), nullable=True),
    Column("is_system_admin", Boolean, nullable=False, default=False),
    Column("nome", String(180), nullable=False),
    Column("usuario", String(120), nullable=False, unique=True),
    Column("perfil", String(30), nullable=False, default="OPERADOR"),
    Column("ativo", Boolean, nullable=False, default=True),
    Column("must_change_password", Boolean, nullable=False, default=True),
    Column("suspended_at", DateTime(timezone=True), nullable=True),
    Column("last_login_at", DateTime(timezone=True), nullable=True),
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

push_devices = Table(
    "push_devices", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("profile_id", Integer, nullable=False),
    Column("token", Text, nullable=False, unique=True),
    Column("platform", String(40), nullable=False, default="ANDROID"),
    Column("device_name", String(180), nullable=False, default="Android"),
    Column("active", Boolean, nullable=False, default=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

# V1.0.0.37 — fluxo do Painel de Laudos entre OPERADOR/ADMIN e FUNCAO.
panel_assignments = Table(
    "panel_assignments", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("document_uid", String(64), nullable=False, unique=True),
    Column("source_record_id", Integer, nullable=True),
    Column("source_local_id", Integer, nullable=True),
    Column("numero_laudo", String(100), nullable=False, default=""),
    Column("numero_serie", String(120), nullable=False, default=""),
    Column("filename", String(260), nullable=False, default=""),
    Column("pdf_data", LargeBinary, nullable=False),
    Column("pdf_size", Integer, nullable=False, default=0),
    Column("pdf_sha256", String(64), nullable=False, default=""),
    Column("status", String(40), nullable=False, default=STATUS_AGUARDANDO_BAIXA),
    Column("assigned_to_profile_id", Integer, nullable=False),
    Column("assigned_by_profile_id", Integer, nullable=False),
    Column("owner_profile_id", Integer, nullable=True),
    Column("correction_message", Text, nullable=False, default=""),
    Column("metadata_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("sent_at", DateTime(timezone=True), nullable=True),
    Column("downloaded_at", DateTime(timezone=True), nullable=True),
)

audit_events = Table(
    "audit_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("profile_id", Integer, nullable=True),
    Column("user_name", String(180), nullable=False, default=""),
    Column("user_email", String(320), nullable=False, default=""),
    Column("user_role", String(40), nullable=False, default=""),
    Column("action", String(100), nullable=False),
    Column("entity_type", String(80), nullable=False, default=""),
    Column("entity_id", String(100), nullable=False, default=""),
    Column("numero_laudo", String(100), nullable=False, default=""),
    Column("details_json", JSON, nullable=False, default=dict),
    Column("created_at", DateTime(timezone=True), nullable=False),
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


BOOTSTRAP_ADMIN_EMAIL = str(os.environ.get("ID_LAUDO_BOOTSTRAP_ADMIN_EMAIL") or "dayvisant4@gmail.com").strip().lower()
BOOTSTRAP_ADMIN_USERNAME = str(os.environ.get("ID_LAUDO_BOOTSTRAP_ADMIN_USERNAME") or "ADMIN").strip() or "ADMIN"
_BOOTSTRAP_DONE = False


def _ensure_online_migrations() -> None:
    """Aplica migrações idempotentes necessárias em bancos já existentes."""
    with engine.begin() as con:
        con.execute(text("ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS email varchar(320)"))
        con.execute(text("ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS is_system_admin boolean NOT NULL DEFAULT false"))
        con.execute(text("ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS must_change_password boolean NOT NULL DEFAULT true"))
        con.execute(text("ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS suspended_at timestamptz"))
        con.execute(text("ALTER TABLE public.profiles ADD COLUMN IF NOT EXISTS last_login_at timestamptz"))
        con.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_profiles_email_ci ON public.profiles (lower(email)) WHERE email IS NOT NULL AND btrim(email) <> ''"))
        con.execute(text("CREATE INDEX IF NOT EXISTS ix_panel_assignments_target_status ON public.panel_assignments (assigned_to_profile_id,status)"))
        con.execute(text("CREATE INDEX IF NOT EXISTS ix_panel_assignments_owner ON public.panel_assignments (owner_profile_id)"))
        con.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_created ON public.audit_events (created_at DESC)"))
        con.execute(text("CREATE INDEX IF NOT EXISTS ix_audit_events_profile ON public.audit_events (profile_id,created_at DESC)"))


def _ensure_bootstrap_admin() -> None:
    """Garante o administrador principal sem armazenar senha no APK.

    O vínculo futuro com Supabase Auth será feito pelo e-mail.
    """
    if not BOOTSTRAP_ADMIN_EMAIL:
        return
    now = now_dt()
    with engine.begin() as con:
        row = con.execute(text("""
            SELECT id FROM public.profiles
            WHERE lower(coalesce(email,'')) = :email
               OR upper(usuario) = upper(:usuario)
            ORDER BY CASE WHEN lower(coalesce(email,'')) = :email THEN 0 ELSE 1 END, id
            LIMIT 1
        """), {"email": BOOTSTRAP_ADMIN_EMAIL, "usuario": BOOTSTRAP_ADMIN_USERNAME}).first()
        if row:
            con.execute(text("""
                UPDATE public.profiles
                   SET email=:email, usuario=:usuario, perfil='ADMIN', ativo=true,
                       is_system_admin=true, updated_at=:updated_at
                 WHERE id=:id
            """), {"email": BOOTSTRAP_ADMIN_EMAIL, "usuario": BOOTSTRAP_ADMIN_USERNAME, "updated_at": now, "id": int(row[0])})
        else:
            con.execute(text("""
                INSERT INTO public.profiles(email,is_system_admin,nome,usuario,perfil,ativo,must_change_password,created_at,updated_at)
                VALUES(:email,true,'Administrador principal',:usuario,'ADMIN',true,true,:created_at,:updated_at)
            """), {"email": BOOTSTRAP_ADMIN_EMAIL, "usuario": BOOTSTRAP_ADMIN_USERNAME, "created_at": now, "updated_at": now})


def _catalog_source_db() -> Path:
    return Path(__file__).resolve().parent / "database" / "dados_criacao_laudos.db"


def _seed_catalog_if_empty() -> None:
    """Faz a carga inicial do catálogo legado apenas quando o PostgreSQL está vazio.

    Depois da primeira carga, o PostgreSQL passa a ser a fonte central e não é
    sobrescrito em cada reinício do Render.
    """
    src_path = _catalog_source_db()
    if not src_path.exists():
        return
    with engine.connect() as con:
        total = int(con.execute(text("SELECT count(*) FROM public.catalog_models")).scalar_one())
    if total > 0:
        return

    with sqlite3.connect(src_path) as src:
        src.row_factory = sqlite3.Row
        models = [dict(r) for r in src.execute("SELECT * FROM cadastro_modelos").fetchall()]
        observations = [dict(r) for r in src.execute("SELECT * FROM cadastro_observacoes").fetchall()]
        people = [dict(r) for r in src.execute("SELECT * FROM cadastro_pessoas").fetchall()]

    with engine.begin() as dst:
        for r in models:
            dst.execute(pg_insert(catalog_models).values(**{k: r.get(k) for k in [
                "id","fabricante","modelo","portaria","classe","elementos","corrente_nominal",
                "corrente_maxima","tensao_nominal","frequencia","constante","portaria_rtm"
            ]}).on_conflict_do_nothing(index_elements=[catalog_models.c.id]))
        for r in observations:
            dst.execute(pg_insert(catalog_observations).values(
                id=r.get("id"), observacao=r.get("observacao"), conclusao=r.get("conclusao")
            ).on_conflict_do_nothing(index_elements=[catalog_observations.c.id]))
        for r in people:
            dst.execute(pg_insert(catalog_people).values(
                id=r.get("id"), categoria=r.get("categoria"), nome=r.get("nome")
            ).on_conflict_do_nothing(index_elements=[catalog_people.c.id]))


def ensure_db() -> Path:
    global _BOOTSTRAP_DONE
    metadata.create_all(engine)
    if not _BOOTSTRAP_DONE:
        _ensure_online_migrations()
        _ensure_bootstrap_admin()
        _seed_catalog_if_empty()
        _BOOTSTRAP_DONE = True
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
                remote_laudo_numero: str | None = None, created_by_profile_id: int | None = None) -> dict:
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
            if created_by_profile_id and not cur.get("created_by_profile_id"):
                values["created_by_profile_id"] = int(created_by_profile_id)
            con.execute(update(espelhos).where(espelhos.c.id == int(record_id)).values(**values))
            rid = int(record_id)
        else:
            result = con.execute(insert(espelhos).values(
                created_at=now, updated_at=now, status=status, numero_laudo=n, ano=ano, tipo=tipo,
                instalacao=inst, numero_serie=serie, modelo=modelo, payload_json=data,
                export_path=export_path or "", bridge_id=bridge_id or "",
                status_message=str(status_message or ""), status_updated_at=now,
                remote_laudo_numero=str(remote_laudo_numero or ""),
                created_by_profile_id=int(created_by_profile_id) if created_by_profile_id else None,
            ).returning(espelhos.c.id))
            rid = int(result.scalar_one())
        row = con.execute(select(espelhos).where(espelhos.c.id == rid)).first()
    return _row_to_dict(row)


def list_records(limit: int = 100, profile_id: int | None = None, include_all: bool = True) -> list[dict]:
    ensure_db()
    stmt = select(espelhos)
    if not include_all:
        if not profile_id:
            return []
        stmt = stmt.where(espelhos.c.created_by_profile_id == int(profile_id))
    stmt = stmt.order_by(desc(espelhos.c.updated_at), desc(espelhos.c.id)).limit(max(1, min(500, int(limit))))
    with engine.connect() as con:
        rows = con.execute(stmt).all()
    return [_row_to_dict(r) for r in rows]


def get_record(record_id: int, profile_id: int | None = None, include_all: bool = True) -> dict | None:
    ensure_db()
    stmt = select(espelhos).where(espelhos.c.id == int(record_id))
    if not include_all:
        if not profile_id:
            return None
        stmt = stmt.where(espelhos.c.created_by_profile_id == int(profile_id))
    with engine.connect() as con:
        row = con.execute(stmt).first()
    return _row_to_dict(row) if row else None


def delete_record(record_id: int, profile_id: int | None = None, include_all: bool = True) -> bool:
    ensure_db()
    stmt = delete(espelhos).where(espelhos.c.id == int(record_id))
    if not include_all:
        if not profile_id:
            return False
        stmt = stmt.where(espelhos.c.created_by_profile_id == int(profile_id))
    with engine.begin() as con:
        result = con.execute(stmt)
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


def list_notifications(limit: int = 100) -> list[dict]:
    ensure_db()
    with engine.connect() as con:
        rows = con.execute(
            select(notifications).order_by(desc(notifications.c.created_at), desc(notifications.c.id)).limit(max(1, min(500, int(limit))))
        ).all()
    out = []
    for r in rows:
        d = dict(r._mapping)
        for k in ("created_at", "read_at"):
            if isinstance(d.get(k), datetime):
                d[k] = d[k].isoformat(timespec="seconds")
        out.append(d)
    return out



def archive_notification(notification_id: int) -> bool:
    """Exclusão lógica: some da caixa do painel, mas mantém trilha de auditoria."""
    ensure_db()
    with engine.begin() as con:
        result = con.execute(
            update(notifications)
            .where(notifications.c.id == int(notification_id))
            .values(status="EXCLUIDA", read_at=now_dt())
        )
    return bool(result.rowcount)


def register_push_device(profile_id: int, token: str, device_name: str = "Android") -> dict:
    ensure_db()
    token = str(token or "").strip()
    if not token:
        raise ValueError("Token FCM não informado.")
    now = now_dt()
    values = {
        "profile_id": int(profile_id),
        "token": token,
        "platform": "ANDROID",
        "device_name": str(device_name or "Android").strip()[:180] or "Android",
        "active": True,
        "updated_at": now,
    }
    with engine.begin() as con:
        existing = con.execute(select(push_devices).where(push_devices.c.token == token)).first()
        if existing:
            con.execute(update(push_devices).where(push_devices.c.token == token).values(**values))
        else:
            con.execute(insert(push_devices).values(created_at=now, **values))
        row = con.execute(select(push_devices).where(push_devices.c.token == token)).first()
    d = dict(row._mapping) if row else {}
    for k in ("created_at", "updated_at"):
        if isinstance(d.get(k), datetime):
            d[k] = d[k].isoformat(timespec="seconds")
    d.pop("token", None)
    return d


def unregister_push_devices(profile_id: int, token: str = "") -> int:
    ensure_db()
    stmt = update(push_devices).where(push_devices.c.profile_id == int(profile_id))
    token = str(token or "").strip()
    if token:
        stmt = stmt.where(push_devices.c.token == token)
    with engine.begin() as con:
        result = con.execute(stmt.values(active=False, updated_at=now_dt()))
    return int(result.rowcount or 0)


def list_push_tokens_for_record(record_id: int) -> list[str]:
    ensure_db()
    with engine.connect() as con:
        record = con.execute(
            select(espelhos.c.created_by_profile_id).where(espelhos.c.id == int(record_id))
        ).first()
        if not record or not record[0]:
            return []
        rows = con.execute(
            select(push_devices.c.token).where(
                push_devices.c.profile_id == int(record[0]),
                push_devices.c.active.is_(True),
            )
        ).all()
    return [str(r[0]) for r in rows if str(r[0] or "").strip()]


def list_app_users() -> list[dict]:
    ensure_db()
    with engine.connect() as con:
        # O administrador principal fica reservado internamente por enquanto.
        rows = con.execute(
            select(profiles).where(profiles.c.is_system_admin.is_(False)).order_by(profiles.c.nome, profiles.c.id)
        ).all()
    out = []
    for r in rows:
        d = dict(r._mapping)
        d["ativo"] = 1 if d.get("ativo") else 0
        d.pop("is_system_admin", None)
        for k in ("created_at", "updated_at", "suspended_at", "last_login_at"):
            if isinstance(d.get(k), datetime): d[k] = d[k].isoformat(timespec="seconds")
        out.append(d)
    return out


def get_app_user(user_id: int) -> dict | None:
    ensure_db()
    with engine.connect() as con:
        row = con.execute(select(profiles).where(profiles.c.id == int(user_id))).first()
    if not row:
        return None
    d = dict(row._mapping)
    d["ativo"] = 1 if d.get("ativo") else 0
    for k in ("created_at", "updated_at", "suspended_at", "last_login_at"):
        if isinstance(d.get(k), datetime): d[k] = d[k].isoformat(timespec="seconds")
    return d


def find_app_user_conflicts(*, usuario: str = "", email: str = "", auth_user_id: str = "", exclude_user_id: int | None = None) -> list[dict]:
    """Localiza conflitos reais de identidade antes de gravar um perfil.

    A V37 convertia qualquer IntegrityError em "login ou e-mail", escondendo
    inclusive conflitos de auth_user_id. A V38 separa cada causa para evitar
    falsos diagnósticos e impedir a criação parcial no Supabase Auth.
    """
    ensure_db()
    usuario = str(usuario or "").strip()
    email = str(email or "").strip().lower()
    auth_user_id = str(auth_user_id or "").strip()
    clauses = []
    if usuario:
        clauses.append(func.lower(profiles.c.usuario) == usuario.lower())
    if email:
        clauses.append(func.lower(func.coalesce(profiles.c.email, "")) == email)
    if auth_user_id:
        clauses.append(profiles.c.auth_user_id == auth_user_id)
    if not clauses:
        return []
    from sqlalchemy import or_
    stmt = select(profiles).where(or_(*clauses))
    if exclude_user_id:
        stmt = stmt.where(profiles.c.id != int(exclude_user_id))
    with engine.connect() as con:
        rows = con.execute(stmt.order_by(profiles.c.is_system_admin.desc(), profiles.c.id)).all()
    out=[]
    for r in rows:
        d=dict(r._mapping)
        d["ativo"] = 1 if d.get("ativo") else 0
        for k in ("created_at", "updated_at", "suspended_at", "last_login_at"):
            if isinstance(d.get(k), datetime): d[k] = d[k].isoformat(timespec="seconds")
        out.append(d)
    return out


def save_app_user(data: dict, user_id: int | None = None) -> dict:
    ensure_db()
    nome = str(data.get("nome") or "").strip()
    usuario = str(data.get("usuario") or "").strip()
    email = str(data.get("email") or "").strip().lower() or None
    perfil = str(data.get("perfil") or "OPERADOR").strip().upper()
    ativo = bool(data.get("ativo", True))
    auth_user_id = str(data.get("auth_user_id") or "").strip() or None
    must_change = bool(data.get("must_change_password", True))
    if not nome or not usuario:
        raise ValueError("Nome e usuário são obrigatórios.")
    if usuario.upper() == BOOTSTRAP_ADMIN_USERNAME.upper():
        raise ValueError("Este usuário é reservado ao administrador principal.")
    if email and email == BOOTSTRAP_ADMIN_EMAIL:
        raise ValueError("Este e-mail é reservado ao administrador principal.")
    if perfil not in {"ADMIN", "OPERADOR", "FUNCAO"}:
        perfil = "OPERADOR"
    now = now_dt()
    try:
        with engine.begin() as con:
            current = con.execute(select(profiles).where(profiles.c.id == int(user_id))).first() if user_id else None
            if current:
                if bool(current._mapping.get("is_system_admin")):
                    raise ValueError("O administrador principal não pode ser alterado por este menu.")
                values = dict(nome=nome, usuario=usuario, email=email, perfil=perfil, ativo=ativo, updated_at=now)
                if auth_user_id:
                    values["auth_user_id"] = auth_user_id
                if "must_change_password" in data:
                    values["must_change_password"] = must_change
                con.execute(update(profiles).where(profiles.c.id == int(user_id)).values(**values))
                rid = int(user_id)
            else:
                rid = int(con.execute(insert(profiles).values(
                    nome=nome, usuario=usuario, email=email, perfil=perfil, ativo=ativo,
                    auth_user_id=auth_user_id, must_change_password=must_change,
                    is_system_admin=False, created_at=now, updated_at=now
                ).returning(profiles.c.id)).scalar_one())
            row = con.execute(select(profiles).where(profiles.c.id == rid)).first()
        d = dict(row._mapping)
        d["ativo"] = 1 if d.get("ativo") else 0
        d.pop("is_system_admin", None)
        for k in ("created_at", "updated_at", "suspended_at", "last_login_at"):
            if isinstance(d.get(k), datetime): d[k] = d[k].isoformat(timespec="seconds")
        return d
    except IntegrityError as exc:
        constraint = ""
        try:
            constraint = str(getattr(getattr(exc, "orig", None), "diag", None).constraint_name or "").lower()
        except Exception:
            constraint = ""
        if "usuario" in constraint:
            raise ValueError(f"Já existe um usuário com o login '{usuario}'. Use outro login.") from exc
        if "email" in constraint:
            raise ValueError(f"Já existe um usuário com o e-mail '{email}'.") from exc
        if "auth_user_id" in constraint:
            raise ValueError("Este acesso do Supabase Auth já está vinculado a outro perfil do ID LAUDO.") from exc
        raise ValueError("Não foi possível salvar o usuário por um conflito de cadastro. Atualize a lista de usuários e tente novamente.") from exc


def bind_auth_profile(auth_user_id: str, email: str, nome: str = "") -> dict | None:
    ensure_db()
    auth_user_id = str(auth_user_id or "").strip()
    email = str(email or "").strip().lower()
    if not auth_user_id or not email:
        return None
    now = now_dt()
    with engine.begin() as con:
        row = con.execute(select(profiles).where(
            (profiles.c.auth_user_id == auth_user_id) | (profiles.c.email.ilike(email))
        ).order_by(profiles.c.is_system_admin.desc(), profiles.c.id)).first()
        if not row and email == BOOTSTRAP_ADMIN_EMAIL:
            _ensure_bootstrap_admin()
            row = con.execute(select(profiles).where(profiles.c.is_system_admin.is_(True)).limit(1)).first()
        if not row:
            return None
        rid = int(row._mapping["id"])
        values = {"auth_user_id": auth_user_id, "email": email, "last_login_at": now, "updated_at": now}
        if nome and not str(row._mapping.get("nome") or "").strip():
            values["nome"] = nome
        con.execute(update(profiles).where(profiles.c.id == rid).values(**values))
        row = con.execute(select(profiles).where(profiles.c.id == rid)).first()
    d = dict(row._mapping)
    d["ativo"] = 1 if d.get("ativo") else 0
    for k in ("created_at", "updated_at", "suspended_at", "last_login_at"):
        if isinstance(d.get(k), datetime): d[k] = d[k].isoformat(timespec="seconds")
    return d


def mark_password_changed(profile_id: int) -> None:
    ensure_db()
    with engine.begin() as con:
        con.execute(update(profiles).where(profiles.c.id == int(profile_id)).values(
            must_change_password=False, updated_at=now_dt()
        ))

def require_password_change_by_email(email: str) -> None:
    ensure_db()
    target = str(email or "").strip().lower()
    if not target:
        return
    with engine.begin() as con:
        con.execute(update(profiles).where(profiles.c.email.ilike(target)).values(
            must_change_password=True, updated_at=now_dt()
        ))


def set_app_user_active(user_id: int, active: bool) -> dict | None:
    ensure_db()
    now = now_dt()
    with engine.begin() as con:
        row = con.execute(select(profiles).where(profiles.c.id == int(user_id))).first()
        if not row:
            return None
        if bool(row._mapping.get("is_system_admin")):
            raise ValueError("O administrador principal não pode ser suspenso.")
        con.execute(update(profiles).where(profiles.c.id == int(user_id)).values(
            ativo=bool(active), suspended_at=None if active else now, updated_at=now
        ))
    return get_app_user(user_id)


def delete_app_user(user_id: int) -> bool:
    ensure_db()
    with engine.begin() as con:
        row = con.execute(select(profiles.c.is_system_admin).where(profiles.c.id == int(user_id))).first()
        if row and bool(row[0]):
            raise ValueError("O administrador principal não pode ser excluído.")
        result = con.execute(delete(profiles).where(profiles.c.id == int(user_id)))
    return bool(result.rowcount)


def bootstrap_admin_info() -> dict:
    ensure_db()
    with engine.connect() as con:
        row = con.execute(select(profiles.c.id, profiles.c.email, profiles.c.usuario, profiles.c.perfil, profiles.c.ativo, profiles.c.auth_user_id, profiles.c.must_change_password)
                          .where(profiles.c.is_system_admin.is_(True)).limit(1)).first()
    if not row:
        return {"configured": False}
    d = dict(row._mapping)
    return {"configured": True, "perfil": d.get("perfil"), "ativo": bool(d.get("ativo")), "auth_linked": bool(d.get("auth_user_id")), "must_change_password": bool(d.get("must_change_password")), "login_mode": "EMAIL_SUPABASE_AUTH"}


def catalog_counts() -> dict:
    ensure_db()
    with engine.connect() as con:
        return {
            "modelos": int(con.execute(text("SELECT count(*) FROM public.catalog_models")).scalar_one()),
            "observacoes": int(con.execute(text("SELECT count(*) FROM public.catalog_observations")).scalar_one()),
            "pessoas": int(con.execute(text("SELECT count(*) FROM public.catalog_people")).scalar_one()),
            "fabricantes": int(con.execute(text("SELECT count(DISTINCT fabricante) FROM public.catalog_models WHERE btrim(coalesce(fabricante,'')) <> ''")).scalar_one()),
        }


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


def export_bridge(data: dict, record_id: int | None = None, created_by_profile_id: int | None = None) -> dict:
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
    saved = save_record(payload, record_id=record_id, status=STATUS_AGUARDANDO, bridge_id=bridge_id, created_by_profile_id=created_by_profile_id)
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
    return {"mode": "ONLINE", "database": host, "persistent": True, "auth": "ATIVO" if str(os.environ.get("ID_LAUDO_AUTH_ENABLED") or "").strip().lower() in {"1","true","yes","sim","on"} else "PREPARADO", "notifications": True}


# ---------------------------------------------------------------------------
# V1.0.0.37 — Painel: distribuição, baixa e auditoria centralizada.
# ---------------------------------------------------------------------------
def list_panel_users(*, active_only: bool = True, roles: list[str] | None = None) -> list[dict]:
    ensure_db()
    stmt = select(profiles).order_by(profiles.c.nome, profiles.c.id)
    if active_only:
        stmt = stmt.where(profiles.c.ativo.is_(True))
    if roles:
        role_values = [str(v or '').strip().upper() for v in roles if str(v or '').strip()]
        if role_values:
            stmt = stmt.where(profiles.c.perfil.in_(role_values))
    with engine.connect() as con:
        rows = con.execute(stmt).all()
    out=[]
    for r in rows:
        d=dict(r._mapping)
        d['ativo']=1 if d.get('ativo') else 0
        d.pop('is_system_admin', None)
        d.pop('must_change_password', None)
        for k in ('created_at','updated_at','suspended_at','last_login_at'):
            if isinstance(d.get(k), datetime): d[k]=d[k].isoformat(timespec='seconds')
        out.append(d)
    return out

def _assignment_public(d: dict, *, include_pdf: bool = False) -> dict:
    out=dict(d or {})
    if not include_pdf:
        out.pop('pdf_data', None)
    for k in ('created_at','updated_at','sent_at','downloaded_at'):
        if isinstance(out.get(k), datetime): out[k]=out[k].isoformat(timespec='seconds')
    return out

def create_panel_assignment(*, pdf_bytes: bytes, filename: str, numero_laudo: str, numero_serie: str,
                            assigned_to_profile_id: int, assigned_by_profile_id: int, owner_profile_id: int | None = None,
                            source_record_id: int | None = None, source_local_id: int | None = None,
                            metadata_json: dict | None = None, document_uid: str = '') -> dict:
    import hashlib
    ensure_db()
    data=bytes(pdf_bytes or b'')
    if not data or not data.startswith(b'%PDF'):
        raise ValueError('O arquivo enviado não é um PDF válido.')
    sha=hashlib.sha256(data).hexdigest()
    uid=str(document_uid or '').strip() or sha[:32] + '-' + uuid.uuid4().hex[:12]
    now=now_dt()
    values=dict(
        document_uid=uid, source_record_id=int(source_record_id) if source_record_id else None,
        source_local_id=int(source_local_id) if source_local_id else None,
        numero_laudo=str(numero_laudo or '').strip(), numero_serie=str(numero_serie or '').strip(),
        filename=str(filename or 'LAUDO.pdf').strip()[:260], pdf_data=data, pdf_size=len(data), pdf_sha256=sha,
        status=STATUS_AGUARDANDO_BAIXA, assigned_to_profile_id=int(assigned_to_profile_id),
        assigned_by_profile_id=int(assigned_by_profile_id), owner_profile_id=int(owner_profile_id or assigned_by_profile_id),
        correction_message='', metadata_json=dict(metadata_json or {}), updated_at=now, sent_at=now, downloaded_at=None,
    )
    with engine.begin() as con:
        existing=con.execute(select(panel_assignments).where(panel_assignments.c.document_uid==uid)).first()
        if existing:
            con.execute(update(panel_assignments).where(panel_assignments.c.id==int(existing._mapping['id'])).values(**values))
            rid=int(existing._mapping['id'])
        else:
            rid=int(con.execute(insert(panel_assignments).values(created_at=now, **values).returning(panel_assignments.c.id)).scalar_one())
        row=con.execute(select(panel_assignments).where(panel_assignments.c.id==rid)).first()
    return _assignment_public(dict(row._mapping))

def get_panel_assignment(assignment_id: int) -> dict | None:
    ensure_db()
    with engine.connect() as con:
        row=con.execute(select(panel_assignments).where(panel_assignments.c.id==int(assignment_id))).first()
    return _assignment_public(dict(row._mapping), include_pdf=True) if row else None

def list_panel_assignments(*, profile_id: int, role: str, limit: int = 500) -> list[dict]:
    ensure_db()
    role=str(role or '').strip().upper()
    stmt=select(panel_assignments).order_by(desc(panel_assignments.c.updated_at)).limit(max(1,min(2000,int(limit or 500))))
    if role=='FUNCAO':
        stmt=stmt.where(panel_assignments.c.assigned_to_profile_id==int(profile_id))
    elif role=='OPERADOR':
        stmt=stmt.where((panel_assignments.c.assigned_to_profile_id==int(profile_id)) | (panel_assignments.c.owner_profile_id==int(profile_id)) | (panel_assignments.c.assigned_by_profile_id==int(profile_id)))
    with engine.connect() as con:
        rows=con.execute(stmt).all()
        user_rows=con.execute(select(profiles.c.id,profiles.c.nome,profiles.c.email,profiles.c.perfil)).all()
    users={int(r[0]): {'id':int(r[0]),'nome':r[1] or '', 'email':r[2] or '', 'perfil':r[3] or ''} for r in user_rows}
    out=[]
    for r in rows:
        d=_assignment_public(dict(r._mapping))
        d['assigned_to']=users.get(int(d.get('assigned_to_profile_id') or 0),{})
        d['assigned_by']=users.get(int(d.get('assigned_by_profile_id') or 0),{})
        d['owner']=users.get(int(d.get('owner_profile_id') or 0),{})
        out.append(d)
    return out

def update_panel_assignment(assignment_id: int, *, status: str | None = None, assigned_to_profile_id: int | None = None,
                            correction_message: str | None = None, pdf_bytes: bytes | None = None, filename: str | None = None,
                            assigned_by_profile_id: int | None = None) -> dict | None:
    import hashlib
    ensure_db(); now=now_dt(); values={'updated_at':now}
    if status is not None: values['status']=str(status or '').strip().upper()
    if assigned_to_profile_id is not None: values['assigned_to_profile_id']=int(assigned_to_profile_id)
    if assigned_by_profile_id is not None: values['assigned_by_profile_id']=int(assigned_by_profile_id)
    if correction_message is not None: values['correction_message']=str(correction_message or '').strip()
    if values.get('status')==STATUS_BAIXADO: values['downloaded_at']=now
    if values.get('status')==STATUS_AGUARDANDO_BAIXA: values['sent_at']=now; values['downloaded_at']=None
    if pdf_bytes is not None:
        data=bytes(pdf_bytes or b'')
        if not data.startswith(b'%PDF'): raise ValueError('O arquivo enviado não é um PDF válido.')
        values.update(pdf_data=data,pdf_size=len(data),pdf_sha256=hashlib.sha256(data).hexdigest())
    if filename is not None: values['filename']=str(filename or 'LAUDO.pdf').strip()[:260]
    with engine.begin() as con:
        result=con.execute(update(panel_assignments).where(panel_assignments.c.id==int(assignment_id)).values(**values))
        if not result.rowcount: return None
        row=con.execute(select(panel_assignments).where(panel_assignments.c.id==int(assignment_id))).first()
    return _assignment_public(dict(row._mapping)) if row else None

def add_audit_event(profile: dict | None, action: str, *, entity_type: str = '', entity_id: object = '', numero_laudo: str = '', details: dict | None = None) -> dict:
    ensure_db(); p=dict(profile or {}); now=now_dt()
    values=dict(
        profile_id=int(p['id']) if p.get('id') else None, user_name=str(p.get('nome') or p.get('usuario') or ''),
        user_email=str(p.get('email') or ''), user_role=str(p.get('perfil') or ''), action=str(action or '').strip().upper(),
        entity_type=str(entity_type or '').strip().upper(), entity_id=str(entity_id or ''), numero_laudo=str(numero_laudo or '').strip(),
        details_json=dict(details or {}), created_at=now,
    )
    with engine.begin() as con:
        rid=int(con.execute(insert(audit_events).values(**values).returning(audit_events.c.id)).scalar_one())
    return {'id':rid, **values, 'created_at':now.isoformat(timespec='seconds')}

def list_audit_events(*, limit: int = 500, profile_id: int | None = None, action: str = '', numero_laudo: str = '') -> list[dict]:
    ensure_db(); stmt=select(audit_events).order_by(desc(audit_events.c.created_at)).limit(max(1,min(5000,int(limit or 500))))
    if profile_id: stmt=stmt.where(audit_events.c.profile_id==int(profile_id))
    if str(action or '').strip(): stmt=stmt.where(audit_events.c.action.ilike(f"%{str(action).strip()}%"))
    if str(numero_laudo or '').strip(): stmt=stmt.where(audit_events.c.numero_laudo.ilike(f"%{str(numero_laudo).strip()}%"))
    with engine.connect() as con: rows=con.execute(stmt).all()
    out=[]
    for r in rows:
        d=dict(r._mapping)
        if isinstance(d.get('created_at'),datetime): d['created_at']=d['created_at'].isoformat(timespec='seconds')
        out.append(d)
    return out
