"""Plain-text project deliverables: Markdown, CSV, text and HTML files.

The bytes are the deliverable. Validation is deterministic and local: strict
UTF-8, non-empty, and for CSV a parse with a consistent column count. HTML
content is not interpreted here; downloads are always attachments with
nosniff, so it never renders on the Atlas origin.
"""

from __future__ import annotations

import csv
import html.parser
import io
import re
from xml.etree import ElementTree

TEXT_MIMES = {
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".html": "text/html",
    # A vector picture is its markup: validated as a well-formed SVG document
    # and, like HTML, always a download that never renders on the Atlas origin.
    ".svg": "image/svg+xml",
}
# Common source and configuration files a Work turn may create in the
# workspace; all are published as plain text.
CODE_MIMES = {
    ".py": "text/x-python",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".ts": "text/typescript",
    ".tsx": "text/typescript",
    ".jsx": "text/javascript",
    ".json": "application/json",
    ".yaml": "text/yaml",
    ".yml": "text/yaml",
    ".toml": "text/toml",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".sh": "text/x-shellscript",
    ".go": "text/x-go",
    ".rs": "text/x-rust",
    ".java": "text/x-java-source",
    ".kt": "text/x-kotlin",
    ".c": "text/x-c",
    ".h": "text/x-c",
    ".cpp": "text/x-c++",
    ".hpp": "text/x-c++",
    ".cs": "text/x-csharp",
    ".rb": "text/x-ruby",
    ".php": "text/x-php",
    ".swift": "text/x-swift",
    ".sql": "text/x-sql",
    ".css": "text/css",
    ".scss": "text/x-scss",
    ".xml": "text/xml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".html": "text/html",
    ".svg": "image/svg+xml",
}
# Extensionless build files are plain text too.
CODE_BASENAMES = {"dockerfile": "text/plain", "makefile": "text/plain"}
# Same bound as the model-facing Markdown content of Office deliverables.
TEXT_CHARACTER_LIMIT = 220000


class TextDeliverableError(ValueError):
    """A named validation failure; the code is safe to show to the model."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def validate_text(content: bytes, suffix: str) -> str:
    """Return the decoded text, or raise a named TextDeliverableError."""
    if suffix not in TEXT_MIMES:
        raise TextDeliverableError("unsupported_text_format")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise TextDeliverableError("text_not_utf8") from None
    if not text.strip():
        raise TextDeliverableError("text_empty")
    if suffix == ".csv":
        csv_rows(text)
    if suffix == ".svg":
        svg_root(text)
    return text


_NAMED_FILE = re.compile(r"[\w.-]+(\.[A-Za-z0-9]{1,5})\b")


def names_markup_file(text: str) -> bool:
    """The request names a file whose content is its text (escargot.svg, data.csv).

    Its inline fallback keeps the code block: the markup is the file, and the
    notice "the content above is the fallback" pointed at a block the answer
    cleanup had removed (staging -184).
    """
    return any(
        match.group(1).lower() in CODE_MIMES for match in _NAMED_FILE.finditer(text)
    )


def named_text_suffix(text: str) -> str | None:
    """The one text format a request names through a file name ("snail.svg"),
    or None when it names none or several.

    Staging -189: a request for a vector file with its name was delivered as a
    Markdown file holding the markup (the router's formats are the office ones
    and Markdown, and the writer followed its reading), then as the vector
    file too: two files, and an answer that said the wrong one was delivered.
    The name decides the format (publish_document then takes that suffix
    first), as the name of an office file does.
    """
    found = {
        match.group(1).lower()
        for match in _NAMED_FILE.finditer(text)
        if match.group(1).lower() in TEXT_MIMES
    }
    return found.pop() if len(found) == 1 else None


# A document type declaration can define entities (expansion bombs, external
# files): an SVG deliverable never needs one.
_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.I)


def svg_root(text: str) -> ElementTree.Element:
    """The root element of an SVG document, or a named TextDeliverableError."""
    if _DECLARATION.search(text):
        raise TextDeliverableError("svg_declaration_not_allowed")
    try:
        root = ElementTree.fromstring(text.lstrip("\ufeff").strip())
    except ElementTree.ParseError:
        raise TextDeliverableError("svg_not_well_formed") from None
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise TextDeliverableError("svg_root_missing")
    return root


def csv_rows(text: str) -> list[list[str]]:
    try:
        rows = [
            row for row in csv.reader(io.StringIO(text, newline=""), strict=True) if row
        ]
    except csv.Error:
        raise TextDeliverableError("csv_parse_error") from None
    if not rows:
        raise TextDeliverableError("text_empty")
    if any(len(row) != len(rows[0]) for row in rows):
        raise TextDeliverableError("csv_inconsistent_columns")
    return rows


class _Text(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def visible_text(text: str, suffix: str) -> str:
    """Text a reader sees, used only to check selected source coverage."""
    if suffix == ".csv":
        return "\n".join(cell for row in csv_rows(text) for cell in row)
    if suffix == ".html":
        parser = _Text()
        parser.feed(text)
        parser.close()
        return "".join(parser.parts)
    if suffix == ".svg":
        # The words drawn in the picture (text, title, desc elements).
        return " ".join(
            " ".join(part.split()) for part in svg_root(text).itertext() if part.strip()
        )
    return text
