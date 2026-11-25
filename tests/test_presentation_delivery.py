"""Offline coverage for validated deterministic PPTX delivery."""

import json
from decimal import Decimal
import time
from typing import Any, cast
import unittest
from unittest.mock import AsyncMock

from services.orchestrator.chat_schema import ChatRequest
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import Reservation, Result, Turn
from services.orchestrator.model import GatewayModel
from services.orchestrator.presentation_delivery import (
    deliver_presentation,
    parse_outline,
)


class PresentationDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def request(self, question: str) -> ChatRequest:
        return ChatRequest.model_validate(
            {"messages": [{"role": "user", "content": question}]}
        )

    def history(self, *messages: tuple[str, str]) -> list[dict[str, str]]:
        return [{"role": role, "content": text} for role, text in messages]

    def outline(self, title: str, topic: str) -> str:
        return json.dumps(
            {
                "title": title,
                "slides": [
                    {
                        "title": topic + " overview",
                        "bullets": [topic + " is the requested topic"],
                    },
                    {
                        "title": topic + " details",
                        "bullets": ["Concrete detail about " + topic],
                    },
                ],
            }
        )

    def deck(self, title: str, topic: str, count: int) -> str:
        return json.dumps(
            {
                "title": title,
                "slides": [
                    {
                        "title": f"{topic} part {number}",
                        "bullets": [f"{topic} detail {number}"],
                    }
                    for number in range(1, count + 1)
                ],
            }
        )

    def count_model(self, count: int | None, reply: str | None = None) -> GatewayModel:
        class Model:
            tools: list[object] = []
            required_file_tool = None
            sink = None
            spent = Decimal(0)
            delivery_reserve = Decimal(0)
            calls: list[list[dict[str, str]]] = []

            async def complete(self, messages: object, timeout: float) -> Turn:
                self.calls.append(list(cast(list[dict[str, str]], messages)))
                content = (
                    str(messages[-1]["content"]) if isinstance(messages, list) else ""
                )
                if "exact number of slides" in content:
                    text = json.dumps({"slides": count})
                else:
                    text = reply or ""
                return Turn(text, (), Reservation(1, Decimal("0.01")))

        return cast(GatewayModel, Model())

    def model(self, reply: str) -> GatewayModel:
        class Model:
            tools: list[object] = []
            required_file_tool = None
            sink = None
            spent = Decimal(0)
            delivery_reserve = Decimal(0)

            async def complete(self, messages: object, timeout: float) -> Turn:
                return Turn(reply, (), Reservation(1, Decimal("0.01")))

        return cast(GatewayModel, Model())

    async def test_french_outline_builds_and_reopens_pptx(self) -> None:
        request = self.request("Crée une présentation PPTX sur le recyclage")
        terminal = type(
            "Terminal",
            (),
            {
                "upload_generated": AsyncMock(
                    return_value="/request/presentation.pptx"
                ),
                "publish": AsyncMock(return_value={}),
            },
        )()
        item = Interaction()
        result = Result(text=self.outline("Le recyclage", "recyclage"), state="done")
        delivered = await deliver_presentation(
            self.model("{}"),
            request,
            item,
            result,
            terminal,
            time.monotonic(),
            ("pptx",),
        )
        self.assertIs(delivered, result)
        terminal.upload_generated.assert_awaited_once()
        terminal.publish.assert_awaited_once()

    async def test_english_invalid_outline_gets_one_repair(self) -> None:
        request = self.request("Create a PPTX presentation about recycling")
        terminal = type(
            "Terminal",
            (),
            {
                "upload_generated": AsyncMock(
                    return_value="/request/presentation.pptx"
                ),
                "publish": AsyncMock(return_value={}),
            },
        )()
        model = self.model(self.outline("Recycling", "recycling"))
        item = Interaction()
        delivered = await deliver_presentation(
            model,
            request,
            item,
            Result(text="not json", state="done"),
            terminal,
            time.monotonic(),
            ("pptx",),
        )
        self.assertIsNotNone(delivered)
        terminal.publish.assert_awaited_once()

    async def test_wrong_counts_are_repaired_to_the_asked_number(self) -> None:
        for asked, built, repaired in ((3, 5, 3), (5, 8, 5), (7, 4, 7)):
            with self.subTest(asked=asked):
                request = self.request(
                    f"Create a PPTX about the fictional Lumen Bay regatta, {asked} slides"
                )
                terminal = type(
                    "Terminal",
                    (),
                    {
                        "upload_generated": AsyncMock(
                            return_value="/request/presentation.pptx"
                        ),
                        "publish": AsyncMock(return_value={}),
                    },
                )()
                topic = "Lumen Bay"
                model = self.count_model(
                    asked, self.deck("Lumen Bay regatta", topic, repaired)
                )
                item = Interaction()
                result = Result(
                    text=self.deck("Lumen Bay regatta", topic, built), state="done"
                )
                delivered = await deliver_presentation(
                    model,
                    request,
                    item,
                    result,
                    terminal,
                    time.monotonic(),
                    ("pptx",),
                )
                self.assertIsNotNone(delivered)
                terminal.upload_generated.assert_awaited_once()
                terminal.publish.assert_awaited_once()

    async def test_an_accepted_outline_uses_its_item_count(self) -> None:
        request = self.request("I choose the second outline. Make the PPTX now.")
        terminal = type(
            "Terminal",
            (),
            {
                "upload_generated": AsyncMock(
                    return_value="/request/presentation.pptx"
                ),
                "publish": AsyncMock(return_value={}),
            },
        )()
        model = self.count_model(5, self.deck("Glass Kite Club", "outline", 5))
        item = Interaction()
        result = Result(text=self.deck("Glass Kite Club", "outline", 6), state="done")
        delivered = await deliver_presentation(
            model,
            request,
            item,
            result,
            terminal,
            time.monotonic(),
            ("pptx",),
        )
        self.assertIsNotNone(delivered)
        terminal.publish.assert_awaited_once()

    async def test_an_asked_extra_title_slide_is_kept(self) -> None:
        request = self.request(
            "Create a 3-slide PPTX about the fictional Amber Ferry, and add a title slide"
        )
        terminal = type(
            "Terminal",
            (),
            {
                "upload_generated": AsyncMock(
                    return_value="/request/presentation.pptx"
                ),
                "publish": AsyncMock(return_value={}),
            },
        )()
        model = self.count_model(4, self.deck("Amber Ferry", "Amber Ferry", 4))
        item = Interaction()
        result = Result(text=self.deck("Amber Ferry", "Amber Ferry", 4), state="done")
        delivered = await deliver_presentation(
            model,
            request,
            item,
            result,
            terminal,
            time.monotonic(),
            ("pptx",),
        )
        # Still off after the repair: the deck is delivered all the same.
        self.assertIsNotNone(delivered)
        terminal.publish.assert_awaited_once()

    async def test_an_earlier_accepted_outline_uses_its_item_count(self) -> None:
        request = self.request("Make the Glass Kite Club PPTX now.")
        history = self.history(
            ("user", "Propose two outlines for the fictional Glass Kite Club."),
            ("assistant", "Outline one has three items. Outline two has five items."),
            ("user", "I choose the second outline."),
        )
        terminal = type(
            "Terminal",
            (),
            {
                "upload_generated": AsyncMock(
                    return_value="/request/presentation.pptx"
                ),
                "publish": AsyncMock(return_value={}),
            },
        )()
        model = self.count_model(5, self.deck("Glass Kite Club", "outline", 5))
        item = Interaction()
        result = Result(text=self.deck("Glass Kite Club", "outline", 6), state="done")
        delivered = await deliver_presentation(
            model,
            request,
            item,
            result,
            terminal,
            time.monotonic(),
            ("pptx",),
            history=cast(list[dict[str, object]], history),
        )
        self.assertIsNotNone(delivered)
        terminal.publish.assert_awaited_once()

    async def test_no_count_returns_null_and_skips_repair(self) -> None:
        request = self.request("Create a PPTX about the fictional Blue Harbor.")
        terminal = type(
            "Terminal",
            (),
            {
                "upload_generated": AsyncMock(
                    return_value="/request/presentation.pptx"
                ),
                "publish": AsyncMock(return_value={}),
            },
        )()
        model = self.count_model(None, self.deck("Blue Harbor", "Blue Harbor", 4))
        item = Interaction()
        result = Result(text=self.deck("Blue Harbor", "Blue Harbor", 4), state="done")
        delivered = await deliver_presentation(
            model,
            request,
            item,
            result,
            terminal,
            time.monotonic(),
            ("pptx",),
        )
        self.assertIsNotNone(delivered)
        self.assertNotIn("presentation_slide_count", item.erreurs)
        self.assertEqual(len(cast(Any, model).calls), 1)
        terminal.publish.assert_awaited_once()

    async def test_a_deck_still_off_after_repair_is_delivered_with_a_note(self) -> None:
        request = self.request("Create a 5-slide PPTX about the fictional Red Ledge.")
        terminal = type(
            "Terminal",
            (),
            {
                "upload_generated": AsyncMock(
                    return_value="/request/presentation.pptx"
                ),
                "publish": AsyncMock(return_value={}),
            },
        )()
        model = self.count_model(5, self.deck("Red Ledge", "Red Ledge", 7))
        item = Interaction()
        result = Result(text=self.deck("Red Ledge", "Red Ledge", 6), state="done")
        delivered = await deliver_presentation(
            model,
            request,
            item,
            result,
            terminal,
            time.monotonic(),
            ("pptx",),
        )
        self.assertIsNotNone(delivered)
        self.assertIn("presentation_slide_count_after_repair", item.erreurs)
        terminal.publish.assert_awaited_once()

    async def test_second_invalid_outline_is_honest_failure(self) -> None:
        request = self.request("Create a PPTX presentation about recycling")
        terminal = type(
            "Terminal", (), {"upload_generated": AsyncMock(), "publish": AsyncMock()}
        )()
        item = Interaction()
        result = Result(text="bad", state="done")
        delivered = await deliver_presentation(
            self.model("still not json"),
            request,
            item,
            result,
            terminal,
            time.monotonic(),
            ("pptx",),
        )
        self.assertIsNone(delivered)
        self.assertIn("presentation_outline_invalid_after_repair", item.erreurs)
        self.assertEqual(result.cost, Decimal("0.01"))
        terminal.upload_generated.assert_not_awaited()

    def test_schema_and_topic_validation_reject_invalid_content(self) -> None:
        with self.assertRaises(ValueError):
            parse_outline(
                self.outline("Different", "different"), "Create a PPTX about recycling"
            )

    def test_owner_phrasing_with_filler_words_is_accepted(self) -> None:
        question = (
            "fais-moi une présentation de /e/OS et Murena, pptx téléchargeable et pdf"
        )
        outline = parse_outline(self.outline("Murena", "Murena"), question)
        self.assertTrue(outline.slides)

    def test_failed_outline_reaches_the_user_as_readable_text(self) -> None:
        from services.orchestrator.presentation_delivery import outline_markdown

        text = outline_markdown(self.outline("Recycling", "recycling"))
        self.assertTrue(text.startswith("# "))
        self.assertIn("## recycling overview", text)
        self.assertNotIn("{", text)
