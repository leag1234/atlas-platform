"""The shared evaluation corpus is never searched for a person on a hosted instance."""

import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from services.orchestrator.chat_pipeline import ChatTools
from services.orchestrator.interactions import Interaction
from services.orchestrator.loop import Call
from services.orchestrator.tools import corpus_tool_enabled


class CorpusToolScopeTests(unittest.TestCase):
    def test_hosted_instances_turn_the_shared_corpus_off(self) -> None:
        layer = Path("infra/instances/common.env").read_text()
        self.assertIn("\nATLAS_CORPUS_TOOL=0\n", layer)

    def test_development_and_gates_keep_it(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ATLAS_CORPUS_TOOL", None)
            self.assertTrue(corpus_tool_enabled())
        with mock.patch.dict(os.environ, {"ATLAS_CORPUS_TOOL": "0"}):
            self.assertFalse(corpus_tool_enabled())

    def test_a_call_is_refused_without_reaching_the_retrieval_service(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {
                "ATLAS_CORPUS_TOOL": "0",
                "ATLAS_WEB_CACHE": str(Path(directory, "cache.sqlite")),
            }
            with (
                mock.patch.dict(os.environ, env),
                mock.patch(
                    "services.orchestrator.chat_pipeline.GatewayModel.post",
                    side_effect=AssertionError("retrieval reached"),
                ),
            ):
                item = Interaction()
                tools = ChatTools(item, "What does the leave policy say?")
                output = asyncio.run(
                    tools.execute(
                        Call("c1", "rag_search", '{"query": "leave policy"}'), 5.0
                    )
                )
        self.assertEqual(output, {"error": "unknown_tool"})
        self.assertEqual(item.chunks_recuperes, [])


if __name__ == "__main__":
    unittest.main()
