"""Task H structured content contract (REQ-ENG-004, REQ-ENG-007)."""

import re
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .cells import (
    MONEY,
    SHEET_COLUMNS,
    Reading,
    NUMBER_START,
    plain,
    read_table,
    shown,
)
from .glyphs import adapt
from .spacing import UNITS

_VALUE = re.compile(
    rf"[+\-−]?\s*(?:(?:{MONEY})\s*)?[+\-−]?\d(?:[\d.,]*\d)?"
    r"(?::\d(?:[\d.,]*\d)?)?"
)
_CURRENCY = re.compile(rf"(?:{MONEY})")
_SUMMARY_UNIT = re.compile(rf"(?:{MONEY}|%|‰|(?:{UNITS}))")


def font_spaces(value: str) -> str:
    # The embedded font lacks some characters: French typography's narrow
    # space, arrows, "CO₂", emoji. They are adapted (see glyphs.adapt) instead
    # of refusing the whole document.
    return adapt(value)


def tidy(cell: str) -> str:
    # A cell of spaces only is an empty cell. Emphasis or backticks around a
    # formula are dropped: "**=SUM(B2:B5)**" is the formula, not a text. So are
    # they around a cell typed with an apostrophe to stay text ("**'=A1**").
    if not cell.strip():
        return ""
    display = plain(cell)
    return display if display.startswith(("=", "'=")) else cell


Text = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=4000),
    AfterValidator(font_spaces),
]
# A table cell may be empty: a template to fill in, a month not started yet.
# It stays empty in every format (a truly empty spreadsheet cell); no
# placeholder character is invented for it.
Cell = Annotated[
    str, StringConstraints(strict=True, max_length=4000), AfterValidator(font_spaces)
]


class ContentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Table(ContentModel):
    # Implied in a workbook, where any cell that starts with "=" is a formula
    # (see holds_formulas). Other formats refuse the flag and show such a cell.
    formulas: bool = Field(default=False, strict=True)
    # A workbook takes wide tables (twelve months and more); the formats laid
    # out on a page have their own lower bound, checked when rendering.
    headers: tuple[Cell, ...] = Field(min_length=1, max_length=SHEET_COLUMNS)
    rows: tuple[tuple[Cell, ...], ...] = Field(min_length=1, max_length=50)

    @field_validator("headers", mode="after")
    @classmethod
    def blank_headers(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(tidy(cell) for cell in value)

    @field_validator("rows", mode="after")
    @classmethod
    def blank_cells(
        cls, value: tuple[tuple[str, ...], ...]
    ) -> tuple[tuple[str, ...], ...]:
        return tuple(tuple(tidy(cell) for cell in row) for row in value)

    @model_validator(mode="after")
    def rectangular(self) -> Self:
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("table_row_width")
        if not any(self.headers):
            # Rows may be blank lines to fill in; the columns still need names.
            raise ValueError("table_headers_required")
        return self

    def read(self) -> tuple[tuple[Reading, ...], ...]:
        """The header row, then the rows: inline markers and numbers read."""
        return read_table(self.headers, self.rows)

    def holds_formulas(self) -> bool:
        """Whether a workbook calculates a cell of this table.

        There a cell that starts with "=" is a formula whether or not the
        table was flagged: a person who opens the file never wants to read
        "=SUM(B2:B5)" as text (staging 2026-11-12). A cell typed "'=A1", with
        an apostrophe, is the text "=A1".
        """
        return any(cell.startswith("=") for row in self.rows for cell in row)


Short = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=120),
    AfterValidator(font_spaces),
]


class Metric(ContentModel):
    """A key number shown large: "12 400 €" over "validated budget"."""

    value: Short = Field(description="the number as it should read, e.g. 12 400 €")
    label: Short = Field(description="what the number measures, a few words")


