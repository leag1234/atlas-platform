"""A numbered copy of a file name is still the file name, never a title
(staging -170: a table added to "The Friday Experiment", saved as
"Four_Days_Full_Output (4).docx", came titled "Four_Days_Full_Output (4)").
The content is fictional."""

from __future__ import annotations

from pathlib import PurePosixPath
from types import SimpleNamespace
import unittest

from packages.doc_render.schema import Content
from packages.doc_render.titles import slug_like
from services.orchestrator.project_document_tools import kept_title


def content(title: str) -> Content:
    return Content.model_validate(
        dict(title=title, sections=[dict(heading="Summary", paragraphs=["Text."])])
    )


class CopyTitleTests(unittest.TestCase):
    def test_a_numbered_copy_of_a_file_name_is_a_slug(self) -> None:
        for title in ("Short_Weeks (4)", "Short_Weeks (12).docx", "short-weeks (2)"):
            with self.subTest(title=title):
                self.assertTrue(slug_like(title))
        self.assertFalse(slug_like("The Kestrel Experiment (2)"))

    def test_a_version_titled_by_its_file_name_keeps_the_current_title(self) -> None:
        current = SimpleNamespace(
            name="Short_Weeks (4).docx",
            document=SimpleNamespace(title="The Kestrel Experiment"),
        )
        tools = SimpleNamespace(built_titles={}, current_file=current)
        path = PurePosixPath("Short_Weeks (4).docx")
        for stem_title in ("Short Weeks (4)", "Short_Weeks (4)", "Short Weeks"):
            with self.subTest(title=stem_title):
                kept = kept_title(tools, path, content(stem_title))
                self.assertEqual(kept.title, "The Kestrel Experiment")
        # A real title the writer gives stays.
        tools.built_titles = {}
        given = kept_title(tools, path, content("Short Weeks at Kestrel Lane"))
        self.assertEqual(given.title, "Short Weeks at Kestrel Lane")


if __name__ == "__main__":
    unittest.main()
