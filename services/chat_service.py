"""Groq-powered conversational data extraction.

The LLM is given two tools:

- ``execute_sql(query)`` - runs a single read-only SELECT and returns the
  rows. This is the main workhorse.
- ``describe_schema()`` - returns the schema block (also in the system
  prompt). Kept as a tool so the LLM can recover if it forgets.

The system prompt includes the schema up-front so the LLM doesn't need to
roundtrip for it in the common case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC
from typing import Any

from groq import APIError, BadRequestError, Groq

from models.schemas import QueryEcho
from services import query_service, schema_service
from utils.config import get_settings
from utils.logger import get_logger

log = get_logger(__name__)


SYSTEM_PROMPT_TEMPLATE = """You are MNGR's data extraction assistant.

MNGR (mngr.club) is an operating system for networked work for independent
creative professionals. This assistant exposes two business datasets to
authorised users via natural-language queries - an e-commerce database and
a customer support database - joined by customer email.

You answer business questions by:
1. Translating the user's plain English into a single SQLite SELECT
   statement.
2. Calling the `execute_sql` tool with that statement.
3. Reading the rows that come back.
4. Writing a clear, natural-language answer for the user. Cite specific
   numbers, names, and dates from the data. Never invent values.

Schemas (the only tables you may query):

{schema_block}

Rules:
- ALWAYS qualify tables with their database alias: `ecommerce.customers`,
  `support.tickets`, etc. Never write bare `customers` - there are two
  customers tables.
- For cross-domain questions, JOIN on email
  (`ecommerce.customers.email = support.customers.email`). The same
  person can have different `id` values in each database.
- SQLite stores dates as ISO strings (e.g. `2026-05-05`). Use
  `date('now', '-30 days')` to express "in the last month".
- Only emit SELECT statements (or `WITH ... SELECT`). The system will
  refuse anything else.
- One statement per tool call. Combine multiple intents using JOIN, UNION
  ALL, or CTEs rather than multiple calls.
- If the result is empty, say so plainly and offer one specific suggestion
  (e.g. broadening the date range).
- If the user's question is ambiguous (e.g. "show me orders" - for whom?
  what date range?), ask ONE clarifying question rather than guessing.
- When listing many rows, summarise sensibly (count, total, top 5) instead
  of dumping a wall of text. The UI also shows the raw SQL you ran in a
  collapsible panel, so the user can audit your work.
- Be concise. Two to four sentences per answer is ideal.

{rls_block}
Today's date is provided in each user message for relative-time queries.
"""


_RLS_PROMPT = """ROW-LEVEL ACCESS CONTROL (in effect for this session):
- You are answering on behalf of the customer with email: **{email}**.
- Every query that touches `ecommerce.customers`, `ecommerce.orders`,
  `support.customers`, or `support.tickets` MUST be constrained to that
  email. Use either:
    WHERE ecommerce.customers.email = '{email}'
  or the equivalent join filter on the support side.
- NEVER return rows belonging to any other customer.
- If the user asks "show me all customers" or any aggregate that would
  reveal other users' data, refuse politely and explain you are scoped
  to their own account.

