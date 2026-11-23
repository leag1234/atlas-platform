"""A function word of another language inside an answer's prose (staging -177).

The French app answer read "le message de fin ... apparaît junto au bouton
« Rejouer »". Fictional answers only.
"""

from __future__ import annotations

import asyncio
import unittest

from services.orchestrator import foreign_words as fw

FIXED = (
    (
        "fr",
        "À la fin des 10 questions, le message de fin avec le score apparaît junto "
        "au bouton « Rejouer » pour relancer une partie.",
        "apparaît à côté du bouton « Rejouer »",
    ),
    (
        "fr",
        "Le bouton est gris pour la question porque il faut une réponse.",
        "question parce qu'il faut",
    ),
    (
        "fr",
        "Le score reste affiché. Además, le score est enregistré dans le "
        "navigateur pour la partie suivante.",
        "De plus, le score",
    ),
    ("fr", "Le chrono est rouge et anche le score est remis à zéro.", "et aussi le"),
    (
        "fr",
        "Le jeu reste ouvert auch après la fin, pour une revanche.",
        "ouvert aussi après",
    ),
    (
        "en",
        "The timer turns red below three seconds, and anche the score is kept.",
        "and also the score",
    ),
    (
        "de",
        "Der Zähler ist rot, und también das Spiel ist zu Ende.",
        "und auch das Spiel",
    ),
    (
        "es",
        "El contador se pone rojo y aussi el marcador se guarda para la partida.",
        "y también el marcador",
    ),
    (
        "it",
        "Il contatore diventa rosso e también il punteggio è salvato per la partita.",
        "e anche il punteggio",
    ),
)

KEPT = (
    # Quotations, code, parentheses, emphasis and links.
    ("fr", "Le titre « Porque te vas » est une chanson de la radio."),
    ("fr", "Utilise la fonction `aber()` pour la suite, dans le fichier."),
    ("fr", "Le bouton (junto) est à droite et il est rouge pour la partie."),
    ("fr", "On dit *también* dans ce cas, et le score est remis à zéro."),
    ("fr", "Voir [porque](https://example.org/porque) pour la suite du jeu."),
    ("fr", "```js\nconst aber = 1; // porque\n```\nLe code est dans le fichier."),
    # Names.
    ("fr", "Le restaurant Pero est à côté de la gare et il est ouvert."),
    ("fr", "Le mouvement Sturm und Drang est né dans les années 1770 avec Goethe."),
    ("fr", "Porque te vas est une chanson très connue dans les années 70."),
    # About language.
    ("fr", "En espagnol, on dit porque pour dire parce que, et también pour aussi."),
    ("en", "The Italian word anche is used for the score in the app."),
    # Words that are legitimate in the answer language.
    ("fr", "Le chat est perché sur le toit et il regarde les oiseaux."),
    ("it", "Il pero è un albero che non sono molti a conoscere per la sua frutta."),
    ("de", "Also ist der Zähler rot und das Spiel ist zu Ende."),
    ("en", "It gave me a sense of déjà vu when the app was reloaded with the score."),
    # A sentence of another language, not a slip.
    ("fr", "Porque no los dos, para la fiesta y para el viaje."),
    ("fr", "Weil der Zug fährt, ist das Spiel zu Ende."),
    # An answer language without a table.
    ("pt", "O contador fica vermelho e também o placar é salvo."),
)


