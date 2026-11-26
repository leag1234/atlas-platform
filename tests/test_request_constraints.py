"""The person's constraints on a reply or a file: read, checked, repaired once.

Fictional content in English, French, German, Spanish and Italian
(services/orchestrator/constraints, docs/33).
"""

import asyncio
from collections.abc import Coroutine
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Any
import unittest

from services.orchestrator.constraints import checks, phrases, pipeline, verify
from services.orchestrator.constraints.model import Constraint, validated
from services.orchestrator.constraints.views import answer_view

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "constraints_measure", ROOT / "tools" / "constraints_measure.py"
)
assert SPEC is not None and SPEC.loader is not None
measure = importlib.util.module_from_spec(SPEC)
sys.modules["constraints_measure"] = measure
SPEC.loader.exec_module(measure)

LINK = '[Open "Tide clock" (version 2)](/api/atlas/artifacts/a1/versions/2/open)'


def run(coroutine: Coroutine[Any, Any, pipeline.Outcome]) -> pipeline.Outcome:
    return asyncio.run(coroutine)


class Reading(unittest.TestCase):
    def test_labelled_requests(self) -> None:
        result = measure.measure_reading(measure.cases())
        total = {"tp": 0, "fp": 0, "fn": 0}
        for tally in result["by_kind"].values():
            for key in total:
                total[key] += tally[key]
        # Every labelled constraint is read; at most one extra reading (the
        # "no intro" phrase that only_request also counts as "nothing else").
        self.assertEqual(total["fn"], 0, result["misses"])
        self.assertLessEqual(total["fp"], 1, result["misses"])

    def test_exact_text_comes_from_quotation_marks(self) -> None:
        found = phrases.read(
            "Antworte kurz. Beginne deine Antwort mit „Kurz gesagt“.", 2
        )
        self.assertEqual(
            [(c.kind, c.text, c.turn) for c in found],
            [("starts_with", "Kurz gesagt", 2)],
        )

    def test_form_next_to_a_file_request_is_left_to_the_model(self) -> None:
        self.assertEqual(
            phrases.read(
                "Crea un PDF con tres fichas, cada una con una frase de cierre.", 1
            ),
            [],
        )

    def test_apostrophes_are_not_quotations(self) -> None:
        found = phrases.read(
            "Don't start with the kids' names, begin with 'Dear all'.", 1
        )
        self.assertEqual(
            [c.text for c in found if c.kind == "starts_with"], ["Dear all"]
        )


class Validation(unittest.TestCase):
    PERSON = [
        "Fais une liste de courses pour un pique-nique. Commence par « Pour samedi »."
    ]

    def test_quote_must_be_the_persons_words(self) -> None:
        raw = {
            "kind": "starts_with",
            "quote": "commence par « Pour dimanche »",
            "text": "Pour dimanche",
        }
        self.assertIsNone(validated(raw, 1, self.PERSON))

    def test_exact_text_never_invented(self) -> None:
        raw = {
            "kind": "starts_with",
            "quote": "Commence par « Pour samedi »",
            "text": "Pour le samedi 3",
        }
        self.assertIsNone(validated(raw, 1, self.PERSON))

    def test_text_shown_in_the_previous_reply(self) -> None:
        person = ["Je prends la deuxième liste, mets ses titres dans le même ordre."]
        shown = "Liste B : Fromages, Fruits, Boissons"
        raw = {
            "kind": "order",
            "quote": "mets ses titres dans le même ordre",
            "items": ["Fromages", "Fruits", "Boissons"],
        }
        item = validated(raw, 2, person, [shown])
        assert item is not None
        self.assertEqual(item.items, ("Fromages", "Fruits", "Boissons"))

    def test_lasting_only_when_said(self) -> None:
        person = ["Réponds toujours en commençant par « Bonjour »."]
        raw = {
            "kind": "starts_with",
            "quote": "Réponds toujours en commençant par « Bonjour »",
            "text": "Bonjour",
            "lasting": True,
        }
        lasting = validated(raw, 1, person)
        assert lasting is not None
        self.assertTrue(lasting.lasting)
        person = ["Commence par « Bonjour »."]
        raw = {
            "kind": "starts_with",
            "quote": "Commence par « Bonjour »",
            "text": "Bonjour",
            "lasting": True,
        }
        once = validated(raw, 1, person)
        assert once is not None
        self.assertFalse(once.lasting)

    def test_start_of_a_part_goes_to_the_verifier(self) -> None:
        person = ["Two paragraphs please. Start the second paragraph with “Also”."]
        raw = {
            "kind": "starts_with",
            "quote": "Start the second paragraph with “Also”",
            "text": "Also",
        }
        item = validated(raw, 1, person)
        assert item is not None
        self.assertEqual(item.kind, "judge")

    def test_titles_of_a_reply_are_an_order(self) -> None:
        person = ["Un tableau avec deux colonnes : Jour et Horaires."]
        raw = {
            "kind": "titles",
            "quote": "deux colonnes : Jour et Horaires",
            "items": ["Jour", "Horaires"],
        }
        item = validated(raw, 1, person)
        assert item is not None
        self.assertEqual(item.kind, "order")

    def test_count_next_to_no_list_is_not_a_list_count(self) -> None:
        found = [
            Constraint("items", "answer", "fünf Wörter", 1, count=5),
            Constraint("list_style", "answer", "in einer Zeile", 1, style="plain"),
        ]
        kinds = [item.kind for item in pipeline.consistent(found)]
        self.assertEqual(kinds, ["judge", "list_style"])

    def test_unknown_kind_or_missing_count(self) -> None:
        self.assertIsNone(
            validated({"kind": "tone", "quote": "Commence par"}, 1, self.PERSON)
        )
        self.assertIsNone(
            validated({"kind": "sentences", "quote": "Commence par"}, 1, self.PERSON)
        )


