"""Adapters: read the staging battery evidence and trip outputs into judge items.

They read files only. The battery scripts are never imported (importing
battery_multiturn.py runs the battery); constants such as the questions are read
from their source text with ast.literal_eval, which executes nothing.
"""

from __future__ import annotations

import ast
import datetime as dt
import html
import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

FORBIDDEN_PARTS = ("/opt/atlas-src/private", "/BRAIN/", "/BRAIN")
UI_CHROME_NOTE = (
    "This answer was captured from the rendered page text, so interface labels such as "
    "'Thought for N seconds', 'Thinking...' or 'Open in the side view' may appear; they are "
    "interface chrome, not answer text."
)
LOGGER_CUT_NOTE = "The battery logger keeps only the first {cap} characters of this answer; its end is cut by the logger, not by Atlas."
DATA_URI = re.compile(r"data:([a-z0-9.+/-]+);base64,[A-Za-z0-9+/=]{200,}", re.I)
CHECK_LINE = re.compile(r"^CHECK (\S+) (PASS|FAIL|NOT OFFERED|NOT SHIPPED)\s*(.*)$")
TRIP_HEAD = re.compile(
    r"^=== (\S+) grant=(\S+) dialog_clicked=(\S+) seconds=(\d+) text_question=(\S+)"
)


class ForbiddenPath(Exception):
    pass


def guard(path: str | Path) -> Path:
    """Refuse anything under the owner's private folder or BRAIN/ (real user data)."""
    real = os.path.realpath(str(path))
    if any(part in real + "/" for part in FORBIDDEN_PARTS):
        raise ForbiddenPath(
            f"refused: {path} is private data and never reaches the judge"
        )
    return Path(real)


@dataclass
class Evidence:
    kind: str
    label: str
    text: str | None = None
    image: str | None = None
    path: str | None = None


@dataclass
class Item:
    item_id: str
    source: str
    kind: str
    classes: list[str]
    evidence: list[Evidence] = field(default_factory=list)
    code_checks: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    group: str | None = None

    def kinds(self) -> set[str]:
        return {e.kind for e in self.evidence}

    def add(
        self,
        kind: str,
        label: str,
        text: str | None = None,
        image: str | None = None,
        path: str | None = None,
    ) -> None:
        if text is None and image is None:
            return
        self.evidence.append(Evidence(kind, label, text, image, path))

    def check(self, cls: str, checks: dict[str, tuple[str, str]], *names: str) -> None:
        for name in names:
            if name in checks:
                status, detail = checks[name]
                self.code_checks.setdefault(cls, []).append((name, status, detail))


def read_text(path: Path, limit: int = 2_000_000) -> str:
    guard(path)
    return path.read_text(encoding="utf-8", errors="replace")[:limit]


def strip_data_uris(text: str) -> str:
    return DATA_URI.sub(
        lambda m: f"data:{m.group(1)};base64,[{len(m.group(0))} bytes elided]", text
    )


def pdf_text(pdf: Path) -> str | None:
    if not pdf.exists():
        return None
    guard(pdf)
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", str(pdf), "-"],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    pages = out.stdout.decode("utf-8", "replace").split("\f")
    return "\n".join(
        f"--- page {i} ---\n{p.strip()}" for i, p in enumerate(pages, 1) if p.strip()
    )


def pdf_pages(pdf: Path) -> int | None:
    try:
        out = subprocess.run(
            ["pdfinfo", str(pdf)], capture_output=True, timeout=60, check=True
        )
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"^Pages:\s+(\d+)", out.stdout.decode("utf-8", "replace"), re.M)
    return int(m.group(1)) if m else None


def numbered_pngs(folder: Path, prefix: str, limit: int = 8) -> list[Path]:
    rx = re.compile(re.escape(prefix) + r"-page-(\d+)\.png$")
    found = [
        (int(m.group(1)), p)
        for p in folder.glob(f"{prefix}-page-*.png")
        if (m := rx.search(p.name))
    ]
    return [p for _, p in sorted(found)][:limit]


