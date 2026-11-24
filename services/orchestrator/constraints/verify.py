"""The three model calls of the pipeline, and how code checks their replies.

All three go through the gateway to an open model (no proprietary provider
is reachable from the product path), reasoning off, temperature 0, JSON out,
each bounded in tokens, seconds and euros:

- reading: the person's messages (and the reply they refer to) into typed
  constraints; code keeps only those whose quote is in the person's words
  and whose exact texts were written by the person or shown to them;
- verifier: the constraints code cannot decide, against the reply; a verdict
  stands only with an evidence quotation code finds in the reply (a verdict
  without one is "not checked": a verifier's unsupported pass is the known
  false-pass failure, its unsupported fail the over-strict one);
- repair: the reply rewritten once for the constraints not met; code
  refuses a rewrite that drops or invents a link, drops a code block or
  changes a figure it did not have to, and keeps the better of the two.

The verifier and the reading ask the fast check profile first: a model other
than the one that wrote the reply when routing gives one (a judge favours
its own text, Panickssery et al. 2024).
"""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from services.orchestrator.constraints.checks import Result
from services.orchestrator.constraints.model import Constraint, merged, norm, validated

READ_TOKENS = 900
VERIFY_TOKENS = 600
REPAIR_TOKENS = 1800
MESSAGE_CHARACTERS = 2500
SHOWN_CHARACTERS = 3000
ANSWER_CHARACTERS = 8000
EARLIER_MESSAGES = 6

READ_SYSTEM = """You list what a person asked about the FORM and exact CONTENT \
of the assistant's reply, as atomic constraints a checker can verify one by one.

Input JSON: "messages" (the person's messages, numbered, the last one is the \
request being answered now), "shown" (the assistant's previous reply, which the \
person may refer to, for example "the second outline"), "known" (constraints \
already found by code: do not repeat them).

Return JSON {"constraints": [...]}; each item:
- "kind": one of starts_with, ends_with, contains, excludes, sentences, items, \
paragraphs, list_style, order, titles, after_file, no_intro, only, no_new_file, judge
- "scope": "answer" when it is about the reply in the conversation, "file" when \
it is about a file, slide deck, document, app or picture being made
- "quote": the person's exact words that state it (copied, short)
- "statement": the constraint in a few words, in the person's language
- "text": the exact text for starts_with, ends_with, contains, excludes, after_file \
(copied exactly as the person wrote it, or as shown in "shown" when they refer to it)
- "items": list of exact texts for order and titles (from the messages or "shown")
- "count" and "relation" (exact, min, max) for sentences, items and paragraphs
- "style": bullets, numbered, checkboxes or plain for list_style and items
- "where": for scope file only: title, body, notes, footer, header, "page:N", \
"slide:N" or "each" (each page or slide)
- "lasting": true only if the person says it applies to all later replies

Rules: one item per constraint; only what the person asked, never good \
practice; nothing about facts or quality; a change request like "change only \
X" is not a constraint on the reply; earlier messages count only for \
constraints that still apply to the reply being written now. "items" counts \
list items only; "paragraphs" (with count and relation) counts paragraphs. \
"excludes" is only for a word or phrase the person forbids as such ("without \
the word ..."), never for a kind of content ("no explanations" is "only" or \
"judge"). starts_with and ends_with are about the whole reply; for a part \
of it (the second paragraph, each line) use "judge". Use "judge" with a \
statement for any other form or content requirement (a table and its \
columns, italics, separators, a tone or form of address, what each item must \
or must not contain). If there is none, return {"constraints": []}."""

VERIFY_SYSTEM = """You check a reply against constraints the person stated. \
For each constraint decide met true or false, ONLY from the reply text.

Input JSON: "constraints" (id, statement, the person's words) and "reply".
Return JSON {"results": [{"id": "...", "met": true|false, "evidence": "..."}]}.
"evidence" is copied verbatim from the reply (at most 25 words): for met=false \
the passage that breaks the constraint, or the passage where the missing part \
should be; for met=true the passage that fulfils it. Never paraphrase the \
evidence. If you cannot quote evidence, omit that constraint."""

REPAIR_SYSTEM = """You correct a reply so that it meets the person's \
constraints listed under "failed", each with what a checker found.

Keep everything else exactly: facts, figures, names, links (markdown links \
copied character for character), code blocks, and the language and register \
of the reply. Change only what each failed constraint needs. Do not add \
explanations about the correction. If a constraint cannot be met by \
rewriting the text (a file was or was not made), leave the text as it is.
Return JSON {"answer": "<the corrected reply in markdown>"}."""


def reading_messages(
    person: Sequence[str], shown: str, known: Sequence[Constraint]
) -> list[dict[str, Any]]:
    numbered = [
        {"n": index + 1, "text": text[:MESSAGE_CHARACTERS]}
        for index, text in enumerate(person)
    ][-EARLIER_MESSAGES:]
    data = {
        "messages": numbered,
        "shown": shown[:SHOWN_CHARACTERS],
        "known": [
            {"kind": item.kind, "quote": item.quote, "text": item.text}
            for item in known
        ],
    }
    return [
        {"role": "system", "content": READ_SYSTEM},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]


