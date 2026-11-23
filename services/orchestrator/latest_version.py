"""A "latest version" answer names the newest version the pages read give.

Staging -176 (2026-11-22): asked for the latest stable Python, the answer read
a maintenance announcement listing 3.10.22 to 3.14.8 and called 3.14.8 the
latest, while the project's download page gave 3.15.2 (-174 had answered
3.15.2 from that page). Which version the answer named depended on which page
the model read first, and "the highest number in this announcement" was taken
for "the latest release".

Three code steps, generic (semantic version numbers, no product list):

1. ``ranked``: for a question about the latest, current or newest version or
   release of something, search results whose address is the subject's own
   download or release page come first ("python.org/downloads",
   "github.com/<owner>/<project>/releases"), an index page before the page of
   one release; the other results keep their order.
2. ``hint``: after each search or page read, the versions of the subject that
   the pages read give (titles, snippets, fetched texts) are compared; when
   they differ, the tool result tells the model the newest stable one and the
   page that gives it.
3. ``note``: when the answer names, as its first version of the subject, one
   lower than the newest stable version a page read gives, and never names
   that one, a closing note in the answer's language states the newer version
   and its page.

A version belongs to the subject when the subject's name stands just before it
("Python 3.15.2", "Zephyr v4.2") or when it continues such a list ("Python
3.10.22, 3.11.17 and 3.14.8"). Pre-releases (a1, b2, rc1, beta, preview,
nightly) and versions announced as future or in development are not stable
releases. A question that asks for a branch ("latest Python 3.13"), a
long-term or pre-release channel (LTS, ESR, beta) is left as it is: the newest
version overall is not its answer.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from services.orchestrator.search_freshness import STOP, fold

# "latest", "current", "newest", "most recent", in the five answer languages.
_LATEST = re.compile(
    r"\b(?:latest|newest|current(?:ly)?|most recent|up to date|last"
    r"|derniere?s?|actuelles?|actuels?|plus recente?s?|a jour"
    r"|neueste[nrs]?|aktuelle[nrs]?|letzte[nrs]?|juengste[nrs]?"
    r"|ultim[oa]s?|mas recientes?|actual(?:es)?|vigentes?"
    r"|piu recenti?|attuali?|corrente)\b"
)
# What is versioned: a version, a release, an update, a build.
_VERSIONED = re.compile(
    r"\b(?:versions?|releases?|released|stable|updates?|builds?|patch"
    r"|sortie|mise a jour|publiee?|parution"
    r"|fassung|ausgabe|veroffentlichung|veroeffentlichung"
    r"|version(?:es)?|lanzamiento|actualizacion"
    r"|versione|versioni|rilascio|aggiornamento)\b"
)
# A channel whose newest version is not the newest overall.
_CHANNEL = re.compile(
    r"\b(?:lts|esr|long[- ]term|extended support|beta|alpha|rc|preview|nightly"
    r"|canary|insider|pre-?release|dev(?:elopment)? (?:version|build|release)"
    r"|support a long terme|langzeit\w*|largo plazo|lungo termine)\b"
)
# Words of the question that are not its subject.
_NOT_SUBJECT = frozenset(
    """
    what which who when where how is are was the latest newest current currently
    most recent last up date stable version versions release releases released
    update updates build builds patch available today now new please tell me
    give find check official
    quelle quel quels quelles qui quand ou comment est sont c'est derniere dernier
    derniers dernieres actuelle actuel actuels actuelles plus recente recent jour
    sortie publiee publie mise parution stable officielle officiel aujourd hui
    disponible stp svp peux tu dire donne trouve
    welche welcher welches was wer wann wo wie ist sind neueste neuesten neuester
    aktuelle aktuellen aktueller letzte letzten stabile fassung ausgabe
    veroffentlichung heute gibt es von vom welchen
    cual cuales que quien cuando donde como es son ultima ultimo ultimas ultimos
    mas reciente actual vigente estable lanzamiento actualizacion hoy dime
    qual quale quali chi quando dove come e sono ultima ultimo ultime ultimi piu
    attuale corrente stabile versione rilascio aggiornamento oggi dimmi
    """.split()
)
_WORD = re.compile(r"[\w][\w.+#-]*[\w+#]|[\w]")
# A version number: two to four numeric parts, an optional "v", an optional
# pre-release tag. Dates ("22.10.2026") have a year after the first part.
_VERSION = re.compile(
    r"(?<![\w.])[vV]?(?P<number>\d{1,4}(?:\.\d{1,3}){1,3})"
    r"(?P<tag>(?:[-.~+]?(?:a|b|c|rc|alpha|beta|dev|pre|preview|snapshot|nightly"
    r"|canary)\.?\d*)(?![a-z]))?(?!\.?\d|[\w-]*\d)"
)
# Between the subject's name and its version: marks and a few words that
# only say "version" ("Python 3.15", "Python version 3.15", "Python, v3.15",
# "Python est la 3.15"); never another name ("Python and Node 20.1").
_LEAD_WORDS = (
    r"version|versions|release|stable|ver|is|was|the|now|"
    r"est|la|le|en|ist|die|der|es|el|e|il|versione|fassung|versão"
)
_LEAD = re.compile(
    r"^[ \t*_`'\"«»:,()-]*(?:(?:" + _LEAD_WORDS + r")[ \t*_`'\"«»:,()-]+){0,3}$",
    re.IGNORECASE,
)
# Between two versions of one list. Possessive: each mark run is read once.
_LIST_GAP = re.compile(
    r"^[ \t*_`]*+(?:[,/&;]|oder|and|und|or|ou|et|ed|e|y|o)?+[ \t*_`]*+"
    r"(?:and|und|or|ou|et|ed|e|y)?+[ \t*_`]*+[vV]?$"
)
# A version announced, not released: around it in its sentence.
_FUTURE = re.compile(
    r"(?i)\b(?:upcoming|future|planned|schedules?|scheduled|expected|will be|coming"
    r"|roadmap|calendrier|zeitplan|calendario|cronograma"
    r"|in development|under development|development|pre-?releases?|candidate"
    r"|prochaine?|futur[e]?|prevue?|a venir|en developpement|developpement"
    r"|geplant|kommende|zukunftig\w*|entwicklung\w*"
    r"|proxim[oa]|futur[oa]|previst[oa]|en desarrollo|desarrollo"
    r"|prossim[oa]|in sviluppo|sviluppo)\b"
)
_RELEASE_SEGMENTS = frozenset(
    {
        "download",
        "downloads",
        "release",
        "releases",
        "release-notes",
        "releasenotes",
        "changelog",
        "changes",
        "news",
        "telechargement",
        "telecharger",
        "herunterladen",
        "descargas",
        "descarga",
        "scarica",
    }
)


@dataclass(frozen=True)
class Version:
    number: tuple[int, ...]
    text: str
    name: str
    url: str


def asks_latest(question: str) -> bool:
    """A question about the newest version or release of something."""
    folded = fold(question)
    return bool(_LATEST.search(folded) and _VERSIONED.search(folded))


def _branch(question: str) -> tuple[int, ...] | None:
    found = _VERSION.search(question)
    return _parse(found["number"]) if found else None


def checked(question: str) -> bool:
    """A latest-version question whose answer is the newest stable version."""
    folded = fold(question)
    return (
        asks_latest(question)
        and not _CHANNEL.search(folded)
        and _branch(question) is None
        and bool(subject(question))
    )


def subject(question: str) -> list[str]:
    """The words naming what is versioned ("python", "node.js")."""
    folded = fold(question).replace("’", "'")
    stops = frozenset().union(*STOP.values()) | _NOT_SUBJECT
    words: list[str] = []
    for word in _WORD.findall(folded):
        word = word.strip("'")
        if "'" in word:
            word = word.split("'", 1)[1]
        if (
            len(word) < 2
            or word in stops
            or not any(c.isalpha() for c in word)
            or _VERSION.fullmatch(word)
        ):
            continue
        if word not in words:
            words.append(word)
    return words


def _parse(number: str) -> tuple[int, ...]:
    return tuple(int(part) for part in number.split("."))


def _plain(text: str) -> str:
    """Link targets out, so a URL's digits are never read as a version."""
    text = re.sub(r"\]\([^)\s]*\)", "]", text)
    return re.sub(r"https?://\S+", " ", text)


