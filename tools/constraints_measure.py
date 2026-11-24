"""Precision and recall of the constraint reading and checkers, offline.

Fictional, hand-labelled cases (tests/fixtures/request_constraints.json) and
files built here with fictional content in several languages:

- reading: for each request, the answer-scope constraints code reads
  (services/orchestrator/constraints/phrases.py) against the labels;
  precision = constraints read that are labelled / constraints read,
  recall = labelled constraints read / labelled constraints;
- checkers: for each reply or file and constraint, whether code finds the
  violation. "Positive" = the reply breaks the constraint. Precision of a
  reported violation matters most (a false one sends a right reply to
  repair); recall is the share of violations found. Undecided verdicts are
  counted apart.

usage: python tools/constraints_measure.py [--json]
"""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.orchestrator.constraints import checks, phrases  # noqa: E402
from services.orchestrator.constraints.model import Constraint, norm  # noqa: E402
from services.orchestrator.constraints.views import answer_view, file_view  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "request_constraints.json"


def cases() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["kind"],
        norm(item.get("text") or ""),
        item.get("count"),
        item.get("relation") or ("exact" if item.get("count") is not None else None),
    )


def _found_key(item: Constraint) -> tuple[Any, ...]:
    return (
        item.kind,
        norm(item.text),
        item.count,
        item.relation if item.count is not None else None,
    )


def measure_reading(data: dict[str, Any]) -> dict[str, Any]:
    by_kind: dict[str, Counter[str]] = {}
    misses: list[str] = []
    for case in data["requests"]:
        expected = Counter(_key(item) for item in case["expect"])
        found = Counter(
            _found_key(item)
            for item in phrases.read(case["text"], 1)
            if item.scope == "answer"
        )
        for key in set(expected) | set(found):
            tally = by_kind.setdefault(key[0], Counter())
            hit = min(expected[key], found[key])
            tally["tp"] += hit
            tally["fp"] += found[key] - hit
            tally["fn"] += expected[key] - hit
            if found[key] != expected[key]:
                misses.append(
                    f"{case['lang']} {key} expected {expected[key]} found {found[key]}: {case['text'][:70]}"
                )
    return {"by_kind": by_kind, "misses": misses}


def _constraint(raw: dict[str, Any]) -> Constraint:
    return Constraint(
        kind=raw["kind"],
        scope=raw.get("scope", "answer"),
        quote="(fixture)",
        turn=1,
        text=raw.get("text", ""),
        items=tuple(raw.get("items", ())),
        count=raw.get("count"),
        relation=raw.get("relation", "exact"),
        style=raw.get("style", ""),
        region=raw.get("region", ""),
        page=raw.get("page", 0),
        each=raw.get("each", False),
        id="K1",
    )


