"""Pydantic models for API requests and responses."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------- Chat ----------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class QueryEcho(BaseModel):
    """The SQL the LLM ran on this turn (echoed back for transparency)."""

    sql: str
    row_count: int
    truncated: bool


class ChatResponse(BaseModel):
    reply: str
    queries: list[QueryEcho] = Field(default_factory=list)


# ---------- Direct query (for testing / API users who want raw access) ----


class QueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=4000)
    max_rows: int = Field(default=200, ge=1, le=1000)


class QueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


# ---------- Schema introspection ----------


class ColumnPayload(BaseModel):
    name: str
    type: str
    notnull: bool
    pk: bool


class TablePayload(BaseModel):
    domain: str
    name: str
    columns: list[ColumnPayload]
    row_count: int


class SchemaResponse(BaseModel):
    tables: list[TablePayload]
