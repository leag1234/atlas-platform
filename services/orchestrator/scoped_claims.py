"""Scoped claims of an answer about a provided document, checked on their passage.

Defect class (offline judge, every staging battery since -151): a summary of an
attached 34-page Commission communication said that "for more than twenty
years, Europe has been outpaced by the United States and China". The document
gives the twenty years to Europe's productivity gap with "the other major
economies", then says the EU fell behind the United States in advanced
technologies while China caught up in many sectors. Each word of the claim is
in the document; the claim joins an actor, a time span and a comparison that
the document states for different actors. The general faithfulness check
(source_faithfulness.py) never read it: the document is 116 000 characters,
above its SOURCE_LIMIT, so the turn was logged "skipped:source_too_long".

What this check does (T8, code finds and fetches, the model decides):
1. code picks the answer's sentences that state a time span (a duration such
   as "for more than twenty years", "depuis dix ans", "seit 2010") together
   with a comparison (behind, ahead, outpaced, caught up, gap, compared with,
   more than; the same in French, German, Spanish and Italian). These scoped
   claims are where a summary overgeneralises: the actor, the period or the
   comparison of one passage carried over to another. No keyword of the
   request is read: the gate is a provided document and such a sentence;
2. code retrieves, for each claim, the passages of the document that share
   its time span value and most of its content words (lexical retrieval over
   windows of three sentences, as BM25-style retrievers do before a reader:
   long documents need passage retrieval first, see source_faithfulness);
3. one small call (the fast screen profile, reasoning off; the turn's own
   profile in strict privacy) fills in, claim by claim, the actor, whom it is
   compared with, the comparison and the time span, from the claim and from
   the passages, names what differs, and returns "supported", a corrected
   sentence stating what the passages say, or "not_found". Asked for a verdict
   alone, the same model rewrote a faithful claim (1 of 1) or, told to name the
   fault first, passed 4 of 6 faulty ones; filling in the four elements first
   gave the right verdict on all 11 live readings below;
4. code keeps a correction only when it adds no number the claim and the
   passages do not hold, keeps the sentence's list marker or bold label, and
   stays a sentence of comparable length. An answer not yet sent gets the
   corrected sentence in place; an answer already streamed gets one short note
   with the corrected sentence, in the answer's language.

Survey (owner rule 2026-11-11: existing work first). Claim-level checking
against retrieved evidence is the common shape of FActScore (Min et al. 2023),
SAFE (Wei et al. 2024), RefChecker (Hu et al. 2024) and MiniCheck (Tang et al.
2024: a small checker on claim-passage pairs matches large judges at a
fraction of their cost). Their cost is one call per atomic fact; here code
selects the few sentences of the risky shape (actor, span, comparison) and one
call checks them together, so a summary without such a sentence costs nothing.
SummaC (Laban et al. 2022) shows that sentence-level pairing with the best
source passages beats whole-document entailment on long sources, which is the
case the general judge skips.

Measured on the staging gateway (2026-11-23, screen profile): the seven
distinct summaries of the Commission communication in the battery logs (six
overgeneralised, one faithful) and four fictional claims (two faulty, two
faithful, English and French): 11 of 11 verdicts right, the six corrections
state the twenty years for "the other major economies" and the United States
and China separately; 0.6 to 2.8 s and 0.0013 to 0.0036 EUR per answer.
An answer without a scoped claim makes no call.

Never blocking (D5): a call that fails, times out or is refused leaves the
answer as it is, and the failure code is logged with counts only, never the
answer or the document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import json
import logging
import re
import time
import unicodedata
from typing import Any

LOGGER = logging.getLogger(__name__)

MAX_CLAIMS = 6
PASSAGES_PER_CLAIM = 2
PASSAGE_CHARACTERS = 900
# A passage must share this many content words with the claim, or its time span.
MIN_SHARED = 3
CHECK_SECONDS = 20.0
CHECK_TOKENS = 2_500
BUDGET_EUR = Decimal("0.01")
MIN_SECONDS = 6.0

CHECK = (
    "Each CLAIM below is a sentence of an assistant's answer about a document "
    "the person provided, followed by the PASSAGES of that document that best "
    "match it. The claim states that an actor compares with others over a time "
    "span. Check that the passages give that time span to the same actor and "
    "the same comparison.\n"
    "For each claim, fill in, from the claim: who is compared (actor), with "
    "whom (compared_with), in what and how (comparison: behind, ahead, caught "
    "up, slower, in which field), over which time span (span); then the same "
    "four from the passages, where the passages give that time span. Then "
    "compare: the claim is faulty when it names a compared_with or a comparison "
    "that the passages do not attach to that time span (a time span the "
    "passage gives for one comparison carried over to other actors or another "
    "comparison; two actors joined under one verb when the passage says "
    "different things about each; some sectors made general; caught up turned "
    "into ahead). Wording, paraphrase and left-out details are no fault.\n"
    'Verdict: "supported" when nothing differs; "corrected" with the claim '
    "rewritten so that it states what the passages say about each actor, in "
    "the claim's language, keeping its list marker, bold label, page reference "
    'and the other words; "not_found" when the passages give nothing on it.\n'
    'Output only JSON: {"claims":[{"id":1,"claim":{"actor":"","compared_with":'
    '"","comparison":"","span":""},"passages":{"actor":"","compared_with":"",'
    '"comparison":"","span":""},"fault":"<what differs, empty if nothing>",'
    '"verdict":"supported|corrected|not_found","text":"<corrected claim, only '
    'when corrected>"}]}.\n'
)
NOTE = {
    "en": "More precisely, the document says: {items}.",
    "fr": "Plus précisément, d'après le document : {items}.",
    "de": "Genauer, laut dem Dokument: {items}.",
    "es": "Con más precisión, según el documento: {items}.",
    "it": "Più precisamente, secondo il documento: {items}.",
}

# Numbers written as words, up to the tens a time span uses, in the five
# languages of the comparison cues (accents removed, lower case).
_NUMBER_WORDS = {
    # en
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "hundred": 100,
    # fr
    "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7,
    "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12, "quinze": 15,
    "vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "cent": 100,
    "dizaine": 10, "quinzaine": 15, "vingtaine": 20, "trentaine": 30,
    # de
    "ein": 1, "einem": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "zwolf": 12, "zwanzig": 20,
    "dreissig": 30, "vierzig": 40, "funfzig": 50,
    # es
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "quince": 15, "veinte": 20,
    "treinta": 30, "cuarenta": 40, "cincuenta": 50,
    # it
    "due": 2, "quattro": 4, "cinque": 5, "sette": 7, "otto": 8, "nove": 9,
    "dieci": 10, "venti": 20, "trenta": 30, "quaranta": 40, "cinquanta": 50,
}  # fmt: skip
_VAGUE = (
    "several|many|a few|plusieurs|nombreuses|nombreux|quelques|des|mehrere|"
    "vielen|viele|varios|varias|muchos|muchas|diversi|molti|alcuni"
)
_UNIT = (
    r"(?:years?|decades?|months?|ans?|annees?|decennies?|mois|jahre[n]?|"
    r"jahrzehnte[n]?|monate[n]?|anos|decadas|meses|anni|decenni|mesi)"
)
_COUNT = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
# A duration: a count (digits, a number word or a vague quantity) then a time
# unit within two words ("plus de vingt ans", "over two decades").
_DURATION = re.compile(
    rf"\b(\d{{1,3}}|{_COUNT}|{_VAGUE})\b(?:\s+\S+){{0,2}}?\s+{_UNIT}\b"
)
# A start point: since / depuis / seit / desde / dal and a year or a decade.
_SINCE = re.compile(
    r"\b(?:since|depuis|seit|desde|dal|dall|dagli|dai)\b(?:\s++(?:the|l|les|"
    r"annees|den|los|anos|gli|anni)\b)*+\s++((?:1[89]|20)\d\d)s?\b"
)
# A comparison between actors or against a reference.
_COMPARISON = re.compile(
    r"\b(?:behind|ahead|outpac\w*|overtak\w*|overtook|outperform\w*|"
    r"underperform\w*|lag\w*|trail\w*|gap|caught up|catch\w* up|compared|"
    r"versus|vs|surpass\w*|exceed\w*|lead(?:s|ing)? (?:in|the)|"
    r"distanc\w*|devanc\w*|retard|rattrap\w*|ecart|prise? de vitesse|"
    r"depass\w*|surpass\w*|par rapport|face aux?|en avance|derriere|"
    r"concurren\w*|hinter\w*|ruckstand|uberhol\w*|vorsprung|im vergleich|"
    r"abgehangt|aufgeholt|detras|rezag\w*|superad\w*|ventaja|frente a|"
    r"en comparacion|alcanzad\w*|dietro|ritardo|superat\w*|vantaggio|"
    r"rispetto a|raggiunt\w*)\b"
    r"|\b(?:more|less|fewer|faster|slower|higher|lower|better|worse)\b.{0,60}\bthan\b"
    r"|\b(?:plus|moins|mieux|davantage)\b.{0,60}\bque?\b"
    r"|\b(?:mehr|weniger|besser|schneller|langsamer)\b.{0,60}\bals\b"
    r"|\b(?:mas|menos|mejor|peor)\b.{0,60}\bque\b"
    r"|\b(?:piu|meno|meglio)\b.{0,60}\bdi\b"
)
_WORD = re.compile(r"[^\W\d_]{4,}")
_DIGITS = re.compile(r"\d+(?:[.,]\d+)?")
_PREFIX = re.compile(
    r"^(\s*+(?:[-*+>]|\d{1,3}[.)])?+\s*+(?:\*\*[^*\n]{1,80}+\*\*\s*+:?+\s*+)?+)"
)
# Function words of the five languages: never evidence that a passage matches.
_STOP = frozenset(
    "that this with from have been were their there which what when where "
    "than more most also into over such only other these those about after "
    "dans pour avec sans sont etre elle elles leur leurs mais plus moins "
    "depuis cette celle ceux comme entre tous toutes tout aussi ainsi encore "
    "deja dont sous selon vers chez fait faire avoir nous vous ils "
    "nicht eine einer eines dass sind wurde werden seit nach auch oder "
    "para como sobre desde entre pero esta este estos estas todo "
    "della delle degli sono anche come dopo prima nella nelle".split()
)


def plain(text: str) -> str:
    """Lower case without accents, apostrophes as spaces (what the cues read)."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[’'`]", " ", stripped).replace("ß", "ss")


