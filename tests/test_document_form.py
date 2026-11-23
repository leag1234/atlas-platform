"""Report pages are filled: sections flow, and the form of the pages is checked.

Staging -150 (2026-11-21): a Word report of six short sections came on six
pages, each about 95 % empty, because the DOCX writer put a page break before
every heading; every check passed (nothing scored the form of a document).
Sections now follow each other on the page (layout.pages, the DOCX writer
places the same breaks), a page break is placed only where it is asked for
(a title page, a \\newpage line) or before a very long section, and the form
of the pages is measured on the rendered PDF (packages/doc_render/form.py) by
verify() and the deliverable check. The content here is fictional.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
from typing import Any
import unittest
import zipfile

from packages.doc_render import form, readback, render
from packages.doc_render.schema import Content
from services.orchestrator import deliverable_check, expectations
from services.orchestrator.project_document_input import normalize

REPORT = """## Introduction

The Fernhill library opens a repair café for small appliances in the spring.

## Calendar

| Step | Date |
| --- | --- |
| Volunteers found | March |
| First session | April |

## Risks

- Too few volunteers
- Spare parts hard to find

## Indicators

Number of objects repaired each month.

## Conclusion

The project needs a budget of 900 euros.

## Sources

Notes of the committee meeting.
"""
LONG = " ".join(["The cooperative keeps a record of every crate it sells."] * 30)


def content_of(arguments: dict[str, Any], suffix: str) -> Content:
    value = normalize(dict(arguments, path="report" + suffix, sources=[]))
    return Content.model_validate(value["content"])


def built(arguments: dict[str, Any], suffix: str) -> tuple[Content, bytes]:
    content = content_of(arguments, suffix)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ("report" + suffix)
        render(content, path, image_root=Path(directory))
        return content, path.read_bytes()


def breaks_of(raw: bytes) -> tuple[int, int]:
    """Page breaks of their own paragraph, and headings that start a page."""
    xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode()
    return xml.count('w:type="page"'), xml.count("<w:pageBreakBefore")


def rebuilt(raw: bytes, suffix: str) -> bytes:
    """The file written again from what reads back from it (a revision)."""
    arguments = json.loads(json.dumps(readback.editable(readback.read(raw, suffix))))
    return built(arguments, suffix)[1]


class FlowTests(unittest.TestCase):
    def test_a_short_report_flows_on_one_page(self) -> None:
        arguments = {"title": "Repair café", "content": REPORT}
        _, docx = built(arguments, ".docx")
        self.assertEqual(breaks_of(docx), (0, 0))
        content, pdf = built(arguments, ".pdf")
        measured = form.measure(pdf)
        self.assertEqual(measured.count, 1)
        self.assertEqual(form.findings(measured), [])
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(form.check(content, Path(directory)), [])

    def test_verify_reports_the_form_of_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            for suffix in (".docx", ".pdf"):
                content = content_of(
                    {"title": "Repair café", "content": REPORT}, suffix
                )
                result = render(
                    content,
                    Path(directory) / ("r" + suffix),
                    image_root=Path(directory),
                )
                self.assertEqual(result.form, ())

    def test_a_page_break_is_placed_only_where_it_is_asked(self) -> None:
        text = "\\newpage\n\n" + REPORT.replace("## Risks", "\\newpage\n\n## Risks")
        arguments = {"title": "Repair café", "content": text}
        content, docx = built(arguments, ".docx")
        self.assertTrue(content.cover)
        self.assertEqual(
            [s.heading for s in content.sections if s.page_break], ["Risks"]
        )
        self.assertEqual(breaks_of(docx), (2, 0))
        _, pdf = built(arguments, ".pdf")
        self.assertEqual(form.measure(pdf).count, 3)
        for suffix, raw in ((".docx", docx), (".pdf", pdf)):
            with self.subTest(suffix=suffix):
                document = readback.read(raw, suffix)
                self.assertTrue(document.cover)
                self.assertEqual(
                    [s.heading for s in document.sections if s.page_break], ["Risks"]
                )
                self.assertEqual(
                    hashlib.sha256(rebuilt(raw, suffix)).hexdigest(),
                    hashlib.sha256(raw).hexdigest(),
                )
        # Asked breaks leave short pages: not a defect of form.
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(form.check(content, Path(directory), pdf), [])

    def test_a_very_long_section_opens_a_page(self) -> None:
        text = (
            f"{LONG}\n\n{LONG}\n\n## Crates\n\n"
            + "\n\n".join([LONG] * 6)
            + "\n\n## End\n\nThe season closes in November."
        )
        arguments = {"title": "Long report", "content": text}
        content, docx = built(arguments, ".docx")
        # Decided by the layout, not asked: the heading starts its page.
        self.assertEqual(breaks_of(docx), (0, 1))
        self.assertFalse(
            any(s.page_break for s in readback.read(docx, ".docx").sections)
        )
        self.assertEqual(
            hashlib.sha256(rebuilt(docx, ".docx")).hexdigest(),
            hashlib.sha256(docx).hexdigest(),
        )
        _, pdf = built(arguments, ".pdf")
        measured = form.measure(pdf)
        self.assertGreaterEqual(measured.filled()[0], 0.4)
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(form.check(content, Path(directory), pdf), [])


class FormTests(unittest.TestCase):
    def broken_pdf(self) -> bytes:
        """The defect of staging -150: one short section per page."""
        text = REPORT.replace("\n## ", "\n\\newpage\n\n## ")
        return built({"title": "Repair café", "content": text}, ".pdf")[1]

    def test_pages_of_one_short_section_each_are_found(self) -> None:
        measured = form.measure(self.broken_pdf())
        self.assertEqual(measured.count, 6)
        self.assertEqual(measured.needed(), 1)
        found = form.findings(measured)
        self.assertEqual(sum("nearly empty" in item for item in found), 5)
        self.assertTrue(
            any("6 pages where its content needs about 1" in i for i in found)
        )
        self.assertGreater(measured.mean_empty(), 0.8)

    def test_a_heading_at_a_page_foot_is_found(self) -> None:
        from reportlab.pdfgen.canvas import Canvas

        output = io.BytesIO()
        canvas = Canvas(output, pagesize=(595, 842))
        for page in range(2):
            canvas.setFont("Helvetica", 12)
            for row in range(50):
                canvas.drawString(50, 790 - row * 14, "A line of body text.")
            if page == 0:
                canvas.setFont("Helvetica", 19)
                canvas.drawString(50, 70, "Budget")
            canvas.showPage()
        canvas.save()
        found = form.findings(form.measure(output.getvalue()))
        self.assertEqual(len(found), 1)
        self.assertIn('ends with the heading "Budget"', found[0])

    def test_the_deliverable_check_names_the_form_gaps(self) -> None:
        gaps = deliverable_check.form_gaps(".pdf", None, self.broken_pdf())
        self.assertEqual(len(gaps), 1)
        self.assertIn("nearly empty", gaps[0])
        self.assertIn("\\newpage", gaps[0])
        content, docx = built({"title": "Repair café", "content": REPORT}, ".docx")
        self.assertEqual(deliverable_check.form_gaps(".docx", content, docx), [])
        self.assertEqual(deliverable_check.form_gaps(".xlsx", content, b""), [])


class SizeTests(unittest.TestCase):
    def test_a_one_page_memo_fits_one_page(self) -> None:
        """An "about one page" request is achievable: short sections flow."""
        content, docx = built({"title": "Memo", "content": REPORT}, ".docx")
        self.assertEqual(breaks_of(docx), (0, 0))
        self.assertEqual(
            form.measure(built({"title": "Memo", "content": REPORT}, ".pdf")[1]).count,
            1,
        )
        item = expectations.Requirement(
            id="r1",
            statement="about one page",
            turn=1,
            method="code",
            check={"part": "size", "unit": "pages", "count": 1, "relation": "about"},
        )
        over = expectations.size_result(item, content, "", {"pages": 4})
        self.assertFalse(over.passed)
        self.assertIn("\\newpage", over.how)
        self.assertTrue(
            expectations.size_result(item, content, "", {"pages": 1}).passed
        )


if __name__ == "__main__":
    unittest.main()
