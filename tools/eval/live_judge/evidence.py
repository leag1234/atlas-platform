"""Turn an answer and its conversation into judge input parts.

Links in the answer are resolved to the files staging stores (read-only): apps
(`/api/atlas/artifacts/<id>/versions/<n>`), pictures (`/images/<key>.<ext>`,
`/images/picture/<set>/<n>`), project deliverables
(`/api/atlas/orgs/<org>/projects/<project>/deliverables/<file>/...`) and Open WebUI
files (`/api/v1/files/<id>`). Office files and PDFs are rendered with LibreOffice and
poppler, as the offline judge does (text through `pdftotext`, first pages as PNG).
"""

from __future__ import annotations

import base64
import io
import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.eval.frontier_judge.adapters import strip_data_uris

from . import source

HEX = r"[0-9a-f]{32}"
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
APP_LINK = re.compile(rf"/api/atlas/artifacts/({HEX})/versions/(\d{{1,4}})")
IMAGE_LINK = re.compile(rf"/images/({HEX})\.(png|jpe?g|webp|gif)\b")
PICTURE_LINK = re.compile(rf"/images/picture/({HEX})/([1-9])\b")
DELIVERABLE_LINK = re.compile(
    rf"/api/atlas/orgs/({UUID})/projects/({UUID})/deliverables/({UUID})"
)
OWUI_FILE_LINK = re.compile(rf"/api/v1/files/({UUID})")
IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "gif": "image/gif",
}
MAX_TEXT = {"user": 8000, "assistant": 5000}
MAX_CONTEXT_CHARS = 40_000
MAX_ANSWER_CHARS = 60_000
MAX_ARTIFACT_TEXT = 30_000
MAX_ARTIFACTS = 5
MAX_IMAGES = 6
MAX_IMAGE_BYTES = 6_000_000
PAGES = 3


BEGIN = "<<<UNTRUSTED DATA BEGIN"
END = "<<<UNTRUSTED DATA END>>>"
MARKER = re.compile(r"<<<\s*UNTRUSTED\s+DATA", re.I)


def wrap(label: str, body: str) -> str:
    """One evidence block: everything between the markers is data, never instructions."""
    clean = MARKER.sub("<<(marker removed)", body)
    return f"{BEGIN} [{label}]>>>\n{clean}\n{END}"


@dataclass
class Paths:
    data_dir: Path
    data_root: Path
    image_dir: Path
    webui_data: Path


@dataclass
class Evidence:
    parts: list[dict[str, Any]] = field(default_factory=list)
    images: int = 0
    artifacts: int = 0
    notes: list[str] = field(default_factory=list)

    def text(self, label: str, body: str) -> None:
        """Untrusted content, delimited as data (markers inside the body are neutralised)."""
        self.parts.append({"type": "input_text", "text": wrap(label, body)})

    def image(self, label: str, data: bytes, mime: str) -> bool:
        if self.images >= MAX_IMAGES or len(data) > MAX_IMAGE_BYTES:
            self.notes.append(f"{label}: image not shown to the judge (limit)")
            return False
        self.parts.append(
            {"type": "input_text", "text": f"### {label} (image follows)"}
        )
        self.parts.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime};base64," + base64.b64encode(data).decode(),
                "detail": "auto",
            }
        )
        self.images += 1
        return True


def _cut(text: str, limit: int) -> str:
    text = strip_data_uris(text)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[cut by the judge harness at {limit} characters]"


def sniff(data: bytes) -> str:
    """File kind from its bytes: pdf, docx, pptx, xlsx, odt, png, jpeg, html, svg or text."""
    head = data[:512]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\x89PNG"):
        return "png"
    if head.startswith(b"\xff\xd8"):
        return "jpeg"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
        except zipfile.BadZipFile:
            return "binary"
        if any(n.startswith("word/") for n in names):
            return "docx"
        if any(n.startswith("ppt/") for n in names):
            return "pptx"
        if any(n.startswith("xl/") for n in names):
            return "xlsx"
        if "content.xml" in names:
            return "odt"
        return "zip"
    lowered = head.lower()
    if b"<svg" in lowered:
        return "svg"
    if b"<html" in lowered or b"<!doctype html" in lowered:
        return "html"
    try:
        data[:4096].decode("utf-8")
    except UnicodeDecodeError:
        return "binary"
    return "text"


