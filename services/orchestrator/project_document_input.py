"""Model-friendly document input, normalized before the strict render boundary."""

from collections.abc import Iterator
from contextlib import contextmanager
from difflib import get_close_matches
import json
from pathlib import PurePosixPath
import re
from typing import Any, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from packages.doc_render.schema import (
    Content,
    PresentationOutline,
    PresentationSlide,
    label,
)
from packages.doc_render.titles import retitled


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    formulas: bool = False
    title: str | None = Field(default=None, min_length=1, max_length=4000)
    content: str | None = Field(default=None, min_length=1, max_length=220000)
    slides: list[PresentationSlide] | None = Field(
        default=None, min_length=1, max_length=30
    )


# What a named rule of the content contract asks for.
RULES = {
    "table_row_width": "every row needs exactly as many cells as the table has headers",
    "table_headers_required": "a table needs at least one column name",
    "section_body_required": "a section needs at least one paragraph, bullet, table or image; a heading alone only opens deeper sub-headings",
    "continued_without_section": "the first section cannot continue a section before it",
    "blank_or_control_text": "a text is blank or holds a tab or a line break; write one line of visible text per item",
    "content_size_limit": "the texts exceed 200000 characters in total",
}


def nested(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for part in get_args(annotation):
        found = nested(part)
        if found is not None:
            return found
    return None


def fields_at(model: type[BaseModel] | None, path: tuple[int | str, ...]) -> list[str]:
    """The field names accepted at a path of the validated object."""
    for part in path:
        if model is None:
            return []
        if isinstance(part, str):
            field = model.model_fields.get(part)
            model = nested(field.annotation) if field else None
    return list(model.model_fields) if model else []


def explain(error: ValidationError, model: type[BaseModel], root: str = "") -> str:
    """A refusal the model can act on: each field path and its rule, nothing internal."""
    reported = error.errors(include_url=False, include_input=False)
    # An item refused for its own reason also leaves its list short: only the
    # item's reason is something to correct.
    problems = [
        problem
        for problem in reported
        if problem["type"] != "too_short"
        or not any(
            len(other["loc"]) > len(problem["loc"])
            and other["loc"][: len(problem["loc"])] == problem["loc"]
            for other in reported
        )
    ]
    lines: list[str] = []
    for problem in problems[:5]:
        path = problem["loc"]
        where = root
        for part in path:
            if isinstance(part, int):
                where += f"[{part}]"
            else:
                where += ("." if where else "") + part
        kind = problem["type"]
        if kind == "extra_forbidden":
            allowed = fields_at(model, path[:-1])
            close = get_close_matches(str(path[-1]), allowed, 1)
            rule = "unknown field"
            if close:
                rule += f' (did you mean "{close[0]}"?)'
            if allowed:
                rule += "; fields allowed here: " + ", ".join(allowed)
        elif kind == "missing":
            rule = "this required field is missing"
        elif kind in ("model_type", "model_attributes_type"):
            allowed = fields_at(model, path)
            rule = "an object is expected here"
            if allowed:
                rule += " with the fields: " + ", ".join(allowed)
        elif kind == "value_error":
            code = str(problem["msg"]).removeprefix("Value error, ")
            if " " in code and code.split(" ", 1)[0] in RULES:
                # A rule of the whole content names the part it applies to.
                code, part = code.split(" ", 1)
                where += ("." if where else "") + part
            rule = RULES.get(code, code)
        elif kind in ("too_long", "too_short"):
            context = problem.get("ctx", {})
            bound = "at most" if kind == "too_long" else "at least"
            limit = context.get("max_length", context.get("min_length"))
            rule = f"{bound} {limit} items here, {context.get('actual_length')} given"
        else:
            rule = str(problem["msg"])
        lines.append(f"{where or 'arguments'}: {rule}")
    if len(problems) > 5:
        lines.append(f"and {len(problems) - 5} more")
    return (
        "Invalid document arguments. "
        + "; ".join(lines)
        + ". Correct these fields and call the tool again."
    )


@contextmanager
def explained(model: type[BaseModel], root: str = "") -> Iterator[None]:
    """Turn a validation report into the short refusal of explain()."""
    try:
        yield
    except ValidationError as error:
        raise ValueError(explain(error, model, root)) from None


# A line alone that starts a new page (readback.PAGE_BREAK is the first).
PAGE_BREAKS = frozenset({"\\newpage", "\\pagebreak"})
# A line "\\footer text" gives the running footer of every page
# (schema.Content.footer), wherever it is written.
FOOTER = re.compile(r"\\footer\b[:\s]*(.*)")
# The order a section writes its blocks in (schema.Section): a block of an
# earlier kind after a later one goes on in a continuation.
RANK = {"paragraphs": 0, "bullets": 1, "tables": 2, "images": 3}


# A line that titles the text under it, written without any mark: short, not a
# sentence (no closing punctuation), alone between blank lines.
TITLE_WORDS = 8
TITLE_CHARACTERS = 70
TITLE_BODY_WORDS = 6
TITLE_MINIMUM = 2
_MARK = re.compile(r"#{1,6}\s+\S")
_LISTED = re.compile(r"(?:[-+*]|\d+[.)])\s+\S")


def marked_titles(lines: list[str], title: str) -> list[str]:
    """The lines of a text whose section titles were written as plain lines,
    with each title marked as a heading ("## Title").

    Staging -187, -188 and -191: a Word report came with "Introduction",
    "Calendrier du projet" and "Conclusion" as plain paragraphs (styles Title
    and Normal only): the writer sent no heading mark, so the structure was
    neither visible nor navigable. A text with no heading at all is read for
    them: a block of one short line that is no sentence and no list or table,
    followed by a paragraph of some length, a list or a table, is a section
    title when at least two such blocks are found (a poem's short lines are
    followed by short lines, never by a paragraph). A text that has a heading
    mark is left as written.
    """
    if any(_MARK.match(line.strip()) for line in lines[1:]):
        return lines
    blocks: list[list[int]] = []
    current: list[int] = []
    for number, line in enumerate(lines):
        if line.strip():
            current.append(number)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)

    def body(block: list[int]) -> bool:
        first = lines[block[0]].strip()
        if _LISTED.match(first) or "|" in first:
            return True
        words = " ".join(lines[number].strip() for number in block).split()
        return len(words) >= TITLE_BODY_WORDS

    def plain(line: str) -> bool:
        words = line.split()
        return (
            0 < len(words) <= TITLE_WORDS
            and len(line) <= TITLE_CHARACTERS
            and not line.startswith(("\\", "!", "#", "|", "*", "_", "`"))
            and not _LISTED.match(line)
            and line[-1] not in ".!?:;,…"
            and line.casefold() != title.strip().casefold()
        )

    found = [
        block[0]
        for index, block in enumerate(blocks[:-1])
        if len(block) == 1
        and (index > 0 or blocks[0][0] > 0 or lines[0].strip() != title.strip())
        and plain(lines[block[0]].strip())
        and body(blocks[index + 1])
        and len(blocks[index + 1]) >= 1
    ]
    if len(found) < TITLE_MINIMUM:
        return lines
    marked = list(lines)
    for number in found:
        marked[number] = "## " + lines[number].strip()
    return marked