def literal_constant(source_file: Path, name: str) -> Any:
    """Read a module-level constant without executing the module."""
    tree = ast.parse(read_text(source_file))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    return None


def parse_checks(log: str) -> dict[str, tuple[str, str]]:
    checks: dict[str, tuple[str, str]] = {}
    for line in log.splitlines():
        m = CHECK_LINE.match(line.strip())
        if m:
            checks[m.group(1)] = (m.group(2), m.group(3).strip())
    return checks


def sections(log: str) -> dict[str, list[Any]]:
    """Map each '== N title' section number to the JSON values printed in it."""
    out: dict[str, list[Any]] = {}
    current = ""
    buf: list[str] = []

    def flush() -> None:
        text = "\n".join(buf)
        values: list[Any] = []
        decoder = json.JSONDecoder()
        i = 0
        while i < len(text):
            j = min(
                [k for k in (text.find("{", i), text.find("[", i)) if k >= 0],
                default=-1,
            )
            if j < 0:
                break
            if j > 0 and text[j - 1] != "\n":
                i = j + 1
                continue
            try:
                value, end = decoder.raw_decode(text, j)
            except json.JSONDecodeError:
                i = j + 1
                continue
            values.append(value)
            i = end
        if current:
            out.setdefault(current, []).extend(values)

    for line in log.splitlines():
        m = re.match(r"^== (\d+) ", line)
        if m:
            flush()
            current, buf = m.group(1), []
        else:
            buf.append(line)
    flush()
    return out


def answer_evidence(
    item: Item, answer: str, cap: int | None, label: str = "answer"
) -> None:
    if cap is not None and len(answer) >= cap:
        item.add("answer_excerpt", label, answer)
        item.notes.append(LOGGER_CUT_NOTE.format(cap=cap))
    else:
        item.add("answer", label, answer)


def history_text(questions: list[str], current: str) -> str:
    lines = [f"Earlier turn {i}: {q}" for i, q in enumerate(questions, 1)]
    lines.append(f"Current request: {current}")
    return "\n".join(lines)


@dataclass
class BatteryConfig:
    tools_dir: Path = Path("/opt/atlas-src/tools")
    attached_pdf: Path = Path("/opt/atlas-src/corpus/docs/COM-2025-30-fr.pdf")
    refetch_sources: bool = False
    refetch_limit: int = 4


