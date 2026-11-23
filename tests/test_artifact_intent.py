"""Tests for interactive app request classification."""

import unittest

from packages.artifact_intent import app_intent


HISTORY = (
    {
        "role": "assistant",
        "content": (
            "[Open Snake](/api/atlas/artifacts/"
            "0123456789abcdef0123456789abcdef/versions/1/open)"
        ),
    },
)


class ArtifactIntentTests(unittest.TestCase):
    def test_create_in_five_languages(self) -> None:
        requests = (
            "Fais un jeu de serpent",
            "Make a timer app",
            "Erstelle ein kleines Spiel",
            "Haz una calculadora",
            "Fai un quiz",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(app_intent(text, ()), "create")

    def test_explicit_web_stack_and_named_games(self) -> None:
        for text in (
            "Write an HTML page",
            "Crée un Pong en JavaScript",
            "Build a canvas visualization",
            "Génère un tableau de bord",
        ):
            with self.subTest(text=text):
                self.assertEqual(app_intent(text, ()), "create")

    def test_required_negatives(self) -> None:
        requests = (
            "Écris un jeu en Python",
            "Programme un jeu avec pygame",
            "Comment faire un jeu HTML ?",
            "Explain how to make a web app",
            "Crée un jeu en PDF",
            "Dessine un jeu",
            "Écris un titre pour une page web",
            "Suggest a title for a landing page",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertIsNone(app_intent(text, ()))

    def test_other_excluded_requests(self) -> None:
        requests = (
            "Create a game with an image",
            "[Attachment: game.txt] Make a game",
            "Create a game in project mode",
            "Create a game and use my API key",
            "Create a game in Flask",
            "Create a game as a DOCX",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertIsNone(app_intent(text, ()))

    def test_office_formats_are_not_app_requests_in_five_languages(self) -> None:
        requests = (
            "Fais une présentation PowerPoint sur le climat",
            "Make a PowerPoint presentation about climate",
            "Erstelle eine PowerPoint-Präsentation über das Klima",
            "Haz una presentación de PowerPoint sobre el clima",
            "Fai una presentazione PowerPoint sul clima",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertIsNone(app_intent(text, ()))

    def test_explicit_web_request_wins_over_office_format(self) -> None:
        self.assertEqual(
            app_intent("Create a presentation as an HTML page", ()),
            "create",
        )

    def test_history_states(self) -> None:
        self.assertEqual(app_intent("Ajoute un bouton", HISTORY), "modify")
        self.assertEqual(app_intent("Make it green", HISTORY), "modify")
        self.assertEqual(app_intent("Change la couleur ?", HISTORY), None)
        self.assertEqual(app_intent("Fais un autre jeu", HISTORY), "available")
        self.assertEqual(app_intent("Ajoute un bouton", ()), "create")

    def test_a_request_worded_as_a_question_is_a_request(self) -> None:
        """Staging -184 (class suite): with the routing call timed out, a polite
        change request to the conversation's app was read as a question."""
        requests = (
            "Kannst du noch einbauen, dass die App vor dem Löschen eines Topfes "
            "nachfragt?",
            "Could you add a dark mode toggle to the seed tracker?",
            "Peux-tu ajouter un bouton pour vider la liste des confitures ?",
            "¿Puedes añadir un contador de días al calendario de riego?",
            "Potresti aggiungere un pulsante per azzerare il punteggio?",
            "can u rename the start button to Begin pls",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(app_intent(text, HISTORY), "modify")
                self.assertEqual(app_intent(text, ()), "create")

    def test_a_check_of_the_app_in_use_goes_to_the_app(self) -> None:
        requests = (
            "Can you double-check this in the actual app you linked? Click "
            "“Add pear” twice and tell me the total.",
            "Kannst du in der App prüfen, ob der Zähler nach dem Leeren auf 0 steht?",
            "Tu peux tester l'appli : trois clics sur « Plus » doivent donner 3.",
            "¿Puedes probar la app con dos clics en «Sumar»?",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(app_intent(text, HISTORY), "modify")

    def test_questions_about_the_app_stay_questions(self) -> None:
        requests = (
            "Kannst du mir sagen, was die App speichert?",
            "Why does the app reset when I reload?",
            "Can you explain how the app keeps its data?",
            "Pourquoi l'appli repart de zéro ?",
            "Change la couleur ?",
            "What does this app do with the names I type?",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertIsNone(app_intent(text, HISTORY))

    def test_history_ignores_non_artifact_links(self) -> None:
        history = ({"role": "assistant", "content": "See example.com"},)
        self.assertEqual(app_intent("Ajoute un bouton", history), "create")


if __name__ == "__main__":
    unittest.main()


class SvgIntentTests(unittest.TestCase):
    def test_svg_drawing_and_show_it_here_are_app_turns(self) -> None:
        from packages.artifact_intent import app_intent

        svg_answer = [
            {"role": "user", "content": "dessine un mouton en svg"},
            {
                "role": "assistant",
                "content": "```svg\n<svg viewBox='0 0 10 10'></svg>\n```",
            },
            {"role": "user", "content": "montre-la directement dans la page ?"},
        ]
        self.assertEqual(app_intent("Dessine-moi une baleine en SVG", []), "create")
        self.assertEqual(app_intent("draw a sheep as an SVG", []), "create")
        self.assertEqual(
            app_intent("montre-la directement dans la page ?", svg_answer), "create"
        )

    def test_plain_drawing_explanation_and_unrelated_show_are_not(self) -> None:
        from packages.artifact_intent import app_intent

        self.assertIsNone(app_intent("dessine un chat", []))
        self.assertIsNone(app_intent("explique le format SVG", []))
        self.assertIsNone(app_intent("montre-moi la météo", []))
        self.assertIsNone(
            app_intent("affiche-le ici", [{"role": "assistant", "content": "Bonjour"}])
        )


class GreetedRequestTests(unittest.TestCase):
    """Staging -185: with the routing call timed out, an app request worded as a
    question after a greeting, built with an infinitive and naming the app's
    title, was answered as code in the chat and no app was stored."""

    def test_a_greeted_question_that_builds_an_app_creates_it(self) -> None:
        requests = (
            "salut ! tu peux me faire une petite appli pour compter les tours "
            "de piste ? mets le titre « Tours Express » en haut stp",
            "hello, could you build a tiny quiz about rivers? put the title "
            "'River Run' at the top",
            "Hallo! Kannst du mir ein kleines Spiel bauen, mit dem Titel "
            "„Sternensammler“?",
            "hola, ¿puedes hacer una calculadora de propinas con el título "
            "«Propina Fácil»?",
            "ciao! potresti creare un timer per la pasta? titolo «Al Dente» in alto",
            "coucou, vous pouvez me coder un minuteur pour les œufs ?",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertEqual(app_intent(text, ()), "create")
                # After an app it reaches the app path too, never the chat.
                self.assertIn(app_intent(text, HISTORY), ("available", "modify"))

    def test_greeted_questions_and_title_requests_stay_chat(self) -> None:
        requests = (
            "salut ! tu peux me faire un résumé de ce texte ?",
            "bonjour, tu peux me dire ce qu'est une application web ?",
            "hi, can you tell me what a dashboard is?",
            "Propose-moi un titre pour mon jeu de cartes",
            "Give me a title for my quiz app",
            "Schlag mir einen Titel für mein Spiel vor",
            "Dame un título para mi juego",
            "Suggeriscimi un titolo per il mio quiz",
        )
        for text in requests:
            with self.subTest(text=text):
                self.assertIsNone(app_intent(text, ()))
