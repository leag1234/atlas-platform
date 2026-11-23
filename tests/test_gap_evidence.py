"""A gap is told only on positive evidence, and an edit that makes room never
drops a list or recommendation item (staging -181: a Word table "missing Q3"
while its row was there; the table turn dropped "error rates" from a success
measure). The content is fictional."""

from __future__ import annotations

import json
import unittest

from services.orchestrator import current_file, deliverable_check

TEXT = (
    "Short Weeks at Kestrel Lane\nOrders\nQuarter | Orders | Sick days\n"
    "Q1 | 41 | 3\nQ2 | 47 | 2\nQ3 | 52 | 1\n"
)


class EvidenceTests(unittest.TestCase):
    def reply(self, *gaps: object) -> str:
        return json.dumps({"gaps": list(gaps), "variants": []})

    def test_a_missing_item_the_text_shows_is_no_gap(self) -> None:
        with self.assertLogs("services.orchestrator.deliverable_check", "WARNING"):
            found = deliverable_check.gaps_of(
                self.reply(
                    {"gap": "the table lacks Q3 data", "missing": "Q3 | 52 | 1"},
                    {"gap": "the table lacks Q4 data", "missing": "Q4"},
                    {"gap": "the title is wrong", "missing": ""},
                    "a plain gap",
                ),
                TEXT,
            )
        self.assertEqual(
            found,
            ["the table lacks Q4 data", "the title is wrong", "a plain gap"],
        )

    def test_without_the_text_every_gap_stays(self) -> None:
        found = deliverable_check.gaps_of(
            self.reply({"gap": "the table lacks Q3 data", "missing": "Q3"})
        )
        self.assertEqual(found, ["the table lacks Q3 data"])

    def test_the_rules_say_the_whole_text_and_keep_items(self) -> None:
        self.assertIn("the file's text is the whole file", deliverable_check.SYSTEM)
        self.assertIn('"missing"', deliverable_check.SYSTEM)
        self.assertIn("never drops an item", deliverable_check.REVISION)
        self.assertIn("never drop an item", current_file.ROOM)


if __name__ == "__main__":
    unittest.main()


class AskedTitleTests(unittest.TestCase):
    """Staging -185: "change the title to 'X'" on a Word report renamed the
    first section heading instead; the check's gap was dropped because the
    words asked were in the file, and the old title stayed. Fictional titles."""

    def content(self, title: str) -> object:
        from types import SimpleNamespace

        return SimpleNamespace(title=title)

    def test_the_title_named_in_the_request_is_read_in_five_languages(self) -> None:
        for request, asked in (
            (
                "change the title to 'Lanterns of Brackwater' pls",
                "Lanterns of Brackwater",
            ),
            ("one page, title it “North Pier Notes”", "North Pier Notes"),
            ("mets le titre « Bilan de l'atelier » en haut", "Bilan de l'atelier"),
            ("Ändere den Titel in „Ein Jahr am Kanal“", "Ein Jahr am Kanal"),
            ("cambia el título a «Mareas de Otoño»", "Mareas de Otoño"),
            ('intitolalo "Rotte del Nord"', "Rotte del Nord"),
            ("title it 'Maya's Orchard'", "Maya's Orchard"),
        ):
            with self.subTest(request=request):
                self.assertEqual(deliverable_check.asked_title(request), asked)

    def test_a_part_title_or_subtitle_is_not_the_documents(self) -> None:
        for request in (
            "change the title of slide 3 to 'Costs'",
            "le titre de la section 2 devient « Coûts »",
            "add a subtitle 'Draft only'",
            "ajoute un sous-titre « Brouillon »",
            "add a table with these numbers: Q1 40, Q2 44",
        ):
            with self.subTest(request=request):
                self.assertEqual(deliverable_check.asked_title(request), "")

    def test_a_title_left_unchanged_is_a_certain_gap(self) -> None:
        request = "please change the title to 'The Lantern Trial'"
        gaps = deliverable_check.asked_title_gaps(
            request, self.content("Ten Weeks at Brackwater")
        )
        self.assertEqual(len(gaps), 1)
        self.assertIn("The Lantern Trial", gaps[0])
        self.assertIn("Ten Weeks at Brackwater", gaps[0])
        self.assertEqual(
            deliverable_check.asked_title_gaps(
                request, self.content("The lantern trial")
            ),
            [],
        )
        self.assertEqual(deliverable_check.asked_title_gaps(request, None), [])

    def test_a_replacement_not_made_is_kept_though_its_words_are_shown(self) -> None:
        text = "Ten Weeks at Brackwater\nThe Lantern Trial\nWe tried shorter weeks.\n"
        found = deliverable_check.gaps_of(
            json.dumps(
                {
                    "gaps": [
                        {
                            "gap": "the title still reads 'Ten Weeks at "
                            "Brackwater'; it should be 'The Lantern Trial'",
                            "missing": "The Lantern Trial",
                        }
                    ],
                    "variants": [],
                }
            ),
            text,
        )
        self.assertEqual(len(found or []), 1)


class AskedTitleReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_refuses_a_file_whose_title_was_not_changed(self) -> None:
        from types import SimpleNamespace

        from packages.doc_render.schema import Content

        tools = SimpleNamespace(request_text="change the title to 'Quiet Harbour'")
        content = Content.model_validate(
            {
                "title": "Busy Harbour",
                "sections": [
                    {"heading": "Quiet Harbour", "paragraphs": ["Boats rest."]}
                ],
            }
        )
        outcome = await deliverable_check.review(tools, ".md", content, b"", b"", [])
        self.assertTrue(any("Quiet Harbour" in gap for gap in outcome.certain))
        self.assertTrue(any("Quiet Harbour" in gap for gap in outcome.gaps))
