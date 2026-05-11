"""FastAPI entry point for the MNGR data extraction chatbot."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from models.schemas import (
    ChatRequest,
    ChatResponse,
    ColumnPayload,
    QueryRequest,
    QueryResponse,
    SchemaResponse,
    TablePayload,
)
from services import chat_service, query_service, schema_service
from utils.config import get_settings
from utils.logger import get_logger

log = get_logger(__name__)
settings = get_settings()

app = FastAPI(
    title="MNGR Data Extraction Chatbot",
    version="0.1.0",
    description=(
        "Natural-language interface over an e-commerce and a customer "
        "support SQLite database. Customers are linked across the two "
        "domains by email."
    ),
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    session_cookie="mngr_data_session",
    same_site="lax",
    https_only=False,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Per-session conversation history (in-memory; fine for the single-instance app).
_CONVERSATIONS: dict[str, list[dict[str, Any]]] = {}


def _session_id(request: Request) -> str:
    sid = request.session.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        request.session["sid"] = sid
    return sid


# ---------- Pages -------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def index(request: Request) -> FileResponse:
    _session_id(request)
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


# ---------- Chat --------------------------------------------------------------


@app.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    sid = _session_id(request)
    convo = _CONVERSATIONS.get(sid) or chat_service.new_conversation()
    log.info(
        "/chat: session=%s convo_len=%d msg=%r",
        sid[:8],
        len(convo),
        body.message[:80],
    )
    try:
        result = chat_service.handle_user_message(
            conversation=convo,
            user_message=body.message,
        )
    except RuntimeError as exc:
        log.error("Chat error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    _CONVERSATIONS[sid] = result.conversation
    return ChatResponse(reply=result.reply, queries=result.queries)


@app.post("/chat/reset")
async def chat_reset(request: Request) -> dict[str, bool]:
    sid = _session_id(request)
    _CONVERSATIONS.pop(sid, None)
    return {"ok": True}


# ---------- Direct SQL (handy for API users, tests, and the README demo) -----


@app.post("/query", response_model=QueryResponse)
async def query(body: QueryRequest) -> QueryResponse:
    try:
        result = query_service.run_query(body.sql, max_rows=body.max_rows)
    except query_service.UnsafeQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return QueryResponse(**result.to_dict())


@app.get("/schema", response_model=SchemaResponse)
async def schema_endpoint() -> SchemaResponse:
    tables = [
        TablePayload(
            domain=t.domain,
            name=t.name,
            columns=[ColumnPayload(**c.__dict__) for c in t.columns],
            row_count=t.row_count,
        )
        for t in schema_service.all_tables()
    ]
    return SchemaResponse(tables=tables)


# ---------- Local dev entrypoint ---------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
