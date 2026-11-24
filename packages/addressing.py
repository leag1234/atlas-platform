"""How the text the code writes addresses the person: the conversation's register.

A conversation in the familiar form (French and Italian tu, Spanish tu, German
du) read code-written text in the polite one: the closing note of a trip that
asks to check the times with the operator, the last step of a plan card (hand
you the app), retry sentences (send your message again) (staging -145,
class C13). Every service table is written in the polite form, the safe default; a
turn whose person writes in the familiar form reads it in that form.

- ``register`` reads how one text addresses the person (moved here from
  services/orchestrator/transit_planner.py, where it was added for the trip
  block): "informal", "formal", or "" when the text does not say.
- ``begin`` records the person's own words for the turn (latest message first),
  with the answer language; ``turn_register`` is the first register they say.
- ``addressed`` writes a service text in the familiar form when the turn's
  register is informal, phrase by phrase from FAMILIAR; an unsure register
  keeps the text as written (polite or neutral).

English has one form. Italian service text is mostly written with "tu"
already; its polite phrases are listed like the others.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from contextvars import ContextVar, Token
from typing import Any

# French words before "moi" that end like a tu imperative but are not one
# (folded).
_NOT_IMPERATIVE = (
    "comme que entre contre jusque lorsque puisque presque quelque sans dans pas "
    "plus moins sous mais puis apres vers hors alors toujours jamais fois ainsi "
    "aussi nous vous les des ces mes ses tes une elle elles ils dites faites "
    "toutes tous autres"
).split()
# How a text addresses the person, per language: informal and formal marks
# (folded text, except German and Italian formal pronouns, capitalised).
_FAMILIAR = {
    # French: a pronoun, or an imperative in the tu form before "moi"
    # ("trouve-moi les horaires", staging -151; the hyphen is folded away),
    # not a word that ends the same way ("comme moi", "pas moi", "chez moi").
    "fr": re.compile(
        r"\b(?:tu|toi|ton|ta|tes|te|t)\b|\b(?!(?:" + "|".join(_NOT_IMPERATIVE) + r")\b)"
        r"[a-z]{2,}[es] moi\b"
    ),
    "es": re.compile(
        r"\b(?:tu|tus|ti|contigo|te|puedes|podrias|quieres|tienes|necesitas|sabes)\b"
    ),
    "de": re.compile(r"\b(?:du|dich|dir|dein|deine|deinen|deinem|deiner|deines)\b"),
    "it": re.compile(r"\b(?:tu|ti|tuo|tua|tuoi|tue|puoi|vuoi|devi)\b"),
}
_POLITE = {
    "fr": (
        re.compile(
            r"\b(?:vous|votre|vos)\b|\b(?:dites|faites)\b moi\b"
            r"|\b(?!chez\b|assez\b)[a-z]{2,}ez moi\b"
        ),
        False,
    ),
    "es": (re.compile(r"\b(?:usted|ustedes)\b"), False),
    "de": (
        re.compile(
            r"\b(?:Ihnen|Ihr|Ihre|Ihren|Ihrem|Ihrer|Ihres)\b|(?<![.!?:\n]\s)(?<!^)\bSie\b"
        ),
        True,
    ),
    "it": (re.compile(r"\b(?:Lei|Le consiglio|La invito)\b"), True),
}
# A request that opens with an imperative says its register by the verb's
# form alone: "dessine six ballons" is the familiar form, "dessinez" the polite
# one (staging -187: a picture request with no pronoun was answered in the
# polite form). Common request verbs, folded; the polite French forms of -er
# verbs are derived (dessine -> dessinez). Nouns that read like one of these
# verbs at the start of a message (liste, garde, aide, resume) are left out.
_FR_TU_ER = (
    "dessine redessine ajoute montre donne trouve enleve retire corrige "
    "explique calcule cherche prepare redige genere colorie imagine invente "
    "raconte envoie remplace compare decris transforme organise planifie "
    "recommande verifie regarde ecoute cree modifie supprime"
).split()
_LEADING = {
    "fr": (
        set(_FR_TU_ER)
        | set(
            "fais refais mets remets ecris prends rends dis peins traduis "
            "choisis convertis lis reecris".split()
        ),
        {w[:-1] + "ez" for w in _FR_TU_ER}
        | set(
            "faites refaites mettez remettez ecrivez prenez rendez dites peignez "
            "traduisez choisissez convertissez lisez reecrivez".split()
        ),
    ),
    "es": (
        set(
            "dibuja haz pon escribe crea muestra dame dime explica traduce calcula "
            "busca prepara genera pinta cuenta envia cambia anade quita corrige "
            "compara recomienda elige revisa lee redacta".split()
        ),
        set(
            "dibuje haga ponga escriba muestre deme digame explique traduzca "
            "calcule busque prepare genere pinte cuente envie cambie anada quite "
            "corrija compare recomiende elija revise lea redacte".split()
        ),
    ),
    "it": (
        set(
            "disegna fai metti scrivi crea mostra dammi dimmi spiega traduci "
            "calcola cerca prepara genera dipingi racconta invia cambia aggiungi "
            "togli correggi confronta scegli controlla leggi".split()
        ),
        set(
            "faccia metta scriva crei mostri spieghi traduca calcoli cerchi "
            "prepari generi dipinga racconti invii aggiunga tolga corregga "
            "confronti scelga controlli legga".split()
        ),
    ),
    "de": (
        set(
            "zeichne mal male mach mache schreib schreibe erstelle zeig zeige gib "
            "sag erklar erklare ubersetze berechne such suche erzahl erzahle schick "
            "schicke andere aendere fug fuge entferne korrigiere vergleiche wahle "
            "prufe lies bau baue".split()
        ),
        set(),
    ),
}
# Words a message may open with before its verb: greetings and "please".
_OPENING = set(
    "salut bonjour bonsoir coucou hello hi hey stp svp hola buenas porfa ciao "
    "salve hallo servus moin bitte".split()
)


def leading_register(text: str, language: str) -> str:
    """The register the opening imperative of ``text`` says, or ""."""
    forms = _LEADING.get(language)
    if forms is None:
        return ""
    words = [w for w in _folded(text).split()[:4]]
    while words and words[0] in _OPENING:
        words.pop(0)
    if not words:
        return ""
    familiar, polite = forms
    if words[0] in familiar:
        return "informal"
    if words[0] in polite:
        return "formal"
    return ""


# Words that read as a familiar mark once folded but are not one: the French
# euphonic t of "y a-t-il", the Spanish word for tea (audit 2026-11-21).
_NOT_FAMILIAR = {
    "fr": re.compile(r"-t-(?=(?:il|elle|on|ils|elles)\b)", re.I),
    "es": re.compile(r"\bté\b", re.I),
}


def _folded(text: str) -> str:
    """Lower case without accents or punctuation, single spaces."""
    plain = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(
        c if c.isalnum() else " " for c in plain if not unicodedata.combining(c)
    )
    return " ".join(plain.split())


def register(text: str, language: str) -> str:
    """How ``text`` addresses the person: "informal", "formal", or "" when it
    does not say (no mark, or both)."""
    familiar = _FAMILIAR.get(language)
    polite = _POLITE.get(language)
    if familiar is None or polite is None or not text.strip():
        return ""
    pattern, cased = polite
    marks = _NOT_FAMILIAR.get(language)
    informal = bool(familiar.search(_folded(marks.sub(" ", text) if marks else text)))
    formal = bool(pattern.search(text if cased else _folded(text)))
    leading = leading_register(text, language)
    informal = informal or leading == "informal"
    formal = formal or leading == "formal"
    if informal == formal:
        return ""
    return "informal" if informal else "formal"


# The person's own words in this turn, latest message first, and the answer
# language. Set per request (chat_stream, chat_api, chat_pipeline.process).
_person: ContextVar[tuple[tuple[str, ...], str]] = ContextVar(
    "atlas_person_words", default=((), "")
)


def begin(texts: Iterable[str], language: str = "") -> Token[Any]:
    """Record the person's messages of the turn, oldest first as sent; the
    text they pasted is not their own words (packages.pasted_text)."""
    from packages.pasted_text import own_words

    words = tuple(own_words(str(text)) for text in texts if str(text).strip())
    return _person.set((tuple(reversed(words)), language))


def end(token: Token[Any]) -> None:
    _person.reset(token)


def person_words() -> tuple[str, ...]:
    """The person's own words in this turn, latest message first."""
    return _person.get()[0]


