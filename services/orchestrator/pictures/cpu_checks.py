"""Whether each CPU picture shows the counts and unusual features asked for.

The image model often misses a counted or unusual detail (staging: "a teapot
with 3 spouts" gave two teapots with one spout). The rewrite lists those details
as short statements in the user's language (rewrite.checks); one vision call
per shown picture, on the gateway's "locate" role (the one that places
diagram labels, locate.py), says which statements the picture does not
show. The answer then names them plainly (note): never silent, never a
refusal, never a promise. When the check cannot run, the answer says the
details could not be checked. A picture that misses a statement is drawn
again (cpu_turn.checked_pictures); outcome says what the retries gave.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from services.orchestrator.pictures.texts import text

MAX_TOKENS = 300
CHECK_SECONDS = 40.0
CHECK_BUDGET = Decimal("0.01")
MAX_SEEN_CHARS = 60
SYSTEM = (
    "You check a picture against statements about it. For each statement, look "
    "carefully at the picture; when the statement gives a number, count. Answer "
    "true only when the picture clearly shows what the statement says, false when "
    "it does not or when you cannot tell. For each statement, 'seen' says what "
    "the picture shows instead, in a few words in the statement's language "
    "(for a count, the number you counted: 'deux roues', 'two apples'), "
    "and is empty when the statement is true. "
    'Reply with JSON only: {"met": [true or false, ...], "seen": ["...", ...]}, '
    "one value per statement, in the order given."
)
QUOTES = {
    "en": ("“", "”"),
    "fr": ("« ", " »"),
    "de": ("„", "“"),
    "es": ("«", "»"),
    "it": ("«", "»"),
}


def messages(checks: list[str], image_url: str) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Statements: " + json.dumps(checks, ensure_ascii=False),
                },
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        },
    ]


def parse(reply: str, count: int) -> list[bool] | None:
    """One boolean per statement, or None when the reply cannot be read."""
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(reply[start : end + 1])
    except ValueError:
        return None
    met = value.get("met") if isinstance(value, dict) else None
    if not isinstance(met, list) or len(met) != count:
        return None
    if not all(isinstance(item, bool) for item in met):
        return None
    return list(met)


def parse_seen(reply: str, count: int) -> list[str]:
    """What the picture shows instead, per statement ("" when not said)."""
    start, end = reply.find("{"), reply.rfind("}")
    try:
        value = json.loads(reply[start : end + 1]) if 0 <= start < end else None
    except ValueError:
        value = None
    seen = value.get("seen") if isinstance(value, dict) else None
    if not isinstance(seen, list) or len(seen) != count:
        return [""] * count
    return [
        " ".join(item.split())[:MAX_SEEN_CHARS] if isinstance(item, str) else ""
        for item in seen
    ]


async def unmet(
    checks: list[str],
    image: str,
    profile: str,
    spend: Any,
    call: Any,
    errors: list[str],
) -> list[str] | None:
    """The statements one picture (base64 PNG) does not show; None when the
    check could not run (the answer then says the details are unchecked)."""
    return (await check(checks, image, profile, spend, call, errors))[0]


async def check(
    checks: list[str],
    image: str,
    profile: str,
    spend: Any,
    call: Any,
    errors: list[str],
) -> tuple[list[str] | None, dict[str, str]]:
    """(unmet, gaps): the statements one picture misses (None: unchecked),
    and for each missed one what the picture shows instead when the check
    said it ("two wheels"), so the answer can name the measured gap."""
    from services.orchestrator.pictures.turn import (
        DRAFT_SHARE,
        Refused,
        _budget,
        _code,
        _refusal,
    )

    payload = {
        "messages": messages(checks, "data:image/png;base64," + image),
        "tools": [],
        "response_format": {"type": "json_object"},
        "profile": profile,
        "vision_role": "locate",
        "max_tokens": MAX_TOKENS,
        "timeout": CHECK_SECONDS,
        "budget_eur": _budget(min(CHECK_BUDGET, spend.remaining())),
        "single_attempt": True,
        "local_enabled": False,
    }
    try:
        reply = await call(payload, CHECK_SECONDS + 5)
    except Exception as error:  # noqa: BLE001 - D5 refusal, else unchecked
        if _refusal(error) and _code(error) not in DRAFT_SHARE:
            raise Refused(error) from error
        if _code(error) in DRAFT_SHARE:
            spend.reserved_unknown += CHECK_BUDGET
        errors.append("picture_cpu_check_" + _code(error))
        return None, {}
    spend.settle(reply)
    answer = str(reply.get("text") or "")
    met = parse(answer, len(checks))
    if met is None:
        errors.append("picture_cpu_check_invalid")
        return None, {}
    seen = parse_seen(answer, len(checks))
    missed = [statement for statement, shown in zip(checks, met) if not shown]
    gaps = {
        statement: found
        for statement, shown, found in zip(checks, met, seen)
        if not shown and found
    }
    return missed, gaps


def items(language: str, checks: list[str]) -> str:
    """The statements, quoted in the language's marks."""
    left, right = QUOTES.get(language, QUOTES["en"])
    return ", ".join(left + check + right for check in checks)


