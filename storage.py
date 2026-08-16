from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import re
import sqlite3
import uuid

APP_NAME = "ID LAUDO"
APP_VERSION = "1.0.0.42"

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

BOOTSTRAP_ADMIN_EMAIL = str(os.environ.get("ID_LAUDO_BOOTSTRAP_ADMIN_EMAIL") or "dayvisant4@gmail.com").strip().lower()
BOOTSTRAP_ADMIN_USERNAME = str(os.environ.get("ID_LAUDO_BOOTSTRAP_ADMIN_USERNAME") or "ADMIN").strip() or "ADMIN"


def documents_dir() -> Path:
    home = Path.home()
    candidates = [home / "Documents", home / "Documentos"]
    for p in candidates:
        if p.exists():
            return p
    return home / "Documents"


def app_data_dir() -> Path:
    root = documents_dir() / "ID LAUDO"
    (root / "DADOS").mkdir(parents=True, exist_ok=True)
    (root / "ENVIAR_ID_CAMPS").mkdir(parents=True, exist_ok=True)
    return root


def db_path() -> Path:
    return app_data_dir() / "DADOS" / "id_laudo.db"


def outbox_dir() -> Path:
    return app_data_dir() / "ENVIAR_ID_CAMPS"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_status(status: object) -> str:
    value = str(status or STATUS_RASCUNHO).strip().upper()
    # Compatibilidade com versões anteriores.
    if value == "PRONTO":
        return STATUS_PRONTO
    return value if value in VALID_STATUSES else STATUS_RASCUNHO


def new_bridge_id() -> str:
    return f"IDL-{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6].upper()}"


