"""One pass per turn: read the constraints, check the reply, repair once, say
what is still not met. Never a loop.

Order of work, cheapest first:

1. code reads the constraints of the latest message (phrases.py); with
   ATLAS_ANSWER_CONSTRAINTS=full and form words in the message, one model
   reading adds the rest, quote-checked (verify.py);
2. code checks every constraint it can on the reply's structured view
   (checks.py); with "full", one verifier call decides the rest, each
   verdict backed by a quotation code finds in the reply;
3. repairs code can make without a model: the requested output alone
   (only_request.restrict), the lead-in dropped, a requested line moved
   under the file link;
4. with "full", one model rewrite for what is still not met, kept only when
   code accepts it and it meets strictly more constraints and breaks none
   that passed;
5. what is still not met is said in one closing sentence, in the person's
   words. A reply already sent (streamed) only gets that sentence.

Modes (ATLAS_ANSWER_CONSTRAINTS): "off"; "code" (the default: steps 1, 2, 3
and 5 by code alone, no model call, no cost); "full".
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import ROUND_FLOOR, Decimal
import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Awaitable, Callable, Sequence

from services.orchestrator.constraints import checks, phrases, verify
from services.orchestrator.constraints.model import Constraint, merged
from services.orchestrator.constraints.views import answer_view

log = logging.getLogger(__name__)

COMPLETE = Callable[[dict[str, Any], float], Awaitable[dict[str, Any]]]
CALL_BUDGET_EUR = Decimal("0.01")
READ_SECONDS = 12.0
VERIFY_SECONDS = 12.0
REPAIR_SECONDS = 25.0
MIN_SECONDS = 4.0
# Kinds a rewrite of the text can fix.
REWRITABLE = frozenset(
    {
        "starts_with",
        "ends_with",
        "contains",
        "excludes",
        "sentences",
        "items",
        "paragraphs",
        "list_style",
        "order",
        "titles",
        "judge",
        "no_intro",
    }
)


def mode() -> str:
    value = os.environ.get("ATLAS_ANSWER_CONSTRAINTS", "code").strip().casefold()
    return value if value in {"off", "code", "full"} else "code"


def holds(text: str) -> bool:
    """Whether the reply to this message is held until it is checked: code
    found a constraint on the reply, or (full mode) the model reading may."""
    current = mode()
    if current == "off" or not text.strip():
        return False
    if any(item.scope == "answer" for item in phrases.read(text, 1)):
        return True
    return current == "full" and phrases.worth_reading(text)


@dataclass
class Outcome:
    answer: str
    constraints: list[Constraint] = field(default_factory=list)
    results: list[checks.Result] = field(default_factory=list)
    repaired: str = ""
    note: str = ""
    calls: int = 0
    cost_eur: Decimal = Decimal(0)
    seconds: float = 0.0

    def unmet(self) -> list[Constraint]:
        failed = {result.id for result in self.results if result.passed is False}
        return [item for item in self.constraints if item.id in failed]

    def log_line(self) -> str:
        return json.dumps(
            {
                "event": "answer_constraints",
                "mode": mode(),
                "constraints": [
                    {"id": c.id, "kind": c.kind, "source": c.source}
                    for c in self.constraints
                ],
                "passed": sum(1 for r in self.results if r.passed is True),
                "failed": sum(1 for r in self.results if r.passed is False),
                "unchecked": sum(1 for r in self.results if r.passed is None),
                "repaired": self.repaired,
                "noted": bool(self.note),
                "calls": self.calls,
                "cost_eur": str(self.cost_eur),
                "seconds": round(self.seconds, 2),
            }
        )


class _Calls:
    def __init__(
        self,
        profile: str,
        strict: bool,
        complete: COMPLETE | None,
        spend: Callable[[Decimal], None] | None,
    ) -> None:
        self.profile = profile
        self.strict = strict
        self.complete = complete
        self.spend = spend
        self.count = 0
        self.cost = Decimal(0)

    def profiles(self) -> list[str]:
        from packages.profiles import picture_profile

        try:
            fast = picture_profile("deliverable_plan")
        except (OSError, ValueError):
            fast = None
        if not isinstance(fast, str) or fast == self.profile:
            return [self.profile]
        return [fast, self.profile]

    async def json(
        self,
        messages: list[dict[str, Any]],
        tokens: int,
        seconds: float,
        parse: Callable[[str], Any],
    ) -> Any:
        started = time.monotonic()
        for index, profile in enumerate(self.profiles()):
            left = seconds - (time.monotonic() - started)
            if left < MIN_SECONDS:
                return None
            limit = left if index else max(MIN_SECONDS, left * 0.6)
            text = await self._once(messages, tokens, limit, profile)
            value = parse(text) if text is not None else None
            if value is not None:
                return value
        return None

    async def _once(
        self, messages: list[dict[str, Any]], tokens: int, seconds: float, profile: str
    ) -> str | None:
        payload: dict[str, Any] = {
            "messages": messages,
            "tools": [],
            "response_format": {"type": "json_object"},
            "profile": profile,
            "max_tokens": tokens,
            "timeout": round(seconds, 1),
            "budget_eur": str(
                CALL_BUDGET_EUR.quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)
            ),
            "reasoning_effort": "none",
            "reasoning_disabled": True,
            "sampling_temperature": 0.0,
            "single_attempt": True,
            "local_enabled": False,
        }
        if self.strict:
            payload["privacy"] = "strict"
        call = self.complete or _complete
        self.count += 1
        try:
            async with asyncio.timeout(seconds + 3):
                reply = await call(payload, seconds + 3)
        except Exception as error:  # noqa: BLE001 - a failed call never blocks the turn
            log.warning(
                json.dumps(
                    {
                        "event": "answer_constraints_call_failed",
                        "error": type(error).__name__,
                        "code": str(getattr(error, "code", ""))[:40],
                        "status": str(getattr(error, "status", ""))[:8],
                    }
                )
            )
            return None
        try:
            spent = Decimal(str(reply.get("cost_eur") or 0))
        except ArithmeticError:
            spent = Decimal(0)
        if spent.is_finite() and spent > 0:
            self.cost += spent
            if self.spend is not None:
                self.spend(spent)
        return str(reply.get("text") or "")


async def _complete(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
    from packages.ports import atlas_ports
    from services.orchestrator.model import GatewayModel

    return await GatewayModel.post(
        os.environ.get("ATLAS_GATEWAY_URL", atlas_ports().url("gateway")).rstrip("/")
        + "/agent/complete",
        payload,
        seconds,
    )


def applicable(found: Sequence[Constraint], turn: int) -> list[Constraint]:
    """The constraints on this reply: the latest message's, and lasting ones."""
    return [
        item
        for item in found
        if item.scope == "answer"
        and (item.turn == turn or (item.lasting and item.kind != "no_new_file"))
    ]


