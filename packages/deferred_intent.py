"""A request that asks to see options in the conversation before any file.

"Propose two outlines here first and wait for my choice before creating the
deck" asks for text now and a file later. The router took the file named in
that sentence as the deliverable of this turn: the deck was created twice
before the outlines were ever shown (class suite, staging -184). The words
that defer the creation are reliable in every interface language, so code
decides: such a turn is answered in the conversation and creates no file.

Two signals count. A wait for the person's choice, approval or answer before
something is made ("wait for my choice", "attendez que je choisisse",
"warte auf meine Auswahl"), or a refusal of any file for now ("don't create
anything yet", "ne créez pas de fichier avant"). Or a request to propose or
show something first, placed before the creation ("before you create the
file, show me two options"). A plain "before creating the file, check the
figures" is a sequence of work and is not deferred.
"""

from __future__ import annotations

import re
import unicodedata

_MAKE = (
    r"(?:cre|fai|fabriqu|gener|produi|prepar|redig|lanc|constru|"
    r"make|creat|generat|build|produc|writ|draft|"
    r"erstell|anleg|mach|generier|baue|schreib|"
    r"crea|hac|haz|gener|elabor|"
    r"far|genera|prepara|realizz)"
)
# Something waits for the person before anything is made.
_WAIT = re.compile(
    r"\b(?:attend(?:s|ez|re)?|patiente[rz]?)\b.{0,40}?"
    r"\b(?:que (?:je|j')|mon |ma |mes |d'avoir)"
    r"|\bavant (?:que (?:je|j')|d'avoir) (?:aie |ai )?(?:choisi|valide|decide|repondu|confirme)"
    r"|\bwait\b.{0,20}?\b(?:for (?:my|me)|until i|till i)\b"
    r"|\b(?:until|before) i(?: have| ve|'ve)? (?:choose|chosen|pick|picked|decide|decided|"
    r"approve|approved|confirm|confirmed|say so|answer)"
    r"|\bwarte(?:n sie|t)?\b.{0,30}?\b(?:auf meine|auf mein|bis ich)\b"
    r"|\bbevor ich\b.{0,30}?\b(?:gewahlt|ausgewahlt|entschieden|zugestimmt|bestatigt|wahle|entscheide)"
    r"|\bespera(?:d|r|me)?\b.{0,30}?\b(?:a que (?:yo )?|mi |hasta que)"
    r"|\bantes de que (?:yo )?(?:elija|decida|confirme|apruebe)"
    r"|\baspett(?:a|i|ate|are)\b.{0,30}?\b(?:che (?:io )?|la mia |il mio |mia )"
    r"|\bprima che (?:io )?(?:scelga|decida|confermi|approvi)"
)
# No file for now.
_NOT_YET = re.compile(
    # Possessive runs (tools/regex_lint.py): the words between "ne" and the
    # negation are spelled out, never skipped over with a free repeat.
    r"\bne\s++(?:(?:me|m'|nous|le|la|les|l'|en|y)\s*+){0,2}+"
    r"(?:cree[sz]?|fai(?:s|tes)|genere[sz]?|produi(?:s|sez)|lance[sz]?)\s++"
    r"(?:(?:plus|encore)\s++)?+(?:pas|rien|aucun)\b.{0,60}?\b(?:avant|tant que|encore)\b"
    r"|\b(?:don't|do not|dont)\s+(?:create|make|generate|build|produce|write)\b.{0,60}?"
    r"\b(?:yet|until|before)\b"
    r"|\b(?:erstell|mach|generier)(?:e|en)?\s++(?:(?:sie|du)\s++)?+(?:noch\s++)?+(?:keine?n?|nichts)\b.{0,60}?"
    r"\b(?:bevor|bis|noch)\b"
    r"|\bno (?:crees|cree|creeis|hagas|haga|generes)\b.{0,60}?\b(?:todavia|aun|hasta|antes)\b"
    r"|\bnon (?:creare|crear|fare|generare|create|fate)\b.{0,60}?\b(?:ancora|prima|finche)\b"
)
# Proposing or showing something first, before the creation.
_FIRST = re.compile(
    r"\b(?:avant de|before(?: you)?|bevor (?:sie|du)?|antes de|prima di)\s*"
    + _MAKE
    + r"\w*\b.{0,120}?\b(?:propos|montr|soumet|donn|present|"
    r"show|propos|suggest|give|list|outline|"
    r"zeig|schlag|vorschlag|gib|nenn|"
    r"muestr|propon|ensen|dame|"
    r"mostr|propon|suggeri|dammi)"
    r"|\b(?:propos|montr|soumet|present)\w*(?:-moi| moi)?\b.{0,80}?\b(?:d'abord|en premier|ici)\b"
    r".{0,120}?\bavant\b"
    r"|\b(?:show|propose|suggest|give|list)\b.{0,80}?\b(?:first|here)\b.{0,120}?\bbefore\b"
    r"|\b(?:zeig|schlag|gib|nenn)\w*\b.{0,80}?\b(?:zuerst|erst|hier)\b.{0,120}?\bbevor\b"
    r"|\b(?:muestr|propon|ensen|da)\w*\b.{0,80}?\b(?:primero|aqui)\b.{0,120}?\bantes\b"
    r"|\b(?:mostr|propon|suggeris|dammi)\w*\b.{0,80}?\b(?:prima|qui)\b.{0,120}?\bprima di\b"
)


_THEN = re.compile(
    r"\b(?:puis|ensuite|apres quoi|then|and then|dann|danach|anschliessend|"
    r"luego|despues|y despues|poi|quindi|dopo)\b"
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


def creation_deferred(text: str) -> bool:
    """True when the request defers any file until the person has chosen."""
    folded = _folded(text[:4000])
    if _WAIT.search(folded) or _NOT_YET.search(folded):
        return True
    first = _FIRST.search(folded)
    # "Before writing it, list the sources, then write the report": one turn.
    return first is not None and not _THEN.search(folded, first.end())
