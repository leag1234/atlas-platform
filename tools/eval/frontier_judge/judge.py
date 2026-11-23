"""Build judge requests, run them (Batch or standard), parse and verify the verdicts."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import Item, guard
from .client import ApiError, OpenAIHttp, output_text, usage_of
from .ledger import BudgetExceeded, Ledger, price_for

HERE = Path(__file__).resolve().parent
MAX_EVIDENCE_CHARS = 150_000
TRUNCATION_MARK = "[evidence truncated by the judge harness"

INSTRUCTIONS = """You are an evaluation judge for Atlas, an AI assistant. You grade ONE defect class at a time.
Rules:
- Use only the evidence given in this message. Never use outside knowledge to fill a gap.
- If the evidence does not let you decide this class, the verdict is "unknown". Never guess.
- Every quote must be copied verbatim from the evidence text (not paraphrased); name the evidence label it comes from.
  For something seen only in an image, use source "image: <label>" and describe what is visible.
- "pass" means the invariant holds for everything in scope; "fail" means at least one clear violation.
- Judge the defect class only. Do not fail an item for problems that belong to another class.
- confidence is your probability (0 to 1) that your verdict is right."""

VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "evidence", "reason", "confidence"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "fail", "unknown"]},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["source", "quote"],
                "properties": {
                    "source": {"type": "string"},
                    "quote": {"type": "string"},
                },
            },
        },
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
}

PAIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["winner", "evidence", "reason", "confidence"],
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "tie", "unknown"]},
        "evidence": VERDICT_SCHEMA["properties"]["evidence"],
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
}


def load_json(name: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((HERE / name).read_text(encoding="utf-8"))
    return value


def rubrics() -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in load_json("rubrics.json")["classes"]}


def missing_evidence(item: Item, rubric: dict[str, Any]) -> list[list[str]]:
    kinds = item.kinds()
    return [
        group for group in rubric["required_evidence"] if not kinds.intersection(group)
    ]


def _rubric_text(rubric: dict[str, Any]) -> str:
    lines = [
        f"Defect class {rubric['id']}: {rubric['name']}",
        f"Invariant: {rubric['invariant']}",
        f"Question: {rubric['question']}",
        "Pass examples (fictional):",
        *[f"- {x}" for x in rubric["pass_examples"]],
        "Fail examples (fictional):",
        *[f"- {x}" for x in rubric["fail_examples"]],
    ]
    return "\n".join(lines)


def _image_part(path: str) -> dict[str, Any]:
    data = guard(path).read_bytes()
    return {
        "type": "input_image",
        "image_url": "data:image/png;base64," + base64.b64encode(data).decode(),
        "detail": "auto",
    }


def evidence_parts(item: Item, kinds: set[str], tag: str = "") -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for ev in item.evidence:
        if ev.kind not in kinds:
            continue
        label = f"{tag}{ev.kind}: {ev.label}"
        if ev.text is not None:
            text = ev.text
            if len(text) > MAX_EVIDENCE_CHARS:
                text = (
                    text[:MAX_EVIDENCE_CHARS]
                    + f"\n[evidence truncated by the judge harness at {MAX_EVIDENCE_CHARS} characters]"
                )
            parts.append(
                {"type": "input_text", "text": f"### Evidence [{label}]\n{text}"}
            )
        if ev.image is not None:
            parts.append(
                {
                    "type": "input_text",
                    "text": f"### Evidence [{label}] (image follows)",
                }
            )
            parts.append(_image_part(ev.image))
    return parts


def used_kinds(rubric: dict[str, Any]) -> set[str]:
    kinds = {k for group in rubric["required_evidence"] for k in group}
    return kinds | set(rubric.get("optional_evidence", []))


def verdict_body(
    item: Item, rubric: dict[str, Any], model: str, max_output: int
) -> dict[str, Any]:
    head = _rubric_text(rubric)
    if item.notes:
        head += "\nNotes about the evidence:\n" + "\n".join(
            f"- {n}" for n in item.notes
        )
    parts = [
        {"type": "input_text", "text": head},
        *evidence_parts(item, used_kinds(rubric)),
    ]
    return {
        "model": model,
        "instructions": INSTRUCTIONS,
        "input": [{"role": "user", "content": parts}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "verdict",
                "schema": VERDICT_SCHEMA,
                "strict": True,
            }
        },
        "max_output_tokens": max_output,
        "store": False,
    }


def pair_body(
    a: Item, b: Item, rubric: dict[str, Any], question: str, model: str, max_output: int
) -> dict[str, Any]:
    """A and B answer the same request; each keeps its own context (run date, locale)."""
    kinds = used_kinds(rubric)
    head = f"{question}\n\n{_rubric_text(rubric)}"
    notes = sorted(set(a.notes) | set(b.notes))
    if notes:
        head += "\nNotes about the evidence:\n" + "\n".join(f"- {n}" for n in notes)
    answer_kinds = (kinds | {"context"}) - {"request"}
    parts = [
        {"type": "input_text", "text": head},
        *evidence_parts(a, {"request"}),
        {"type": "input_text", "text": "=== Answer A (with its own test context) ==="},
        *evidence_parts(a, answer_kinds, "A "),
        {"type": "input_text", "text": "=== Answer B (with its own test context) ==="},
        *evidence_parts(b, answer_kinds, "B "),
    ]
    return {
        "model": model,
        "instructions": INSTRUCTIONS,
        "input": [{"role": "user", "content": parts}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "pairwise",
                "schema": PAIR_SCHEMA,
                "strict": True,
            }
        },
        "max_output_tokens": max_output,
        "store": False,
    }


@dataclass
class Task:
    task_id: str
    item_id: str
    cls: str
    body: dict[str, Any]
    pair: tuple[str, str] | None = None
    source: str = ""


@dataclass
class Result:
    task_id: str
    model: str
    served_model: str
    mode: str
    verdict: str
    evidence: list[dict[str, str]] = field(default_factory=list)
    reason: str = ""
    confidence: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    unverified_quotes: list[str] = field(default_factory=list)
    error: str = ""


def estimate_usd(body: dict[str, Any], models: dict[str, Any], mode: str) -> float:
    chars = 0
    images = 0
    for msg in body["input"]:
        for part in msg["content"]:
            if part["type"] == "input_text":
                chars += len(part["text"])
            else:
                images += 1
    chars += len(body.get("instructions", "")) + 1500  # schema and framing
    tokens_in = int(chars / float(models["chars_per_token_estimate"])) + images * int(
        models["image_token_estimate"]
    )
    price = price_for(models, body["model"], mode)
    return price.usd(tokens_in, int(body["max_output_tokens"]))


def _norm(text: str) -> str:
    text = text.replace(" ", " ").replace(" ", " ").replace("’", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


def verify_quotes(evidence: list[dict[str, str]], corpus: str) -> list[str]:
    """Quotes that do not appear verbatim (whitespace-normalised) in the text evidence."""
    hay = _norm(corpus)
    bad: list[str] = []
    for ev in evidence:
        quote = str(ev.get("quote", ""))
        if (
            str(ev.get("source", "")).lower().startswith("image")
            or not quote.strip()
            or TRUNCATION_MARK in quote
        ):
            continue
        q = _norm(quote).strip("\"'«» .…")
        if q and q not in hay:
            bad.append(quote[:200])
    return bad


def corpus_of(*items: Item) -> str:
    return (
        "\n".join(ev.text or "" for it in items for ev in it.evidence)
        + "\n"
        + "\n".join(n for it in items for n in it.notes)
    )


def parse_result(
    task: Task, response: dict[str, Any], mode: str, models: dict[str, Any], corpus: str
) -> Result:
    model = str(task.body["model"])
    tin, tout = usage_of(response)
    usd = price_for(models, model, mode).usd(tin, tout)
    res = Result(
        task.task_id,
        model,
        str(response.get("model", model)),
        mode,
        "unknown",
        input_tokens=tin,
        output_tokens=tout,
        usd=usd,
    )
    if response.get("status") not in (None, "completed"):
        res.error = f"response status {response.get('status')}: {json.dumps(response.get('incomplete_details'))}"
        return res
    try:
        payload = json.loads(output_text(response))
    except json.JSONDecodeError:
        res.error = "unparseable judge output"
        return res
    res.verdict = str(payload.get("winner") or payload.get("verdict") or "unknown")
    res.evidence = [dict(e) for e in payload.get("evidence") or []]
    res.reason = str(payload.get("reason", ""))
    res.confidence = float(payload.get("confidence") or 0.0)
    res.unverified_quotes = verify_quotes(res.evidence, corpus)
    return res


class Runner:
    def __init__(
        self,
        http: OpenAIHttp,
        ledger: Ledger,
        models: dict[str, Any],
        workdir: Path,
        poll_seconds: float = 30.0,
    ) -> None:
        self.http = http
        self.ledger = ledger
        self.models = models
        self.workdir = workdir
        self.poll_seconds = poll_seconds

    def run(
        self,
        tasks: list[Task],
        mode: str,
        corpora: dict[str, str],
        batch_timeout: float,
    ) -> tuple[list[Result], list[str]]:
        if not tasks:
            return [], []
        if mode == "batch":
            return self._batch(tasks, corpora, batch_timeout)
        return self._standard(tasks, corpora)

    def _standard(
        self, tasks: list[Task], corpora: dict[str, str]
    ) -> tuple[list[Result], list[str]]:
        results: list[Result] = []
        notes: list[str] = []
        for task in tasks:
            key = f"{self.ledger.run_id}/{task.task_id}/{task.body['model']}"
            try:
                self.ledger.reserve(
                    key, estimate_usd(task.body, self.models, "standard"), task.task_id
                )
            except BudgetExceeded as exc:
                notes.append(f"stopped before {task.task_id}: {exc}")
                break
            try:
                response = self.http.create_response(task.body)
            except ApiError as exc:
                self.ledger.settle(key)
                results.append(
                    Result(
                        task.task_id,
                        str(task.body["model"]),
                        "",
                        "standard",
                        "unknown",
                        error=str(exc),
                    )
                )
                continue
            res = parse_result(
                task, response, "standard", self.models, corpora[task.task_id]
            )
            self.ledger.spend(
                key,
                res.served_model,
                "standard",
                price_for(self.models, res.model, "standard"),
                res.input_tokens,
                res.output_tokens,
            )
            self.ledger.settle(key)
            results.append(res)
        return results, notes

    def _batch(
        self, tasks: list[Task], corpora: dict[str, str], timeout: float
    ) -> tuple[list[Result], list[str]]:
        model = str(tasks[0].body["model"])
        estimate = sum(estimate_usd(t.body, self.models, "batch") for t in tasks)
        key = f"{self.ledger.run_id}/batch/{model}/{int(time.time())}"
        notes: list[str] = []
        try:
            self.ledger.reserve(key, estimate, f"{len(tasks)} requests")
        except BudgetExceeded as exc:
            return [], [f"batch of {len(tasks)} {model} requests not sent: {exc}"]
        lines = [
            json.dumps(
                {
                    "custom_id": t.task_id,
                    "method": "POST",
                    "url": "/v1/responses",
                    "body": t.body,
                }
            )
            for t in tasks
        ]
        by_id = {t.task_id: t for t in tasks}
        try:
            file_id = self.http.upload_file(
                f"judge-{model}.jsonl", ("\n".join(lines) + "\n").encode("utf-8")
            )
            batch = self.http.create_batch(file_id)
        except ApiError as exc:
            self.ledger.settle(key)
            return [], [f"batch submission failed: {exc}"]
        (self.workdir / f"batch-{model}.json").write_text(
            json.dumps({"batch_id": batch["id"], "ledger_key": key})
        )
        start = time.time()
        while batch.get("status") not in (
            "completed",
            "failed",
            "expired",
            "cancelled",
        ):
            if time.time() - start > timeout:
                notes.append(
                    f"batch {batch['id']} not done after {timeout:.0f} s: cancelled, partial results kept"
                )
                self.http.cancel_batch(batch["id"])
                while batch.get("status") not in (
                    "cancelled",
                    "completed",
                    "failed",
                    "expired",
                ):
                    time.sleep(self.poll_seconds)
                    batch = self.http.get_batch(batch["id"])
                break
            time.sleep(self.poll_seconds)
            batch = self.http.get_batch(batch["id"])
        results: list[Result] = []
        for fid_key in ("output_file_id", "error_file_id"):
            fid = batch.get(fid_key)
            if not fid:
                continue
            for line in self.http.file_content(fid).decode("utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                task = by_id.get(row.get("custom_id", ""))
                if task is None:
                    continue
                resp = row.get("response") or {}
                if resp.get("status_code") == 200 and isinstance(
                    resp.get("body"), dict
                ):
                    res = parse_result(
                        task, resp["body"], "batch", self.models, corpora[task.task_id]
                    )
                else:
                    err = row.get("error") or (resp.get("body") or {}).get("error")
                    res = Result(
                        task.task_id,
                        model,
                        "",
                        "batch",
                        "unknown",
                        error=f"batch row error: {json.dumps(err)[:300]}",
                    )
                    body = resp.get("body")
                    if isinstance(body, dict):
                        res.input_tokens, res.output_tokens = usage_of(body)
                self.ledger.spend(
                    f"{key}/{task.task_id}",
                    res.served_model or model,
                    "batch",
                    price_for(self.models, model, "batch"),
                    res.input_tokens,
                    res.output_tokens,
                )
                results.append(res)
        self.ledger.settle(key)
        if batch.get("status") != "completed":
            notes.append(
                f"batch {batch['id']} ended {batch.get('status')}: {json.dumps(batch.get('errors'))[:300]}"
            )
        return results, notes


def in_sample(task_id: str, percent: int) -> bool:
    return int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) % 100 < percent