def render_document(data: bytes, kind: str) -> tuple[str | None, list[bytes]]:
    """(text, first page PNGs) of an office file or PDF; (None, []) when it cannot be rendered."""
    with tempfile.TemporaryDirectory(prefix="live-judge-") as tmp:
        folder = Path(tmp)
        src = folder / f"input.{kind}"
        src.write_bytes(data)
        pdf = src
        if kind != "pdf":
            soffice = shutil.which("soffice")
            if soffice is None:
                return None, []
            try:
                subprocess.run(
                    [
                        soffice,
                        f"-env:UserInstallation=file://{folder}/profile",
                        "--headless",
                        "--convert-to",
                        "pdf",
                        "--outdir",
                        str(folder),
                        str(src),
                    ],
                    capture_output=True,
                    timeout=120,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError):
                return None, []
            pdf = folder / "input.pdf"
            if not pdf.exists():
                return None, []
        text = None
        try:
            out = subprocess.run(
                ["pdftotext", "-layout", str(pdf), "-"],
                capture_output=True,
                timeout=60,
                check=True,
            )
            pages = out.stdout.decode("utf-8", "replace").split("\f")
            text = "\n".join(
                f"--- page {i} ---\n{p.strip()}"
                for i, p in enumerate(pages, 1)
                if p.strip()
            )
        except (OSError, subprocess.SubprocessError):
            text = None
        images: list[bytes] = []
        try:
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "50",
                    "-png",
                    "-l",
                    str(PAGES),
                    str(pdf),
                    str(folder / "page"),
                ],
                capture_output=True,
                timeout=60,
                check=True,
            )
            images = [p.read_bytes() for p in sorted(folder.glob("page*.png"))[:PAGES]]
        except (OSError, subprocess.SubprocessError):
            images = []
        return text, images


def add_file(ev: Evidence, label: str, data: bytes, name: str = "") -> None:
    kind = sniff(data)
    ev.artifacts += 1
    if kind in ("png", "jpeg"):
        ev.image(label, data, "image/png" if kind == "png" else "image/jpeg")
    elif kind in ("pdf", "docx", "pptx", "xlsx", "odt"):
        text, pages = render_document(data, kind)
        if text is None and not pages:
            ev.text(
                label, f"[{kind} file {name}: it could not be rendered for the judge]"
            )
            return
        ev.text(
            f"{label} ({kind}, text extracted from its PDF render)",
            _cut(text or "", MAX_ARTIFACT_TEXT),
        )
        for i, png in enumerate(pages, 1):
            ev.image(f"{label} page {i}", png, "image/png")
    elif kind in ("html", "svg", "text"):
        what = {
            "html": "HTML source (not rendered)",
            "svg": "SVG source",
            "text": "text",
        }[kind]
        ev.text(
            f"{label} ({what})",
            _cut(data.decode("utf-8", "replace"), MAX_ARTIFACT_TEXT),
        )
    else:
        ev.text(
            label, f"[{kind} file {name}, {len(data)} bytes: not readable by the judge]"
        )


def _webui_path(paths: Paths, stored: str) -> Path | None:
    """Host path of an Open WebUI upload (stored as a container path)."""
    if not stored:
        return None
    name = Path(stored).name
    candidate = paths.webui_data / "uploads" / name
    if candidate.exists() and candidate.resolve().is_relative_to(
        paths.webui_data.resolve()
    ):
        return candidate
    return None


def _owui_file(
    ev: Evidence, db: sqlite3.Connection, paths: Paths, file_id: str, label: str
) -> None:
    rec = source.file_record(db, file_id)
    if rec is None:
        ev.text(label, "[file not found]")
        return
    path = _webui_path(paths, str(rec["path"]))
    name = str(rec["filename"])
    data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
    content = str((data or {}).get("content") or "")
    if path is not None and path.stat().st_size <= 30_000_000:
        raw = path.read_bytes()
        if sniff(raw) in ("png", "jpeg"):
            add_file(ev, f"{label} {name}", raw, name)
            return
    if content:
        ev.artifacts += 1
        ev.text(
            f"{label} {name} (text extracted by the interface)",
            _cut(content, MAX_ARTIFACT_TEXT),
        )
    elif path is not None:
        add_file(ev, f"{label} {name}", path.read_bytes(), name)
    else:
        ev.text(label, f"[{name}: content not available]")