def refetch(url: str, limit: int = 120_000) -> str | None:
    """Fetch a cited public page again at judge time (the battery logs URLs, not page text)."""
    if not url.startswith(("http://", "https://")):
        return None
    req = urllib.request.Request(
        url, headers={"User-Agent": "frontier-judge/1 (offline evaluation)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read(2_000_000).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - any fetch failure just means no evidence
        return None
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    return re.sub(r"\s+", " ", text).strip()[:limit] or None


def battery_items(log_path: Path, cfg: BatteryConfig) -> list[Item]:
    log_path = guard(log_path)
    run = log_path.name.removesuffix(".log")
    d = log_path.with_suffix(".d")
    log = read_text(log_path)
    checks = parse_checks(log)
    secs = sections(log)
    items: list[Item] = []
    items += _deliverable_items(run, d, secs.get("8", []), checks, cfg)
    items += _app_items(run, d / "multiturn", checks)
    items += _docx_items(run, d / "multiturn", checks)
    items += _topic_items(run, d / "multiturn", checks, cfg)
    items += _strict_items(run, d / "multiturn", checks)
    items += _privacy_items(run, secs.get("7", []), checks)
    items += _ui_items(run, secs.get("2", []), checks, cfg)
    return items


def _deliverable_items(
    run: str,
    d: Path,
    values: list[Any],
    checks: dict[str, tuple[str, str]],
    cfg: BatteryConfig,
) -> list[Item]:
    items: list[Item] = []
    source = None
    src_file = cfg.tools_dir / "battery_deliverables.py"
    if src_file.exists():
        source = literal_constant(src_file, "SOURCE")
    for value in values:
        if not isinstance(value, dict):
            continue
        for kind in ("deck", "document"):
            rec = value.get(kind)
            if not isinstance(rec, dict) or "question" not in rec:
                continue
            it = Item(
                f"{run}/deliverable/{kind}",
                run,
                "deliverable",
                ["C2", "C3", "C4", "C5", "C7", "C10", "C13", "C14"],
            )
            it.add(
                "request",
                "request (the input file notes/projet.txt was uploaded to the project)",
                str(rec["question"]),
            )
            answer_evidence(it, str(rec.get("answer", "")), 900)
            if isinstance(source, str):
                it.add(
                    "source_text",
                    "input file notes/projet.txt (the only source given)",
                    source,
                )
            folder = d / "deliverables"
            txt = pdf_text(folder / f"{kind}.pdf")
            if txt:
                it.add(
                    "current_version",
                    f"{kind} text extracted from its LibreOffice PDF render",
                    txt,
                    path=str(folder / f"{kind}.pdf"),
                )
            for png in numbered_pngs(folder, kind):
                it.add(
                    "rendered_pages",
                    f"{kind} {png.stem}",
                    image=str(png),
                    path=str(png),
                )
            record = {
                k: rec.get(k)
                for k in ("status", "deliverables", "download", "rendered_pages")
            }
            signals = rec.get("signals")
            if isinstance(signals, dict):
                record["signals"] = {
                    k: v for k, v in signals.items() if k != "per_slide"
                }
            it.add(
                "artifact_record",
                "deliverable record from the battery",
                json.dumps(record, ensure_ascii=False),
            )
            it.check(
                "C10", checks, f"deliverable.{kind}", f"deliverable.{kind}.preview"
            )
            it.check("C4", checks, "rawtext")
            items.append(it)
    return items


def _load(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    value = json.loads(read_text(path))
    return value if isinstance(value, dict) else None


def _app_items(run: str, mt: Path, checks: dict[str, tuple[str, str]]) -> list[Item]:
    data = _load(mt / "app.json")
    if not data:
        return []
    items: list[Item] = []
    asked: list[str] = []
    prev_html: tuple[int, str, Path] | None = None
    for n, turn in enumerate(data.get("turns") or [], 1):
        name = str(turn.get("turn"))
        q = str(turn.get("question", ""))
        cls = ["C2", "C4", "C5", "C11", "C13", "C14"]
        if name in ("flags", "broken"):
            cls.append("C12")
        it = Item(f"{run}/app/{name}", run, "app_turn", cls)
        it.add("request", "conversation requests", history_text(asked, q))
        answer_evidence(it, str(turn.get("answer", "")), 2500)
        versions = sorted({int(v[1]) for v in turn.get("links") or [] if len(v) > 1})
        cur = None
        for v in versions:
            p = mt / "app" / f"turn{n}-{name}-v{v}.html"
            if p.exists():
                cur = (v, strip_data_uris(read_text(p)), p)
        if cur:
            it.add(
                "current_version",
                f"stored HTML of version {cur[0]}",
                cur[1],
                path=str(cur[2]),
            )
            if prev_html and prev_html[0] < cur[0]:
                it.add(
                    "previous_version",
                    f"stored HTML of version {prev_html[0]}",
                    prev_html[1],
                    path=str(prev_html[2]),
                )
                it.classes.append("C1")
        for suffix, label in (
            ("-side", "side view screenshot (the app as the person sees it)"),
            ("", "chat page screenshot"),
        ):
            png = mt / "app" / f"turn{n}-{name}{suffix}.png"
            if png.exists():
                it.add("rendered_pages", label, image=str(png), path=str(png))
        record = {
            "versions_linked_in_answer": versions,
            "stored_versions": turn.get("stored_versions"),
            "version_shown_in_side_view": (turn.get("side") or {}).get("version_shown"),
        }
        it.add(
            "artifact_record",
            "artifact version record",
            json.dumps(record, ensure_ascii=False),
        )
        side_checks = {
            k: v
            for k, v in checks.items()
            if k.startswith(f"multiturn.app.{name}.side")
        }
        if side_checks:
            it.add(
                "render_checks",
                "render check results",
                "\n".join(f"{k} {s} {d}" for k, (s, d) in side_checks.items()),
            )
        it.check("C1", checks, f"multiturn.app.{name}.features_kept")
        it.check("C2", checks, f"multiturn.app.{name}.claims_match_file")
        it.check(
            "C11",
            checks,
            f"multiturn.app.{name}.new_version",
            f"multiturn.app.{name}.no_version",
            f"multiturn.app.{name}.link",
        )
        it.check(
            "C12",
            checks,
            f"multiturn.app.{name}.side_media_shown",
            f"multiturn.app.{name}.side_media_blocked",
        )
        it.check("C4", checks, "rawtext")
        it.check("C5", checks, f"multiturn.app.{name}.answer")
        items.append(it)
        asked.append(q)
        if cur:
            prev_html = cur
    return items


def _docx_items(run: str, mt: Path, checks: dict[str, tuple[str, str]]) -> list[Item]:
    data = _load(mt / "docx.json")
    if not data:
        return []
    items: list[Item] = []
    asked: list[str] = []
    prev: tuple[str, Path] | None = None
    for n, turn in enumerate(data.get("turns") or [], 1):
        name = str(turn.get("turn"))
        q = str(turn.get("question", ""))
        it = Item(
            f"{run}/docx/{name}",
            run,
            "docx_turn",
            ["C2", "C3", "C4", "C5", "C10", "C11", "C13", "C14"],
        )
        it.add("request", "conversation requests", history_text(asked, q))
        it.add(
            "source_text",
            "sources available",
            "The person attached no source. The only facts supplied are those in the requests:\n"
            + "\n".join(asked + [q]),
        )
        answer_evidence(it, str(turn.get("answer", "")), 1500)
        pdf = mt / "docx" / f"turn{n}-{name}.pdf"
        txt = pdf_text(pdf)
        if txt:
            it.add(
                "current_version",
                "document text (LibreOffice PDF render)",
                txt,
                path=str(pdf),
            )
            if prev:
                it.add(
                    "previous_version",
                    "previous document text",
                    prev[0],
                    path=str(prev[1]),
                )
                it.classes.append("C1")
        for png in numbered_pngs(mt / "docx", f"turn{n}-{name}"):
            it.add(
                "rendered_pages", f"document {png.stem}", image=str(png), path=str(png)
            )
        file_rec = turn.get("file") or {}
        record = {
            k: file_rec.get(k)
            for k in ("path", "strategy", "file_id", "version_id", "revision")
        }
        record["headings"] = turn.get("headings")
        it.add(
            "artifact_record",
            "file and version record",
            json.dumps(record, ensure_ascii=False),
        )
        it.check(
            "C11",
            checks,
            f"multiturn.docx.{name}.same_file_new_version",
            f"multiturn.docx.{name}.deliverable",
        )
        it.check("C1", checks, f"multiturn.docx.{name}.title_kept")
        it.check(
            "C2",
            checks,
            f"multiturn.docx.{name}.change_present",
            f"multiturn.docx.{name}.content",
        )
        it.check("C10", checks, f"multiturn.docx.{name}.preview")
        it.check("C5", checks, f"multiturn.docx.{name}.answer")
        it.check("C4", checks, "rawtext")
        items.append(it)
        asked.append(q)
        if txt:
            prev = (txt, pdf)
    return items


def _topic_items(
    run: str, mt: Path, checks: dict[str, tuple[str, str]], cfg: BatteryConfig
) -> list[Item]:
    data = _load(mt / "topic.json")
    if not data:
        return []
    items: list[Item] = []
    asked: list[str] = []
    for turn in data.get("turns") or []:
        name = str(turn.get("turn"))
        q = str(turn.get("question", ""))
        it = Item(
            f"{run}/topic/{name}",
            run,
            "topic_turn",
            ["C3", "C4", "C5", "C7", "C13", "C14"],
        )
        it.add("request", "conversation requests", history_text(asked, q))
        answer_evidence(it, str(turn.get("answer", "")), None)
        retrieved = [str(u) for u in turn.get("retrieved") or []]
        cited = [str(u) for u in turn.get("cited") or []]
        it.add(
            "source_urls",
            "URLs retrieved by Atlas in this turn, and URLs cited",
            json.dumps({"retrieved": retrieved, "cited": cited}),
        )
        if cfg.refetch_sources:
            for url in (cited or retrieved)[: cfg.refetch_limit]:
                page = refetch(url)
                if page:
                    it.add(
                        "source_text",
                        f"{url} (re-fetched at judge time; the battery logs URLs, not page text)",
                        page,
                    )
        it.check(
            "C3",
            checks,
            f"multiturn.topic.{name}.sources",
            f"multiturn.topic.{name}.value",
        )
        it.check("C13", checks, f"multiturn.topic.{name}.language")
        it.check(
            "C14",
            checks,
            f"multiturn.topic.{name}.understood",
            f"multiturn.topic.{name}.on_task",
        )
        it.check("C5", checks, f"multiturn.topic.{name}.answer")
        it.check("C4", checks, "rawtext")
        items.append(it)
        asked.append(q)
    return items


def _strict_items(run: str, mt: Path, checks: dict[str, tuple[str, str]]) -> list[Item]:
    data = _load(mt / "strict.json")
    if not data:
        return []
    items: list[Item] = []
    for turn in data.get("turns") or []:
        name = str(turn.get("turn"))
        it = Item(f"{run}/strict/{name}", run, "strict_turn", ["C4", "C8"])
        answer_evidence(it, str(turn.get("answer", "")), None)
        record = {
            "requested": "strict",
            "applied": turn.get("applied"),
            "code": turn.get("code"),
            "urls_cited": turn.get("urls"),
            "owned_endpoint_requests_before_after": data.get("owned_requests"),
            "router_journal": data.get("journal"),
        }
        it.add(
            "privacy_record",
            "privacy level record",
            json.dumps(record, ensure_ascii=False),
        )
        it.check(
            "C8", checks, f"multiturn.strict.{name}.answer", "multiturn.strict.owned"
        )
        it.check("C4", checks, "rawtext")
        items.append(it)
    return items


def _privacy_items(
    run: str, values: list[Any], checks: dict[str, tuple[str, str]]
) -> list[Item]:
    items: list[Item] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        shared = {
            k: value.get(k)
            for k in (
                "owned_requests",
                "journal",
                "strict_served_owned",
                "strict_state",
            )
        }
        for key, rec in value.items():
            if (
                not isinstance(rec, dict)
                or "applied" not in rec
                and "chat_level" not in rec
            ):
                continue
            requested = (
                "enhanced"
                if key.startswith("enhanced")
                else "strict"
                if key.startswith("strict")
                else "standard"
            )
            it = Item(
                f"{run}/privacy/{key}",
                run,
                "privacy",
                ["C8", "C9"] if rec.get("code") else ["C8"],
            )
            answer_evidence(it, str(rec.get("answer", "")), 600)
            record = {
                "requested": requested,
                **{
                    k: rec.get(k)
                    for k in ("status", "applied", "chat_level", "code", "note")
                },
            }
            if requested == "strict":
                record.update(shared)
            it.add(
                "privacy_record",
                "privacy level record",
                json.dumps(record, ensure_ascii=False),
            )
            check = {
                "standard": "privacy.standard",
                "enhanced": "privacy.enhanced",
            }.get(requested)
            names = (
                [check]
                if check
                else [
                    "privacy.strict.fact",
                    "privacy.strict.doc",
                    "privacy.strict.ui",
                    "privacy.strict.owned",
                ]
            )
            it.check("C8", checks, *names)
            items.append(it)
    return items


def _ui_items(
    run: str, values: list[Any], checks: dict[str, tuple[str, str]], cfg: BatteryConfig
) -> list[Item]:
    turns = (
        literal_constant(cfg.tools_dir / "ui_tour.py", "TURNS")
        if (cfg.tools_dir / "ui_tour.py").exists()
        else None
    )
    questions = {
        str(t[0]): str(t[1])
        for t in turns or []
        if isinstance(t, tuple) and len(t) >= 2
    }
    if not questions:
        return []
    doc_text = pdf_text(cfg.attached_pdf) if cfg.attached_pdf.exists() else None
    pages = pdf_pages(cfg.attached_pdf) if doc_text else None
    items: list[Item] = []
    asked: list[str] = []
    for value in values:
        if not isinstance(value, list):
            continue
        for rec in value:
            if not isinstance(rec, dict) or rec.get("turn") not in questions:
                continue
            name = str(rec["turn"])
            q = questions[name]
            it = Item(
                f"{run}/ui/{name}",
                run,
                "ui_turn",
                ["C2", "C3", "C4", "C7", "C13", "C14"],
            )
            it.notes.append(UI_CHROME_NOTE)
            it.add(
                "request",
                "conversation requests (a public PDF was attached in the first turn)",
                history_text(asked, q),
            )
            answer_evidence(it, str(rec.get("answer", "")), 6000)
            if doc_text:
                head = f"Attached document {cfg.attached_pdf.name}; pdfinfo page count: {pages}.\n"
                it.add(
                    "source_text",
                    "attached PDF text (pdftotext)",
                    head + doc_text,
                    path=str(cfg.attached_pdf),
                )
            it.check("C2", checks, f"ui.{name}")
            it.check("C4", checks, "rawtext")
            items.append(it)
            asked.append(q)
    return items


def trip_items(out_path: Path, script: Path | None) -> list[Item]:
    out_path = guard(out_path)
    run = out_path.stem
    cases: dict[str, str] = {}
    locale: dict[str, str] = {}
    tz: dict[str, str] = {}
    if script and script.exists():
        for name, question, *_ in literal_constant(script, "CASES") or []:
            cases[str(name)] = str(question)
        locale = literal_constant(script, "LOCALE") or {}
        tz = literal_constant(script, "TZ") or {}
    finished = dt.datetime.fromtimestamp(out_path.stat().st_mtime, dt.UTC)
    items: list[Item] = []
    name = ""
    head = ""
    buf: list[str] = []

    def flush() -> None:
        if not name:
            return
        it = Item(
            f"{run}/{name}",
            run,
            "trip",
            ["C4", "C5", "C6", "C7", "C13", "C14"],
            group=f"trip/{name}",
        )
        it.notes.append(UI_CHROME_NOTE)
        it.notes.append(
            "The itinerary lines, the 'Arrives X for Y requested' line and the closing notes are generated by code "
            "from the timetable service; the short sentences around them are written by the model."
        )
        if name in cases:
            it.add(
                "request",
                "request typed in the chat (position granted by the browser)",
                cases[name],
            )
        zone = tz.get(name)
        local = finished.astimezone(ZoneInfo(zone)) if zone else finished
        context = {
            "run_finished_local": local.isoformat(timespec="minutes"),
            "tomorrow_local": (local.date() + dt.timedelta(days=1)).isoformat(),
            "date_note": "Local date and time where the person is, taken when the trip run finished (cases run a few minutes apart).",
            "interface_locale": locale.get(name, "fr-FR"),
            "timezone": tz.get(name),
            "battery_header": head,
        }
        it.add("context", "test context", json.dumps(context))
        answer_evidence(it, "\n".join(buf).strip(), 6000)
        items.append(it)

    for line in read_text(out_path).splitlines():
        m = TRIP_HEAD.match(line)
        if m:
            flush()
            name, head, buf = m.group(1), line, []
        elif name:
            buf.append(line)
    flush()
    return items
