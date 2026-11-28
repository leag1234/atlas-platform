"""Details of answers and files written from a turn's evidence (fictional content).

Defect class C3, invented facts, on documents and deliverables: every date,
figure with its unit, version and name of an answer that restates a provided
document, and of a file produced in the turn, is matched by code against the
turn's evidence (documents, pages read, tool results) with the matcher of
sourced answers (unsourced_details). A date is matched by value: another day
or month is another date, and a date the answer gives for an event the source
dates (a receipt, a publication) must be the one the source gives for it.
"""

import asyncio
import io
import json
import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from services.orchestrator import chat_pipeline, deliverable_check, document_quotes
from services.orchestrator import unsourced_details as details
from services.orchestrator.request_router import Decision
from services.orchestrator.unsourced_details import Evidence

# One cover note per language: the note is dated the 30th and records the
# receipt of the file on the 29th.
NOTES = {
    "fr": (
        "NOTE DE TRANSMISSION\nVasselin, le 30 janvier 2026\n"
        "Date de réception : 29 janvier 2026\nOrigine : coopérative Brelune\n"
        "Le dossier Korvel compte 1 487 pages et prévoit 12,4 millions d'euros "
        "de travaux. Le taux de réemploi atteint 38 %. Le logiciel Tarnweld "
        "4.2.1 tient le registre."
    ),
    "en": (
        "TRANSMISSION NOTE\nVasselin, 30 January 2026\n"
        "Date of receipt: 29 January 2026\nOrigin: Brelune cooperative\n"
        "The Korvel file runs to 1,487 pages and plans 12.4 million euros of "
        "works. The reuse rate reaches 38%. The Tarnweld 4.2.1 software keeps "
        "the register."
    ),
    "de": (
        "ÜBERMITTLUNGSVERMERK\nVasselin, den 30. Januar 2026\n"
        "Eingangsdatum: 29. Januar 2026\nHerkunft: Genossenschaft Brelune\n"
        "Die Akte Korvel umfasst 1.487 Seiten und sieht Arbeiten für 12,4 Mio. "
        "Euro vor. Die Wiederverwendungsquote liegt bei 38 %. Die Software "
        "Tarnweld 4.2.1 führt das Register."
    ),
}
# The receipt given as the 30th: the date of the note, not of the receipt.
WRONG_RECEIPT = {
    "fr": "Le dossier Korvel a été transmis à Vasselin le 30 janvier 2026.",
    "en": "The Korvel file was received in Vasselin on 30 January 2026.",
    "de": "Die Akte Korvel ist am 30. Januar 2026 in Vasselin eingegangen.",
}
# The same, without the year.
WRONG_DAY = {
    "fr": "Brelune a reçu le dossier le 30 janvier.",
    "en": "Brelune received the file on January 30.",
    "de": "Brelune hat die Akte am 30. Januar erhalten.",
}
# Every detail from the note, some in another written form or rounded.
FAITHFUL = {
    "fr": (
        "Le dossier Korvel de la coopérative Brelune a été reçu le 29/01/2026 ; "
        "la note est datée du 30 janvier 2026. Il compte 1 487 pages, prévoit "
        "environ 12 millions d'euros de travaux, avec un taux de réemploi de "
        "38 %, et le registre tourne sur Tarnweld 4.2.1."
    ),
    "en": (
        "Brelune's Korvel file was received on January 29, 2026, and the note "
        "is dated 2026-01-30. It runs to 1,487 pages and plans about 12 million "
        "euros of works, with a 38 % reuse rate; Tarnweld 4.2.1 keeps the "
        "register."
    ),
    "de": (
        "Die Akte Korvel der Genossenschaft Brelune ging am 29.01.2026 ein; der "
        "Vermerk trägt das Datum 30. Januar 2026. Sie umfasst 1.487 Seiten, "
        "sieht rund 12 Mio. Euro für Arbeiten vor, bei einer Quote von 38 %, "
        "und Tarnweld 4.2.1 führt das Register."
    ),
}
# A report adding an outside statistic none of the turn's texts holds.
INVENTED = {
    "fr": (
        "Rapport Korvel\nLe dossier compte 1 487 pages. Selon une étude de "
        "l'institut Quillon, 42 % des coopératives ont adopté ce modèle en 2025."
    ),
    "en": (
        "Korvel report\nThe file runs to 1,487 pages. A Quillon Institute study "
        "found that 42% of cooperatives adopted this model in 2025."
    ),
    "de": (
        "Bericht Korvel\nDie Akte umfasst 1.487 Seiten. Laut einer Studie des "
        "Quillon-Instituts haben 42 % der Genossenschaften dieses Modell 2025 "
        "übernommen."
    ),
}


