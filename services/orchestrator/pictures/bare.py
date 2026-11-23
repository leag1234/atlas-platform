"""A picture shown alone when the person asks for the image and nothing else.

"Show just the image here, no title or text" was answered with an intro
sentence, a check note, choice links and the app footer (class suite,
staging -184), turn after turn, while the person asked three times for the
image alone. Every picture path writes its own lead and footer, so the
request is honoured once, on the finished answer: only its image lines stay.

The footer link is what the next turn reads to find the conversation's
picture (artifact_tools.history_references). It is kept, as the target of
the image itself: the image opens its version in the app panel when clicked,
and no link text is shown.
"""

from __future__ import annotations

import re
import unicodedata

# The person asks for the image alone, whatever the language.
_ALONE = re.compile(
    r"\b(?:juste|seulement|uniquement|rien que|que) (?:l'|la |cette |une )?image\b"
    r"|\bl'image (?:seule|toute seule|uniquement|seulement)\b"
    r"|\brien (?:d'autre|au-dessus|au dessus|en dessous|en-dessous|autour|avant|apres)\b"
    r"|\bsans (?:la |une |aucune |de )?(?:phrase|introduction|intro|legende|commentaire)s?\b"
    r"|\bsans (?:les |aucun |de )?liens?\b"
    r"|\b(?:just|only) (?:the |this |an? )?(?:image|picture|drawing)\b"
    r"|\b(?:image|picture|drawing) only\b|\bthe (?:image|picture) alone\b"
    r"|\bnothing (?:else|above|below|around|before|after)\b"
    r"|\bno (?:intro|introduction|caption|comment|sentence|links?)\b"
    r"|\bwithout (?:the |any )?(?:intro|introduction|caption|comment|sentence|links?)\b"
    r"|\bnur (?:das|ein) bild\b|\bdas bild allein\b"
    r"|\bnichts (?:anderes|sonst|daruber|darunter|davor|danach|drumherum)\b"
    r"|\bohne (?:den |die |einen |eine )?(?:satz|einleitung|kommentar|bildunterschrift|links?)\b"
    r"|\b(?:solo|solamente|unicamente) (?:la |una )?imagen\b|\bla imagen sola\b"
    r"|\bnada (?:mas|encima|debajo|alrededor|antes|despues)\b"
    r"|\bsin (?:la |una |ninguna )?(?:frase|introduccion|comentario|leyenda|enlaces?)\b"
    r"|\b(?:solo|soltanto|solamente) (?:l'|un'|una )?immagine\b|\bl'immagine da sola\b"
    r"|\bniente (?:altro|sopra|sotto|attorno|prima|dopo)\b"
    r"|\bsenza (?:la |una |alcuna )?(?:frase|introduzione|commento|didascalia|link)\b"
)
# "No text" is about the answer only when the image is to be shown here: in
# "a poster with no text" it is about the picture.
_NO_TEXT = re.compile(
    r"\bsans (?:titre ni |aucun )?texte\b|\bno text\b|\bwithout (?:any )?text\b"
    r"|\bohne text\b|\bsin texto\b|\bsenza testo\b"
)
_HERE = re.compile(
    r"\b(?:ici|directement|here|directly|hier|direkt|aqui|directamente|"
    r"direttamente)\b|\b(?:dans|in) (?:la conversation|the chat|this chat|den chat)\b"
)
_IMAGE_LINE = re.compile(r"^!\[[^\]\n]*\]\([^)\s]+\)$")
_OPEN = re.compile(
    r"\((/api/atlas/artifacts/[0-9a-f]{32}/versions/[0-9]{1,4}/open[^)\s]*)\)"
)


def _folded(text: str) -> str:
    text = text.replace("’", "'").replace("ʼ", "'")
    return " ".join(
        "".join(
            character
            for character in unicodedata.normalize("NFKD", text.casefold())
            if not unicodedata.combining(character)
        ).split()
    )


def image_only(request_text: str) -> bool:
    """True when the request asks for the picture with no text around it."""
    folded = _folded(request_text[:4000])
    if _ALONE.search(folded):
        return True
    return bool(_NO_TEXT.search(folded) and _HERE.search(folded))


# The plain leads of a picture answer: they say nothing the image does not.
_LEADS = ("single", "intro", "cpu_intro", "changed")
# Fixed paragraphs that only explain the links, dropped with them.
_HINTS = ("hint",)


def _stock() -> tuple[list[str], set[str]]:
    """The plain leads and link-only paragraphs, in every interface language
    and both registers: a follow-up answers in the picture's own language."""
    from services.orchestrator.artifact_tools import FOOTER_LABELS
    from services.orchestrator.pictures.texts import LANGUAGES, TEXTS, text

    leads: set[str] = set()
    paragraphs: set[str] = set()
    for language in LANGUAGES:
        table = TEXTS.get(language, TEXTS["en"])
        for n in range(1, 10):
            for key in _LEADS:
                leads.add(table[key].format(n=n))
                leads.add(text(language, key, n=n))
        for key in _HINTS:
            paragraphs.update((table[key], text(language, key)))
        labels = FOOTER_LABELS.get(language, FOOTER_LABELS["en"])
        paragraphs.add(labels["temporary"])
    return sorted(leads, key=len, reverse=True), {
        " ".join(item.split()) for item in paragraphs
    }


def bare(answer: str) -> str:
    """The answer reduced to its images, each opening its stored version.

    The plain lead, the choice links, their hint and the app footer go. What
    the turn says about the result itself (a check that found something
    missing, a new draw instead of a correction) stays, after the image: a
    request for the image alone never hides that it is not what was asked.
    An answer without an image (a failure, a question) is returned unchanged.
    """
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", answer) if part.strip()]
    images = [part for part in paragraphs if _IMAGE_LINE.match(part)]
    if not images:
        return answer
    leads, dropped = _stock()
    kept: list[str] = []
    for part in paragraphs:
        if part in images or "](" in part or " ".join(part.split()) in dropped:
            continue
        for lead in leads:
            part = part.replace(lead, "")
        part = " ".join(part.split())
        if part:
            kept.append(part)
    opened = _OPEN.search(answer)
    shown = (
        images
        if opened is None
        else [f"[{image}]({opened.group(1)})" for image in images]
    )
    return "\n\n".join(shown + kept)
