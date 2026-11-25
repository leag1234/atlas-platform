"""Summary values of a built document keep one line each."""

import tempfile
import unittest
from pathlib import Path

from packages.doc_render import Content, Section, render, verify


class DocumentTotalLinesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def content(self, paragraphs: tuple[str, ...]) -> Content:
        return Content(
            title="Provided title",
            sections=(Section(heading="Provided heading", paragraphs=paragraphs),),
        )

    def test_invoice_summary_lines(self) -> None:
        content = self.content(
            ("Subtotal: 1 250,00 EUR Tax 20 %: 250,00 EUR Total: 1 500,00 EUR",)
        )
        for suffix in ("pdf", "docx"):
            path = self.root / f"invoice.{suffix}"
            result = render(content, path, image_root=self.root)
            self.assertIn(
                "Subtotal: 1 250,00 EUR", " ".join(result.text).replace("\xa0", " ")
            )
            self.assertIn(
                "Tax 20 %: 250,00 EUR", " ".join(result.text).replace("\xa0", " ")
            )
            self.assertIn(
                "Total: 1 500,00 EUR", " ".join(result.text).replace("\xa0", " ")
            )
            self.assertNotIn("Tax 20 %: 250,00 EUR Total", result.text)
            verify(path, expected=content)

    def test_score_and_time_summary_lines(self) -> None:
        content = self.content(
            (
                "Average: 3.5 Total: 7",
                "Hours: 4 Rate: 20 Total: 80",
            )
        )
        for suffix in ("pdf", "docx"):
            path = self.root / f"summary.{suffix}"
            result = render(content, path, image_root=self.root)
            self.assertIn("Average: 3.5", "".join(result.text))
            self.assertIn("Total: 7", "".join(result.text))
            self.assertIn("Hours: 4", "".join(result.text))
            self.assertIn("Rate: 20", "".join(result.text))
            self.assertIn("Total: 80", "".join(result.text))
            self.assertNotIn("3.5 Total", "".join(result.text))
            self.assertNotIn("Rate: 20 Total", "".join(result.text))
            verify(path, expected=content)

    def test_prose_with_colons_is_not_split(self) -> None:
        content = self.content(("Note: this is prose. Then: another sentence.",))
        path = self.root / "prose.pdf"
        result = render(content, path, image_root=self.root)
        self.assertIn(
            "Note: this is prose. Then: another sentence.", "".join(result.text)
        )
        verify(path, expected=content)

    def test_final_total_is_distinct(self) -> None:
        content = self.content(("Subtotal: 10 Tax: 2 Total: 12",))
        path = self.root / "total.docx"
        result = render(content, path, image_root=self.root)
        self.assertIn("Total: 12", "".join(result.text))
        verify(path, expected=content)

    def test_currency_and_unit_values_keep_their_unit(self) -> None:
        content = self.content(
            (
                "Subtotal EUR: 126,90 € Tax 20 % (EUR part only): 25,38 € "
                "Total EUR: 152,28 €",
                "Subtotal: CHF 43.20 Tax: CHF 8.64 Total: CHF 51.84",
                "Subtotal: $ 8.74 Tax: $ 1.75 Total: $ 10.49",
            )
        )
        for suffix in ("pdf", "docx"):
            path = self.root / f"currency.{suffix}"
            result = render(content, path, image_root=self.root)
            text = " ".join(result.text).replace("\xa0", " ")
            self.assertIn("Subtotal EUR: 126,90 €", text)
            self.assertIn("Tax 20 % (EUR part only): 25,38 €", text)
            self.assertIn("Total EUR: 152,28 €", text)
            self.assertIn("Subtotal: CHF 43.20", text)
            self.assertIn("Tax: CHF 8.64", text)
            self.assertIn("Total: CHF 51.84", text)
            self.assertIn("Subtotal: $ 8.74", text)
            self.assertIn("Tax: $ 1.75", text)
            self.assertIn("Total: $ 10.49", text)
            self.assertNotIn("25,38 € Total", text)
            self.assertNotIn("CHF 8.64 Total", text)
            verify(path, expected=content)

    def test_time_values_keep_their_colon(self) -> None:
        content = self.content(("Start: 09:30 End: 17:45 Total: 8 h 15",))
        for suffix in ("pdf", "docx"):
            path = self.root / f"time.{suffix}"
            result = render(content, path, image_root=self.root)
            text = " ".join(result.text).replace("\xa0", " ")
            self.assertIn("Start: 09:30", text)
            self.assertIn("End: 17:45", text)
            self.assertIn("Total: 8 h 15", text)
            self.assertNotIn("09:30 End", text)
            self.assertNotIn("17:45 Total", text)
            verify(path, expected=content)
