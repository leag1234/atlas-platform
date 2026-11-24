"""A request for the answer alone gets its deliverable and nothing around it."""

import unittest

from services.orchestrator.only_request import asks_only, restrict

LINKS = (
    "[Télécharger relevé.py](/api/atlas/orgs/o/projects/p/deliverables/d/download)"
    " · [Aperçu](/api/atlas/orgs/o/projects/p/deliverables/d/preview)"
)
PLAN = "**Plan** (terminé en 5 s)\n\n- ✓ Lancer le script\n- ✓ Montrer les lignes"

ONLY = (
    "Lance le script et montre-moi seulement les deux lignes de la console.",
    "Run it and show me only the three result lines.",
    "Führ den Code aus und zeig mir nur die zwei Zeilen.",
    "Ejecútalo y muéstrame solo las dos líneas de salida.",
    "Eseguilo e dimmi soltanto il risultato.",
    "Execute e mostre apenas as duas linhas.",
    "Dis juste ce qu'il reste à vérifier.",
    "Give me the figure, nothing else.",
    "Donne la date. Rien d'autre.",
    "Nenne die Stadt, nichts weiter.",
    "Escribe el nombre, nada más.",
    "Scrivi la parola, niente altro.",
    "Escreva a palavra, nada mais.",
    "Réponds sans commentaire.",
    "Reply with the number, no extra text.",
)
NOT_ONLY = (
    "Change only the last weight from 2 to 7 and run it.",
    "Ne change que le titre de la page 2.",
    "Ändere nur die Überschrift.",
    "Show me an example that is just right for a beginner, with comments.",
    "Réponds uniquement si tu es sûr du résultat, avec une explication.",
    "Write a long essay about harbours.",
    "Donne-moi un exemple juste.",
)


class AsksOnlyTests(unittest.TestCase):
    def test_requests_for_the_deliverable_alone(self) -> None:
        for request in ONLY:
            with self.subTest(request=request):
                self.assertTrue(asks_only(request))

    def test_limits_of_a_change_or_a_condition_are_not_limits_of_the_answer(
        self,
    ) -> None:
        for request in NOT_ONLY:
            with self.subTest(request=request):
                self.assertFalse(asks_only(request))


class RestrictTests(unittest.TestCase):
    def test_console_lines_keep_neither_links_nor_plan_nor_lead(self) -> None:
        answer = (
            "Voici les deux lignes de la console :\n\n```\nTOTAL: 12\nRESTE: 3\n```"
            f"\n\n{LINKS}\n\n{PLAN}"
        )
        self.assertEqual(restrict(answer), "```\nTOTAL: 12\nRESTE: 3\n```")

    def test_a_quotation_is_kept_without_its_lead_and_its_remark(self) -> None:
        answer = (
            "Voici la phrase :\n\n> Il reste à contrôler la tenue des étiquettes.\n\n"
            "Elle reprend l'état de la fiche."
        )
        self.assertEqual(
            restrict(answer), "Il reste à contrôler la tenue des étiquettes."
        )

    def test_a_blank_line_inside_code_does_not_split_it(self) -> None:
        answer = "Result:\n\n```\na\n\nb\n```\n\n" + LINKS
        self.assertEqual(restrict(answer), "```\na\n\nb\n```")

    def test_what_follows_code_may_explain_a_failure_and_stays(self) -> None:
        answer = "```\nTraceback\n```\n\nThe script failed on line 2: a missing file."
        self.assertEqual(restrict(answer), answer)

    def test_a_file_answer_made_of_links_keeps_them(self) -> None:
        self.assertEqual(restrict(LINKS), LINKS)

    def test_a_picture_is_not_a_link_line(self) -> None:
        answer = "![Phare](https://x.example/images/a)\n\n" + LINKS
        self.assertEqual(restrict(answer), "![Phare](https://x.example/images/a)")

    def test_a_plain_answer_is_unchanged(self) -> None:
        answer = "Il reste à mesurer l'humidité.\n\nEt à relire la fiche."
        self.assertEqual(restrict(answer), answer)


if __name__ == "__main__":
    unittest.main()


EDIT_LIMITS = (
    "Change seulement la quantité de farine, ne change rien d'autre.",
    "Corrige le bouton Valider, rien d'autre.",
    "Change only the quantity in the second row, nothing else.",
    "Fix the score counter, nothing more.",
    "Ändere nur die Farbe des Zählers, sonst nichts ändern.",
    "Korrigiere den Titel, nichts anderes.",
    "Cambia solo el precio, nada más.",
    "Corrige solo eso, nada más.",
    "Modifica solo il titolo, niente altro.",
    "Correggi il pulsante, niente altro.",
)
APP_ANSWER = (
    "Le compteur affiche maintenant le score sur 10.\n\n"
    "[Ouvrir Quiz des volcans v2](/api/atlas/artifacts/a1b2c3/versions/2/download)\n\n"
    "**Plan** (terminé en 9 s)\n\n- ✓ Modifier le compteur"
)


class EditLimitTests(unittest.TestCase):
    def test_a_limit_of_the_change_is_not_a_limit_of_the_answer(self) -> None:
        for request in EDIT_LIMITS:
            with self.subTest(request=request):
                self.assertFalse(asks_only(request))

    def test_a_limit_of_the_answer_after_a_change_still_counts(self) -> None:
        for request in (
            "Change the price to 4, then show me only the total line.",
            "Corrige la formule et donne-moi seulement le résultat.",
            "Cambia el precio y dime solo el resultado.",
        ):
            with self.subTest(request=request):
                self.assertTrue(asks_only(request))

    def test_the_link_to_the_new_version_is_never_removed(self) -> None:
        restricted = restrict(APP_ANSWER)
        self.assertIn("/api/atlas/artifacts/a1b2c3/versions/2/download", restricted)
        self.assertNotIn("Plan", restricted)

    def test_a_saved_file_link_stays_when_the_file_is_the_output(self) -> None:
        answer = f"Le tableau des cotisations est prêt.\n\n{LINKS}\n\n{PLAN}"
        self.assertEqual(
            restrict(answer), f"Le tableau des cotisations est prêt.\n\n{LINKS}"
        )

    def test_outside_links_still_go(self) -> None:
        answer = "Il reste 3 lots.\n\n[Source](https://example.org/lots)"
        self.assertEqual(restrict(answer), "Il reste 3 lots.")
