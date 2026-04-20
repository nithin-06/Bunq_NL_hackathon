"""
ocr.py — Extract raw text from receipt images using Tesseract.
"""

import pytesseract
from PIL import Image
import cv2
import numpy as np


def preprocess_image(image_path: str) -> np.ndarray:
    """Basic preprocessing to improve OCR accuracy."""
    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Denoise + threshold
    denoised = cv2.fastNlMeansDenoising(gray, h=10)
    _, thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return thresh


def extract_text(image_path: str) -> str:
    """
    Run Tesseract OCR on a receipt image.

    Args:
        image_path: Path to receipt image (jpg/png).

    Returns:
        Raw extracted text as a string.
    """
    try:
        preprocessed = preprocess_image(image_path)
        pil_img = Image.fromarray(preprocessed)
        # PSM 6 = assume uniform block of text (good for receipts)
        config = "--psm 6 --oem 3"
        raw_text = pytesseract.image_to_string(pil_img, config=config)
        return raw_text.strip()
    except Exception as e:
        raise RuntimeError(f"OCR failed for {image_path}: {e}")


# --- quick test ---
if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "test_receipt.jpg"
    text = extract_text(path)
    print("=== RAW OCR OUTPUT ===")
    print(text)