"""Count lexical rules in harness code and enforce a downward baseline."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tools" / "harness_rules.json"
SCANNED = (ROOT / "services" / "orchestrator", ROOT / "packages")
KINDS = ("regex", "word_tables", "language_tables")
REGEX_MODULES = {"re", "regex"}
REGEX_FUNCTIONS = {
    "compile",
    "search",
    "match",
    "fullmatch",
    "findall",
    "finditer",
    "sub",
    "subn",
    "split",
}
COLLECTION_CALLS = {"frozenset", "set", "tuple", "dict"}
LANGUAGE_CODE = re.compile(r"^[a-z]{2}(?:[-_][A-Za-z]{2,4})?$")


@dataclass(frozen=True)
class FileCounts:
    regex: int
    word_tables: int
    language_tables: int


def _qualified_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_qualified_name(node.value)}.{node.attr}"
    return ""


def _string_literals(node: ast.AST) -> int:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return 1
    if isinstance(node, (ast.Tuple, ast.List, ast.Set, ast.Dict)):
        values = node.elts if not isinstance(node, ast.Dict) else node.values
        return sum(_string_literals(value) for value in values)
    if isinstance(node, ast.IfExp):
        return _string_literals(node.body) + _string_literals(node.orelse)
    if isinstance(node, ast.Call) and _qualified_name(node.func) in COLLECTION_CALLS:
        return sum(_string_literals(argument) for argument in node.args)
    return 0


def _is_word_table(node: ast.AST) -> bool:
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return _string_literals(node) >= 3
    if isinstance(node, ast.Call) and _qualified_name(node.func) in COLLECTION_CALLS:
        return _string_literals(node) >= 3
    return False


def _language_keys(node: ast.Dict) -> int:
    count = 0
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            normalized = key.value.lower().replace("_", "-")
            if LANGUAGE_CODE.fullmatch(normalized):
                count += 1
    return count


def _assignment_value(node: ast.stmt) -> ast.AST | None:
    if isinstance(node, ast.Assign):
        return node.value
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return node.value
    if isinstance(node, ast.AugAssign):
        return node.value
    return None


def count_source(source: str) -> FileCounts:
    tree = ast.parse(source)
    regex = 0
    word_tables = 0
    language_tables = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _qualified_name(node.func)
            if (
                name.partition(".")[0] in REGEX_MODULES
                and name.partition(".")[2] in REGEX_FUNCTIONS
            ):
                regex += 1
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            value = _assignment_value(node)
            if value is not None and _is_word_table(value):
                word_tables += 1
        if isinstance(node, ast.Dict) and _language_keys(node) >= 2:
            language_tables += 1
    return FileCounts(regex, word_tables, language_tables)


def count_tree(root: Path = ROOT) -> dict[str, FileCounts]:
    counts: dict[str, FileCounts] = {}
    for directory in (root / "services" / "orchestrator", root / "packages"):
        if not directory.exists():
            continue
        for path in sorted(directory.rglob("*.py")):
            relative = path.relative_to(root).as_posix()
            counts[relative] = count_source(path.read_text(encoding="utf-8"))
    return counts


def totals(counts: dict[str, FileCounts]) -> dict[str, int]:
    return {
        kind: sum(getattr(value, kind) for value in counts.values()) for kind in KINDS
    }


def _baseline_data(counts: dict[str, FileCounts]) -> dict[str, object]:
    return {
        "totals": totals(counts),
        "files": {name: vars(value) for name, value in counts.items()},
        "raised": [],
    }


def _growth_report(
    current: dict[str, FileCounts], baseline: dict[str, dict[str, object]]
) -> list[str]:
    previous_files = baseline.get("files", {})
    lines = []
    for kind in KINDS:
        for filename, value in current.items():
            old = int(previous_files.get(filename, {}).get(kind, 0))  # type: ignore[attr-defined]
            if getattr(value, kind) > old:
                lines.append(f"{filename}: {kind} {old} -> {getattr(value, kind)}")
    return lines


def check(baseline_path: Path = BASELINE, root: Path = ROOT) -> tuple[bool, list[str]]:
    current = count_tree(root)
    baseline: dict[str, dict[str, object]] = json.loads(
        baseline_path.read_text(encoding="utf-8")
    )
    current_totals = totals(current)
    baseline_totals = baseline.get("totals", {})
    grown = _growth_report(current, baseline)
    failures = [
        f"{kind} {baseline_totals.get(kind, 0)} -> {value}"
        for kind, value in current_totals.items()
        if value > int(baseline_totals.get(kind, 0))  # type: ignore[call-overload]
    ]
    return not failures, failures + grown


def update(
    reason: str | None, baseline_path: Path = BASELINE, root: Path = ROOT
) -> int:
    current = count_tree(root)
    old = (
        json.loads(baseline_path.read_text(encoding="utf-8"))
        if baseline_path.exists()
        else {}
    )
    old_totals = old.get("totals", {})
    new_totals = totals(current)
    raised = old.get("raised", [])
    baseline_exists = baseline_path.exists()
    for kind in KINDS:
        if new_totals[kind] > old_totals.get(kind, 0) and baseline_exists:
            if not reason:
                print(f"error: --reason is required to raise {kind}", file=sys.stderr)
                return 2
            raised.append(
                {
                    "date": date.today().isoformat(),
                    "kind": kind,
                    "from": old_totals.get(kind, 0),
                    "to": new_totals[kind],
                    "reason": reason,
                }
            )
    data = _baseline_data(current)
    data["raised"] = raised
    baseline_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--reason")
    args = parser.parse_args()
    if args.update:
        return update(args.reason)
    current = count_tree()
    current_totals = totals(current)
    print(json.dumps(current_totals, indent=2, sort_keys=True))
    for kind in KINDS:
        largest = sorted(
            current.items(), key=lambda item: (-getattr(item[1], kind), item[0])
        )[:10]
        print(f"\n{kind}:")
        for filename, value in largest:
            print(f"  {getattr(value, kind):4}  {filename}")
    if args.check:
        passed, failures = check()
        if not passed:
            print("\nharness rules increased:", file=sys.stderr)
            print("\n".join(failures), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
