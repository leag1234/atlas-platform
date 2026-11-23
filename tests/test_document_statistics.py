"""Counts over attachments and pasted texts are computed by code (C2).

Staging -165: a PDF word count "done directly" by the model gave 44 for 85.
All documents here are fictional.
"""

import asyncio
import base64
from decimal import Decimal
import io
import json
from typing import Any
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from agent_provider import AgentRequest
from packages.attachment_reader import extract_bytes
from services.orchestrator import document_stats
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.document_chat import process_documents
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import Call, GatewayError, Turn
from services.orchestrator.model import Configuration, GatewayCallError, GatewayModel

# Fictional pages: the whole word appears 2, 0, 2 and 1 times (on page 3 once
# capitalised and once split by a line-end hyphen, plus one plural).
PAGES = [
    "La compétitivité du Vallon dépend de la compétitivité des ateliers.",
    "Les ateliers du Vallon publient un rapport annuel.",
    "Compétitivité : un objectif. La compéti-\ntivité durable reste le cap. "
    "Les compétitivités régionales divergent.",
    "Conclusion sur la COMPÉTITIVITÉ et la croissance.",
]


def pdf(pages: list[str]) -> bytes:
    """A PDF whose pages hold the given text (WinAnsi, so accents survive)."""
    writer = PdfWriter()
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
                NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
            }
        )
    )
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        lines = []
        for line in text.split("\n"):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            lines.append(f"({escaped}) Tj T*")
        stream = DecodedStreamObject()
        stream.set_data(
            ("BT /F1 10 Tf 50 740 Td 14 TL " + " ".join(lines) + " ET").encode("cp1252")
        )
        page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def paged_text() -> document_stats.Text:
    text = "\n\n".join(f"[Page {i}]\n{body}" for i, body in enumerate(PAGES, 1))
    return document_stats.Text("rapport-vallon.pdf", text, len(PAGES))


def counts(term: str, texts: list[document_stats.Text] | None = None) -> dict[str, Any]:
    result = document_stats.execute(
        json.dumps({"terms": [term]}), texts or [paged_text()]
    )
    found: dict[str, Any] = result["documents"][0]["counts"][0]
    return found


class CountingTests(unittest.TestCase):
    def test_accented_term_case_variants_and_split_word(self) -> None:
        found = counts("compétitivité")
        # Whole words, case-insensitive: pages 1 (2), 3 (2), 4 (1).
        self.assertEqual(found["occurrences"], 5)
        self.assertEqual(found["occurrences_case_sensitive"], 3)
        self.assertEqual(found["occurrences_including_longer_words"], 6)
        self.assertEqual(found["occurrences_by_page"], {"1": 2, "3": 2, "4": 1})

    def test_term_typed_without_accents(self) -> None:
        found = counts("competitivite")
        self.assertEqual(found["occurrences"], 0)
        self.assertEqual(found["occurrences_ignoring_accents"], 5)

    def test_absent_term_counts_zero(self) -> None:
        found = counts("licorne")
        self.assertEqual(found["occurrences"], 0)
        self.assertEqual(found["occurrences_including_longer_words"], 0)
        self.assertNotIn("occurrences_by_page", found)

    def test_multiword_term_and_typographic_apostrophe(self) -> None:
        text = document_stats.Text(
            "note.txt", "L’atelier ouvre. L'atelier  ferme.\nl'atelier\nrouvre."
        )
        self.assertEqual(counts("l'atelier", [text])["occurrences"], 3)
        self.assertEqual(counts("atelier ferme", [text])["occurrences"], 1)

    def test_page_markers_are_not_text(self) -> None:
        document = document_stats.execute("{}", [paged_text()])["documents"][0]
        self.assertEqual(document["pages"], 4)
        self.assertEqual(counts("page")["occurrences"], 0)
        words = sum(
            len([t for t in body.split() if any(c.isalnum() for c in t)])
            for body in PAGES
        )
        # The split word counts once.
        self.assertEqual(document["words"], words - 1)

    def test_pdf_pages_through_the_extractor(self) -> None:
        text, pages, errors = extract_bytes(pdf(PAGES), ".pdf")
        self.assertEqual(errors, [])
        result = document_stats.execute(
            json.dumps({"terms": ["compétitivité", "Vallon"]}),
            [document_stats.Text("rapport-vallon.pdf", text, pages)],
        )
        document = result["documents"][0]
        self.assertEqual(document["pages"], 4)
        self.assertEqual(document["counts"][0]["occurrences"], 5)
        self.assertEqual(document["counts"][1]["occurrences"], 2)
        self.assertIn("4 pages", result["summary"])
        self.assertIn("'compétitivité' 5 occurrences", result["summary"])

    def test_unpaginated_file_and_pasted_text_have_no_page_count(self) -> None:
        docx = document_stats.Text("notes.docx", "One two three.")
        document = document_stats.execute("{}", [docx])["documents"][0]
        self.assertIsNone(document["pages"])
        self.assertIn("not computed", document["pages_note"])
        pasted = document_stats.pasted_texts(
            [("user", "word " * 120), ("assistant", "x " * 400), ("user", "short")]
        )
        self.assertEqual([t.name for t in pasted], ["pasted text 1"])
        document = document_stats.execute("{}", pasted)["documents"][0]
        self.assertEqual(document["words"], 120)
        self.assertIsNone(document["pages"])

    def test_errors_filename_and_arguments(self) -> None:
        texts = [paged_text(), document_stats.Text("annexe.txt", "Budget annexe.")]
        one = document_stats.execute(
            json.dumps({"filename": "annexe.txt", "terms": ["budget"]}), texts
        )
        self.assertEqual([d["filename"] for d in one["documents"]], ["annexe.txt"])
        unknown = document_stats.execute(json.dumps({"filename": "x.pdf"}), texts)
        self.assertEqual(unknown["error"], "unknown_filename")
        self.assertEqual(
            document_stats.execute('{"terms": "budget"}', texts)["error"],
            "invalid_arguments",
        )
        self.assertEqual(document_stats.execute("{}", [])["error"], "no_document")
        unread = document_stats.Text("scan.pdf", "[Page 1]\nA", 2, ("PDF page 2/2",))
        self.assertFalse(
            document_stats.execute("{}", [unread])["documents"][0]["complete"]
        )

    def test_quoted_terms_are_always_counted(self) -> None:
        question = "Combien de fois le mot « compétitivité » et “Vallon” apparaissent ?"
        self.assertEqual(
            document_stats.quoted_terms(question), ["compétitivité", "Vallon"]
        )
        merged = json.loads(
            document_stats.merged_terms(
                '{"terms": ["Compétitivité"]}', document_stats.quoted_terms(question)
            )
        )
        self.assertEqual(merged["terms"], ["Compétitivité", "Vallon"])
        self.assertEqual(
            json.loads(document_stats.merged_terms("not json", ["x"]))["terms"], ["x"]
        )