class Chart(ContentModel):
    """A simple bar chart drawn by code from the numbers given, never invented."""

    title: Short | None = None
    labels: tuple[Short, ...] = Field(min_length=2, max_length=12)
    values: tuple[float, ...] = Field(min_length=2, max_length=12)
    unit: Annotated[str, StringConstraints(strict=True, max_length=20)] = ""

    @model_validator(mode="after")
    def paired(self) -> Self:
        if len(self.labels) != len(self.values):
            raise ValueError("chart_labels_values_mismatch")
        if any(value != value or abs(value) > 1e15 for value in self.values):
            raise ValueError("chart_value_invalid")
        return self

    def texts(self) -> tuple[str, ...]:
        return (*((self.title,) if self.title else ()), *self.labels)


# A group title written as a bold line of its own ("**Team Wellbeing**").
LABEL = re.compile(r"(\*\*|__)([^*_\s][^*_]{0,118}?)\1\s*:?")
LABEL_WORDS = 12


def label(line: str) -> str | None:
    """The title of a bold line that names the group of text under it.

    A sentence (ending with . ! or ?) set in bold stays a paragraph.
    """
    found = LABEL.fullmatch(line.strip())
    if not found:
        return None
    text = found[2].strip().rstrip(":\uff1a").strip()
    if not text or text.endswith((".", "!", "?")) or len(text.split()) > LABEL_WORDS:
        return None
    return text


def split_summary(text: str) -> tuple[str, ...]:
    """A paragraph of several summary values as one line for each.

    A model may write "Subtotal: 10 Tax: 2 Total: 12" as one paragraph. The
    renderer wraps it between words, possibly inside a value. A paragraph
    made only of such pairs is split before layout; each label stays with
    its value and the final total is emphasized.
    """
    displayed = plain(text)
    if displayed.count(":") < 2:
        return (text,)
    # A time ("09:30") keeps its colon; only a label colon separates pairs.
    pieces = re.split(r"(?<!\d):(?!\d)", displayed)
    if len(pieces) < 3:
        return (text,)
    lines = []
    label = pieces[0].strip()
    for piece in pieces[1:]:
        following = piece.split()
        start = 0
        if (
            len(following) > 1
            and _CURRENCY.fullmatch(following[0])
            and NUMBER_START.match(following[1])
        ):
            start = 1
        if not following[start:] or not NUMBER_START.match(following[start]):
            return (text,)
        end = start + 1
        while end < len(following) and (
            _VALUE.fullmatch(following[end]) or _SUMMARY_UNIT.fullmatch(following[end])
        ):
            end += 1
        value, label_words = following[:end], following[end:]
        if not label or not label_words and piece is not pieces[-1]:
            return (text,)
        lines.append(f"{label}: {' '.join(value)}")
        label = " ".join(label_words)
    if label:
        return (text,)
    return tuple(
        f"**{line}**" if index == len(lines) - 1 else line
        for index, line in enumerate(lines)
    )


class Section(ContentModel):
    heading: Text
    paragraphs: tuple[Text, ...] = Field(default=(), max_length=50)
    bullets: tuple[Text, ...] = Field(default=(), max_length=50)
    tables: tuple[Table, ...] = Field(default=(), max_length=10)
    images: tuple[Text, ...] = Field(default=(), max_length=5)
    # Slide-only parts. A deck shows notes as speaker notes, metrics as
    # number callouts and a chart as a native chart; the formats laid out on
    # pages receive them as paragraphs and tables (Content.flattened).
    notes: tuple[Text, ...] = Field(default=(), max_length=50)
    metrics: tuple[Metric, ...] = Field(default=(), max_length=4)
    chart: Chart | None = None
    # The bullets are ordered steps or dates: a deck draws a timeline.
    sequence: bool = Field(default=False, strict=True)
    # A deck draws the bullets as a bulleted list, never as cards, columns or
    # a cover: the person asked for bullet points (docs/33). Code sets it
    # when a layout requirement asks for bullets; the writer may too.
    bulleted: bool = Field(default=False, strict=True)
    # Pages: the section starts on a new page. Sections otherwise follow each
    # other on the page (layout.py): a page break before every heading left a
    # six-section report on six pages filled to a twentieth (staging -150,
    # 2026-11-21). Set only where the person or the plan asks for it.
    page_break: bool = Field(default=False, strict=True)
    # 2: a sub-heading under the level-1 section before it. A section keeps
    # its paragraphs, then its bullets, then its tables: three group titles
    # written as paragraphs were all printed before the nine points of their
    # three lists (staging -183, 2026-11-23). Each group is a sub-section.
    level: int = Field(default=1, ge=1, le=2, strict=True)
    # The text goes on under the heading of the section before it, which is
    # not written again: a paragraph after a list stays after it.
    continued: bool = Field(default=False, strict=True)
    # A deck draws this heading on the slide of the section before it, under
    # its bullets: a named part is not one of them.
    subheading: Text | None = None

    def has_body(self) -> bool:
        return bool(
            self.paragraphs
            or self.bullets
            or self.tables
            or self.images
            or self.metrics
            or self.chart
        )

    @model_validator(mode="after")
    def substantive(self) -> Self:
        # A level-1 heading may stand alone over its sub-sections
        # (Content.grouped); anything else needs a body.
        if not self.has_body() and (self.level > 1 or self.continued):
            raise ValueError("section_body_required")
        return self

    def slide_only(self) -> bool:
        return bool(self.notes or self.metrics or self.chart)

    def drawn(self) -> bool:
        """Whether the heading is written (a continuation does not repeat it)."""
        return not self.continued or self.subheading is not None