_items = items


def gapped(language: str, missed: list[str], gaps: dict[str, str]) -> str:
    """The missed statements, each with what the picture shows instead."""
    left, right = QUOTES.get(language, QUOTES["en"])
    return ", ".join(
        left
        + statement
        + right
        + (
            " " + text(language, "gap_seen", seen=gaps[statement])
            if gaps.get(statement)
            else ""
        )
        for statement in missed
    )


def gaps_of(candidates: list[dict[str, Any]]) -> dict[str, str]:
    """The first gap measured for each missed statement over the pictures."""
    found: dict[str, str] = {}
    for candidate in candidates:
        for statement, seen in (candidate.get("gaps") or {}).items():
            found.setdefault(statement, seen)
    return found


def outcome(
    language: str,
    checks: list[str],
    results: list[list[str] | None],
    attempts: int,
    unsatisfied: bool,
    gaps: dict[str, str] | None = None,
) -> str:
    """What the answer says of the shown pictures' checks once retries ran.

    Every picture drawn failed: the statements missed and the number of
    attempts, the shown pictures said to be the closest ones, never the
    answer. Some pictures were left out (retries, or one of the two failed)
    and the shown ones pass: how many were drawn and that only those that
    pass are shown. Otherwise the per-picture note."""
    if not checks:
        return ""
    if unsatisfied:
        missed = [c for c in checks if any(c in (r or []) for r in results)]
        key = "cpu_unsatisfied_one" if len(results) == 1 else "cpu_unsatisfied_all"
        return text(
            language,
            key,
            n=attempts,
            items=gapped(language, missed or checks, gaps or {}),
        )
    if attempts > len(results) and results and all(r == [] for r in results):
        return text(language, "cpu_retried", n=attempts, items=items(language, checks))
    return note(language, checks, results)


def note(language: str, checks: list[str], results: list[list[str] | None]) -> str:
    """What the answer says about the checks of its pictures ("" when every
    checked picture shows everything)."""
    if not checks or not results:
        return ""
    checked = [(n, r) for n, r in enumerate(results, 1) if r is not None]
    if not checked:
        return text(language, "cpu_check_unknown", items=_items(language, checks))
    missed = [(n, r) for n, r in checked if r]
    if not missed:
        return ""
    if len(missed) == len(results) and all(r == missed[0][1] for _, r in missed):
        key = "cpu_check_one" if len(results) == 1 else "cpu_check_all"
        return text(language, key, items=_items(language, missed[0][1]))
    return " ".join(
        text(language, "cpu_check_some", n=n, items=_items(language, r))
        for n, r in missed
    )
