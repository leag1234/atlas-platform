"""Scoped claims of an answer about a provided document, and reports without data.

Self-written fictional texts (places, firms and figures are invented); no model
is called: the checker's and the file check's replies are recorded.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock

from packages.doc_render.schema import Content
from services.orchestrator import deliverable_check, document_quotes
from services.orchestrator import scoped_claims
from services.orchestrator import source_faithfulness as faith
from services.orchestrator.presentation_delivery import build_project_document
from services.orchestrator.project_document_input import normalize
from services.orchestrator.project_document_tools import DESCRIPTIONS

# A passage that is easy to overgeneralise: the span belongs to the gap with
# the other districts; Orrin Bay leads in one field, Marlow Heath only caught up.
EN_PASSAGE = (
    "For more than twelve years, the Kestrel Valley has grown more slowly than "
    "the other northern districts, because of a persistent gap in rail "
    "investment. The valley fell behind Orrin Bay in freight logistics, while "
    "Marlow Heath has caught up in many sectors and may lead in some new ones. "
    "The root cause is the low share of public money spent on maintenance."
)
EN_CLAIM = (
    "- **Diagnosis:** for more than twelve years, the Kestrel Valley has been "
    "outpaced by Orrin Bay and Marlow Heath because of a persistent gap in rail "
    "investment."
)
EN_FIXED = (
    "- **Diagnosis:** for more than twelve years, the Kestrel Valley has grown "
    "more slowly than the other northern districts because of a persistent gap "
    "in rail investment; it fell behind Orrin Bay in freight logistics, while "
    "Marlow Heath caught up in many sectors."
)
FR_PASSAGE = (
    "Depuis plus de quinze ans, la région de Valmorel croît moins vite que les "
    "autres régions alpines, du fait d'un investissement public plus faible. "
    "Valmorel a pris du retard sur Val-Corbin dans le tourisme d'hiver, tandis "
    "que la Haute-Lisère a rattrapé son écart dans de nombreux secteurs. "
    "La cause profonde tient au manque de formation des jeunes actifs."
)
FR_CLAIM = (
    "Depuis plus de quinze ans, Valmorel est distancée par Val-Corbin et la "
    "Haute-Lisère, faute d'investissement public."
)
FR_FIXED = (
    "Depuis plus de quinze ans, Valmorel croît moins vite que les autres régions "
    "alpines, faute d'investissement public ; elle a pris du retard sur "
    "Val-Corbin dans le tourisme d'hiver, tandis que la Haute-Lisère a rattrapé "
    "son écart dans de nombreux secteurs."
)
FILLER = (
    "The district library opened a reading room for school groups on Tuesdays. "
    "Volunteers repaint the benches of the market square every spring. "
    "A choir rehearses in the old granary and sings at the harvest fair. "
)


def long_document(passage: str) -> str:
    """A document over the general judge's SOURCE_LIMIT, the passage inside."""
    half = FILLER * (faith.SOURCE_LIMIT // len(FILLER) // 2 + 1)
    return half + passage + " " + half


def source_of(text: str, request: str) -> faith.Source:
    sources = document_quotes.Sources()
    sources.add_document(text, "report.pdf")
    sources.add_conversation(request)
    found = faith.provided(sources)
    assert found is not None
    return found


class Calls:
    """Recorded replies in order; every payload is kept."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        self.payloads.append(payload)
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return dict(reply)


def verdicts(*items: dict[str, Any]) -> dict[str, str]:
    return {"text": json.dumps({"claims": list(items)}), "cost_eur": "0.001"}


def review(
    answer: str, source: faith.Source, calls: Calls, **options: Any
) -> tuple[str, str, faith.Report]:
    arguments: dict[str, Any] = {
        "request": "Summarise this report in five points.",
        "profile": "atlas-qwen",
        "language": "en",
        "streamed": False,
        "complete": calls,
    }
    arguments.update(options)
    return asyncio.run(faith.review(answer, source, **arguments))


class DetectionTests(unittest.TestCase):
    def test_a_time_span_with_a_comparison_is_a_scoped_claim(self) -> None:
        for sentence in (
            EN_CLAIM,
            FR_CLAIM,
            "Seit über zehn Jahren liegt Orrin Bay hinter Marlow Heath.",
            "Desde hace veinte años, Valmorel va por detrás de Val-Corbin.",
            "Since 2014 the valley has lagged behind the coast.",
            "Over two decades the district grew faster than its neighbours.",
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(scoped_claims.scoped(sentence))

    def test_a_span_alone_or_a_comparison_alone_is_not(self) -> None:
        for sentence in (
            "The trial lasted twelve weeks and ended in May.",
            "Work stopped for six months after the flood.",
            "Orrin Bay is ahead of Marlow Heath in freight.",
            "Valmorel compte plus d'habitants que Val-Corbin.",
        ):
            with self.subTest(sentence=sentence):
                self.assertFalse(scoped_claims.scoped(sentence))

    def test_the_passage_with_the_same_span_and_words_is_retrieved(self) -> None:
        found = scoped_claims.claims(
            "A short summary. " + EN_CLAIM, long_document(EN_PASSAGE)
        )
        self.assertEqual(len(found), 1)
        self.assertTrue(
            any("Marlow Heath has caught up" in p for p in found[0].passages)
        )
        self.assertLessEqual(len(found[0].passages), scoped_claims.PASSAGES_PER_CLAIM)


class LongDocumentTests(unittest.TestCase):
    """The general judge skips these sources; the scoped check reads them."""

    def test_an_overgeneralised_claim_is_corrected_before_sending(self) -> None:
        answer = "Five points.\n\n" + EN_CLAIM + "\n- The report has forty pages."
        calls = Calls(
            verdicts(
                {
                    "id": 1,
                    "fault": "compared_with",
                    "verdict": "corrected",
                    "text": EN_FIXED,
                }
            )
        )
        text, addition, report = review(
            answer, source_of(long_document(EN_PASSAGE), "Summarise it."), calls
        )
        self.assertIn(EN_FIXED, text)
        self.assertNotIn("outpaced by Orrin Bay and Marlow Heath", text)
        self.assertIn("- The report has forty pages.", text)
        self.assertEqual(addition, "")
        self.assertEqual(report.outcome, "skipped:source_too_long")
        self.assertEqual(report.scoped["corrected"], 1)
        self.assertEqual(report.cost_eur, Decimal("0.001"))
        # One small call: the claim and its passage, never the whole document.
        self.assertEqual(len(calls.payloads), 1)
        content = calls.payloads[0]["messages"][0]["content"]
        self.assertIn("Marlow Heath has caught up in many sectors", content)
        self.assertLess(len(content), 6_000)

    def test_a_streamed_french_answer_gets_the_correction_as_a_note(self) -> None:
        answer = "Résumé en cinq points.\n\n" + FR_CLAIM
        calls = Calls(
            verdicts(
                {
                    "id": 1,
                    "fault": "compared_with",
                    "verdict": "corrected",
                    "text": FR_FIXED,
                }
            )
        )
        text, addition, report = review(
            answer,
            source_of(long_document(FR_PASSAGE), "Résume ce rapport."),
            calls,
            language="fr",
            streamed=True,
        )
        self.assertEqual(text, answer)
        self.assertTrue(addition.startswith("*Plus précisément, d'après le document"))
        self.assertIn("la Haute-Lisère a rattrapé son écart", addition)
        self.assertEqual(report.scoped["outcome"], "noted")

    def test_a_supported_claim_changes_nothing(self) -> None:
        answer = "Five points.\n\n" + EN_CLAIM
        calls = Calls(verdicts({"id": 1, "verdict": "supported"}))
        text, addition, _ = review(
            answer, source_of(long_document(EN_PASSAGE), "Summarise it."), calls
        )
        self.assertEqual((text, addition), (answer, ""))

    def test_a_correction_adding_a_number_is_not_kept(self) -> None:
        answer = "Five points.\n\n" + EN_CLAIM
        invented = EN_FIXED.replace("many sectors", "14 sectors")
        calls = Calls(
            verdicts(
                {
                    "id": 1,
                    "fault": "compared_with",
                    "verdict": "corrected",
                    "text": invented,
                }
            )
        )
        text, addition, report = review(
            answer, source_of(long_document(EN_PASSAGE), "Summarise it."), calls
        )
        self.assertEqual((text, addition), (answer, ""))
        self.assertEqual(report.scoped["rejected"], 1)

    def test_a_rewrite_naming_no_fault_is_a_paraphrase_and_not_kept(self) -> None:
        answer = "Five points.\n\n" + EN_CLAIM
        calls = Calls(verdicts({"id": 1, "verdict": "corrected", "text": EN_FIXED}))
        text, addition, report = review(
            answer, source_of(long_document(EN_PASSAGE), "Summarise it."), calls
        )
        self.assertEqual((text, addition), (answer, ""))
        self.assertEqual(report.scoped["rejected"], 1)

    def test_a_failed_check_leaves_the_answer_and_logs_its_code(self) -> None:
        answer = "Five points.\n\n" + EN_CLAIM
        for reply, code in (
            (TimeoutError(), "failed:timeout"),
            ({"text": "no json", "cost_eur": "0"}, "failed:unreadable"),
        ):
            with self.subTest(code=code):
                text, addition, report = review(
                    answer,
                    source_of(long_document(EN_PASSAGE), "Summarise it."),
                    Calls(reply),
                )
                self.assertEqual((text, addition), (answer, ""))
                self.assertEqual(report.scoped["outcome"], code)

    def test_log_mode_checks_without_changing_the_answer(self) -> None:
        answer = "Five points.\n\n" + EN_CLAIM
        calls = Calls(
            verdicts(
                {
                    "id": 1,
                    "fault": "compared_with",
                    "verdict": "corrected",
                    "text": EN_FIXED,
                }
            )
        )
        text, addition, report = review(
            answer,
            source_of(long_document(EN_PASSAGE), "Summarise it."),
            calls,
            setting="log",
        )
        self.assertEqual((text, addition), (answer, ""))
        self.assertEqual(report.scoped["outcome"], "logged")

    def test_an_answer_without_scoped_claims_costs_no_call(self) -> None:
        calls = Calls()
        answer = "The report describes the rail network and its maintenance."
        text, _, report = review(
            answer, source_of(long_document(EN_PASSAGE), "Summarise it."), calls
        )
        self.assertEqual(text, answer)
        self.assertEqual(calls.payloads, [])
        self.assertEqual(report.scoped, {})


class ShortDocumentTests(unittest.TestCase):
    def test_the_judge_reads_the_corrected_answer(self) -> None:
        answer = "Summary.\n\n" + EN_CLAIM
        clean = {
            "text": json.dumps({"links": [], "unsupported": []}),
            "cost_eur": "0.006",
        }
        calls = Calls(
            verdicts(
                {
                    "id": 1,
                    "fault": "compared_with",
                    "verdict": "corrected",
                    "text": EN_FIXED,
                }
            ),
            clean,
        )
        text, addition, report = review(
            answer, source_of(EN_PASSAGE * 3, "Summarise it."), calls
        )
        self.assertIn(EN_FIXED, text)
        self.assertEqual((addition, report.outcome), ("", "clean"))
        judged = calls.payloads[1]["messages"][0]["content"]
        self.assertIn(EN_FIXED, judged)


def report_file(markdown: str) -> tuple[Content, Any]:
    value = normalize({"path": "report.docx", "content": markdown})
    content = Content.model_validate(value["content"])
    document = build_project_document(
        content, ".docx", [], max_bytes=16 * 1024 * 1024, timeout=30, pictures={}
    )
    return content, document


def file_tools(request: str) -> SimpleNamespace:
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


REPORT_REQUEST = (
    "hi, could you put together a short word report about our team trying a "
    "4 day work week? about one page"
)
INVENTED = (
    "# Four Days at Tallow Desk\n\n## Findings\n\n"
    "- Output remained stable across the eight-week trial\n"
    "- Satisfaction scores rose\n\n## Next steps\n\n"
    "A follow-up review is planned three months after the decision."
)
GAP = {
    "gaps": [
        {
            "gap": "invented results of the team written as observed: stable "
            "output, rising satisfaction, an eight-week trial, a follow-up in "
            "three months; each must be a bracketed placeholder such as "
            "[result to add]",
            "missing": "",
        }
    ],
    "variants": [],
}


class ReportWithoutDataTests(unittest.IsolatedAsyncioTestCase):
    async def check(self, sources: document_quotes.Sources) -> tuple[Any, Any, Any]:
        content, document = report_file(INVENTED)
        tools = file_tools(REPORT_REQUEST)
        complete = AsyncMock(
            side_effect=[{"text": json.dumps(GAP), "cost_eur": "0.002"}] * 2
        )
        token = document_quotes.current.set(sources)
        try:
            outcome = await deliverable_check.review(
                tools,
                ".docx",
                content,
                document.content,
                document.preview,
                [],
                complete=complete,
            )
        finally:
            document_quotes.current.reset(token)
        awaited = complete.await_args_list[0]
        user = awaited.args[0]["messages"][1]["content"]
        text = user if isinstance(user, str) else user[0]["text"]
        return outcome, json.loads(text.split("\n(The picture")[0]), tools

    async def test_results_the_person_never_gave_are_refused_once(self) -> None:
        sources = document_quotes.Sources()
        sources.add_conversation(REPORT_REQUEST)
        outcome, sent, tools = await self.check(sources)
        # Code tells the check that the person gave no document.
        self.assertIs(sent["facts"]["person_gave_documents"], False)
        self.assertTrue(any("[result to add]" in gap for gap in outcome.gaps))
        with self.assertRaises(ValueError) as refused:
            deliverable_check.decide(tools, outcome)
        self.assertIn("[result to add]", str(refused.exception))

    async def test_a_provided_document_is_reported_to_the_check(self) -> None:
        sources = document_quotes.Sources()
        sources.add_document("Trial log of Tallow Desk: output 112, 118, 121.", "a.md")
        sources.add_conversation(REPORT_REQUEST)
        _, sent, _ = await self.check(sources)
        self.assertIs(sent["facts"]["person_gave_documents"], True)

    def test_the_rule_is_given_to_the_writer_and_the_check(self) -> None:
        description = DESCRIPTIONS["publish_document"]
        self.assertIn("is never written as observed", description)
        self.assertIn("[result to add]", description)
        self.assertIn("figures the person gave are written as given", description)
        self.assertIn("person_gave_documents", deliverable_check.SYSTEM)
        self.assertIn("[result", deliverable_check.SYSTEM)


if __name__ == "__main__":
    unittest.main()
