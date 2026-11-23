"""A report just over the pages asked is laid out compact; a repeated plan
ends the turn with its file.

Staging -162/-163 (2026-11-22), the multi-turn Word report asked "about one
page": the first versions printed on two pages (250 words where a page held
about 236), the rewrite after the one refusal came back on two pages, and
in turn 3 the model sent the same plan again and again after publishing the
table version until the step limit; the person read "the work did not
complete" while the file had been updated.

- A file printed over the asked pages by up to a quarter of a page's
  capacity is first rebuilt with the compact report spacing (spacing and
  margins, never a smaller type) and measured again; it is published as is
  when that fits, without spending the turn's refusal.
- In Work, the same plan sent four times in a row ends the tool phase: with
  a published file the turn is done with a short sentence (its links are the
  code's), otherwise it stops on the step limit at once.

The content is fictional.
"""

from __future__ import annotations

from decimal import Decimal
import io
import json
import os
from pathlib import Path
import tempfile
from typing import Any
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from docx import Document

from packages.doc_render import readback, render
from packages.doc_render.schema import Content
from services.orchestrator.loop import (
    PLAN_REPEATS,
    Call,
    Limits,
    Query,
    Reservation,
    Turn,
    run,
)
from services.orchestrator.project_document_tools import compacted, execute
from services.orchestrator.terminal_client import ProjectPublisher
from services.orchestrator.work_closing import closing_step
from services.retrieval.deliverable_render import Rendered

TURNS = (
    "a short word report about our office trying a 4 day week, about one page, "
    "title it 'Short Weeks at Kestrel Lane'",
    "change the title to 'The Kestrel Experiment' pls",
    "add a table with these numbers: Q1 41 orders, Q2 47, Q3 52",
)
SIZE = {
    "statement": "About one page long",
    "method": "code",
    "part": "size",
    "unit": "pages",
    "count": 1,
    "relation": "about",
}
BODY = "\n\n".join(
    f"## Part {n}\n\n" + "The office at Kestrel Lane kept its usual output. " * 4
    for n in range(5)
)