def file_cases(directory: Path) -> list[tuple[str, Path, dict[str, Any], bool]]:
    """(name, file, constraint, met) built with fictional content."""
    from docx import Document
    from openpyxl import Workbook
    from pptx import Presentation
    from pptx.util import Inches
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    built: list[tuple[str, Path, dict[str, Any], bool]] = []
    titles = ["Seeds", "Soil", "Water"]

    def deck(
        name: str, *, bullets: bool, example_on_slide: bool, order: list[str]
    ) -> Path:
        deck = Presentation()
        for title in order:
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = title
            body = slide.placeholders[1].text_frame
            tips = [
                f"{title} tip {n}: keep the trays labelled and dry" for n in (1, 2, 3)
            ]
            if bullets:
                body.text = tips[0]
                for tip in tips[1:]:
                    body.add_paragraph().text = tip
            else:
                slide.placeholders[1]._element.getparent().remove(
                    slide.placeholders[1]._element
                )
                for index, tip in enumerate(tips):
                    box = slide.shapes.add_textbox(
                        Inches(1), Inches(2 + index), Inches(7), Inches(0.8)
                    )
                    box.text_frame.text = tip
            example = f"Example: a school garden sows {title.lower()} in April."
            if example_on_slide:
                box = slide.shapes.add_textbox(
                    Inches(1), Inches(5.5), Inches(7), Inches(0.8)
                )
                box.text_frame.text = example
            else:
                slide.notes_slide.notes_text_frame.text = example
        path = directory / name
        deck.save(str(path))
        return path

    good = deck("garden-good.pptx", bullets=True, example_on_slide=True, order=titles)
    cards = deck(
        "garden-cards.pptx", bullets=False, example_on_slide=True, order=titles
    )
    notes = deck(
        "garden-notes.pptx", bullets=True, example_on_slide=False, order=titles
    )
    shuffled = deck(
        "garden-order.pptx",
        bullets=True,
        example_on_slide=True,
        order=["Seeds", "Water", "Soil"],
    )
    built += [
        (
            "deck titles in order",
            good,
            {"kind": "titles", "scope": "file", "items": titles},
            True,
        ),
        (
            "deck titles out of order",
            shuffled,
            {"kind": "titles", "scope": "file", "items": titles},
            False,
        ),
        (
            "deck 3 bullets per slide",
            good,
            {
                "kind": "items",
                "scope": "file",
                "count": 3,
                "style": "bullets",
                "each": True,
            },
            True,
        ),
        (
            "deck tips as cards",
            cards,
            {"kind": "list_style", "scope": "file", "style": "bullets", "each": True},
            False,
        ),
        (
            "deck bullets",
            good,
            {"kind": "list_style", "scope": "file", "style": "bullets", "each": True},
            True,
        ),
        (
            "deck example on slide",
            good,
            {"kind": "contains", "scope": "file", "text": "Example:", "each": True},
            True,
        ),
        (
            "deck example in notes only",
            notes,
            {"kind": "contains", "scope": "file", "text": "Example:", "each": True},
            False,
        ),
        (
            "deck slide 2 has Soil",
            good,
            {"kind": "contains", "scope": "file", "text": "Soil tip 3", "page": 2},
            True,
        ),
        (
            "deck excludes word",
            good,
            {"kind": "excludes", "scope": "file", "text": "muddy"},
            True,
        ),
    ]

    def document(name: str, *, footer: bool, boxes: int) -> Path:
        doc = Document()
        doc.add_heading("Atelier du mardi", level=1)
        doc.add_paragraph("Rangez les pinceaux après chaque séance.")
        for n in range(boxes):
            doc.add_paragraph(f"☐ Vérifier le pot {n + 1}")
        if footer:
            doc.sections[0].footer.paragraphs[0].text = "Club de peinture du mardi"
        path = directory / name
        doc.save(str(path))
        return path

    with_footer = document("atelier-footer.docx", footer=True, boxes=4)
    no_footer = document("atelier-nofooter.docx", footer=False, boxes=3)
    built += [
        (
            "docx footer present",
            with_footer,
            {
                "kind": "contains",
                "scope": "file",
                "text": "Club de peinture du mardi",
                "region": "footer",
            },
            True,
        ),
        (
            "docx footer missing",
            no_footer,
            {
                "kind": "contains",
                "scope": "file",
                "text": "Club de peinture du mardi",
                "region": "footer",
            },
            False,
        ),
        (
            "docx 4 checkboxes",
            with_footer,
            {"kind": "items", "scope": "file", "count": 4, "style": "checkboxes"},
            True,
        ),
        (
            "docx 3 of 4 checkboxes",
            no_footer,
            {"kind": "items", "scope": "file", "count": 4, "style": "checkboxes"},
            False,
        ),
        (
            "docx starts with title",
            with_footer,
            {"kind": "starts_with", "scope": "file", "text": "Atelier du mardi"},
            True,
        ),
    ]

    def pdf(name: str, *, footer: bool, bullets: bool) -> Path:
        path = directory / name
        page = canvas.Canvas(str(path), pagesize=A4)
        for number, title in enumerate(["Montaje", "Desmontaje"], start=1):
            page.setFont("Helvetica-Bold", 18)
            page.drawString(72, 770, title)
            page.setFont("Helvetica", 11)
            for index in range(3):
                mark = "• " if bullets else ""
                page.drawString(
                    72,
                    730 - 20 * index,
                    f"{mark}Paso {index + 1} de la hoja {number}: revisar las mesas",
                )
            if footer:
                page.drawString(72, 40, "Taller de cerámica, sala 2")
            page.showPage()
        page.save()
        return path

    pdf_good = pdf("taller-good.pdf", footer=True, bullets=True)
    pdf_bad = pdf("taller-bad.pdf", footer=False, bullets=False)
    built += [
        (
            "pdf footer on each page",
            pdf_good,
            {
                "kind": "contains",
                "scope": "file",
                "text": "Taller de cerámica, sala 2",
                "region": "footer",
                "each": True,
            },
            True,
        ),
        (
            "pdf footer missing",
            pdf_bad,
            {
                "kind": "contains",
                "scope": "file",
                "text": "Taller de cerámica, sala 2",
                "region": "footer",
                "each": True,
            },
            False,
        ),
        (
            "pdf page titles",
            pdf_good,
            {"kind": "titles", "scope": "file", "items": ["Montaje", "Desmontaje"]},
            True,
        ),
        (
            "pdf bullets per page",
            pdf_good,
            {
                "kind": "items",
                "scope": "file",
                "count": 3,
                "style": "bullets",
                "each": True,
            },
            True,
        ),
        (
            "pdf no bullets",
            pdf_bad,
            {"kind": "list_style", "scope": "file", "style": "bullets", "each": True},
            False,
        ),
    ]

    page_good = directory / "zaehler-good.html"
    page_good.write_text(
        "<!doctype html><html><head><title>Wolkenzähler</title><style>b{}</style></head><body>"
        "<h1>Wolkenzähler</h1><p id=n>0</p><button>Eine Wolke dazu</button>"
        "<button>Wolke abziehen</button><script>let n=0</script></body></html>",
        encoding="utf-8",
    )
    page_bad = directory / "zaehler-bad.html"
    page_bad.write_text(
        "<html><body><h1>Wolkenzähler</h1><button>Wolke dazu</button><button>Zurück</button></body></html>",
        encoding="utf-8",
    )
    built += [
        (
            "html exact button label",
            page_good,
            {"kind": "contains", "scope": "file", "text": "Wolke abziehen"},
            True,
        ),
        (
            "html label differs",
            page_bad,
            {"kind": "contains", "scope": "file", "text": "Wolke abziehen"},
            False,
        ),
        (
            "html no script text",
            page_good,
            {"kind": "excludes", "scope": "file", "text": "let n"},
            True,
        ),
    ]

    svg_good = directory / "badge-good.svg"
    svg_good.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><circle cx="100" cy="120" r="60"/>'
        '<text x="100" y="270">PORTE NORD</text></svg>',
        encoding="utf-8",
    )
    svg_bad = directory / "badge-bad.svg"
    svg_bad.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 300"><text x="100" y="30">PORTE NORD</text>'
        '<circle cx="100" cy="170" r="60"/></svg>',
        encoding="utf-8",
    )
    built += [
        (
            "svg caption below",
            svg_good,
            {
                "kind": "contains",
                "scope": "file",
                "text": "PORTE NORD",
                "region": "footer",
            },
            True,
        ),
        (
            "svg caption on top",
            svg_bad,
            {
                "kind": "contains",
                "scope": "file",
                "text": "PORTE NORD",
                "region": "footer",
            },
            False,
        ),
    ]

    book = Workbook()
    sheet = book.create_sheet("Budget", 0)
    sheet.append(["Poste", "Montant (EUR)"])
    sheet.append(["Peinture", 12.5])
    del book[book.sheetnames[1]]
    workbook = directory / "budget.xlsx"
    book.save(str(workbook))
    built += [
        (
            "xlsx header",
            workbook,
            {"kind": "contains", "scope": "file", "text": "Montant (EUR)"},
            True,
        ),
        (
            "xlsx missing header",
            workbook,
            {"kind": "contains", "scope": "file", "text": "Montant (USD)"},
            False,
        ),
    ]
    return built