def consistent(found: Sequence[Constraint]) -> list[Constraint]:
    """Readings that contradict each other are resolved before any check: a
    count of list items next to "no list" counts something else (words on one
    line), and goes to the verifier in the person's words."""
    plain = any(item.kind == "list_style" and item.style == "plain" for item in found)
    if not plain:
        return list(found)
    return [
        replace(item, kind="judge", statement=item.quote, count=None, style="")
        if item.kind == "items"
        else item
        for item in found
    ]


def check_all(
    items: Sequence[Constraint], answer: str, facts: dict[str, Any]
) -> dict[str, checks.Result]:
    view = answer_view(answer)
    facts = {**facts, "raw": answer}
    return {item.id: checks.check(item, view, facts) for item in items}


# ---- repairs by code --------------------------------------------------------------

_OWN_LINK = re.compile(r"\]\((?:https?://[^)/\s]+)?/(?:api/atlas/|images/)")


def _paragraphs(answer: str) -> list[str]:
    return [block for block in re.split(r"\n\s*\n", answer.strip()) if block.strip()]


def code_repair(answer: str, failed: Sequence[Constraint]) -> str:
    """The repairs code makes without a model, in a fixed order."""
    kinds = {item.kind for item in failed}
    if "only" in kinds:
        from services.orchestrator.only_request import restrict

        answer = restrict(answer)
    if kinds & {"no_intro", "sentences", "starts_with", "only"}:
        # The lead-in sentence goes when what it introduces follows.
        lead = checks.lead_in(answer_view(answer))
        blocks = _paragraphs(answer)
        if lead is not None and len(blocks) > 1:
            first = answer_view(blocks[0]).blocks
            if len(first) == 1 and first[0].text == lead.text:
                answer = "\n\n".join(blocks[1:])
    for item in failed:
        if item.kind == "after_file":
            answer = _line_after_file(answer, item.text)
    return answer


