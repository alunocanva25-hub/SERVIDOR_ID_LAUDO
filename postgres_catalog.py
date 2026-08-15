from __future__ import annotations
from sqlalchemy import select, or_, func
from postgres_storage import engine, ensure_db, catalog_models, catalog_observations, catalog_people


def clean(value) -> str:
    return str(value or "").strip()


def list_models(search: str = "", limit: int = 500) -> list[dict]:
    ensure_db()
    stmt = select(catalog_models)
    if clean(search):
        term = f"%{clean(search).upper()}%"
        stmt = stmt.where(or_(func.upper(catalog_models.c.modelo).like(term), func.upper(catalog_models.c.fabricante).like(term)))
    stmt = stmt.order_by(catalog_models.c.modelo, catalog_models.c.fabricante).limit(max(1, min(500, int(limit))))
    with engine.connect() as con:
        return [dict(r._mapping) for r in con.execute(stmt).all()]


def list_observations() -> list[dict]:
    ensure_db()
    with engine.connect() as con:
        rows = con.execute(select(catalog_observations).order_by(catalog_observations.c.id)).all()
    return [dict(r._mapping) for r in rows if clean(r._mapping.get("observacao"))]


def list_observation_portarias() -> list[str]:
    seen, out = set(), []
    for row in list_observations():
        text = clean(row.get("conclusao"))
        key = text.upper()
        if text and key not in seen:
            seen.add(key); out.append(text)
    return out


def list_people() -> dict[str, list[str]]:
    ensure_db()
    result: dict[str, list[str]] = {}
    with engine.connect() as con:
        rows = con.execute(select(catalog_people).order_by(catalog_people.c.categoria, catalog_people.c.nome)).all()
    for r in rows:
        d = dict(r._mapping)
        if clean(d.get("nome")):
            result.setdefault(clean(d.get("categoria")), []).append(clean(d.get("nome")))
    return result