def markdown(text: str, title: str, *, grouped: bool = True) -> Content:
    """A deliberately small text format: headings, paragraphs, lists and tables.

    Sections follow each other on the page. A line \\newpage (or \\pagebreak)
    starts the next section on a new page; before any text, it gives the
    title a page of its own.

    The shallowest headings (after an opening "# " title) are sections;
    deeper ones, and a bold line of its own that titles the text under it
    (label), are level-2 sub-sections. Every block stays where it was
    written: a paragraph after a list, or a list after a table, goes on in
    a continuation of the section (schema.Section.continued). Without
    grouped (a workbook: a sheet per section, its texts under its first
    table), bold lines stay paragraphs and nothing continues.
    """
    sections: list[dict[str, Any]] = []
    section: dict[str, Any] = dict(
        heading=title, paragraphs=[], bullets=[], tables=[], images=[]
    )
    paragraph: list[str] = []
    lines = text.splitlines()
    if grouped:
        lines = marked_titles(lines, title)
    cover = broken = False
    footer: str | None = None
    depths = [
        len(found[1])
        for number, line in enumerate(lines)
        if (found := re.match(r"(#{1,6})\s+\S", line.strip()))
        and not (number == 0 and found[1] == "#")
    ]
    top = min(depths, default=1)

    def body(value: dict[str, Any]) -> bool:
        return any(value[kind] for kind in RANK)

    def opened(heading: str, level: int, **more: Any) -> dict[str, Any]:
        return dict(
            heading=heading,
            paragraphs=[],
            bullets=[],
            tables=[],
            images=[],
            level=level,
            **more,
        )

    def place(kind: str) -> None:
        """Go on in a continuation when a later kind of block was written."""
        nonlocal section
        if grouped and any(
            section[later] for later in RANK if RANK[later] > RANK[kind]
        ):
            finish()
            section = opened(
                section["heading"], section.get("level", 1), continued=True
            )

    def more_text(start: int) -> bool:
        """Whether text follows before the next heading or page break."""
        for line in lines[start:]:
            line = line.strip()
            if re.fullmatch(r"#{1,6}\s+.+", line) or line in PAGE_BREAKS:
                return False
            if line:
                return True
        return False

    def flush() -> None:
        if paragraph:
            section["paragraphs"].append(" ".join(paragraph))
            paragraph.clear()

    def finish() -> None:
        flush()
        # A heading alone is kept: it may open sub-sections (left out below
        # when none follows).
        sections.append(section.copy())

    def cells(line: str) -> list[str]:
        # One outer pipe on each side is the table border; an empty last cell
        # written "||" stays a cell.
        line = line.strip()
        line = line[1:] if line.startswith("|") else line
        line = line[:-1] if line.endswith("|") else line
        return [cell.strip() for cell in line.split("|")]

    index = 0
    while index < len(lines):
        line = lines[index].strip()
        # A picture drawn in this turn (work_pictures.py), on its own line:
        # "![radishes](picture-0123456789ab)". Any other image is refused.
        picture = re.fullmatch(r"!\[[^\]\n]{0,200}\]\((picture-[0-9a-f]{12})\)", line)
        if picture:
            flush()
            place("images")
            section["images"].append(picture[1])
            index += 1
            continue
        # From the first "![" only (atomic): a later one has fewer "](" after it.
        if re.match(r"(?>.*?!\[).*?\]\(", line) or line.startswith("```"):
            raise ValueError("Unsupported Markdown image or fenced block")
        heading = re.fullmatch(r"#{1,6}\s+(.+)", line)
        bullet = re.fullmatch(r"(?:[-+*]|\d+[.)])\s+(.+)", line)
        running = FOOTER.fullmatch(line)
        if running:
            flush()
            footer = running[1].strip() or None
        elif line in PAGE_BREAKS:
            # A new page, asked for: before any text, the title has a page
            # of its own; otherwise the next section starts a new page.
            flush()
            if not sections and not (
                section["paragraphs"]
                or section["bullets"]
                or section["tables"]
                or section["images"]
            ):
                cover = True
            else:
                broken = True
        elif heading:
            finish()
            depth = len(line) - len(line.lstrip("#"))
            section = opened(
                heading[1],
                1 if depth <= top or index == 0 or not grouped else 2,
                page_break=broken,
            )
            broken = False
            if index == 0 and line.startswith("# "):
                title = heading[1]
        elif not line:
            flush()
        elif (
            "|" in line
            and index + 1 < len(lines)
            and all(
                re.fullmatch(r":?-{3,}:?", cell) for cell in cells(lines[index + 1])
            )
        ):
            flush()
            place("tables")
            headers = cells(line)
            if len(cells(lines[index + 1])) != len(headers):
                raise ValueError("table_row_width")
            rows = []
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                # An empty Markdown cell stays an empty cell in every format (a
                # value to fill in later); a missing cell is still a width error.
                row = cells(lines[index])
                while len(row) > len(headers) and not row[-1]:
                    # A doubled closing pipe adds no column.
                    row.pop()
                rows.append(row)
                index += 1
            section["tables"].append(dict(headers=headers, rows=rows))
            continue
        elif bullet:
            flush()
            place("bullets")
            section["bullets"].append(bullet[1])
        elif (
            grouped
            and not paragraph
            and (named := label(line))
            and more_text(index + 1)
        ):
            finish()
            section = opened(named, 2, page_break=broken, label=line)
            broken = False
        else:
            if not paragraph:
                place("paragraphs")
            paragraph.append(line)
        index += 1
    finish()
    # A heading with nothing under it, not even a sub-section, is left out.
    # Bold title lines with nothing between them (a file read back after its
    # titles were set over one list, staging -183) stay paragraphs over the
    # text that follows: the form check names them (form.misgrouped).
    kept: list[dict[str, Any]] = []
    carried: list[str] = []
    for number, value in enumerate(sections):
        written = value.pop("label", None)
        if body(value) or (
            number + 1 < len(sections)
            and sections[number + 1].get("level", 1) > value.get("level", 1)
        ):
            if (
                carried
                and written is not None
                and kept
                and not any(kept[-1][kind] for kind in RANK if kind != "paragraphs")
            ):
                # The titles, in their order, then the text: as written.
                into = kept[-1]
                into["paragraphs"] += [*carried, written, *value["paragraphs"]]
                for kind in ("bullets", "tables", "images"):
                    into[kind] += value[kind]
            else:
                value["paragraphs"] = [*carried, *value["paragraphs"]]
                kept.append(value)
            carried = []
        elif written is not None:
            carried.append(written)
    if carried and kept:
        kept[-1]["paragraphs"].extend(carried)
    with explained(Content, "content"):
        return Content.model_validate(
            dict(title=title, sections=kept, cover=cover, footer=footer)
        )


