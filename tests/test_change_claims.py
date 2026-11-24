"""The answer of a change request whose code is the stored version again:
the plain statement first, then what the model wrote without its claims of a
change. Apps and sentences are fictional."""

from __future__ import annotations

import unittest

from services.orchestrator.artifact_tools import FOOTER_LABELS, identical_answer
from services.orchestrator.change_claims import without_change_claims


class WithoutClaimsTests(unittest.TestCase):
    def test_claims_of_change_go_in_every_language(self) -> None:
        for text in (
            "I changed the colour of the kite.",
            "The score now resets after nine.",
            "J'ai ajouté un bouton pour la pluie.",
            "La page affiche désormais la lune.",
            "Ich habe die Farbe des Drachens angepasst.",
            "Der Zähler springt jetzt auf null.",
            "He cambiado el color del cometa.",
            "He corregido el contador de estrellas.",
            "Ho corretto il contatore delle stelle.",
            "Ahora el marcador vuelve a cero.",
            "Ho aggiornato il colore dell'aquilone.",
            "Il punteggio adesso riparte da zero.",
            "This is a new version of the page.",
        ):
            with self.subTest(text=text):
                self.assertEqual(without_change_claims(text), "")

    def test_statements_of_behaviour_stay(self) -> None:
        for text in (
            "The score starts at 3 and each press adds 2.",
            "Au chargement la page affiche « Niveau : 3 ».",
            "Beim Neuladen steht wieder 3 da.",
            "Después del reinicio vuelve a 3.",
            "Dopo il reset torna a 3.",
            "Browsertest: nicht ausgeführt.",
        ):
            with self.subTest(text=text):
                self.assertEqual(without_change_claims(text), text)

    def test_a_mixed_answer_keeps_only_what_claims_nothing(self) -> None:
        text = (
            "I fixed the lamp counter. It starts at 4 and each press adds 2.\n\n"
            "```html\n<p>now</p>\n```\n\n"
            "I added a reset line. Nothing runs offline."
        )
        kept = without_change_claims(text)
        self.assertIn("It starts at 4 and each press adds 2.", kept)
        self.assertIn("Nothing runs offline.", kept)
        self.assertIn("```html", kept)
        self.assertNotIn("fixed", kept)
        self.assertNotIn("I added", kept)


class IdenticalAnswerTests(unittest.TestCase):
    def test_the_statement_leads_and_the_rest_follows(self) -> None:
        for language in ("en", "fr", "de", "es", "it"):
            with self.subTest(language=language):
                answer = identical_answer("Beim Start 4, danach 6.", 3, language)
                statement = FOOTER_LABELS[language]["identical"].format(version=3)
                self.assertTrue(answer.startswith(statement))
                self.assertTrue(answer.endswith("Beim Start 4, danach 6."))

    def test_without_content_the_person_is_told_what_to_do_next(self) -> None:
        for language in ("en", "fr", "de", "es", "it"):
            with self.subTest(language=language):
                answer = identical_answer("Ich habe es korrigiert.", 2, language)
                statement = FOOTER_LABELS[language]["identical"].format(version=2)
                self.assertTrue(answer.startswith(statement))
                self.assertGreater(len(answer), len(statement) + 60)
                self.assertNotIn("{", answer)


if __name__ == "__main__":
    unittest.main()
