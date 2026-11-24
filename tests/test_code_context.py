"""The conversation's latest code reaches the step that runs it."""

import unittest

from services.orchestrator.code_context import code_hint, code_of, last_code

BARE = (
    "Peux-tu lancer ce script pour de vrai ?\n\n"
    "poids = [4, 9, 1, 6]\n"
    "print('SOMME:', sum(poids))\n"
    "print('MAX:', max(poids))"
)
FENCED = "Voici une version :\n\n```python\nx = 3\nprint(x * 2)\n```\n\nFin."


class CodeContextTests(unittest.TestCase):
    def test_bare_lines_after_a_sentence_are_code(self) -> None:
        self.assertEqual(
            code_of(BARE),
            "poids = [4, 9, 1, 6]\nprint('SOMME:', sum(poids))\nprint('MAX:', max(poids))",
        )

    def test_a_fenced_block_is_code(self) -> None:
        self.assertEqual(code_of(FENCED), "x = 3\nprint(x * 2)")

    def test_prose_and_lists_are_not_code(self) -> None:
        for text in (
            "Bonjour, peux-tu m'aider ?",
            "Voici la liste :\n- un\n- deux\n- trois",
            "Si tu veux (ou pas), dis-le moi.\nMerci beaucoup.",
        ):
            with self.subTest(text=text):
                self.assertEqual(code_of(text), "")

    def test_the_latest_block_wins_and_says_whose_it_is(self) -> None:
        found = last_code([("user", BARE), ("assistant", FENCED), ("user", "ok")])
        assert found is not None
        self.assertEqual(
            (found[0], found[1], found[2]), ("assistant", "x = 3\nprint(x * 2)", 2)
        )

    def test_the_hint_names_the_code_for_a_run_with_a_change(self) -> None:
        hint = code_hint(
            [("user", BARE), ("assistant", "Done.")],
            "Change the last value to 7 and run it again.",
        )
        self.assertIn("print('MAX:', max(poids))", hint)
        self.assertIn("Do not ask for it again", hint)

    def test_a_request_with_its_own_code_gets_no_hint(self) -> None:
        self.assertEqual(code_hint([("user", BARE)], FENCED), "")

    def test_no_code_in_the_conversation_gets_no_hint(self) -> None:
        self.assertEqual(code_hint([("user", "Hello")], "Run it."), "")


if __name__ == "__main__":
    unittest.main()
