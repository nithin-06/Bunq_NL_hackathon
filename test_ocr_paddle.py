import cv2
import re
import sys
from paddleocr import PaddleOCR
from rapidfuzz import fuzz


# ----------------------------
# Preprocessing
# ----------------------------
def preprocess(image_path):
    img = cv2.imread(image_path)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=2.5, beta=30)

    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        11, 2
    )

    return thresh, img.shape


# ----------------------------
# Fuzzy matching
# ----------------------------
def fuzzy_match(text, keywords, threshold=70):
    for k in keywords:
        if fuzz.partial_ratio(text, k) > threshold:
            return True
    return False


# ----------------------------
# Currency Detection
# ----------------------------
def detect_currency(text):
    if "€" in text:
        return "€"
    if "$" in text:
        return "$"
    if "₹" in text:
        return "₹"
    return "€"


# ----------------------------
# Merchant Extraction
# ----------------------------
def extract_merchant(lines):
    for line in lines[:5]:
        if len(line) > 3 and not re.search(r"\d", line):
            return line.strip()
    return "Unknown"


# ----------------------------
# Core Extraction (FINAL)
# ----------------------------
def extract_values(result, img_shape):
    height, width = img_shape[:2]

    totals = []
    discounts = []
    subtotals = []

    lines = [line[1][0] for line in result[0]]

    for i, line in enumerate(result[0]):
        box, (text, score) = line
        text_lower = text.lower()

        if score < 0.6:
            continue

        # context window
        context = " ".join(lines[max(0, i-2): i+3]).lower()
        prev_line = lines[i-1].lower() if i > 0 else ""

        # skip date
        if re.search(r"\d{1,2}[./-]\d{1,2}[./-]\d{2,4}", context):
            continue

        # skip percentage lines
        if "%" in context:
            continue

        matches = re.findall(r"\d+[.,]?\d{0,2}", text_lower)

        # detect if line is only a number
        is_number_only = re.fullmatch(r"[+-]?\d+[.,]?\d*", text_lower.strip())

        for m in matches:
            try:
                value = float(m.replace(",", "."))

                # ----------------------------
                # STRICT FILTERS
                # ----------------------------
                if len(m) > 6:
                    continue

                if "." not in m and "," not in m and value > 100:
                    continue

                if not (0.01 <= value <= 1000):
                    continue

                # ----------------------------
                # POSITION
                # ----------------------------
                y_avg = sum([p[1] for p in box]) / 4
                x_avg = sum([p[0] for p in box]) / 4

                weight = 0

                if y_avg > height * 0.6:
                    weight += 40

                if x_avg > width * 0.5:
                    weight += 20

                # ----------------------------
                # MULTI-LINE LINKING (KEY FIX)
                # ----------------------------
                if is_number_only and fuzzy_match(prev_line, ["korting", "discount", "actiekorting"]):
                    discounts.append((value, weight + 300))
                    continue

                if is_number_only and fuzzy_match(prev_line, ["total", "totaal", "bedrag"]):
                    totals.append((value, weight + 300))
                    continue

                # ----------------------------
                # CONTEXT SEMANTIC MATCH
                # ----------------------------
                if fuzzy_match(context, ["korting", "discount", "actiekorting"]):
                    discounts.append((value, weight + 250))

                elif fuzzy_match(context, ["total", "totaal", "bedrag", "te betalen"]):
                    totals.append((value, weight + 200))

                elif fuzzy_match(context, ["subtotaal", "subtotal"]):
                    subtotals.append((value, weight + 150))

                else:
                    if "." in m or "," in m:
                        totals.append((value, weight))

            except:
                pass

    return totals, discounts, subtotals


# ----------------------------
# Select best value
# ----------------------------
def select_best(values):
    if not values:
        return None
    values.sort(key=lambda x: x[1], reverse=True)
    return values[0][0]


# ----------------------------
# Fallback
# ----------------------------
def fallback_total(text):
    nums = re.findall(r"\d+[.,]?\d{1,2}", text)

    vals = []
    for n in nums:
        try:
            val = float(n.replace(",", "."))
            if 0.01 <= val <= 1000:
                vals.append(val)
        except:
            pass

    return max(vals) if vals else None


# ----------------------------
# MAIN PIPELINE
# ----------------------------
def run_pipeline(image_path):
    print(f"\n[INFO] Processing: {image_path}")

    processed, shape = preprocess(image_path)

    ocr = PaddleOCR(use_angle_cls=True, lang='en', use_gpu=False)
    result = ocr.ocr(processed, cls=True)

    print("\n[RAW OCR OUTPUT]")
    print("=" * 50)

    lines = []
    for line in result[0]:
        text = line[1][0]
        lines.append(text)
        print(text)

    print("=" * 50)

    full_text = "\n".join(lines)

    merchant = extract_merchant(lines)
    currency = detect_currency(full_text)

    totals, discounts, subtotals = extract_values(result, shape)

    discount = select_best(discounts)
    total_paid = select_best(totals)
    subtotal = select_best(subtotals)

    # ----------------------------
    # SMART OVERRIDE
    # ----------------------------
    if discount is not None and (total_paid is None or total_paid < discount):
        total_paid = 0.0

    if total_paid is None:
        total_paid = fallback_total(full_text)

    print("\n[FINAL RESULT]")
    print("=" * 50)
    print(f"Merchant: {merchant}")

    if total_paid is not None:
        print(f"Total Paid: {currency}{total_paid:.2f}")
    else:
        print("Total Paid: Not found")

    if discount is not None:
        print(f"Discount: {currency}{discount:.2f}")

    if subtotal is not None:
        print(f"Subtotal: {currency}{subtotal:.2f}")

    print("=" * 50)


# ----------------------------
# ENTRY
# ----------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python receipt_ocr_final_v3.py <image_path>")
        sys.exit(1)

    run_pipeline(sys.argv[1])