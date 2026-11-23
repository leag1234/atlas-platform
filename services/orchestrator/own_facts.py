"""Facts about the person's own case that nobody gave, in a file made without data.

Staging -183 and -184 (offline judge, class C3): a one-page report "about our
team trying a 4 day work week", asked with no data, stated the team's results
as observed. After PR 650 the results became placeholders, but the file still
said "reducing hours from 40 to 32 per week", "No client complaints or deadline
slippage recorded" and "All meetings consolidated into 3-day windows". The
general file check, given the rule among a dozen others, let them pass (0 of 3
files flagged on main, 2 of 3 with the rule widened, and it flagged the
placeholders of a faithful file as missing content).

So this is one question of its own, asked only when the person gave no
document nor pasted text (deliverable_check.documents_given is False): code
numbers the lines of the file, a small call (the fast plan profile, reasoning
off) lists the lines that state as settled a fact about the person's own team,
organisation or project that their messages do not give, with the words that
state it; code keeps only words the line holds and that are not a bracketed
placeholder. What is left is a gap of the file: refused once, the writer puts a
placeholder in its place or removes it (deliverable_check.decide).

Code alone cannot tell "a standard week is 40 hours" (general knowledge) from
"we went from 40 to 32 hours" (the team's own fact), nor see a claim of absence
("no complaints recorded"), which holds no figure: unsourced_details checks
only external figures in a turn that read nothing, for that reason. The model
decides; the request and the earlier messages are what it compares with.

Never blocking (D5): a failed or unreadable call adds no gap and is logged with
its code.
"""

from __future__ import annotations

import asyncio
from decimal import ROUND_FLOOR, Decimal
import json
import logging
import re
from typing import Any

LOGGER = logging.getLogger(__name__)

MAX_LINES = 80
LINE_CHARACTERS = 300
MAX_ITEMS = 8
TOKENS = 700
SECONDS = 20.0
MIN_SECONDS = 6.0

QUESTION = (
    "A person asked an assistant for a file about their own team, organisation, "
    "project or event, and gave no document: their MESSAGES are all they gave. "
    "Below are the numbered LINES of the file. List every line that states, as "
    "an observed or settled fact about the person's own case, something the "
    "messages do not give: an outcome or a trend; any figure or count (hours, "
    "people, days, amounts, percentages, scores); a schedule or working "
    "pattern (days, hours, meeting arrangements); a duration or a date; a claim "
    "that something did not happen or was not recorded (no complaints, no "
    "delay). Not listed: what the messages give, even reworded; bracketed "
    "placeholders such as [result to add]; general knowledge that is not about "
    "the person's case; proposals, recommendations, questions and next steps "
    "written as such; titles and headings.\n"
    'Output only JSON: {"lines":[{"line":3,"words":"<the words of that line '
    'stating the fact, copied exactly>"}]}, an empty list when there is none.'
)
GAP = (
    "statements about the person's own case that no message gives: {items} "
    "(write each one as a bracketed placeholder in the file's language at its "
    "place, such as [result to add], or remove it; keep every placeholder and "
    "every figure the person gave)"
)
_PLACEHOLDER = re.compile(r"\[[^\[\]\n]{1,80}+\]")
_WORDS = re.compile(r"[^\W_]++")


def lines(text: str) -> list[str]:
    """The file's lines worth reading: three words or more, placeholders apart."""
    found: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if len(_WORDS.findall(_PLACEHOLDER.sub(" ", line))) < 3:
            continue
        found.append(line[:LINE_CHARACTERS])
        if len(found) == MAX_LINES:
            break
    return found


def messages(
    request: str, earlier: list[str], numbered: list[str]
) -> list[dict[str, str]]:
    asked = "\n".join(f"- {text}" for text in [*earlier, request] if text.strip())
    listed = "\n".join(f"{index}. {line}" for index, line in enumerate(numbered, 1))
    return [
        {
            "role": "user",
            "content": QUESTION + "\n\nMESSAGES:\n" + asked + "\n\nLINES:\n" + listed,
        }
    ]


def _plain(text: str) -> str:
    return " ".join(_WORDS.findall(text.casefold()))


_DIGIT_WORDS = {
    "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten",
}  # fmt: skip
# A statement whose words are mostly the person's is theirs, reworded.
GIVEN_SHARE = 0.6


def _given_words(asked: list[str]) -> set[str]:
    words = set(_plain(" ".join(asked)).split())
    return words | {_DIGIT_WORDS[w] for w in words if w in _DIGIT_WORDS}


