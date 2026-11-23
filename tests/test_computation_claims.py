"""A claim of a deed (counted, calculated, checked) needs the deed (C2).

Fictional answers only.
"""

import asyncio
import unittest
from unittest.mock import patch

from services.orchestrator import chat_pipeline, computation_claims
from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.computation_claims import Deeds, note, unbacked
from services.orchestrator.interactions import Interaction


class ClaimTests(unittest.TestCase):
    def test_computation_claims_without_a_computation_tool(self) -> None:
        for answer in (
            # The staging -165 answer, with fictional numbers.
            "Je n'ai pas d'outil terminal, mais le texte du PDF m'a été fourni, "
            "donc j'ai compté directement dessus : 44 occurrences.",
            "Occurrences : 39 (comptage insensible à la casse sur le texte extrait).",
            "Il y a 12 mentions, comptées à la main.",
            "J'ai bien recompté : 27 lignes.",
            "I counted 18 occurrences of the word in the text.",
            "There are 31 instances, counted manually across the pages.",
            "By my count, the report has 14 tables.",
            "Ich habe die Wörter gezählt: 120.",
            "He contado 9 apariciones.",
            "Ho contato 7 volte la parola.",
        ):
            with self.subTest(answer=answer):
                verdict = unbacked(answer, Deeds(provided=True))
                self.assertTrue(verdict.computation)

    def test_a_computation_tool_backs_the_claim(self) -> None:
        answer = "J'ai compté 85 occurrences."
        for tool in ("document_statistics", "calculator", "terminal_command"):
            with self.subTest(tool=tool):
                self.assertFalse(unbacked(answer, Deeds(tools={tool})))
        self.assertFalse(unbacked(answer, Deeds(terminal_commands=1)))
        # A search is no computation.
        self.assertTrue(unbacked(answer, Deeds(tools={"web_search"})).computation)

    def test_check_claims(self) -> None:
        answer = "J'ai vérifié : les drapeaux fonctionnent pour les 20 pays."
        self.assertTrue(unbacked(answer, Deeds()).check)
        self.assertTrue(unbacked("I tested it, all 20 flags work.", Deeds()).check)
        # A tool, or the document the person gave, is the check.
        self.assertFalse(unbacked(answer, Deeds(tools={"web_fetch"})))
        self.assertFalse(unbacked(answer, Deeds(provided=True)))

    def test_not_claims(self) -> None:
        for answer in (
            "Je n'ai pas compté les occurrences : je ne peux pas le faire ici.",
            "I have not counted them; this is an estimate.",
            "J'ai un compte chez cette banque depuis 2019.",
            "Le comptage des voix est en cours dans 12 bureaux.",
            "With 98% of votes counted, the result is 52%.",
            "Excel calcule directement la somme de la colonne.",
            "Le document compte 34 pages.",
            "Vous avez écrit « j'ai compté 40 mots », mais le texte en a plus.",
            "Il faut tenir compte de 3 annexes.",
        ):
            with self.subTest(answer=answer):
                self.assertFalse(unbacked(answer, Deeds()))

    def test_the_persons_own_words_are_not_claims(self) -> None:
        asked = "J'ai compté 40 mots, tu confirmes ?"
        answer = "Tu as écrit : J'ai compté 40 mots, tu confirmes ? Le texte en a 42."
        self.assertFalse(unbacked(answer, Deeds(), [asked]))

    def test_notes_say_the_numbers_were_not_computed(self) -> None:
        verdict = unbacked("J'ai compté 44 occurrences.", Deeds(provided=True))
        self.assertIn("pas un comptage", note(verdict, "fr"))
        self.assertIn("not a verified count", note(verdict, "en"))
        for language in ("de", "es", "it", "xx"):
            self.assertTrue(note(verdict, language))
        both = unbacked("I counted 12 and I tested the app.", Deeds())
        self.assertEqual(len(note(both, "en").split("\n\n")), 2)

    def test_record_is_bounded_to_the_turn(self) -> None:
        computation_claims.record(("calculator",))  # no turn: no effect
        deeds = Deeds()
        token = computation_claims.current.set(deeds)
        try:
            computation_claims.record(("document_statistics",))
            computation_claims.record_provided()
        finally:
            computation_claims.current.reset(token)
        self.assertEqual(deeds.tools, {"document_statistics"})
        self.assertTrue(deeds.provided and deeds.computed())


class PipelineTests(unittest.TestCase):
    def run_answer(self, answer: str, tools: tuple[str, ...] = ()) -> Interaction:
        async def answered(request: ChatRequest, item: Interaction) -> None:
            computation_claims.record(tools)
            item.reponse, item.state = answer, "done"

        request = ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": "Combien de mots ?"}]}
        )
        item = Interaction()
        with patch.object(chat_pipeline, "_process_request", answered):
            asyncio.run(chat_pipeline.process(request, item))
        return item

    def test_unbacked_count_gets_the_note(self) -> None:
        item = self.run_answer("J'ai compté 42 mots dans ce passage.")
        self.assertTrue(item.reponse.startswith("J'ai compté 42 mots"))
        self.assertIn(computation_claims.NOTES["fr"][0], item.reponse)
        step = next(s for s in item.steps if s["phase"] == "claim_check")
        self.assertEqual(step["computation_claims"], 1)

    def test_count_made_by_code_corrects_a_wrong_number(self) -> None:
        from services.orchestrator import document_stats

        text = document_stats.Text("note.txt", "atelier " * 30 + "fin " * 10)

        async def answered(request: ChatRequest, item: Interaction) -> None:
            document_stats.hold(text)
            document_stats.facts(request.user_text, document_stats.held_texts())
            item.reponse, item.state = "Le mot « atelier » revient 12 fois.", "done"

        request = ChatRequest.model_validate(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "Combien de fois le mot « atelier » apparaît ?",
                    }
                ]
            }
        )
        item = Interaction()
        with patch.object(chat_pipeline, "_process_request", answered):
            asyncio.run(chat_pipeline.process(request, item))
        self.assertIn("« atelier » : 30 occurrences", item.reponse)
        # Code counted: the deed is there, no estimate note.
        self.assertNotIn(computation_claims.NOTES["fr"][0], item.reponse)

    def test_backed_count_is_left_as_is(self) -> None:
        answer = "J'ai compté 42 mots dans ce passage."
        item = self.run_answer(answer, ("document_statistics",))
        self.assertEqual(item.reponse, answer)


if __name__ == "__main__":
    unittest.main()