class Checkers(unittest.TestCase):
    def test_labelled_replies_and_files(self) -> None:
        result = measure.measure_checks(measure.cases())
        for label in ("answers", "files"):
            counts = result[label]["counts"]
            self.assertEqual(counts.get("fp", 0), 0, result[label]["wrong"])
            self.assertEqual(counts.get("fn", 0), 0, result[label]["wrong"])
            self.assertEqual(counts.get("undecided", 0), 0, result[label]["wrong"])

    def test_sentence_count(self) -> None:
        self.assertEqual(
            checks.sentence_count("Il pleut. Prends un ciré, p. ex. le vert !"), 2
        )
        self.assertEqual(
            checks.sentence_count("Der Wert liegt bei 3.5 m, z. B. am Ufer."), 1
        )
        self.assertEqual(checks.sentence_count("Sin punto final"), 1)

    def test_after_file_without_link_is_undecided(self) -> None:
        item = Constraint("after_file", "answer", "q", 1, text="Checked: no", id="K1")
        result = checks.check(item, answer_view("Checked: no"))
        self.assertIsNone(result.passed)

    def test_no_new_file_needs_the_turn_facts(self) -> None:
        item = Constraint("no_new_file", "answer", "q", 1, id="K1")
        self.assertIsNone(checks.check(item, answer_view("ok")).passed)
        self.assertFalse(
            checks.check(
                item, answer_view("ok"), {"produced": True, "made": "plan.pdf"}
            ).passed
        )
        self.assertTrue(
            checks.check(item, answer_view("ok"), {"produced": False}).passed
        )

    def test_turn_outcome_condition_decides_the_constraint(self) -> None:
        person = ["Envoie le rapport. Si tu ne peux pas l'attacher, dis-le."]
        raw = {
            "kind": "judge",
            "scope": "answer",
            "quote": "Si tu ne peux pas l'attacher, dis-le",
            "statement": "dire si la pièce jointe manque",
            "condition": "tu ne peux pas attacher le fichier",
        }
        item = validated(raw, 1, person)
        assert item is not None
        self.assertEqual(item.condition, "tu ne peux pas attacher le fichier")
        self.assertTrue(
            checks.check(
                item,
                answer_view("Voici le rapport."),
                {"produced": True, "made": "rapport.pdf"},
            ).passed
        )
        self.assertFalse(
            checks.check(
                item, answer_view("Voici le rapport."), {"produced": False}
            ).passed
        )

    def test_outside_condition_is_left_to_the_verifier(self) -> None:
        item = Constraint(
            "judge",
            "answer",
            "Si le magasin est fermé le dimanche, dis-le",
            1,
            statement="dire si le magasin est fermé",
            condition="le magasin est fermé le dimanche",
            id="K1",
        )
        self.assertIsNone(
            checks.check(item, answer_view("Le magasin est ouvert.")).passed
        )


