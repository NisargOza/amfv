# PDF extraction and OCR review

Initial diagnostic, October 6, 2026; local CPU-model extension, October 25,
2026. The Firecrawl versus FinePDFs comparison is **not complete**. Firecrawl
was skipped because `FIRECRAWL_API_KEY` is unset. FinePDFs was inspected but its
GPU OCR pipeline was not executed. “finepdf” is provisionally interpreted as
Hugging Face FinePDFs, pending user confirmation.

## Measured sample and method

Two committed English, born-digital WHO excerpts, six pages each:

- Bloodstream infections, publication `9789240121805`, source PDF pages 30-35.
- HIV service delivery, publication `9789240124233`, source PDF pages 11-16.

The fixtures, original URLs and reuse notes are documented in
[fixture attribution](../test/fixtures/pdf/README.md). Raw outputs are
research derivatives of the WHO sources and carry the source reuse conditions;
the repository's code licence does not replace those conditions.

Hardware: Apple M4 MacBook Air, 16 GB RAM; macOS 26.2, Python 3.13.15. Docling
used its default automatic accelerator selection, not a forced CPU benchmark.
Its model weights were already cached by the integration tests. Each elapsed
time covers conversion/export; Tesseract times include 300-DPI rendering and
all six page OCR calls. These are single runs, not stable throughput estimates.

Native extraction used PDFium's text layer. Tesseract received page PNGs only,
with English language data and automatic page segmentation (`--psm 3`). Docling
used OCR off and table structure/cell matching on. Rasterizing born-digital PDFs
tests an OCR path; it does not substitute for naturally scanned/degraded PDFs.

Seven source-checked anchors per excerpt cover recommendation wording,
population, a negation, concentration/risk numbers, age-dependent certainty,
intervals, task sharing and a date-column footnote. Relevant source pages were
rendered and checked visually. The checks normalize whitespace and hyphenated
line breaks. Anchor presence is not a clinical accuracy, recall, CER or WER
score, and it does not establish table alignment or absence of hallucinations.

The extension adds local word-sequence similarity for those 14 passages and
checks all 26 dated recommendations in the two-page HIV table. Recommendation
text receives credit at 90% or greater word similarity. Table linkage is a
separate, stricter score: date, recommendation and status must remain together
in one Markdown or HTML row. These checks make near-misses and destroyed table
relationships visible, but they are still not full-document CER/WER or a
clinical-accuracy score.

## Results

| Backend | Version/configuration | CVC anchors | HIV anchors | Seconds: CVC / HIV |
| --- | --- | --- | --- | --- |
| Native PDF text | pypdfium2 5.14.0 | 7/7 | 6/7 | 0.024 / 0.026 |
| Tesseract | 5.5.3, English, 300 DPI, PSM 3 | 7/7 | 7/7 | 17.029 / 13.848 |
| Docling before footnote fix | 2.134.0, default markdown export | 7/7 | 6/7 | 12.787 / 5.156 |
| AMFV Docling after footnote fix | 2.134.0, missing footnotes retained with page provenance | 7/7 | 7/7 | 11.282 / 5.035 |
| Firecrawl auto and forced OCR | Hosted API | skipped | skipped | not measured |
| FinePDFs | Published full pipeline | not run | not run | not measured |

The native HIV miss is a text-layer artefact: `high-certainty` contains U+FFFE
in place of the hyphen. Both certainty populations remain readable in the raw
output, but exact matching fails. This is not an omitted recommendation.

Docling preserved all 26 dated recommendation rows in the two-page HIV table,
including their date/status columns. Their order and the 2025 Updated/New
statuses were checked against the source rendering. Native output separates
status values into a block after the recommendation text, which weakens their
row association. Tesseract's anchor success alone does not prove a usable
three-column table: its output is unstructured text, groups statuses separately,
and omits the `New` status for the 2025 mental-health recommendation on page 2.

The default Docling export omitted the footnote defining the date column.
The structured conversion still contained that footnote. AMFV now appends
detected footnotes missing from markdown under `PDF footnotes`, with PDF page
numbers. The HIV footnote retains its source wording and page 2 provenance.
The real-PDF test and refreshed reference output check this behavior. Table
captions, certainty phrases and recommendation rows remain present.

Results and raw markdown:

