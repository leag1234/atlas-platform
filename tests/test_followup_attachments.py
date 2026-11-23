"""Attachments of an earlier turn stay readable; their file id never reaches the model."""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Mapping
import unittest

from packages.turn_attachments import select_documents, side_view_markers
from services.orchestrator.chat_schema import ChatRequest

FILE_ID = "0b7c2f4e-1d2a-4c55-9e1f-6a3b8d9c0e11"
LINK = (
    f"[Open in side view](/api/v1/files/{FILE_ID}/content"
    "#atlas-view=pdf&title=Quarterly+report.pdf&slot=top)"
)
PDF = {
    "id": FILE_ID,
    "name": "Quarterly report.pdf",
    "content_type": "application/pdf",
}


def chain(reply: Mapping[str, object], follow_up: str) -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": "Summarize the attached report.", "files": [PDF]},
        {"role": "assistant", **reply},
        {"role": "user", "content": follow_up, "files": []},
    ]


def filter_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "managed_filter_followup", Path("infra/openwebui_images.py").resolve()
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FollowUpAttachmentTests(unittest.TestCase):
    def selectors(
        self,
    ) -> tuple[tuple[str, Callable[[list[dict[str, Any]]], list[dict[str, Any]]]], ...]:
        return (
            ("shared", select_documents),
            ("filter", filter_module().fallback_documents),
        )

    def test_follow_up_after_a_failed_answer_reads_the_document_again(self) -> None:
        failed = {
            "content": LINK,
            "error": {"content": {"code": "provider_quota"}},
        }
        for follow_up in ("Any luck?", "Try again please", "Et maintenant ?"):
            for name, select in self.selectors():
                with self.subTest(selector=name, follow_up=follow_up):
                    selected = select(chain(failed, follow_up))
                    self.assertEqual([f["id"] for f in selected], [FILE_ID])
                    self.assertTrue(selected[0]["reused"])

    def test_reply_with_only_the_side_view_link_counts_as_undelivered(self) -> None:
        for name, select in self.selectors():
            with self.subTest(selector=name):
                selected = select(chain({"content": LINK + "\n\n"}, "And?"))
                self.assertEqual([f["id"] for f in selected], [FILE_ID])

    def test_answered_turn_keeps_the_turn_scoped_rule(self) -> None:
        answered = {"content": LINK + "\n\nRevenue grew in the third quarter."}
        for name, select in self.selectors():
            with self.subTest(selector=name):
                self.assertEqual(
                    select(chain(answered, "What is the capital of Peru?")), []
                )
                self.assertEqual(
                    [f["id"] for f in select(chain(answered, "Shorten the document"))],
                    [FILE_ID],
                )

    def test_side_view_link_becomes_a_marker_without_the_file_id(self) -> None:
        text = side_view_markers(LINK + "\n\nAnswer.")
        self.assertNotIn(FILE_ID, text)
        self.assertNotIn("/api/v1/files/", text)
        self.assertIn("[Attachment shown in the side view: Quarterly report.pdf]", text)
        delivered = f"[report.docx](/api/v1/files/{FILE_ID}/content#atlas-view=doc)"
        self.assertEqual(side_view_markers(delivered), delivered)

    def test_model_history_never_holds_the_file_id(self) -> None:
        request = ChatRequest.model_validate(
            {
                "messages": [
                    {"role": "user", "content": "Summarize the attached report."},
                    {"role": "assistant", "content": LINK},
                    {"role": "user", "content": "Any luck?"},
                ],
            }
        )
        history = "\n".join(message.text for message in request.messages)
        self.assertNotIn(FILE_ID, history)
        self.assertIn("Attachment shown in the side view", history)


if __name__ == "__main__":
    unittest.main()
