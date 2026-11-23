"""A request to make a document is delivered as that document (staging
-179: "could you put together a short word report ... about one page" was
routed to research; the turn searched the web and answered without the
file). The content is fictional."""

from __future__ import annotations

import unittest

from packages.work_intent import named_office_format
from services.orchestrator.loop import error_word
from services.orchestrator.request_router import Decision, _settle

ASKED = (
    "hi, could you put together a short word report about our office trying a "
    "4 day week? about one page, title it 'Short Weeks at Kestrel Lane'"
)


class NamedFileTests(unittest.TestCase):
    def test_a_named_document_is_a_file_request(self) -> None:
        for text, expected in (
            (ASKED, "docx"),
            ("can you compile an excel table of my expenses", "xlsx"),
            ("rédige un rapport Word sur le télétravail", "docx"),
            ("what is a word report?", None),
            ("how many words in a word report?", None),
            ("I read a word document yesterday", None),
        ):
            with self.subTest(text=text[:40]):
                self.assertEqual(named_office_format(text), expected)

    def test_research_never_replaces_the_document(self) -> None:
        for kind in ("research", "answer"):
            with self.subTest(kind=kind):
                routed = Decision.model_validate(
                    {"kind": kind, "needs_research": kind == "research"}
                )
                settled, rule = _settle(ASKED, routed)
                assert settled is not None
                self.assertEqual((settled.kind, settled.file_format), ("file", "docx"))
                self.assertEqual(rule, "named_file")
                self.assertEqual(settled.needs_research, kind == "research")
        # A follow-up or a request naming no file keeps the model's decision.
        follow = Decision.model_validate({"kind": "research", "follow_up": True})
        self.assertEqual(_settle(ASKED, follow)[0], follow)
        plain = Decision.model_validate({"kind": "research"})
        self.assertEqual(_settle("what is the 4 day week?", plain)[0], plain)


class ErrorWordTests(unittest.TestCase):
    def test_an_error_message_is_logged_by_its_first_word(self) -> None:
        self.assertEqual(
            error_word("deliverable_check: the file was built but not published"),
            "deliverable_check",
        )
        self.assertEqual(error_word("expectations: the file ..."), "expectations")
        self.assertEqual(error_word("Document rendering failed"), "document")
        self.assertIsNone(error_word("search_unavailable"))
        self.assertIsNone(error_word(None))
        self.assertEqual(error_word("!!"), "unknown")


if __name__ == "__main__":
    unittest.main()