def missing(answer: str, source: str) -> list[str]:
    found = details.unsupported(answer, Evidence.of([source]), Evidence.of([]))
    return [detail.text for detail in found]


class DatesTest(unittest.TestCase):
    def test_the_receipt_given_as_the_next_day_is_caught(self) -> None:
        for language, note in NOTES.items():
            with self.subTest(language=language):
                self.assertTrue(
                    any("30" in text for text in missing(WRONG_RECEIPT[language], note))
                )
                self.assertTrue(
                    any("30" in text for text in missing(WRONG_DAY[language], note))
                )

    def test_a_document_saying_received_on_the_29th(self) -> None:
        source = "Le colis a été reçu le 29 janvier par l'atelier Brelune."
        self.assertEqual(
            missing("L'atelier a reçu le colis le 30 janvier.", source), ["30 janvier"]
        )
        self.assertEqual(
            missing("L'atelier a reçu le colis le 29 janvier.", source), []
        )

    def test_details_all_from_the_document_pass(self) -> None:
        for language, note in NOTES.items():
            with self.subTest(language=language):
                self.assertEqual(missing(FAITHFUL[language], note), [])

    def test_another_day_or_month_is_no_near_match(self) -> None:
        source = (
            "16.01.2026 – 08:00 Statistikamt Velmarre: Inflationsrate 2025 bei 2,2 %"
        )
        self.assertEqual(missing("Communiqué du 16 janvier 2026.", source), [])
        self.assertEqual(missing("Press release of January 16, 2026.", source), [])
        self.assertEqual(
            missing("Communiqué du 15 janvier 2026.", source), ["15 janvier 2026"]
        )
        self.assertEqual(
            missing("Pressemitteilung vom 16. Februar 2026.", source),
            ["16. Februar 2026"],
        )

    def test_a_date_without_an_event_may_be_any_date_of_the_source(self) -> None:
        note = NOTES["en"]
        self.assertEqual(missing("The note is dated 30 January 2026.", note), [])

    def test_a_range_and_a_year_written_elsewhere_hold_their_days(self) -> None:
        source = "Les assises Brelune se tiennent les 29 et 30 janvier 2026."
        self.assertEqual(
            missing("Les assises s'ouvrent le 29 janvier 2026.", source), []
        )

    def test_a_modal_verb_is_no_month(self) -> None:
        self.assertEqual(details.date_spans("Two of them may 5 times a day"), [])


class FiguresTest(unittest.TestCase):
    def test_a_percentage_needs_a_percentage(self) -> None:
        source = "Le chantier Korvel dure 40 jours."
        self.assertEqual(
            missing("Le chantier a réduit les coûts de 40 %.", source), ["40 %"]
        )
        self.assertEqual(missing("Taux de 38 percent.", "A rate of 38%."), [])

    def test_an_invented_outside_statistic_is_caught(self) -> None:
        for language, report in INVENTED.items():
            with self.subTest(language=language):
                found = missing(report, NOTES[language])
                self.assertTrue(any("42" in text for text in found), found)


