"""A new Word report asked with a page size gets its budget before it is
written (staging -167: the first version came on two pages; staging -170: a
budget the writer had to count was not kept, one number of words is). The content is
fictional."""

from __future__ import annotations

from decimal import Decimal
import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from services.orchestrator import page_budget

ASKED = "a short word report about our office trying a 4 day week, about one page"


def tools(reply: dict[str, Any], **extra: Any) -> SimpleNamespace:
    async def complete(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        data = json.loads(payload["messages"][1]["content"])
        assert data["latest_message"] == ASKED
        return {"text": json.dumps(reply)}

    values: dict[str, Any] = dict(
        current_file=None,
        requested_suffix=".docx",
        check_model=SimpleNamespace(profile="atlas-qwen", spent=Decimal(0)),
        request_text=ASKED,
        earlier_requests=[],
        lang="en",
        expectations_complete=complete,
    )
    values.update(extra)
    return SimpleNamespace(**values)


ONE_PAGE = {
    "new": [
        {"statement": "Short report", "method": "judge"},
        {
            "statement": "About one page",
            "method": "code",
            "part": "size",
            "unit": "pages",
            "count": 1,
            "relation": "about",
        },
    ]
}


class PageBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        environment = patch.dict(os.environ, {"ATLAS_DATA_DIR": directory.name})
        environment.start()
        self.addCleanup(environment.stop)

    async def test_a_one_page_report_gets_its_budget(self) -> None:
        text = await page_budget.first_brief(tools(ONE_PAGE))
        self.assertIn("asks for about 1 page", text)
        self.assertIn("holds about 230 words a page", text)
        self.assertIn("at most about 195 words in all", text)
        self.assertIn("at most 4 short sections", text)
        self.assertEqual(page_budget.brief(2).count("2 pages"), 2)

    async def test_no_budget_without_a_page_size_or_for_a_change(self) -> None:
        none = {"new": [{"statement": "Short report", "method": "judge"}]}
        self.assertEqual(await page_budget.first_brief(tools(none)), "")
        self.assertEqual(
            await page_budget.first_brief(tools(ONE_PAGE, current_file=object())), ""
        )
        self.assertEqual(
            await page_budget.first_brief(tools(ONE_PAGE, requested_suffix=".pptx")),
            "",
        )


if __name__ == "__main__":
    unittest.main()
