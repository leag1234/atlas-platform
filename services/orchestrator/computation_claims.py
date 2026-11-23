"""A claim of a deed needs the deed: counted, calculated, checked or run (C2).

Defect class C2: an answer asserts that something was computed, counted,
checked or done when no tool did it. Staging -165 (2026-11-22): "the full
text of the PDF was provided, so I counted directly" gave 44 occurrences of a
word that appears 85 times; earlier, a count "done by hand" was wrong, and
"the flags work" held for 5 of 20 countries.

Code knows what ran in the turn: every tool call the model asked for (the
gateway turn, model.py) and every tool the service ran (chat_pipeline,
document_chat) are recorded here. After the answer, a sentence that claims a
deed is matched against them:
- a computation claim ("I counted", "comptage", "calculated", "by my
  count") needs a computation tool (calculator, dates, units, currencies,
  document_statistics, code execution or the terminal);
- a check claim ("I checked", "j'ai verifie", "I tested", "I ran") needs a
  tool of any kind, or a document the person provided (reading it is the
  check).
A claim without its deed gets one closing note in the answer's language that
says plainly the numbers were not computed (or nothing was run): an estimate is
never left presented as a count. Negated claims ("I did not count") and the
person's own words are not claims. No model call; the answer text is kept.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
import re
import unicodedata

# Tools whose result is a computation by code.
COMPUTATION_TOOLS = frozenset(
    {
        "calculator",
        "date_time",
        "unit_convert",
        "currency_convert",
        "document_statistics",
        "code_execution",
        "terminal_command",
        "journey_plan",
        "transit_plan",
    }
)


@dataclass
class Deeds:
    """What ran in one turn: tool names, and whether a provided text was read."""

    tools: set[str] = field(default_factory=set)
    provided: bool = False
    terminal_commands: int = 0

    def computed(self) -> bool:
        return bool(self.tools & COMPUTATION_TOOLS) or self.terminal_commands > 0

    def acted(self) -> bool:
        return bool(self.tools) or self.provided or self.terminal_commands > 0


current: ContextVar[Deeds | None] = ContextVar("atlas_deeds", default=None)


def record(names: Iterable[str]) -> None:
    """Record the tools a turn ran or asked for (no-op outside a turn)."""
    if (deeds := current.get()) is not None:
        deeds.tools.update(str(name) for name in names)


def record_provided() -> None:
    """Record that the turn read a document or text the person provided."""
    if (deeds := current.get()) is not None:
        deeds.provided = True


# --- Claims ------------------------------------------------------------------
# Patterns run on lower-case text without accents, in the five UI languages.
_COUNT = (
    r"(?:compte|comptes|comptee|comptees|recompte|calcule|calcules|calculee|"
    r"calculees|denombre|denombres)"
)
# Adverbs that may stand between the auxiliary and the participle ("j'ai bien
# compte"): never any word, or "j'ai un compte" would read as a count.
_ADVERBS_FR = (
    r"(?:(?:bien|donc|alors|ensuite|aussi|egalement|deja|directement|"
    r"manuellement|soigneusement|moi-meme|tout|tous|toutes)\s+){0,2}"
)
_COMPUTATION = re.compile(
    "|".join(
        (
            # French: a first-person or passive deed, and the nouns of a count.
            rf"\b(?:j'ai|j'en\s+ai|nous\s+avons|on\s+a|je\s+les\s+ai|je\s+l'ai)\s+"
            rf"{_ADVERBS_FR}{_COUNT}\b",
            r"\b(?:mon|par|d'apres\s+mon|selon\s+mon)\s+(?:propre\s+)?"
            r"(?:comptage|decompte|denombrement)\b",
            r"\bcomptage\s+(?:insensible|sensible)\s+a(?:ux)?\s+(?:la\s+)?casse\b",
            r"\bcomptage\s+(?:direct|manuel)\s+(?:sur|dans)\s+(?:le|les)\s+(?:texte|document|pdf|pages)\b",
            r"\b(?:selon|d'apres)\s+mes\s+calculs?\b|\bapres\s+calcul\b",
            # English.
            r"\b(?:i|we)\s+(?:have\s+|'ve\s+|just\s+|also\s+)?(?:\w+\s+)?"
            r"(?:counted|calculated|computed|tallied|measured|recounted)\b",
            r"\b(?:counted|calculated|computed|tallied)\s+(?:by\s+hand|manually|directly|"
            r"from\s+the\s+(?:text|document|pdf)|in\s+the\s+(?:text|document|pdf)|"
            r"page\s+by\s+page|one\s+by\s+one|myself)\b",
            r"\bby\s+my\s+(?:count|calculations?|tally)\b|\bmy\s+(?:count|calculations?|tally)\s+"
            r"(?:gives|shows|finds|is|comes)\b",
            r"\bcase[-\s]insensitive\s+count\b|\bmy\s+(?:manual|direct|own|careful)\s+count\b",
            r"\bcounting\s+(?:case-insensitively|by\s+hand|manually|every|each|all)\b",
            # German, Spanish, Italian.
            r"\b(?:ich\s+habe|wir\s+haben)\b.{0,40}\b(?:gezahlt|berechnet|nachgezahlt)\b",
            r"\bnach\s+meiner\s+(?:zahlung|berechnung)\b",
            r"\b(?:he|hemos)\s+(?:contado|calculado|medido)\b|\bsegun\s+mis?\s+(?:cuenta|calculos?)\b",
            r"\b(?:ho|abbiamo)\s+(?:contato|calcolato|misurato)\b|\bsecondo\s+i\s+miei\s+calcoli\b",
        )
    )
)
# French participles with a manner ("comptees a la main"), read with their
# accents: without them "calcule directement" is also the present tense.
_COMPUTATION_ACCENTED = re.compile(
    r"\b(?:compté|comptée|recompté|calculé|calculée|dénombré|dénombrée)s?\s+"
    r"(?:à\s+la\s+main|manuellement|directement|un\s+à\s+un|une\s+à\s+une|"
    r"sur\s+le\s+texte|dans\s+le\s+texte|dans\s+le\s+document|page\s+par\s+page|"
    r"moi-même)"
)
_CHECK = re.compile(
    "|".join(
        (
            rf"\b(?:j'ai|nous\s+avons|je\s+l'ai|je\s+les\s+ai)\s+{_ADVERBS_FR}"
            r"(?:verifie|verifies|verifiee|teste|testee|testes|execute|lance|fait\s+tourner|"
            r"controle|recoupe)\b",
            r"\bapres\s+verification\b",
            r"\b(?:i|we)\s+(?:have\s+|'ve\s+|just\s+|also\s+)?(?:\w+\s+)?"
            r"(?:checked|verified|tested|ran|executed|double-checked|confirmed\s+by\s+running)\b",
            r"\b(?:ich\s+habe|wir\s+haben)\b.{0,40}\b(?:gepruft|uberpruft|getestet|ausgefuhrt)\b",
            r"\b(?:he|hemos)\s+(?:verificado|comprobado|probado|ejecutado)\b",
            r"\b(?:ho|abbiamo)\s+(?:verificato|controllato|testato|eseguito)\b",
        )
    )
)
# Words that negate the deed when they come shortly before it in the clause.
_NEGATION = re.compile(
    r"\b(?:pas|jamais|ni|sans|not|never|didn't|haven't|hasn't|cannot|can't|couldn't|"
    r"without|nicht|nie|kein|ohne|no|nunca|sin|non|mai|senza)\b|n'(?:ai|avons|a)\b"
)
_CLAUSE_END = re.compile(r"[.;:!?\n,]")


def plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    return "".join(c for c in folded if not unicodedata.combining(c))


def _quoted(text: str) -> str:
    """The text with quotations blanked: a quoted sentence is not a claim."""
    return re.sub(r"«[^«»]{0,400}»|“[^“”]{0,400}”|\"[^\"\n]{0,400}\"", " ", text)


def _claims(pattern: re.Pattern[str], text: str) -> list[str]:
    found: list[str] = []
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 40) : match.start()]
        clause = _CLAUSE_END.split(before)[-1]
        if _NEGATION.search(clause) or _NEGATION.search(match.group(0)):
            continue
        found.append(match.group(0))
    return found


@dataclass(frozen=True)
class Verdict:
    computation: tuple[str, ...]
    check: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.computation or self.check)


def unbacked(answer: str, deeds: Deeds, asked: Iterable[str] = ()) -> Verdict:
    """The claims of the answer that no deed of the turn backs."""
    unquoted = _quoted(answer)
    text = plain(unquoted)
    lower = unicodedata.normalize("NFC", unquoted.lower().replace("’", "'"))
    for words in asked:
        # The person's own sentence repeated in the answer is not a claim.
        own = plain(words).strip()
        if len(own) >= 12:
            text = text.replace(own, " ")
        own_lower = unicodedata.normalize("NFC", words.lower().replace("’", "'"))
        if len(own_lower.strip()) >= 12:
            lower = lower.replace(own_lower.strip(), " ")
    computation = (
        ()
        if deeds.computed()
        else tuple(_claims(_COMPUTATION, text) + _claims(_COMPUTATION_ACCENTED, lower))
    )
    check = () if deeds.acted() else tuple(_claims(_CHECK, text))
    return Verdict(computation, check)


NOTES: dict[str, tuple[str, str]] = {
    "fr": (
        "Remarque : aucun outil n'a calculé les nombres de cette réponse. Ceux "
        "qui y sont présentés comme comptés ou calculés sont des estimations, "
        "pas un comptage vérifié.",
        "Remarque : aucun outil n'a rien exécuté ni vérifié pour cette réponse. "
        "Ce qu'elle dit avoir vérifié ou testé ne l'a pas été.",
    ),
    "en": (
        "Note: no tool computed the numbers in this answer. Those presented as "
        "counted or calculated are estimates, not a verified count.",
        "Note: no tool ran or checked anything for this answer. What it says "
        "was checked or tested was not.",
    ),
    "de": (
        "Hinweis: Kein Werkzeug hat die Zahlen dieser Antwort berechnet. Die als "
        "gezählt oder berechnet dargestellten Zahlen sind Schätzungen, keine "
        "geprüfte Zählung.",
        "Hinweis: Kein Werkzeug hat für diese Antwort etwas ausgeführt oder "
        "geprüft. Was als geprüft oder getestet bezeichnet wird, wurde es nicht.",
    ),
    "es": (
        "Nota: ninguna herramienta calculó las cifras de esta respuesta. Las "
        "presentadas como contadas o calculadas son estimaciones, no un recuento "
        "verificado.",
        "Nota: ninguna herramienta ejecutó ni comprobó nada para esta respuesta. "
        "Lo que dice haber comprobado o probado no lo fue.",
    ),
    "it": (
        "Nota: nessuno strumento ha calcolato i numeri di questa risposta. Quelli "
        "presentati come contati o calcolati sono stime, non un conteggio "
        "verificato.",
        "Nota: nessuno strumento ha eseguito o verificato nulla per questa "
        "risposta. Ciò che dice di aver verificato o testato non lo è stato.",
    ),
}


def note(verdict: Verdict, language: str) -> str:
    """The closing note for unbacked claims ("" when there is none)."""
    computation, check = NOTES.get(language, NOTES["en"])
    parts = []
    if verdict.computation:
        parts.append(computation)
    if verdict.check:
        parts.append(check)
    return "\n\n".join(parts)