def spans_of(text: str) -> set[int]:
    """The time span values a text states (20 for "vingt ans", 2010 for "seit 2010")."""
    found: set[int] = set()
    lowered = plain(text)
    for match in _DURATION.finditer(lowered):
        word = match.group(1)
        if word.isdigit():
            found.add(int(word))
        elif word in _NUMBER_WORDS:
            found.add(_NUMBER_WORDS[word])
        else:
            found.add(-1)  # a vague duration ("several years")
    found.update(int(m.group(1)) for m in _SINCE.finditer(lowered))
    return found


def scoped(sentence: str) -> bool:
    """A sentence that states a time span together with a comparison."""
    lowered = plain(sentence)
    return bool(spans_of(sentence)) and _COMPARISON.search(lowered) is not None


def _stems(text: str) -> set[str]:
    return {word[:6] for word in _WORD.findall(plain(text)) if word not in _STOP}


_SPLIT = re.compile(r"(?<=[.!?;])\s+(?=[\"«“(]?[^\W\d_])")


def windows(text: str) -> list[str]:
    """The document as overlapping windows of three sentences."""
    flat = " ".join(re.sub(r"(?<=\w)-\n(?=\w)", "", text).split())
    parts = [part for part in _SPLIT.split(flat) if part.strip()]
    if not parts:
        return []
    found: list[str] = []
    for index in range(len(parts)):
        window = " ".join(parts[max(0, index - 1) : index + 2])
        found.append(window[:PASSAGE_CHARACTERS])
    return found


