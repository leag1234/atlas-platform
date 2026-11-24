"""The constraint pipeline never makes a reply worse (docs/33, section 4 bis).

- a count or form about one PART of the reply is never checked on the whole
  reply: per part when the parts are delimited, otherwise undecided;
- a repair keeps the subject, the content and every delivered item, never
  copies the person's instructions, is never empty, and replaces the reply
  only when it passes strictly more constraints and loses none;
- a gap is said only for what code decided failed, naming what is missing,
  impersonally (right in either register);
- a reply code wrote for a stopped turn is neither checked nor rewritten;
- one log line per turn, counts only.

Fictional content in English, French, German, Spanish and Italian.
"""

import asyncio
from collections.abc import Coroutine
import json
import os
from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock

from services.orchestrator.constraints import checks, pipeline
from services.orchestrator.constraints.model import Constraint, part_of
from services.orchestrator.constraints.views import answer_view

LINK = "[Open buoy-counter (version 1)](/api/atlas/artifacts/b7/versions/1/open)"


def run(coroutine: Coroutine[Any, Any, pipeline.Outcome]) -> pipeline.Outcome:
    return asyncio.run(coroutine)


class Gateway:
    """Answers each call kind (reading, verifier, repair) from a script."""

    MARKERS = {
        "read": "FORM and exact CONTENT",
        "verify": "You check a reply",
        "repair": "You correct a reply",
    }

    def __init__(self, **replies: Any) -> None:
        self.replies = replies
        self.payloads: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        system = payload["messages"][0]["content"]
        for name, marker in self.MARKERS.items():
            if marker in system:
                self.payloads.append((name, payload))
                reply = self.replies.get(name)
                if reply is None:
                    return {"text": "{}"}
                text = reply if isinstance(reply, str) else json.dumps(reply)
                return {"text": text, "cost_eur": "0.0003"}
        return {"text": "{}"}

    def sent(self, name: str) -> dict[str, Any]:
        payload = next(p for kind, p in self.payloads if kind == name)
        return dict(json.loads(payload["messages"][1]["content"]))


def items(count: int, quote: str) -> dict[str, Any]:
    return {
        "kind": "items",
        "scope": "answer",
        "count": count,
        "relation": "exact",
        "quote": quote,
        "statement": quote,
    }


class ModelProfile(unittest.TestCase):
    def setUp(self) -> None:
        import packages.profiles as profiles

        self._picture_profile = profiles.picture_profile
        profiles.picture_profile = lambda role: "fast-check"

    def tearDown(self) -> None:
        import packages.profiles as profiles

        profiles.picture_profile = self._picture_profile