def turn_tools(index: int) -> Mock:
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
            new = [SIZE] if data["latest_message"] == TURNS[0] else []
            return {"text": json.dumps({"new": new})}
        return {
            "text": json.dumps(
                {
                    "results": [
                        {"id": r["id"], "met": True} for r in data["requirements"]
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
    tools.check_model = Mock(profile="atlas-qwen", spent=Decimal(0), strict=False)
    tools.expectations_complete = complete
    tools.expectations = None
    tools.picture_scope = "reader|conversation-11"
    tools.lang = "en"
    tools.deliverable_refused = False
    tools.current_file = None
    tools.turn_files = {}
    tools.turn_created = set()
    tools.built_titles = {}
    tools.deliverable_checks = 3
    return tools


def margin(raw: bytes) -> float:
    value = Document(io.BytesIO(raw)).sections[0].left_margin
    assert value is not None
    return float(value.inches)


class CompactionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"ATLAS_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    async def three_turns(
        self, prints: list[tuple[int, tuple[float, ...]]]
    ) -> tuple[list[dict[str, Any]], list[bytes]]:
        printed = iter(prints)
        written: list[bytes] = []

        async def publish(document: Any, name: str, *rest: Any) -> dict[str, Any]:
            written.append(bytes(document.content))
            return dict(
                id=str(uuid4()), file_version_id=str(uuid4()), relative_path=name
            )

        def rendered(*args: Any, **kwargs: Any) -> Rendered:
            total, filled = next(printed)
            return Rendered(pages=(), total=total, landscape=False, filled=filled)

        replies: list[dict[str, Any]] = []
        path = "Short_Weeks.docx"
        with (
            patch.object(ProjectPublisher, "publish", AsyncMock(side_effect=publish)),
            patch(
                "services.retrieval.deliverable_render.render_pages",
                side_effect=rendered,
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
            first = dict(
                path=path, title="Short Weeks at Kestrel Lane", content=BODY, sources=[]
            )
            replies.append(
                dict(
                    await execute(
                        turn_tools(0), "publish_document", json.dumps(first), 120
                    )
                )
            )
            edit = readback.editable(readback.read(written[-1], ".docx"))
            edit["title"] = "The Kestrel Experiment"
            replies.append(
                dict(
                    await execute(
                        turn_tools(1),
                        "publish_document",
                        json.dumps(
                            dict(
                                edit, path=path, sources=[], strategy="edited_in_place"
                            )
                        ),
                        120,
                    )
                )
            )
            edit = readback.editable(readback.read(written[-1], ".docx"))
            edit["content"] += (
                "\n\n## Orders\n\n| Quarter | Orders |\n| --- | --- |\n"
                "| Q1 | 41 |\n| Q2 | 47 |\n| Q3 | 52 |"
            )
            replies.append(
                dict(
                    await execute(
                        turn_tools(2),
                        "publish_document",
                        json.dumps(
                            dict(
                                edit, path=path, sources=[], strategy="edited_in_place"
                            )
                        ),
                        120,
                    )
                )
            )
        return replies, written

    async def test_three_turns_just_over_one_page_are_laid_out_compact(self) -> None:
        # Turn 1 prints just over a page, then fits compact; turn 2 fits; the
        # table of turn 3 pushes it over again, and compact it fits.
        replies, written = await self.three_turns(
            [
                (2, (0.8, 0.12)),
                (1, (0.74,)),
                (1, (0.7,)),
                (2, (0.8, 0.15)),
                (1, (0.79,)),
            ]
        )
        for reply in replies:
            self.assertNotIn("error", reply, reply)
            self.assertNotIn("gaps", reply, reply)
            self.assertEqual(reply["length"], {"pages": 1})
        # The first compact level (spacing) was enough each time.
        self.assertEqual([compacted(raw) for raw in written], [1, 0, 1])
        self.assertEqual([margin(raw) for raw in written], [1.0, 1.0, 1.0])
        body = Document(io.BytesIO(written[-1]))
        self.assertEqual(len(body.tables), 1)

    async def test_a_report_far_over_the_size_is_refused_with_its_target(self) -> None:
        printed = iter([(2, (0.8, 0.8))])
        with (
            patch.object(ProjectPublisher, "publish", AsyncMock()),
            patch(
                "services.retrieval.deliverable_render.render_pages",
                side_effect=lambda *a, **k: Rendered(
                    pages=(), total=2, landscape=False, filled=next(printed)[1]
                ),
            ),
            patch(
                "services.orchestrator.work_save.project_file_paths",
                AsyncMock(return_value={}),
            ),
            self.assertRaises(ValueError) as refused,
        ):
            await execute(
                turn_tools(0),
                "publish_document",
                json.dumps(
                    dict(
                        path="Short_Weeks.docx",
                        title="Short Weeks",
                        content=BODY,
                        sources=[],
                    )
                ),
                120,
            )
        self.assertIn("cut about", str(refused.exception))

    def test_the_compact_layout_keeps_the_type_size(self) -> None:
        content = Content.model_validate(
            dict(title="Short Weeks", sections=[dict(heading="A", paragraphs=["x"])])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "r.docx"
            render(content, path, image_root=Path(directory), compact=2)
            document = Document(str(path))
        heading = document.styles["Heading 1"]
        self.assertEqual(heading.paragraph_format.space_before.pt, 6)
        self.assertEqual(heading.font.size.pt, 14)
        top = document.sections[0].top_margin
        assert top is not None
        self.assertEqual(top.inches, 0.75)


PUBLISHED = {
    "trust": "untrusted",
    "deliverable": {
        "id": "f1",
        "file_version_id": "v1",
        "relative_path": "kestrel.docx",
    },
    "text": "[Download kestrel.docx](/d) · [Preview](/p)",
    "file_version_ids": ["v1"],
}
PLAN = json.dumps(
    {"steps": [{"text": "Check the page", "status": "completed"}]}, sort_keys=True
)


class PlanRepeatTests(unittest.IsolatedAsyncioTestCase):
    async def loop(self, publish_first: bool) -> tuple[Any, int]:
        calls = 0

        async def complete(messages: list[dict[str, Any]], timeout: float) -> Turn:
            nonlocal calls
            calls += 1
            if publish_first and calls == 1:
                return Turn(
                    "",
                    (
                        Call(
                            "p1",
                            "publish_document",
                            json.dumps(
                                {
                                    "path": "kestrel.docx",
                                    "content": "# K",
                                    "sources": [],
                                }
                            ),
                        ),
                    ),
                )
            return Turn("", (Call(f"u{calls}", "update_plan", PLAN),))

        async def tool(call: Any, timeout: float) -> dict[str, Any]:
            if call.name == "publish_document":
                return dict(PUBLISHED)
            return {"refused": [1], "next_step": "the check step is marked by the code"}

        model = Mock(
            estimate=Mock(return_value=Reservation(100, Decimal("0.001"))),
            complete=AsyncMock(side_effect=complete),
        )
        tools = Mock(
            estimate=Mock(return_value=Reservation(0, Decimal(0))),
            execute=AsyncMock(side_effect=tool),
        )
        selection = Mock(selection=Mock(deliverable_pending=Mock(return_value=False)))
        with patch("services.orchestrator.code_workspace.enabled", return_value=True):
            limits = Limits(
                profile="atlas-qwen",
                tokens=None,
                tool_calls=40,
                wall_clock=60,
                cost=Decimal("1"),
                model_calls=40,
                code_mode=True,
            )
            result = await run(
                Query(question="add a table to the kestrel report", lang="en"),
                model,
                tools,
                "",
                limits,
                final_only=True,
                after_tools=lambda executed: closing_step(selection, True, executed),
            )
        return result, calls

    async def test_a_repeated_plan_after_the_file_ends_the_turn_with_it(self) -> None:
        result, calls = await self.loop(publish_first=True)
        self.assertEqual(result.state, "done")
        self.assertEqual(result.text, "Here is the file.")
        self.assertLessEqual(calls, PLAN_REPEATS + 3)

    async def test_a_repeated_plan_without_a_file_stops_early(self) -> None:
        result, calls = await self.loop(publish_first=False)
        self.assertNotEqual(result.state, "done")
        self.assertLessEqual(calls, PLAN_REPEATS + 2)


if __name__ == "__main__":
    unittest.main()
