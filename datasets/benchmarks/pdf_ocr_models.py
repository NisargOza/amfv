"""Optional local OCR model adapters for the PDF diagnostic."""

from __future__ import annotations

import base64
import io
import os
from functools import cache
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from typing import Any

HAS_DOCTR = find_spec("doctr") is not None and find_spec("torch") is not None
HAS_LIGHTON = find_spec("torch") is not None and find_spec("transformers") is not None
HAS_PADDLEOCR = find_spec("paddle") is not None and find_spec("paddleocr") is not None
HAS_PADDLEOCR_VL = HAS_PADDLEOCR

LIGHTON_MODEL = "lightonai/LightOnOCR-2-1B"
LIGHTON_REVISION = "be7c41313c30e940fe5571de59e5fabc779e00c8"
PADDLEOCR_VL_MODEL = "PaddlePaddle/PaddleOCR-VL-1.6"
PADDLEOCR_VL_REVISION = "c5630abae1d940eafe0697512a0325494b02ab42"


def _physical_memory_gib() -> float | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except (ValueError, OSError):
        return None


PHYSICAL_MEMORY_GIB = _physical_memory_gib()
LIGHTON_RUNTIME_REASON = (
    f"Full-precision LightOnOCR-2 needs more than the detected {PHYSICAL_MEMORY_GIB:.1f} GiB; "
    "an observed first-page attempt exceeded 9 GiB of swap"
    if PHYSICAL_MEMORY_GIB is not None and PHYSICAL_MEMORY_GIB < 12
    else None
)
PADDLEOCR_VL_RUNTIME_REASON = (
    f"PaddleOCR-VL was not started on the detected {PHYSICAL_MEMORY_GIB:.1f} GiB host after another "
    "1B-parameter vision-language OCR model exceeded 9 GiB of swap"
    if PHYSICAL_MEMORY_GIB is not None and PHYSICAL_MEMORY_GIB < 12
    else None
)


def _torch_device(torch: Any) -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@cache
def _load_doctr() -> tuple[Any, str]:
    import torch
    from doctr.models import ocr_predictor

    device = _torch_device(torch)
    predictor = ocr_predictor(
        det_arch="fast_base",
        reco_arch="crnn_vgg16_bn",
        pretrained=True,
        resolve_blocks=True,
    ).to(device)
    return predictor, device


