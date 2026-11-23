"""Configuration, one-answer judging and the polling loop."""

from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from packages.ports import AtlasPorts, port_base
from tools.eval.frontier_judge.client import OpenAIHttp
from tools.eval.frontier_judge.ledger import BudgetExceeded, Ledger

from . import evidence, guard, judge, source, store

LOG = logging.getLogger("live_judge")
FRONTIER_HOME = Path("/opt/atlas-src/eval/frontier-judge")
DEFAULT_KEY_FILE = Path("/opt/atlas-src/secrets.env")
PORT_OFFSET = 8041
SLOW_SECONDS = 240
MAX_ATTEMPTS = 3
SETTLE_SECONDS = 5


@dataclass
class Config:
    identity: dict[str, str]
    paths: evidence.Paths
    log_dir: Path
    webui_db: Path
    privacy_db: Path
    privacy_minimum: str
    store_path: Path
    summary_dir: Path
    ledger_path: Path
    chat_ui_url: str
    port: int
    daily_cap_eur: float
    month_cap_eur: float
    usd_to_eur: float
    bulk_model: str
    arbiter_model: str
    second_below: int
    second_share: float
    workers: int
    poll_seconds: float

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        identity = guard.check(env)
        base = port_base(identity["ATLAS_PORT_BASE"])
        data_dir = Path(identity["ATLAS_DATA_DIR"])
        webui = Path(
            env.get("ATLAS_LIVE_JUDGE_WEBUI_DATA")
            or f"/var/lib/docker/volumes/atlas-chat-ui-{base}/_data"
        )
        table = judge.models()
        quality = Path(env.get("ATLAS_LIVE_JUDGE_DIR") or data_dir / "quality")
        return cls(
            identity=identity,
            paths=evidence.Paths(
                data_dir=data_dir,
                data_root=Path(env.get("ATLAS_DATA_ROOT") or data_dir / "project-data"),
                image_dir=Path(env.get("ATLAS_IMAGE_DIR") or data_dir / "images"),
                webui_data=webui,
            ),
            log_dir=Path(identity["ATLAS_INTERACTION_DIR"]),
            webui_db=webui / "webui.db",
            privacy_db=data_dir / "conversation-privacy.sqlite",
            privacy_minimum=env.get("ATLAS_PRIVACY_MINIMUM", ""),
            store_path=quality / "scores.sqlite",
            summary_dir=quality / "summary",
            ledger_path=Path(
                env.get("ATLAS_LIVE_JUDGE_LEDGER") or FRONTIER_HOME / "ledger.jsonl"
            ),
            chat_ui_url=AtlasPorts(base).url("chat_ui"),
            port=int(env.get("ATLAS_LIVE_JUDGE_PORT") or PORT_OFFSET + base),
            daily_cap_eur=float(env.get("ATLAS_LIVE_JUDGE_DAILY_EUR") or 5.0),
            month_cap_eur=float(table["caps_eur"]["month"]),
            usd_to_eur=float(table["usd_to_eur"]),
            bulk_model=str(table["roles"]["bulk"]),
            arbiter_model=str(table["roles"]["arbiter"]),
            second_below=int(env.get("ATLAS_LIVE_JUDGE_SECOND_BELOW") or 5),
            second_share=float(env.get("ATLAS_LIVE_JUDGE_SECOND_SHARE") or 0.6),
            workers=int(env.get("ATLAS_LIVE_JUDGE_WORKERS") or 3),
            poll_seconds=float(env.get("ATLAS_LIVE_JUDGE_POLL") or 10),
        )


def load_key(path: Path) -> str:
    """OPENAI_API_KEY from the secrets file; no other secret is read or exported."""
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        if line.startswith("OPENAI_API_KEY="):
            value = line.split("=", 1)[1].strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value
    return ""


_version: tuple[float, str] = (0.0, "")


def version_tag(repo: Path = guard.REPO) -> str:
    global _version
    if time.time() - _version[0] < 60 and _version[1]:
        return _version[1]
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = "unknown"
    _version = (time.time(), out or "unknown")
    return _version[1]


def _day() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%d")


