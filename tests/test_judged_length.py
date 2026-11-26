"""A length the ledger leaves to the judge is judged on the printed pages.

Staging -154 (2026-11-22): "a short word report ... about one page" was
published three times on two pages. The extraction profiles wrote the size
check in shapes the ledger did not read ("method": {"code": {...}}, the
fields under "code", a JSON text, "method": "size"), so "About one page
long" went to the judge (whose own description also named "a length"); the
judge read only the text and passed it, no page count was measured, and the
answer called the file a one-page report. Now every such shape is read as
the size check; whenever a requirement still goes to the judge, the pages of
a document are printed and measured, the judge receives them as facts, and
the closing states them.

This test runs the project Work path: publish_document in a project, the
ledger with a judged length, the refusal, the second publication and the
closing instruction. The content is fictional.
"""

from __future__ import annotations

from decimal import Decimal
import json
import os
import tempfile
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from services.orchestrator import expectations, work_closing
from services.orchestrator.project_document_tools import execute
from services.orchestrator.terminal_client import ProjectPublisher
from services.retrieval.deliverable_render import Rendered

ASKED = (
    "hi, could you put together a short word report about our office trying "
    "a 4 day week? about one page, title it 'Short Weeks at Kestrel Lane'"
)
REPORT = "\n\n".join(
    f"## Part {n}\n\n"
    + "The Kestrel Lane office kept its usual output during the trial. " * 6
    for n in range(8)
)


def work_tools(judged: list[dict[str, Any]]) -> Mock:
    """The tools of one project Work turn, with a scripted ledger model."""
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
            # The extraction of staging -154: the length left to the judge.
            return {
                "text": json.dumps(
                    {
                        "new": [
                            {
                                "statement": "Short report on the 4 day week",
                                "method": "judge",
                            },
                            {"statement": "About one page long", "method": "judge"},
                        ]
                    }
                )
            }
        judged.append(data)
        pages = (data.get("facts") or {}).get("pages")
        return {
            "text": json.dumps(
                {
                    "results": [
                        {
                            "id": item["id"],
                            # A judge reading the text alone passed the length.
                            "met": not (
                                "page" in item["statement"]
                                and isinstance(pages, int)
                                and pages > 1
                            ),
                            "why": f"the file prints on {pages} pages",
                        }
                        for item in data["requirements"]
                    ]
                }
            )
        }

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
    tools.picture_scope = "reader|conversation-1"
    tools.lang = "en"
    tools.deliverable_refused = False
    tools.current_file = None
    # The model check of the file against the request is not this test's:
    # its calls are used up, the ledger runs.
    tools.deliverable_checks = 3
    return tools


class JudgedLengthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(
            os.environ,
            {"ATLAS_DATA_DIR": directory.name},
        )
        environment.start()
        self.addCleanup(environment.stop)

    def test_the_extraction_never_sends_a_length_to_the_judge(self) -> None:
        ledger = expectations.Ledger("document")
        system = expectations.extraction_messages(ledger, ASKED, [])[0]["content"]
        self.assertNotIn("a tone, a length", system)
        self.assertIn("A length of the whole file", system)
        ledger.requirements.append(
            expectations.Requirement("R1", "About one page long", 1)
        )
        self.assertEqual(expectations.measured_units(ledger, ".docx"), {"pages"})
        self.assertEqual(expectations.measured_units(ledger, ".pptx"), set())

    def test_every_reply_shape_of_a_size_is_a_code_check(self) -> None:
        """The shapes the staging profiles gave for "about one page" (-154)."""
        size = {"part": "size", "unit": "pages", "count": 1, "relation": "about"}
        for raw in (
            {"method": "code", **size},
            {"method": {"code": size}},
            {"method": "code", "code": size},
            {"method": json.dumps(size)},
            {"method": size},
            {"check": size},
            {"method": "size", "unit": "pages", "count": 1, "relation": "about"},
        ):
            with self.subTest(raw=raw):
                self.assertEqual(
                    expectations.normalized_check("document", raw), ("code", size)
                )
        # A judged statement stays judged, an unreadable shape too.
        self.assertEqual(
            expectations.normalized_check(
                "document", {"method": "judge", "judge": "a warm tone"}
            ),
            ("judge", {}),
        )
        self.assertEqual(
            expectations.normalized_check("document", {"method": "{not json"}),
            ("judge", {}),
        )

    async def test_a_two_page_report_is_refused_then_said_with_its_length(self) -> None:
        judged: list[dict[str, Any]] = []
        tools = work_tools(judged)
        write = AsyncMock(
            side_effect=lambda document, name, *rest: dict(
                id=str(uuid4()), file_version_id=str(uuid4()), relative_path=name
            )
        )
        arguments = dict(
            path="Short_Weeks.docx",
            title="Short Weeks at Kestrel Lane",
            content=REPORT,
            sources=[],
        )
        with (
            patch.object(ProjectPublisher, "publish", write),
            patch(
                "services.retrieval.deliverable_render.render_pages",
                return_value=Rendered(pages=(), total=2, landscape=False),
            ) as render,
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
            patch(
                "services.orchestrator.file_results.project_deliverable_links",
                return_value="links",
            ),
        ):
            # The refusal reaches the Work loop as the tool's error.
            with self.assertRaises(ValueError) as refused:
                await execute(tools, "publish_document", json.dumps(arguments), 120)
            self.assertIn("About one page long", str(refused.exception))
            self.assertIn("2 pages", str(refused.exception))
            self.assertEqual(judged[0]["facts"]["pages"], 2)
            write.assert_not_awaited()
            # The same file again: published, the length said.
            second = await execute(
                tools, "publish_document", json.dumps(arguments), 120
            )
            self.assertGreaterEqual(render.call_count, 2)
        self.assertNotIn("error", second, second)
        self.assertEqual(second["length"], {"pages": 2})
        self.assertTrue(any("About one page long" in gap for gap in second["gaps"]))
        closing = work_closing.closing_step(
            Mock(selection=None), True, [("publish_document", second)]
        )
        assert closing is not None
        self.assertIn("Short_Weeks.docx has 2 pages", closing)
        self.assertIn("never say it fits on fewer pages", closing)


if __name__ == "__main__":
    unittest.main()
