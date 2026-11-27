from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.harness_rules import check, count_source, update


class HarnessRulesTests(unittest.TestCase):
    def test_counts_each_kind(self) -> None:
        counts = count_source(
            "import re\n"
            'WORDS = ("one", "two", "three")\n'
            'LANGUAGES = {"fr": 1, "en": 2}\n'
            'if re.search("x", value):\n'
            "    pass\n"
        )
        self.assertEqual(
            (counts.regex, counts.word_tables, counts.language_tables), (1, 1, 1)
        )

    def test_growing_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "services" / "orchestrator" / "sample.py"
            source.parent.mkdir(parents=True)
            baseline = root / "baseline.json"
            source.write_text("import re\n", encoding="utf-8")
            update(None, baseline, root)
            source.write_text(
                'import re\nif re.match("x", value):\n    pass\n', encoding="utf-8"
            )
            passed, failures = check(baseline, root)
            self.assertFalse(passed)
            self.assertIn("services/orchestrator/sample.py: regex 0 -> 1", failures)

    def test_rise_without_reason_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "services" / "orchestrator" / "sample.py"
            source.parent.mkdir(parents=True)
            baseline = root / "baseline.json"
            source.write_text("import re\n", encoding="utf-8")
            update(None, baseline, root)
            source.write_text(
                'import re\nif re.match("x", value):\n    pass\n', encoding="utf-8"
            )
            self.assertEqual(update(None, baseline, root), 2)

    def test_decrease_updates_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "services" / "orchestrator" / "sample.py"
            source.parent.mkdir(parents=True)
            baseline = root / "baseline.json"
            source.write_text(
                'import re\nif re.match("x", value):\n    pass\n', encoding="utf-8"
            )
            update(None, baseline, root)
            source.write_text("import re\n", encoding="utf-8")
            update(None, baseline, root)
            data = json.loads(baseline.read_text(encoding="utf-8"))
            self.assertEqual(data["totals"]["regex"], 0)
            self.assertEqual(data["raised"], [])

    def test_repository_totals_are_not_above_baseline(self) -> None:
        passed, failures = check()
        self.assertTrue(passed, failures)


if __name__ == "__main__":
    unittest.main()
