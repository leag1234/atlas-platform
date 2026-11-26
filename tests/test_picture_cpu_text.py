"""CPU pictures: counted and unusual details checked and said, words to show
set by code (rewrite checks and text, text_setting, cpu_checks)."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch
from xml.etree import ElementTree

import numpy as np
from PIL import Image, ImageDraw

from services.orchestrator.interactions import Interaction
from services.orchestrator.pictures import (
    cpu_checks,
    cpu_turn,
    cpu_finish,
    cpu_server,
    rewrite,
    store,
    text_setting,
    turn,
)
from services.orchestrator.pictures.choice import latest_set_id
from test_picture_cpu import CPU_ENVIRONMENT, CpuGateway, FakeServer, rewrite_reply
from test_picture_turn import Request
from picture_jobs import memoise_drafts

LOGO = "logo pour ma boulangerie « Le Pain d'Élise »"


def busy_bottom(size: int = 768, top: tuple[int, int, int] = (246, 240, 226)) -> str:
    """A picture with a plain top quarter and a striped, detailed rest."""
    picture = Image.new("RGB", (size, size), top)
    draw = ImageDraw.Draw(picture)
    for x in range(0, size, 12):
        draw.line([(x, size // 4 + 10), (x, size)], fill=(60, 90, 40), width=5)
    buffer = io.BytesIO()
    picture.save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


def decode(data: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")


class RewriteDetailTests(unittest.TestCase):
    def test_words_kept_as_the_user_wrote_them(self) -> None:
        reply = rewrite_reply(
            prompt="A bakery logo with a wheat sheaf, a sign reading 'Le Pain d'Elise'.",
            text=["« Le Pain d’Elise »"],
        )
        parsed = rewrite.parse(reply, source=LOGO)
        assert parsed is not None
        self.assertEqual(parsed.text, ["Le Pain d'Élise"])
        self.assertNotIn("Pain", parsed.prompt.replace(rewrite.STYLE, ""))
        self.assertIn(rewrite.RESERVE, parsed.prompt)
        self.assertTrue(parsed.prompt.endswith(rewrite.STYLE))

    def test_quoted_words_keep_their_case_composed_words_the_reply_capitals(
        self,
    ) -> None:
        quoted = "un mug avec « le café de Zoé »"
        self.assertEqual(rewrite.verbatim("Le Cafe de Zoe", quoted), "le café de Zoé")
        composed = "affiche pour la fête du village, samedi 14 juin"
        self.assertEqual(
            rewrite.verbatim("Fete du village", composed), "Fête du village"
        )
        self.assertEqual(rewrite.verbatim("Samedi 14 juin", composed), "Samedi 14 juin")
        # Words the request does not hold (a greeting written for a card).
        self.assertEqual(
            rewrite.verbatim("Joyeux anniversaire !", "carte pour Léo"),
            "Joyeux anniversaire !",
        )
        self.assertEqual(rewrite.verbatim("“Save the Bees”", ""), "Save the Bees")

    def test_text_lines_and_checks_are_bounded_and_clean(self) -> None:
        reply = rewrite_reply(
            text="Fête du village\nSaint-Aubin-sur-Mer\nsamedi\ntrop",
            checks=[
                "une seule horloge",
                " une seule horloge ",
                4,
                "les aiguilles sont rouges",
            ],
        )
        parsed = rewrite.parse(reply, source="")
        assert parsed is not None
        self.assertEqual(len(parsed.text), rewrite.MAX_TEXT_LINES)
        self.assertEqual(
            parsed.checks, ["une seule horloge", "les aiguilles sont rouges"]
        )
        plain = rewrite.parse(rewrite_reply())
        assert plain is not None
        self.assertEqual((plain.text, plain.checks), ([], []))
        self.assertNotIn(rewrite.RESERVE, plain.prompt)

    def test_a_changed_prompt_keeps_one_reserve_line(self) -> None:
        previous = "A logo with soft shadows. " + rewrite.RESERVE + ". " + rewrite.STYLE
        parsed = rewrite.parse(
            rewrite_reply(prompt=previous, text=["Chez Zoé"]),
            source="change le nom en « Chez Zoé »",
        )
        assert parsed is not None
        self.assertEqual(parsed.prompt.count(rewrite.RESERVE), 1)
        self.assertNotIn("..", parsed.prompt)
        self.assertEqual(parsed.text, ["Chez Zoé"])

    def test_a_diagram_has_labels_never_words_to_set(self) -> None:
        reply = rewrite_reply(diagram=True, labels=["tige"], text=["La fleur"])
        parsed = rewrite.parse(reply)
        assert parsed is not None
        self.assertTrue(parsed.diagram)
        self.assertEqual(parsed.text, [])

    def test_request_language_is_asked_and_the_prompt_stays_english(self) -> None:
        content = rewrite.messages("a teapot with 3 spouts")[1]["content"]
        self.assertTrue(content.startswith("a teapot with 3 spouts"))
        self.assertIn("'checks' and 'title' in the language of the request", content)
        self.assertIn("prompt stays in English", content)
        change = rewrite.messages("x", previous="P", change="y")
        self.assertIn("in the language of the request", change[1]["content"])
        self.assertIn('"language"', rewrite.SYSTEM)


class TextSettingTests(unittest.TestCase):
    def test_words_go_to_the_empty_band_in_dark_ink(self) -> None:
        picture = decode(busy_bottom())
        result, placement = text_setting.set_words(picture, ["Le Pain d'Élise"])
        top, bottom = placement["band"]
        self.assertLess(bottom, 768 // 4 + 20)
        self.assertEqual(placement["fill"], "#1c1c22")
        self.assertIsNone(placement["plate"])
        self.assertGreaterEqual(placement["contrast"], text_setting.CONTRAST)
        self.assertEqual(placement["missing"], [])
        x0, y0, x1, y1 = placement["box"]
        self.assertTrue(0 <= x0 < x1 <= 768 and top - 30 <= y0 < y1 <= bottom + 30)
        # Ink was laid in the band and nowhere else changed.
        before = np.asarray(picture, dtype=int)
        after = np.asarray(result, dtype=int)
        changed = np.argwhere(np.abs(after - before).sum(axis=2) > 0)
        self.assertGreater(len(changed), 500)
        self.assertTrue((changed[:, 0] >= y0 - 1).all() and (changed[:, 0] <= y1).all())

    def test_long_words_are_broken_and_fit_the_width(self) -> None:
        words = ["Les Recettes de Mamie Jo et de ses amis du marché couvert"]
        size, lines = text_setting.fit(words, 691, 200)
        self.assertGreater(len(lines), 1)
        face = text_setting.font(size)
        assert face is not None
        self.assertTrue(all(face.getlength(line) <= 691 for line in lines))
        self.assertEqual(" ".join(lines), words[0])
        short, one = text_setting.fit(["CAFÉ"], 691, 110)
        self.assertEqual(one, ["CAFÉ"])
        self.assertLessEqual(short * text_setting.LINE_HEIGHT, 110)

    def test_one_line_breaks_evenly_only_for_a_clear_gain(self) -> None:
        size, lines = text_setting.fit(["Save the Bees"], 691, 270)
        self.assertEqual(lines, ["Save the Bees"])
        size, lines = text_setting.fit(["Le Dernier Été à Noirmoutier"], 691, 260)
        self.assertEqual(lines, ["Le Dernier Été", "à Noirmoutier"])
        face = text_setting.font(size)
        assert face is not None
        widths = [face.getlength(line) for line in lines]
        self.assertLess(max(widths) - min(widths), 0.25 * max(widths))

    def test_band_grows_over_the_plain_rows(self) -> None:
        picture = decode(busy_bottom())
        top, bottom, _ = text_setting.band(picture, 1)
        self.assertGreater(bottom - top, text_setting.band_height(768, 1))
        self.assertLessEqual(bottom, 768 // 4 + 12)

    def test_ink_and_plate_follow_the_background(self) -> None:
        dark = np.full((40, 200, 3), 30, dtype=np.uint8)
        self.assertEqual(text_setting.ink(dark)["fill"], "#ffffff")
        self.assertIsNone(text_setting.ink(dark)["plate"])
        light = np.full((40, 200, 3), 240, dtype=np.uint8)
        self.assertEqual(text_setting.ink(light)["fill"], "#1c1c22")
        mixed = light.copy()
        mixed[:, ::4] = 20
        choice = text_setting.ink(mixed)
        self.assertIsNotNone(choice["plate"])
        self.assertGreaterEqual(choice["contrast"], text_setting.CONTRAST)
        self.assertAlmostEqual(text_setting.contrast(1.0, 0.0), 21.0)

    def test_glyphs_the_font_lacks_are_reported(self) -> None:
        face = text_setting.font(40)
        assert face is not None
        self.assertEqual(
            text_setting.missing_glyphs(face, "Œuvre « Élise » d’été àç ñ ß"), []
        )
        self.assertEqual(text_setting.missing_glyphs(face, "Zoé 漢"), ["漢"])

    def test_finished_picture_keeps_the_words_as_svg_text(self) -> None:
        result = cpu_finish.finish_picture(
            {
                "image": busy_bottom(),
                "title": "Logo",
                "text": ["Le Pain d'Élise", "<Zoé & Léo>"],
            }
        )
        image = decode(result["image"]["data"])
        self.assertEqual(image.size, (768, 768))
        root = ElementTree.fromstring(result["svg"])
        texts = [node.text for node in root.iter() if node.tag.endswith("}text")]
        self.assertEqual(texts, ["Le Pain d'Élise", "<Zoé & Léo>"])
        self.assertEqual(result["report"]["text"]["lines"], texts)
        # The embedded picture is the one without words (the text is editable).
        self.assertIn('class="words"', result["svg"])

    def test_labels_win_over_words(self) -> None:
        result = cpu_finish.finish_picture(
            {
                "image": busy_bottom(),
                "labels": ["tige"],
                "points": {"tige": [500, 500]},
                "text": ["Titre"],
            }
        )
        self.assertNotIn("<text", result["svg"])
        self.assertNotIn("text", result["report"])


class CheckTests(unittest.TestCase):
    CHECKS = ["une seule horloge", "les aiguilles sont rouges"]

    def test_reply_shapes(self) -> None:
        self.assertEqual(cpu_checks.parse('{"met": [true, false]}', 2), [True, False])
        self.assertEqual(
            cpu_checks.parse('```json\n{"met": [false, false]}\n```', 2), [False, False]
        )
        self.assertIsNone(cpu_checks.parse('{"met": [true]}', 2))
        self.assertIsNone(cpu_checks.parse('{"met": ["yes", false]}', 2))
        self.assertIsNone(cpu_checks.parse("no", 2))

    def test_notes(self) -> None:
        legs = "les aiguilles sont rouges"
        self.assertEqual(cpu_checks.note("fr", self.CHECKS, [[], []]), "")
        self.assertEqual(cpu_checks.note("fr", [], [None]), "")
        both = cpu_checks.note("fr", self.CHECKS, [[legs], [legs]])
        self.assertEqual(
            both,
            "Vérification automatique : ces images ne montrent peut-être pas "
            "« les aiguilles sont rouges ».",
        )
        one = cpu_checks.note("en", ["one clock"], [["one clock"]])
        self.assertEqual(
            one, "An automatic check found that this picture may not show “one clock”."
        )
        some = cpu_checks.note("fr", self.CHECKS, [[], self.CHECKS])
        self.assertIn("l'image 2 ne montre peut-être pas", some)
        self.assertNotIn("l'image 1", some)
        unknown = cpu_checks.note("en", ["two tails"], [None, None])
        self.assertIn("could not be checked", unknown)
        self.assertIn("“two tails”", unknown)
        # One picture checked, one not: only the checked one is spoken of.
        self.assertIn("picture 1", cpu_checks.note("en", ["a"], [["a"], None]))


class RegisterTests(unittest.TestCase):
    """The picture texts follow the person's register (PR 645 on every path)."""

    POLITE = {
        "fr": re.compile(r"\b(?:vous|votre|vos|[Dd]ites|[Cc]liquez|répondez)\b"),
        "de": re.compile(r"\b(?:Sie|Ihr|Ihre|Ihren|Ihrer|Ihnen)\b"),
        "es": re.compile(
            r"\b(?:usted|[Dd]ígame|[Dd]iga|pidió|sus peticiones|su primera)\b"
        ),
    }
    FAMILIAR = {
        "fr": "salut, tu peux me dessiner un phare ?",
        "de": "kannst du mir einen Leuchtturm zeichnen?",
        "es": "¿puedes dibujarme un faro? tú sabes",
    }

    def test_every_text_in_the_familiar_form(self) -> None:
        from packages import addressing
        from services.orchestrator.pictures import texts as picture_texts

        for language, pattern in self.POLITE.items():
            token = addressing.begin([self.FAMILIAR[language]], language)
            try:
                self.assertEqual(addressing.turn_register(language), "informal")
                for key, template in picture_texts.TEXTS[language].items():
                    values = {name: "X" for name in re.findall(r"\{(\w+)\}", template)}
                    written = picture_texts.text(language, key, **values)
                    with self.subTest(language=language, key=key):
                        self.assertIsNone(pattern.search(written), written)
            finally:
                addressing.end(token)
        token = addressing.begin(["dessine-moi un phare stp, tu peux ?"], "fr")
        try:
            self.assertTrue(picture_texts.text("fr", "review_next").startswith("Dis "))
        finally:
            addressing.end(token)

    def test_a_neutral_turn_keeps_the_register_of_the_conversation(self) -> None:
        # The turn itself says nothing about the register ("two wheels"): the
        # fixed texts follow the earlier messages, with nothing recorded for
        # the turn.
        from types import SimpleNamespace

        from services.orchestrator.pictures import texts as picture_texts
        from services.orchestrator.pictures.dialogue import person_register

        for language, pattern in self.POLITE.items():
            request = SimpleNamespace(
                person_texts=[self.FAMILIAR[language], "2"],
            )
            found = person_register(request, language)
            with self.subTest(language=language):
                self.assertEqual(found, "informal")
                for key in ("cpu_unsatisfied_all", "code_failed_too", "changed"):
                    template = picture_texts.TEXTS[language][key]
                    values = {name: "X" for name in re.findall(r"\{(\w+)\}", template)}
                    written = picture_texts.text(language, key, found=found, **values)
                    self.assertIsNone(pattern.search(written), written)

    def test_polite_or_unknown_register_keeps_the_tables(self) -> None:
        from services.orchestrator.pictures import texts as picture_texts

        self.assertEqual(
            picture_texts.text("fr", "hint"), picture_texts.TEXTS["fr"]["hint"]
        )