class PartScope(ModelProfile):
    PERSON_EN = (
        "Before you book anything, sketch two picnic menus for the lighthouse "
        "crew: one with 4 dishes and one with 6. Start your reply with "
        '"Two menus".'
    )

    def outcome(self, person: str, reply: str, read: list[dict[str, Any]]) -> Any:
        return run(
            pipeline.enforce(
                [("user", person)],
                reply,
                language="en",
                complete=Gateway(read={"constraints": read}),
                run_mode="full",
            )
        )

    def menus(self, *sizes: int) -> str:
        parts = ["Two menus"]
        for number, size in enumerate(sizes, start=1):
            dishes = "\n".join(f"- dish {number}.{n}" for n in range(1, size + 1))
            parts.append(f"**Menu {number}**\n{dishes}")
        return "\n\n".join(parts)

    def test_counts_of_two_parts_are_checked_per_part(self) -> None:
        read = [items(4, "one with 4 dishes"), items(6, "one with 6")]
        outcome = self.outcome(self.PERSON_EN, self.menus(6, 4), read)
        parts = [c for c in outcome.constraints if c.kind == "items"]
        self.assertEqual({c.part for c in parts}, {"one"})
        verdicts = {r.id: r.passed for r in outcome.results}
        self.assertTrue(all(verdicts[c.id] for c in parts))
        self.assertEqual(outcome.note, "")

    def test_a_part_no_list_meets_is_a_certain_gap(self) -> None:
        read = [items(4, "one with 4 dishes"), items(6, "one with 6")]
        outcome = self.outcome(self.PERSON_EN, self.menus(4, 5), read)
        self.assertEqual(
            outcome.note,
            "This reply does not yet meet the request: no list of 6 items.",
        )

    def test_parts_not_delimited_are_undecided_never_a_gap(self) -> None:
        read = [items(4, "one with 4 dishes"), items(6, "one with 6")]
        merged_list = "Two menus\n\n" + "\n".join(f"- dish {n}" for n in range(10))
        outcome = self.outcome(self.PERSON_EN, merged_list, read)
        self.assertEqual(outcome.note, "")
        self.assertEqual(outcome.unmet(), [])

    def test_more_part_counts_than_parts_are_undecided(self) -> None:
        person = (
            "Proposez-moi trois menus de fête pour le club de voile : un de 2 plats, "
            "un de 3 et un de 4."
        )
        read = [
            items(2, "un de 2 plats"),
            items(3, "un de 3"),
            items(4, "un de 4"),
        ]
        reply = (
            "**Menu A**\n- soupe\n- tarte\n\n**Menu B**\n- salade\n- poisson\n- flan"
        )
        outcome = self.outcome(person, reply, read)
        self.assertTrue(all(r.passed is None for r in outcome.results))
        self.assertEqual(outcome.note, "")

    def test_each_part_is_checked_on_every_introduced_part(self) -> None:
        person = (
            "Gliedere die Antwort in zwei Abschnitte, Anreise und Ausrüstung, mit "
            "jeweils drei Stichpunkten."
        )
        read = [items(3, "mit jeweils drei Stichpunkten")]
        good = (
            "## Anreise\n- Zug\n- Rad\n- Bus\n\n## Ausrüstung\n- Zelt\n- Lampe\n- Seil"
        )
        short = "## Anreise\n- Zug\n- Rad\n- Bus\n\n## Ausrüstung\n- Zelt\n- Lampe"
        passed = run(
            pipeline.enforce(
                [("user", person)],
                good,
                language="de",
                complete=Gateway(read={"constraints": read}),
                run_mode="full",
            )
        )
        self.assertEqual([c.part for c in passed.constraints], ["each"])
        self.assertEqual(passed.note, "")
        failed = run(
            pipeline.enforce(
                [("user", person)],
                short,
                language="de",
                complete=Gateway(read={"constraints": read}),
                run_mode="full",
            )
        )
        self.assertIn("Liste 2: 2 Punkte, verlangt: 3", failed.note)

    def test_counts_that_cannot_all_hold_on_the_whole_reply(self) -> None:
        person = "Dame exactamente tres ideas para decorar el faro y dos ejemplos de colores."
        read = [items(3, "tres ideas"), items(2, "dos ejemplos")]
        reply = "- guirnaldas\n- farolillos\n- banderines\n- azul\n- blanco"
        outcome = self.outcome(person, reply, read)
        self.assertEqual({c.part for c in outcome.constraints}, {"one"})
        self.assertEqual(outcome.note, "")

    def test_one_count_read_twice_stays_on_the_whole_reply(self) -> None:
        person = "Réponds en deux phrases : pourquoi les bouées sont-elles rouges ?"
        read = [
            {
                "kind": "sentences",
                "scope": "answer",
                "count": 2,
                "quote": "en deux phrases",
                "statement": "deux phrases",
            }
        ]
        reply = "Pour être vues. Le rouge tranche. La mer est bleue. Voilà."
        outcome = run(
            pipeline.enforce(
                [("user", person)],
                reply,
                language="fr",
                complete=Gateway(read={"constraints": read}),
                run_mode="full",
            )
        )
        self.assertEqual([c.part for c in outcome.constraints], [""])
        self.assertIn("phrases : 4, demandé : 2", outcome.note)

    def test_an_offered_sentence_is_a_part_not_the_reply_length(self) -> None:
        person = "¿Me propones una frase de cierre para el boletín del club de remo?"
        reply = (
            "«Nos vemos en el agua el sábado.»\n\nEs breve y deja la cita clara. "
            "Si prefieres otro tono, dímelo."
        )
        outcome = run(
            pipeline.enforce([("user", person)], reply, language="es", run_mode="code")
        )
        self.assertEqual([c.part for c in outcome.constraints], ["one"])
        self.assertEqual(outcome.note, "")
        whole = run(
            pipeline.enforce(
                [("user", "Responde en una frase: ¿cuándo sale el bote?")],
                "Sale a mediodía. Llega tarde.",
                language="es",
                run_mode="code",
            )
        )
        self.assertIn("frases: 2, se pidió: 1", whole.note)

    def test_words_about_parts(self) -> None:
        self.assertEqual(part_of("one with 4 dishes and one with 6"), "one")
        self.assertEqual(part_of("l'autre en cinq points"), "one")
        self.assertEqual(part_of("zwei Listen mit Ideen"), "one")
        self.assertEqual(part_of("tres puntos por sección"), "each")
        self.assertEqual(part_of("tre punti in ogni sezione"), "each")
        self.assertEqual(part_of("Toujours en une phrase, à chaque réponse"), "")
        self.assertEqual(part_of("in exactly three bullet points"), "")
        self.assertEqual(part_of("entre trois et cinq points"), "")
        self.assertEqual(part_of("Suggest one sentence for the invitation card"), "one")
        self.assertEqual(part_of("Schlag mir einen Satz für die Karte vor"), "one")
        self.assertEqual(part_of("Answer in one sentence for now"), "")


