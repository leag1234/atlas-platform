"""Values the assistant can compute are computed, never left as placeholders.

Class suite, staging 2026-11-24 (class C7): a one-page file had to show values
that follow from the counts and sizes the person gave; it came back with a
bracketed placeholder in every result cell. The rule for the person's own
facts (a placeholder where an outcome was not given) was applied to values
derivable from the given figures.

Fictional requests in five languages; no model is called.
"""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

from packages.doc_render.schema import Content
from services.orchestrator import deliverable_check, derived_values, own_facts
from services.orchestrator.project_document_input import normalize
from services.orchestrator.project_document_tools import DESCRIPTIONS


def content_of(markdown: str, path: str = "sheet.pdf") -> Content:
    value = normalize({"path": path, "content": markdown})
    return Content.model_validate(value["content"])


def tools_for(request: str, earlier: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        request_text=request,
        earlier_requests=earlier or [],
        deliverable_checks=0,
        deliverable_refused=False,
    )


DE_REQUEST = (
    "Mach mir ein PDF für die Bastelgruppe: wir schneiden 23 Holzleisten zu "
    "145 mm und 11 Leisten zu 310 mm. Eine Zeile pro Los und eine Summe, "
    "Spalten in mm und in m mit drei Nachkommastellen."
)
IT_REQUEST = (
    "Fammi una pagina PDF per il laboratorio: 6 pannelli da 1250 g e 14 "
    "staffe da 85 g. Una riga per lotto e il totale, in g e in kg."
)


class DerivationTests(unittest.TestCase):
    def test_products_sums_and_conversions_follow(self) -> None:
        given = derived_values.figures(DE_REQUEST)
        for stated, decimals in (
            ("3335", 0),  # 23 × 145
            ("3.335", 3),  # in metres
            ("3410", 0),  # 11 × 310
            ("6745", 0),  # the sum of both lots
            ("6.745", 3),
            ("0.145", 3),  # one given length converted
        ):
            with self.subTest(stated=stated):
                self.assertTrue(
                    derived_values.derivable(Decimal(stated), decimals, given)
                )
        self.assertFalse(derived_values.derivable(Decimal("98765"), 0, given))

    def test_one_given_figure_is_converted_never_multiplied_by_ten(self) -> None:
        given = derived_values.figures("our studio is trying a 4 day week")
        self.assertFalse(derived_values.derivable(Decimal("40"), 0, given))
        self.assertFalse(derived_values.derivable(Decimal("32"), 0, given))

    def test_italian_weights(self) -> None:
        given = derived_values.figures(IT_REQUEST)
        for stated, decimals in (("7500", 0), ("1190", 0), ("8.69", 2)):
            with self.subTest(stated=stated):
                self.assertTrue(
                    derived_values.derivable(Decimal(stated), decimals, given)
                )


class OwnFactsTests(unittest.TestCase):
    def test_a_computed_line_is_the_person_own(self) -> None:
        self.assertTrue(own_facts.given("Holzleisten 3335 3.335", [DE_REQUEST]))
        self.assertTrue(own_facts.given("Summe 6745 6.745", [DE_REQUEST]))

    def test_an_invented_figure_is_still_not_given(self) -> None:
        asked = ["could you write a short report about our studio trying a 4 day week?"]
        self.assertFalse(
            own_facts.given("reducing hours from 40 to 32 per week", asked)
        )

    def test_the_question_and_the_repair_say_computed_values_are_written(
        self,
    ) -> None:
        self.assertIn("follows by calculation", own_facts.QUESTION)
        self.assertIn("computed and written", own_facts.GAP)
        description = DESCRIPTIONS["publish_document"]
        self.assertIn("follows by calculation", description)
        self.assertIn("never a placeholder", description)
        self.assertIn("follows by calculation", deliverable_check.SYSTEM)

    def test_a_listed_computed_line_is_dropped(self) -> None:
        numbered = ["Kurze Leisten (23 × 145 mm) 3335 3.335", "Summe 6745 6.745"]
        reply = json.dumps(
            {
                "lines": [
                    {"line": 1, "words": "3335 3.335"},
                    {"line": 2, "words": "Summe 6745 6.745"},
                ]
            }
        )
        self.assertEqual(own_facts.read(reply, numbered, [DE_REQUEST]), [])


