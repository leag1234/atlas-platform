"""Which picture a follow-up names: "in the second one", "keep image 3",
"la dernière", "auf dem zweiten Bild", "na segunda imagem".

A change asked for a picture already shown applies to that picture, never to
whichever one the conversation's artifact happened to hold, and never as a
new set drawn from scratch. Code reads the reference (a number, an ordinal or
"the last" attached to a picture word, or an ordinal standing for a picture),
in English, French, German, Spanish, Italian and Portuguese, then resolves it
against what the conversation showed:

- the candidates of the last answer that showed several (numbered 1 to n on
  its sheet): the named candidate becomes the artifact's current picture,
  exactly as a choice does (choice._apply);
- otherwise the separate pictures the conversation showed, in order: the
  named one becomes the conversation's current picture.

"That one", "the same drawing" and the like name the current picture, which
is already the one a change applies to. An ordinal that counts things inside
the picture ("make the second cat bigger") is not a reference: the word after
it is not a picture word. Two different pictures named at once (a combination)
are left to the ordinary reading.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from services.orchestrator.pictures import store

LOGGER = logging.getLogger(__name__)
# The answers a reference may point into (as dialogue.RECENT_ANSWERS).
RECENT_ANSWERS = 6
MAX_REFERENCE_CHARS = 600
LAST = 0  # the reference "the last one"

ORDINALS: dict[int, frozenset[str]] = {
    1: frozenset(
        "first 1st premier premiere 1er 1re 1ere erste ersten erstes erster "
        "erstem primero primera primer 1o 1a prima primo primeira primeiro".split()
    ),
    2: frozenset(
        "second 2nd deuxieme seconde second 2e 2eme 2nde zweite zweiten zweites "
        "zweiter zweitem segundo segunda 2o 2a seconda secondo".split()
    ),
    3: frozenset(
        "third 3rd troisieme 3e 3eme dritte dritten drittes dritter drittem "
        "tercero tercera tercer 3o 3a terza terzo terceira terceiro".split()
    ),
    4: frozenset(
        "fourth 4th quatrieme 4e 4eme vierte vierten viertes vierter viertem "
        "cuarto cuarta 4o 4a quarta quarto".split()
    ),
}
LAST_WORDS = frozenset(
    "last latest derniere dernier letzte letzten letztes letzter letztem "
    "ultima ultimo".split()
)
CARDINALS: dict[str, int] = {
    "1": 1, "2": 2, "3": 3, "4": 4,
    "one": 1, "un": 1, "une": 1, "eins": 1, "uno": 1, "una": 1, "um": 1,
    "two": 2, "deux": 2, "zwei": 2, "dos": 2, "due": 2, "dois": 2, "duas": 2,
    "three": 3, "trois": 3, "drei": 3, "tres": 3, "tre": 3,
    "four": 4, "quatre": 4, "vier": 4, "cuatro": 4, "quattro": 4, "quatro": 4,
}  # fmt: skip
# Cardinal words read right after a picture word without a number word
# ("picture two"): only these, since "une image un peu", "imagen tres"
# (an accented adverb once folded) say no number.
BARE_CARDINALS = frozenset({"one", "two", "three", "four", "zwei", "drei", "vier"})
PICTURE_WORDS = frozenset(
    """
    picture pictures image images img pic pics drawing drawings illustration
    proposal proposals option candidate sketch
    dessin dessins illustration proposition propositions
    bild bilder bildes zeichnung zeichnungen vorschlag vorschlags vorschlage
    variante entwurf
    imagen imagenes dibujo dibujos propuesta propuestas opcion
    immagine immagini disegno disegni proposta proposte opzione
    imagem imagens desenho desenhos opcao foto photo fotos photos
    """.split()
)
# "one" after an ordinal: "the second one", "the last ones".
ONE_WORDS = frozenset({"one", "ones"})
NUMBER_WORDS = frozenset({"number", "numero", "nummer", "nr", "no", "n", "num"})
# Words that may stand before an ordinal used alone for a picture.
LEADS = frozenset(
    """
    the in on of from for with keep take use
    la le l les dans sur de du sur garde gardez prends prenez reprends
    die der das den dem im am auf in vom von behalte nimm
    el en de del sobre con quedate usa toma
    il lo nella nel sulla sul della del dalla tieni usa prendi
    a o na no da do sobre fica usa pega mantem
    """.split()
)
# Words that may follow an ordinal used alone (a new clause begins).
AFTER = frozenset(
    """
    but and to make change keep with without
    mais et pour avec sans change mets rends enleve ajoute garde
    aber und mit ohne mach mache andere
    pero y con sin cambia pon haz quita
    ma e con senza cambia metti fai togli
    mas e com sem muda poe faz tira
    """.split()
)
SHOWN = re.compile(r"/images/picture/([0-9a-f]{32})/([1-4])\b")
_TOKEN = re.compile(r"[#a-z0-9]+|[.,;:!?()\[\]]")
_DIGIT_ORDINAL = re.compile(r"^([1-4])(?:st|nd|rd|th|e|eme|er|re|ere|nde|o|a)?$")


@dataclass(frozen=True)
class Reference:
    """A picture the message names: `number` 1 to 4, or LAST."""

    number: int


@dataclass(frozen=True)
class Target:
    """What a reference resolves to: a candidate of a set (index), or the
    current version of a set's artifact (index None)."""

    set_id: str
    index: int | None
    number: int
    count: int


