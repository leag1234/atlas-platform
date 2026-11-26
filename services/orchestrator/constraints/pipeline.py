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
from services.orchestrator.constraints.model import (
    Constraint,
    fold,
    merged,
    norm,
    part_of,
)
from services.orchestrator.constraints.views import answer_view

log = logging.getLogger(__name__)

COMPLETE = Callable[[dict[str, Any], float], Awaitable[dict[str, Any]]]
CALL_BUDGET_EUR = Decimal("0.01")
READ_SECONDS = 20.0
VERIFY_SECONDS = 20.0
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
    # Why the pipeline did not check this reply ("stopped_turn": a reply code
    # wrote for a turn that stopped, never rewritten).
    skipped: str = ""
    # The repairs tried ("code", "model") and why one was not kept.
    attempted: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    # Gateway calls that failed (timeout, provider error, empty output).
    failed_calls: list[str] = field(default_factory=list)

    def unmet(self) -> list[Constraint]:
        """What code decided, with certainty, is not met: never an undecided
        constraint, never a verifier's verdict (the checker's own doubt)."""
        failed = {
            result.id
            for result in self.results
            if result.passed is False and result.by == "code"
        }
        return [item for item in self.constraints if item.id in failed]

    def log_line(self) -> str:
        """One line per turn, counts only: never the person's or the reply's
        words."""
        kinds: dict[str, int] = {}
        for item in self.constraints:
            kinds[item.kind] = kinds.get(item.kind, 0) + 1
        return json.dumps(
            {
                "event": "answer_constraints",
                "mode": mode(),
                "skipped": self.skipped,
                "constraints": len(self.constraints),
                "kinds": kinds,
                "from_model": sum(1 for c in self.constraints if c.source == "model"),
                "part_scoped": sum(1 for c in self.constraints if c.part),
                "passed": sum(1 for r in self.results if r.passed is True),
                "failed": sum(1 for r in self.results if r.passed is False),
                "failed_certain": len(self.unmet()),
                "undecided": sum(1 for r in self.results if r.passed is None),
                "repair_attempted": "+".join(self.attempted),
                "repair_kept": self.repaired,
                "repair_dropped": self.dropped,
                "noted": bool(self.note),
                "calls": self.calls,
                "failed_calls": len(self.failed_calls),
                "failed_call_causes": self.failed_calls,
                "cost_eur": str(self.cost_eur),
                "seconds": round(self.seconds, 2),
            },
            sort_keys=True,
        )


