"""Staging -177: the person's register reaches every answer path.

The person wrote "salut ! tu peux me faire une petite appli" (familiar form);
the code-written plan card said "Te livrer l'application", the model's app
answer said "Voici votre appli ... vous propose". The register sentence that
the trip instruction gave first (staging -155) is now part of the answer
instruction (packages.addressing.answer_instruction: the language instruction
and the register) every answer path appends (apps, documents, decks,
research, chat), and of the paths that write their own language line.

Fictional requests only.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from packages import addressing
from packages.addressing import answer_instruction as language_instruction
from packages.language import language_instruction as plain_instruction

ROOT = Path(__file__).resolve().parents[1]

FAMILIAR = {
    "fr": ("salut ! tu peux me faire une petite appli pour réviser ?", "tu", "vous"),
    "de": ("hallo, kannst du mir eine kleine App zum Lernen bauen?", "du", "Sie"),
    "es": ("hola, ¿puedes hacerme una pequeña app para repasar?", "tú", "usted"),
    "it": ("ciao, puoi farmi una piccola app per ripassare?", "tu", "Lei"),
}
POLITE = {
    "fr": ("Pourriez-vous me rédiger un document sur les marées ?", "vous", "tu"),
    "de": ("Können Sie mir bitte ein Dokument über Gezeiten schreiben?", "Sie", "du"),
    "es": ("¿Podría usted escribirme un documento sobre las mareas?", "usted", "tú"),
    "it": ("Lei potrebbe scrivermi un documento sulle maree?", "Lei", "tu"),
}


class Turn:
    """The addressing context of one request, as chat_stream sets it."""

    def __init__(self, text: str, language: str) -> None:
        self.text, self.language = text, language

    def __enter__(self) -> None:
        self.token = addressing.begin([self.text], self.language)

    def __exit__(self, *exc: object) -> None:
        addressing.end(self.token)


class RegisterInstructionTest(unittest.TestCase):
    def test_the_language_instruction_names_the_familiar_register(self) -> None:
        for language, (asked, own, other) in FAMILIAR.items():
            with self.subTest(language=language), Turn(asked, language):
                stated = language_instruction(language)
                self.assertIn(f"address them with '{own}', never '{other}'", stated)
                # The language instruction itself is unchanged before it.
                self.assertTrue(stated.startswith("\nAnswer in "))

    def test_the_language_instruction_names_the_polite_register(self) -> None:
        for language, (asked, own, other) in POLITE.items():
            with self.subTest(language=language), Turn(asked, language):
                self.assertIn(
                    f"address them with '{own}', never '{other}'",
                    language_instruction(language),
                )

    def test_no_register_no_sentence(self) -> None:
        without = language_instruction("fr")
        self.assertEqual(without, plain_instruction("fr"))
        self.assertNotIn("addresses you", without)
        for language, asked in (
            ("en", "can you build me a small quiz app?"),
            ("fr", "une appli pour réviser les capitales"),
            ("de", "eine App zum Lernen der Hauptstädte"),
        ):
            with self.subTest(language=language), Turn(asked, language):
                self.assertNotIn("addresses you", language_instruction(language))
        with Turn("une appli pour réviser les capitales", "fr"):
            self.assertEqual(language_instruction("fr"), without)

    def test_the_register_is_the_persons_latest_one(self) -> None:
        token = addressing.begin(
            [
                "Pourriez-vous me faire une appli ?",
                "merci, tu peux ajouter un chrono ?",
            ],
            "fr",
        )
        try:
            self.assertIn("'tu', never 'vous'", language_instruction("fr"))
        finally:
            addressing.end(token)

    def test_the_trip_instruction_is_the_same_sentence(self) -> None:
        from services.orchestrator import transit_planner as tp

        for language, (asked, _, _) in FAMILIAR.items():
            with self.subTest(language=language), Turn(asked, language):
                self.assertEqual(
                    tp.address_instruction([asked], language),
                    addressing.address_instruction(language),
                )


class AnswerPathsTest(unittest.TestCase):
    """Each answer path's instruction carries the register."""

    def test_app_document_deck_research_and_chat_paths(self) -> None:
        from services.orchestrator import (
            app_turn,
            chat_pipeline,
            closing,
            project_chat,
            project_transcript,
        )

        # The app path (app_turn), the chat, research, document and deck
        # paths (chat_pipeline's agent loop and closing calls) and the
        # project paths append the shared answer instruction.
        for module in (
            app_turn,
            chat_pipeline,
            closing,
            project_chat,
            project_transcript,
        ):
            with self.subTest(module=module.__name__):
                self.assertIs(vars(module)["answer_instruction"], language_instruction)
                # No path appends the bare language instruction any more.
                self.assertNotIn("language_instruction", vars(module))
        for language, (asked, own, _) in FAMILIAR.items():
            with self.subTest(language=language), Turn(asked, language):
                self.assertIn(
                    f"'{own}'", vars(app_turn)["answer_instruction"](language)
                )

    def test_paths_with_their_own_language_line(self) -> None:
        from services.orchestrator import person_lookup

        for language, (asked, own, other) in FAMILIAR.items():
            with self.subTest(language=language), Turn(asked, language):
                self.assertIn(
                    f"'{own}', never '{other}'",
                    person_lookup.synthesis_instruction(language),
                )
        self.assertNotIn("addresses you", person_lookup.synthesis_instruction("fr"))
        # The attachment answer and the Work engine name it too.
        document_chat = (ROOT / "services/orchestrator/document_chat.py").read_text()
        self.assertIn(
            "addressing.address_instruction(request.prose_lang)", document_chat
        )
        opencode = (ROOT / "services/orchestrator/work_opencode_turn.py").read_text()
        self.assertIn(
            "addressing.address_instruction(addressing.answer_language", opencode
        )


if __name__ == "__main__":
    unittest.main()