def given(words: str, asked: list[str]) -> bool:
    """Whether the person's messages already hold a statement: all its figures
    and most of its words ("our team trying a 4 day work week" holds "our team
    piloted a four-day work week")."""
    held = _given_words(asked)
    plain = [w for w in _plain(_PLACEHOLDER.sub(" ", words)).split() if len(w) >= 2]
    if not plain:
        return True
    if any(w.isdigit() and w not in held for w in plain):
        return False
    return sum(w in held for w in plain) >= GIVEN_SHARE * len(plain)


def read(reply: str, numbered: list[str], asked: list[str] | None = None) -> list[str]:
    """The quoted statements code keeps; raises ValueError on another reply."""
    from packages.model_json import object_content

    value = object_content(reply)
    listed = value.get("lines")
    if not isinstance(listed, list):
        raise ValueError("no lines")
    kept: list[str] = []
    for item in listed:
        if not isinstance(item, dict):
            continue
        number = item.get("line")
        if not isinstance(number, int) or not 1 <= number <= len(numbered):
            continue
        line = numbered[number - 1]
        words = " ".join(str(item.get("words") or "").split()) or line
        bare = _plain(_PLACEHOLDER.sub(" ", words))
        # Words the line does not hold, or a placeholder alone: not this file's fact.
        if not bare or bare not in _plain(line):
            continue
        if asked is not None and given(words, asked):
            continue
        if words not in kept:
            kept.append(words)
    return kept[:MAX_ITEMS]


def start(
    tools: Any,
    request: str,
    earlier: list[str],
    text: str,
    seconds: float,
    call: Any,
    allowance: Decimal,
) -> asyncio.Task[tuple[list[str], Decimal]] | None:
    """The question, started now; None when it does not apply or cannot run."""
    from services.orchestrator.work_pictures import plan_profiles

    numbered = lines(text)
    profile = next(iter(plan_profiles(tools)), None)
    if not numbered or profile is None or seconds < MIN_SECONDS or allowance <= 0:
        return None
    model = getattr(tools, "check_model", None)
    strict = bool(getattr(model, "strict", False))
    limit = min(SECONDS, profile[1], seconds)

    async def run() -> tuple[list[str], Decimal]:
        payload: dict[str, Any] = {
            "messages": messages(request, earlier, numbered),
            "tools": [],
            "response_format": {"type": "json_object"},
            "profile": profile[0],
            "max_tokens": TOKENS,
            "timeout": limit,
            "budget_eur": str(allowance.quantize(Decimal("0.0001"), ROUND_FLOOR)),
            "reasoning_effort": "none",
            "reasoning_disabled": True,
            "sampling_temperature": 0.0,
            "single_attempt": True,
            "local_enabled": False,
        }
        if strict:
            payload["privacy"] = "strict"
        try:
            reply = await call(payload, limit + 5)
        except Exception as error:  # noqa: BLE001 - never blocks the delivery
            LOGGER.warning(
                json.dumps({"event": "own_facts_failed", "error": type(error).__name__})
            )
            return [], Decimal(0)
        try:
            spent = Decimal(str(reply.get("cost_eur") or 0))
        except ArithmeticError:
            spent = Decimal(0)
        spent = spent if spent.is_finite() and spent > 0 else Decimal(0)
        try:
            found = read(str(reply.get("text") or ""), numbered, [*earlier, request])
        except ValueError:
            LOGGER.warning(
                json.dumps({"event": "own_facts_failed", "error": "unreadable"})
            )
            return [], spent
        LOGGER.warning(
            json.dumps(
                {"event": "own_facts", "lines": len(numbered), "found": len(found)}
            )
        )
        return found, spent

    return asyncio.create_task(run())


_PLACEHOLDER_GAP = re.compile(r"(?i)\bplaceholders?\b")


def without_placeholder_gaps(gaps: list[str]) -> list[str]:
    """The general check's gaps, less those calling the placeholders a defect
    (a gap naming placeholder text without asking for a bracketed one).

    With no document given, a placeholder stands for the person's own data,
    the form this module asks for; told so, the check still called it missing
    content in 2 of 2 staging readings (2026-11-23), and its refusal would
    push the writer back to inventing.
    """
    kept = [
        gap
        for gap in gaps
        # A gap asking for a bracketed placeholder is kept: it is this form.
        if not _PLACEHOLDER_GAP.search(gap) or "[" in gap
    ]
    if len(kept) < len(gaps):
        LOGGER.warning(
            json.dumps(
                {
                    "event": "own_facts_placeholder_gap_dropped",
                    "count": len(gaps) - len(kept),
                }
            )
        )
    return kept


def gap(found: list[str]) -> str:
    return GAP.format(items="; ".join("“" + item + "”" for item in found))
