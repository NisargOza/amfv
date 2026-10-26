# PDF/OCR diagnostic summary

The percentages below use source-checked clinical passages and the 26 recommendation rows in the HIV table.
They do not establish clinical correctness outside this small sample.

| Backend | Result | Critical facts | Passage word similarity | HIV recommendation text >=90% | Correctly linked table rows | Time | Throughput |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| native | ok | 13/14 (92.9%) | 99.5% | 26/26 (100.0%) | 0/26 (0.0%) | 0.046s | 260.87 pages/s |
| tesseract | ok | 14/14 (100.0%) | 100.0% | 26/26 (100.0%) | 0/26 (0.0%) | 23.875s | 0.50 pages/s |
| docling | skipped | - | - | - | - | - | - |
| doctr | ok | 12/14 (85.7%) | 98.3% | 25/26 (96.2%) | 0/26 (0.0%) | 53.787s | 0.22 pages/s |
| lightonocr-2 | skipped | - | - | - | - | - | - |
| paddleocr-v6 | ok | 14/14 (100.0%) | 100.0% | 26/26 (100.0%) | 0/26 (0.0%) | 365.327s | 0.03 pages/s |
| paddleocr-vl-1.6 | skipped | - | - | - | - | - | - |
| firecrawl-auto | skipped | - | - | - | - | - | - |
| firecrawl-ocr | skipped | - | - | - | - | - | - |

Skipped backends:
- **docling:** The AMFV Docling exporter requires the WHO scraper PR.
- **lightonocr-2:** Full-precision LightOnOCR-2 needs more than the detected 8.0 GiB; an observed first-page attempt exceeded 9 GiB of swap.
- **paddleocr-vl-1.6:** PaddleOCR-VL was not started on the detected 8.0 GiB host after another 1B-parameter vision-language OCR model exceeded 9 GiB of swap.
- **firecrawl-auto:** FIRECRAWL_API_KEY is not configured.
- **firecrawl-ocr:** FIRECRAWL_API_KEY is not configured.

## Plain-language findings

- **Fastest on this run:** native processed 12 pages in 0.046 seconds.
- **Fastest raster OCR:** tesseract processed 12 pages in 23.875 seconds.
- **Speed versus Tesseract:** Tesseract was 2.3x as fast as doctr (55.6% less elapsed time).
- **Speed versus Tesseract:** Tesseract was 15.3x as fast as paddleocr-v6 (93.5% less elapsed time).

A backend can recover all recommendation text while still losing table relationships. The linked-row
score therefore requires the date, recommendation and status to remain together in one structured row.
