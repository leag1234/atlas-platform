"""A picture asked for alone is shown alone (pictures/bare.py)."""

import unittest

from services.orchestrator.artifact_tools import HISTORY_REFERENCE_PATTERN, footer
from services.orchestrator.pictures.bare import bare, image_only
from services.orchestrator.pictures.texts import text

ARTIFACT = "4c" * 16
IMAGE = "https://atlas.example/images/" + "9e" * 16

ALONE: tuple[str, ...] = (
    "Tu peux en faire une version sous la pluie ? Affiche juste l’image ici, "
    "sans titre ni texte.",
    "je veux voir le dessin directement ici, sans titre ni texte.",
    "Réaffiche la même, sans la phrase au-dessus ni les liens en dessous.",
    "Rien au-dessus ni en dessous, s’il te plaît.",
    "Show me only the picture of the paper boat, nothing else.",
    "Nur das Bild vom Holzpferd bitte, ohne Satz davor.",
    "Solo la imagen del farol, sin texto aquí.",
    "Solo l'immagine della teiera, niente altro.",
)
ALONE += (
    "Fais-moi une affiche carrée pour un musée imaginaire. Pas d'autre texte, pas de cadre.",
    "A single poster for a fictional harbour festival, no frame please.",
    "Eine einzige Skizze der Windmühle, kein Rahmen.",
    "Dibuja una sola imagen del faro, sin otro texto.",
    "Disegna un solo disegno del ponte, senza cornice.",
)
CONTENT = (
    "Dessine une affiche sans texte avec un phare et trois mouettes.",
    "Draw a poster of a kite festival with no text on it.",
    "Zeichne einen Fuchs im Schnee.",
)


def answer(lead: str, *, choices: bool = False, version: int = 2) -> str:
    parts = [lead, f"![Bateau de papier]({IMAGE})"]
    if choices:
        parts.append(
            f"{text('fr', 'links')} [Image 1]({IMAGE}/1) · [Image 2]({IMAGE}/2)"
        )
        parts.append(text("fr", "hint"))
    parts.append(
        footer(ARTIFACT, version, "Bateau de papier", "bateau-de-papier.html", "fr")
    )
    return "\n\n".join(parts)


class ImageOnlyTests(unittest.TestCase):
    def test_requests_for_the_image_alone(self) -> None:
        for request in ALONE:
            with self.subTest(request=request):
                self.assertTrue(image_only(request))

    def test_text_about_the_picture_itself_is_not_the_answer(self) -> None:
        for request in CONTENT:
            with self.subTest(request=request):
                self.assertFalse(image_only(request))

    def test_a_board_request_for_one_picture_shows_it_alone(self) -> None:
        from services.orchestrator.pictures.turn import _answer
        from services.orchestrator.pictures import store

        picture_set = store.PictureSet(
            set_id="ab" * 16,
            owner="o",
            language="fr",
            title="Affiche",
            candidates=[
                store.Candidate(image_key="k" + str(n), svg="<svg/>") for n in range(3)
            ],
        )
        board = _answer("fr", "x", picture_set, [IMAGE], IMAGE + "/sheet", "")
        self.assertIn("/1)", board)
        alone = _answer("fr", "x", picture_set, [IMAGE], IMAGE, "", alone=True)
        self.assertNotIn("/images/picture/", alone)
        self.assertNotIn("Propositions", alone)
        self.assertEqual(alone.count("!["), 1)

    def test_lead_and_links_go_and_the_version_stays_reachable(self) -> None:
        shown = bare(answer(text("fr", "changed")))
        self.assertTrue(shown.startswith(f"[![Bateau de papier]({IMAGE})]("))
        self.assertNotIn("Voici", shown)
        self.assertNotIn("Télécharger", shown)
        self.assertEqual(shown.count("\n"), 0)
        # The next turn still finds the conversation's picture.
        match = HISTORY_REFERENCE_PATTERN.search(shown)
        assert match is not None
        self.assertEqual((match.group(1), match.group(2)), (ARTIFACT, "2"))

    def test_choice_links_and_hint_go(self) -> None:
        shown = bare(answer(text("fr", "cpu_intro", n=2), choices=True, version=1))
        self.assertNotIn(text("fr", "links"), shown)
        self.assertNotIn("Aperçu temporaire", shown)
        self.assertEqual(shown.count("!["), 1)

    def test_what_the_turn_says_about_the_result_is_kept(self) -> None:
        caveat = text("fr", "cpu_variation")
        shown = bare(answer(caveat))
        self.assertTrue(shown.startswith("[!["))
        self.assertTrue(shown.endswith(caveat))
        check = text("fr", "cpu_check_all", items="« deux voiles rouges »")
        shown = bare(answer(text("fr", "cpu_intro", n=2) + " " + check, choices=True))
        self.assertTrue(shown.endswith(check))
        self.assertNotIn(text("fr", "cpu_intro", n=2), shown)

    def test_other_languages(self) -> None:
        for language in ("en", "de", "es", "it"):
            shown = bare(
                "\n\n".join(
                    (
                        text(language, "single"),
                        f"![Kite]({IMAGE})",
                        footer(ARTIFACT, 3, "Kite", "kite.html", language),
                    )
                )
            )
            with self.subTest(language=language):
                self.assertEqual(shown.split("](/api/")[0], f"[![Kite]({IMAGE})")

    def test_an_answer_without_image_is_unchanged(self) -> None:
        failure = text("fr", "cpu_filtered")
        self.assertEqual(bare(failure), failure)


if __name__ == "__main__":
    unittest.main()
