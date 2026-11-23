"""A report laid out compact to fit its pages stays compact in its next
versions (staging -166: a table added to a compact one-page report was
built with the usual spacing and printed on two pages, while its writer had
been given the room of the compact print). The content is fictional."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import tempfile
from types import SimpleNamespace
import unittest

from packages.doc_render import readback, render
from packages.doc_render.schema import Content
from services.orchestrator.current_file import CurrentFile
from services.orchestrator.project_document_tools import compacted, kept_compact

REPORT = Content.model_validate(
    dict(
        title="Short Weeks at Kestrel Lane",
        sections=[dict(heading="Summary", paragraphs=["The office kept its output."])],
    )
)


def built(compact: bool) -> bytes:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "r.docx"
        render(REPORT, path, image_root=Path(directory), compact=2 if compact else 0)
        return path.read_bytes()


class KeptCompactTests(unittest.TestCase):
    def test_a_compact_file_is_rebuilt_compact(self) -> None:
        compact, usual = built(True), built(False)
        self.assertEqual(compacted(compact), 2)
        self.assertEqual(compacted(usual), 0)
        self.assertEqual(compacted(b"not a docx"), 0)
        for raw, name, expected in (
            (compact, "kestrel.docx", 2),
            (usual, "kestrel.docx", 0),
            (compact, "orchard.docx", 0),
        ):
            with self.subTest(expected=expected, name=name):
                current = CurrentFile(
                    name, ".docx", "f1", "v1", readback.read(raw, ".docx"), raw=raw
                )
                tools = SimpleNamespace(current_file=current)
                self.assertEqual(
                    kept_compact(tools, PurePosixPath("kestrel.docx")), expected
                )
        self.assertFalse(kept_compact(SimpleNamespace(), PurePosixPath("kestrel.docx")))


if __name__ == "__main__":
    unittest.main()
