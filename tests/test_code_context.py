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
ONE_LINE = "Kannst du diesen Python-Code ausführen? boxes = [4, 9, 2]; print(boxes[0]); print(sum(boxes))"
WHOLE_MESSAGE = (
    "Peux-tu exécuter ce code ? prix = [3, 8]; total = sum(prix); print(total)"
)
DELIVERED = "I created boxes.py and ran it. The output is:\n\n```\n4\n15\n```"


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

    def test_the_latest_block_wins(self) -> None:
        older = "```js\nconst values = [2, 5];\nconsole.log(values.length);\n```"
        found = last_code([("user", older), ("user", BARE), ("user", "ok")])
        assert found is not None
        self.assertEqual(
            (found[0], found[1], found[2]), ("user", BARE.split("\n\n", 1)[1], 2)
        )

    def test_an_assistant_output_block_is_not_the_code_to_run(self) -> None:
        output = "Here is the result:\n\n```\nMOOS-PUNKTE: 77\nGESAMT: 12\n```"
        found = last_code([("user", BARE), ("assistant", output), ("user", "ok")])
        assert found is not None
        self.assertEqual(
            (found[0], found[1], found[2]), ("user", BARE.split("\n\n", 1)[1], 3)
        )

    def test_an_assistant_written_script_is_the_code_to_run(self) -> None:
        found = last_code([("assistant", FENCED), ("user", "run it")])
        assert found is not None
        self.assertEqual(
            (found[0], found[1], found[2]), ("assistant", "x = 3\nprint(x * 2)", 2)
        )

    def test_assistant_language_tags_distinguish_code_from_output(self) -> None:
        cases = [
            ("```python\nfor value in [1, 2]:\n    print(value)\n```", True),
            ("```js\nconsole.log([1, 2].length);\n```", True),
            ("```text\nTOTAL: 3\n```", False),
            ("```console\nTOTAL: 3\n```", False),
            ("```\nTOTAL: 3\n```", False),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                found = last_code([("assistant", text), ("user", "run it")])
                self.assertEqual(found is not None, expected)

    def test_varied_person_snippets_are_reachable(self) -> None:
        cases = [
            (
                '```go\npackage main\n\nfunc main() {\n\tprintln("TOTAL: 12")\n}\n```',
                'package main\n\nfunc main() {\n\tprintln("TOTAL: 12")\n}',
            ),
            (
                '```ruby\nprix = [3, 8]\nputs "SOMME: #{prix.sum}"\n```',
                'prix = [3, 8]\nputs "SOMME: #{prix.sum}"',
            ),
            (
                '```sh\ncount=7\n echo "NOMBRE: $count"\n```',
                'count=7\n echo "NOMBRE: $count"',
            ),
        ]
        for text, expected in cases:
            with self.subTest(language=expected.splitlines()[0]):
                found = last_code([("user", text), ("user", "run it again")])
                assert found is not None
                self.assertEqual(found[1], expected)

    def test_the_hint_names_the_code_for_a_run_with_a_change(self) -> None:
        hint = code_hint(
            [("user", BARE), ("assistant", "Done.")],
            "Change the last value to 7 and run it again.",
        )
        self.assertIn("print('MAX:', max(poids))", hint)
        self.assertIn("Do not ask for it again", hint)

    def test_an_unfenced_one_line_snippet_is_not_reconstructed(self) -> None:
        hint = code_hint([("user", ONE_LINE)], "Run the code unchanged again.")
        self.assertIn("boxes = [4, 9, 2]", hint)
        self.assertIn("print(boxes[0])", hint)
        self.assertIn("print(sum(boxes))", hint)
        self.assertIn("Kannst du diesen Python-Code ausführen?", hint)

    def test_an_unfenced_multiline_snippet_hands_the_whole_message(self) -> None:
        message = (
            "¿Puedes ejecutar esto? valores = [1, 2]\n"
            "print(valores[0])\n"
            "print(sum(valores))"
        )
        hint = code_hint([("user", message)], "Ejecútalo otra vez.")
        self.assertIn("¿Puedes ejecutar esto?", hint)
        self.assertIn("valores = [1, 2]", hint)
        self.assertIn("print(sum(valores))", hint)

    def test_a_delivered_code_file_is_named_first(self) -> None:
        hint = code_hint(
            [("user", ONE_LINE), ("assistant", DELIVERED)],
            "Run the code unchanged again in the Python tool.",
        )
        self.assertIn("boxes.py", hint)
        self.assertNotIn("boxes = [4, 9, 2]", hint)

    def test_a_fenced_hint_is_unchanged(self) -> None:
        hint = code_hint([("user", FENCED)], "Run it again.")
        self.assertIn("x = 3\nprint(x * 2)", hint)
        self.assertNotIn("Voici une version", hint)

    def test_a_request_with_its_own_code_gets_no_hint(self) -> None:
        self.assertEqual(code_hint([("user", BARE)], FENCED), "")

    def test_no_code_in_the_conversation_gets_no_hint(self) -> None:
        self.assertEqual(code_hint([("user", "Hello")], "Run it."), "")


if __name__ == "__main__":
    unittest.main()