class RelevanceTests(unittest.TestCase):
    def test_counting_requests_in_five_languages(self) -> None:
        for question in (
            "Avec le terminal, compte le nombre de pages du PDF joint et le nombre "
            "de fois où le mot « compétitivité » apparaît.",
            "combien de pages fait ce document ?",
            "Combien y a-t-il de mots dans le texte ?",
            "tu peux compter les occurences du mot atelier stp",
            "How many times does the word budget appear in the report?",
            "Give me the page count and the number of words.",
            "count the occurrences of 'Vallon'",
            "Wie oft kommt das Wort Werkstatt im Dokument vor?",
            "¿Cuántas veces aparece la palabra taller?",
            "Quante volte compare la parola officina?",
        ):
            with self.subTest(question=question):
                self.assertTrue(document_stats.relevant(question))

    def test_other_document_requests_keep_their_request(self) -> None:
        for question in (
            "fais-moi une synthèse d'une page",
            "Résume ce rapport en tenant compte des annexes.",
            "Fais un compte rendu du document.",
            "Summarize the attached report in five points.",
            "What does page 3 say about the budget?",
            "Does this count as a breach of contract?",
        ):
            with self.subTest(question=question):
                self.assertFalse(document_stats.relevant(question))

    def test_terminal_requests(self) -> None:
        self.assertTrue(document_stats.terminal_requested("Avec le terminal, compte"))
        self.assertTrue(document_stats.terminal_requested("use Python to count words"))
        self.assertTrue(document_stats.terminal_requested("en ligne de commande"))
        self.assertFalse(document_stats.terminal_requested("compte les pages du PDF"))


def gateway_model() -> GatewayModel:
    return GatewayModel(
        "http://unused",
        Configuration(
            input_eur_per_mtok=Decimal("0.1"),
            output_eur_per_mtok=Decimal("0.1"),
            max_tokens=1000,
        ),
    )


def counting_request(question: str) -> ChatRequest:
    return ChatRequest.model_validate(
        {
            "atlas_ui": True,
            "messages": [{"role": "user", "content": question}],
            "documents": [
                {
                    "filename": "rapport-vallon.pdf",
                    "file_data": base64.b64encode(pdf(PAGES)).decode(),
                }
            ],
        }
    )