class _Calls:
    def __init__(
        self,
        profile: str,
        strict: bool,
        complete: COMPLETE | None,
        spend: Callable[[Decimal], None] | None,
        reasoning_room: int = 0,
    ) -> None:
        self.profile = profile
        self.strict = strict
        self.complete = complete
        self.spend = spend
        self.reasoning_room = reasoning_room
        self.count = 0
        self.cost = Decimal(0)
        self.failed: list[str] = []

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
            "max_tokens": tokens + self.reasoning_room,
            "timeout": round(seconds, 1),
            "budget_eur": str(
                CALL_BUDGET_EUR.quantize(Decimal("0.0001"), rounding=ROUND_FLOOR)
            ),
            "reasoning_effort": "none",
            "reasoning_ceiling": "low",
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
            cause = _call_cause(error)
            self.failed.append(cause)
            log.warning(
                json.dumps(
                    {
                        "event": "answer_constraints_call_failed",
                        "error": cause,
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


def _call_cause(error: Exception) -> str:
    code = str(getattr(error, "code", "") or "")
    if code:
        return code[:40]
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, RuntimeError):
        return "provider_error"
    return type(error).__name__ or "error"


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


COUNT_KINDS = frozenset({"items", "sentences", "paragraphs"})


def part_scoped(found: Sequence[Constraint]) -> list[Constraint]:
    """Constraints about one PART of the reply are never checked against the
    whole reply. A count or a list form is part-scoped when the person's words
    describe a part ("one with four dishes and one with six", "the other
    list", "three points in each section", "two menus"), or when the counts read
    cannot all hold on the whole reply (two exact counts of the same thing
    that differ, or the same count asked in two separate places)."""
    marked = [
        replace(item, part=part_of(item.quote))
        if item.scope == "answer"
        and item.kind in COUNT_KINDS | {"list_style"}
        and not item.part
        else item
        for item in found
    ]
    for kind in COUNT_KINDS:
        group = [
            index
            for index, item in enumerate(marked)
            if item.scope == "answer"
            and item.kind == kind
            and item.relation == "exact"
            and item.count is not None
            and not item.part
        ]
        clash = any(
            _apart(marked[a], marked[b])
            for position, a in enumerate(group)
            for b in group[position + 1 :]
        )
        if clash:
            for index in group:
                marked[index] = replace(marked[index], part="one")
    styles = {
        item.style
        for item in marked
        if item.scope == "answer" and item.kind == "list_style" and not item.part
    }
    if len(styles) > 1:
        # Two list forms asked: each is about its own part.
        marked = [
            replace(item, part="one")
            if item.scope == "answer" and item.kind == "list_style"
            else item
            for item in marked
        ]
    return marked


def _apart(first: Constraint, second: Constraint) -> bool:
    """Two exact counts of the same thing that cannot both describe the whole
    reply: different numbers, or the same number in separate words (one
    reading of the same words by code and by the model is one constraint)."""
    if first.count != second.count:
        return True
    one, other = fold(first.quote), fold(second.quote)
    if not one or not other or one in other or other in one:
        return False
    words, others = set(one.split()), set(other.split())
    return len(words & others) < 0.5 * min(len(words), len(others))


def check_all(
    items: Sequence[Constraint], answer: str, facts: dict[str, Any]
) -> dict[str, checks.Result]:
    view = answer_view(answer)
    facts = {**facts, "raw": answer}
    whole = [item for item in items if not item.part]
    results = {item.id: checks.check(item, view, facts) for item in whole}
    results.update(checks.part_results([item for item in items if item.part], view))
    return results


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
        if index < links[0] and not _OWN_LINK.search(block):
            # The asked line written as the last sentences of a paragraph
            # above the link ("... as asked. Tested: no."): those sentences
            # become the line under the link.
            sentences = re.split(r"(?<=[.!?…])\s+", block.strip())
            start = next(
                (
                    position
                    for position, sentence in enumerate(sentences)
                    if position
                    and re.sub(r"^[\W_]+", "", norm(sentence)).startswith(wanted)
                ),
                None,
            )
            if start is not None:
                moved.append(" ".join(sentences[start:]))
                kept.append(" ".join(sentences[:start]))
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
# Impersonal, so that it reads right in either register (tu or vous, du or
# Sie, tu or usted): the note never addresses the person.
_NOTE = {
    "fr": "Cette réponse ne respecte pas encore la demande : {items}.",
    "en": "This reply does not yet meet the request: {items}.",
    "de": "Diese Antwort erfüllt die Anfrage noch nicht: {items}.",
    "es": "Esta respuesta todavía no cumple la petición: {items}.",
    "it": "Questa risposta non rispetta ancora la richiesta: {items}.",
}
_AND = {"fr": " et ", "en": " and ", "de": " und ", "es": " y ", "it": " e "}
# What is missing, by kind: {q} the exact text or the person's words, {found}
# what code measured, {want} what was asked.
_MISSING: dict[str, dict[str, str]] = {
    "fr": {
        "starts_with": "elle ne commence pas par {q}",
        "ends_with": "elle ne se termine pas par {q}",
        "contains": "{q} n’y figure pas",
        "excludes": "{q} y figure encore",
        "sentences": "phrases : {found}, demandé : {want}",
        "items": "éléments de liste : {found}, demandé : {want}",
        "paragraphs": "paragraphes : {found}, demandé : {want}",
        "part_items": "aucune liste de {want} éléments",
        "part_each": "liste {k} : {found} éléments, demandé : {want}",
        "list_style": "la liste n’a pas la forme demandée ({q})",
        "order": "l’ordre demandé n’est pas suivi ({q})",
        "after_file": "la ligne {q} n’est pas sous le lien du fichier",
        "no_intro": "elle commence par une introduction",
        "only": "elle ajoute du texte autour de ce qui était demandé",
        "no_new_file": "un fichier a déjà été créé ou modifié",
        "other": "{q}",
    },
    "en": {
        "starts_with": "it does not begin with {q}",
        "ends_with": "it does not end with {q}",
        "contains": "{q} is missing",
        "excludes": "{q} is still used",
        "sentences": "sentences: {found}, asked: {want}",
        "items": "list items: {found}, asked: {want}",
        "paragraphs": "paragraphs: {found}, asked: {want}",
        "part_items": "no list of {want} items",
        "part_each": "list {k}: {found} items, asked: {want}",
        "list_style": "the list is not in the form asked ({q})",
        "order": "the order asked is not followed ({q})",
        "after_file": "the line {q} is not under the file link",
        "no_intro": "it begins with an introduction",
        "only": "it adds text around what was asked",
        "no_new_file": "a file was already created or changed",
        "other": "{q}",
    },
    "de": {
        "starts_with": "sie beginnt nicht mit {q}",
        "ends_with": "sie endet nicht mit {q}",
        "contains": "{q} fehlt",
        "excludes": "{q} kommt noch vor",
        "sentences": "Sätze: {found}, verlangt: {want}",
        "items": "Listenpunkte: {found}, verlangt: {want}",
        "paragraphs": "Absätze: {found}, verlangt: {want}",
        "part_items": "keine Liste mit {want} Punkten",
        "part_each": "Liste {k}: {found} Punkte, verlangt: {want}",
        "list_style": "die Liste hat nicht die verlangte Form ({q})",
        "order": "die verlangte Reihenfolge fehlt ({q})",
        "after_file": "die Zeile {q} steht nicht unter dem Dateilink",
        "no_intro": "sie beginnt mit einer Einleitung",
        "only": "sie enthält mehr als das Verlangte",
        "no_new_file": "eine Datei wurde bereits erstellt oder geändert",
        "other": "{q}",
    },
    "es": {
        "starts_with": "no empieza por {q}",
        "ends_with": "no termina con {q}",
        "contains": "falta {q}",
        "excludes": "todavía aparece {q}",
        "sentences": "frases: {found}, se pidió: {want}",
        "items": "elementos de lista: {found}, se pidió: {want}",
        "paragraphs": "párrafos: {found}, se pidió: {want}",
        "part_items": "ninguna lista de {want} elementos",
        "part_each": "lista {k}: {found} elementos, se pidió: {want}",
        "list_style": "la lista no tiene la forma pedida ({q})",
        "order": "no sigue el orden pedido ({q})",
        "after_file": "la línea {q} no está debajo del enlace del archivo",
        "no_intro": "empieza con una introducción",
        "only": "añade texto alrededor de lo pedido",
        "no_new_file": "ya se creó o modificó un archivo",
        "other": "{q}",
    },
    "it": {
        "starts_with": "non inizia con {q}",
        "ends_with": "non finisce con {q}",
        "contains": "manca {q}",
        "excludes": "compare ancora {q}",
        "sentences": "frasi: {found}, richiesto: {want}",
        "items": "elementi dell’elenco: {found}, richiesto: {want}",
        "paragraphs": "paragrafi: {found}, richiesto: {want}",
        "part_items": "nessun elenco di {want} elementi",
        "part_each": "elenco {k}: {found} elementi, richiesto: {want}",
        "list_style": "l’elenco non ha la forma richiesta ({q})",
        "order": "non segue l’ordine richiesto ({q})",
        "after_file": "la riga {q} non è sotto il link del file",
        "no_intro": "inizia con un’introduzione",
        "only": "aggiunge testo attorno a quanto richiesto",
        "no_new_file": "un file è già stato creato o modificato",
        "other": "{q}",
    },
}
_WANT = {
    "fr": {"exact": "{n}", "max": "{n} au plus", "min": "au moins {n}"},
    "en": {"exact": "{n}", "max": "at most {n}", "min": "at least {n}"},
    "de": {"exact": "{n}", "max": "höchstens {n}", "min": "mindestens {n}"},
    "es": {"exact": "{n}", "max": "como máximo {n}", "min": "al menos {n}"},
    "it": {"exact": "{n}", "max": "al massimo {n}", "min": "almeno {n}"},
}
LABEL_CHARACTERS = 90
_FOUND = re.compile(r"^(?:page \d+: )?(\d+) ")
_PART_FOUND = re.compile(r"^list (\d+): (\d+) ")


def _clip(text: str) -> str:
    text = " ".join(text.split()).rstrip(" .;,")
    if len(text) > LABEL_CHARACTERS:
        text = text[: LABEL_CHARACTERS - 1].rstrip() + "…"
    return text


def missing(
    language: str, item: Constraint, result: checks.Result | None = None
) -> str:
    """What is missing, in the person's language, for one constraint."""
    language = language if language in _MISSING else "en"
    table = _MISSING[language]
    left, right = _QUOTES[language]
    quoted = (
        item.text
        if item.kind
        in {"starts_with", "ends_with", "contains", "excludes", "after_file"}
        else item.label()
    )
    q = f"{left}{_clip(quoted)}{right}"
    if item.kind in COUNT_KINDS and item.count is not None:
        want = _WANT[language][item.relation].format(n=item.count)
        if item.part:
            if item.kind != "items":
                return table["other"].format(q=q)
            part = _PART_FOUND.match(result.evidence) if result is not None else None
            if item.part == "each" and part is not None:
                return table["part_each"].format(
                    k=part.group(1), found=part.group(2), want=want
                )
            return table["part_items"].format(want=want)
        found = _FOUND.match(result.evidence) if result is not None else None
        if found is not None:
            return table[item.kind].format(found=found.group(1), want=want)
        return table["other"].format(q=q)
    return table.get(item.kind, table["other"]).format(q=q)


def note(
    language: str,
    unmet: Sequence[Constraint],
    results: dict[str, checks.Result] | None = None,
) -> str:
    """One closing sentence naming what is missing, for what code decided is
    not met; empty when nothing is."""
    if not unmet:
        return ""
    language = language if language in _NOTE else "en"
    results = results or {}
    labels = list(
        dict.fromkeys(missing(language, item, results.get(item.id)) for item in unmet)
    )[:4]
    text = (
        ", ".join(labels[:-1])
        + (_AND[language] if len(labels) > 1 else "")
        + labels[-1]
    )
    return _NOTE[language].format(items=text)


# ---- what a repair may not do ---------------------------------------------------------

_LINK_TARGET = re.compile(r"\]\(([^)\s]+)\)")
_URL = re.compile(r"https?://[^\s)\]>]+")
_FENCED = re.compile(r"(`{3,}|~{3,})[^\n]*+\n.*?\1", re.S)
_CONTENT_WORD = re.compile(r"[^\W\d_]{5,}")
NEW_WORDS_SHARE = 0.2
NEW_WORDS_FLOOR = 2
PARROT_WORDS = 3


def delivered(answer: str) -> set[str]:
    """What the reply delivers and a repair must keep: links (files, app
    versions, pictures, sources) and code blocks."""
    found = set(_LINK_TARGET.findall(answer)) | set(_URL.findall(answer))
    found |= {match.group(0) for match in _FENCED.finditer(answer)}
    return found


def _words(text: str) -> set[str]:
    plain = re.sub(r"\]\([^)]*\)", "]", text)
    return set(_CONTENT_WORD.findall(fold(plain)))


def refusal(
    original: str,
    repaired: str,
    person: Sequence[str],
    shown: str,
    allowed: Sequence[str],
) -> str:
    """Why a repaired reply may not replace the original ("" when it may).

    A repair changes the form, never the subject or the content: it keeps
    every delivered item, adds no content the reply, the conversation or the
    asked texts did not have, and never writes the person's instructions back
    as reply text."""
    if not repaired.strip():
        return "empty"
    if not delivered(original) <= delivered(repaired):
        return "lost_delivered"
    sources = set().union(
        *(_words(text) for text in [original, shown, *person, *allowed])
    )
    added = _words(repaired) - sources
    if len(added) > max(NEW_WORDS_FLOOR, NEW_WORDS_SHARE * len(_words(repaired))):
        return "new_content"
    asked = [fold(text) for text in person[-1:]]
    kept = fold(original)
    exact = [fold(text) for text in allowed if text]
    for line in re.split(r"[\n.!?;:]+", repaired):
        folded = fold(line)
        if (
            len(folded.split()) >= PARROT_WORDS
            and folded not in kept
            and any(folded in message for message in asked)
            and not any(folded in text or text in folded for text in exact)
        ):
            return "parrots_request"
    return ""


# ---- the pass -------------------------------------------------------------------------


def _passed(results: dict[str, checks.Result]) -> set[str]:
    return {key for key, result in results.items() if result.passed is True}


def better(before: dict[str, checks.Result], after: dict[str, checks.Result]) -> bool:
    """A repair is kept only when it passes strictly more constraints and
    loses none that passed."""
    return _passed(before) < _passed(after)


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
    reasoning_room: int = 0,
    run_mode: str | None = None,
    stopped: bool = False,
) -> Outcome:
    """The reply checked against the person's constraints, repaired once.

    stopped: the reply is the one code wrote for a turn that stopped (a
    failure, a limit): the person's constraints are on the answer they asked
    for, not on that explanation, which is never checked nor rewritten."""
    started = time.monotonic()
    current = run_mode or mode()
    original = answer
    outcome = Outcome(answer)
    person = [text for role, text in messages if role == "user"]
    if current == "off" or not person or not answer.strip():
        return outcome
    if stopped:
        outcome.skipped = "stopped_turn"
        return outcome
    turn = len(person)
    latest = person[-1]
    shown = next(
        (text for role, text in reversed(messages[:-1]) if role == "assistant"), ""
    )
    calls = _Calls(profile, strict, complete, spend, reasoning_room)

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
    items = merged(part_scoped(consistent(applicable(found, turn))))
    outcome.constraints = items
    if not items:
        outcome.seconds = time.monotonic() - started
        outcome.calls, outcome.cost_eur = calls.count, calls.cost
        outcome.failed_calls = calls.failed
        return outcome
    facts: dict[str, Any] = {"produced": produced, "made": made}
    allowed = [text for item in items for text in (item.text, *item.items) if text]

    async def judged(text: str) -> dict[str, checks.Result]:
        results = check_all(items, text, facts)
        open_items = [
            item
            for item in items
            if results[item.id].passed is None and (item.kind == "judge" or item.part)
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
        outcome.attempted.append("code")
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
            why = refusal(answer, repaired, person, shown, allowed) or (
                "" if better(results, again) else "no_gain"
            )
            if why:
                outcome.dropped.append("code:" + why)
            else:
                answer, results, outcome.repaired = repaired, again, "code"
        else:
            outcome.dropped.append("code:unchanged")
        failed = [item for item in items if results[item.id].passed is False]
        rewritable = [item for item in failed if item.kind in REWRITABLE]
        if current == "full" and rewritable and left() > MIN_SECONDS:
            outcome.attempted.append("model")
            numbers = [str(item.count) for item in items if item.count is not None]
            rewritten = await calls.json(
                verify.repair_messages(
                    answer,
                    [(item, results[item.id]) for item in rewritable],
                    messages,
                ),
                verify.REPAIR_TOKENS,
                min(REPAIR_SECONDS, left()),
                lambda reply: verify.parse_repair(reply, answer, numbers),
            )
            if not rewritten:
                outcome.dropped.append("model:no_valid_rewrite")
            else:
                why = refusal(answer, rewritten, person, shown, allowed)
                again = await judged(rewritten) if not why else results
                if not why and not better(results, again):
                    why = "no_gain"
                if why:
                    outcome.dropped.append("model:" + why)
                else:
                    answer, results = rewritten, again
                    outcome.repaired = (outcome.repaired + "+model").lstrip("+")
    if not answer.strip():
        # Whatever produced it, an empty reply is never shown.
        answer, outcome.repaired = original, ""
        results = await judged(answer)
    outcome.answer = answer
    outcome.results = [results[item.id] for item in items]
    outcome.note = note(language, outcome.unmet(), results)
    outcome.calls, outcome.cost_eur = calls.count, calls.cost
    outcome.failed_calls = calls.failed
    outcome.seconds = time.monotonic() - started
    return outcome
