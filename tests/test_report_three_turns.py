"""A short one-page report, asked over three turns, keeps its asked title.

Staging -156 (2026-11-22), the multi-turn Word report of the battery:

1. Turn 1 was refused once for its size, and the rewrite came without a
   title argument, opening with the asked title as a plain line: the file
   was titled from its name ("Four Days Full Output") with the asked title
   below it as body text, and the title change and the table of the next
   turns kept the wrong title.
2. "a short word report" was read as "about 1 word": the one-page report of
   154 words failed, was cut to a stub, and every answer opened with defects.
3. The check found the wrong title but the turn had spent its one refusal.

This test runs the three turns through publish_document in a project: the
creation with a size refusal and a rewrite, the title change and the table,
each version read back as the next turn starts from it. The content is
fictional.
"""

from __future__ import annotations

from decimal import Decimal
import io
import json
import os
import tempfile
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from docx import Document

from packages.doc_render import readback
from services.orchestrator import expectations
from services.orchestrator.project_document_tools import execute
from services.orchestrator.terminal_client import ProjectPublisher
from services.retrieval.deliverable_render import Rendered

TURNS = (
    "hi, could you put together a short word report about our office trying a "
    "4 day week? about one page, title it 'Short Weeks, Full Output'",
    "change the title to 'The Kestrel Experiment' pls",
    "add a table with these numbers: Q1 41 orders, Q2 47, Q3 52",
)
# What the extraction profiles of staging -156 replied, per message.
EXTRACTED = {
    TURNS[0]: {
        "new": [
            {
                "statement": "Short word report",
                "method": "size",
                "unit": "words",
                "count": 1,
            },
            {
                "statement": "About one page long",
                "method": "code",
                "code": {
                    "part": "size",
                    "unit": "pages",
                    "count": 1,
                    "relation": "about",
                },
            },
            {"statement": "Title 'Short Weeks, Full Output'", "method": "judge"},
        ]
    },
    TURNS[1]: {
        "changed": [
            {
                "id": "R3",
                "statement": "Title 'The Kestrel Experiment'",
                "quote": "change the title to 'The Kestrel Experiment'",
            }
        ]
    },
    TURNS[2]: {
        "new": [
            {
                "statement": "Table of orders per quarter",
                "method": "code",
                "part": "table",
                "words": ["orders"],
            }
        ]
    },
}
BODY = "\n\n".join(
    f"## {heading}\n\n" + "The office at Kestrel Lane kept its usual output. " * 5
    for heading in ("Summary", "Findings", "Recommendations", "Conclusion")
)


def turn_tools(index: int, judged: list[dict[str, Any]]) -> Mock:
    """The tools of one project Work turn, with scripted ledger replies."""
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
            return {"text": json.dumps(EXTRACTED.get(data["latest_message"], {}))}
        judged.append(data)
        pages = (data.get("facts") or {}).get("pages")
        return {
            "text": json.dumps(
                {
                    "results": [
                        {
                            "id": item["id"],
                            # A judge that reads a vague length against the
                            # measured pages, as its instructions say.
                            "met": "Short word" not in item["statement"] or pages == 1,
                            "why": f"{pages} pages",
                        }
                        for item in data["requirements"]
                    ]
                }
            )
        }

    tools = Mock(execution=execution)
    tools.deliverables = []
    tools.requested_suffix = ".docx" if index == 0 else None
    tools.numbered_copies = False
    tools.request_text = TURNS[index]
    tools.earlier_requests = list(TURNS[:index])
    tools.calculation_refusals = set()
    tools.check_model = Mock(
        profile="atlas-qwen", spent=Decimal(0), strict=False, reasoning_room=0
    )
    tools.expectations_complete = complete
    tools.expectations = None
    tools.picture_scope = "reader|conversation-7"
    tools.lang = "en"
    tools.deliverable_refused = False
    tools.current_file = None
    tools.built_titles = None
    # The model check of the file against the request is not this test's.
    tools.deliverable_checks = 3
    return tools


def title_of(raw: bytes) -> tuple[str, str, list[str]]:
    document = Document(io.BytesIO(raw))
    titles = [
        p.text for p in document.paragraphs if getattr(p.style, "name", "") == "Title"
    ]
    body = [
        p.text for p in document.paragraphs if getattr(p.style, "name", "") != "Title"
    ]
    return document.core_properties.title, titles[0] if titles else "", body


class ThreeTurnReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"ATLAS_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_the_asked_title_stays_through_refusal_rewrite_and_edits(
        self,
    ) -> None:
        written: list[bytes] = []
        pages = iter([2, 1, 1, 1, 1, 1, 1])

        async def publish(document: Any, name: str, *rest: Any) -> dict[str, Any]:
            written.append(bytes(document.content))
            return dict(
                id=str(uuid4()), file_version_id=str(uuid4()), relative_path=name
            )

        judged: list[dict[str, Any]] = []
        path = "Short_Weeks_Full_Output.docx"
        with (
            patch.object(ProjectPublisher, "publish", AsyncMock(side_effect=publish)),
            patch(
                "services.retrieval.deliverable_render.render_pages",
                side_effect=lambda *a, **k: Rendered(
                    pages=(), total=next(pages), landscape=False
                ),
            ),
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
            patch(
                "services.orchestrator.file_results.project_deliverable_links",
                return_value="links",
            ),
        ):
            # Turn 1: a titled first version, refused for its two pages.
            tools = turn_tools(0, judged)
            first = dict(
                path=path,
                title="Short Weeks, Full Output",
                content=BODY + "\n\n" + BODY,
                sources=[],
            )
            with self.assertRaises(ValueError) as refused:
                await execute(tools, "publish_document", json.dumps(first), 120)
            self.assertIn("About one page long", str(refused.exception))
            # "Short" is not a count of words the person wrote: it is judged
            # on the measured pages, never as "about 1 word".
            self.assertNotIn("words, about 1", str(refused.exception))
            # The rewrite: no title argument, the title as its opening line.
            rewrite = dict(
                path=path,
                content="Short Weeks, Full Output\n\n" + BODY,
                sources=[],
                strategy="regenerated",
            )
            reply = await execute(tools, "publish_document", json.dumps(rewrite), 120)
            self.assertNotIn("gaps", reply, reply)
            self.assertEqual(reply["length"], {"pages": 1})
            core, title, body = title_of(written[-1])
            self.assertEqual((core, title), ("Short Weeks, Full Output",) * 2)
            self.assertNotIn("Short Weeks, Full Output", body)
            self.assertNotIn("Short Weeks Full Output", body)

            # Turn 2: the title changed in the version read back.
            tools = turn_tools(1, judged)
            edit = readback.editable(readback.read(written[-1], ".docx"))
            edit["title"] = "The Kestrel Experiment"
            reply = await execute(
                tools,
                "publish_document",
                json.dumps(
                    dict(edit, path=path, sources=[], strategy="edited_in_place")
                ),
                120,
            )
            self.assertNotIn("gaps", reply, reply)
            core, title, body = title_of(written[-1])
            self.assertEqual((core, title), ("The Kestrel Experiment",) * 2)
            self.assertFalse(any("Short Weeks" in text for text in body))

            # Turn 3: a table added to that version.
            tools = turn_tools(2, judged)
            edit = readback.editable(readback.read(written[-1], ".docx"))
            edit["content"] += (
                "\n\n## Orders\n\n| Quarter | Orders |\n| --- | --- |\n"
                "| Q1 | 41 |\n| Q2 | 47 |\n| Q3 | 52 |"
            )
            reply = await execute(
                tools,
                "publish_document",
                json.dumps(
                    dict(edit, path=path, sources=[], strategy="edited_in_place")
                ),
                120,
            )
            self.assertNotIn("gaps", reply, reply)
            core, title, body = title_of(written[-1])
            self.assertEqual((core, title), ("The Kestrel Experiment",) * 2)
            self.assertEqual(len(Document(io.BytesIO(written[-1])).tables), 1)
        # The vague length went to the judge with the measured pages.
        self.assertTrue(
            any(
                item["statement"] == "Short word report" and data["facts"]["pages"] == 1
                for data in judged
                for item in data["requirements"]
            )
        )

    def test_a_document_without_a_title_argument_takes_its_own(self) -> None:
        from packages.doc_render.schema import Content
        from services.orchestrator.project_document_input import normalize

        for text, body in (
            ("# Short Weeks, Full Output\n\n" + BODY, "Summary"),
            ("Short Weeks, Full Output\n\n" + BODY, "Summary"),
            ("**Short Weeks, Full Output**\n\n" + BODY, "Summary"),
        ):
            with self.subTest(text=text[:30]):
                content = Content.model_validate(
                    normalize(dict(path="Short_Weeks.docx", content=text, sources=[]))[
                        "content"
                    ]
                )
                self.assertIn(
                    content.title,
                    ("Short Weeks, Full Output", "**Short Weeks, Full Output**"),
                )
                self.assertFalse(
                    any(
                        "Short Weeks, Full Output" in p
                        for s in content.sections[1:]
                        for p in s.paragraphs
                    )
                )
                self.assertIn(body, [s.heading for s in content.sections])
        # A sentence that opens the text is not a title: the name stays.
        content = Content.model_validate(
            normalize(
                dict(
                    path="Short_Weeks.docx",
                    content="Our office tried a short week.\n\n" + BODY,
                    sources=[],
                )
            )["content"]
        )
        self.assertEqual(content.title, "Short Weeks")

    def test_only_a_word_count_the_person_wrote_is_a_size(self) -> None:
        ledger = expectations.Ledger("document")
        ledger.apply(
            {
                "new": [
                    {
                        "statement": "Short",
                        "method": "size",
                        "unit": "words",
                        "count": 1,
                    },
                    {
                        "statement": "At most 1 500 words",
                        "method": "code",
                        "part": "size",
                        "unit": "words",
                        "count": 1500,
                        "relation": "max",
                    },
                    {
                        "statement": "Five slides",
                        "method": "code",
                        "part": "size",
                        "unit": "slides",
                        "count": 5,
                    },
                ]
            },
            1,
            ["a short deck of five slides, at most 1 500 words"],
        )
        methods = {item.statement: item.method for item in ledger.active()}
        self.assertEqual(
            methods,
            {"Short": "judge", "At most 1 500 words": "code", "Five slides": "code"},
        )


if __name__ == "__main__":
    unittest.main()