class Repairs(ModelProfile):
    PERSON = (
        "Name three tide-pool creatures as bullet points, then hold on until I "
        "pick one."
    )
    NUMBERED = "1. Sea star\n2. Hermit crab\n3. Anemone"
    READ = {
        "constraints": [
            {
                "kind": "list_style",
                "scope": "answer",
                "style": "bullets",
                "quote": "as bullet points",
                "statement": "bullet points",
            }
        ]
    }

    def repaired(self, rewrite: str, person: str = "", reply: str = "") -> Any:
        gateway = Gateway(read=self.READ, repair={"answer": rewrite})
        outcome = run(
            pipeline.enforce(
                [
                    ("user", "Hi."),
                    ("assistant", "Hello."),
                    ("user", person or self.PERSON),
                ],
                reply or self.NUMBERED,
                language="en",
                complete=gateway,
                run_mode="full",
            )
        )
        return outcome, gateway

    def test_repair_gets_the_conversation_and_is_kept_when_better(self) -> None:
        outcome, gateway = self.repaired("- Sea star\n- Hermit crab\n- Anemone")
        sent = gateway.sent("repair")
        self.assertEqual(
            [m["role"] for m in sent["conversation"]], ["user", "assistant", "user"]
        )
        self.assertIn("tide-pool", sent["conversation"][-1]["text"])
        self.assertEqual(outcome.repaired, "model")
        self.assertEqual(outcome.note, "")

    def test_repair_copying_the_instruction_is_dropped(self) -> None:
        outcome, _ = self.repaired(
            "- Sea star\n- Hermit crab\n- Anemone\n\nHold on until I pick one."
        )
        self.assertEqual(outcome.answer, self.NUMBERED)
        self.assertIn("model:parrots_request", outcome.dropped)

    def test_repair_with_other_content_is_dropped(self) -> None:
        outcome, _ = self.repaired("- Introduction\n- Development\n- Conclusion")
        self.assertEqual(outcome.answer, self.NUMBERED)
        self.assertIn("model:new_content", outcome.dropped)
        # What code decided is still said, naming what is missing.
        self.assertIn("the list is not in the form asked", outcome.note)

    def test_repair_losing_a_constraint_that_passed_is_dropped(self) -> None:
        person = (
            "Empieza tu respuesta con «Buenas noticias» y contesta en una frase como "
            "máximo: ¿a qué hora sale el ferry?"
        )
        reply = "Buenas noticias: el ferry sale a las 9. Llega a las 11."
        outcome = run(
            pipeline.enforce(
                [("user", person)],
                reply,
                language="es",
                complete=Gateway(
                    repair={"answer": "El ferry sale a las 9 y llega a las 11."}
                ),
                run_mode="full",
            )
        )
        self.assertEqual(outcome.answer, reply)
        self.assertIn("model:no_gain", outcome.dropped)
        self.assertIn("frases: 2, se pidió: como máximo 1", outcome.note)

    def test_refusals(self) -> None:
        person = ["Bau die Seite und schick den Link."]
        self.assertEqual(pipeline.refusal("Fertig.", "  ", person, "", []), "empty")
        self.assertEqual(
            pipeline.refusal("Fertig: " + LINK, "Fertig.", person, "", []),
            "lost_delivered",
        )
        code = "Run:\n\n```sh\nmake buoys\n```"
        self.assertEqual(
            pipeline.refusal(code, "Run: make buoys", person, "", []),
            "lost_delivered",
        )
        self.assertEqual(
            pipeline.refusal("Fertig: " + LINK, LINK + "\n\nFertig.", person, "", []),
            "",
        )