"""


def build_system_prompt(current_user_email: str | None = None) -> str:
    schema = schema_service.schema_summary_for_prompt()
    rls = _RLS_PROMPT.format(email=current_user_email) if current_user_email else ""
    return SYSTEM_PROMPT_TEMPLATE.format(schema_block=schema, rls_block=rls)


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": (
                "Run a single read-only SQLite SELECT statement against the "
                "ecommerce + support databases. Returns the rows (capped at "
                "200) and the column list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "A single SQLite SELECT (or WITH ... SELECT). "
                            "Always qualify tables with their database "
                            "alias: `ecommerce.<table>` or `support.<table>`."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_schema",
            "description": (
                "Return the schema of both databases. The schema is also in "
                "the system prompt - only call this if you need a refresher."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


# ---------- Conversation state -----------------------------------------------


@dataclass
class ChatTurnResult:
    reply: str
    conversation: list[dict[str, Any]]
    queries: list[QueryEcho] = field(default_factory=list)


def new_conversation(current_user_email: str | None = None) -> list[dict[str, Any]]:
    return [{"role": "system", "content": build_system_prompt(current_user_email)}]


# ---------- Tool execution ----------------------------------------------------


def _execute_tool_call(
    name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], QueryEcho | None]:
    """Run a tool. Returns (json-serialisable result, query-echo-or-None)."""
    if name == "execute_sql":
        sql = (arguments.get("query") or "").strip()
        if not sql:
            return ({"error": "missing_query", "message": "No SQL provided."}, None)
        try:
            result = query_service.run_query(sql)
        except query_service.QueryTooExpensiveError as exc:
            log.warning("execute_sql cost-guarded: %s", exc)
            return (
                {
                    "error": "query_too_expensive",
                    "message": str(exc),
                    "hint": (
                        "The query would do a very wide scan or cartesian "
                        "product. Narrow it with a WHERE filter or add an "
                        "explicit JOIN condition between the tables, then "
                        "try again."
                    ),
                },
                None,
            )
        except query_service.UnsafeQueryError as exc:
            log.warning("execute_sql rejected: %s", exc)
            return (
                {
                    "error": "invalid_sql",
                    "message": str(exc),
                    "hint": (
                        "Only single SELECT statements are allowed. Rewrite "
                        "your query as a single SELECT, qualifying tables "
                        "with their database alias."
                    ),
                },
                None,
            )
        echo = QueryEcho(
            sql=sql,
            row_count=result.row_count,
            truncated=result.truncated,
            columns=result.columns,
            rows=result.rows,
        )
        return (result.to_dict(), echo)

    if name == "describe_schema":
        return ({"schema": schema_service.schema_summary_for_prompt()}, None)

    return ({"error": "unknown_tool", "message": f"Unknown tool '{name}'."}, None)


# ---------- LLM driver --------------------------------------------------------


def _client() -> Groq:
    s = get_settings()
    if not s.groq_api_key:
        raise RuntimeError("GROQ_API_KEY is not set. See .env.example.")
    return Groq(api_key=s.groq_api_key)


def handle_user_message(
    conversation: list[dict[str, Any]],
    user_message: str,
    max_tool_iterations: int = 4,
    current_user_email: str | None = None,
) -> ChatTurnResult:
    """Drive one user turn through Groq, executing any tool calls it makes.

    If ``current_user_email`` is given, the system prompt is configured to
    constrain the LLM to that user's rows. This is a demo of how a real
    row-level access-control layer would slot in - in production you would
    *also* enforce the filter post-LLM (e.g. wrap the generated SQL in a
    CTE that filters by email), but the prompt-level enforcement here is
    a useful starting point.
    """
    from datetime import datetime

    s = get_settings()
    client = _client()

    conversation = (
        list(conversation) if conversation else new_conversation(current_user_email)
    )
    today_hint = (
        f"(Today is {datetime.now(UTC).strftime('%A %d %B %Y')} (UTC). "
        "Use this for relative-time queries like 'in the last month'.)"
    )
    conversation.append(
        {"role": "user", "content": f"{user_message}\n\n{today_hint}"}
    )

    queries: list[QueryEcho] = []
    tool_validation_retries_left = 1

    for _ in range(max_tool_iterations):
        try:
            resp = client.chat.completions.create(
                model=s.groq_model,
                messages=conversation,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.2,
                max_tokens=1200,
            )
        except BadRequestError as exc:
            err_msg = str(exc)
            log.warning("Groq tool-use validation failed: %s", err_msg)
            if tool_validation_retries_left > 0 and "tool_use_failed" in err_msg:
                tool_validation_retries_left -= 1
                conversation.append(
                    {
                        "role": "system",
                        "content": (
                            "Your previous tool call had invalid arguments: "
                            + err_msg + "\nFix the arguments and try again."
                        ),
                    }
                )
                continue
            log.exception("Groq API error (non-recoverable)")
            reply = (
                "I tried to look that up but my tool call was malformed. "
                "Could you rephrase the request?"
            )
            conversation.append({"role": "assistant", "content": reply})
            return ChatTurnResult(reply=reply, conversation=conversation, queries=queries)
        except APIError:
            log.exception("Groq API error")
            reply = (
                "I'm having trouble reaching my language backend right now. "
                "Please try again in a moment."
            )
            conversation.append({"role": "assistant", "content": reply})
            return ChatTurnResult(reply=reply, conversation=conversation, queries=queries)

        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            reply = msg.content or "(no response)"
            log.info("LLM responded with no tool calls. reply preview: %s", reply[:140])
            conversation.append({"role": "assistant", "content": reply})
            return ChatTurnResult(reply=reply, conversation=conversation, queries=queries)

        log.info(
            "LLM called tools: %s",
            [tc.function.name for tc in tool_calls],
        )
        conversation.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result, echo = _execute_tool_call(tc.function.name, args)
            if echo:
                queries.append(echo)
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": tc.function.name,
                    "content": json.dumps(result, default=str),
                }
            )

    fallback = (
        "Sorry, I got stuck looking that up. Could you rephrase your question?"
    )
    conversation.append({"role": "assistant", "content": fallback})
    return ChatTurnResult(reply=fallback, conversation=conversation, queries=queries)
