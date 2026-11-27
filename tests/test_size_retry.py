"""A file printed over the size asked is refused with a measured target.

Staging -158 (2026-11-22): a report asked "about one page" was refused for
its two pages with "shorten it to the size asked", and both rewrites came
back on two pages (the creation, and the version a table was added to). The
refusal now gives the printed pages, the words, how many words a page of
this layout holds (measured on the print) and how many to cut. The report
spacing is tighter within the house style (12 pt above a heading, 8 pt under
the title, one-inch side margins; type sizes unchanged), and a version that
shortens text to stay within the asked size is not reported as an
unrequested change. The content is fictional.
"""

from __future__ import annotations

from decimal import Decimal

import json
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from docx import Document

from packages.doc_render import render
from packages.doc_render.schema import Content
from services.orchestrator import deliverable_check, expectations
from services.orchestrator.project_document_tools import execute
from services.orchestrator.terminal_client import ProjectPublisher
from services.retrieval.deliverable_render import Rendered

ASKED = "a short report on our office trying a 4 day week, about one page"
BODY = "\n\n".join(
    f"## Part {n}\n\n" + "The office at Kestrel Lane kept its usual output. " * 6
    for n in range(6)
)


def page_item() -> expectations.Requirement:
    return expectations.Requirement(
        "R1",
        "About one page",
        1,
        method="code",
        check={"part": "size", "unit": "pages", "count": 1, "relation": "about"},
    )


class TargetTests(unittest.TestCase):
    def test_the_capacity_of_a_page_is_measured_on_the_print(self) -> None:
        # 282 words over a full first page and a third of the second.
        self.assertEqual(expectations.page_capacity(282, (0.76, 0.31)), 210)
        self.assertEqual(expectations.page_capacity(150, (0.4,)), 300)
        self.assertIsNone(expectations.page_capacity(0, (0.5,)))
        self.assertIsNone(expectations.page_capacity(120, ()))

    def test_the_refusal_names_pages_words_capacity_and_the_cut(self) -> None:
        content = Content.model_validate(
            dict(title="Short Weeks", sections=[dict(heading="A", paragraphs=["x"])])
        )
        measures: dict[str, int | None] = {
            "pages": 2,
            "words": 282,
            "words_per_page": 210,
        }
        result = expectations.size_result(page_item(), content, "", measures)
        self.assertFalse(result.passed)
        self.assertIn("the file is 2 pages of 282 words", result.how)
        self.assertIn("holds about 210 words", result.how)
        self.assertIn("about 189 words (cut about 93)", result.how)
        self.assertIn("keep every section, table and part", result.how)
        # Unmeasured, the refusal stays as it was.
        measures["words_per_page"] = None
        plain = expectations.size_result(page_item(), content, "", measures)
        self.assertNotIn("cut about", plain.how)

    def test_a_pdf_report_is_measured_without_a_renderer(self) -> None:
        content = Content.model_validate(
            dict(
                title="Short Weeks",
                sections=[
                    dict(heading=f"Part {n}", paragraphs=["Output held. " * 60])
                    for n in range(6)
                ],
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "r.pdf"
            render(content, path, image_root=Path(directory))
            raw = path.read_bytes()
        import asyncio

        measures = asyncio.run(
            expectations.file_measures(
                content, "Output held. " * 360, ".pdf", raw, {"pages"}, 30
            )
        )
        pages, words = measures["pages"], measures["words_per_page"]
        assert isinstance(pages, int) and isinstance(words, int)
        self.assertGreater(pages, 1)
        self.assertGreater(words, 100)

    def test_the_report_spacing_is_tighter_with_the_same_type(self) -> None:
        content = Content.model_validate(
            dict(title="Short Weeks", sections=[dict(heading="A", paragraphs=["x"])])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "r.docx"
            render(content, path, image_root=Path(directory))
            document = Document(str(path))
        heading = document.styles["Heading 1"]
        self.assertEqual(heading.paragraph_format.space_before.pt, 12)
        self.assertEqual(heading.font.size.pt, 14)
        self.assertEqual(document.styles["Title"].paragraph_format.space_after.pt, 8)
        margin = document.sections[0].left_margin
        assert margin is not None
        self.assertEqual(margin.inches, 1.0)

    def test_shortening_for_an_asked_size_is_not_an_unrequested_change(self) -> None:
        self.assertIn("within a size the person asked", deliverable_check.REVISION)


def work_tools() -> Mock:
    execution = Mock()
    execution.check = AsyncMock()

    async def public(method: str, path: str) -> tuple[int, dict[str, Any]]:
        if path == "folders":
            return 200, dict(items=[dict(id="folder", revision=0)])
        return 200, dict(deliverables_folder_id="folder")

    execution.broker.public = AsyncMock(side_effect=public)

    async def complete(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        data = json.loads(payload["messages"][1]["content"])
        if "latest_message" in data:
            item = {
                "statement": "About one page",
                "method": "code",
                "part": "size",
                "unit": "pages",
                "count": 1,
                "relation": "about",
            }
            return {"text": json.dumps({"new": [item]})}
        return {"text": json.dumps({"results": []})}

    tools = Mock(execution=execution)
    tools.deliverables = []
    tools.requested_suffix = ".docx"
    tools.numbered_copies = False
    tools.request_text = ASKED
    tools.earlier_requests = []
    tools.calculation_refusals = set()
    tools.check_model = Mock(
        profile="atlas-qwen", spent=Decimal(0), strict=False, reasoning_room=0
    )
    tools.expectations_complete = complete
    tools.expectations = None
    tools.picture_scope = "reader|conversation-9"
    tools.lang = "en"
    tools.deliverable_refused = False
    tools.current_file = None
    tools.turn_files = {}
    tools.built_titles = {}
    tools.deliverable_checks = 3
    return tools


class RefusalPathTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"ATLAS_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_a_two_page_report_is_refused_with_its_target(self) -> None:
        tools = work_tools()
        arguments = dict(
            path="Short_Weeks.docx", title="Short Weeks", content=BODY, sources=[]
        )
        with (
            patch.object(
                ProjectPublisher,
                "publish",
                AsyncMock(
                    side_effect=lambda document, name, *rest: dict(
                        id=str(uuid4()),
                        file_version_id=str(uuid4()),
                        relative_path=name,
                    )
                ),
            ),
            patch(
                "services.retrieval.deliverable_render.render_pages",
                return_value=Rendered(
                    pages=(), total=2, landscape=False, filled=(0.8, 0.3)
                ),
            ),
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
            self.assertRaises(ValueError) as refused,
        ):
            await execute(tools, "publish_document", json.dumps(arguments), 120)
        text = str(refused.exception)
        self.assertIn("the file is 2 pages of", text)
        self.assertIn("a page of this layout holds about", text)
        self.assertIn("cut about", text)


if __name__ == "__main__":
    unittest.main()
