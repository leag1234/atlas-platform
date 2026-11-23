"""Residual C3 and C7 defects of staging -184, by class.

- a file made without data states the person's own facts (hours, schedules,
  "no complaints recorded") next to its placeholders (own_facts.py);
- a figure of the notes is attached to another noun of the notes
  (figure_nouns.py);
- a page reference does not hold the item it is given for (page_refs.py).

Self-written fictional texts in English and French; no model is called (the
file check's replies are recorded).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, patch

from packages.doc_render.schema import Content
from services.orchestrator import deliverable_check, document_quotes
from services.orchestrator import figure_nouns, own_facts, page_refs
from services.orchestrator import source_faithfulness as faith
from services.orchestrator.presentation_delivery import build_project_document
from services.orchestrator.project_document_input import normalize

REQUEST = "could you write a short word report about our team trying a 4 day work week?"
REPORT = """# Four Days at Tallow Desk

## Overview

Our team tried a four-day work week over [trial period to add], reducing hours from 40 to 32 per week.

## Findings

- Output: [result to add]
- No client complaints or missed deadlines were recorded

## How we worked

- All meetings moved into 3-day windows
"""
FR_REQUEST = "fais-moi un petit rapport word sur l'essai de la semaine de 4 jours dans mon équipe"
FR_REPORT = """# Quatre jours chez Brume Conseil

## Contexte

L'équipe a testé la semaine de quatre jours pendant [durée à compléter], avec des journées de 9 heures du lundi au jeudi.

## Bilan

