"""FastAPI entry point for the MNGR data extraction chatbot."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
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


class PersonaRequest(BaseModel):
    """Set the demo 'current user' (a customer email) for row-level scoping."""

    email: EmailStr | None = None


@app.get("/auth/persona")
async def get_persona(request: Request) -> dict[str, str | None]:
    """Return the demo 'current user' email if one is set on this session."""
    return {"email": request.session.get("persona_email")}


@app.post("/auth/persona")
async def set_persona(request: Request, body: PersonaRequest) -> dict[str, str | None]:
    """Set / clear the demo persona.

    Demo only: a real RLS layer would derive the user from an authenticated
    session (OAuth, SAML, etc.) and enforce the filter at the SQL execution
    layer too, not just via the system prompt. See INTERVIEW notes.
    """
    sid = _session_id(request)
    if body.email is None:
        request.session.pop("persona_email", None)
        _CONVERSATIONS.pop(sid, None)
        log.info("/auth/persona: cleared persona for session=%s", sid[:8])
    else:
        request.session["persona_email"] = str(body.email)
        # Force a new conversation so the new persona's RLS prompt takes effect.
        _CONVERSATIONS.pop(sid, None)
        log.info(
            "/auth/persona: set persona=%s for session=%s", body.email, sid[:8]
        )
    return {"email": request.session.get("persona_email")}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    sid = _session_id(request)
    persona_email = request.session.get("persona_email")
    convo = _CONVERSATIONS.get(sid) or chat_service.new_conversation(persona_email)
    log.info(
        "/chat: session=%s convo_len=%d persona=%s msg=%r",
        sid[:8],
        len(convo),
        persona_email or "-",
        body.message[:80],
    )
    try:
        result = chat_service.handle_user_message(
            conversation=convo,
            user_message=body.message,
            current_user_email=persona_email,
        )
    except RuntimeError as exc:
        log.error("Chat error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

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
    except query_service.QueryTooExpensiveError as exc:
        raise HTTPException(status_code=400, detail=f"Query too expensive: {exc}") from exc
    except query_service.UnsafeQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
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