class Engine:
    def __init__(
        self,
        cfg: Config,
        http: OpenAIHttp | None,
        scores: store.Store,
        run_id: Callable[[], str] | None = None,
        run_cap_eur: float | None = None,
    ) -> None:
        self.cfg = cfg
        self.http = http
        self.store = scores
        self.run_id = run_id or (lambda: f"live-judge-{_day()}")
        self.run_cap_eur = cfg.daily_cap_eur if run_cap_eur is None else run_cap_eur
        self._inflight: set[str] = set()
        self._lock = threading.Lock()

    def ledger(self) -> Ledger:
        return Ledger(
            self.cfg.ledger_path,
            self.run_id(),
            self.cfg.month_cap_eur,
            self.run_cap_eur,
            self.cfg.usd_to_eur,
        )

    def base_record(self, answer: source.Answer) -> dict[str, Any]:
        end = answer.ended_at or answer.updated_at
        meta = source.turn_log_match(self.cfg.log_dir, answer) or {}
        return {
            "message_id": answer.message_id,
            "chat_id": answer.chat_id,
            "user_hash": answer.user_hash,
            "version": version_tag(),
            "answered_at": float(end),
            "task_type": str(meta.get("task_type") or ""),
            "latency_s": round(float(end) - answer.created_at, 1)
            if answer.created_at
            else None,
        }

    def judge_one(self, answer: source.Answer) -> dict[str, Any]:
        """Grade one answer and store the result; returns the stored record."""
        previous = self.store.get(answer.message_id) or {}
        record = self.base_record(answer)
        record["attempts"] = int(previous.get("attempts") or 0)
        level = source.privacy_level(
            self.cfg.privacy_db,
            answer.user_id,
            answer.chat_id,
            self.cfg.privacy_minimum,
        )
        if level != "standard":
            # Enhanced and strict conversations never leave the owned or pseudonymised path.
            record.update(state="skipped_privacy", scored_at=time.time(), verdict=level)
            self.store.put(record)
            return record
        slow = ["C19"] if (record["latency_s"] or 0) > SLOW_SECONDS else []
        db = source.connect_ro(self.cfg.webui_db)
        try:
            ev = evidence.build(answer, db, self.cfg.paths)
        finally:
            db.close()
        record["artifacts"] = ev.artifacts
        if not answer.text.strip() and not ev.artifacts:
            record.update(
                state="scored",
                scored_at=time.time(),
                score=0,
                verdict="Empty answer.",
                classes=["C5", *slow],
                model="code",
            )
            self.store.put(record)
            return record
        if self.http is None:
            raise RuntimeError("no API client")
        record.update(state="pending")
        self.store.put(record)
        ledger = self.ledger()
        key = f"{answer.message_id}:{record['attempts']}"
        try:
            first = judge.grade(
                self.http,
                ledger,
                judge.body(ev, self.cfg.bulk_model, record["answered_at"]),
                key,
            )
        except BudgetExceeded as exc:
            record.update(state="budget", error=str(exc)[:300])
            self.store.put(record)
            return record
        record["attempts"] += 1
        record["cost_eur"] = (
            float(previous.get("cost_eur") or 0) + first.usd * self.cfg.usd_to_eur
        )
        if first.score is None:
            record.update(
                state="failed" if record["attempts"] >= MAX_ATTEMPTS else "pending",
                error=first.error,
            )
            self.store.put(record)
            return record
        final = first
        if first.score < self.cfg.second_below and self._second_allowed(ledger):
            try:
                second = judge.grade(
                    self.http,
                    ledger,
                    judge.body(ev, self.cfg.arbiter_model, record["answered_at"]),
                    key + ":arbiter",
                )
                record["cost_eur"] += second.usd * self.cfg.usd_to_eur
                if second.score is not None:
                    final = second
            except BudgetExceeded:
                pass
        record.update(
            state="scored",
            scored_at=time.time(),
            score=final.score,
            verdict=final.verdict,
            classes=sorted(set(final.classes) | set(slow), key=lambda c: int(c[1:])),
            worst=final.worst,
            language=final.language,
            model=final.model,
            served_model=final.served_model,
            first_score=first.score if final is not first else None,
            first_model=first.model if final is not first else "",
            error="",
        )
        self.store.put(record)
        return record

    def _second_allowed(self, ledger: Ledger) -> bool:
        _month, run = ledger.totals()
        return run < self.cfg.second_share * self.run_cap_eur

    def submit(self, pool: ThreadPoolExecutor, answer: source.Answer) -> bool:
        with self._lock:
            if answer.message_id in self._inflight:
                return False
            self._inflight.add(answer.message_id)

        def work() -> None:
            try:
                self.judge_one(answer)
            except Exception:  # noqa: BLE001 - one answer never stops the loop
                LOG.exception("judging %s failed", answer.message_id)
            finally:
                with self._lock:
                    self._inflight.discard(answer.message_id)

        pool.submit(work)
        return True

    def due(self, answer: source.Answer, epoch: float) -> bool:
        if answer.created_at < epoch:
            return False
        known = self.store.get(answer.message_id)
        if known is None:
            return True
        return (
            known["state"] == "pending" and int(known["attempts"] or 0) < MAX_ATTEMPTS
        )

    def poll_once(self, pool: ThreadPoolExecutor) -> int:
        epoch = float(self.store.get_meta("judge_epoch") or time.time())
        watermark = float(self.store.get_meta("watermark") or epoch)
        now = time.time()
        db = source.connect_ro(self.cfg.webui_db)
        try:
            answers = source.finished_since(db, watermark - 120, now - SETTLE_SECONDS)
        finally:
            db.close()
        started = 0
        for answer in answers:
            if self.due(answer, epoch) and self.submit(pool, answer):
                started += 1
        if answers:
            self.store.set_meta("watermark", str(max(a.updated_at for a in answers)))
        return started

    def resume_pending(self, pool: ThreadPoolExecutor) -> int:
        """Answers left pending by a stop (marked before their call) are judged again."""
        started = 0
        db = source.connect_ro(self.cfg.webui_db)
        try:
            for row in self.store.rows(state="pending"):
                if int(row["attempts"] or 0) >= MAX_ATTEMPTS:
                    continue
                answer = source.one(db, row["chat_id"], row["message_id"])
                if answer is not None and self.submit(pool, answer):
                    started += 1
        finally:
            db.close()
        return started

    def loop(self, stop: threading.Event) -> None:
        if not self.store.get_meta("judge_epoch"):
            self.store.set_meta("judge_epoch", str(time.time()))
        last_summary = 0.0
        with ThreadPoolExecutor(max_workers=self.cfg.workers) as pool:
            self.resume_pending(pool)
            while not stop.is_set():
                try:
                    self.poll_once(pool)
                except Exception:  # noqa: BLE001 - a locked or restarting database is retried
                    LOG.exception("poll failed")
                if time.time() - last_summary > 600:
                    try:
                        store.write_summaries(self.store, self.cfg.summary_dir)
                    except Exception:  # noqa: BLE001
                        LOG.exception("summary failed")
                    last_summary = time.time()
                stop.wait(self.cfg.poll_seconds)
