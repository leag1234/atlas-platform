"""A removed source never leaves the words that introduced it (staging -173)."""

import time
import unittest

from services.orchestrator.dangling_words import repair
from services.orchestrator.source_truth import (
    clean_citation_residue,
    person_citations,
    without_unretrieved,
)

GONE = "https://docs.example/release-notes"
ALSO_GONE = "https://agency.example/page"
KEPT = "https://weather.example/today"


def rendered(text: str, retrieved: frozenset[str] = frozenset()) -> str:
    """The answer after the D7 guard and the residue cleanup, in stream order."""
    return clean_citation_residue(without_unretrieved(text, set(retrieved)))


class RealShapeTests(unittest.TestCase):
    def test_staging_173_sentence_reads_whole(self) -> None:
        text = (
            f"D'après mes connaissances (à vérifier sur [docs.example]({GONE}), "
            "non consulté ici) : la dernière version stable de Zephyr est la 4.2."
        )
        self.assertEqual(
            rendered(text),
            "D'après mes connaissances (à vérifier, non consulté ici) : "
            "la dernière version stable de Zephyr est la 4.2.",
        )

    def test_each_language_drops_the_orphaned_preposition(self) -> None:
        link = f"[docs]({GONE})"
        cases = [
            (
                f"From memory (to be checked on {link}, not consulted here): 4.2.",
                "From memory (to be checked, not consulted here): 4.2.",
            ),
            (
                f"Plus de détails sur le site de {link}.",
                "Plus de détails sur le site.",
            ),
            (
                f"Aus dem Gedächtnis (zu prüfen auf {link}, hier nicht abgerufen).",
                "Aus dem Gedächtnis (zu prüfen, hier nicht abgerufen).",
            ),
            (
                f"De memoria (por verificar en {link}, no consultado aquí).",
                "De memoria (por verificar, no consultado aquí).",
            ),
            (
                f"A memoria (da verificare su {link}, non consultato qui).",
                "A memoria (da verificare, non consultato qui).",
            ),
            (
                f"Mehr dazu auf der Seite {link} oder bei [Amt]({ALSO_GONE}).",
                "Mehr dazu auf der Seite.",
            ),
            (
                f"Dettagli su {link} e presso [ente]({ALSO_GONE}).",
                "Dettagli.",
            ),
            (
                f"Version 4.2 (see {link}, not consulted).",
                "Version 4.2 (not consulted).",
            ),
            (
                f"Voir {link} pour la suite.",
                "Pour la suite.",
            ),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(rendered(text), expected)

    def test_a_clause_left_empty_goes_with_its_capital(self) -> None:
        self.assertEqual(
            rendered(f"According to [the notes]({GONE}), it ships in May."),
            "It ships in May.",
        )

    def test_bare_url_reads_as_a_neutral_phrase(self) -> None:
        self.assertEqual(
            rendered(f"Version 4.2 (à vérifier sur {GONE}, non consulté ici)."),
            "Version 4.2 (à vérifier sur unverified source, non consulté ici).",
        )

    def test_person_citations_keep_the_sentence_whole(self) -> None:
        text = f"Ada Example a fondé la société (selon [un article]({GONE}))."
        self.assertEqual(person_citations(text, set()), "")
        kept = (
            f"Ada Example vit à Lyon, d'après [la fiche]({KEPT}). Rien sur [x]({GONE})."
        )
        self.assertEqual(
            person_citations(kept, {KEPT}),
            f"Ada Example vit à Lyon, d'après [la fiche]({KEPT}).",
        )


class UnchangedTests(unittest.TestCase):
    def test_prepositions_that_belong_to_the_text_stay(self) -> None:
        cases = [
            "What are you waiting for? See the 4.2 notes.",
            "And so on. Comptez sur, disons, deux heures.",
            "Das weiß ich nicht, frag die. Es ist das.",
            "Voy en coche a Madrid, como siempre.",
            "Vado su e giù dalle scale.",
            "Donne-le. Call print() on it.",
            "A, B and C.",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(rendered(text + f" [x]({GONE})").rstrip(), text)

    def test_a_retrieved_citation_changes_nothing(self) -> None:
        text = f"Prévisions à vérifier sur [la page]({KEPT}), consultée ici."
        self.assertEqual(rendered(text, frozenset({KEPT})), text)

    def test_preposition_followed_by_a_word_stays(self) -> None:
        before = f"À vérifier sur [docs]({GONE}) et sur le site officiel."
        self.assertEqual(
            repair(before, "À vérifier sur  et sur le site officiel."),
            "À vérifier sur le site officiel.",
        )
        self.assertEqual(
            repair("Look on the map at noon.", "Look on the map at noon."),
            "Look on the map at noon.",
        )

    def test_code_is_never_repaired(self) -> None:
        before = f"Run it:\n```\nsee on ,\n```\nDetails on [docs]({GONE})."
        after = "Run it:\n```\nsee on ,\n```\nDetails on ."
        self.assertEqual(repair(before, after), "Run it:\n```\nsee on ,\n```\nDetails.")
        inline = repair(f"Use `see on` on [x]({GONE}).", "Use `see on` on .")
        self.assertEqual(inline, "Use `see on`.")

    def test_a_call_keeps_its_brackets(self) -> None:
        self.assertEqual(
            repair(f"Call print() on [x]({GONE}) now.", "Call print() on  now."),
            "Call print() on now.",
        )

    def test_spaces_already_in_the_text_stay(self) -> None:
        before = "Line with two  spaces.  Next [a](https://x.example/a)."
        self.assertEqual(
            repair(before, "Line with two  spaces.  Next ."),
            "Line with two  spaces.  Next.",
        )
        self.assertEqual(
            repair(
                "Hard break  \nnext [a](https://x.example/a)", "Hard break  \nnext "
            ),
            "Hard break  \nnext ",
        )


class LinearTimeTests(unittest.TestCase):
    def test_long_runs_stay_fast(self) -> None:
        cases = [
            ("x", "on " * 20_000 + "."),
            ("x", " " * 100_000 + "x"),
            ("x", "(" * 50_000),
            ("a on b. " * 5_000, "a on. " * 20_000),
            ("a on b. " * 5_000, "a on  and " * 20_000),
            ("x", "`a` " * 30_000),
        ]
        for original, edited in cases:
            started = time.monotonic()
            repair(original, edited)
            self.assertLess(time.monotonic() - started, 2.0)


class SeparatorRunTests(unittest.TestCase):
    """Staging -176: "« ... » — :" left before a link once its label went."""

    def test_staging_176_dash_and_colon(self) -> None:
        text = (
            "annonce du 22 octobre 2026 « Zephyrine 4.14.8 est disponible » — "
            f"[Blog Zephyrine]({GONE}) :\n[1]({KEPT})\n\nSuite."
        )
        self.assertEqual(
            rendered(text, frozenset({KEPT})),
            "annonce du 22 octobre 2026 « Zephyrine 4.14.8 est disponible » :"
            f"\n[1]({KEPT})\n\nSuite.",
        )

    def test_five_languages(self) -> None:
        cases = {
            "en": (
                f"See the post — [the blog]({GONE}): [1]({KEPT}) for details.",
                f"See the post: [1]({KEPT}) for details.",
            ),
            "fr": (
                f"Voici l'annonce – [le blog]({GONE}) ; la suite vient.",
                "Voici l'annonce ; la suite vient.",
            ),
            "de": (
                f"Die Ankündigung — [Blog]({GONE}), dann folgt mehr.",
                "Die Ankündigung, dann folgt mehr.",
            ),
            "es": (
                f"El anuncio — [blog]({GONE}) — [1]({KEPT}) sigue.",
                f"El anuncio — [1]({KEPT}) sigue.",
            ),
            "it": (
                f"L'annuncio: [blog]({GONE}), [1]({KEPT}) segue.",
                f"L'annuncio: [1]({KEPT}) segue.",
            ),
        }
        for language, (text, expected) in cases.items():
            with self.subTest(language=language):
                self.assertEqual(rendered(text, frozenset({KEPT})), expected)

    def test_runs_written_on_purpose_stay(self) -> None:
        original = f"Le meilleur — de loin —, et [ici]({GONE}) fin. Il est 12:30 :-)"
        self.assertEqual(
            repair(original, "Le meilleur — de loin —, et  fin. Il est 12:30 :-)"),
            "Le meilleur — de loin —, et fin. Il est 12:30 :-)",
        )

    def test_code_is_never_touched(self) -> None:
        original = f"Run `a — : b` — [doc]({GONE}): done"
        self.assertEqual(
            repair(original, "Run `a — : b` — : done"), "Run `a — : b`: done"
        )


if __name__ == "__main__":
    unittest.main()