def _line_after_file(answer: str, text: str) -> str:
    from services.orchestrator.constraints.model import norm

    blocks = _paragraphs(answer)
    links = [index for index, block in enumerate(blocks) if _OWN_LINK.search(block)]
    if not links:
        return answer
    wanted = re.sub(r"^[\W_]+|[\W_]+$", "", norm(text))
    moved: list[str] = []
    kept: list[str] = []
    for index, block in enumerate(blocks):
        lines = block.splitlines()
        hits = [
            line
            for line in lines
            if re.sub(r"^[\W_]+", "", norm(line)).startswith(wanted)
        ]
        if hits and index < links[0]:
            moved.extend(hits)
            rest = [line for line in lines if line not in hits]
            if any(line.strip() for line in rest):
                kept.append("\n".join(rest))
            continue
        kept.append(block)
    if not moved:
        return answer
    last = max(index for index, block in enumerate(kept) if _OWN_LINK.search(block))
    kept = kept[: last + 1] + ["\n".join(moved)] + kept[last + 1 :]
    return "\n\n".join(kept)


# ---- the gap note -------------------------------------------------------------------

_QUOTES = {
    "fr": ("« ", " »"),
    "de": ("„", "“"),
    "en": ("“", "”"),
    "es": ("«", "»"),
    "it": ("«", "»"),
}
_NOTE = {
    "fr": "Cette réponse ne respecte pas encore ce que vous avez demandé : {items}.",
    "en": "This reply does not yet meet what you asked: {items}.",
    "de": "Diese Antwort erfüllt noch nicht, was Sie verlangt haben: {items}.",
    "es": "Esta respuesta aún no cumple lo que pidió: {items}.",
    "it": "Questa risposta non rispetta ancora quanto richiesto: {items}.",
}
_AND = {"fr": " et ", "en": " and ", "de": " und ", "es": " y ", "it": " e "}
LABEL_CHARACTERS = 90


def note(language: str, unmet: Sequence[Constraint]) -> str:
    if not unmet:
        return ""
    language = language if language in _NOTE else "en"
    left, right = _QUOTES[language]
    labels: list[str] = []
    for item in unmet[:4]:
        label = item.label().rstrip(" .;,")
        if len(label) > LABEL_CHARACTERS:
            label = label[: LABEL_CHARACTERS - 1].rstrip() + "…"
        labels.append(f"{left}{label}{right}")
    text = (
        ", ".join(labels[:-1])
        + (_AND[language] if len(labels) > 1 else "")
        + labels[-1]
    )
    return _NOTE[language].format(items=text)


# ---- the pass -------------------------------------------------------------------------


def _score(results: dict[str, checks.Result]) -> int:
    return sum(1 for result in results.values() if result.passed is False)


