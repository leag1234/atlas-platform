"""A request that defers any file until the person chooses is answered in text."""

import unittest

from packages.deferred_intent import creation_deferred
from services.orchestrator import request_router
from services.orchestrator.request_router import Decision

DEFERRED = (
    "Avant de créer le fichier PowerPoint, proposez-moi deux plans pour une "
    "conférence sur les cerfs-volants de nuit, et attendez que je choisisse.",
    "Tu peux d’abord me proposer ici trois sommaires pour l’atelier de reliure ? "
    "Attends mon choix avant de faire le fichier.",
    "Ne créez pas de nouveau document avant que j’aie choisi l’une des deux trames.",
    "Before you make the slides about moss gardens, give me two outlines and "
    "wait for my pick.",
    "Show me three title options first, before you build the report on tram maps.",
    "Don't create the spreadsheet yet: list the columns for the seed swap first.",
    "Bevor Sie die Präsentation über Leuchtturmwärter erstellen, schlagen Sie mir "
    "zwei Gliederungen vor und warten Sie auf meine Auswahl.",
    "Erstellen Sie noch keine Datei, bis ich mich entschieden habe.",
    "Antes de crear el documento sobre relojes de arena, muéstrame dos índices y "
    "espera a que elija uno.",
    "Prima di creare la presentazione sui mulini a vento, proponimi due scalette e "
    "aspetta che io scelga.",
)
NOT_DEFERRED = (
    "J’ai choisi le deuxième plan. Vous pouvez créer le PowerPoint maintenant ?",
    "Make a five-slide deck about lighthouse maintenance.",
    "Before you write the report, list the sources you used, then write it.",
    "Erstellen Sie bitte eine PowerPoint-Datei mit genau vier Folien über Kerzenzieher.",
    "Tu peux me faire un PDF A4 de deux pages pour la fête des lanternes ?",
    "Wait, I meant the green version: redo the spreadsheet with it.",
    "Hazme un documento Word de una página sobre cometas.",
)


class DeferredCreationTests(unittest.TestCase):
    def test_deferrals_in_five_languages(self) -> None:
        for text in DEFERRED:
            with self.subTest(text=text):
                self.assertTrue(creation_deferred(text))

    def test_requests_to_make_the_file_now(self) -> None:
        for text in NOT_DEFERRED:
            with self.subTest(text=text):
                self.assertFalse(creation_deferred(text))

    def test_a_routed_file_or_app_becomes_an_answer(self) -> None:
        for kind in ("file", "app"):
            routed = Decision(kind=kind, file_format="pptx" if kind == "file" else None)
            for follow_up in (False, True):
                settled = request_router.settle(
                    DEFERRED[0], routed.model_copy(update={"follow_up": follow_up})
                )
                assert settled is not None
                self.assertEqual(settled.kind, "answer")
                self.assertIsNone(settled.file_format)

    def test_the_choice_that_follows_still_makes_the_file(self) -> None:
        routed = Decision(
            kind="file",
            file_format="pptx",
            follow_up=True,
            changes_delivered=True,
        )
        self.assertEqual(request_router.settle(NOT_DEFERRED[0], routed), routed)

    def test_no_profile_decided(self) -> None:
        fallback = request_router.deliver_fallback(DEFERRED[0])
        assert fallback is not None
        self.assertEqual(fallback.kind, "answer")
        made = request_router.deliver_fallback(NOT_DEFERRED[0])
        assert made is not None
        self.assertEqual(made.kind, "file")


if __name__ == "__main__":
    unittest.main()
