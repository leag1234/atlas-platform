"""Read finished answers from the staging Open WebUI database (read-only).

Open WebUI 0.13 keeps one row per message in `chat_message` (id = "<chat id>-<message
id>", the message id being the one the browser shows as `#message-<id>`), with the
parent link, the content, the attached files and `done`. That table is the reliable
source for the request, its conversation, the answer and the message id; the Atlas turn
log has no chat or message id and is only matched for metadata (task type, model).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REASONING_BLOCK = re.compile(
    r"<details\b[^>]*\btype=\"(?:reasoning|tool_calls|code_interpreter)\"[^>]*>.*?</details>",
    re.S,
)
MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
CHAT_ID = re.compile(r"^[A-Za-z0-9:_-]{1,120}$")


@dataclass
class Turn:
    role: str
    text: str
    files: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Answer:
    message_id: str
    chat_id: str
    user_id: str
    created_at: float
    updated_at: float
    model_id: str
    text: str
    history: list[Turn]
    started_at: float | None = None
    ended_at: float | None = None
    output_tokens: int | None = None

    @property
    def user_hash(self) -> str:
        return hashlib.sha256(
            ("atlas-live-judge:" + self.user_id).encode()
        ).hexdigest()[:12]

    @property
    def request(self) -> Turn | None:
        for turn in reversed(self.history):
            if turn.role == "user":
                return turn
        return None


def connect_ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    return db


def _json(value: Any) -> Any:
    if value is None or not isinstance(value, str | bytes):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def content_text(value: Any) -> str:
    """The visible text of a stored message content (string or multimodal parts)."""
    value = _json(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return "" if value is None else str(value)


def visible_answer(text: str) -> str:
    """Drop the collapsed reasoning and tool blocks (the person sees them folded)."""
    return REASONING_BLOCK.sub("", text).strip()


def message_id_of(row_id: str, chat_id: str) -> str:
    prefix = chat_id + "-"
    return row_id[len(prefix) :] if row_id.startswith(prefix) else row_id


def _files(value: Any) -> list[dict[str, Any]]:
    value = _json(value)
    return [f for f in value if isinstance(f, dict)] if isinstance(value, list) else []


def history(
    db: sqlite3.Connection, chat_id: str, parent_id: str | None, limit: int = 40
) -> list[Turn]:
    """The branch that led to an answer, oldest first (at most `limit` messages)."""
    turns: list[Turn] = []
    seen: set[str] = set()
    current = parent_id
    while current and len(turns) < limit and current not in seen:
        seen.add(current)
        row = db.execute(
            "SELECT role, content, files, parent_id FROM chat_message WHERE id = ?",
            (f"{chat_id}-{current}",),
        ).fetchone()
        if row is None:
            break
        text = content_text(row["content"])
        if row["role"] == "assistant":
            text = visible_answer(text)
        turns.append(Turn(str(row["role"]), text, _files(row["files"])))
        current = row["parent_id"]
    turns.reverse()
    return turns


def _answer(db: sqlite3.Connection, row: sqlite3.Row) -> Answer:
    chat_id = str(row["chat_id"])
    meta = _json(row["meta"]) or {}
    usage = _json(row["usage"]) or {}
    turns = history(db, chat_id, row["parent_id"])
    return Answer(
        message_id=message_id_of(str(row["id"]), chat_id),
        chat_id=chat_id,
        user_id=str(row["owner"] or row["user_id"] or ""),
        created_at=float(row["created_at"] or 0),
        updated_at=float(row["updated_at"] or 0),
        model_id=str(row["model_id"] or ""),
        text=visible_answer(content_text(row["content"])),
        history=turns,
        started_at=meta.get("response_started_at") if isinstance(meta, dict) else None,
        ended_at=meta.get("response_ended_at") if isinstance(meta, dict) else None,
        output_tokens=usage.get("output_tokens") if isinstance(usage, dict) else None,
    )


SELECT = (
    "SELECT m.id, m.chat_id, m.user_id, m.parent_id, m.content, m.model_id, m.meta, "
    "m.usage, m.created_at, m.updated_at, c.user_id AS owner "
    "FROM chat_message m JOIN chat c ON c.id = m.chat_id "
)


def finished_since(
    db: sqlite3.Connection, since: float, until: float, limit: int = 200
) -> list[Answer]:
    """Finished assistant answers whose row changed in [since, until), oldest first."""
    rows = db.execute(
        SELECT + "WHERE m.role = 'assistant' AND m.done = 1 AND m.updated_at >= ? "
        "AND m.updated_at < ? ORDER BY m.updated_at LIMIT ?",
        (int(since), int(until), limit),
    ).fetchall()
    return [_answer(db, row) for row in rows]


def one(db: sqlite3.Connection, chat_id: str, message_id: str) -> Answer | None:
    row = db.execute(
        SELECT + "WHERE m.id = ? AND m.role = 'assistant'",
        (f"{chat_id}-{message_id}",),
    ).fetchone()
    return _answer(db, row) if row else None


def lookup(
    db: sqlite3.Connection, chat_id: str, message_ids: list[str]
) -> dict[str, tuple[str, bool, float]]:
    """message id -> (owner user id, done, created_at) for the assistant messages of a chat."""
    out: dict[str, tuple[str, bool, float]] = {}
    for mid in message_ids:
        row = db.execute(
            "SELECT m.done, m.created_at, c.user_id AS owner FROM chat_message m "
            "JOIN chat c ON c.id = m.chat_id WHERE m.id = ? AND m.role = 'assistant'",
            (f"{chat_id}-{mid}",),
        ).fetchone()
        if row is not None:
            out[mid] = (
                str(row["owner"] or ""),
                bool(row["done"]),
                float(row["created_at"] or 0),
            )
    return out


def file_record(db: sqlite3.Connection, file_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT id, filename, path, meta, data FROM file WHERE id = ?", (file_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "filename": row["filename"] or "",
        "path": row["path"] or "",
        "meta": _json(row["meta"]) or {},
        "data": _json(row["data"]) or {},
    }


def privacy_level(path: Path, user: str, chat: str, minimum: str = "") -> str:
    """The conversation's privacy level (standard, enhanced, strict), never lower than the minimum."""
    rank = {"standard": 0, "enhanced": 1, "strict": 2}
    level = "standard"
    if path.exists():
        db = connect_ro(path)
        try:
            row = db.execute(
                "SELECT level FROM conversation_privacy WHERE user = ? AND chat = ?",
                (user, chat),
            ).fetchone()
        finally:
            db.close()
        if row is not None and row["level"] in rank:
            level = str(row["level"])
    floor = minimum.strip().lower()
    if floor in rank and rank[floor] > rank[level]:
        level = floor
    return level


