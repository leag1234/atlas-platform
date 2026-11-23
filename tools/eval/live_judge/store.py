"""Private score store (SQLite, mode 600) and the summaries drawn from it.

Never in git: it lives in the instance data directory (default
<ATLAS_DATA_DIR>/quality/scores.sqlite).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import statistics
import threading
from collections import Counter
from pathlib import Path
from typing import Any

FINAL = ("scored", "skipped_privacy", "skipped_empty", "failed")
STATES = (*FINAL, "pending", "budget")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
  message_id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL,
  user_hash TEXT NOT NULL,
  version TEXT NOT NULL,
  answered_at REAL NOT NULL,
  scored_at REAL,
  state TEXT NOT NULL,
  score INTEGER,
  verdict TEXT NOT NULL DEFAULT '',
  classes TEXT NOT NULL DEFAULT '[]',
  worst TEXT NOT NULL DEFAULT '',
  language TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '',
  served_model TEXT NOT NULL DEFAULT '',
  first_score INTEGER,
  first_model TEXT NOT NULL DEFAULT '',
  cost_eur REAL NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT NOT NULL DEFAULT '',
  task_type TEXT NOT NULL DEFAULT '',
  automated INTEGER NOT NULL DEFAULT 0,
  artifacts INTEGER NOT NULL DEFAULT 0,
  latency_s REAL
);
CREATE INDEX IF NOT EXISTS scores_answered ON scores(answered_at);
CREATE INDEX IF NOT EXISTS scores_version ON scores(version);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self.db.execute(
                "SELECT value FROM meta WHERE key = ?", (key,)
            ).fetchone()
        return str(row["value"]) if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO meta VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self.db.commit()

    def get(self, message_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM scores WHERE message_id = ?", (message_id,)
            ).fetchone()
        return dict(row) if row else None

    def many(self, message_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not message_ids:
            return {}
        marks = ",".join("?" * len(message_ids))
        with self._lock:
            rows = self.db.execute(
                f"SELECT * FROM scores WHERE message_id IN ({marks})", message_ids
            ).fetchall()
        return {str(r["message_id"]): dict(r) for r in rows}

    def put(self, record: dict[str, Any]) -> None:
        record = dict(record)
        if isinstance(record.get("classes"), list):
            record["classes"] = json.dumps(record["classes"])
        cols = list(record)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "message_id")
        with self._lock:
            self.db.execute(
                f"INSERT INTO scores ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
                f"ON CONFLICT(message_id) DO UPDATE SET {updates}",
                [record[c] for c in cols],
            )
            self.db.commit()

    def rows(
        self,
        since: float = 0.0,
        until: float | None = None,
        version: str | None = None,
        state: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM scores WHERE answered_at >= ?"
        args: list[Any] = [since]
        if until is not None:
            sql += " AND answered_at < ?"
            args.append(until)
        if version:
            sql += " AND version = ?"
            args.append(version)
        if state:
            sql += " AND state = ?"
            args.append(state)
        with self._lock:
            return [
                dict(r) for r in self.db.execute(sql + " ORDER BY answered_at", args)
            ]

    def spent_eur(self, since: float) -> float:
        with self._lock:
            row = self.db.execute(
                "SELECT COALESCE(SUM(cost_eur), 0) AS s FROM scores WHERE scored_at >= ?",
                (since,),
            ).fetchone()
        return float(row["s"])


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r["state"] == "scored" and r["score"] is not None]
    scores = [int(r["score"]) for r in scored]
    by_class: Counter[str] = Counter()
    for r in scored:
        by_class.update(json.loads(r["classes"] or "[]"))
    by_task: dict[str, list[int]] = {}
    for r in scored:
        by_task.setdefault(r["task_type"] or "unknown", []).append(int(r["score"]))
    return {
        "answers": len(rows),
        "scored": len(scores),
        "states": dict(Counter(r["state"] for r in rows)),
        "mean": round(statistics.fmean(scores), 2) if scores else None,
        "median": statistics.median(scores) if scores else None,
        "distribution": {str(i): scores.count(i) for i in range(11)},
        "share_below_5": round(sum(s < 5 for s in scores) / len(scores), 3)
        if scores
        else None,
        "by_class": dict(sorted(by_class.items(), key=lambda kv: int(kv[0][1:]))),
        "by_task_type": {
            k: {"n": len(v), "mean": round(statistics.fmean(v), 2)}
            for k, v in sorted(by_task.items())
        },
        "by_account": {
            kind: {
                "n": len(v),
                "mean": round(statistics.fmean(v), 2) if v else None,
            }
            for kind, v in (
                ("human", [int(r["score"]) for r in scored if not r["automated"]]),
                ("automated", [int(r["score"]) for r in scored if r["automated"]]),
            )
        },
        "cost_eur": round(sum(float(r["cost_eur"] or 0) for r in rows), 4),
    }


def write_summaries(
    store: Store, folder: Path, day: dt.date | None = None
) -> list[Path]:
    """Daily file for `day` (UTC, default today) and one file per version seen that day."""
    day = day or dt.datetime.now(dt.UTC).date()
    start = dt.datetime.combine(day, dt.time(), dt.UTC).timestamp()
    rows = store.rows(start, start + 86400)
    written: list[Path] = []
    for sub in ("by-day", "by-version"):
        (folder / sub).mkdir(parents=True, exist_ok=True, mode=0o700)
    out = folder / "by-day" / f"{day.isoformat()}.json"
    data = {
        "day": day.isoformat(),
        **summarize(rows),
        "versions": sorted({r["version"] for r in rows}),
    }
    _write(out, data)
    written.append(out)
    for version in sorted({r["version"] for r in rows}):
        vrows = store.rows(version=version)
        vout = folder / "by-version" / f"{_safe(version)}.json"
        first = min(r["answered_at"] for r in vrows)
        last = max(r["answered_at"] for r in vrows)
        _write(
            vout,
            {
                "version": version,
                "first_answer": dt.datetime.fromtimestamp(first, dt.UTC).isoformat(),
                "last_answer": dt.datetime.fromtimestamp(last, dt.UTC).isoformat(),
                **summarize(vrows),
            },
        )
        written.append(vout)
    return written


def _safe(name: str) -> str:
    return (
        "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:100]
        or "unknown"
    )


def _write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