def run_doctr(path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Run docTR with pinned detector and recognizer architecture names."""
    from doctr.io import DocumentFile

    predictor, device = _load_doctr()
    result = predictor(DocumentFile.from_pdf(path))
    exported = result.export()

    pages: list[str] = []
    for page in exported["pages"]:
        blocks: list[str] = []
        for block in page["blocks"]:
            lines = [" ".join(word["value"] for word in line["words"]) for line in block["lines"]]
            blocks.append("\n".join(lines))
        pages.append("\n\n".join(blocks))

    metadata = {
        "version": version("python-doctr"),
        "model": "fast_base + crnn_vgg16_bn",
        "device": device,
        "torch": version("torch"),
        "output": "plain text reconstructed from docTR blocks, lines and words",
    }
    return "\n\n".join(pages), exported, metadata


def _image_data_url(image: Any) -> str:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _portable_json(value: Any, *, source: Path) -> Any:
    """Remove the local checkout prefix from retained third-party provenance."""
    if isinstance(value, dict):
        return {key: _portable_json(item, source=source) for key, item in value.items()}
    if isinstance(value, list):
        return [_portable_json(item, source=source) for item in value]
    if value == str(source):
        return source.name
    return value


@cache
def _load_lighton() -> tuple[Any, Any, Any, str]:
    import torch
    from transformers import LightOnOcrForConditionalGeneration, LightOnOcrProcessor

    device = _torch_device(torch)
    dtype = torch.float32 if device in {"mps", "cpu"} else torch.bfloat16
    processor = LightOnOcrProcessor.from_pretrained(LIGHTON_MODEL, revision=LIGHTON_REVISION)
    model = LightOnOcrForConditionalGeneration.from_pretrained(
        LIGHTON_MODEL,
        revision=LIGHTON_REVISION,
        torch_dtype=dtype,
        low_cpu_mem_usage=True,
    ).to(device)
    model.eval()
    return processor, model, torch, device


def run_lighton(path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Run the pinned LightOnOCR-2 model on page images using PyTorch MPS or CPU."""
    import pypdfium2 as pdfium

    processor, model, torch, device = _load_lighton()
    dtype = torch.float32 if device in {"mps", "cpu"} else torch.bfloat16
    pages: list[str] = []
    raw_pages: list[dict[str, Any]] = []
    with pdfium.PdfDocument(path) as document, torch.inference_mode():
        for page_number, page in enumerate(document, start=1):
            bitmap = page.render(scale=200 / 72)
            try:
                image = bitmap.to_pil()
            finally:
                bitmap.close()
                page.close()
            longest = max(image.size)
            if longest > 1540:
                ratio = 1540 / longest
                image = image.resize((round(image.width * ratio), round(image.height * ratio)))
            conversation = [
                {
                    "role": "user",
                    "content": [{"type": "image", "url": _image_data_url(image)}],
                }
            ]
            inputs = processor.apply_chat_template(
                conversation,
                add_generation_prompt=True,
                tokenize=True,
                return_dict=True,
                return_tensors="pt",
            )
            inputs = {
                key: value.to(device=device, dtype=dtype) if value.is_floating_point() else value.to(device)
                for key, value in inputs.items()
            }
            output_ids = model.generate(**inputs, max_new_tokens=4096, do_sample=False)
            generated_ids = output_ids[0, inputs["input_ids"].shape[1] :]
            output = processor.decode(generated_ids, skip_special_tokens=True)
            pages.append(output)
            raw_pages.append({"page": page_number, "markdown": output})

    metadata = {
        "version": version("transformers"),
        "model": LIGHTON_MODEL,
        "model_revision": LIGHTON_REVISION,
        "device": device,
        "torch": version("torch"),
        "rendering": "200 DPI, longest edge capped at 1540 px",
    }
    return "\n\n".join(pages), {"pages": raw_pages}, metadata


@cache
def _load_paddleocr_vl() -> Any:
    from paddleocr import PaddleOCRVL

    return PaddleOCRVL(device="cpu", pipeline_version="v1.6")


def run_paddleocr_vl(path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Run PaddleOCR-VL 1.6 locally using its supported Apple CPU path."""
    pipeline = _load_paddleocr_vl()
    pages = list(pipeline.predict(input=str(path)))
    markdown_pages: list[str] = []
    raw_pages: list[dict[str, Any]] = []
    for page_number, result in enumerate(pages, start=1):
        markdown = result.markdown
        markdown_pages.append(markdown.get("markdown_texts", ""))
        raw_pages.append(
            {
                "page": page_number,
                "result": _portable_json(result.json, source=path),
                "markdown": markdown_pages[-1],
            }
        )

    metadata = {
        "version": version("paddleocr"),
        "model": PADDLEOCR_VL_MODEL,
        "model_revision_observed_when_pinned": PADDLEOCR_VL_REVISION,
        "device": "cpu",
        "pipeline_version": "v1.6",
    }
    return "\n\n".join(markdown_pages), {"pages": raw_pages}, metadata


@cache
def _load_paddleocr_v6() -> Any:
    from paddleocr import PaddleOCR

    return PaddleOCR(
        device="cpu",
        lang="en",
        ocr_version="PP-OCRv6",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
    )


def run_paddleocr_v6(path: Path) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Run the compact PP-OCRv6 text detector and recognizer on CPU."""
    pipeline = _load_paddleocr_v6()
    pages: list[str] = []
    raw_pages: list[dict[str, Any]] = []
    for page_number, result in enumerate(pipeline.predict(input=str(path)), start=1):
        raw = _portable_json(result.json, source=path)
        payload = raw.get("res", raw)
        text = "\n".join(payload.get("rec_texts", []))
        pages.append(text)
        raw_pages.append({"page": page_number, "result": raw})

    metadata = {
        "version": version("paddleocr"),
        "model": "PP-OCRv6",
        "device": "cpu",
        "output": "plain text in PaddleOCR reading order",
    }
    return "\n\n".join(pages), {"pages": raw_pages}, metadata


__all__ = [
    "HAS_DOCTR",
    "HAS_LIGHTON",
    "HAS_PADDLEOCR",
    "HAS_PADDLEOCR_VL",
    "LIGHTON_RUNTIME_REASON",
    "PADDLEOCR_VL_RUNTIME_REASON",
    "run_doctr",
    "run_lighton",
    "run_paddleocr_v6",
    "run_paddleocr_vl",
]