def answer_language(default: str = "en") -> str:
    return _person.get()[1] or default


def turn_register(language: str) -> str:
    """The register of the person's latest message that says one, else ""."""
    for text in person_words():
        found = register(text, language)
        if found:
            return found
    return ""


# How an instruction to the answer model names each register, per language:
# the familiar form, then the polite one.
ADDRESS_FORMS = {
    "fr": ("tu", "vous"),
    "es": ("t\u00fa", "usted"),
    "de": ("du", "Sie"),
    "it": ("tu", "Lei"),
}


def address_instruction(language: str, found: str | None = None) -> str:
    """The sentence of a model instruction that names the person's register.

    The answer model writes its own prose in the register it likes, whatever
    the person's: "salut ! tu peux me faire une petite appli" was answered
    "Voici votre appli ... vous propose" under a plan card that said "Te
    livrer" (staging -177). Every answer path tells the model, as the
    trip instruction did first (staging -155): ``answer_instruction``, which
    they append, carries it. ``found`` is the register when
    the caller knows it, else the turn's (``turn_register``); "" when the
    language has one form or the person's words do not say.
    """
    forms = ADDRESS_FORMS.get(language)
    if not forms:
        return ""
    found = turn_register(language) if found is None else found
    if found not in {"informal", "formal"}:
        return ""
    own, other = forms if found == "informal" else forms[::-1]
    return (
        f" The person addresses you with '{own}': in every sentence you write to "
        f"them, address them with '{own}', never '{other}'."
    )