async def enforce(
    messages: Sequence[tuple[str, str]],
    answer: str,
    *,
    language: str,
    produced: bool | None = None,
    made: str = "",
    streamed: bool = False,
    profile: str = "",
    strict: bool = False,
    seconds: float = 40.0,
    complete: COMPLETE | None = None,
    spend: Callable[[Decimal], None] | None = None,
    run_mode: str | None = None,
) -> Outcome:
    """The reply checked against the person's constraints, repaired once."""
    started = time.monotonic()
    current = run_mode or mode()
    outcome = Outcome(answer)
    person = [text for role, text in messages if role == "user"]
    if current == "off" or not person or not answer.strip():
        return outcome
    turn = len(person)
    latest = person[-1]
    shown = next(
        (text for role, text in reversed(messages[:-1]) if role == "assistant"), ""
    )
    calls = _Calls(profile, strict, complete, spend)

    def left() -> float:
        return seconds - (time.monotonic() - started)

    found = phrases.read(latest, turn)
    if current == "full" and phrases.worth_reading(latest) and left() > MIN_SECONDS:
        read = await calls.json(
            verify.reading_messages(person, shown, found),
            verify.READ_TOKENS,
            min(READ_SECONDS, left()),
            lambda reply: verify.parse_reading(reply, turn, person, shown),
        )
        found = verify.deduplicated(found, read or [])
    items = merged(consistent(applicable(found, turn)))
    outcome.constraints = items
    if not items:
        outcome.seconds = time.monotonic() - started
        outcome.calls, outcome.cost_eur = calls.count, calls.cost
        return outcome
    facts: dict[str, Any] = {"produced": produced, "made": made}

    async def judged(text: str) -> dict[str, checks.Result]:
        results = check_all(items, text, facts)
        open_items = [
            item
            for item in items
            if results[item.id].passed is None and item.kind in {"judge"}
        ]
        if current == "full" and open_items and left() > MIN_SECONDS:
            verdicts = await calls.json(
                verify.verify_messages(open_items, text),
                verify.VERIFY_TOKENS,
                min(VERIFY_SECONDS, left()),
                lambda reply: verify.parse_verdicts(reply, open_items, text),
            )
            results.update(verdicts or {})
        return results

    results = await judged(answer)
    failed = [item for item in items if results[item.id].passed is False]
    if failed and not streamed:
        repaired = code_repair(answer, failed)
        if repaired.strip() and repaired != answer:
            again = check_all(items, repaired, facts)
            # Verdicts of the model on text code did not change stay valid.
            for key, value in results.items():
                if (
                    again[key].passed is None
                    and value.passed is not None
                    and value.by == "model"
                ):
                    again[key] = value
            if _score(again) < _score(results) and not _broke(results, again):
                answer, results, outcome.repaired = repaired, again, "code"
        failed = [item for item in items if results[item.id].passed is False]
        rewritable = [item for item in failed if item.kind in REWRITABLE]
        if current == "full" and rewritable and left() > MIN_SECONDS:
            numbers = [str(item.count) for item in items if item.count is not None]
            rewritten = await calls.json(
                verify.repair_messages(
                    answer, [(item, results[item.id]) for item in rewritable]
                ),
                verify.REPAIR_TOKENS,
                min(REPAIR_SECONDS, left()),
                lambda reply: verify.parse_repair(reply, answer, numbers),
            )
            if rewritten:
                again = await judged(rewritten)
                if _score(again) < _score(results) and not _broke(results, again):
                    answer, results = rewritten, again
                    outcome.repaired = (outcome.repaired + "+model").lstrip("+")
    outcome.answer = answer
    outcome.results = [results[item.id] for item in items]
    outcome.note = note(language, outcome.unmet())
    outcome.calls, outcome.cost_eur = calls.count, calls.cost
    outcome.seconds = time.monotonic() - started
    return outcome


def _broke(before: dict[str, checks.Result], after: dict[str, checks.Result]) -> bool:
    """A repair never breaks a constraint that passed."""
    return any(
        result.passed is True and after[key].passed is False
        for key, result in before.items()
    )
