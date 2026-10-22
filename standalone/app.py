"""Plain FastAPI web interface for the existing Python data pipeline.

Run with: uvicorn standalone.app:app --host 0.0.0.0 --port 8000
Does not import Jac. The Jac LLM fallback remains unavailable until ported.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from services import pipe, store  # noqa: E402

app = FastAPI(title="Road to Research - standalone", version="0.1.0")
HTML = Path(__file__).with_name("index.html")


def authorize(key: str | None) -> None:
    expected = os.environ.get("ADMIN_API_KEY", "")
    if len(expected) < 24:
        raise HTTPException(503, "Set a random ADMIN_API_KEY of at least 24 characters.")
    if not key or not secrets.compare_digest(key, expected):
        raise HTTPException(401, "Invalid admin key.")


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(HTML)


@app.get("/healthz")
def health():
    return {"ok": True}


@app.get("/readyz")
def ready():
    try:
        store.db().client.admin.command("ping")
        return {"database": "connected"}
    except Exception:
        raise HTTPException(503, "Database unavailable.") from None


@app.get("/api/search")
def search(
    q: str = Query("", max_length=300),
    university: str = Query("", max_length=200),
    department: str = Query("", max_length=200),
    subfield: str = Query("", max_length=200),
    has_grant: bool = False,
    has_hiring: bool = False,
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(24, ge=1, le=100),
):
    return store.search(query=q, university=university, department=department,
                        subfield=subfield, has_grant=has_grant, has_hiring=has_hiring,
                        offset=(page - 1) * limit, limit=limit)


@app.get("/api/filters")
def filters(university: str = Query("", max_length=200),
            department: str = Query("", max_length=200)):
    return store.filter_options(university=university, department=department)


@app.get("/api/professors/{professor_id:path}")
def professor(professor_id: str):
    if len(professor_id) > 350:
        raise HTTPException(400, "Invalid ID.")
    p = store.get_professor(professor_id)
    if p is None:
        raise HTTPException(404, "Professor not found.")
    ids = [g.get("id") for g in (p.get("grants") or []) if isinstance(g, dict) and g.get("id")]
    return {
        "professor": p,
        "papers": store.papers_by_ids((p.get("paper_ids") or [])[:250]),
        "grants": store.grants_by_ids(ids[:250]),
    }


@app.get("/api/admin/status")
def admin_status(x_admin_key: str | None = Header(None)):
    authorize(x_admin_key)
    return {
        "overview": store.overview(),
        "running": pipe.running(),
        "activity": pipe.log_state(),
        "institutions": [
            {"id": i["id"], "name": i.get("name"),
             "state": i.get("pipeline_state"),
             "professors": i.get("n_professors", 0),
             "processed": i.get("n_processed", 0),
             "coverage": i.get("coverage_state", "")}
            for i in store.list_institutions()[:100]
        ],
        "review_open": store.db().staff_review.count_documents({"resolved": False}),
    }


@app.get("/api/admin/review")
def review(x_admin_key: str | None = Header(None)):
    authorize(x_admin_key)
    return {"items": store.staff_review_items(100)}


@app.post("/api/admin/load")
def load(x_admin_key: str | None = Header(None)):
    authorize(x_admin_key)
    year, total, new = pipe.load_ipeds()
    return {"year": year, "universities": total, "added": new}


@app.post("/api/admin/step")
def one_step(x_admin_key: str | None = Header(None)):
    authorize(x_admin_key)
    if pipe.running():
        raise HTTPException(409, "Stop auto-processing before running a manual step.")
    msg = pipe.step()
    if msg:
        pipe.log(msg)
    return {"message": msg or "Nothing pending."}


@app.post("/api/admin/start")
def start(x_admin_key: str | None = Header(None)):
    authorize(x_admin_key)
    pipe.start()
    return {"running": pipe.running()}


@app.post("/api/admin/stop")
def stop(x_admin_key: str | None = Header(None)):
    authorize(x_admin_key)
    pipe.stop()
    return {"running": pipe.running()}
