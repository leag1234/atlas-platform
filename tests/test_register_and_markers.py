"""Service text follows the conversation's register; internal markers never
reach the person (staging -145, classes C13 and C4).

C13: a conversation in the familiar form read the code's own text in the
polite form: the trip notes asking to check the times with the operator, the
last step of the plan card (hand you the app), retry sentences, receipts. C4: an app answer listed its files with
"[unverified source]", a strict answer named the tool `web_search`.
All content is fictional.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from packages import addressing, user_errors
from packages.artifact_media import Media
from packages.owned_server import STARTING
from services.orchestrator import (
    app_media,
    code_mode,
    expectations,
    journey,
    loop,
    source_truth,
    transit_planner,
    travel_claims,
    travel_rebuild,
    work_estimates,
    work_plan,
)
from services.orchestrator.markers import tool_words, without_markers
from services.orchestrator.narration import TurnHold, without_internal
from services.orchestrator.work_progress import PLAN_LABELS

FAMILIAR_TEXT = {
    "fr": "salut, tu peux me faire une appli pour réviser ?",
    "es": "hola, ¿puedes hacerme una app para repasar?",
    "de": "hallo, kannst du mir eine App zum Lernen machen?",
    "it": "ciao, puoi farmi un'app per ripassare?",
}
POLITE_TEXT = {
    "fr": "Bonjour, pouvez-vous me faire une application ?",
    "es": "Buenos días, ¿podría usted hacerme una aplicación?",
    "de": "Guten Tag, können Sie mir eine App machen?",
    "it": "Buongiorno, Lei potrebbe farmi un'app?",
}
# Polite forms that must not survive in a familiar conversation (test-side
# detectors, one per language).
POLITE_LEFT = {
    "fr": re.compile(
        r"\b(?:vous|votre|vos)\b|\b(?!chez\b|assez\b|nez\b)\w{2,}ez\b", re.I
    ),
    "de": re.compile(r"\b(?:Sie|Ihr|Ihre|Ihren|Ihrem|Ihrer|Ihres|Ihnen)\b"),
    "es": re.compile(
        r"\b(?:usted|ustedes|int[ée]ntelo|haga|vuelva|divida|env[íi]e|env[íi]ela|"
        r"pida|precise|abra|compruebe|compru[ée]belo|compru[ée]belas|t[ée]ngalo|"
        r"d[íi]game|entregarle|darle|desea|pidi[óo]|su (?:mensaje|posici[óo]n|m[áa]quina|"
        r"primer|d[íi]a))\b",
        re.I,
    ),
    "it": re.compile(
        r"\b(?:Lei|consegnarle|darle|verifichi|dica|controlli|tenga|"
        r"pu[òo] viaggiare|sua posizione|suo giorno|suo tempo)\b",
        re.I,
    ),
}


def service_tables() -> list[tuple[str, dict[str, Any]]]:
    """Every table of person-facing service text: language to text, or
    language to a table of texts."""
    tables: list[tuple[str, dict[str, Any]]] = [
        (f"user_errors.{name}", value)
        for name, value in vars(user_errors).items()
        if isinstance(value, dict) and name.lstrip("_").isupper()
    ]
    tables += [
        ("PLAN_LABELS", PLAN_LABELS),
        ("travel_claims.WORDING", travel_claims.WORDING),
        ("travel_rebuild.WORDING", travel_rebuild.WORDING),
        ("journey.WORDS", journey.WORDS),
        ("app_media.NOTE", app_media.NOTE),
        ("code_mode.WORK_FAILURE", code_mode.WORK_FAILURE),
        ("loop.APPROVAL_WAITING", loop.APPROVAL_WAITING),
        ("expectations._UNMET", expectations._UNMET),
    ]
    return tables


def texts(table: object, language: str = "") -> list[tuple[str, str]]:
    """(language, text) pairs of a table, however it nests."""
    found: list[tuple[str, str]] = []
    if isinstance(table, dict):
        for key, value in table.items():
            lang = key if key in ("fr", "de", "es", "it", "en") else language
            found += texts(value, lang)
    elif isinstance(table, str) and language:
        found.append((language, table))
    return found


class Conversation:
    def __init__(self, text: str, language: str) -> None:
        self.text, self.language = text, language

    def __enter__(self) -> None:
        self.token = addressing.begin([self.text], self.language)

    def __exit__(self, *_: object) -> None:
        addressing.end(self.token)


class RegisterTests(unittest.TestCase):
    def test_one_detection_shared_with_the_trip_block(self) -> None:
        self.assertIs(transit_planner.register, addressing.register)
        for language, text in FAMILIAR_TEXT.items():
            with Conversation(text, language):
                self.assertEqual(addressing.turn_register(language), "informal")
        for language, text in POLITE_TEXT.items():
            with Conversation(text, language):
                self.assertEqual(addressing.turn_register(language), "formal")

    def test_every_polite_service_phrase_has_its_familiar_form(self) -> None:
        for name, table in service_tables():
            for language, text in texts(table):
                if language not in POLITE_LEFT:
                    continue
                familiar = addressing.familiar(text, language)
                with self.subTest(table=name, language=language, text=text[:60]):
                    self.assertIsNone(POLITE_LEFT[language].search(familiar))
                    # Placeholders are kept, and a second pass changes nothing.
                    self.assertEqual(
                        re.findall(r"\{\w+\}", familiar), re.findall(r"\{\w+\}", text)
                    )
                    self.assertEqual(addressing.familiar(familiar, language), familiar)

    def test_trip_notes(self) -> None:
        expected = {
            "fr": ("vérifie-les auprès de l’exploitant", "Vérifie le trajet complet"),
            "es": ("compruébalas con el operador", "Comprueba el viaje completo"),
            "de": ("prüfe die Fahrpläne", "Prüfe die ganze Reise"),
            "it": ("verifica gli orari", "Controlla il viaggio completo"),
        }
        for language, (block_note, planner) in expected.items():
            with self.subTest(language=language):
                block = " ".join(
                    str(value)
                    for value in transit_planner.WORDS[language].values()
                    if isinstance(value, str)
                )
                styled = transit_planner.in_register(
                    block, language, FAMILIAR_TEXT[language]
                )
                self.assertIn(block_note, styled)
                self.assertIsNone(POLITE_LEFT[language].search(styled))
                with Conversation(FAMILIAR_TEXT[language], language):
                    self.assertIn(planner, travel_rebuild._wording("planner", language))
                    missing = journey._words(language)["missing"]
                    self.assertIsNone(POLITE_LEFT[language].search(missing))
                with Conversation(POLITE_TEXT[language], language):
                    self.assertEqual(
                        travel_rebuild._wording("planner", language),
                        travel_rebuild.WORDING["planner"][language],
                    )

    def test_not_verified_note(self) -> None:
        for language in ("fr", "es", "de", "it"):
            with Conversation(FAMILIAR_TEXT[language], language):
                note = addressing.addressed(
                    travel_claims.wording("route", language), language
                )
            with self.subTest(language=language):
                self.assertIsNone(POLITE_LEFT[language].search(note))
                self.assertIn("{values}", note)


class PlanCardTests(unittest.TestCase):
    def setUp(self) -> None:
        # A private history store (as in test_work_plan.py).
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        seed = Path(folder.name) / "seed.json"
        rows = [["app", "turn", "atlas-test", 40.0 + i, 0.01, True] for i in range(30)]
        seed.write_text(json.dumps({"measures": rows}))
        for patcher in (
            patch.dict(os.environ, {"ATLAS_DATA_DIR": folder.name}),
            patch.object(work_estimates, "SEED", seed),
            patch.object(work_estimates, "_seed_cache", None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def plan(self, language: str) -> work_plan.ChatPlan:
        plan = work_plan.ChatPlan(language, "atlas-test")
        plan.propose(work_plan.app_steps(language, False), source="app")
        plan.code_step(1)
        plan.code_step(2)
        plan.finish(True)
        return plan

    def test_the_delivery_step_follows_the_register(self) -> None:
        familiar = {
            "fr": "Te livrer l’application",
            "es": "Entregarte la aplicación",
            "de": "Dir die App übergeben",
            "it": "Consegnarti l’app",
        }
        for language, step in familiar.items():
            with self.subTest(language=language):
                plan = self.plan(language)
                with Conversation(FAMILIAR_TEXT[language], language):
                    shown = [s["text"] for s in plan.payload()["steps"]]
                    self.assertEqual(shown[-1], step)
                    self.assertIn(step, plan.markdown())
                    self.assertIsNone(POLITE_LEFT[language].search(plan.markdown()))
                for neutral in (POLITE_TEXT[language], "ok"):
                    with Conversation(neutral, language):
                        self.assertEqual(
                            plan.payload()["steps"][-1]["text"],
                            PLAN_LABELS[language]["app_deliver"],
                        )

    def test_the_model_is_told_the_register(self) -> None:
        self.assertIn("tu or vous", work_plan.SYSTEM)
        self.assertNotIn("Vous livrer", work_plan.SYSTEM)


class SentenceTests(unittest.TestCase):
    def test_receipt_of_embedded_files(self) -> None:
        media = Media(
            name="flag",
            mime="image/png",
            data=b"x",
            url="https://images.example.org/flag-of-elsewhere.png",
            filename="flag-of-elsewhere.png",
            licence="CC0",
            licence_source="person",
        )
        expected = {
            "fr": "(indiquée par toi)",
            "es": "(indicada por ti)",
            "de": "(von dir angegeben)",
            "it": "(indicata da te)",
        }
        for language, words in expected.items():
            with self.subTest(language=language):
                with Conversation(FAMILIAR_TEXT[language], language):
                    note = app_media.media_note(language, [media], [])
                self.assertIn(words, note)
                with Conversation(POLITE_TEXT[language], language):
                    polite = app_media.media_note(language, [media], [])
                self.assertIn(
                    app_media.NOTE[language]["person"].format(licence="CC0"), polite
                )

    def test_errors_and_the_time_cut_sentence(self) -> None:
        with Conversation(FAMILIAR_TEXT["fr"], "fr"):
            self.assertIn(
                "Pose une question plus courte",
                user_errors.time_cut_sentence(
                    "fr", written=False, finished=False, owned=True, limit=90
                ),
            )
            self.assertIn("Renvoie ton message", user_errors.app_failure("app", "fr"))
            self.assertIn("Réessaie", user_errors.unexpected_failure("fr"))
        with Conversation(FAMILIAR_TEXT["de"], "de"):
            self.assertIn(
                "Deine Nachricht",
                str(user_errors.owned_server_sentence(STARTING, "de", {})),
            )
        with Conversation(FAMILIAR_TEXT["es"], "es"):
            self.assertIn("Inténtalo", user_errors.unexpected_failure("es"))
        # Unsure: the polite form, as written.
        with Conversation("ok merci", "fr"):
            self.assertIn("Réessayez", user_errors.unexpected_failure("fr"))
        self.assertIn("Réessayez", user_errors.unexpected_failure("fr"))

    def test_a_name_with_a_capital_is_not_a_verb(self) -> None:
        with Conversation(FAMILIAR_TEXT["es"], "es"):
            self.assertEqual(
                addressing.addressed("Tren a Haga y Abra.", "es"), "Tren a Haga y Abra."
            )


class MarkerTests(unittest.TestCase):
    def test_bracketed_markers_and_tool_names_are_removed(self) -> None:
        answer = (
            "- drapeau.png ([unverified source] SVG converti en image PNG)\n"
            "Recherche web : l'outil `web_search` est bloqué [citation needed].\n"
            "Voir [la page](https://example.org) et la note [1]. - [ ] à faire"
        )
        with Conversation("fais-moi un quiz", "fr"):
            cleaned = without_markers(answer)
        self.assertNotIn("[unverified source]", cleaned)
        self.assertNotIn("[citation needed]", cleaned)
        self.assertNotIn("web_search", cleaned)
        self.assertIn("(SVG converti", cleaned)
        self.assertIn("l'outil recherche web est bloqué.", cleaned)
        self.assertIn("[la page](https://example.org)", cleaned)
        self.assertIn("[1]", cleaned)
        self.assertIn("- [ ] à faire", cleaned)

    def test_the_person_s_own_words_and_code_are_kept(self) -> None:
        quoted = "Tu as écrit « [placeholder] » et `web_search` dans ton fichier."
        with Conversation("que veut dire [placeholder] et web_search ici ?", "fr"):
            self.assertEqual(without_markers(quoted), quoted)
        code = "```python\ndef web_search(q):\n    return [todo]\n```\nLa fonction `web_search` cherche."
        with Conversation("écris une fonction de recherche", "fr"):
            self.assertEqual(without_markers(code), code)
        for text in ("my_file.py", "https://x.example/web_search", "web_search_v2"):
            self.assertEqual(without_markers(text), text)

    def test_every_answer_path(self) -> None:
        answer = "La page est ici [unverified source].\n\nOutil `web_fetch` utilisé."
        with Conversation("donne-moi la page", "fr"):
            final = without_internal(answer)
            sent: list[dict[str, object]] = []

            async def sink(event: dict[str, object]) -> None:
                sent.append(event)

            async def stream() -> None:
                hold = TurnHold(sink)
                for chunk in (
                    "Voici la réponse.\n\n",
                    "Deuxième paragraphe.\n\n",
                    answer,
                ):
                    await hold({"delta": {"content": chunk}})
                await hold.release()

            asyncio.run(stream())
        streamed = "".join(
            str(event["delta"]["content"])  # type: ignore[index]
            for event in sent
            if isinstance(event.get("delta"), dict)
        )
        for text in (final, streamed):
            self.assertNotIn("[unverified source]", text)
            self.assertNotIn("web_fetch", text)
            self.assertIn("lecture de pages web", text)

    def test_an_untraced_address_reads_in_the_person_s_language(self) -> None:
        with Conversation("donne-moi la source", "fr"):
            text = source_truth.without_unretrieved(
                "Selon https://invented.example/page, oui.", set()
            )
        self.assertIn("source non vérifiée", text)
        self.assertNotIn("[", text)

    def test_tool_words(self) -> None:
        self.assertEqual(tool_words("web_search", "fr"), "recherche web")
        self.assertEqual(tool_words("web_search", "de"), "Websuche")
        self.assertEqual(tool_words("some_tool", "fr"), "some tool")


if __name__ == "__main__":
    unittest.main()


class LeadingImperativeTests(unittest.TestCase):
    """Staging -187: a request that opens with a familiar imperative and no
    pronoun ("dessine ...") was answered in the polite form. Fictional texts."""

    def test_the_opening_verb_says_the_register(self) -> None:
        from packages.addressing import register

        for text, language, expected in (
            ("dessine trois lanternes vertes sur un quai", "fr", "informal"),
            ("salut, fais un poster pour la fête du verger", "fr", "informal"),
            ("Dessinez un phare sous la pluie", "fr", "formal"),
            ("Faites un plan du jardin partagé", "fr", "formal"),
            ("dibuja cuatro cometas azules", "es", "informal"),
            ("Dibuje un faro al atardecer", "es", "formal"),
            ("disegna una barca con due vele", "it", "informal"),
            ("Zeichne einen Fuchs im Schnee", "de", "informal"),
            ("Zeichnen Sie einen Fuchs im Schnee", "de", "formal"),
        ):
            with self.subTest(text=text):
                self.assertEqual(register(text, language), expected)

    def test_no_opening_verb_says_nothing(self) -> None:
        from packages.addressing import register

        for text, language in (
            ("Une maison au bord du canal", "fr"),
            ("Liste des courses pour samedi", "fr"),
            ("Quelle heure est-il à Lisbonne ?", "fr"),
            ("Una casa junto al río", "es"),
            ("Mi serve un faro", "it"),
        ):
            with self.subTest(text=text):
                self.assertEqual(register(text, language), "")

    def test_a_picture_text_follows_the_opening_verb(self) -> None:
        from packages import addressing
        from services.orchestrator.pictures.texts import text

        for words, language, polite, familiar in (
            ("dessine six lanternes", "fr", "Cliquez", "Clique"),
            ("dibuja seis faroles", "es", "Dígame", "Dime"),
        ):
            with self.subTest(words=words):
                token = addressing.begin([words], language)
                try:
                    shown = (
                        text(language, "hint")
                        + " "
                        + text(language, "same_drawing", version=1)
                    )
                finally:
                    addressing.end(token)
                self.assertNotIn(polite, shown)
                self.assertIn(familiar, shown)