def rendered_cases(directory: Path) -> list[tuple[str, Path, dict[str, Any], bool]]:
    """Files built by Atlas's own renderer (packages/doc_render), as delivered:
    themed decks (cards, cover, footer, notes), DOCX and PDF reports."""
    from packages.doc_render import render
    from packages.doc_render.schema import Content

    def build(name: str, value: dict[str, Any]) -> Path:
        path = directory / name
        render(Content.model_validate(value), path, image_root=directory)
        return path

    tips = {
        "Semis": [
            "Semer en godets dès la fin de l'hiver, à l'abri du vent",
            "Étiqueter chaque godet avec la variété et la date",
            "Garder le terreau humide sans jamais le détremper",
        ],
        "Repiquage": [
            "Repiquer quand la plantule a deux vraies feuilles bien formées",
            "Tenir la plantule par une feuille, jamais par la tige",
            "Arroser au pied juste après le repiquage, sans mouiller",
        ],
        "Récolte": [
            "Récolter le matin, quand les feuilles sont encore fraîches",
            "Couper au couteau propre pour éviter les maladies",
            "Ranger la récolte à l'ombre dans une caisse aérée",
        ],
    }

    def deck(bulleted: bool, example_in_notes: bool) -> dict[str, Any]:
        sections = []
        for heading, bullets in tips.items():
            example = f"Exemple : le jardin partagé de la rue {heading.lower()}."
            sections.append(
                dict(
                    heading=heading,
                    bullets=bullets,
                    bulleted=bulleted,
                    notes=[example] if example_in_notes else [],
                    paragraphs=[] if example_in_notes else [example],
                )
            )
        return dict(title="Atelier potager", sections=sections)

    listed = build("potager-liste.pptx", deck(True, False))
    # Three developed points alone on a slide: drawn as cards unless bulleted.
    cards = build("potager-cartes.pptx", deck(False, True))
    notes = build("potager-notes.pptx", deck(True, True))
    titles = list(tips)
    built: list[tuple[str, Path, dict[str, Any], bool]] = [
        (
            "rendered deck: slide titles in order",
            listed,
            {"kind": "titles", "scope": "file", "items": titles},
            True,
        ),
        (
            "rendered deck: titles of another order",
            listed,
            {
                "kind": "titles",
                "scope": "file",
                "items": [titles[1], titles[0], titles[2]],
            },
            False,
        ),
        (
            "rendered deck: bulleted list on each slide",
            listed,
            {"kind": "list_style", "scope": "file", "style": "bullets", "each": True},
            True,
        ),
        (
            "rendered deck: three items on each slide",
            listed,
            {"kind": "items", "scope": "file", "count": 3, "each": True},
            True,
        ),
        (
            "rendered deck: three points drawn as cards",
            cards,
            {"kind": "list_style", "scope": "file", "style": "bullets", "each": True},
            False,
        ),
        (
            "rendered deck: example on each slide",
            listed,
            {"kind": "contains", "scope": "file", "text": "Exemple", "each": True},
            True,
        ),
        (
            "rendered deck: example only in the notes",
            notes,
            {"kind": "contains", "scope": "file", "text": "Exemple", "each": True},
            False,
        ),
        (
            "rendered deck: example in the notes, asked there",
            notes,
            {
                "kind": "contains",
                "scope": "file",
                "text": "Exemple",
                "region": "notes",
                "each": True,
            },
            True,
        ),
    ]
    report = dict(
        title="Fiches du vestiaire",
        footer="Vestiaire du gymnase",
        sections=[
            dict(
                heading="Ouverture",
                paragraphs=["Ouvrir les casiers à 8 h."],
                bullets=[
                    "[ ] Allumer la lumière",
                    "[ ] Vérifier les clés",
                    "[ ] Aérer",
                    "[ ] Noter l'heure",
                ],
            ),
            dict(
                heading="Fermeture",
                paragraphs=["Fermer à 20 h."],
                bullets=["[ ] Ramasser les serviettes", "[ ] Éteindre"],
                page_break=True,
            ),
        ],
    )
    plain = dict(report, footer=None)
    for suffix in (".docx", ".pdf"):
        with_footer = build("vestiaire" + suffix, report)
        without = build("vestiaire-sans" + suffix, plain)
        built += [
            (
                f"rendered {suffix}: running footer",
                with_footer,
                {
                    "kind": "contains",
                    "scope": "file",
                    "text": "Vestiaire du gymnase",
                    "region": "footer",
                },
                True,
            ),
            (
                f"rendered {suffix}: footer missing",
                without,
                {
                    "kind": "contains",
                    "scope": "file",
                    "text": "Vestiaire du gymnase",
                    "region": "footer",
                },
                False,
            ),
            (
                f"rendered {suffix}: section titles in order",
                with_footer,
                {
                    "kind": "titles",
                    "scope": "file",
                    "items": ["Ouverture", "Fermeture"],
                },
                True,
            ),
            (
                f"rendered {suffix}: six list items",
                with_footer,
                {"kind": "items", "scope": "file", "count": 6},
                True,
            ),
            (
                f"rendered {suffix}: four items asked on page 2",
                with_footer,
                {"kind": "items", "scope": "file", "count": 4, "page": 2},
                False,
            ),
        ]
    docx_file = directory / "vestiaire.docx"
    built.append(
        (
            "rendered .docx: boxes to tick",
            docx_file,
            {"kind": "list_style", "scope": "file", "style": "checkboxes"},
            True,
        )
    )
    return built