class CodeRepairs(unittest.TestCase):
    def test_line_moved_under_the_file(self) -> None:
        messages = [
            ("user", 'Make the tide clock page and write under the file "Opened: no".')
        ]
        answer = "The clock shows the next high tide.\n\nOpened: no\n\n" + LINK
        outcome = run(
            pipeline.enforce(messages, answer, language="en", run_mode="code")
        )
        self.assertEqual(outcome.repaired, "code")
        self.assertTrue(outcome.answer.rstrip().endswith("Opened: no"))
        self.assertIn(LINK, outcome.answer)
        self.assertEqual(outcome.note, "")

    def test_lead_in_dropped(self) -> None:
        messages = [("user", "Sin introducción, dame los pasos para regar un bonsái.")]
        answer = "Claro, aquí tienes los pasos:\n\n1. Riega por la mañana.\n2. Deja escurrir."
        outcome = run(
            pipeline.enforce(messages, answer, language="es", run_mode="code")
        )
        self.assertTrue(outcome.answer.startswith("1. Riega"))
        self.assertEqual(outcome.note, "")

    def test_unmet_names_what_is_missing(self) -> None:
        messages = [("user", "Beschreibe den Leuchtturm ohne das Wort „romantisch“.")]
        answer = "Der Leuchtturm steht romantisch auf der Klippe."
        outcome = run(
            pipeline.enforce(messages, answer, language="de", run_mode="code")
        )
        self.assertEqual(outcome.answer, answer)
        self.assertIn("„romantisch“ kommt noch vor", outcome.note)
        self.assertTrue(outcome.note.startswith("Diese Antwort erfüllt die Anfrage"))

    def test_a_file_made_against_a_deferral_is_said(self) -> None:
        messages = [
            (
                "user",
                "Propose two layouts here first and wait for my choice before creating the poster.",
            )
        ]
        outcome = run(
            pipeline.enforce(
                messages,
                "Done: [poster.pdf](/api/atlas/orgs/o/projects/p/deliverables/d/download)",
                language="en",
                produced=True,
                made="poster.pdf",
                run_mode="code",
            )
        )
        self.assertEqual([c.kind for c in outcome.unmet()], ["no_new_file"])

    def test_conditional_failure_is_not_repaired_when_the_file_was_delivered(
        self,
    ) -> None:
        messages = [
            (
                "user",
                "Send the report. If you could not attach the file, just say so.",
            )
        ]
        answer = "Here is the report.\n\n" + LINK
        outcome = run(
            pipeline.enforce(
                messages,
                answer,
                language="en",
                produced=True,
                made="report.pdf",
                run_mode="code",
            )
        )
        self.assertEqual(outcome.answer, answer)
        self.assertEqual(outcome.repaired, "")
        self.assertEqual(outcome.note, "")

    def test_plain_turn_costs_nothing(self) -> None:
        outcome = run(
            pipeline.enforce(
                [("user", "Quanto è alto il Monte Bianco?")],
                "4806 m.",
                language="it",
                run_mode="code",
            )
        )
        self.assertEqual(outcome.constraints, [])
        self.assertEqual(outcome.calls, 0)

    def test_earlier_turn_constraints_do_not_bind_this_reply(self) -> None:
        messages = [
            ("user", "Commence ta réponse par « Deux idées »."),
            ("assistant", "Deux idées : A et B."),
            ("user", "Je prends A, fais-le."),
        ]
        outcome = run(
            pipeline.enforce(messages, "C'est fait.", language="fr", run_mode="code")
        )
        self.assertEqual(outcome.constraints, [])

    def test_off(self) -> None:
        outcome = run(
            pipeline.enforce(
                [("user", "Commence par « X »")], "Y", language="fr", run_mode="off"
            )
        )
        self.assertEqual(outcome.constraints, [])


