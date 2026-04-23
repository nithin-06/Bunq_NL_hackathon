import cv2
import re
import sys
import numpy as np
from paddleocr import PaddleOCR
from rapidfuzz import fuzz


KNOWN_MERCHANTS = [
    "kruidvat", "primark", "jumbo", "lidl", "aldi", "tesco", "spar"
]


# ----------------------------
# Preprocessing
# ----------------------------
def preprocess_variants(image):
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


# ----------------------------
# Normalize
# ----------------------------
def normalize(text):
    text = text.lower()
    text = re.sub(r"[^a-z ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# ----------------------------
# Merchant
# ----------------------------
def extract_merchant(lines):
    candidates = []

    for i, line in enumerate(lines[:8]):
        clean = normalize(line)

        if len(clean) < 3:
            continue

        if any(char.isdigit() for char in clean):
            continue

        candidates.append((clean, 100 - i * 10))

    best_match = None
    best_score = 0

    for cand, pos_weight in candidates:
        for merchant in KNOWN_MERCHANTS:
            score = fuzz.partial_ratio(cand, merchant)

            if merchant in cand:
                score += 30

            score += pos_weight

            if score > best_score:
                best_score = score
                best_match = merchant

    if best_score > 80:
        return best_match.capitalize()

    return "Unknown"


# ----------------------------
# Currency
# ----------------------------
def detect_currency(text):
    if "€" in text:
        return "€"
    if "£" in text:
        return "£"
    if "$" in text:
        return "$"
    return "€"


# ----------------------------
# OCR
# ----------------------------
def run_ocr(image):
    ocr = PaddleOCR(use_angle_cls=True, lang='en')

    all_lines = []
    all_results = []

    for v in preprocess_variants(image):
        result = ocr.ocr(v, cls=True)

        if not result or result[0] is None:
            continue

        for line in result[0]:
            text = line[1][0]
            all_lines.append(text)
            all_results.append(line)

    return all_lines, all_results


# ----------------------------
# Extract values (FIXED HARD)
# ----------------------------
def extract_values(results, img_shape):
    height, width = img_shape[:2]

    totals = []
    discounts = []

    lines = [r[1][0].lower() for r in results]

    for i, line in enumerate(results):
        box, (text, score) = line

        if score < 0.5:
            continue

        context = " ".join(lines[max(0, i-2): i+3])

        # 🚫 skip loyalty/points
        if any(word in context for word in ["punten", "points", "kaart", "loyal"]):
            continue

        matches = re.findall(r"-?\d+[.,]\d{2}|\b\d{1,4}\b", text)

        for m in matches:
            try:
                # prefer decimals strongly
                if re.fullmatch(r"\d{1,4}", m):
                    # 🚫 reject plain integers unless VERY strong total context
                    if "tota" not in context:
                        continue
                    value = float(m)
                else:
                    value = float(m.replace(",", "."))

                if not (0.0 <= abs(value) <= 1000):
                    continue

                y_avg = sum([p[1] for p in box]) / 4
                x_avg = sum([p[0] for p in box]) / 4

                weight = 0
                if y_avg > height * 0.6:
                    weight += 40
                if x_avg > width * 0.5:
                    weight += 20

                # TOTAL
                if fuzz.partial_ratio(context, "totaal") > 65 or "tota" in context:
                    totals.append((value, weight + 300))
                    continue

                # DISCOUNT
                if fuzz.partial_ratio(context, "korting") > 70:
                    discounts.append((abs(value), weight + 200))
                    continue

            except:
                pass

    return totals, discounts


# ----------------------------
# Select
# ----------------------------
def select_best(values):
    if not values:
        return None
    values.sort(key=lambda x: x[1], reverse=True)
    return values[0][0]


# ----------------------------
# MAIN
# ----------------------------
def run_pipeline(image_path):
    print(f"\n[INFO] Processing: {image_path}")

    image = cv2.imread(image_path)

    lines, results = run_ocr(image)

    print("\n[RAW OCR OUTPUT]")
    print("=" * 50)
    for l in lines:
        print(l)
    print("=" * 50)

    full_text = "\n".join(lines)

    merchant = extract_merchant(lines)
    currency = detect_currency(full_text)

    totals, discounts = extract_values(results, image.shape)

    total_paid = select_best(totals)
    discount = select_best(discounts)

    # force 0 detection
    for l in lines:
        if "tota" in l.lower() and re.search(r"0+[.,]?0*", l):
            total_paid = 0.0

    if discount is not None and (total_paid is None or total_paid < discount):
        total_paid = 0.0

    print("\n[FINAL RESULT]")
    print("=" * 50)
    print(f"Merchant: {merchant}")

    if total_paid is not None:
        print(f"Total Paid: {currency}{total_paid:.2f}")
    else:
        print("Total Paid: Not found")

    if discount is not None:
        print(f"Discount: {currency}{discount:.2f}")

    print("=" * 50)


# ----------------------------
# ENTRY
# ----------------------------
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_ocr_paddle.py <image>")
        sys.exit(1)

    run_pipeline(sys.argv[1])