def ensure_db() -> Path:
    path = db_path()
    with sqlite3.connect(path) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS espelhos(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'RASCUNHO',
                numero_laudo TEXT,
                ano TEXT,
                tipo TEXT,
                instalacao TEXT,
                numero_serie TEXT,
                modelo TEXT,
                payload_json TEXT NOT NULL,
                export_path TEXT DEFAULT '',
                bridge_id TEXT DEFAULT '',
                status_message TEXT DEFAULT '',
                status_updated_at TEXT DEFAULT '',
                remote_laudo_numero TEXT DEFAULT ''
            )
            """
        )
        # Migração segura de bancos criados pelas versões anteriores.
        cols = {r[1] for r in con.execute("PRAGMA table_info(espelhos)").fetchall()}
        migrations = {
            "bridge_id": "TEXT DEFAULT ''",
            "status_message": "TEXT DEFAULT ''",
            "status_updated_at": "TEXT DEFAULT ''",
            "remote_laudo_numero": "TEXT DEFAULT ''",
        }
        for name, ddl in migrations.items():
            if name not in cols:
                con.execute(f"ALTER TABLE espelhos ADD COLUMN {name} {ddl}")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS app_users(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL,
                usuario TEXT NOT NULL UNIQUE COLLATE NOCASE,
                email TEXT,
                is_system_admin INTEGER NOT NULL DEFAULT 0,
                perfil TEXT NOT NULL DEFAULT 'OPERADOR',
                ativo INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        user_cols = {r[1] for r in con.execute("PRAGMA table_info(app_users)").fetchall()}
        if "email" not in user_cols:
            con.execute("ALTER TABLE app_users ADD COLUMN email TEXT")
        if "is_system_admin" not in user_cols:
            con.execute("ALTER TABLE app_users ADD COLUMN is_system_admin INTEGER NOT NULL DEFAULT 0")
        row = con.execute(
            "SELECT id FROM app_users WHERE lower(coalesce(email,''))=? OR upper(usuario)=upper(?) ORDER BY id LIMIT 1",
            (BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_USERNAME),
        ).fetchone()
        now = now_iso()
        if row:
            con.execute(
                "UPDATE app_users SET email=?,usuario=?,perfil='ADMIN',ativo=1,is_system_admin=1,updated_at=? WHERE id=?",
                (BOOTSTRAP_ADMIN_EMAIL, BOOTSTRAP_ADMIN_USERNAME, now, int(row[0])),
            )
        else:
            con.execute(
                "INSERT INTO app_users(nome,usuario,email,is_system_admin,perfil,ativo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                ("Administrador principal", BOOTSTRAP_ADMIN_USERNAME, BOOTSTRAP_ADMIN_EMAIL, 1, "ADMIN", 1, now, now),
            )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS app_settings(
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        con.commit()
    return path


def _summary(payload: dict) -> tuple[str, ...]:
    return (
        str(payload.get("numero_laudo") or "").strip(),
        str(payload.get("ano") or "").strip(),
        str(payload.get("tipo") or "NR").strip().upper(),
        str(payload.get("instalacao") or "").strip(),
        str(payload.get("numero_serie") or "").strip(),
        str(payload.get("modelo") or "").strip(),
    )


def save_record(
    data: dict,
    record_id: int | None = None,
    status: str = STATUS_RASCUNHO,
    export_path: str = "",
    bridge_id: str = "",
    status_message: str | None = None,
    remote_laudo_numero: str | None = None,
    created_by_profile_id: int | None = None,
) -> dict:
    ensure_db()
    status = normalize_status(status)
    raw = json.dumps(data, ensure_ascii=False)
    n, ano, tipo, inst, serie, modelo = _summary(data)
    now = now_iso()
    with sqlite3.connect(db_path()) as con:
        con.row_factory = sqlite3.Row
        if record_id:
            current = con.execute("SELECT * FROM espelhos WHERE id=?", (int(record_id),)).fetchone()
            if not current:
                record_id = None
            else:
                current_bridge = str(current["bridge_id"] or "")
                final_bridge = bridge_id or current_bridge
                msg = str(current["status_message"] or "") if status_message is None else str(status_message or "")
                remote = str(current["remote_laudo_numero"] or "") if remote_laudo_numero is None else str(remote_laudo_numero or "")
                con.execute(
                    """UPDATE espelhos SET updated_at=?, status=?, numero_laudo=?, ano=?, tipo=?, instalacao=?,
                       numero_serie=?, modelo=?, payload_json=?, export_path=CASE WHEN ?<>'' THEN ? ELSE export_path END,
                       bridge_id=?, status_message=?, status_updated_at=?, remote_laudo_numero=?
                       WHERE id=?""",
                    (now, status, n, ano, tipo, inst, serie, modelo, raw, export_path, export_path,
                     final_bridge, msg, now, remote, int(record_id)),
                )
                rid = int(record_id)
        if not record_id:
            final_bridge = bridge_id or ""
            cur = con.execute(
                """INSERT INTO espelhos(created_at,updated_at,status,numero_laudo,ano,tipo,instalacao,numero_serie,modelo,payload_json,export_path,bridge_id,status_message,status_updated_at,remote_laudo_numero)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (now, now, status, n, ano, tipo, inst, serie, modelo, raw, export_path,
                 final_bridge, str(status_message or ""), now, str(remote_laudo_numero or "")),
            )
            rid = int(cur.lastrowid)
        con.commit()
        row = con.execute("SELECT * FROM espelhos WHERE id=?", (rid,)).fetchone()
    return row_to_dict(row)


def row_to_dict(row) -> dict:
    d = dict(row)
    d["status"] = normalize_status(d.get("status"))
    try:
        d["payload"] = json.loads(d.pop("payload_json"))
    except Exception:
        d["payload"] = {}
        d.pop("payload_json", None)
    return d


