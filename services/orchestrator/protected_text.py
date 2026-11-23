"""The parts of an answer that code wrote survive every rewrite of the answer.

Staging -148 (2026-11-21): the unsourced details review rewrote the answer of
an app turn and dropped its footer, the link to the version Atlas had just
stored; the next turn found no current app and created a second artifact.
Any bounded rewrite of an answer (details review, grounding, source
faithfulness, travel checks, the time cut, the internal-text filter) reads
and rewrites prose; none may drop or alter what code wrote around it.

Protected, measured on the answer before the rewrite:

- a line linking a version Atlas stored in this turn (item.artifacts), a file
  it produced (item.files) or a verified project deliverable (its download or
  preview route, which only server-returned receipts carry,
  file_results.project_deliverable_links);
- the folded source list (``<details>...</details>``) code appends;
- blocks the caller names: a trip planner's block, the plan card's text.

After the rewrite, a protected line or block that is no longer there verbatim
is put back: lines and blocks before the source list, the source list last
(when the rewrite left none). A link Atlas did not store is never protected,
so artifact_links still removes an invented version link.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

LOGGER = logging.getLogger(__name__)

LINK = re.compile(r"\[[^\]\n]{0,300}\]\(([^)\s]{1,800})\)")
DETAILS = re.compile(r"<details>[\s\S]*?</details>", re.IGNORECASE)
# A verified receipt of a project deliverable (file_results._deliverable_links).
DELIVERABLE = re.compile(
    r"^/api/atlas/orgs/[0-9a-f-]{36}/projects/[0-9a-f-]{36}/deliverables/"
    r"[0-9A-Za-z_-]{1,80}/(?:download|preview)$"
)


@dataclass
class Protected:
    parts: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.parts or self.details)


def _path(url: str) -> str:
    try:
        return urlparse(url).path.rstrip("/")
    except ValueError:
        return url.split("#", 1)[0].split("?", 1)[0]


def routes(item: Any) -> set[str]:
    """The paths of what code stored or produced in this turn."""
    found: set[str] = set()
    for artifact in getattr(item, "artifacts", None) or []:
        if isinstance(artifact, dict) and "id" in artifact and "version" in artifact:
            prefix = (
                f"/api/atlas/artifacts/{artifact['id']}/versions/{artifact['version']}"
            )
            found |= {prefix + "/open", prefix + "/download"}
    for url in getattr(item, "files", None) or []:
        if isinstance(url, str) and url:
            path = _path(url)
            found.add(path)
            if path.endswith("/download"):
                found.add(path[: -len("/download")] + "/preview")
    return found


def segments(text: str, item: Any, blocks: Iterable[str] = ()) -> Protected:
    """The code-written parts of an answer, before it is rewritten."""
    protected = Protected()
    if not text:
        return protected
    allowed = routes(item)
    folded = DETAILS.findall(text)
    body = DETAILS.sub("", text)
    for block in blocks:
        block = block.strip()
        if block and block in text and block not in protected.parts:
            protected.parts.append(block)
    for line in body.splitlines():
        line = line.strip()
        if not line or any(line in part for part in protected.parts):
            continue
        for match in LINK.finditer(line):
            path = _path(match.group(1))
            if path in allowed or DELIVERABLE.match(path):
                protected.parts.append(line)
                break
    protected.details = folded
    return protected


def restore(text: str, protected: Protected, where: str) -> str:
    """The rewritten answer with every protected part it lost put back."""
    if not protected:
        return text
    missing = [part for part in protected.parts if part not in text]
    restored = len(missing)
    if missing:
        insert = "\n\n".join(missing)
        cut = text.find("<details>")
        if cut >= 0:
            head = text[:cut].rstrip()
            text = (head + "\n\n" if head else "") + insert + "\n\n" + text[cut:]
        else:
            text = (text.rstrip() + "\n\n" if text.strip() else "") + insert
    if protected.details and "<details>" not in text:
        text = text.rstrip() + "\n\n" + "\n\n".join(protected.details)
        restored += len(protected.details)
    if restored:
        LOGGER.warning(
            json.dumps(
                {"event": "code_text_restored", "where": where, "parts": restored}
            )
        )
    return text