class Content(ContentModel):
    title: Text
    sections: tuple[Section, ...] = Field(min_length=1, max_length=100)
    # Pages: the title stands alone on a first page (a title page), and the
    # first section starts on the next one. Only when it is asked for.
    cover: bool = Field(default=False, strict=True)
    # Pages: a running footer printed at the foot of every page (a PDF and a
    # DOCX page footer, a deck's footer line), never a line of the body. Asked
    # "in the footer of both pages", it was written under the last paragraph
    # of each page (staging -184): the content had no footer to fill.
    footer: Text | None = None

    @model_validator(mode="after")
    def printable(self) -> Self:
        typed = self.typed()
        if self.footer is not None and (
            not self.footer.strip() or any(ord(c) < 32 for c in self.footer)
        ):
            raise ValueError("blank_or_control_text")
        for text in typed:
            if not text.strip() or any(ord(c) < 32 for c in text):
                raise ValueError("blank_or_control_text")
        if sum(map(len, typed)) > 200_000:
            raise ValueError("content_size_limit")
        return self

    @model_validator(mode="after")
    def grouped(self) -> Self:
        """A heading without a body opens sub-sections; text goes on under one."""
        for index, section in enumerate(self.sections):
            following = self.sections[index + 1 : index + 2]
            if not section.has_body() and not (
                following and following[0].level > section.level
            ):
                # The rule, then where it applies (explain() reads both).
                raise ValueError(f"section_body_required sections[{index}]")
            if section.continued and not index:
                raise ValueError("continued_without_section sections[0]")
        return self

    def texts(self, *, sheet: bool = False) -> tuple[str, ...]:
        """Every written text as displayed; empty table cells hold none.

        Inline markers are not part of the text, in a title, a heading, a
        paragraph, a bullet or a table cell. With sheet, a cell a workbook
        stores as a number gives that number.
        """
        result = [shown(self.title)]
        for section in self.sections:
            heading = (section.heading,) if section.drawn() else ()
            subheading = (section.subheading,) if section.subheading else ()
            result.extend(
                map(
                    shown,
                    (*heading, *subheading, *section.paragraphs, *section.bullets),
                )
            )
            result.extend(map(shown, slide_texts(section)))
            for table in section.tables:
                for row in table.read():
                    result.extend(
                        cell.stored if sheet else cell.text for cell in row if cell.text
                    )
        return tuple(result)

    def typed(self) -> tuple[str, ...]:
        """Every written text as it was typed, inline markers included.

        A source excerpt is quoted as typed and displayed without its
        markers: the check of source coverage compares with this text.
        """
        result = [self.title]
        for section in self.sections:
            result.extend((section.heading, *section.paragraphs, *section.bullets))
            result.extend(slide_texts(section))
            for table in section.tables:
                result.extend(filter(None, table.headers))
                result.extend(cell for row in table.rows for cell in row if cell)
        return tuple(result)

    def flattened(self) -> "Content":
        """The content for a format laid out on pages: slide-only parts as text.

        Speaker notes become paragraphs, metrics a two-column table and a
        chart the table of its numbers: nothing written for a deck is lost
        in a DOCX, a PDF or a workbook.
        """
        value = self.model_dump(mode="json")
        for section, data in zip(self.sections, value["sections"], strict=True):
            data["paragraphs"] = [
                line
                for paragraph in data["paragraphs"]
                for line in split_summary(paragraph)
            ]
            data["bullets"] = [
                line for bullet in data["bullets"] for line in split_summary(bullet)
            ]
        if not any(section.slide_only() for section in self.sections):
            return Content.model_validate(value)
        for section, data in zip(self.sections, value["sections"], strict=True):
            data["paragraphs"] = [*data["paragraphs"], *data.pop("notes")]
            data["bullets"] = [
                *data["bullets"],
                *(f"{m.label}: {m.value}" for m in section.metrics),
            ][:50]
            tables = list(data["tables"])
            if section.chart is not None:
                chart = section.chart
                tables.append(
                    dict(
                        headers=[chart.title or section.heading, chart.unit],
                        rows=[
                            [label, number_text(number)]
                            for label, number in zip(
                                chart.labels, chart.values, strict=True
                            )
                        ],
                    )
                )
            data.pop("metrics")
            data.pop("chart")
            data["tables"] = tables[:10]
        return Content.model_validate(value)

    def repeats_title(self) -> bool:
        """Whether the first heading says the title again.

        A Markdown text that opens with its title gives both: its first
        section is headed by that title. Each format then writes the text
        once (a workbook showed it twice under its table, staging 2026-11-12).
        """
        return "".join(shown(self.title).split()) == "".join(
            shown(self.sections[0].heading).split()
        )


