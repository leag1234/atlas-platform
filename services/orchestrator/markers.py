"""Internal markers and tool names never reach the person (class C4).

The frontier judge (staging -145) found an app answer listing each embedded
file with "[unverified source]" and a strict answer saying that the tool
`web_search` was blocked. Both came from text written for the
model or for the logs: the citation guard's English placeholder for an address
it could not trace, and the tool result naming the tool. The sources now say it
in the person's words; this guard catches the family in whatever path an answer
takes (UI stream, API with or without stream, a time cut):

- a bracketed English marker ("[unverified source]", "[citation needed]",
  "[placeholder]") is removed;
- a known tool name used as a word ("`web_search`", "publish_document") reads
  as plain words in the answer's language ("recherche web").

Fenced code is the answer's content and is not read; neither is anything the
person wrote themselves (their words quoted back), nor a name the answer's own
code defines.
"""

from __future__ import annotations

import functools
import re

from packages import addressing

# Plain words for the tools a model names most, per language; another tool
# reads as its name without underscores.
TOOL_WORDS: dict[str, dict[str, str]] = {
    "web_search": {
        "en": "web search",
        "fr": "recherche web",
        "de": "Websuche",
        "es": "búsqueda web",
        "it": "ricerca web",
    },
    "web_fetch": {
        "en": "page reading",
        "fr": "lecture de pages web",
        "de": "Abruf von Webseiten",
        "es": "lectura de páginas web",
        "it": "lettura di pagine web",
    },
    "web_images": {
        "en": "image search",
        "fr": "recherche d’images",
        "de": "Bildersuche",
        "es": "búsqueda de imágenes",
        "it": "ricerca di immagini",
    },
    "read_document": {
        "en": "document reading",
        "fr": "lecture de documents",
        "de": "Lesen von Dokumenten",
        "es": "lectura de documentos",
        "it": "lettura di documenti",
    },
    "generate_image": {
        "en": "image generation",
        "fr": "génération d’images",
        "de": "Bilderzeugung",
        "es": "generación de imágenes",
        "it": "generazione di immagini",
    },
    "publish_document": {
        "en": "file publishing",
        "fr": "publication de fichiers",
        "de": "Veröffentlichen von Dateien",
        "es": "publicación de archivos",
        "it": "pubblicazione di file",
    },
    "transit_plan": {
        "en": "journey planner",
        "fr": "calcul d’itinéraire",
        "de": "Fahrplanauskunft",
        "es": "planificador de viajes",
        "it": "pianificatore di viaggio",
    },
}
# Tool names of the chat, Work and code engine that a model may name: only the
# ones made of several words joined by "_" (a plain word such as "read" or
# "bash" is ordinary text).
_EXTRA_TOOLS = frozenset(
    {
        "terminal_command",
        "code_execution",
        "local_shell",
        "make_pictures",
        "transit_plan",
        "load_skill",
        "update_plan",
        "request_approval",
    }
)


@functools.cache
def tool_names() -> frozenset[str]:
    from packages import privacy
    from services.orchestrator import work_plan, work_progress

    names = (
        set(TOOL_WORDS)
        | _EXTRA_TOOLS
        | set(privacy.TOOL_CAPABILITIES)
        | set(privacy.STRICT_EXCLUDED_TOOLS)
        | set(work_progress.STEPS)
        | set(work_progress.SILENT)
        | set(work_progress.CHAT_ACTIVITIES)
        | set(work_plan.TOOL_TYPES)
    )
    return frozenset(
        name for name in names if re.fullmatch(r"[a-z]+(?:_[a-z]+)+", name)
    )


def tool_words(name: str, language: str) -> str:
    words = TOOL_WORDS.get(name)
    if words is not None:
        return words.get(language, words["en"])
    return name.replace("_", " ")


@functools.cache
def _tool_pattern() -> re.Pattern[str]:
    names = "|".join(sorted(map(re.escape, tool_names()), key=len, reverse=True))
    # The name alone, or in backticks: never part of an address, a path, a
    # file name or a longer identifier.
    return re.compile(rf"(`?)(?<![\w/.:@-])({names})(?![\w/@-]|\.\w)\1")


# A bracketed marker: a few lower-case English words between brackets, one of
# them a marker word; never a link label "[text](", a reference "[text][", a
# footnote "[1]" or a task box "[ ]".
_MARKER_WORDS = (
    "unverified|unconfirmed|unsourced|citation|source|sources|needed|placeholder|"
    "todo|tbd|insert|redacted|removed|missing|unknown|link|url|reference|internal|"
    "tool|marker|omitted"
)
_MARKER = re.compile(
    r"(?P<before>[ \t]?)(?<![\w\[!])\[(?=[a-z _-]{3,40}\])"
    rf"(?=[a-z _-]*\b(?:{_MARKER_WORDS})\b)[a-z]+(?:[ _-][a-z]+){{0,4}}\]"
    r"(?![(\[:])(?P<after>[ \t]?)"
)
_FENCE = re.compile(r"```.*?(?:```|$)", re.S)


def _person_text() -> str:
    return "\n".join(addressing.person_words()).casefold()


def has_marker(text: str) -> bool:
    """Whether ``text`` (outside fenced code) holds a marker or a tool name
    the person did not write themselves."""
    return without_markers(text) != text


def without_markers(text: str, language: str | None = None) -> str:
    """``text`` without its bracketed English markers, its tool names in plain
    words (fenced code and the person's own words kept)."""
    if "[" not in text and "_" not in text:
        return text
    language = language or addressing.answer_language()
    person = _person_text()
    code = " ".join(match[0] for match in _FENCE.finditer(text))

    def marker(match: re.Match[str]) -> str:
        if match[0].strip().casefold() in person:
            return match[0]
        # One space stays between the words the marker separated.
        return " " if match["before"] and match["after"] else ""

    def tool(match: re.Match[str]) -> str:
        name = match[2]
        if name in person or name in code:
            return match[0]
        return tool_words(name, language)

    parts: list[str] = []
    last = 0
    for fence in _FENCE.finditer(text):
        parts.append(_prose(text[last : fence.start()], marker, tool))
        parts.append(fence[0])
        last = fence.end()
    parts.append(_prose(text[last:], marker, tool))
    return "".join(parts)


def _prose(text: str, marker, tool) -> str:  # type: ignore[no-untyped-def]
    if "[" in text:
        text = _MARKER.sub(marker, text)
    if "_" in text:
        text = _tool_pattern().sub(tool, text)
    return text