def _tokens(message: str) -> list[str]:
    # "n°2" reads as "no 2" ("°" would be lost in the ASCII folding).
    message = message.replace("'", " ").replace("\u2019", " ").replace("°", "o ")
    folded = (
        unicodedata.normalize("NFKD", message)
        .encode("ascii", "ignore")
        .decode()
        .casefold()
    )
    return _TOKEN.findall(folded)


def _ordinal(word: str) -> int | None:
    if word in LAST_WORDS:
        return LAST
    for number, names in ORDINALS.items():
        if word in names:
            return number
    digit = _DIGIT_ORDINAL.match(word)
    if digit and word not in CARDINALS:
        return int(digit.group(1))
    return None


def _number_after(tokens: list[str], at: int) -> int | None:
    """A number written after a picture word: "image 2", "picture number
    two", "Bild Nr. 3", "proposition n°2", "image #4"."""
    index = at + 1
    while index < len(tokens) and tokens[index] in NUMBER_WORDS | {"."}:
        index += 1
    if index >= len(tokens):
        return None
    word = tokens[index].lstrip("#")
    if word in CARDINALS and (
        word.isdigit() or index > at + 1 or word in BARE_CARDINALS
    ):
        # Digits always; a number word after a number word ("number two");
        # a few unambiguous ones right after the picture word.
        return CARDINALS[word]
    ordinal = _ordinal(word)
    return ordinal if ordinal not in (None, LAST) else None


def referenced(message: str) -> Reference | None:
    """The picture a message names, or None (none, or two different ones)."""
    if not message.strip() or len(message) > MAX_REFERENCE_CHARS:
        return None
    tokens = _tokens(message)
    found: set[int] = set()
    for index, word in enumerate(tokens):
        before = tokens[index - 1] if index else ""
        after = tokens[index + 1] if index + 1 < len(tokens) else ""
        if word in PICTURE_WORDS:
            number = _number_after(tokens, index)
            if number is not None:
                found.add(number)
            continue
        if word.startswith("#") and word[1:] in CARDINALS and word[1:].isdigit():
            found.add(CARDINALS[word[1:]])
            continue
        ordinal = _ordinal(word)
        if ordinal is None:
            continue
        if after in PICTURE_WORDS or after in ONE_WORDS:
            found.add(ordinal)
        elif before in LEADS and (
            not after or not after[0].isalnum() or after in AFTER
        ):
            # An ordinal standing for a picture: "in the second, ...",
            # "keep the first but ...", "on the third:", "the last".
            found.add(ordinal)
    if len(found) != 1:
        return None
    return Reference(found.pop())


def _assistant_answers(messages: Sequence[Mapping[str, object]]) -> list[str]:
    return [
        str(message.get("content") or "")
        for message in messages
        if message.get("role") == "assistant"
        and isinstance(message.get("content"), str)
    ][-RECENT_ANSWERS:]