def versions(text: str, names: list[str], url: str = "") -> list[Version]:
    """The stable versions of the subject that text gives, in text order."""
    if not names:
        return []
    text = _plain(text)
    folded = fold(text)
    if len(folded) != len(text):
        # Folding changed the length (a ligature): read the folded text.
        text = folded
    pattern = re.compile(
        r"(?<![\w.])(?:" + "|".join(re.escape(name) for name in names) + r")(?![\w])"
    )
    mentions = [(match.start(), match.end()) for match in pattern.finditer(folded)]
    found: list[Version] = []
    previous_end = -1
    previous_related = False
    for match in _VERSION.finditer(text):
        start = match.start()
        related = False
        name = ""
        if previous_related and _LIST_GAP.match(text[previous_end:start]):
            related, name = True, found[-1].name if found else ""
        else:
            for mention_start, mention_end in reversed(mentions):
                if mention_end > start:
                    continue
                if start - mention_end > 40:
                    break
                if _LEAD.match(text[mention_end:start]):
                    related, name = True, text[mention_start:mention_end]
                break
        previous_end, previous_related = match.end(), related
        if not related:
            continue
        number = _parse(match["number"])
        if any(len(str(part)) == 4 for part in number[1:]):
            continue
        around = _clause(text, start, match.end())
        if match["tag"] or _FUTURE.search(fold(around)):
            # Still a list member: its neighbours read through it.
            continue
        found.append(Version(number, match[0].lstrip("vV"), name, url))
    return found