class Fake:
    """A gateway that answers each call kind from a script."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        self.payloads.append(payload)
        system = payload["messages"][0]["content"]
        for marker, reply in self.replies.items():
            if marker in system:
                return {
                    "text": json.dumps(reply, ensure_ascii=False),
                    "cost_eur": "0.0004",
                }
        return {"text": "{}"}


class ModelSteps(unittest.TestCase):
    def setUp(self) -> None:
        import packages.profiles as profiles

        self._picture_profile = profiles.picture_profile
        profiles.picture_profile = lambda role: "fast-check"

    def tearDown(self) -> None:
        import packages.profiles as profiles

        profiles.picture_profile = self._picture_profile

    def test_reading_verifier_and_repair(self) -> None:
        person = (
            "Écris trois conseils pour un jardin partagé, en liste à cases à cocher."
        )
        fake = Fake(
            {
                "FORM and exact CONTENT": {
                    "constraints": [
                        {
                            "kind": "list_style",
                            "scope": "answer",
                            "style": "checkboxes",
                            "quote": "en liste à cases à cocher",
                            "statement": "liste à cases à cocher",
                        },
                        {
                            "kind": "items",
                            "scope": "answer",
                            "count": 3,
                            "style": "checkboxes",
                            "quote": "trois conseils",
                            "statement": "trois conseils",
                        },
                    ]
                },
                "You correct a reply": {
                    "answer": "- [ ] Arroser le soir\n- [ ] Pailler les tomates\n- [ ] Partager les outils"
                },
            }
        )
        answer = "- Arroser le soir\n- Pailler les tomates\n- Partager les outils"
        spent: list[Decimal] = []
        outcome = run(
            pipeline.enforce(
                [("user", person)],
                answer,
                language="fr",
                profile="writer",
                complete=fake,
                spend=spent.append,
                run_mode="full",
            )
        )
        self.assertEqual(outcome.repaired, "model")
        self.assertIn("- [ ] Pailler", outcome.answer)
        self.assertEqual(outcome.note, "")
        self.assertEqual(fake.payloads[0]["profile"], "fast-check")
        self.assertTrue(all(p["sampling_temperature"] == 0.0 for p in fake.payloads))
        self.assertEqual(len(spent), outcome.calls)

    def test_repair_dropping_a_link_is_refused(self) -> None:
        self.assertIsNone(
            verify.parse_repair(json.dumps({"answer": "Fixed."}), "Here: " + LINK)
        )
        self.assertIsNone(
            verify.parse_repair(json.dumps({"answer": "It is 12 m."}), "It is 11 m.")
        )
        self.assertEqual(
            verify.parse_repair(json.dumps({"answer": LINK}), "Here: " + LINK), LINK
        )

    def test_verdict_without_evidence_in_the_reply_is_not_kept(self) -> None:
        item = Constraint(
            "judge", "answer", "q", 1, statement="polite closing", id="K1"
        )
        reply = json.dumps(
            {
                "results": [
                    {"id": "K1", "met": True, "evidence": "Kind regards, the team"}
                ]
            }
        )
        verdicts = verify.parse_verdicts(reply, [item], "See you Monday.")
        assert verdicts is not None
        self.assertIsNone(verdicts["K1"].passed)
        reply = json.dumps(
            {"results": [{"id": "K1", "met": True, "evidence": "See you Monday"}]}
        )
        verdicts = verify.parse_verdicts(reply, [item], "See you Monday.")
        assert verdicts is not None
        self.assertTrue(verdicts["K1"].passed)

    def test_invented_requirement_is_dropped(self) -> None:
        fake = Fake(
            {
                "FORM and exact CONTENT": {
                    "constraints": [
                        {
                            "kind": "starts_with",
                            "scope": "answer",
                            "quote": "start with Hello",
                            "text": "Hello",
                        }
                    ]
                }
            }
        )
        outcome = run(
            pipeline.enforce(
                [("user", "List three rivers in Wales, numbered.")],
                "1. Usk\n2. Wye\n3. Teifi",
                language="en",
                complete=fake,
                run_mode="full",
            )
        )
        self.assertEqual([c.kind for c in outcome.constraints], [])

    def test_failed_call_never_blocks(self) -> None:
        async def broken(payload: dict[str, Any], seconds: float) -> dict[str, Any]:
            raise TimeoutError

        outcome = run(
            pipeline.enforce(
                [("user", "Answer in exactly two sentences: why do cats purr?")],
                "Nobody knows for sure.",
                language="en",
                complete=broken,
                run_mode="full",
            )
        )
        self.assertEqual([c.kind for c in outcome.unmet()], ["sentences"])
        self.assertEqual(outcome.answer, "Nobody knows for sure.")


class Hold(unittest.TestCase):
    def test_holds_only_with_a_constraint_in_code_mode(self) -> None:
        import os

        previous = os.environ.get("ATLAS_ANSWER_CONSTRAINTS")
        os.environ["ATLAS_ANSWER_CONSTRAINTS"] = "code"
        try:
            self.assertTrue(
                pipeline.holds("Réponds en une phrase : pourquoi la mer est salée ?")
            )
            self.assertFalse(pipeline.holds("Pourquoi la mer est salée ?"))
            os.environ["ATLAS_ANSWER_CONSTRAINTS"] = "off"
            self.assertFalse(
                pipeline.holds("Réponds en une phrase : pourquoi la mer est salée ?")
            )
        finally:
            if previous is None:
                os.environ.pop("ATLAS_ANSWER_CONSTRAINTS", None)
            else:
                os.environ["ATLAS_ANSWER_CONSTRAINTS"] = previous


class Files(unittest.TestCase):
    def test_unknown_format_is_not_read(self) -> None:
        from services.orchestrator.constraints.views import file_view

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.mp3"
            path.write_bytes(b"ID3")
            self.assertIsNone(file_view(path))


if __name__ == "__main__":
    unittest.main()