def turn_log_match(log_dir: Path, answer: Answer) -> dict[str, Any] | None:
    """Best-effort metadata of the Atlas turn behind an answer (no chat id in the log).

    Matched on the answer end time (60 s window) and, when known, the output tokens.
    """
    end = answer.ended_at or answer.updated_at
    day = dt.datetime.fromtimestamp(end, dt.UTC).strftime("%Y-%m-%d")
    path = log_dir / f"{day}.jsonl"
    if not path.exists():
        return None
    best: tuple[float, dict[str, Any]] | None = None
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
                stamp = dt.datetime.fromisoformat(rec["timestamp"]).timestamp()
            except (ValueError, KeyError, TypeError):
                continue
            if rec.get("task_type") == "title":
                continue
            gap = abs(stamp - end)
            if gap > 60:
                continue
            tokens = (rec.get("tokens") or {}).get("out")
            if (
                answer.output_tokens
                and tokens
                and int(tokens) != int(answer.output_tokens)
            ):
                continue
            if best is None or gap < best[0]:
                best = (gap, rec)
    if best is None:
        return None
    rec = best[1]
    return {
        "task_type": rec.get("task_type") or "",
        "model": rec.get("modele_utilise") or "",
        "turn_cost_eur": rec.get("cout_eur"),
        "latency_s": round(
            float((rec.get("latence_ms") or {}).get("total") or 0) / 1000, 1
        ),
    }