def opening_heading(text: str | None) -> str:
    """The text of a first-level "# " heading that opens a Markdown text.

    Without a title argument, that heading is the document's title (it is
    then written once, repeats_title); the file name read as words used to
    stand in, above the asked title shown as a heading (staging -156).
    """
    for line in (text or "").splitlines():
        if line.strip():
            found = re.fullmatch(r"#\s+(.+)", line.strip())
            return found[1].strip() if found else ""
    return ""


def normalize(value: dict[str, Any]) -> dict[str, Any]:
    """Keep strict objects compatible; parse JSON once without coercing bad input."""
    if not isinstance(value, dict):
        raise ValueError("Document arguments must be an object")
    result = dict(value)
    content = result.get("content")
    if isinstance(content, str) and len(content) > 220000:
        raise ValueError("Document content exceeds 220000 characters")
    suffix = PurePosixPath(str(result.get("path", ""))).suffix.lower()
    if isinstance(content, str) and content.lstrip().startswith(("{", "[")):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as error:
            raise ValueError(
                "content starts like JSON but is not valid JSON (line "
                f"{error.lineno}, column {error.colno}: {error.msg}). Send valid "
                "JSON, or Markdown text."
            ) from None
    if isinstance(content, dict):
        with explained(Content, "content"):
            normalized_content = Content.model_validate(content).model_dump()
        formulas = result.pop("formulas", False)
        if not isinstance(formulas, bool):
            raise ValueError("Formulas must be a boolean")
        if formulas:
            if suffix != ".xlsx":
                raise ValueError("Formulas require XLSX")
            for section in normalized_content["sections"]:
                for table in section["tables"]:
                    table["formulas"] = True
        result["content"] = retitled(
            normalized_content,
            PurePosixPath(str(result.get("path", ""))).stem,
            deck=suffix == ".pptx",
        )
        return result
    with explained(DocumentInput):
        simple = DocumentInput.model_validate(
            {
                key: result.pop(key)
                for key in ("title", "content", "slides", "formulas")
                if key in result
            }
        )
    suffix = PurePosixPath(str(result.get("path", ""))).suffix.lower()
    stem = PurePosixPath(str(result.get("path", ""))).stem
    # Never the file name as a title (titles.py): a deck without a title
    # takes its cover heading, a text its "# " line, else the stem in words.
    title = simple.title or opening_heading(simple.content) or stem
    if suffix == ".pptx" and simple.slides is not None and simple.content is None:
        with explained(Content, "content"):
            normalized = PresentationOutline(
                title=title, slides=tuple(simple.slides)
            ).content()
    elif (
        suffix in {".docx", ".pdf", ".xlsx"}
        and simple.content is not None
        and simple.slides is None
    ):
        normalized = markdown(simple.content, title, grouped=suffix != ".xlsx")
    else:
        raise ValueError("Use Markdown content for DOCX/PDF/XLSX or slides for PPTX")
    result["content"] = retitled(normalized.model_dump(), stem, deck=suffix == ".pptx")
    if simple.formulas:
        if suffix != ".xlsx":
            raise ValueError("Formulas require XLSX")
        for section in result["content"]["sections"]:
            for table in section["tables"]:
                table["formulas"] = True
    return result
