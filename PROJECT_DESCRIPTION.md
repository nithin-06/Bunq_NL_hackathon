# Split

> Collect first. Pay once. No chasing.

## Project Description

Split rethinks shared payments from first principles. Most group payment tools help after one person has already paid the full bill. That means the hardest part of the experience still remains: someone takes the financial risk, then has to chase everyone else, and the product merely makes the reminders slightly more convenient.

Split fixes the problem at the source by changing the sequence of the transaction itself.

Instead of treating bill splitting as an after-payment reimbursement flow, Split turns the receipt into a coordinated checkout session. A user scans the receipt, each participant says or types what they had in natural language, the system maps those claims to receipt items, calculates each person's share, and sends them a bunq payment link or QR code. Only when everyone has contributed does Split unlock the final payment to the merchant.

That makes Split more than a bill splitter. It is a pre-settlement layer for shared expenses: no one fronts the money, no one gets stuck sending reminders, and the social friction disappears because the group settles the bill together before the transaction is completed.

## Why Split Stands Out

- Most tools digitize the IOU. Split prevents the IOU from being created in the first place.
- We solve a behavioral problem, not just a math problem, by changing when payment happens.
- We combine AI receipt understanding, natural language claiming, and bunq-powered payment orchestration in one end-to-end flow.
- Our core mechanism, "no one pays until everyone pays," creates fairness, accountability, and zero upfront burden.

## Inspiration

Splitting bills is universal, but the experience is still broken. In almost every group dinner, trip, or event, one person pays first and becomes the temporary lender for everyone else. From that moment on, the experience shifts from enjoying time together to following up on pending payments.

We realized the real problem is not the split itself. The real problem is timing.

As long as money is collected after the purchase, one person always carries the risk and the awkwardness. That insight led us to build Split around a simple but powerful idea: collect first, then pay.

## What it does

Split lets a group settle a bill before the final merchant payment is released.

1. Scan a receipt.
2. Let each participant describe what they had using voice or text.
3. Use AI to extract receipt items and map natural language claims to those items.
4. Calculate each person's share automatically.
5. Send each participant a bunq payment link or QR code.
6. Unlock the final merchant payment only after the full amount has been collected.

The result is a smoother group-payment experience with no manual back-and-forth and no need to chase anyone afterward.

## How we built it

We built Split as an end-to-end flow that combines AI, voice, OCR, and payments:

- Anthropic Claude Vision is used as the primary engine for receipt understanding.
- PaddleOCR acts as a fallback to handle difficult or inconsistent receipts more robustly.
- Whisper-based transcription enables voice input for participants who want to simply say what they ordered.
- Our matching layer maps natural language statements like "I had the pasta and a glass of wine" to the actual receipt lines.
- bunq APIs generate payment requests, shareable payment links, and QR-based payment flows.
- A payment release mechanism holds the merchant payment until the group contribution is complete.
- The current prototype runs in bunq's sandbox environment.

## Challenges we ran into

The hardest part was making messy real-world inputs behave like structured financial data.

Receipts vary wildly in layout, image quality, abbreviations, tax formatting, and item naming, so extracting reliable line items required a layered OCR approach rather than a single model. Natural language mapping was another challenge: people rarely repeat menu text exactly, so we had to bridge the gap between human phrasing and receipt phrasing with flexible matching logic.

On the payments side, the challenge was not just generating payment requests, but designing a flow where the final payment remains controlled until everyone has contributed. That required us to think about Split as a coordinated payment system, not just a calculator.

## Accomplishments that we're proud of

We built a working prototype that already demonstrates the full product loop:

- Scans real receipts with AI.
- Accepts natural language input through voice or text.
- Allocates costs across multiple people.
- Sends bunq-powered payment requests.
- Enforces a "no one pays until everyone pays" release flow.

Most importantly, we turned a socially awkward moment into a structured, fair, and low-friction experience.

## What we learned

We learned that strong product ideas sometimes come from changing sequence rather than adding features.

The biggest insight from building Split is that shared expenses are not mainly a calculation problem. They are a coordination problem. Once we shifted the timing of payment, many of the awkward behaviors that people accept as normal simply disappeared.

We also gained practical experience combining AI vision, OCR fallbacks, voice transcription, matching logic, and real payment infrastructure in a way that feels simple to the user even when the backend is doing complex work.

## What's next for Split

Our next steps are focused on accuracy, product depth, and real-world readiness:

- Improve receipt parsing speed and reliability across more merchants and receipt formats.
- Strengthen item matching for more complex orders, shared dishes, and ambiguous phrasing.
- Move from bunq sandbox flows toward production-ready payment handling.
- Refine the mobile experience so the entire flow feels native at the table.
- Expand beyond restaurant bills into travel, rent, events, and any shared expense where upfront coordination matters.

## Vision

We want Split to become the default way groups handle shared payments.

Not by helping people send better reminders, but by making reminders unnecessary in the first place.