def answer_instruction(language: str) -> str:
    """The prose language instruction (packages.language) with the person's
    register when their words say one: what every answer path appends to its
    system prompt (apps, documents, decks, research, chat; staging -177).
    packages.language stays standalone (it is copied alone into the chat UI
    image), so the register is added here."""
    from packages.language import language_instruction

    stated = language_instruction(language)
    address = address_instruction(language)
    return stated.rstrip("\n") + "\n" + address.strip() + "\n" if address else stated


# The polite phrases of service text and their familiar form, per language.
# A phrase written with a lower-case first letter matches either case and keeps
# the case it found (a capital only where a sentence starts: elsewhere it is a
# name); one written capitalised (German Ihr, Ihnen) matches as written and is
# capitalised only where a sentence starts. Longer phrases are
# applied first. tests/test_register_and_markers.py checks that every polite
# phrase of the service tables has its familiar form here.
FAMILIAR: dict[str, tuple[tuple[str, str], ...]] = {
    "fr": (
        ("je ne vous la donne", "je ne te la donne"),
        ("que vous avez autorisée", "que tu as autorisée"),
        ("indiquée par vous", "indiquée par toi"),
        ("indiqué par vous", "indiqué par toi"),
        ("si vous pouvez", "si tu peux"),
        ("avant de vous fier", "avant de te fier"),
        ("vous livrer", "te livrer"),
        ("vous donner", "te donner"),
        ("votre message", "ton message"),
        ("votre demande", "ta demande"),
        ("votre position", "ta position"),
        ("votre jour", "ton jour"),
        ("votre premier clic", "ton premier clic"),
        ("votre approbation", "ton approbation"),
        ("votre machine", "ta machine"),
        ("votre temps de trajet", "ton temps de trajet"),
        ("vérifiez-y", "vérifies-y"),
        ("réessayez", "réessaie"),
        ("renvoyez", "renvoie"),
        ("posez", "pose"),
        ("découpez", "découpe"),
        ("ouvrez", "ouvre"),
        ("demandez", "demande"),
        ("précisez", "précise"),
        ("vérifiez", "vérifie"),
        ("prévoyez", "prévois"),
        ("donnez-moi", "donne-moi"),
    ),
    "de": (
        ("versuchen Sie es", "versuch es"),
        ("teilen Sie", "teile"),
        ("stellen Sie", "stell"),
        ("senden Sie Ihre Nachricht", "sende deine Nachricht"),
        ("senden Sie sie", "sende sie"),
        ("beginnen Sie", "beginne"),
        ("bitten Sie", "bitte"),
        ("präzisieren Sie", "präzisiere"),
        ("fragen Sie", "frag"),
        ("planen Sie", "plane"),
        ("prüfen Sie", "prüfe"),
        ("nennen Sie mir Ihre", "nenn mir deine"),
        ("bevor Sie die App teilen", "bevor du die App teilst"),
        ("wenn Sie nachts reisen können", "wenn du nachts reisen kannst"),
        ("von Ihnen angegeben", "von dir angegeben"),
        ("ein von Ihnen erlaubter", "ein von dir erlaubter"),
        ("auf Ihre Freigabe", "auf deine Freigabe"),
        ("Ihre Nachricht", "deine Nachricht"),
        ("Ihrem ersten Klick", "deinem ersten Klick"),
        ("Ihre Maschine", "deine Maschine"),
        ("Ihres Tages", "deines Tages"),
        ("Ihr Standort", "dein Standort"),
        ("Ihnen die App", "dir die App"),
        ("Ihnen das Ergebnis", "dir das Ergebnis"),
        ("Ihnen die Datei", "dir die Datei"),
    ),
    "es": (
        ("inténtelo", "inténtalo"),
        ("haga", "haz"),
        ("vuelva a", "vuelve a"),
        ("divida", "divide"),
        ("su mensaje", "tu mensaje"),
        ("lo que pidi\u00f3", "lo que pediste"),
        ("envíela", "envíala"),
        ("envíe", "envía"),
        ("pida", "pide"),
        ("precise", "precisa"),
        ("abra", "abre"),
        ("si lo desea", "si quieres"),
        ("indicada por usted", "indicada por ti"),
        ("indicado por usted", "indicado por ti"),
        ("compruébelas", "compruébalas"),
        ("compruébelo", "compruébalo"),
        ("compruebe", "comprueba"),
        ("téngalo en cuenta", "tenlo en cuenta"),
        ("dígame su tiempo", "dime tu tiempo"),
        ("su posición", "tu posición"),
        ("su máquina", "tu máquina"),
        ("un comando que usted autorizó", "un comando que autorizaste"),
        ("su primer clic", "tu primer clic"),
        ("de su día", "de tu día"),
        ("entregarle", "entregarte"),
        ("darle", "darte"),
        ("si puede viajar", "si puedes viajar"),
        ("antes de fiarse", "antes de fiarte"),
    ),
    "it": (
        ("consegnarle", "consegnarti"),
        ("darle", "darti"),
        ("la verifichi", "verificala"),
        ("mi dica il suo tempo", "dimmi il tuo tempo"),
        ("la sua posizione", "la tua posizione"),
        ("controlli linee", "controlla linee"),
        (
            "controlli lì gli orari del suo giorno",
            "controlla lì gli orari del tuo giorno",
        ),
        (
            "controlli il viaggio completo e gli orari del suo giorno",
            "controlla il viaggio completo e gli orari del tuo giorno",
        ),
        ("ne tenga conto e lo controlli", "tienine conto e controllalo"),
        ("se può viaggiare", "se puoi viaggiare"),
    ),
}
_SENTENCE_START = re.compile(r"(?:[.!?:]\s+|\n\s*(?:[-*]\s+)?)$")


