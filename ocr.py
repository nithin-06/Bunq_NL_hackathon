"""
ocr.py - Extract text from receipt images using PaddleOCR.

This code path is written against PaddleOCR 2.x result shapes.
"""

from importlib.metadata import version

import cv2
import numpy as np

_ocr_engine = None


def _package_version(name: str) -> str:
    try:
        return version(name)
    except Exception:
        return "unknown"


def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from paddleocr import PaddleOCR
        except ImportError as e:
            raise RuntimeError(
                "[ERROR] PaddleOCR not installed. Fix: pip install paddleocr rapidfuzz"
            ) from e

        paddleocr_version = _package_version("paddleocr")
        if not paddleocr_version.startswith("2."):
            raise RuntimeError(
                "Unsupported paddleocr version "
                f"{paddleocr_version}. This code expects PaddleOCR 2.x."
            )

        try:
            print("[OCR] Loading PaddleOCR model (first run downloads ~200MB)...")
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang="en")
            print("[OCR] Ready.")
        except Exception as e:
            raise RuntimeError(
                "Failed to initialize PaddleOCR. "
                f"Installed versions: paddleocr={_package_version('paddleocr')}, "
                f"paddlepaddle={_package_version('paddlepaddle')}. "
                f"Original error: {e}"
            ) from e

    return _ocr_engine


def _preprocess_variants(image: np.ndarray) -> list[np.ndarray]:
    variants = [image]

    img = cv2.resize(image, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    thresh = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11,
        2,
    )
    variants.append(thresh)

    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharp = cv2.filter2D(gray, -1, kernel)
    variants.append(sharp)

    return variants


def run_ocr_full(image_path: str) -> tuple[list[str], list, tuple]:
    """
    Run PaddleOCR on a receipt image across all preprocessed variants.

    Returns:
        lines: all extracted text strings
        results: raw PaddleOCR entries (box, (text, score))
        image_shape: original image shape
    """
    ocr = _get_ocr()

    image = cv2.imread(image_path)
    if image is None:
        raise RuntimeError(f"Could not read image: {image_path}")

    all_lines = []
    all_results = []

    for variant in _preprocess_variants(image):
        result = ocr.ocr(variant, cls=True)

        if not result:
            continue

        lines_block = result[0]
        if lines_block is None:
            continue
        if not isinstance(lines_block, list):
            raise RuntimeError(
                "Unexpected OCR result shape. This code expects PaddleOCR 2.x output."
            )

        for line in lines_block:
            if not isinstance(line, (list, tuple)) or len(line) < 2:
                continue
            text = line[1][0]
            all_lines.append(text)
            all_results.append(line)

    if not all_lines:
        raise RuntimeError(
            "OCR ran but extracted no text. The image may be unreadable or not a receipt."
        )

    return all_lines, all_results, image.shape


def extract_text(image_path: str) -> str:
    lines, _, _ = run_ocr_full(image_path)
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "test_receipt.jpg"
    lines, results, shape = run_ocr_full(path)
    print("\n[RAW OCR OUTPUT]")
    print("=" * 50)
    for line in lines:
        print(line)
    print("=" * 50)
    print(f"\n{len(lines)} lines from {path}")
