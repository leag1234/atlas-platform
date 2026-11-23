"""The answer describes the delivered file, never the checks behind it.

Staging -159 (2026-11-22), a deck asked "pour presenter le projet au comite
de pilotage": a check reported that the file "is not a proof that the
presentation took place", the retry waited for an approval card, and the
answer told the person that the budget "now appears explicitly" and that
"the regeneration was not approved". The purpose of a file is met by a file
made for it; the instructions of the closing, of a file published with gaps
and of both checks keep the checks, refusals, approvals and corrections of
the turn out of the reply. The content is fictional.
"""

from __future__ import annotations

from typing import Any
import unittest

from services.orchestrator import deliverable_check, expectations, work_closing


class InternalsTests(unittest.TestCase):
    def test_the_closing_describes_the_file_as_delivered(self) -> None:
        for gaps in ((), ("a slide on the budget",)):
            with self.subTest(gaps=gaps):
                text = work_closing.closing_instruction(
                    [{"relative_path": "kestrel.pptx"}], gaps, True
                )
                self.assertIn("Never tell how the file was checked", text)
                self.assertIn("a check that passed or could not run", text)
                self.assertEqual("call a gap normal" in text, bool(gaps))
                self.assertEqual("never say that a gap was fixed" in text, bool(gaps))

    def test_a_file_published_with_gaps_says_them_of_the_file(self) -> None:
        note = deliverable_check.NOTE.format(gaps="a slide on the budget")
        self.assertIn("Say it of the file as delivered", note)
        self.assertIn("never mention this check, a refusal, a regeneration", note)

    def test_a_purpose_is_met_by_a_file_made_for_it(self) -> None:
        for prompt in (deliverable_check.SYSTEM, expectations.JUDGE_SYSTEM):
            with self.subTest(prompt=prompt[:40]):
                self.assertIn("never ask for proof that the event happened", prompt)


class FileNameTests(unittest.IsolatedAsyncioTestCase):
    def test_a_named_file_is_checked_on_the_name_it_is_saved_under(self) -> None:
        item = expectations.Requirement(
            "R1", "Rapport Word nomme kestrel-report.docx", 1
        )
        for saved, passed in (
            ("deliverables/kestrel-report.docx", True),
            ("Kestrel-Report (2).docx", True),
            ("orchard.docx", False),
        ):
            with self.subTest(saved=saved):
                result = expectations.file_name_result(item, saved)
                assert result is not None
                self.assertEqual(result.passed, passed)
        # A name in another format does not name this file: the judge reads
        # the format with the saved name as a fact.
        self.assertIsNone(expectations.file_name_result(item, "kestrel-report.pdf"))
        # A source file the person names is not the deliverable's name
        # (staging -161: "a deck from the file notes/projet.txt").
        source = expectations.Requirement(
            "R3", "Presentation PowerPoint from the file notes/kestrel.txt", 1
        )
        self.assertIsNone(expectations.file_name_result(source, "kestrel.pptx"))
        plain = expectations.Requirement("R2", "A Word report with a conclusion", 1)
        self.assertIsNone(expectations.file_name_result(plain, "kestrel-report.docx"))

    async def test_the_name_never_goes_to_the_judge_as_content(self) -> None:
        """Staging -160: "a Word report named rapport-hirondelle.docx" was
        judged on the text alone, failed, took the turn's one refusal, and
        the answer explained that a DOCX text holds no file name."""
        import json
        import os
        import tempfile
        from decimal import Decimal
        from types import SimpleNamespace
        from unittest.mock import patch

        from packages.doc_render.schema import Content

        seen: list[dict[str, Any]] = []
        asked = "write a Word report named kestrel-report.docx with a conclusion"

        async def complete(
            payload: dict[str, object], seconds: float
        ) -> dict[str, str]:
            messages = payload["messages"]
            assert isinstance(messages, list)
            data = json.loads(messages[1]["content"])
            if "latest_message" in data:
                items = [
                    {
                        "statement": "Word report named kestrel-report.docx",
                        "method": "judge",
                    },
                    {"statement": "A Word document", "method": "judge"},
                ]
                return {"text": json.dumps({"new": items})}
            seen.append(data)
            results = [
                {"id": r["id"], "met": data.get("file_name") == "kestrel-report.docx"}
                for r in data["requirements"]
            ]
            return {"text": json.dumps({"results": results})}

        tools = SimpleNamespace(
            check_model=SimpleNamespace(profile="atlas-qwen", spent=Decimal(0)),
            request_text=asked,
            earlier_requests=[],
            picture_scope="reader|conversation-3",
            lang="en",
            deliverable_refused=False,
            expectations_complete=complete,
        )
        content = Content.model_validate(
            dict(title="Kestrel", sections=[dict(heading="A", paragraphs=["Text."])])
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"ATLAS_DATA_DIR": directory}),
        ):
            results = await expectations.document_check(
                tools, "deliverables/kestrel-report.docx", ".docx", content, "Text.", 60
            )
        self.assertEqual([r.passed for r in results], [True, True])
        # Only the format went to the judge, with the name as a fact.
        self.assertEqual(
            [r["statement"] for r in seen[0]["requirements"]], ["A Word document"]
        )
        self.assertEqual(seen[0]["file_name"], "kestrel-report.docx")


if __name__ == "__main__":
    unittest.main()