def _tally(rows: list[tuple[str, bool, bool | None]]) -> dict[str, Any]:
    """rows: (name, met, verdict). Positive = a violation."""
    tally: Counter[str] = Counter()
    wrong: list[str] = []
    for name, met, verdict in rows:
        if verdict is None:
            tally["undecided"] += 1
            wrong.append(f"undecided: {name}")
            continue
        violated, reported = not met, not verdict
        tally[
            "tp"
            if violated and reported
            else "fp"
            if reported
            else "fn"
            if violated
            else "tn"
        ] += 1
        if violated != reported:
            wrong.append(
                ("false violation: " if reported else "missed violation: ") + name
            )
    return {"counts": dict(tally), "wrong": wrong}


def measure_checks(data: dict[str, Any]) -> dict[str, Any]:
    rows: list[tuple[str, bool, bool | None]] = []
    started = time.perf_counter()
    for index, case in enumerate(data["answers"]):
        item = _constraint(case["constraint"])
        verdict = checks.check(
            item, answer_view(case["answer"]), {"raw": case["answer"]}
        ).passed
        rows.append((f"{case['lang']} {item.kind} #{index}", case["met"], verdict))
    answer_ms = (time.perf_counter() - started) * 1000 / max(1, len(rows))
    file_rows: list[tuple[str, bool, bool | None]] = []
    with tempfile.TemporaryDirectory() as directory:
        built = file_cases(Path(directory)) + rendered_cases(Path(directory))
        started = time.perf_counter()
        views: dict[Path, Any] = {}
        for name, path, raw, met in built:
            if path not in views:
                views[path] = file_view(path)
            view = views[path]
            verdict = (
                None if view is None else checks.check(_constraint(raw), view).passed
            )
            file_rows.append((name, met, verdict))
        file_ms = (time.perf_counter() - started) * 1000 / max(1, len(built))
    return {
        "answers": _tally(rows),
        "files": _tally(file_rows),
        "answer_ms": round(answer_ms, 2),
        "file_ms": round(file_ms, 1),
    }