_CLAUSE_END = re.compile(r"[.!?](?:\s|$)|[,;:()\[\]\n]")


def _clause(text: str, start: int, end: int) -> str:
    """The clause a version stands in, at most 60 characters on each side."""
    low = max(0, start - 60)
    before = [m.end() for m in _CLAUSE_END.finditer(text, low, start)]
    after = _CLAUSE_END.search(text, end, min(len(text), end + 60))
    return text[before[-1] if before else low : after.start() if after else end + 60]


def _newer(left: tuple[int, ...], right: tuple[int, ...]) -> bool:
    """left is a later release than right ("3.15" is not later than "3.15.2")."""
    size = min(len(left), len(right))
    if left[:size] == right[:size]:
        return False
    return left > right


def newest(pages: dict[str, list[str]], names: list[str]) -> Version | None:
    best: Version | None = None
    for url, texts in pages.items():
        for text in texts:
            for version in versions(text, names, url):
                if best is None or _newer(version.number, best.number):
                    best = version
    return best


def _release_score(link: str, names: list[str]) -> int:
    parts = urlsplit(link)
    labels = parts.netloc.lower().split(":")[0].split(".")
    segments = [segment for segment in parts.path.lower().split("/") if segment]
    plain = {re.sub(r"[^a-z0-9]", "", name) for name in names}
    own = any(re.sub(r"[^a-z0-9]", "", label) in plain for label in labels) or any(
        re.sub(r"[^a-z0-9]", "", segment) in plain for segment in segments[:2]
    )
    release = [
        index for index, segment in enumerate(segments) if segment in _RELEASE_SEGMENTS
    ]
    if not own or not release:
        return 0
    # The index of releases ("/downloads/", "/releases/latest") before the
    # page of one release ("/downloads/release/python-3148/").
    tail = segments[release[-1] + 1 :]
    return 2 if not tail or tail == ["latest"] else 1


def ranked(data: dict[str, object], question: str) -> dict[str, object]:
    """Search results with the subject's release pages first."""
    rows = data.get("results")
    if not isinstance(rows, list) or len(rows) < 2 or not asks_latest(question):
        return data
    names = subject(question)
    if not names:
        return data
    scores = [
        _release_score(str(row.get("link", "")), names) if isinstance(row, dict) else 0
        for row in rows
    ]
    if not any(scores):
        return data
    order = sorted(range(len(rows)), key=lambda index: -scores[index])
    return {**data, "results": [rows[index] for index in order]}


def hint(question: str, pages: dict[str, list[str]] | None) -> dict[str, str] | None:
    """For the model: the newest stable version the pages read give, when they differ."""
    if not pages or not checked(question):
        return None
    names = subject(question)
    seen = {
        version.number
        for url, texts in pages.items()
        for text in texts
        for version in versions(text, names, url)
    }
    best = newest(pages, names)
    if best is None or len(seen) < 2:
        return None
    return {
        "newest_stable_version_seen": best.text,
        "page": best.url,
        "note": (
            f"The pages read name several versions of {best.name}. The newest "
            f"stable one they give is {best.text}, on {best.url}. A page "
            "announcing maintenance releases of older branches does not give "
            "the latest version: answer with the newest release the project's "
            "own release or download page gives, and cite that page."
        ),
    }


_NOTES = {
    "en": "Note: a page read for this answer gives a newer version of {name}: "
    "{version} ([{label}]({url})).",
    "fr": "À noter : une page consultée pour cette réponse indique une version "
    "plus récente de {name} : {version} ([{label}]({url})).",
    "de": "Hinweis: Eine für diese Antwort gelesene Seite nennt eine neuere "
    "Version von {name}: {version} ([{label}]({url})).",
    "es": "Nota: una página consultada para esta respuesta indica una versión "
    "más reciente de {name}: {version} ([{label}]({url})).",
    "it": "Nota: una pagina consultata per questa risposta indica una versione "
    "più recente di {name}: {version} ([{label}]({url})).",
}


def _label(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or url).removeprefix("www.")
    path = parts.path.rstrip("/")
    return host + path if path and len(host + path) <= 60 else host


def note(
    answer: str, question: str, pages: dict[str, list[str]] | None, language: str
) -> str:
    """A closing note when the answer names an older version than a page gives."""
    if not pages or not checked(question):
        return ""
    names = subject(question)
    named = versions(answer, names)
    best = newest(pages, names)
    if not named or best is None:
        return ""
    if any(version.number == best.number for version in named):
        return ""
    if not _newer(best.number, named[0].number):
        return ""
    template = _NOTES.get(language, _NOTES["en"])
    return template.format(
        name=named[0].name or best.name,
        version=best.text,
        label=_label(best.url),
        url=best.url,
    )