def passages(claim: str, document: list[str]) -> list[str]:
    """The windows that best match a claim: shared span value, shared stems."""
    wanted = _stems(claim)
    values = {value for value in spans_of(claim) if value >= 0}
    scored: list[tuple[float, int]] = []
    for index, window in enumerate(document):
        shared = len(wanted & _stems(window))
        same_span = bool(values & spans_of(window))
        if shared < MIN_SHARED and not (same_span and shared >= 1):
            continue
        scored.append((shared + (4 if same_span else 0), index))
    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen: list[int] = []
    for _, index in scored:
        # Overlapping windows say the same thing: keep distinct ones.
        if all(abs(index - other) > 1 for other in chosen):
            chosen.append(index)
        if len(chosen) == PASSAGES_PER_CLAIM:
            break
    return [document[index] for index in sorted(chosen)]


@dataclass
class Claim:
    start: int
    end: int
    text: str
    passages: list[str]
    corrected: str = ""


@dataclass
class Outcome:
    """What the check did: the answer to send, a note, counts for the log."""

    text: str
    note: str = ""
    candidates: int = 0
    sent: int = 0
    corrected: int = 0
    rejected: int = 0
    outcome: str = (
        ""  # none, supported, corrected, noted, failed:<code>, skipped:<reason>
    )
    cost_eur: Decimal = Decimal(0)
    seconds: float = 0.0
    # Spans of the claims corrected or noted, in the answer checked.
    spans: list[tuple[int, int]] = field(default_factory=list)

    def covers(self, start: int, end: int) -> bool:
        return any(start < right and left < end for left, right in self.spans)

    def metadata(self) -> dict[str, object]:
        return {
            "outcome": self.outcome,
            "candidates": self.candidates,
            "sent": self.sent,
            "corrected": self.corrected,
            "rejected": self.rejected,
            "cost_eur": str(self.cost_eur),
            "seconds": round(self.seconds, 2),
        }