def _set_count(answer: str) -> tuple[str, int] | None:
    """(set id, candidates shown) of an answer that showed several."""
    numbers: dict[str, set[int]] = {}
    for match in SHOWN.finditer(answer):
        numbers.setdefault(match.group(1), set()).add(int(match.group(2)))
    if not numbers:
        return None
    set_id = list(numbers)[-1]
    count = len(numbers[set_id])
    return (set_id, count) if count > 1 else None


def _artifact_ids(answer: str) -> list[str]:
    from services.orchestrator.artifact_tools import HISTORY_REFERENCE_PATTERN

    return [match.group(1) for match in HISTORY_REFERENCE_PATTERN.finditer(answer)]


def resolve(
    reference: Reference,
    messages: Sequence[Mapping[str, object]],
    owner: str,
) -> Target | None:
    """The picture `reference` names in the conversation `messages` (the
    turns before the last message), when it is one of this owner's."""
    answers = _assistant_answers(messages)
    owner_hash = store.owner_hash(owner)
    for answer in reversed(answers):
        shown = _set_count(answer)
        if shown is None:
            continue
        set_id, count = shown
        picture_set = store.load(set_id)
        if picture_set is None or picture_set.owner != owner_hash:
            return None
        number = count if reference.number == LAST else reference.number
        return Target(set_id, number - 1, number, count)
    # No answer showed several candidates: the separate pictures shown, in order.
    sets: list[str] = []
    for answer in answers:
        for artifact_id in _artifact_ids(answer):
            picture_set = store.by_artifact(artifact_id)
            if (
                picture_set is not None
                and picture_set.owner == owner_hash
                and picture_set.set_id not in sets
            ):
                sets.append(picture_set.set_id)
    if len(sets) < 2:
        return None
    number = len(sets) if reference.number == LAST else reference.number
    if number > len(sets):
        return Target("", None, number, len(sets))
    return Target(sets[number - 1], None, number, len(sets))


def current_for(
    request: Any, current: Any, request_id: str
) -> tuple[Any, Target | None]:
    """The conversation's current artifact once the picture the last message
    names is made current, and that target; (current, None) when the
    message names none. A number beyond the pictures shown is returned as a
    target with an empty set id (the caller says which exist)."""
    from services.orchestrator.artifact_tools import current_artifact_message
    from services.orchestrator.pictures.choice import _apply, _artifact_link

    reference = referenced(str(getattr(request, "user_text", "") or ""))
    if reference is None:
        return current, None
    owner = str(getattr(request, "artifact_owner", "") or "")
    history = [{"role": m.role, "content": m.text} for m in request.messages[:-1]]
    target = resolve(reference, history, owner)
    if target is None:
        return current, None
    if not target.set_id or target.number > target.count:
        return current, Target("", target.index, target.number, target.count)
    picture_set = store.load(target.set_id)
    if picture_set is None or not picture_set.artifact_id:
        return current, None
    if target.index is not None:
        _apply(picture_set, target.index, owner, request_id)
    link = _artifact_link(picture_set, owner, picture_set.language)
    if link is None:
        return current, None
    made = current_artifact_message(
        [{"role": "assistant", "content": link[0]}, {"role": "user", "content": ""}],
        owner,
    )
    LOGGER.info(
        "picture_reference number=%d candidate=%s count=%d",
        target.number,
        target.index,
        target.count,
    )
    return (made if made is not None else current), target


async def handle_reference(
    request: Any, item: Any, current: Any, request_id: str
) -> tuple[Any, bool]:
    """(current artifact, answered): the picture the last message names made
    current; a number beyond the pictures shown is answered with the count
    (True), never applied to another picture."""
    import asyncio

    from services.orchestrator.pictures.texts import answer_language, text

    made, target = await asyncio.to_thread(current_for, request, current, request_id)
    if target is None:
        return current, False
    if not target.set_id:
        language = answer_language("", str(getattr(request, "lang", "") or ""))
        item.task_type = "drawing"
        item.reponse = text(
            language,
            "missing" if target.index is not None else "missing_shown",
            count=target.count,
            n=target.number,
        )
        item.state = "done"
        return current, True
    # The reading of the follow-up is told which picture was named.
    request.__dict__["picture_reference"] = target.number
    return made, False
