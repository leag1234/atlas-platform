"""What a model's answer may keep when its code came back identical.

A change request whose code equals the stored version stores nothing and
claims nothing (C2): the answer starts with the plain statement that no change
was made. It used to be only that sentence, which also deleted the answer to
what the person actually asked (a question, a complaint, a line to write under
the file). Now the answer keeps the sentences that do not claim a change, and
when none is left the statement says what the person can do (they describe
what they see).

A sentence claims a change when it says something was changed, fixed, added,
removed or is now otherwise ("now", "instead of"), or names a new version.
The stems cover English, French, German, Spanish and Italian.
"""

from __future__ import annotations

import re

# Stems, matched at the start of a word (accents and case are kept as typed).
CLAIM = re.compile(
    r"(?<![\w])(?:"
    # English
    r"chang|updat|modif|fix(?:ed|es)?\b|correct|adjust|replac|added\b"
    r"|remov|renam|now\b|instead of|no longer|new version|revis|improv|swapp"
    # French
    r"|corrig|mis(?:e|es)? à jour|ajout|ajust|remplac|supprim|renomm|désormais"
    r"|maintenant|au lieu de|ne \w+ plus|nouvelle version|amélior|rectifi"
    # German
    r"|geänder|ändere|angepasst|anpass|korrigier|aktualisier|ersetzt|hinzugefügt"
    r"|entfernt|umbenannt|jetzt\b|nun\b|statt\b|anstatt|nicht mehr|neue version"
    r"|verbessert|behoben"
    # Spanish
    r"|cambi|modific|corrig|correg|actualiz|ajust|reemplaz|añad|elimin|renombr|ahora\b"
    r"|en lugar de|en vez de|ya no\b|nueva versión|mejor|arregl"
    # Italian
    r"|cambi|modific|corrett|aggiorn|sistemat|sistemo|sistemata|sostitu|aggiunt|rimoss|rinomin"
    r"|adesso\b|ora\b|invece di|non più|nuova versione|miglior"
    r")",
    re.I,
)
_SENTENCES = re.compile(r"\S.*?(?:[.!?…]+(?=\s|$)|\Z)", re.S)
_VERBATIM = ("```", "|", "    ", "#")


def without_change_claims(text: str) -> str:
    """The text without the sentences that claim a change; code blocks, tables
    and headings stay as written. Empty when nothing else is left."""
    kept: list[str] = []
    for paragraph in text.split("\n\n"):
        if paragraph.lstrip().startswith(_VERBATIM):
            kept.append(paragraph)
            continue
        lines: list[str] = []
        for line in paragraph.split("\n"):
            sentences = [match.group(0) for match in _SENTENCES.finditer(line)]
            rest = " ".join(
                sentence for sentence in sentences if CLAIM.search(sentence) is None
            ).strip()
            if rest:
                lines.append(rest)
        if lines:
            kept.append("\n".join(lines))
    return "\n\n".join(kept).strip()