def claims(body: str, document_text: str) -> list[Claim]:
    """The scoped claims of an answer, each with its best passages."""
    from services.orchestrator.source_faithfulness import sentences

    found: list[Claim] = []
    document: list[str] | None = None
    for start, end in sentences(body):
        sentence = body[start:end]
        if not scoped(sentence):
            continue
        if document is None:
            document = windows(document_text)
        best = passages(sentence, document)
        if best:
            found.append(Claim(start, end, sentence, best))
        if len(found) == MAX_CLAIMS:
            break
    return found


def messages(found: list[Claim]) -> list[dict[str, str]]:
    blocks = []
    for number, claim in enumerate(found, start=1):
        listed = "\n".join(f"- {passage}" for passage in claim.passages)
        blocks.append(
            f"CLAIM {number}: {claim.text.strip()}\nPASSAGES {number}:\n{listed}"
        )
    return [{"role": "user", "content": CHECK + "\n" + "\n\n".join(blocks)}]


def _numbers(text: str) -> set[str]:
    return {value.replace(",", ".") for value in _DIGITS.findall(text)}


def accepted(claim: Claim, corrected: str) -> str:
    """A correction code can keep, with the claim's own prefix, or ""."""
    text = " ".join(corrected.split())
    original = " ".join(claim.text.split())
    if not text or plain(text) == plain(original):
        return ""
    if len(text) > max(2 * len(original), len(original) + 300):
        return ""
    allowed = _numbers(original) | {
        n for passage in claim.passages for n in _numbers(passage)
    }
    if not _numbers(text) <= allowed:
        return ""
    prefix = _PREFIX.match(claim.text.lstrip("\n"))
    marker = prefix.group(1) if prefix else ""
    if marker.strip() and not text.startswith(marker.strip()):
        text = marker.strip() + " " + text
    leading = claim.text[: len(claim.text) - len(claim.text.lstrip())]
    trailing = claim.text[len(claim.text.rstrip()) :]
    return leading + text + trailing