class CheckGateway(CpuGateway):
    """The CPU gateway, plus the check call's replies (one per picture)."""

    def __init__(self, reply: str, met: Sequence[str | Exception]) -> None:
        super().__init__(reply)
        self.met = list(met)

    async def __call__(self, payload: dict[str, Any], seconds: float) -> dict[str, Any]:
        if payload["messages"][0]["content"] == cpu_checks.SYSTEM:
            self.payloads.append(payload)
            answer = self.met.pop(0) if self.met else '{"met": [true]}'
            if isinstance(answer, Exception):
                raise answer
            return {
                "text": answer,
                "usage": {"prompt_tokens": 800, "completion_tokens": 10},
                "cost_eur": "0.0003",
            }
        return await super().__call__(payload, seconds)


class CpuTurnDetailTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="picture-cpu-text-"))
        self.addCleanup(shutil.rmtree, self.folder, True)
        environment = CPU_ENVIRONMENT | {
            "ATLAS_IMAGE_DIR": str(self.folder / "images"),
            "ATLAS_PICTURE_DIR": str(self.folder / "sets"),
            "ATLAS_DATA_DIR": str(self.folder / "data"),
            "ATLAS_ARTIFACTS": "1",
        }
        patcher = patch.dict(os.environ, environment)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def run_turn(
        self, gateway: CpuGateway, server: FakeServer, text: str
    ) -> Interaction:
        for name in ("health", "render_set"):
            patcher = patch.object(cpu_server, name, getattr(server, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        item = Interaction()
        await turn.process_picture_turn(Request(text), item, complete=gateway)
        return item

    def picture_set(self, item: Interaction) -> store.PictureSet:
        set_id = latest_set_id([{"role": "assistant", "content": item.reponse}])
        assert set_id is not None
        picture_set = store.load(set_id)
        assert picture_set is not None
        return picture_set

    def without_code(self) -> None:
        patcher = patch.object(turn, "available", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_missed_count_is_drawn_again_until_the_attempts_end(self) -> None:
        # Every attempt misses the count: the closest are shown, said to
        # fail with what the check saw, never presented as the answer.
        self.without_code()
        checks = ["un seul vélo", "le vélo a cinq roues"]
        missed = '{"met": [true, false], "seen": ["", "deux roues"]}'
        gateway = CheckGateway(rewrite_reply(checks=checks), [missed] * 6)
        server = FakeServer()
        item = await self.run_turn(gateway, server, "un vélo à 5 roues")
        checked = [
            p
            for p in gateway.payloads
            if p["messages"][0]["content"] == cpu_checks.SYSTEM
        ]
        self.assertEqual(len(checked), cpu_turn.MAX_ATTEMPTS)
        self.assertEqual(len(server.renders), cpu_turn.MAX_ATTEMPTS)
        self.assertEqual(len({seed for _, seed in server.renders}), 6)
        for payload in checked:
            self.assertEqual(payload["vision_role"], "locate")
            self.assertIn(
                json.dumps(checks, ensure_ascii=False),
                payload["messages"][1]["content"][0]["text"],
            )
        answer = item.reponse.replace("\xa0", " ")
        self.assertIn(
            "n'a pas réussi à dessiner « le vélo a cinq roues » (l'image montre : "
            "deux roues)",
            answer,
        )
        self.assertIn("aucun de mes 6 essais", item.reponse)
        self.assertIn("pas ce que vous avez demandé", item.reponse)
        self.assertIn("picture_cpu_unsatisfied", item.erreurs)
        picture_set = self.picture_set(item)
        self.assertEqual(picture_set.misses, 1)
        self.assertEqual(store.current_unmet(picture_set), ["le vélo a cinq roues"])
        self.assertAlmostEqual(item.cout_eur, 0.0005 + 6 * 0.0003, places=6)

    async def test_only_pictures_that_pass_are_shown(self) -> None:
        self.without_code()
        missed = '{"met": [false], "seen": ["trois tasses"]}'
        gateway = CheckGateway(
            rewrite_reply(checks=["sept tasses"]),
            [missed, missed, '{"met": [true]}', missed],
        )
        server = FakeServer()
        item = await self.run_turn(gateway, server, "sept tasses sur une table")
        self.assertEqual(len(server.renders), 4)
        self.assertTrue(item.reponse.startswith("Voici l'image."), item.reponse[:90])
        self.assertIn("j'ai fait 4 essais", item.reponse)
        self.assertNotIn("Une seule des deux images", item.reponse)
        self.assertNotIn("peut-être pas", item.reponse)
        picture_set = store.by_artifact(str(item.artifacts[0]["id"]))
        assert picture_set is not None
        self.assertEqual(len(picture_set.candidates), 1)
        self.assertEqual(picture_set.candidates[0].unmet, [])
        self.assertEqual(picture_set.misses, 0)

    async def test_one_of_two_failing_is_left_out_without_a_retry(self) -> None:
        self.without_code()
        gateway = CheckGateway(
            rewrite_reply(checks=["deux parapluies"]),
            ['{"met": [false]}', '{"met": [true]}'],
        )
        server = FakeServer()
        item = await self.run_turn(gateway, server, "deux parapluies")
        self.assertEqual(len(server.renders), 2)
        self.assertIn("j'ai fait 2 essais", item.reponse)
        self.assertNotIn("Une seule des deux images", item.reponse)

    async def test_a_drawing_by_code_answers_when_every_attempt_failed(self) -> None:
        memoise_drafts(self)
        missed = '{"met": [false], "seen": ["une seule chaise"]}'
        gateway = CheckGateway(
            rewrite_reply(checks=["quatre chaises"]),
            # The CPU attempts, then one check per drawing by code.
            [missed] * cpu_turn.MAX_ATTEMPTS
            + ['{"met": [true]}', missed, missed, missed],
        )
        server = FakeServer()
        with patch.object(turn, "soft_seconds", lambda: 30.0):
            item = await self.run_turn(gateway, server, "quatre chaises")
        self.assertIn("dessinées par du code", item.reponse)
        self.assertIn("picture_code_instead", item.erreurs)
        picture_set = store.by_artifact(str(item.artifacts[0]["id"]))
        assert picture_set is not None
        self.assertEqual(picture_set.engine, "code")
        self.assertEqual(len(picture_set.candidates), 1)

    async def test_check_failure_says_unchecked_and_keeps_the_pictures(self) -> None:
        gateway = CheckGateway(
            rewrite_reply(checks=["le chien a deux queues"]),
            [RuntimeError("down"), "not json"],
        )
        item = await self.run_turn(gateway, FakeServer(), "un chien à deux queues")
        self.assertEqual(len(self.picture_set(item).candidates), 2)
        self.assertIn("n'ont pas pu être vérifiés", item.reponse)
        self.assertIn("picture_cpu_check_invalid", item.erreurs)

    async def test_all_met_says_nothing_more(self) -> None:
        gateway = CheckGateway(
            rewrite_reply(checks=["trois chats"]),
            ['{"met": [true]}', '{"met": [true]}'],
        )
        item = await self.run_turn(gateway, FakeServer(), "trois chats")
        self.assertNotIn("Vérification", item.reponse)

    async def test_words_are_set_on_both_pictures_and_kept_in_the_artifact(
        self,
    ) -> None:
        reply = rewrite_reply(
            prompt="A bakery logo with a loaf of bread.", text=["Le Pain d'Elise"]
        )
        server = FakeServer()
        item = await self.run_turn(CpuGateway(reply), server, LOGO)
        for prompt, _ in server.renders:
            self.assertIn(rewrite.RESERVE, prompt)
        picture_set = self.picture_set(item)
        for candidate in picture_set.candidates:
            self.assertIn(">Le Pain d'Élise</text>", candidate.svg)
        self.assertNotIn("Vérification", item.reponse)


if __name__ == "__main__":
    unittest.main()