- Productivité : [résultat à compléter]
- Aucun retard de livraison n'a été constaté
"""


def built(markdown: str) -> tuple[Content, Any]:
    value = normalize({"path": "report.docx", "content": markdown})
    content = Content.model_validate(value["content"])
    document = build_project_document(
        content, ".docx", [], max_bytes=16 * 1024 * 1024, timeout=30, pictures={}
    )
    return content, document


def tools_for(request: str) -> SimpleNamespace:
    model = SimpleNamespace(
        profile="atlas-qwen", strict=False, spent=Decimal(0), allowance=Decimal("0.1")
    )
    return SimpleNamespace(
        check_model=model,
        request_text=request,
        earlier_requests=[],
        deliverable_checks=0,
        deliverable_refused=False,
    )


def line_of(markdown: str, words: str) -> int:
    """The number the question gives the line holding these words."""
    content, _ = built(markdown)
    numbered = own_facts.lines(deliverable_check.drawn_text(content))
    return next(i for i, line in enumerate(numbered, 1) if words in line)


class OwnFactsTests(unittest.IsolatedAsyncioTestCase):
    async def review(
        self,
        markdown: str,
        request: str,
        check: dict[str, Any],
        lines: list[dict[str, Any]] | None,
        document: str = "",
    ) -> tuple[Any, list[dict[str, Any]], SimpleNamespace]:
        content, built_file = built(markdown)
        tools = tools_for(request)
        sources = document_quotes.Sources()
        if document:
            sources.add_document(document, "notes.md")
        sources.add_conversation(request)
        asked: list[dict[str, Any]] = []

        async def complete(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
            first = payload["messages"][0]["content"]
            if isinstance(first, str) and first.startswith(own_facts.QUESTION):
                asked.append(payload)
                return {
                    "text": json.dumps({"lines": lines or []}),
                    "cost_eur": "0.0005",
                }
            if payload["messages"][0]["role"] == "system":
                return {"text": json.dumps(check), "cost_eur": "0.001"}
            return {
                "text": json.dumps({"links": [], "unsupported": []}),
                "cost_eur": "0",
            }

        token = document_quotes.current.set(sources)
        try:
            with patch(
                "services.orchestrator.work_pictures.plan_profiles",
                return_value=[("atlas-fast", 20.0)],
            ):
                outcome = await deliverable_check.review(
                    tools,
                    ".docx",
                    content,
                    built_file.content,
                    built_file.preview,
                    [],
                    complete=complete,
                )
        finally:
            document_quotes.current.reset(token)
        return outcome, asked, tools

    async def test_the_person_own_facts_nobody_gave_are_refused_once(self) -> None:
        hours = line_of(REPORT, "40 to 32")
        listed = [
            {"line": hours, "words": "reducing hours from 40 to 32 per week"},
            {
                "line": line_of(REPORT, "No client"),
                "words": "No client complaints or missed deadlines were recorded",
            },
            {
                "line": line_of(REPORT, "3-day"),
                "words": "All meetings moved into 3-day windows",
            },
            # Given by the person, reworded: not kept.
            {"line": hours, "words": "Our team tried a four-day work week"},
            # A placeholder alone, and words the line does not hold.
            {"line": hours, "words": "[trial period to add]"},
            {"line": hours, "words": "Output doubled"},
        ]
        outcome, asked, tools = await self.review(REPORT, REQUEST, CLEAN, listed)
        self.assertEqual(len(asked), 1)
        self.assertIn(REQUEST, asked[0]["messages"][0]["content"])
        stated = [gap for gap in outcome.unsupported if "own case" in gap]
        self.assertEqual(len(stated), 1)
        for words in ("40 to 32", "No client complaints", "3-day windows"):
            self.assertIn(words, stated[0])
        for words in ("four-day work week", "[trial period to add]", "doubled"):
            self.assertNotIn(words, stated[0])
        self.assertEqual(tools.check_model.spent, Decimal("0.0015"))
        with self.assertRaises(ValueError) as refused:
            deliverable_check.decide(tools, outcome)
        self.assertIn("bracketed placeholder", str(refused.exception))

    async def test_a_french_report_is_asked_the_same_way(self) -> None:
        words = "Aucun retard de livraison n'a été constaté"
        listed = [{"line": line_of(FR_REPORT, "Aucun retard"), "words": words}]
        outcome, _, _ = await self.review(FR_REPORT, FR_REQUEST, CLEAN, listed)
        self.assertTrue(any(words in gap for gap in outcome.unsupported))

    async def test_a_placeholder_is_never_a_gap_without_data(self) -> None:
        called = {
            "gaps": [
                {
                    "gap": "The report has placeholder text instead of results",
                    "missing": "",
                }
            ]
        }
        outcome, _, _ = await self.review(REPORT, REQUEST, called, [])
        self.assertEqual(outcome.gaps, [])

    async def test_a_file_from_a_given_document_is_not_asked(self) -> None:
        notes = "Tallow Desk trial: hours went from 40 to 32 per week. " * 20
        _, asked, _ = await self.review(REPORT, REQUEST, CLEAN, [], notes)
        self.assertEqual(asked, [])

    def test_a_statement_mostly_in_the_messages_is_the_person_own(self) -> None:
        asked = [REQUEST]
        self.assertTrue(own_facts.given("our team tried a four-day work week", asked))
        self.assertFalse(own_facts.given("reducing hours from 40 to 32", asked))
        self.assertFalse(own_facts.given("no client complaints were recorded", asked))


CLEAN: dict[str, Any] = {"gaps": [], "variants": []}


NOTES = (
    "Projet Martinet : application de covoiturage pour les salariés d'un "
    "campus de 4 200 personnes. Pilote sur 150 salariés en juin. "
    "Budget validé : 9 800 euros. Équipe : 4 personnes."
)


class FigureNounTests(unittest.TestCase):
    def test_a_figure_moved_to_another_noun_of_the_notes_is_named(self) -> None:
        found = figure_nouns.mismatches(
            ["Cible : campus de 4 200 salariés", "Pilote sur 150 salariés"], [NOTES]
        )
        self.assertEqual(found, ["“4 200 salariés” (the source has “4 200 personnes”)"])

    def test_same_noun_units_currencies_and_translations_pass(self) -> None:
        self.assertEqual(
            figure_nouns.mismatches(
                [
                    "un campus de 4 200 personnes",
                    "9 800 euros",
                    "4 200 people on the campus",
                    "Équipe : 4 personnes",
                    "juin 2026",
                ],
                [NOTES],
            ),
            [],
        )

    def test_english_notes_too(self) -> None:
        notes = "Project Swift: a car-sharing app for the employees of a site of 1,800 people."
        self.assertEqual(
            figure_nouns.mismatches(["A site of 1,800 employees"], [notes]),
            ["“1,800 employees” (the source has “1,800 people”)"],
        )

    def test_the_file_check_refuses_it_once(self) -> None:
        tools = tools_for("Prépare une présentation à partir des notes : " + NOTES)
        gaps = deliverable_check.unit_gaps(
            tools, None, "Cible : campus de 4 200 salariés".encode()
        )
        self.assertEqual(len(gaps), 1)
        self.assertIn("another noun", gaps[0])


PAGED = (
    "[Page 1]\nRapport du conseil de Valdor. Sommaire.\n"
    "[Page 2]\nLe programme AquaVert finance les bassins de rétention.\n"
    "[Page 3]\nLe fonds régional pour la mobilité est doté de 40 millions.\n"
    "[Page 4]\nLe fonds régional sera revu en 2027. Annexes.\n"
)


class PageReferenceTests(unittest.TestCase):
    def test_a_page_without_the_named_item_gets_its_page(self) -> None:
        answer = "Financement : le fonds régional et le programme AquaVert (p. 3–4)."
        out = page_refs.check(answer, [PAGED], streamed=False, language="fr")
        self.assertEqual(
            out.text,
            "Financement : le fonds régional et le programme AquaVert (p. 2–4).",
        )

    def test_a_streamed_answer_gets_a_note(self) -> None:
        answer = "Funding: the regional fund and the AquaVert programme (pages 3 to 4)."
        out = page_refs.check(answer, [PAGED], streamed=True, language="en")
        self.assertEqual(out.text, answer)
        self.assertEqual(out.note, "*Pages checked in the document: AquaVert p. 2.*")

    def test_right_pages_unknown_items_and_printed_numbers_change_nothing(self) -> None:
        for answer in (
            "Le programme AquaVert (p. 2).",
            "Le programme NovaPlan (p. 3).",  # not in the document
            "Le programme AquaVert (p. 40).",  # beyond the last page
            # No named item.
            "Le fonds régional (p. 1).",
        ):
            with self.subTest(answer=answer):
                out = page_refs.check(answer, [PAGED], streamed=False, language="fr")
                self.assertEqual((out.text, out.note), (answer, ""))

    def test_the_review_corrects_a_held_answer(self) -> None:
        sources = document_quotes.Sources()
        sources.add_document(PAGED * 3, "valdor.pdf")
        sources.add_conversation("Résume ce rapport.")
        source = faith.provided(sources)
        assert source is not None
        answer = "Résumé du rapport du conseil. Le programme AquaVert finance les bassins (p. 3)."
        text, addition, report = asyncio.run(
            faith.review(
                answer,
                source,
                request="Résume ce rapport.",
                profile="atlas-qwen",
                language="fr",
                streamed=False,
                complete=AsyncMock(side_effect=TimeoutError()),
            )
        )
        self.assertIn("(p. 2–3)", text)
        self.assertEqual(report.pages, 1)


if __name__ == "__main__":
    unittest.main()