def parse_reading(
    reply: str, turn: int, person: Sequence[str], shown: str
) -> list[Constraint] | None:
    from packages.model_json import object_content

    try:
        value = object_content(reply)
    except ValueError:
        return None
    items = value.get("constraints")
    if not isinstance(items, list):
        return None
    found: list[Constraint] = []
    for raw in items[:40]:
        item = validated(raw, turn, person, [shown] if shown else [], source="model")
        if item is not None:
            found.append(item)
    return found


def verify_messages(items: Sequence[Constraint], answer: str) -> list[dict[str, Any]]:
    data = {
        "constraints": [
            {"id": item.id, "statement": item.label(), "person": item.quote}
            for item in items
        ],
        "reply": answer[:ANSWER_CHARACTERS],
    }
    return [
        {"role": "system", "content": VERIFY_SYSTEM},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]


def _found_in(evidence: str, answer: str) -> bool:
    """Whether the evidence quotation is in the reply: the whole of it, or
    its words in order with at most a few dropped."""
    wanted = norm(evidence).strip(" .…\"'")
    if len(wanted) < 3:
        return False
    plain = norm(re.sub(r"\]\([^)]*\)", "]", answer))
    if wanted in plain:
        return True
    words = wanted.split()
    if len(words) < 4:
        return False
    hits = sum(1 for word in words if word in plain)
    return hits >= 0.9 * len(words) and " ".join(words[:3]) in plain


def parse_verdicts(
    reply: str, items: Sequence[Constraint], answer: str
) -> dict[str, Result] | None:
    from packages.model_json import object_content

    try:
        value = object_content(reply)
    except ValueError:
        return None
    results = value.get("results")
    if not isinstance(results, list):
        return None
    ids = {item.id for item in items}
    verdicts: dict[str, Result] = {}
    for entry in results:
        if not isinstance(entry, dict) or entry.get("id") not in ids:
            continue
        met = entry.get("met")
        evidence = " ".join(str(entry.get("evidence") or "").split())[:300]
        if not isinstance(met, bool):
            continue
        if not _found_in(evidence, answer):
            # Unsupported: neither claimed nor reported.
            verdicts[str(entry["id"])] = Result(str(entry["id"]), None, "", "model")
            continue
        verdicts[str(entry["id"])] = Result(str(entry["id"]), met, evidence, "model")
    return verdicts


def repair_messages(
    answer: str, failed: Sequence[tuple[Constraint, Result]]
) -> list[dict[str, Any]]:
    data = {
        "failed": [
            {
                "constraint": item.label(),
                "person": item.quote,
                **({"text": item.text} if item.text else {}),
                **({"items": list(item.items)} if item.items else {}),
                **(
                    {"count": item.count, "relation": item.relation}
                    if item.count is not None
                    else {}
                ),
                "found": result.evidence,
            }
            for item, result in failed
        ],
        "reply": answer[:ANSWER_CHARACTERS],
    }
    return [
        {"role": "system", "content": REPAIR_SYSTEM},
        {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
    ]


_LINK = re.compile(r"\]\(([^)\s]+)\)")
_URL = re.compile(r"https?://[^\s)\]>]+")
_FENCED = re.compile(r"(`{3,}|~{3,})[^\n]*+\n.*?\1", re.S)
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
_LIST_NUMBER = re.compile(r"(?m)^[ \t]{0,6}\d{1,3}[.)][ \t]")


def parse_repair(
    reply: str, answer: str, allowed_numbers: Sequence[str] = ()
) -> str | None:
    """The rewritten reply, or None when code refuses it."""
    from packages.model_json import object_content

    try:
        value = object_content(reply)
    except ValueError:
        return None
    text = value.get("answer")
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    if len(text) > 2 * len(answer) + 400:
        return None
    before = set(_LINK.findall(answer)) | set(_URL.findall(answer))
    after = set(_LINK.findall(text)) | set(_URL.findall(text))
    if after - before or (before - after):
        # A link dropped or invented: the deliverable or a source would change.
        return None
    if any(
        block not in text for block in (m.group(0) for m in _FENCED.finditer(answer))
    ):
        return None
    known = set(_NUMBER.findall(answer)) | set(allowed_numbers)
    # The numbers of a numbered list are not figures.
    figures = _NUMBER.findall(_LIST_NUMBER.sub("", text))
    if any(number not in known for number in figures):
        # A figure the reply did not have: a rewrite never adds facts.
        return None
    return text


def deduplicated(
    code: Sequence[Constraint], model: Sequence[Constraint]
) -> list[Constraint]:
    """Code's reading first; a model item of a kind code already read with the
    same text is the same constraint."""
    return merged([*code, *model])