class ForeignWordsTest(unittest.TestCase):
    def test_slips_are_replaced(self) -> None:
        for language, text, expected in FIXED:
            with self.subTest(text=text):
                out, counts = fw.clean(text, language)
                self.assertIn(expected, out)
                self.assertEqual(counts.get("replaced"), 1)

    def test_quotes_code_names_and_legitimate_words_stay(self) -> None:
        for language, text in KEPT:
            with self.subTest(text=text):
                self.assertEqual(fw.clean(text, language)[0], text)

    def test_the_persons_own_words_stay(self) -> None:
        text = "Le bouton porque est à droite et il est rouge pour la partie."
        self.assertNotEqual(fw.clean(text, "fr")[0], text)
        own = ["ajoute un bouton porque à droite"]
        self.assertEqual(fw.clean(text, "fr", own)[0], text)

    def test_no_listed_word_is_a_word_of_the_answer_language(self) -> None:
        for language, table in fw._TABLES.items():
            with self.subTest(language=language):
                self.assertFalse(set(table) & fw.LEGITIMATE[language])
        self.assertNotIn("perché", fw._TABLES["fr"])
        self.assertNotIn("pero", fw._TABLES["it"])
        self.assertNotIn("also", fw._TABLES["de"])

    def test_the_stream_reads_as_the_stored_answer(self) -> None:
        for language, text, _ in FIXED:
            for size in (1, 3, 7):
                with self.subTest(text=text, size=size):
                    # The first content of a turn goes out at once; a
                    # stream's first piece is a token or two.
                    rewriter = fw.Rewriter(language)
                    streamed = (
                        rewriter.feed(text[:1])
                        + "".join(
                            rewriter.feed(text[i : i + size])
                            for i in range(1, len(text), size)
                        )
                        + rewriter.finish()
                    )
                    self.assertEqual(streamed, fw.clean(text, language)[0])

    def test_the_first_content_of_a_turn_is_never_held(self) -> None:
        # An answer opening on a listed word is fixed in the stored answer
        # only: the stream sends its first piece at once.
        text = "Además, le score est enregistré dans le navigateur pour la partie."
        rewriter = fw.Rewriter("fr")
        self.assertEqual(rewriter.feed(text[:6]), "Además")
        self.assertTrue(fw.clean(text, "fr")[0].startswith("De plus, le score"))

    def test_text_without_a_listed_word_is_not_held(self) -> None:
        rewriter = fw.Rewriter("fr")
        # The first content, even a word that may still grow, is never held.
        self.assertEqual(rewriter.feed("Bon"), "Bon")
        self.assertEqual(rewriter.feed("jour, le score "), "jour, le score ")
        # A last word that cannot become a listed one goes out at once.
        self.assertEqual(rewriter.feed("est en haut"), "est en haut")
        # One that may ("a", as in "aussi") waits for its next character.
        self.assertEqual(rewriter.feed(" a"), " ")
        self.assertEqual(rewriter.feed("vec le chrono"), "avec le chrono")
        self.assertEqual(rewriter.finish(), "")

    def test_a_listed_word_waits_only_for_the_words_that_decide(self) -> None:
        rewriter = fw.Rewriter("fr")
        out = rewriter.feed("Le")
        out += rewriter.feed(" message de fin avec le score apparaît ")
        out += rewriter.feed("junto au bouton")
        # Held from the listed word on, not the text before it.
        self.assertEqual(out, "Le message de fin avec le score apparaît ")
        out += rewriter.feed(" pour relancer une nouvelle partie, ")
        self.assertEqual(
            out,
            "Le message de fin avec le score apparaît à côté du bouton pour "
            "relancer une nouvelle partie, ",
        )
        out += rewriter.feed("puis le chrono")
        self.assertTrue(out.endswith("puis le chrono"), out)

    def test_the_gate_keeps_other_events_in_order(self) -> None:
        sent: list[dict[str, object]] = []

        async def sink(event: dict[str, object]) -> None:
            sent.append(event)

        async def run() -> None:
            gate = fw.Gate(sink, fw.Rewriter("fr"))
            await gate({"delta": {"content": "Le"}})
            await gate({"delta": {"content": " message apparaît junto"}})
            await gate({"delta": {"content": " au bouton pour une partie."}})
            await gate({"status": "done"})

        asyncio.run(run())
        text = "".join(
            str(e["delta"]["content"])  # type: ignore[index]
            for e in sent
            if "delta" in e
        )
        self.assertEqual(text, "Le message apparaît à côté du bouton pour une partie.")
        self.assertEqual(sent[-1], {"status": "done"})


if __name__ == "__main__":
    unittest.main()