def _ratio(a: int, b: int) -> str:
    return "n/a" if b == 0 else f"{a}/{b} ({100 * a / b:.0f} %)"


def main(argv: list[str]) -> int:
    data = cases()
    reading = measure_reading(data)
    checking = measure_checks(data)
    if "--json" in argv:
        print(
            json.dumps(
                {
                    "reading": {k: dict(v) for k, v in reading["by_kind"].items()},
                    "checks": checking,
                },
                ensure_ascii=False,
                indent=1,
                default=str,
            )
        )
        return 0
    total: Counter[str] = Counter()
    print("Reading (code), by kind: precision, recall")
    for kind, tally in sorted(reading["by_kind"].items()):
        total.update(tally)
        print(
            f"  {kind:12} P {_ratio(tally['tp'], tally['tp'] + tally['fp'])}  R {_ratio(tally['tp'], tally['tp'] + tally['fn'])}"
        )
    print(
        f"  {'all':12} P {_ratio(total['tp'], total['tp'] + total['fp'])}  R {_ratio(total['tp'], total['tp'] + total['fn'])}"
    )
    for miss in reading["misses"]:
        print("   miss:", miss)
    for label in ("answers", "files"):
        counts = checking[label]["counts"]
        tp, fp, fn = counts.get("tp", 0), counts.get("fp", 0), counts.get("fn", 0)
        print(
            f"Checks on {label}: violations P {_ratio(tp, tp + fp)}  R {_ratio(tp, tp + fn)}  "
            f"passes kept {counts.get('tn', 0)}  undecided {counts.get('undecided', 0)}"
        )
        for wrong in checking[label]["wrong"]:
            print("   ", wrong)
    print(
        f"Time per check: reply {checking['answer_ms']} ms, file {checking['file_ms']} ms (view built once per file)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
