"""
ocr.py — Extract text from receipt images using PaddleOCR.

PaddleOCR is significantly more accurate than Tesseract on real-world receipts:
- handles skewed/rotated text via angle classifier
- works well on low-contrast and crumpled receipts
- returns bounding boxes used by parser for positional scoring

Returns both:
  - raw text string  (for backward-compatible pipeline)
  - structured results list  (for richer parser)

Install:
    pip install paddlepaddle paddleocr rapidfuzz
    # CPU-only (no GPU needed for hackathon):
    pip install paddlepaddle -i https://pypi.tuna.tsinghua.edu.cn/simple
    # or just: pip install paddleocr  (pulls paddlepaddle automatically)
"""

import cv2
import numpy as np

# Singleton — loaded once, reused across calls (first load downloads ~200MB)
_ocr_engine = None

def _get_ocr():
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from paddleocr import PaddleOCR
            print("[OCR] Loading PaddleOCR model (first run downloads ~200MB)...")
            _ocr_engine = PaddleOCR(use_angle_cls=True, lang='en')
            print("[OCR] Ready.")
        except ImportError:
            raise RuntimeError(
                "\n[ERROR] PaddleOCR not installed.\n"
                "Fix: pip install paddleocr rapidfuzz"
            )
    return _ocr_engine


# ---------------------------------------------------------------------------
# Preprocessing — verbatim from test_ocr_paddle.py
# ---------------------------------------------------------------------------

def _preprocess_variants(image: np.ndarray) -> list:
    variants = []

    variants.append(image)

    img = cv2.resize(image, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11, 2
    )
    variants.append(thresh)

    kernel = np.array([[0, -1, 0],
                       [-1, 5, -1],
                       [0, -1, 0]])
    sharp = cv2.filter2D(gray, -1, kernel)
    variants.append(sharp)

    return variants


# ---------------------------------------------------------------------------
# Core OCR — verbatim logic from test_ocr_paddle.py::run_ocr()
# NO deduplication — all variants contribute, matching original behaviour
# ---------------------------------------------------------------------------

def run_ocr_full(image_path: str) -> tuple[list[str], list, tuple]:
    """
    Run PaddleOCR on a receipt image across all preprocessed variants.

    Returns:
        lines        — all extracted text strings (no dedup, matches original)
        results      — all raw PaddleOCR entries (box, (text, score))
        image_shape  — original image shape for positional scoring in parser
    """
    ocr = _get_ocr()

    image = cv2.imread(image_path)
    if image is None:
        raise RuntimeError(f"Could not read image: {image_path}")

    all_lines = []
    all_results = []

    for v in _preprocess_variants(image):
        result = ocr.ocr(v, cls=True)

        if not result or result[0] is None:
            continue

        for line in result[0]:
            text = line[1][0]
            all_lines.append(text)
            all_results.append(line)

    return all_lines, all_results, image.shape


def extract_text(image_path: str) -> str:
    """Backward-compatible: returns plain joined text."""
    lines, _, _ = run_ocr_full(image_path)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

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