def _capitalised(text: str) -> str:
    return text[:1].upper() + text[1:]


def _compiled(
    pairs: Iterable[tuple[str, str]],
) -> list[tuple[re.Pattern[str], str, bool]]:
    rules = []
    for formal, informal in sorted(pairs, key=lambda pair: -len(pair[0])):
        follows_case = formal[:1].islower()
        head = (
            f"[{re.escape(formal[0])}{re.escape(formal[0].upper())}]"
            if follows_case
            else re.escape(formal[0])
        )
        pattern = re.compile(r"(?<!\w)" + head + re.escape(formal[1:]) + r"(?!\w)")
        rules.append((pattern, informal, follows_case))
    return rules


_RULES = {language: _compiled(pairs) for language, pairs in FAMILIAR.items()}


def familiar(text: str, language: str, extra: Iterable[tuple[str, str]] = ()) -> str:
    """``text`` with its polite phrases in the familiar form."""
    extra = tuple(extra)
    rules = (
        _compiled(FAMILIAR.get(language, ()) + extra)
        if extra
        else _RULES.get(language, [])
    )
    for pattern, informal, follows_case in rules:

        def replace(
            match: re.Match[str], informal: str = informal, follows: bool = follows_case
        ) -> str:
            found = match[0]
            before = match.string[max(0, match.start() - 8) : match.start()]
            starts = not match.string[: match.start()].strip() or bool(
                _SENTENCE_START.search(before)
            )
            if follows and found[:1].isupper():
                # A capital inside a sentence is a name ("Haga" in Stockholm),
                # not the verb of the table.
                return _capitalised(informal) if starts else found
            return _capitalised(informal) if starts and not follows else informal

        text = pattern.sub(replace, text)
    return text


def addressed(
    text: str,
    language: str,
    found: str | None = None,
    extra: Iterable[tuple[str, str]] = (),
) -> str:
    """A service text in the register of the turn (``found`` when the caller
    knows it): familiar when the person writes so, else as written."""
    if not text or language not in FAMILIAR:
        return text
    if (found if found is not None else turn_register(language)) != "informal":
        return text
    return familiar(text, language, extra)


def addressed_table(table: Mapping[str, Any], language: str) -> dict[str, Any]:
    """A table of service texts (key to text) in the register of the turn."""
    found = turn_register(language)
    return {
        key: addressed(value, language, found) if isinstance(value, str) else value
        for key, value in table.items()
    }