def list_records(limit: int = 100, profile_id: int | None = None, include_all: bool = True) -> list[dict]:
    ensure_db()
    with sqlite3.connect(db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT * FROM espelhos ORDER BY updated_at DESC, id DESC LIMIT ?", (max(1, min(500, int(limit))),)).fetchall()
    return [row_to_dict(r) for r in rows]


def get_record(record_id: int, profile_id: int | None = None, include_all: bool = True) -> dict | None:
    ensure_db()
    with sqlite3.connect(db_path()) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM espelhos WHERE id=?", (int(record_id),)).fetchone()
    return row_to_dict(row) if row else None


def delete_record(record_id: int, profile_id: int | None = None, include_all: bool = True) -> bool:
    ensure_db()
    with sqlite3.connect(db_path()) as con:
        cur = con.execute("DELETE FROM espelhos WHERE id=?", (int(record_id),))
        con.commit()
        return cur.rowcount > 0


def update_record_status(record_id: int, status: str, message: str = "", remote_laudo_numero: str = "") -> dict | None:
    ensure_db()
    normalized = normalize_status(status)
    now = now_iso()
    with sqlite3.connect(db_path()) as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT payload_json FROM espelhos WHERE id=?", (int(record_id),)).fetchone()
        if not row:
            return None
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except Exception:
            payload = {}
        bridge = payload.setdefault("_bridge", {})
        bridge["status"] = normalized
        bridge["status_updated_at"] = now
        if message:
            bridge["status_message"] = message
        if remote_laudo_numero:
            bridge["remote_laudo_numero"] = remote_laudo_numero
        raw = json.dumps(payload, ensure_ascii=False)
        con.execute(
            """UPDATE espelhos SET status=?, status_message=?, remote_laudo_numero=?, status_updated_at=?, updated_at=?, payload_json=? WHERE id=?""",
            (normalized, str(message or ""), str(remote_laudo_numero or ""), now, now, raw, int(record_id)),
        )
        con.commit()
        full = con.execute("SELECT * FROM espelhos WHERE id=?", (int(record_id),)).fetchone()
    return row_to_dict(full) if full else None


def list_notifications(limit: int = 100) -> list[dict]:
    # No modo local não há Central de Notificações remota.
    return []



def archive_notification(notification_id: int) -> bool:
    return False


def archive_notifications_for_record(record_id: int) -> int:
    return 0

def register_push_device(profile_id: int, token: str, device_name: str = "Android") -> dict:
    return {"profile_id": int(profile_id), "device_name": str(device_name or "Android"), "active": False}

def unregister_push_devices(profile_id: int, token: str = "") -> int:
    return 0

def list_push_tokens_for_record(record_id: int) -> list[str]:
    return []

def list_app_users() -> list[dict]:
    ensure_db()
    with sqlite3.connect(db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id,nome,usuario,email,perfil,ativo,created_at,updated_at FROM app_users WHERE coalesce(is_system_admin,0)=0 ORDER BY nome COLLATE NOCASE, id"
        ).fetchall()
    return [dict(r) for r in rows]


def find_app_user_conflicts(*, usuario: str = "", email: str = "", auth_user_id: str = "", exclude_user_id: int | None = None) -> list[dict]:
    usuario = str(usuario or "").strip().lower()
    email = str(email or "").strip().lower()
    out=[]
    for u in list_app_users():
        if exclude_user_id and int(u.get("id") or 0) == int(exclude_user_id):
            continue
        if usuario and str(u.get("usuario") or "").strip().lower() == usuario:
            out.append(dict(u)); continue
        if email and str(u.get("email") or "").strip().lower() == email:
            out.append(dict(u))
    return out


def save_app_user(data: dict, user_id: int | None = None) -> dict:
    ensure_db()
    nome = str(data.get("nome") or "").strip()
    usuario = str(data.get("usuario") or "").strip()
    email = str(data.get("email") or "").strip().lower() or None
    perfil = str(data.get("perfil") or "OPERADOR").strip().upper()
    ativo = 1 if bool(data.get("ativo", True)) else 0
    if not nome or not usuario:
        raise ValueError("Nome e usuário são obrigatórios.")
    if usuario.upper() == BOOTSTRAP_ADMIN_USERNAME.upper():
        raise ValueError("Este usuário é reservado ao administrador principal.")
    if email and email == BOOTSTRAP_ADMIN_EMAIL:
        raise ValueError("Este e-mail é reservado ao administrador principal.")
    if perfil not in {"ADMIN", "OPERADOR", "FUNCAO"}:
        perfil = "OPERADOR"
    now = now_iso()
    try:
        with sqlite3.connect(db_path()) as con:
            con.row_factory = sqlite3.Row
            if user_id:
                protected = con.execute("SELECT is_system_admin FROM app_users WHERE id=?", (int(user_id),)).fetchone()
                if protected and int(protected[0] or 0):
                    raise ValueError("O administrador principal não pode ser alterado por este menu.")
                cur = con.execute(
                    "UPDATE app_users SET nome=?,usuario=?,email=?,perfil=?,ativo=?,updated_at=? WHERE id=?",
                    (nome, usuario, email, perfil, ativo, now, int(user_id)),
                )
                if cur.rowcount == 0:
                    user_id = None
            if not user_id:
                cur = con.execute(
                    "INSERT INTO app_users(nome,usuario,email,is_system_admin,perfil,ativo,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (nome, usuario, email, 0, perfil, ativo, now, now),
                )
                user_id = int(cur.lastrowid)
            con.commit()
            row = con.execute("SELECT id,nome,usuario,email,perfil,ativo,created_at,updated_at FROM app_users WHERE id=?", (int(user_id),)).fetchone()
        return dict(row)
    except sqlite3.IntegrityError as exc:
        raise ValueError("Já existe um usuário com esse login.") from exc


def delete_app_user(user_id: int) -> bool:
    ensure_db()
    with sqlite3.connect(db_path()) as con:
        protected = con.execute("SELECT is_system_admin FROM app_users WHERE id=?", (int(user_id),)).fetchone()
        if protected and int(protected[0] or 0):
            raise ValueError("O administrador principal não pode ser excluído.")
        cur = con.execute("DELETE FROM app_users WHERE id=?", (int(user_id),))
        con.commit()
        return cur.rowcount > 0


def bootstrap_admin_info() -> dict:
    ensure_db()
    with sqlite3.connect(db_path()) as con:
        row = con.execute("SELECT perfil,ativo FROM app_users WHERE coalesce(is_system_admin,0)=1 LIMIT 1").fetchone()
    return {"configured": bool(row), "perfil": row[0] if row else None, "ativo": bool(row[1]) if row else False, "login_mode": "EMAIL_SUPABASE_AUTH" if row else None}


def get_app_setting(key: str, default=None):
    ensure_db()
    with sqlite3.connect(db_path()) as con:
        row = con.execute("SELECT value_json FROM app_settings WHERE key=?", (str(key),)).fetchone()
    if not row:
        return default
    try:
        return json.loads(row[0])
    except Exception:
        return default


def set_app_setting(key: str, value) -> None:
    ensure_db()
    raw = json.dumps(value, ensure_ascii=False)
    now = now_iso()
    with sqlite3.connect(db_path()) as con:
        con.execute(
            """INSERT INTO app_settings(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
            (str(key), raw, now),
        )
        con.commit()


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
        "id": bridge_id,
        "app": APP_NAME,
        "version": APP_VERSION,
        "schema": "id-laudo-bridge/2",
        "target": "ID CAMPS LAUDOS",
        "created_at": bridge.get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "status": STATUS_PRONTO,
        "status_label": "Pronto para ID CAMPS",
    })
    payload["_bridge"] = bridge
    tipo = safe_component(payload.get("tipo") or "NR")
    nr = safe_component(payload.get("numero_laudo") or "____")
    ano = safe_component(payload.get("ano") or datetime.now().year)
    inst = safe_component(payload.get("instalacao"))
    serie = safe_component(payload.get("numero_serie"))
    name = f"IDLAUDO_{tipo}-{nr}-{ano}_INST_{inst}_MD_{serie}_{bridge_id}.json"
    target = outbox_dir() / name
    index = 2
    while target.exists():
        target = outbox_dir() / f"{Path(name).stem}_{index}.json"
        index += 1
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    saved = save_record(
        payload,
        record_id=record_id,
        status=STATUS_PRONTO,
        export_path=str(target),
        bridge_id=bridge_id,
        status_message="Modo local: arquivo preparado somente neste computador. Use o servidor ONLINE para enviar ao PostgreSQL.",
    )
    return {"record": saved, "path": str(target), "filename": target.name, "bridge_id": bridge_id}


# V1.0.0.37 — stubs do fluxo central quando o servidor estiver em modo SQLite local.
def list_panel_users(*, active_only=True, roles=None):
    rows = list_app_users()
    if active_only: rows = [r for r in rows if bool(r.get("ativo"))]
    if roles:
        allowed={str(v).upper() for v in roles}
        rows=[r for r in rows if str(r.get("perfil") or "").upper() in allowed]
    return rows

def create_panel_assignment(**kwargs):
    raise ValueError("Distribuição de laudos exige o backend PostgreSQL online.")

def get_panel_assignment(assignment_id): return None
def list_panel_assignments(*, profile_id, role, limit=500): return []
def update_panel_assignment(assignment_id, **kwargs): return None
def add_audit_event(profile, action, **kwargs):
    return {"id":0,"action":str(action or ""),"created_at":now_iso() if "now_iso" in globals() else datetime.now().isoformat(timespec="seconds")}
def list_audit_events(*, limit=500, profile_id=None, action="", numero_laudo=""): return []
