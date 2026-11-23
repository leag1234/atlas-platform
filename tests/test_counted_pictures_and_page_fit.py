"""A file asked with a number of pictures and a number of pages: the pictures
are counted on the built file, a picture short is said with the measured
numbers (never refused for a rewrite that cannot draw it), and pictures
shrink by code, level by level, until the file prints on the pages asked
(a one-page sheet with two pictures printed on two pages: each picture took
320 points of an A4 page). An HTML page or small app is built in the app
panel. All content is fictional."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest

from docx import Document
from PIL import Image
from pypdf import PdfReader

from packages.doc_render import render
from packages.doc_render.layout import A4, PICTURE_SHARE, report_geometry
from packages.doc_render.renderer import COMPACT_LEVELS
from packages.doc_render.schema import Content
from packages.doc_render.verify import printed_pictures
from services.orchestrator import expectations
from services.orchestrator import request_router
from services.orchestrator.project_document_tools import compacted_to_size

NAMES = ("picture-0a1b2c3d4e5f.jpg", "picture-9f8e7d6c5b4a.jpg")


def jpeg(colour: str, side: int = 768) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (side, side), colour).save(output, "JPEG")
    return output.getvalue()


def sheet(pictures: int = 2, emoji: bool = False) -> Content:
    sections = [
        dict(
            heading=heading,
            paragraphs=[text + (" \U0001f331" if emoji else "")],
            images=[NAMES[index]] if index < pictures else [],
        )
        for index, (heading, text) in enumerate(
            (
                ("Seed tray", "The seed tray of the rooftop garden, freshly sown."),
                ("Sprouted tray", "The same tray ten days later, in leaf."),
            )
        )
    ]
    return Content.model_validate(
        dict(title="Garden club sheet: sowing cress", sections=sections)
    )


def built(content: Content, suffix: str, compact: int = 0) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, colour in zip(NAMES, ("teal", "olive")):
            (root / name).write_bytes(jpeg(colour))
        path = root / ("sheet" + suffix)
        render(content, path, image_root=root, compact=compact)
        return path.read_bytes()


def pages(raw: bytes) -> int:
    return len(PdfReader(io.BytesIO(raw)).pages)


class PrintedPictureTests(unittest.TestCase):
    def test_each_picture_drawn_is_counted_on_the_file(self) -> None:
        for count in (0, 1, 2):
            with self.subTest(count=count):
                content = sheet(count)
                self.assertEqual(
                    printed_pictures(built(content, ".pdf"), ".pdf"), count
                )
                self.assertEqual(
                    printed_pictures(built(content, ".docx"), ".docx"), count
                )

    def test_an_emoji_drawn_as_a_small_image_is_not_a_picture(self) -> None:
        raw = built(sheet(1, emoji=True), ".pdf")
        self.assertEqual(printed_pictures(raw, ".pdf"), 1)

    def test_an_unreadable_file_is_not_measured(self) -> None:
        self.assertIsNone(printed_pictures(b"not a pdf", ".pdf"))


class PictureFitTests(unittest.TestCase):
    def test_pictures_shrink_level_by_level(self) -> None:
        heights = [report_geometry(sheet(), level).image_height for level in range(5)]
        self.assertEqual(heights[0], A4.image_height)
        self.assertEqual(heights, sorted(heights, reverse=True))
        self.assertEqual(len(PICTURE_SHARE), COMPACT_LEVELS + 1)

    def test_a_two_picture_sheet_fits_one_page_at_a_compact_level(self) -> None:
        content = sheet(2)
        self.assertEqual(pages(built(content, ".pdf")), 2)
        fitted = [
            level
            for level in range(1, COMPACT_LEVELS + 1)
            if pages(built(content, ".pdf", level)) == 1
        ]
        self.assertTrue(fitted)
        # Smaller, never dropped: both pictures are still printed.
        raw = built(content, ".pdf", fitted[0])
        self.assertEqual(printed_pictures(raw, ".pdf"), 2)

    def test_a_word_report_narrows_its_pictures(self) -> None:
        widths = [
            Document(io.BytesIO(built(sheet(1), ".docx", level))).inline_shapes[0].width
            for level in (0, COMPACT_LEVELS)
        ]
        self.assertLess(widths[1], widths[0])


PAGES = expectations.Requirement(
    "R1",
    "On one page",
    1,
    method="code",
    check={"part": "size", "unit": "pages", "count": 1, "relation": "exact"},
)
PICTURES = expectations.Requirement(
    "R2",
    "Exactly two pictures",
    1,
    method="code",
    check={"part": "size", "unit": "pictures", "count": 2, "relation": "exact"},
)


def ledger_tools(*items: expectations.Requirement, **fields: Any) -> Any:
    turn = expectations.TurnLedger(
        "k", expectations.Ledger("document", list(items), len(items) + 1), "p", "en"
    )
    return SimpleNamespace(expectations=turn, lang="en", **fields)


class CompactPdfTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_pdf_with_pictures_is_fitted_to_its_pages(self) -> None:
        content = sheet(2)
        raw = built(content, ".pdf")
        tools = ledger_tools(
            PAGES, file_measures={"pages": 2, "words": 30, "span_permille": 300}
        )
        pictures = {name: jpeg("teal") for name in NAMES}
        with self.assertLogs(
            "services.orchestrator.project_document_tools", "WARNING"
        ) as logs:
            compact, results = await compacted_to_size(
                tools,
                content,
                ".pdf",
                SimpleNamespace(content=raw),
                [expectations.Result("R1", False, "2 pages")],
                [],
                pictures,
                120,
            )
        self.assertEqual([r.passed for r in results], [True])
        self.assertEqual(pages(compact.content), 1)
        self.assertEqual(printed_pictures(compact.content, ".pdf"), 2)
        self.assertEqual(tools.file_measures["pages"], 1)
        self.assertTrue(any('"fits": true' in line for line in logs.output))

    async def test_a_pdf_without_pictures_is_left_as_built(self) -> None:
        content = sheet(0)
        document = SimpleNamespace(content=built(content, ".pdf"))
        tools = ledger_tools(PAGES, file_measures={"pages": 2, "words": 30})
        kept, results = await compacted_to_size(
            tools,
            content,
            ".pdf",
            document,
            [expectations.Result("R1", False, "2 pages")],
            [],
            {},
            120,
        )
        self.assertIs(kept, document)
        self.assertEqual([r.passed for r in results], [False])


class PictureCountTests(unittest.IsolatedAsyncioTestCase):
    def test_a_number_of_pictures_is_a_size_code_checks(self) -> None:
        method, check = expectations.normalized_check(
            "document",
            {
                "method": "code",
                "part": "size",
                "unit": "pictures",
                "count": 3,
                "relation": "exact",
            },
        )
        self.assertEqual(
            (method, check["unit"], check["count"]), ("code", "pictures", 3)
        )

    async def test_the_pictures_are_counted_on_the_built_file(self) -> None:
        content = sheet(1)
        measures = await expectations.file_measures(
            content, "", ".pdf", built(content, ".pdf"), {"pictures"}, 30
        )
        self.assertEqual(measures["pictures"], 1)
        result = expectations.size_result(PICTURES, content, "", measures)
        self.assertFalse(result.passed)
        self.assertIn("the file has 1 pictures, exactly 2 were asked", result.how)
        self.assertNotIn("shorten", result.how)

    def test_a_counted_picture_part_needs_its_number(self) -> None:
        item = expectations.Requirement(
            "R3",
            "Two photos of the tray",
            1,
            method="code",
            check={"part": "picture", "words": ["tray"], "count": 2},
        )
        one = expectations.document_result(item, sheet(1), "")
        two = expectations.document_result(item, sheet(2), "")
        self.assertFalse(one.passed)
        self.assertIn("1 pictures", one.how)
        self.assertTrue(two.passed)

    def test_pictures_not_drawable_in_the_turn_are_said_not_refused(self) -> None:
        missing = [expectations.Result("R2", False, "the file has 1 pictures")]
        tools = ledger_tools(PICTURES, pictures_pending=["Sprouted tray"])
        expectations.document_refusal(tools, missing)
        self.assertFalse(getattr(tools, "deliverable_refused", False))
        self.assertTrue(expectations.document_gaps(tools, missing))
        # Drawn but not placed: the writer can place it, so it is refused.
        placeable = ledger_tools(PICTURES, pictures_pending=[])
        with self.assertRaises(ValueError):
            expectations.document_refusal(placeable, missing)


def decision(kind: str, file_format: str | None = None) -> request_router.Decision:
    return request_router.Decision.model_validate(
        {"kind": kind, "file_format": file_format}
    )


class HtmlRouteTests(unittest.TestCase):
    def test_a_page_asked_as_an_html_file_is_built_in_the_app_panel(self) -> None:
        for text in (
            "Kannst du mir einen Würfelzähler als einzelne HTML-Datei bauen?",
            "make me a tiny habit tracker, one index.html please",
            "une page de minuteur pour la cuisine en html",
        ):
            with self.subTest(text=text):
                routed = request_router.settle(text, decision("file", "md"))
                assert routed is not None
                self.assertEqual((routed.kind, routed.file_format), ("app", None))

    def test_another_named_format_stays_a_file(self) -> None:
        text = "turn this html page into a PDF handout for the choir"
        routed = request_router.settle(text, decision("file", "pdf"))
        assert routed is not None
        self.assertEqual((routed.kind, routed.file_format), ("file", "pdf"))

    def test_without_html_a_file_stays_a_file(self) -> None:
        routed = request_router.settle(
            "a Markdown file listing the lighthouse keepers", decision("file", "md")
        )
        assert routed is not None
        self.assertEqual(routed.kind, "file")

    def test_a_link_to_an_html_page_is_not_a_format(self) -> None:
        self.assertFalse(
            request_router.names_html("summarise https://example.org/notes.html")
        )


if __name__ == "__main__":
    unittest.main()