def _picture(paths: Paths, set_id: str, number: int) -> Path | None:
    record = paths.image_dir / "pictures" / f"{set_id}.json"
    if not record.exists():
        return None
    try:
        data = json.loads(record.read_text("utf-8"))
        candidate = data["candidates"][number - 1]
    except (ValueError, KeyError, IndexError, TypeError):
        return None
    for key in (candidate.get("render_key"), candidate.get("image_key")):
        if isinstance(key, str) and re.fullmatch(HEX, key):
            path = paths.image_dir / f"{key}.png"
            if path.exists():
                return path
    return None


def _latest_blob(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    files = [p for p in folder.iterdir() if p.is_file()]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def answer_artifacts(
    ev: Evidence, answer_text: str, db: sqlite3.Connection, paths: Paths
) -> None:
    seen: set[str] = set()

    def first(key: str) -> bool:
        if key in seen or ev.artifacts >= MAX_ARTIFACTS:
            return False
        seen.add(key)
        return True

    for m in APP_LINK.finditer(answer_text):
        if first(m.group(0)):
            path = (
                paths.data_dir
                / "artifacts"
                / "blobs"
                / m.group(1)
                / f"{int(m.group(2))}.html"
            )
            if path.exists():
                add_file(
                    ev, f"Delivered app (version {int(m.group(2))})", path.read_bytes()
                )
            else:
                ev.text("Delivered app", "[the linked app version is not stored]")
    for m in IMAGE_LINK.finditer(answer_text):
        if first(m.group(0)):
            path = paths.image_dir / f"{m.group(1)}.{m.group(2)}"
            if path.exists():
                ev.artifacts += 1
                ev.image(
                    "Picture shown in the answer",
                    path.read_bytes(),
                    IMAGE_MIME[m.group(2)],
                )
    for m in PICTURE_LINK.finditer(answer_text):
        if first(m.group(0)):
            pic = _picture(paths, m.group(1), int(m.group(2)))
            if pic is not None:
                ev.artifacts += 1
                ev.image(
                    f"Picture choice {m.group(2)} shown in the answer",
                    pic.read_bytes(),
                    "image/png",
                )
    for m in DELIVERABLE_LINK.finditer(answer_text):
        if first(m.group(3)):
            folder = (
                paths.data_root
                / "orgs"
                / m.group(1)
                / "projects"
                / m.group(2)
                / "blobs"
                / m.group(3)
            )
            blob = _latest_blob(folder)
            if blob is not None:
                add_file(ev, "Delivered file", blob.read_bytes())
            else:
                ev.text("Delivered file", "[the linked deliverable is not stored]")
    for m in OWUI_FILE_LINK.finditer(answer_text):
        if first(m.group(1)):
            _owui_file(ev, db, paths, m.group(1), "File linked in the answer")


def conversation(
    ev: Evidence, answer: source.Answer, db: sqlite3.Connection, paths: Paths
) -> None:
    turns = answer.history
    blocks: list[str] = []
    used = 0
    kept = 0
    for turn in reversed(turns[:-1]):
        body = _cut(turn.text, MAX_TEXT.get(turn.role, 4000))
        if used + len(body) > MAX_CONTEXT_CHARS:
            break
        blocks.append(f"[{turn.role}]\n{body}")
        used += len(body)
        kept += 1
    omitted = max(0, len(turns) - 1 - kept)
    if blocks or omitted:
        head = f"[{omitted} earlier messages omitted]\n\n" if omitted else ""
        ev.text(
            "Earlier conversation (oldest first)", head + "\n\n".join(reversed(blocks))
        )
    request = answer.request
    if request is None:
        ev.text("Request", "[no user message found]")
        return
    ev.text("Request (the person's last message)", _cut(request.text, 20_000))
    for f in request.files[:4]:
        file_id = str(f.get("id") or (f.get("file") or {}).get("id") or "")
        url = str(f.get("url") or "")
        if f.get("type") == "image" and url.startswith("data:image/"):
            mime, _, b64 = url[5:].partition(";base64,")
            try:
                ev.image("Image attached to the request", base64.b64decode(b64), mime)
            except ValueError:
                pass
        elif re.fullmatch(UUID, file_id):
            _owui_file(ev, db, paths, file_id, "File attached to the request:")
        elif m := OWUI_FILE_LINK.search(url):
            _owui_file(ev, db, paths, m.group(1), "File attached to the request:")


def build(answer: source.Answer, db: sqlite3.Connection, paths: Paths) -> Evidence:
    ev = Evidence()
    conversation(ev, answer, db, paths)
    ev.text(
        "Answer being graded", _cut(answer.text, MAX_ANSWER_CHARS) or "[empty answer]"
    )
    answer_artifacts(ev, answer.text, db, paths)
    return ev