- [Initial comparison](results/2026-10-06/results.json)
- [Final Docling export](results/2026-10-06/footnotes-preserved/results.json)
- [Manifest and source hashes](pdf_ocr_manifest.json)
- [Reproducible runner](pdf_ocr.py)

The five-document Docling/Marker/PyMuPDF4LLM results in the original
[WHO PR #6](https://github.com/MedARC-AI/amfv/pull/6) came from that author.
They were not reproduced in this update.

## CPU model extension

The October 25 run used an Apple M2 with 8 GiB RAM, macOS 15.2 and Python
3.13.2. Model weights were already cached, but each backend was initialized
once inside the timed 12-page run. The first document therefore includes cold
model initialization and the second benefits from the loaded model. Timings
are single-run observations, not stable throughput claims.

| Backend | Checked facts | Passage word similarity | HIV recommendation text | Linked HIV rows | 12-page time / throughput |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native PDF text | 13/14 (92.9%) | 99.5% | 26/26 (100%) | 0/26 | 0.046 s / 260.87 pages/s |
| Tesseract 5.5.3 | 14/14 (100%) | 100% | 26/26 (100%) | 0/26 | 23.875 s / 0.50 pages/s |
| docTR 1.1.0, FAST + CRNN | 12/14 (85.7%) | 98.3% | 25/26 (96.2%) | 0/26 | 53.787 s / 0.22 pages/s |
| PP-OCRv6, medium detector + recognizer | 14/14 (100%) | 100% | 26/26 (100%) | 0/26 | 365.327 s / 0.03 pages/s |
| Reviewed AMFV Docling export from October 6 | 14/14 (100%) | 100% | 26/26 (100%) | 26/26 (100%) | not rerun on this host |

On these clean, born-digital excerpts, PP-OCRv6 did not improve checked text
accuracy over Tesseract. Tesseract produced the same 14/14 facts and 26/26 HIV
recommendations while using **93.5% less elapsed time** (15.3 times as fast).
Tesseract also used **55.6% less elapsed time** than docTR (2.3 times as fast),
while finding two more exact facts and one more HIV recommendation above the
90% similarity threshold. This does not establish that Tesseract will win on
scanned, skewed or degraded documents; that needs a separate fixture set.

The most important quality result is structural: native text and all three
raster OCR engines recovered most or all recommendation wording but linked
**0 of 26** date/recommendation/status rows. Rescoring the retained AMFV Docling
export linked **26 of 26**. The practical workflow is therefore native text or
Tesseract for inexpensive text recovery, plus a layout/table exporter when
row relationships matter. A 100% text score alone is not a usable table.

LightOnOCR-2-1B was loaded in full precision and attempted on the first page,
but the process exceeded 9 GiB of swap and was stopped before producing a page.
That is a hardware-limit observation, not an accuracy result. PaddleOCR-VL 1.6
was not started after that failure. The runner now records explicit skips for
both vision-language backends on hosts with less than 12 GiB RAM.

Raw output, model metadata, source hashes and per-check details are retained in
[the October 25 evidence](results/2026-10-25/). The summary is intended for
quick review; `results.json` is the machine-readable record.

## Firecrawl and FinePDFs status

The runner supports Firecrawl API v2 `auto` and forced `ocr` modes, per its
[official API guide](https://github.com/firecrawl/firecrawl-docs/blob/84e2b419134e753e6f22ac1f3fcb713256154487/advanced-scraping-guide.mdx).
It verifies that the public fixture URL has exactly the local PDF's SHA-256
before submitting it. Only the six-page public excerpts are configured. Missing
credentials produce an explicit skipped result. No Firecrawl request was sent.
Its hosted model revision is not pinned, so retain the API response metadata.

[FinePDFs](https://github.com/huggingface/finepdfs/tree/15aa381fae828c8acd6efa058fa0b41d2c602ede)
is a corpus-processing pipeline, not a drop-in equivalent of the Firecrawl API.
Its published code uses modified vendored Docling for embedded text and
`reducto/RolmOCR` served by vLLM for OCR. Its embedded-text extractor disables
table structure, unlike this AMFV configuration. A stock Docling run therefore
cannot be reported as a FinePDFs result. Its top-level code is AGPL-3.0; this
update did not copy it into the AMFV package.

No NVIDIA/vLLM inference host or remote OCR endpoint was provided for this Mac.
The next comparison needs the confirmed intended “finepdf” project, Firecrawl
credentials and actual FinePDFs outputs from the same hashed excerpts. A larger
test should also include naturally scanned pages and manually transcribed
ground truth, with table-cell/date/status associations and numeric/negation
errors graded separately.

## OCR candidates and tested challengers

The measured entries below are results only for these two clean excerpts. The
remaining entries are candidates for a larger controlled comparison:

| Candidate | Role | Evidence/status |
| --- | --- | --- |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | Local CPU baseline for scanned text | Tested here; text anchors survived, table structure needs a separate parser. |
| [docTR](https://github.com/mindee/doctr) | PyTorch CPU/MPS text detector and recognizer | Tested with FAST + CRNN; near-perfect passage similarity, but slower and less exact than Tesseract here. |
| [RolmOCR](https://huggingface.co/reducto/RolmOCR) | First OCR model for a faithful FinePDFs comparison | FinePDFs' published OCR model; Apache-2.0 model card. Not run here. |
| [PaddleOCR / PP-StructureV3 / PaddleOCR-VL](https://github.com/PaddlePaddle/PaddleOCR) | Challenger for layout/table-rich guideline pages | PP-OCRv6 text OCR was tested; it matched Tesseract's checked text but was 15.3 times slower. Structure/VL remains untested on this host. |
| [LightOnOCR-2](https://huggingface.co/lightonai/LightOnOCR-2-1B) | Compact vision-language OCR candidate | Attempted but did not complete on 8 GiB RAM; requires a larger-memory host for a valid accuracy result. |
| [olmOCR](https://github.com/allenai/olmocr) | Independent document-OCR challenger | Apache-2.0; supports local NVIDIA inference or a remote server. Not run here. |

Start with RolmOCR for FinePDFs fidelity, PaddleOCR's structure pipeline for
table quality, and Tesseract as the inexpensive baseline. Keep Firecrawl's
hosted parser as a separate backend rather than assuming its OCR model is
interchangeable with these candidates. Use native extraction for clean text
layers when its order and encoding survive source checks.

## Running the diagnostic

New local runs under `results/` are ignored by default. The reviewed
`results/2026-10-06/` evidence is deliberately retained in version control.
Use a new output directory when rerunning an experiment to preserve that evidence.

```bash
uv sync --dev --group ocr
uv run --group ocr python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-pdf-results --backend native tesseract
```

The optional `docling` backend uses the AMFV exporter from the separate WHO
scraper update. It records a skip until that module and its PDF dependencies
are installed. After the WHO change is merged, reproduce the final export with:

```bash
uv sync --dev --group pdf --group ocr
uv run --group pdf --group ocr python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-docling-results --backend docling
```

The initial default-export measurements are retained as historical evidence;
the runner uses the final AMFV exporter when that integration is available.

The CPU-friendly challengers are separate optional groups so the default OCR
environment stays small. docTR downloads its detector and recognizer on first
use:

```bash
uv sync --dev --group ocr --group ocr-doctr
uv run --group ocr --group ocr-doctr python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-doctr-results --backend doctr
```

PaddleOCR publishes Apple Silicon CPU wheels for its Python package, but the
PaddlePaddle runtime comes from the project's wheel index rather than PyPI.
Install it only in the benchmark environment, then use `--no-sync` so uv does
not remove that separately installed runtime:

```bash
uv sync --dev --group ocr --group ocr-paddle
uv pip install paddlepaddle==3.2.1 \
  --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/
uv run --no-sync python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-paddle-results --backend paddleocr-v6
```

LightOnOCR-2 and PaddleOCR-VL remain available runner targets, but the runner
records an explicit resource skip on hosts with less than 12 GiB RAM. That
guard was added after LightOnOCR-2's first page exceeded 9 GiB of swap on the
8 GiB test machine. The interrupted attempt is not an accuracy result.

Tesseract and its English language data must be installed separately. Tesseract
5.5.3 was installed with Homebrew for this run. PDF model weights were downloaded
to the normal cache, and FinePDFs was inspected in `/tmp/amfv-finepdfs-20261006`.

With `FIRECRAWL_API_KEY` configured securely in the process environment:

```bash
uv run --group ocr python datasets/benchmarks/pdf_ocr.py \
  --output-dir /tmp/amfv-firecrawl-results --backend firecrawl-auto firecrawl-ocr
```

The second command calls the paid hosted service when credentials are present.
The missing-key path was exercised here; live Firecrawl integration is untested.