class Notes(ModelProfile):
    def test_a_verifier_verdict_is_never_said_as_a_gap(self) -> None:
        reply = "The ferry leaves at nine. Bring a coat."
        gateway = Gateway(
            read={
                "constraints": [
                    {
                        "kind": "judge",
                        "scope": "answer",
                        "quote": "End with a friendly closing line",
                        "statement": "a friendly closing line",
                    }
                ]
            },
            verify={
                "results": [{"id": "K1", "met": False, "evidence": "Bring a coat."}]
            },
            repair="not json",
        )
        outcome = run(
            pipeline.enforce(
                [
                    (
                        "user",
                        "When does the ferry leave? End with a friendly closing line.",
                    )
                ],
                reply,
                language="en",
                complete=gateway,
                run_mode="full",
            )
        )
        self.assertEqual([r.passed for r in outcome.results], [False])
        self.assertEqual(outcome.unmet(), [])
        self.assertEqual(outcome.note, "")
        self.assertEqual(outcome.answer, reply)

    def test_note_is_impersonal_and_names_what_is_missing(self) -> None:
        cases = [
            (
                "de",
                "Beginne deine Antwort mit „Kurz gesagt“: wie weit ist die Insel?",
                "Die Insel liegt vier Kilometer entfernt.",
                "Diese Antwort erfüllt die Anfrage noch nicht: sie beginnt nicht mit "
                "„Kurz gesagt“.",
            ),
            (
                "fr",
                "Réponds en une phrase : combien de marches a le phare ?",
                "Il en a cent. Elles sont en pierre. La vue est belle.",
                "Cette réponse ne respecte pas encore la demande : phrases : 3, "
                "demandé : 1.",
            ),
            (
                "it",
                "Rispondi in una frase: di che colore è la barca?",
                "È gialla. Ha una vela bianca.",
                "Questa risposta non rispetta ancora la richiesta: frasi: 2, "
                "richiesto: 1.",
            ),
        ]
        for language, person, reply, expected in cases:
            with self.subTest(language=language):
                outcome = run(
                    pipeline.enforce(
                        [("user", person)], reply, language=language, run_mode="code"
                    )
                )
                self.assertEqual(outcome.note, expected)
                for address in (" Sie ", " du ", " vous ", " tu ", " usted "):
                    self.assertNotIn(address, f" {outcome.note} ")

    def test_line_written_inside_a_paragraph_is_moved_under_the_link(self) -> None:
        person = "Bau mir die Seite und schreib unter den Link „Geprüft:“ mit Ergebnis."
        reply = "Die Seite zählt Bojen am Steg. Geprüft: nein.\n\n" + LINK
        outcome = run(
            pipeline.enforce([("user", person)], reply, language="de", run_mode="code")
        )
        self.assertEqual(outcome.repaired, "code")
        self.assertTrue(outcome.answer.rstrip().endswith("Geprüft: nein."))
        self.assertIn(LINK, outcome.answer)
        self.assertEqual(outcome.note, "")


class Turn(unittest.TestCase):
    def test_reply_of_a_stopped_turn_is_left_alone(self) -> None:
        explanation = (
            "The model service refused the request (provider_error, HTTP 429). "
            "Nothing was created; send the message again to retry."
        )
        outcome = run(
            pipeline.enforce(
                [("user", 'Start your reply with "Three routes" and list them.')],
                explanation,
                language="en",
                run_mode="code",
                stopped=True,
            )
        )
        self.assertEqual(outcome.answer, explanation)
        self.assertEqual(outcome.skipped, "stopped_turn")
        self.assertEqual((outcome.note, outcome.calls), ("", 0))

    def test_log_line_has_counts_only(self) -> None:
        outcome = run(
            pipeline.enforce(
                [
                    (
                        "user",
                        "Réponds en une phrase : quand rentre le goéland Zéphyrin ?",
                    )
                ],
                "Sur le toit. Toujours.",
                language="fr",
                run_mode="code",
            )
        )
        line = json.loads(outcome.log_line())
        self.assertEqual(line["event"], "answer_constraints")
        self.assertEqual(line["kinds"], {"sentences": 1})
        self.assertEqual((line["failed_certain"], line["undecided"]), (1, 0))
        self.assertIn("repair_dropped", line)
        self.assertNotIn("Zéphyrin", outcome.log_line())
        self.assertNotIn("toit", outcome.log_line())