ES_TABLE = """# Hoja de corte

| Lote | Longitud (mm) | Longitud (m) |
|---|---|---|
| Listones (23 de 145 mm) | [resultado a completar] | [resultado a completar] |
| Barras (11 de 310 mm) | [resultado a completar] | [resultado a completar] |
| Total | [resultado a completar] | [resultado a completar] |
"""
ES_REQUEST = (
    "Hazme un PDF con 23 listones de 145 mm y 11 barras de 310 mm, una fila por "
    "lote y el total, en mm y en m."
)
FR_TRIAL = """# Essai du nouveau vernis

| Échantillon | Couches | Tenue après un mois |
|---|---|---|
| Planche A | 2 | [résultat à compléter] |
| Planche B | 3 | [résultat à compléter] |
"""
FR_TRIAL_REQUEST = (
    "Fais une fiche PDF pour l'essai du vernis : planche A avec 2 couches, "
    "planche B avec 3 couches. On n'a pas encore les résultats."
)
EN_LINES = """# Paint order

- Wall area: 4.2 × 2.6 m = [area to add]
- Drying time: [to be measured]
"""


class PlaceholderGapTests(unittest.TestCase):
    def test_placeholders_for_computable_values_are_a_gap(self) -> None:
        gaps = deliverable_check.computed_placeholder_gaps(
            tools_for(ES_REQUEST), content_of(ES_TABLE)
        )
        self.assertEqual(len(gaps), 1)
        self.assertIn("follow by calculation", gaps[0])
        self.assertIn("Total", gaps[0])
        self.assertIn("[resultado a completar]", gaps[0])

    def test_a_written_calculation_on_a_line_is_a_gap(self) -> None:
        gaps = deliverable_check.computed_placeholder_gaps(
            tools_for("Paint for a wall of 4.2 m by 2.6 m, as a PDF please"),
            content_of(EN_LINES),
        )
        self.assertEqual(len(gaps), 1)
        self.assertIn("4.2 × 2.6", gaps[0])
        self.assertNotIn("Drying", gaps[0])

    def test_outcomes_nobody_gave_keep_their_placeholder(self) -> None:
        self.assertEqual(
            deliverable_check.computed_placeholder_gaps(
                tools_for(FR_TRIAL_REQUEST), content_of(FR_TRIAL)
            ),
            [],
        )

    def test_figures_from_earlier_messages_count(self) -> None:
        gaps = deliverable_check.computed_placeholder_gaps(
            tools_for("Mettilo in una pagina PDF", [IT_REQUEST]),
            content_of(
                "# Pesi\n\n| Lotto | g | kg |\n|---|---|---|\n"
                "| Pannelli (6 da 1250 g) | [da calcolare] | [da calcolare] |\n"
            ),
        )
        self.assertEqual(len(gaps), 1)

    def test_the_gap_refuses_the_file_once(self) -> None:
        gaps = deliverable_check.computed_placeholder_gaps(
            tools_for(ES_REQUEST), content_of(ES_TABLE)
        )
        outcome = deliverable_check.Outcome()
        outcome.certain = list(gaps)
        outcome.gaps = list(gaps)
        tools = tools_for(ES_REQUEST)
        with self.assertRaises(ValueError) as refused:
            deliverable_check.decide(tools, outcome)
        self.assertIn("follow by calculation", str(refused.exception))
        # A second file with the same gaps is published with a note.
        note = deliverable_check.decide(tools, outcome)
        self.assertIn("follow by calculation", note)


if __name__ == "__main__":
    unittest.main()