def docx(text: str) -> bytes:
    from docx import Document

    document = Document()
    title, *lines = text.splitlines()
    document.add_heading(title, level=1)
    for line in lines:
        document.add_paragraph(line)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class DeliverableTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {"ATLAS_UNSOURCED_DETAILS": "revise_mark"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def gaps(self, raw: bytes, suffix: str, evidence: list[str]) -> list[str]:
        sources = document_quotes.Sources()
        for text in evidence:
            sources.add_evidence(text)
        tools = SimpleNamespace(
            request_text="Fais un rapport Word sur le dossier Korvel",
            earlier_requests=[],
        )
        token = document_quotes.current.set(sources)
        try:
            return deliverable_check.detail_gaps(tools, suffix, raw)[0]
        finally:
            document_quotes.current.reset(token)

    def test_a_report_with_an_invented_statistic_is_a_gap(self) -> None:
        for language, report in INVENTED.items():
            with self.subTest(language=language):
                gaps = self.gaps(docx(report), ".docx", [NOTES[language]])
                self.assertEqual(len(gaps), 1)
                self.assertIn("42", gaps[0])
                self.assertNotIn("1 487", gaps[0])

    def test_a_report_from_its_sources_passes(self) -> None:
        for language, note in NOTES.items():
            with self.subTest(language=language):
                self.assertEqual(
                    self.gaps(docx("Korvel\n" + FAITHFUL[language]), ".docx", [note]),
                    [],
                )

    def test_a_detail_marked_not_verified_passes(self) -> None:
        marked = INVENTED["fr"].replace("en 2025.", "en 2025 (non vérifié).")
        self.assertEqual(self.gaps(marked.encode(), ".md", [NOTES["fr"]]), [])

    def test_without_any_source_only_outside_figures_are_checked(self) -> None:
        story = "Le renard Brelune traversa la forêt de Korvel avec ses 3 amis."
        self.assertEqual(self.gaps(story.encode(), ".md", []), [])
        gaps = self.gaps(INVENTED["en"].encode(), ".md", [])
        self.assertEqual(len(gaps), 1)
        self.assertIn("42%", gaps[0])

    def test_the_gap_is_refused_once_then_said(self) -> None:
        tools = SimpleNamespace(
            request_text="Write a Word report on the Korvel file",
            earlier_requests=[],
            deliverable_refused=False,
        )
        sources = document_quotes.Sources()
        sources.add_evidence(NOTES["en"])
        token = document_quotes.current.set(sources)
        try:
            outcome = asyncio.run(
                deliverable_check.review(
                    tools, ".docx", None, docx(INVENTED["en"]), b"", []
                )
            )
        finally:
            document_quotes.current.reset(token)
        self.assertTrue(outcome.certain)
        with self.assertRaisesRegex(ValueError, "deliverable_check: .*42%"):
            deliverable_check.decide(tools, outcome)
        self.assertIn("42%", deliverable_check.decide(tools, outcome))

    def test_a_source_file_of_the_publication_is_evidence(self) -> None:
        tools = SimpleNamespace(
            request_text="Make a deck from notes.txt", earlier_requests=[]
        )
        token = document_quotes.current.set(document_quotes.Sources())
        try:
            gaps, _ = deliverable_check.detail_gaps(
                tools, ".md", FAITHFUL["en"].encode(), [NOTES["en"]]
            )
        finally:
            document_quotes.current.reset(token)
        self.assertEqual(gaps, [])

    def test_off_mode_checks_nothing(self) -> None:
        with patch.dict(os.environ, {"ATLAS_UNSOURCED_DETAILS": "off"}):
            self.assertEqual(
                self.gaps(docx(INVENTED["fr"]), ".docx", [NOTES["fr"]]), []
            )


# A report built from a project file: figures the person gave in the
# request, totals and shares computed from the file's numbers, the file
# name's date.
PROJECT_NOTE = (
    "Projet Korvel : budget validé de 12 400 euros, équipe de 4 personnes, "
    "pilote sur 200 salariés en mai 2026."
)
PROJECT_REPORT = (
    "Rapport Korvel 2026-11-21\n"
    "Le budget de 12 400 euros représente 3 100 euros par personne. Le pilote "
    "concerne 200 salariés, et la réserve demandée de 1 500 euros porte le total "
    "à 13 900 euros."
)


class SourcedComputedTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {"ATLAS_UNSOURCED_DETAILS": "revise_mark"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_values_computations_and_file_name_are_sourced(self) -> None:
        tools = SimpleNamespace(
            request_text="À partir de notes/projet.txt, rédige rapport-korvel-2026-11-21.md "
            "en ajoutant une réserve de 1 500 euros",
            earlier_requests=[],
            deliverable_refused=False,
        )
        token = document_quotes.current.set(document_quotes.Sources())
        try:
            gaps, kinds = deliverable_check.detail_gaps(
                tools,
                ".md",
                PROJECT_REPORT.encode(),
                [PROJECT_NOTE],
                "rapport-korvel-2026-11-21.md",
            )
        finally:
            document_quotes.current.reset(token)
        self.assertEqual((gaps, kinds), ([], {}))


class RefusalBudgetTest(unittest.TestCase):
    """A turn refuses its file at most once, whichever check finds the gaps."""

    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {"ATLAS_UNSOURCED_DETAILS": "revise_mark"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def ledger_turn(self) -> Any:
        from services.orchestrator import expectations

        ledger = expectations.Ledger("document")
        ledger.requirements.append(
            expectations.Requirement("R1", "A table of the schedule", 1)
        )
        return expectations.TurnLedger("k", ledger, "atlas-test", "fr")

    def test_the_ledger_does_not_refuse_after_the_details_check(self) -> None:
        from services.orchestrator import expectations

        tools = SimpleNamespace(
            request_text="Write a Word report on the Korvel file",
            earlier_requests=[],
            deliverable_refused=False,
            expectations=self.ledger_turn(),
        )
        unmet = [expectations.Result("R1", False, "no table in the file")]
        sources = document_quotes.Sources()
        sources.add_evidence(NOTES["en"])
        token = document_quotes.current.set(sources)
        try:
            with self.assertLogs("services.orchestrator.deliverable_check") as logs:
                first = asyncio.run(
                    deliverable_check.review(
                        tools, ".docx", None, docx(INVENTED["en"]), b"", []
                    )
                )
                with self.assertRaises(ValueError):
                    deliverable_check.decide(tools, first)
                # The repair still lacks the table and keeps the figure: the
                # ledger does not refuse again, the file is published with both.
                expectations.document_refusal(tools, unmet)
                second = asyncio.run(
                    deliverable_check.review(
                        tools, ".docx", None, docx(INVENTED["en"]), b"", []
                    )
                )
                note = deliverable_check.decide(tools, second)
        finally:
            document_quotes.current.reset(token)
        self.assertIn("42%", note)
        self.assertNotIn("for each one", note)
        decisions = [
            json.loads(line.split(":", 2)[2])
            for line in logs.output
            if "deliverable_decision" in line
        ]
        self.assertEqual([d["refused"] for d in decisions], [True, False])
        self.assertEqual(decisions[0]["details"].get("number"), 1)

    def test_a_second_ledger_refusal_never_comes(self) -> None:
        from services.orchestrator import expectations

        tools = SimpleNamespace(
            deliverable_refused=False, expectations=self.ledger_turn()
        )
        unmet = [expectations.Result("R1", False, "no table in the file")]
        with self.assertRaises(ValueError):
            expectations.document_refusal(tools, unmet)
        expectations.document_refusal(tools, unmet)  # published, not refused
        self.assertEqual(tools.expectations.repairs, 1)


def turn(answer: str, document: str) -> tuple[Any, Any, document_quotes.Sources]:
    request = SimpleNamespace(
        messages=[SimpleNamespace(role="user", text="Résume ce document.")],
        model="atlas-test",
        user_text="Résume ce document.",
    )
    item = SimpleNamespace(reponse=answer, chunks_recuperes=[], steps=[], cout_eur=0.0)
    sources = document_quotes.Sources()
    sources.add_document(document, "note.pdf")
    return request, item, sources


class DocumentTurnTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.dict(os.environ, {"ATLAS_UNSOURCED_DETAILS": "revise_mark"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_writing_decision_skips_the_streamed_note(self) -> None:
        request, item, sources = turn(WRONG_RECEIPT["fr"], NOTES["fr"])
        request.__dict__["model_route"] = Decision(kind="answer", states_facts=False)
        with (
            patch.dict(os.environ, {"ATLAS_MODEL_ROUTER": "1"}),
            patch.object(
                details,
                "streamed_check",
                MagicMock(return_value=("", details.Report())),
            ) as check,
        ):
            note = asyncio.run(
                chat_pipeline._document_details(
                    request, item, sources, [None], True, "fr"
                )
            )
        self.assertEqual(note, "")
        check.assert_not_called()

    def test_a_fact_decision_keeps_the_streamed_note(self) -> None:
        request, item, sources = turn(WRONG_RECEIPT["fr"], NOTES["fr"])
        request.__dict__["model_route"] = Decision(kind="answer", states_facts=True)
        with (
            patch.dict(os.environ, {"ATLAS_MODEL_ROUTER": "1"}),
            patch.object(
                details,
                "streamed_check",
                MagicMock(return_value=("", details.Report())),
            ) as check,
        ):
            note = asyncio.run(
                chat_pipeline._document_details(
                    request, item, sources, [None], True, "fr"
                )
            )
        self.assertEqual(note, "")
        check.assert_called_once()

    def test_a_writing_decision_skips_the_held_review(self) -> None:
        request, item, sources = turn(WRONG_RECEIPT["fr"], NOTES["fr"])
        request.__dict__["model_route"] = Decision(kind="answer", states_facts=False)
        with (
            patch.dict(os.environ, {"ATLAS_MODEL_ROUTER": "1"}),
            patch.object(
                details,
                "review",
                AsyncMock(return_value=("", details.Report())),
            ) as review,
        ):
            note = asyncio.run(
                chat_pipeline._document_details(
                    request, item, sources, [None], False, "fr"
                )
            )
        self.assertEqual(note, "")
        review.assert_not_awaited()

    def test_a_streamed_answer_gets_a_closing_note(self) -> None:
        request, item, sources = turn(WRONG_RECEIPT["fr"], NOTES["fr"])
        note = asyncio.run(
            chat_pipeline._document_details(request, item, sources, [None], True, "fr")
        )
        self.assertIn("30 janvier 2026", note)
        self.assertEqual(item.steps[-1]["phase"], "document_details")

    def test_a_held_answer_is_revised_once(self) -> None:
        request, item, sources = turn(WRONG_RECEIPT["fr"], NOTES["fr"])
        fixed = "Le dossier Korvel a été reçu à Vasselin le 29 janvier 2026."
        mock = AsyncMock(
            return_value={"text": '{"1": "' + fixed + '"}', "cost_eur": "0.0004"}
        )
        with patch.object(details, "_complete", mock):
            note = asyncio.run(
                chat_pipeline._document_details(
                    request, item, sources, [None], False, "fr"
                )
            )
        self.assertEqual(note, "")
        self.assertEqual(mock.await_count, 1)
        self.assertNotIn("30 janvier", item.reponse)

    def test_a_faithful_answer_costs_no_call(self) -> None:
        request, item, sources = turn(FAITHFUL["fr"], NOTES["fr"])
        mock = AsyncMock()
        with patch.object(details, "_complete", mock):
            asyncio.run(
                chat_pipeline._document_details(
                    request, item, sources, [None], False, "fr"
                )
            )
        mock.assert_not_awaited()
        self.assertEqual(item.reponse, FAITHFUL["fr"])

    def test_names_translated_from_the_document_are_not_flagged(self) -> None:
        answer = (
            "La note de la Coopérative Brelune indique que le dossier Korvel compte "
            "1 487 pages et que le logiciel Tarnweld 4.2.1 tient le registre."
        )
        request, item, sources = turn(answer, NOTES["en"])
        note = asyncio.run(
            chat_pipeline._document_details(request, item, sources, [None], True, "fr")
        )
        self.assertEqual(note, "")


if __name__ == "__main__":
    unittest.main()