def read(reply: str, found: list[Claim]) -> int:
    """Sets each claim's correction from the reply; returns those rejected.

    Raises ValueError when the reply is not the expected JSON.
    """
    from packages.model_json import object_content

    value = object_content(reply)
    listed = value.get("claims")
    if not isinstance(listed, list):
        raise ValueError("no claims")
    rejected = 0
    for item in listed:
        if not isinstance(item, dict) or item.get("verdict") != "corrected":
            continue
        if not str(item.get("fault") or "").strip():
            # A rewrite that names no differing element is a paraphrase.
            rejected += 1
            continue
        number = item.get("id")
        if not isinstance(number, int) or not 1 <= number <= len(found):
            continue
        claim = found[number - 1]
        kept = accepted(claim, str(item.get("text") or ""))
        if kept:
            claim.corrected = kept
        else:
            rejected += 1
    return rejected


def note(items: list[str], language: str) -> str:
    template = NOTE.get(language) or NOTE["en"]
    quoted = "; ".join(
        "« " + item + " »" if language == "fr" else "“" + item + "”" for item in items
    )
    return "*" + template.format(items=quoted) + "*"


async def check(
    body: str,
    document_text: str,
    *,
    profile: str | None,
    streamed: bool,
    language: str,
    seconds: float,
    complete: Any,
    write: bool = True,
) -> Outcome:
    """Check the scoped claims of an answer; never raises.

    ``write`` false (log mode) checks and counts, the answer unchanged.
    """
    from services.orchestrator import source_faithfulness as faith

    started = time.monotonic()
    result = Outcome(text=body)

    def done(outcome: str) -> Outcome:
        result.outcome = result.outcome or outcome
        result.seconds = time.monotonic() - started
        if result.candidates:
            LOGGER.warning("scoped_claims %s", json.dumps(result.metadata()))
        return result

    found = claims(body, document_text)
    result.candidates = len(found)
    if not found:
        return done("none")
    if profile is None or seconds < MIN_SECONDS:
        return done("skipped:" + ("no_profile" if profile is None else "deadline"))
    limit = min(CHECK_SECONDS, seconds)
    result.sent = len(found)
    payload = faith._payload(messages(found), profile, CHECK_TOKENS, limit, BUDGET_EUR)
    try:
        reply = await complete(payload, limit + 5)
    except Exception as error:  # noqa: BLE001 - reported by code, never raised on
        result.cost_eur = faith._failure_cost(error) or Decimal(0)
        return done("failed:" + faith._failure_code(error))
    result.cost_eur = faith._cost(reply) if isinstance(reply, dict) else Decimal(0)
    text = reply.get("text") if isinstance(reply, dict) else None
    try:
        result.rejected = read(text if isinstance(text, str) else "", found)
    except ValueError:
        return done("failed:unreadable")
    changed = [claim for claim in found if claim.corrected]
    result.corrected = len(changed)
    if not changed:
        return done("supported")
    result.spans = [(claim.start, claim.end) for claim in changed]
    if not write:
        return done("logged")
    if streamed:
        result.note = note(
            [
                " ".join(claim.corrected.split()).lstrip("-*+> ").replace("**", "")
                for claim in changed
            ],
            language,
        )
        return done("noted")
    pieces: list[str] = []
    position = 0
    for claim in changed:
        pieces.append(body[position : claim.start])
        pieces.append(claim.corrected)
        position = claim.end
    pieces.append(body[position:])
    result.text = "".join(pieces)
    return done("corrected")
