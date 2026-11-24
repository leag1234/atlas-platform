"""The parts a person asked to see on a slide are on the slide (fictional decks)."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pptx import Presentation

from packages.doc_render import render
from packages.doc_render.schema import Content
from services.orchestrator import slide_parts

REQUEST = (
    "Make a deck for the beekeeping fair. Give the first slide the title "
    "«The Honey Round». Add a slide with the heading "
    "«Winter preparation» and under the list the sentence "
    "«Close the hive entrances before the first frost.»"
)


def deck(sections: list[dict[str, object]], title: str = "Bee fair 2027") -> Content:
    return Content.model_validate(dict(title=title, sections=sections))


def slides_text(content: Content) -> tuple[list[str], list[str]]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "deck.pptx"
        render(content, path, image_root=Path(directory))
        built = Presentation(str(path))
    shown, notes = [], []
    for slide in built.slides:
        shown.append(
            " | ".join(s.text_frame.text for s in slide.shapes if s.has_text_frame)
        )
        notes.append(
            slide.notes_slide.notes_text_frame.text if slide.has_notes_slide else ""
        )
    return shown, notes


class RequestedTests(unittest.TestCase):
    def test_roles_follow_the_title_word_in_five_languages(self) -> None:
        cases = (
            ("The first slide is titled “Hive Round Up”.", "title"),
            ("Le titre de la première diapositive : «La Ronde du Miel».", "title"),
            ("Die Überschrift lautet „Honigrunde am Morgen“.", "title"),
            ("El título será «La vuelta de la miel».", "title"),
            ("Il titolo è «Il giro del miele».", "title"),
            ("Write the sentence “Close the hive before the frost”.", "text"),
        )
        for request, role in cases:
            with self.subTest(request=request):
                (part,) = slide_parts.requested([request])
                self.assertEqual(part.role, role)


class PlacedTests(unittest.TestCase):
    def test_text_held_only_by_notes_goes_on_the_slide(self) -> None:
        parts = slide_parts.requested([REQUEST])
        content = deck(
            [
                dict(heading="The Honey Round", paragraphs=["Opening"]),
                dict(
                    heading="Autumn",
                    bullets=["Feed the colony", "Reduce the entrance"],
                    notes=[
                        "Winter preparation",
                        "Close the hive entrances before the first frost.",
                    ],
                ),
            ]
        )
        shown, _ = slides_text(content)
        self.assertNotIn("Close the hive entrances", shown[1])
        repaired = slide_parts.placed(content, parts)
        shown, notes = slides_text(repaired)
        slide = shown[1]
        self.assertIn("Close the hive entrances before the first frost.", slide)
        self.assertGreater(
            slide.index("Close the hive"), slide.index("Reduce the entrance")
        )
        self.assertNotIn("Close the hive", notes[1])

    def test_sentence_under_a_list_is_drawn_after_it_on_the_same_slide(self) -> None:
        content = deck(
            [
                dict(heading="Cover", paragraphs=["Opening"]),
                dict(
                    heading="Autumn", bullets=["Feed the colony", "Reduce the entrance"]
                ),
                dict(
                    heading="Autumn",
                    paragraphs=["Close the hive entrances before the first frost."],
                    continued=True,
                ),
                dict(heading="Spring", paragraphs=["Inspect the frames."]),
            ]
        )
        shown, _ = slides_text(content)
        self.assertEqual(len(shown), 3)
        self.assertLess(shown[1].index("Reduce"), shown[1].index("Close the hive"))

    def test_requested_first_title_is_the_title_not_the_subtitle(self) -> None:
        parts = slide_parts.requested([REQUEST])
        content = deck([dict(heading="The Honey Round", paragraphs=["Opening"])])
        repaired = slide_parts.placed(content, parts)
        self.assertEqual(repaired.title, "The Honey Round")
        shown, _ = slides_text(repaired)
        self.assertEqual(shown[0].count("The Honey Round"), 1)

    def test_text_the_content_never_held_is_not_invented(self) -> None:
        parts = slide_parts.requested([REQUEST])
        content = deck([dict(heading="Autumn", paragraphs=["Feed the colony."])])
        self.assertEqual(slide_parts.placed(content, parts).sections, content.sections)


class MissingTests(unittest.TestCase):
    def test_a_part_only_in_the_notes_of_the_built_deck_is_named(self) -> None:
        parts = slide_parts.requested([REQUEST])
        content = deck(
            [
                dict(heading="The Honey Round", paragraphs=["Opening"]),
                dict(
                    heading="Autumn",
                    bullets=["Feed", "Reduce"],
                    notes=["Close the hive entrances before the first frost."],
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.pptx"
            render(content, path, image_root=Path(directory))
            raw = path.read_bytes()
        gaps = slide_parts.missing(raw, parts)
        self.assertEqual(len(gaps), 1)
        self.assertIn("Close the hive entrances", gaps[0])
        repaired = slide_parts.placed(content, parts)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deck.pptx"
            render(repaired, path, image_root=Path(directory))
            self.assertEqual(slide_parts.missing(path.read_bytes(), parts), [])


if __name__ == "__main__":
    unittest.main()