def _request(person: str) -> Any:
    return SimpleNamespace(
        atlas_ui=False,
        conversation_messages=[SimpleNamespace(role="user", text=person)],
        ui_locale="fr",
        answer_language="fr",
        model="writer",
    )


def _item(reply: str) -> Any:
    return SimpleNamespace(
        reponse=reply, files=[], artifacts=[], images=[], cout_eur=0.0, steps=[]
    )


class Hook(unittest.TestCase):
    PERSON = "Commencez votre réponse par « Trois pistes » et proposez des balades."

    def setUp(self) -> None:
        self._mode = os.environ.get("ATLAS_ANSWER_CONSTRAINTS")
        os.environ["ATLAS_ANSWER_CONSTRAINTS"] = "code"

    def tearDown(self) -> None:
        if self._mode is None:
            os.environ.pop("ATLAS_ANSWER_CONSTRAINTS", None)
        else:
            os.environ["ATLAS_ANSWER_CONSTRAINTS"] = self._mode

    def hook(self, item: Any, plan: Any = None) -> None:
        from services.orchestrator import chat_pipeline, work_plan

        token = work_plan.current.set(plan)
        try:
            asyncio.run(
                chat_pipeline._answer_constraints(_request(self.PERSON), item, None)
            )
        finally:
            work_plan.current.reset(token)

    def test_explanation_of_a_stopped_plan_is_not_rewritten(self) -> None:
        from services.orchestrator import work_plan

        plan = work_plan.ChatPlan(language="fr", profile="writer", route="work-file")
        plan.steps = [mock.Mock()]
        plan.finished, plan.success = True, False
        explanation = "Le service de modèle a refusé la demande (HTTP 429)."
        item = _item(explanation)
        self.hook(item, plan)
        self.assertEqual(item.reponse, explanation)
        self.assertEqual(item.steps, [])

    def test_a_chat_reply_is_checked(self) -> None:
        item = _item("Voici des balades le long de la côte.")
        self.hook(item)
        self.assertIn("elle ne commence pas par « Trois pistes »", item.reponse)

    def test_an_empty_result_never_replaces_the_reply(self) -> None:
        from services.orchestrator import chat_pipeline

        empty = pipeline.Outcome(
            "",
            constraints=[
                Constraint("starts_with", "answer", "q", 1, text="X", id="K1")
            ],
        )
        item = _item("Trois pistes : la dune, le port, le bois.")
        with mock.patch.object(pipeline, "enforce", mock.AsyncMock(return_value=empty)):
            asyncio.run(
                chat_pipeline._answer_constraints(_request(self.PERSON), item, None)
            )
        self.assertEqual(item.reponse, "Trois pistes : la dune, le port, le bois.")


class LayoutParts(unittest.TestCase):
    def test_counts_that_cannot_all_hold_go_to_the_judge(self) -> None:
        from services.orchestrator.expectations import Requirement, layout_part_scoped

        def layout(rid: str, statement: str, count: int, where: str = "") -> Any:
            check = {"part": "layout", "rule": "items", "count": count}
            if where:
                check["where"] = where
            return Requirement(rid, statement, 1, method="code", check=check)

        scoped = layout_part_scoped(
            [
                layout("R1", "three points on each slide", 3, "each"),
                layout("R2", "twelve points in all", 12, "each"),
                layout("R3", "a checklist of four boxes", 4),
                layout("R4", "a second checklist of four boxes", 4),
                layout("R5", "two points on slide 2", 2, "slide:2"),
            ]
        )
        self.assertEqual(scoped, {"R1", "R2", "R3", "R4"})


class PartsView(unittest.TestCase):
    def test_runs_of_list_items_are_the_parts(self) -> None:
        view = answer_view(
            "Intro.\n\nOption A:\n- a\n- b\n\nOption B:\n1. c\n2. d\n3. e\n\nBye."
        )
        self.assertEqual(checks.list_runs(view), [(2, True), (3, True)])


if __name__ == "__main__":
    unittest.main()
