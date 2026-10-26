"""Compare PDF text, Docling, raster OCR and Firecrawl on the same WHO excerpts.

Run from the workspace root with `uv run --group ocr python
datasets/benchmarks/pdf_ocr.py --output-dir /tmp/amfv-pdf-results`.
This is a small diagnostic, not a character-error-rate or model leaderboard.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

import httpx
from pdf_ocr_metrics import score_output
from pdf_ocr_models import (
    HAS_DOCTR,
    HAS_LIGHTON,
    HAS_PADDLEOCR,
    HAS_PADDLEOCR_VL,
    LIGHTON_RUNTIME_REASON,
    PADDLEOCR_VL_RUNTIME_REASON,
    run_doctr,
    run_lighton,
    run_paddleocr_v6,
    run_paddleocr_vl,
)

try:
    import pypdfium2 as pdfium

    HAS_PDFIUM = True
except ImportError:
    HAS_PDFIUM = False

try:
    from amfv_datasets.scraping.pdf import HAS_DOCLING as HAS_AMFV_DOCLING
    from amfv_datasets.scraping.pdf import pdf_to_markdown

    HAS_AMFV_PDF = True
except ImportError:
    HAS_AMFV_PDF = False
    HAS_AMFV_DOCLING = False

_FIXTURES = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "pdf"
_MANIFEST = Path(__file__).with_name("pdf_ocr_manifest.json")
_BACKENDS = (
    "native",
    "tesseract",
    "docling",
    "doctr",
    "lightonocr-2",
    "paddleocr-v6",
    "paddleocr-vl-1.6",
    "firecrawl-auto",
    "firecrawl-ocr",
)


def _native_text(path: Path) -> str:
    with pdfium.PdfDocument(path) as document:
        pages: list[str] = []
        for page in document:
            text_page = page.get_textpage()
            try:
                pages.append(text_page.get_text_range())
            finally:
                text_page.close()
                page.close()
        return "\n\n".join(pages)


def _tesseract_text(path: Path, *, dpi: int, page_segmentation: int) -> str:
    with tempfile.TemporaryDirectory(prefix="amfv-ocr-") as directory, pdfium.PdfDocument(path) as document:
        pages: list[str] = []
        for index, page in enumerate(document, start=1):
            image_path = Path(directory) / f"page-{index}.png"
            bitmap = page.render(scale=dpi / 72)
            try:
                bitmap.to_pil().save(image_path)
            finally:
                bitmap.close()
                page.close()
            result = subprocess.run(
                ["tesseract", str(image_path), "stdout", "-l", "eng", "--psm", str(page_segmentation)],
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            pages.append(result.stdout)
        return "\n\n".join(pages)


def _firecrawl_text(document: dict[str, Any], *, mode: str, api_key: str) -> tuple[str, dict[str, Any]]:
    """Check remote input identity before sending a public fixture URL for parsing."""
    url = document["public_fixture_url"]
    with httpx.Client(timeout=240, follow_redirects=True) as client:
        source = client.get(url)
        source.raise_for_status()
        actual_hash = hashlib.sha256(source.content).hexdigest()
        if actual_hash != document["sha256"]:
            raise ValueError(f"Remote fixture differs from the local PDF: {document['filename']}")
        response = client.post(
            "https://api.firecrawl.dev/v2/scrape",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "url": url,
                "formats": ["markdown"],
                "parsers": [{"type": "pdf", "mode": mode, "maxPages": document["pages"], "pages": True}],
                "timeout": 180000,
                "maxAge": 0,
                "onlyMainContent": False,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError("Firecrawl returned an unsuccessful scrape; inspect the API dashboard")
        data = payload.get("data", {})
        markdown = data.get("markdown", "")
        if not markdown.strip():
            raise RuntimeError("Firecrawl returned no markdown; the PDF comparison is incomplete")
        return markdown, {"mode": mode, "response": payload}


def _summary_markdown(report: dict[str, Any]) -> str:
    """Create a lay summary without combining accuracy and speed into one opaque score."""
    aggregates: dict[str, dict[str, Any]] = {}
    for result in report["results"]:
        backend = result["backend"]
        aggregate = aggregates.setdefault(
            backend,
            {
                "statuses": [],
                "reasons": [],
                "documents": 0,
                "pages": 0,
                "seconds": 0.0,
                "facts": 0,
                "facts_total": 0,
                "similarity_weighted_total": 0.0,
                "similarity_reference_words": 0,
                "recommendations": 0,
                "recommendations_total": 0,
                "linked_rows": 0,
                "linked_rows_total": 0,
            },
        )
        aggregate["statuses"].append(result["status"])
        if result["status"] != "ok":
            if reason := result.get("reason"):
                aggregate["reasons"].append(reason)
            continue
        aggregate["documents"] += 1
        aggregate["pages"] += result["pages"]
        aggregate["seconds"] += result["seconds"]
        metrics = result["metrics"]
        fact_total = metrics["critical_facts_total"]
        aggregate["facts"] += metrics["critical_facts_found"]
        aggregate["facts_total"] += fact_total
        reference_words = metrics["critical_passage_reference_words"]
        aggregate["similarity_weighted_total"] += metrics["critical_passage_word_similarity_percent"] * reference_words
        aggregate["similarity_reference_words"] += reference_words
        if "table_rows_total" in metrics:
            aggregate["recommendations"] += metrics["table_recommendations_found"]
            aggregate["recommendations_total"] += metrics["table_rows_total"]
            aggregate["linked_rows"] += metrics["table_rows_linked"]
            aggregate["linked_rows_total"] += metrics["table_rows_total"]

    lines = [
        "# PDF/OCR diagnostic summary",
        "",
        "The percentages below use source-checked clinical passages and the 26 recommendation rows in the HIV table.",
        "They do not establish clinical correctness outside this small sample.",
        "",
        "| Backend | Result | Critical facts | Passage word similarity | HIV recommendation text >=90% | "
        "Correctly linked table rows | Time | Throughput |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for backend, aggregate in aggregates.items():
        if aggregate["documents"] == 0:
            status = " / ".join(sorted(set(aggregate["statuses"])))
            lines.append(f"| {backend} | {status} | - | - | - | - | - | - |")
            continue
        fact_percent = aggregate["facts"] / aggregate["facts_total"] * 100
        similarity = aggregate["similarity_weighted_total"] / aggregate["similarity_reference_words"]
        recommendations = (
            f"{aggregate['recommendations']}/{aggregate['recommendations_total']} "
            f"({aggregate['recommendations'] / aggregate['recommendations_total'] * 100:.1f}%)"
            if aggregate["recommendations_total"]
            else "n/a"
        )
        linked = (
            f"{aggregate['linked_rows']}/{aggregate['linked_rows_total']} "
            f"({aggregate['linked_rows'] / aggregate['linked_rows_total'] * 100:.1f}%)"
            if aggregate["linked_rows_total"]
            else "n/a"
        )
        throughput = aggregate["pages"] / aggregate["seconds"]
        lines.append(
            f"| {backend} | ok | {aggregate['facts']}/{aggregate['facts_total']} ({fact_percent:.1f}%) | "
            f"{similarity:.1f}% | {recommendations} | {linked} | {aggregate['seconds']:.3f}s | "
            f"{throughput:.2f} pages/s |"
        )

    skipped = {name: values for name, values in aggregates.items() if not values["documents"]}
    if skipped:
        lines.extend(["", "Skipped backends:"])
        for name, values in skipped.items():
            reasons = "; ".join(dict.fromkeys(values["reasons"])) or "no successful result"
            lines.append(f"- **{name}:** {reasons}.")

    successful = {name: values for name, values in aggregates.items() if values["documents"]}
    if successful:
        fastest_name, fastest = min(successful.items(), key=lambda item: item[1]["seconds"])
        lines.extend(
            [
                "",
                "## Plain-language findings",
                "",
                f"- **Fastest on this run:** {fastest_name} processed {fastest['pages']} pages in "
                f"{fastest['seconds']:.3f} seconds.",
            ]
        )
        raster_ocr = {
            name: values
            for name, values in successful.items()
            if name in {"tesseract", "doctr", "lightonocr-2", "paddleocr-v6", "paddleocr-vl-1.6"}
        }
        if raster_ocr:
            fastest_ocr_name, fastest_ocr = min(raster_ocr.items(), key=lambda item: item[1]["seconds"])
            lines.append(
                f"- **Fastest raster OCR:** {fastest_ocr_name} processed {fastest_ocr['pages']} pages in "
                f"{fastest_ocr['seconds']:.3f} seconds."
            )
        tesseract = successful.get("tesseract")
        if tesseract:
            for name, values in raster_ocr.items():
                if name == "tesseract":
                    continue
                ratio = values["seconds"] / tesseract["seconds"]
                if ratio > 1:
                    time_reduction = (values["seconds"] - tesseract["seconds"]) / values["seconds"] * 100
                    lines.append(
                        f"- **Speed versus Tesseract:** Tesseract was {ratio:.1f}x as fast as {name} "
                        f"({time_reduction:.1f}% less elapsed time)."
                    )
                else:
                    lines.append(f"- **Speed versus Tesseract:** {name} was {1 / ratio:.1f}x as fast.")

    lines.extend(
        [
            "",
            "A backend can recover all recommendation text while still losing table relationships. The linked-row",
            "score therefore requires the date, recommendation and status to remain together in one structured row.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    """Run selected backends and retain raw outputs, provenance and anchor checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=_BACKENDS, nargs="+", default=["native", "tesseract"])
    parser.add_argument("--document", action="append", help="Limit the run to one or more manifest filenames")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--psm", type=int, choices=range(14), default=3)
    args = parser.parse_args()
    if args.dpi < 72:
        parser.error("--dpi must be at least 72")

    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    documents = manifest["documents"]
    if args.document:
        requested = set(args.document)
        known = {document["filename"] for document in documents}
        if unknown := requested - known:
            parser.error(f"unknown --document value(s): {', '.join(sorted(unknown))}")
        documents = [document for document in documents if document["filename"] in requested]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    report: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "methodology": manifest["methodology"],
        "backends": args.backend,
        "raster_dpi": args.dpi,
        "tesseract_psm": args.psm,
        "documents": [document["filename"] for document in documents],
        "results": results,
    }
    report_path = args.output_dir / "results.json"
    failed = False
    for document in documents:
        path = _FIXTURES / document["filename"]
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != document["sha256"]:
            raise ValueError(f"Local fixture changed; review and update the manifest: {path}")
        for backend in args.backend:
            result: dict[str, Any] = {
                "document": document["filename"],
                "sha256": actual_hash,
                "pages": document["pages"],
                "backend": backend,
            }
            results.append(result)
            api_key = os.environ.get("FIRECRAWL_API_KEY")
            if backend.startswith("firecrawl-") and not api_key:
                result.update(status="skipped", reason="FIRECRAWL_API_KEY is not configured")
            elif backend == "tesseract" and shutil.which("tesseract") is None:
                result.update(status="skipped", reason="Install the Tesseract executable and English language data")
            elif backend in {"native", "tesseract"} and not HAS_PDFIUM:
                result.update(status="skipped", reason="Install the raster dependencies with uv sync --group ocr")
            elif backend == "docling" and not HAS_AMFV_PDF:
                result.update(status="skipped", reason="The AMFV Docling exporter requires the WHO scraper PR")
            elif backend == "docling" and not HAS_AMFV_DOCLING:
                result.update(status="skipped", reason="Install WHO's PDF dependencies with uv sync --group pdf")
            elif backend == "doctr" and not HAS_DOCTR:
                result.update(status="skipped", reason="Install the local model with uv sync --group ocr-doctr")
            elif backend == "lightonocr-2" and LIGHTON_RUNTIME_REASON:
                result.update(status="skipped", reason=LIGHTON_RUNTIME_REASON)
            elif backend == "lightonocr-2" and not HAS_LIGHTON:
                result.update(status="skipped", reason="Install the local model with uv sync --group ocr-lighton")
            elif backend == "paddleocr-v6" and not HAS_PADDLEOCR:
                result.update(
                    status="skipped",
                    reason="Install PaddleOCR and PaddlePaddle in the isolated environment; see PDF_OCR_REVIEW.md",
                )
            elif backend == "paddleocr-vl-1.6" and PADDLEOCR_VL_RUNTIME_REASON:
                result.update(status="skipped", reason=PADDLEOCR_VL_RUNTIME_REASON)
            elif backend == "paddleocr-vl-1.6" and not HAS_PADDLEOCR_VL:
                result.update(
                    status="skipped",
                    reason="Install PaddleOCR-VL in its isolated Apple Silicon environment; see PDF_OCR_REVIEW.md",
                )
            else:
                start = time.perf_counter()
                try:
                    details: dict[str, Any] | None = None
                    if backend == "native":
                        text = _native_text(path)
                        result["version"] = version("pypdfium2")
                    elif backend == "tesseract":
                        text = _tesseract_text(path, dpi=args.dpi, page_segmentation=args.psm)
                        result["version"] = subprocess.check_output(["tesseract", "--version"], text=True).splitlines()[
                            0
                        ]
                    elif backend == "docling":
                        text = pdf_to_markdown(path.read_bytes(), running_header=document["title"], name=path.name)
                        result["version"] = version("docling")
                        result["ocr"] = False
                        result["export"] = "AMFV markdown export, including footnotes"
                        result["accelerator"] = "Docling default auto"
                        result["table_structure"] = True
                    elif backend == "doctr":
                        text, details, metadata = run_doctr(path)
                        result.update(metadata)
                    elif backend == "lightonocr-2":
                        text, details, metadata = run_lighton(path)
                        result.update(metadata)
                    elif backend == "paddleocr-v6":
                        text, details, metadata = run_paddleocr_v6(path)
                        result.update(metadata)
                    elif backend == "paddleocr-vl-1.6":
                        text, details, metadata = run_paddleocr_vl(path)
                        result.update(metadata)
                    else:
                        text, details = _firecrawl_text(
                            document, mode=backend.removeprefix("firecrawl-"), api_key=api_key
                        )
                        result["version"] = "hosted API v2; model version not pinned"
                    if details is not None:
                        details_path = args.output_dir / f"{path.stem}.{backend}.raw.json"
                        details_path.write_text(
                            json.dumps(details, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
                        )
                        result["raw_output"] = details_path.name
                    if not text.strip():
                        raise ValueError("No readable output")
                    backend_seconds = round(time.perf_counter() - start, 3)
                    output_name = f"{path.stem}.{backend}.md"
                    output_path = args.output_dir / output_name
                    output_path.write_text(text, encoding="utf-8")
                    scoring_start = time.perf_counter()
                    metrics = score_output(text, document)
                    scoring_seconds = round(time.perf_counter() - scoring_start, 3)
                    checks = metrics.pop("anchors")
                    result.update(
                        status="ok",
                        seconds=backend_seconds,
                        scoring_seconds=scoring_seconds,
                        characters=len(text),
                        output=output_name,
                        output_sha256=hashlib.sha256(output_path.read_bytes()).hexdigest(),
                        anchors=checks,
                        anchors_found=sum(check["present"] for check in checks),
                        anchors_total=len(checks),
                        metrics=metrics,
                    )
                except Exception as exc:  # noqa: BLE001 - keep completed backend evidence when another backend fails
                    failed = True
                    result.update(status="error", reason=str(exc), seconds=round(time.perf_counter() - start, 3))
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"{path.name}: {backend}: {result['status']}", flush=True)
    (args.output_dir / "summary.md").write_text(_summary_markdown(report), encoding="utf-8")
    return int(failed)


__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