class DocumentRouteTests(unittest.TestCase):
    def run_turn(
        self, question: str, first: Turn | Exception
    ) -> tuple[list[dict[str, Any]], list[list[dict[str, Any]]], Interaction]:
        model = gateway_model()
        seen: list[dict[str, Any]] = []
        captured: list[list[dict[str, Any]]] = []

        async def complete(messages: list[dict[str, Any]], timeout: float) -> Turn:
            captured.append(messages)
            seen.append(
                {
                    "tools": [
                        function["name"]
                        for t in model.tools
                        if isinstance(function := t.get("function"), dict)
                    ],
                    "required": model.required_file_tool,
                    "answer_only": model.answer_only,
                    "sink": model.sink is not None,
                }
            )
            if len(captured) > 1:
                return Turn("Le document compte 4 pages.")
            if isinstance(first, Exception):
                raise first
            return first

        item = Interaction()
        with (
            patch.object(GatewayModel, "connect", return_value=model),
            patch.object(model, "complete", complete),
        ):
            asyncio.run(process_documents(counting_request(question), item))
        return seen, captured, item

    def test_named_terms_are_counted_before_the_answer(self) -> None:
        seen, captured, item = self.run_turn(
            "Avec le terminal, compte le nombre de pages et le nombre de fois où le "
            "mot « compétitivité » apparaît.",
            Turn("Le document compte 4 pages."),
        )
        # One call: the counts are a fact of the turn, no tool to hope for.
        self.assertEqual(len(captured), 1)
        self.assertEqual(seen[0]["tools"], [])
        self.assertIsNone(seen[0]["required"])
        system = str(captured[0][0]["content"])
        self.assertIn(document_stats.FACTS_INSTRUCTION, system)
        self.assertIn(document_stats.TERMINAL_INSTRUCTION, system)
        facts = facts_of(captured[0])
        assert facts is not None
        document = facts["documents"][0]
        self.assertEqual(document["pages"], 4)
        self.assertEqual(
            {c["term"]: c["occurrences"] for c in document["counts"]},
            {"compétitivité": 5},
        )
        self.assertTrue(facts["asked"]["pages"])
        self.assertEqual(item.reponse, "Le document compte 4 pages.")

    def test_unnamed_term_requires_the_tool(self) -> None:
        call = Call("c1", "document_statistics", json.dumps({"terms": ["Vallon"]}))
        seen, captured, item = self.run_turn(
            "Combien de fois le nom de la vallée apparaît-il ?", Turn("", (call,))
        )
        self.assertEqual(len(captured), 2)
        self.assertEqual(seen[0]["required"], "document_statistics")
        self.assertTrue(seen[1]["answer_only"])
        facts = facts_of(captured[0])
        assert facts is not None
        self.assertTrue(facts["asked"]["occurrences_without_named_term"])
        output = json.loads(str(captured[1][-1]["content"]))
        self.assertEqual(output["documents"][0]["counts"][0]["occurrences"], 2)
        self.assertEqual(captured[1][-2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(item.steps[0]["phase"], "document_statistics")

    def test_provider_that_skips_the_required_call_still_gets_counts(self) -> None:
        seen, captured, item = self.run_turn(
            "How many times does each town name appear?", Turn("About 3.")
        )
        self.assertEqual(len(captured), 2)
        output = json.loads(str(captured[1][-1]["content"]))
        self.assertEqual(output["documents"][0]["pages"], 4)

    def test_refused_required_call_falls_back_to_code(self) -> None:
        question = "Combien de fois chaque ville apparaît ?"
        refused = GatewayError("invalid_request", 400, "tool_choice")
        seen, captured, item = self.run_turn(question, refused)
        output = json.loads(str(captured[1][-1]["content"]))
        self.assertEqual(output["documents"][0]["pages"], 4)
        # A gateway without this tool in its required list answers 422.
        older = GatewayCallError("http_422", 422)
        seen, captured, item = self.run_turn(question, older)
        self.assertEqual(len(captured), 2)
        budget = GatewayError("cost_budget", 429, "daily budget")
        with self.assertRaises(GatewayError):
            self.run_turn(question, budget)

    def test_other_turns_keep_their_request(self) -> None:
        seen, captured, item = self.run_turn(
            "Résume ce rapport en trois points.", Turn("Trois points.")
        )
        self.assertEqual(len(captured), 1)
        self.assertEqual(seen[0]["tools"], [])
        self.assertIsNone(facts_of(captured[0]))
        self.assertNotIn(
            document_stats.FACTS_INSTRUCTION, str(captured[0][0]["content"])
        )


def facts_of(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for message in messages:
        content = str(message.get("content", ""))
        if content.startswith('{"computed_document_statistics"'):
            found: dict[str, Any] = json.loads(content)["computed_document_statistics"]
            return found
    return None


class FactsTests(unittest.TestCase):
    def test_terms_read_from_the_question(self) -> None:
        for question, terms in (
            ("Combien de fois le mot « compétitivité » apparaît ?", ["compétitivité"]),
            ("compte les occurences du mot atelier stp", ["atelier"]),
            ("How many times does the word budget appear?", ["budget"]),
            ("Count the term “Vallon” and the word ateliers.", ["Vallon", "ateliers"]),
            ("Wie oft kommt das Wort Werkstatt vor?", ["Werkstatt"]),
            ("Combien de pages fait ce PDF ?", []),
        ):
            with self.subTest(question=question):
                self.assertEqual(document_stats.asked_terms(question), terms)

    def test_facts_only_on_counting_requests_with_texts(self) -> None:
        texts = [paged_text()]
        self.assertIsNone(document_stats.facts("Résume ce rapport.", texts))
        self.assertIsNone(document_stats.facts("Combien de pages ?", []))
        facts = document_stats.facts("How many words and pages?", texts)
        assert facts is not None
        self.assertTrue(facts["asked"]["words"] and facts["asked"]["pages"])
        self.assertEqual(facts["documents"][0]["pages"], 4)
        unread = document_stats.facts("Combien de pages ?", [], ["scan.pdf"])
        assert unread is not None
        self.assertEqual(unread["not_counted"][0]["filename"], "scan.pdf")

    def test_correction_names_the_counts_the_answer_misses(self) -> None:
        facts = document_stats.facts(
            "Compte les pages et le nombre de fois où le mot « compétitivité » "
            "apparaît.",
            [paged_text()],
        )
        wrong = "Le document fait 4 pages ; « compétitivité » apparaît 48 fois."
        note = document_stats.correction(wrong, facts, "fr")
        self.assertIn("« compétitivité » : 5 occurrences", note)
        self.assertNotIn("4 pages", note)
        right = "4 pages, et « compétitivité » apparaît 5 fois (comptage par code)."
        self.assertEqual(document_stats.correction(right, facts, "fr"), "")
        english = document_stats.correction("It appears 48 times.", facts, "en")
        self.assertTrue(english.startswith("Counted by code over the full text"))
        self.assertEqual(document_stats.correction(wrong, None, "fr"), "")

    def test_thousands_separators_count_as_the_number(self) -> None:
        long_text = document_stats.Text("long.txt", "mot " * 1234)
        facts = document_stats.facts("Combien de mots ?", [long_text])
        self.assertEqual(
            document_stats.correction("Il y a 1 234 mots.", facts, "fr"), ""
        )
        self.assertEqual(
            document_stats.correction("Il y a 1,234 words.", facts, "en"), ""
        )

    def test_turn_record_survives_a_child_task(self) -> None:
        async def child() -> None:
            document_stats.hold(paged_text())
            document_stats.facts("Combien de pages ?", document_stats.held_texts())

        async def turn() -> dict[str, Any] | None:
            token = document_stats.begin()
            try:
                await asyncio.create_task(child())
                return document_stats.current_facts()
            finally:
                document_stats.end(token)

        facts = asyncio.run(turn())
        assert facts is not None
        self.assertEqual(facts["documents"][0]["pages"], 4)
        self.assertEqual(document_stats.held_texts(), ())


class ServedPathTests(unittest.TestCase):
    def test_attachments_read_in_process_are_counted_on_their_full_text(
        self,
    ) -> None:
        from services.orchestrator.attachment_reading import read_announced

        request = counting_request("Combien de pages et de mots ?")

        async def read() -> tuple[list[dict[str, object]], tuple[Any, ...]]:
            token = document_stats.begin()
            try:
                with patch("packages.attachment_reader.DOCUMENT_CONTEXT_BYTES", 100):
                    sources = await read_announced(
                        request, Interaction(), None, read_document_bytes=True
                    )
                return sources, document_stats.held_texts()
            finally:
                document_stats.end(token)

        sources, held = asyncio.run(read())
        # The model reads a summary; the count reads the whole text.
        self.assertTrue(sources[0]["hierarchical_synthesis"])
        self.assertEqual(len(held), 1)
        self.assertEqual(held[0].pages, 4)
        self.assertIn("COMPÉTITIVITÉ", held[0].text)


class GatewayTests(unittest.TestCase):
    def test_count_tool_may_be_required(self) -> None:
        AgentRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "Count."}],
                "tools": [document_stats.declaration()],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "document_statistics"},
                },
                "timeout": 5.0,
                "local_enabled": False,
                "observe": True,
                "profile": "atlas-glm",
            }
        )


if __name__ == "__main__":
    unittest.main()
