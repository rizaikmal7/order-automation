# Order Intake Automation

An LLM-powered pipeline that turns photographed, handwritten wholesale order forms into validated, structured order records — built as a 3-day technical trial for an AI/ML Engineer role.

> **Context:** This was built for a synthetic wholesale apparel brand used as the trial scenario. All order form images are synthetic — built to look like real phone photos of handwritten forms, but containing no real customer data. `products_master.csv` and `accounts_master.csv` are equally synthetic. Company and evaluator names have been left out of this write-up.

---

## The Problem

A retail sales team photographs handwritten order forms on their phones and sends them in. Someone in the office then reads each photo, manually types the order into a spreadsheet, and replies to confirm receipt — roughly 3–4 hours a day. Two failure modes drove the need for automation:

- An order was entered with the wrong quantity and shipped short.
- During a busy week, the backlog grew to two days, leaving sales reps unsure whether their orders had even been received.

**The brief was explicit about priorities:** an automation that processes 100% of orders but gets 4% wrong is worse than the manual process it replaces. Keeping bad data out of the spreadsheet mattered more than raw throughput.

---

## What It Does

Given a folder of order photographs, the pipeline produces:

1. **Structured order records** validated against a product catalogue and an account list
2. **A review queue** — anything the system isn't confident about, with the *specific* field, row, and value that triggered the flag (never a bare "needs review")
3. **A duplicate log** — re-scans of orders already processed, kept separate from genuine issues so the review queue stays actionable
4. **Draft confirmation messages** per order, tone-adjusted for clean / flagged / duplicate outcomes (drafted only — nothing is sent automatically)

Output syncs to both local CSVs and a Google Sheet with three tabs: `Clean Orders`, `Review Queue`, `Duplicate Log`.

---

## Architecture

```
main.py (orchestrator)
  │
  ├─► extractor.py      Gemini Vision OCR → structured JSON per image
  │                      Retries on 429 / 503 / RESOURCE_EXHAUSTED with exponential backoff
  │
  ├─► [multi-page merge] Forms marked "Page X of Y" with the same Order No
  │                      are combined into one record before validation
  │
  ├─► validator.py       Deterministic checks against master data:
  │                        • account code/name resolution
  │                        • S + M + L + XL arithmetic vs. written total
  │                        • style code, color, and price vs. catalogue
  │                        • operational notes (HOLD / CANCEL / etc.)
  │                        • duplicate/re-scan detection via content fingerprint
  │                      Routes each order to exactly one of: clean / review / duplicate
  │
  ├─► confirmation.py    Drafts the rep-facing message, tone differs by outcome
  │
  └─► sheet_writer.py    Writes to local CSVs and the corresponding Google Sheet tabs
```

**Stack:** Python, Gemini Vision API (structured JSON output), Pydantic (schema validation), pandas, gspread + Google Sheets API.

---

## Result (Trial Run)

38 photographed forms → 37 form instances after multi-page consolidation → 34 distinct wholesale orders:

| Outcome | Count | Notes |
|---|---|---|
| Clean (ready for fulfillment) | 15 orders / 51 line items | Passed every deterministic check |
| Review Queue (flagged) | 19 orders | Each with a specific, actionable reason |
| Duplicate re-scans | 3 | Caught and logged separately, not double-fulfilled |

Every review-queue reason names the exact line, field, and value — e.g. `Line 2 Price Discrepancy: Form price $78.00 != Catalog price $70.00` — never a generic "needs review."

---

## A Bug I Found and What I Learned From It

The duplicate-detection logic only registered an order's fingerprint once it passed validation as "clean." That meant a **re-scan of an order that had already failed validation once** (e.g. missing ship date) went completely undetected — the pipeline reprocessed it as if it were new, silently inflating the review queue.

I found this by manually reconciling order counts against the sheet — the pipeline itself never surfaced it. Fixing it meant separating two different questions that the code had conflated: *"has this exact content been seen before?"* (should always dedupe) vs. *"has this order number been resolved before?"* (should allow legitimate corrected resubmissions through). The fix registers content fingerprints for every order regardless of outcome, while still allowing a corrected resubmission of a previously-failed order to pass through cleanly.

A second, related issue surfaced during re-testing: **the fingerprint is computed from OCR output, not the image itself.** Switching Gemini model versions mid-run (due to rate limiting) produced slightly different extracted text for the same physical form, which was enough to break the exact-match fingerprint and let a handful of already-clean orders get re-entered. This is documented as a known limitation with a recommended fix in [`HANDOVER.md`](./HANDOVER.md), rather than something I patched under time pressure without being sure of the tradeoffs.

---

## Running It

```bash
pip install -r requirements.txt
cp .env.example .env        # add your GEMINI_API_KEY
# place a Google service-account credentials.json in the project root
# (Sheets + Drive scope) if you want Google Sheets sync — the pipeline
# runs fine on local CSV alone without it

python main.py --batch batch1
```

Re-running the same batch is safe and idempotent by design — see [`HANDOVER.md`](./HANDOVER.md) for the one known edge case (cross-model-version re-runs) and how to check for it.

---

## Docs

- [`HANDOVER.md`](./HANDOVER.md) — full technical handover: architecture detail, verification steps, and the three most likely failure modes with concrete mitigations
- `products_master.csv` / `accounts_master.csv` — synthetic master data used for validation

---

## Disclaimer

This was built under a compressed 3-day trial timeline. Known limitations and things I would not yet trust in production are documented candidly in `HANDOVER.md` rather than glossed over — I think that's more useful to a reader than a polished-looking repo that hides where the rough edges are.
