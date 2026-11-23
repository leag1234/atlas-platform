"""document_quality.py FILE.docx|FILE.pdf...: score the form of document pages.

The class of defect: a six-section report delivered on six pages, each about
95 % empty, because a page break preceded every heading (staging -150,
2026-11-21); the battery measured "mean empty 0.956" and passed. For each
file this prints one JSON line: pages, the pages its content needs, the
filled share of each page (packages/doc_render/form.py, measured on the PDF
LibreOffice prints), the mean empty share of the rendered pages (share of the
dominant colour, as deck_quality.py measures slides), and the reasons it
fails: a nearly empty page that is not the last, more pages than the content
needs, a heading at a page foot. A DOCX is printed with `soffice`; the page
pictures need `pdftoppm`. Without them a DOCX is not scored.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from deck_quality import converted, empty_share  # noqa: E402


def score(path: Path) -> dict[str, object]:
    from packages.doc_render import form

    result: dict[str, object] = {"document": path.name}
    with tempfile.TemporaryDirectory(prefix="document-quality-") as directory:
        work = Path(directory)
        if path.suffix == ".pdf":
            pdf = path
        elif shutil.which("soffice"):
            pdf = converted(path, work)
        else:
            result["error"] = "soffice is not installed"
            return result
        measured = form.measure(pdf)
        result.update(form.summary(measured))
        shares: list[float] = []
        if shutil.which("pdftoppm"):
            subprocess.run(
                ["pdftoppm", "-r", "30", "-png", str(pdf), str(work / "page")],
                check=True,
                timeout=120,
            )
            pngs = sorted(
                work.glob("page-*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[1])
            )
            shares = [empty_share(png)[0] for png in pngs]
        result["mean_white"] = round(sum(shares) / len(shares), 3) if shares else None
        result["fail_reasons"] = form.findings(measured)
    return result


def main(arguments: list[str]) -> int:
    failed = 0
    for name in arguments:
        scored = score(Path(name))
        failed += bool(scored.get("fail_reasons") or scored.get("error"))
        print(json.dumps(scored, ensure_ascii=False))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