def slide_texts(section: Section) -> tuple[str, ...]:
    """The written texts of a section's slide-only parts, in reading order."""
    metrics = tuple(text for m in section.metrics for text in (m.value, m.label))
    chart = section.chart.texts() if section.chart else ()
    return (*metrics, *chart, *section.notes)


def number_text(value: float) -> str:
    """A chart value as a cell: 12.0 reads 12, 12.5 reads 12.5."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


class PresentationSlide(ContentModel):
    """The bounded model-facing contract for a single presentation slide."""

    title: Text
    bullets: tuple[Text, ...] = Field(
        default=(),
        max_length=8,
        description="short lines, one idea each; a 'Label: text' line shows its label in bold",
    )
    notes: Text | None = Field(
        default=None,
        description="speaker notes: what the presenter says, not shown on the slide",
    )
    table: Table | None = None
    metrics: tuple[Metric, ...] = Field(
        default=(),
        max_length=4,
        description="2 to 4 key numbers shown large, only numbers given or read",
    )
    chart: Chart | None = Field(
        default=None,
        description="a bar chart of numbers given or read, never invented",
    )
    sequence: bool = Field(
        default=False,
        strict=True,
        description="true only when the bullets are ordered steps or dates",
    )
    bulleted: bool = Field(
        default=False,
        strict=True,
        description="true when the person asked for bullet points: a bulleted list, never cards",
    )
    # Pictures drawn for the deck (make_pictures references), shown on the slide.
    images: tuple[Text, ...] = Field(
        default=(),
        max_length=2,
        description="references returned by make_pictures, shown on this slide",
    )

    @model_validator(mode="after")
    def substantive(self) -> Self:
        if not (
            self.bullets or self.table or self.metrics or self.chart or self.images
        ):
            raise ValueError("section_body_required")
        return self


class PresentationOutline(ContentModel):
    """Trusted code maps this concise outline to the generic render contract."""

    title: Text
    slides: tuple[PresentationSlide, ...] = Field(min_length=1, max_length=30)

    def content(self) -> Content:
        return Content(
            title=self.title,
            sections=tuple(
                Section(
                    heading=slide.title,
                    bullets=slide.bullets,
                    tables=(slide.table,) if slide.table else (),
                    images=slide.images,
                    notes=(slide.notes,) if slide.notes else (),
                    metrics=slide.metrics,
                    chart=slide.chart,
                    sequence=slide.sequence,
                    bulleted=slide.bulleted,
                )
                for slide in self.slides
            ),
        )
