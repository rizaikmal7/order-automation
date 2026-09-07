# Order Intake Automation — Handover Document

**Author:** Muhammad Ikmal Riza
**Date:** Friday, 28 August 2026
**Status:** Working pipeline, tested against both trial batches (38 images, 34 distinct orders)

---

## 1. What It Does

Takes a folder of photographed wholesale order forms and produces, per image/order:

1. A structured, validated order record (or a specific reason why it isn't clean)
2. A line in one of three Google Sheet tabs: **Clean Orders**, **Review Queue**, or **Duplicate Log**
3. A draft confirmation message back to the sales rep (saved to `confirmations.txt`, not sent automatically)

It does **not** ship orders, does **not** touch inventory, and does **not** send anything to a customer or rep. Everything downstream of the sheet is still a human action.

---

## 2. Architecture

```
main.py (orchestrator)
  │
  ├─► extractor.py     — Gemini Vision OCR per image → structured JSON (schemas.py: OrderFormRaw)
  │                       Retries on 429/503/RESOURCE_EXHAUSTED with exponential backoff.
  │
  ├─► [multi-page merge]— If a form has "Page X of Y", pages with the same Order No
  │                       are combined into one record before validation.
  │
  ├─► validator.py      — Deterministic checks against products_master.csv / accounts_master.csv:
  │                         - account code/name resolution
  │                         - S+M+L+XL arithmetic vs written qty_total
  │                         - style code + color + price vs catalog
  │                         - operational notes (HOLD/CANCEL/etc.)
  │                         - duplicate/re-scan detection (fingerprint-based)
  │                       Routes each order to exactly one of: clean / review / duplicate.
  │
  ├─► confirmation.py   — Drafts the rep-facing message, tone differs by outcome
  │                       (clean / needs review / duplicate-no-action-needed).
  │
  └─► sheet_writer.py   — Appends to local CSVs (clean_orders.csv, review_queue.csv,
                          duplicate_log.csv) AND to the corresponding Google Sheet tabs.
                          Local CSVs are also the pipeline's own memory for dedup —
                          see Section 5.
```

**Master data:** `products_master.csv` (style code, name, price, available colors) and `accounts_master.csv` (account code, name, city, terms) are the source of truth for validation. Update these files directly when the catalogue or account list changes — no code change needed.

---

## 3. How to Run It

```bash
# One-time setup
cp .env.example .env          # add GEMINI_API_KEY
# credentials.json (Google service account, Sheets + Drive scope) must be present in the project root
pip install -r requirements.txt

# Run a batch
python main.py --batch batch1
python main.py --batch batch2
```

Each run is **incremental** — it reads existing `clean_orders.csv` / `review_queue.csv` / `duplicate_log.csv` on startup to know what it's already seen, then appends. It does not clear anything. Running the same batch twice should add zero new Clean/Review rows and route everything to Duplicate Log instead (see Section 6 for the one caveat).

---

## 4. How to Verify It Worked

After a run, check the console block:

```
=== FINAL EXECUTION METRICS ===
Images In                : N
Consolidated Records     : N (after multi-page merge)
Distinct Orders          : Clean + Review
  - Clean                : X (Total Lines: Y)
  - Review (genuine)     : Z
Duplicate Re-scans Logged: D
```

Sanity checks before trusting a run:
- `Images In` should match the actual file count in the batch folder.
- Every **Review Queue** row must have a non-empty, specific `Review Reasons` value — never a bare "needs review". If you ever see that, it's a bug in `validator.py`, not a data issue.
- Spot-check 2–3 Clean orders against the source image manually before the first production run of any given day — this is the one step nothing in the pipeline can do for you (see the manager brief).
- Run `python -c "..."` dedup check (see Section 6) after any re-run to confirm no line-item-identical rows landed in `clean_orders.csv`.

---

## 5. How Duplicate Detection Works

Every order gets a **fingerprint**: `ORDER_NO :: sorted(style_code + normalized_color + qty_total for each line)`.

- On startup, the validator loads fingerprints from `clean_orders.csv`, `review_queue.csv`, and `duplicate_log.csv` (the last two only if they already have a `_Fingerprint` column — see Section 6, Failure Mode #1).
- If a new order's fingerprint has been seen before → routed to **Duplicate Log**, not re-entered anywhere.
- If the Order No has been seen before but the fingerprint is **different** → flagged as `"Order Number Reused With Different Content"` in Review Queue, for manual reconciliation (could be a legitimate amendment or a data-entry error — the system doesn't guess which).

This only protects orders whose *first* submission is clean. A different image of an already-*review-flagged* order will also be caught, but only from the point this session's fingerprint fix was deployed onward — see Failure Mode #1 below for the exact boundary.

---

## 6. Three Most Likely Ways This Breaks

### Failure Mode #1 — Duplicate detection misses re-scans across model/run changes
**What happens:** The fingerprint is built from the *extracted text*, not the image itself. If the same physical form is OCR'd twice by different model versions (or the same model with slightly non-deterministic output), a tiny difference — a color spelled "Sea Glass" vs "Seaglass", a qty read as 12 vs 13 — produces a different fingerprint. The duplicate goes undetected and gets written to Clean Orders or Review Queue a second time.

**How we found it:** Discovered today (28 Aug) after switching Gemini models mid-run due to rate limiting. A subsequent audit of `clean_orders.csv` found the same Order No + line items appearing under multiple `File Source` values.

**What to do about it:**
- Before any re-run, back up all three CSVs (`cp clean_orders.csv clean_orders_backup_$(date +%F).csv`, etc.).
- After any re-run, run the dedup check below and manually remove exact-content duplicate rows if found:
  ```python
  import pandas as pd
  df = pd.read_csv('clean_orders.csv')
  dedup_cols = ['Order No','Style Code','Color','Qty S','Qty M','Qty L','Qty XL','Qty Total','Unit Price']
  dupes = df[df.duplicated(subset=dedup_cols, keep=False)]
  print(dupes if not dupes.empty else 'Clean.')
  ```
- **Recommended fix (not yet implemented):** loosen the fingerprint match — e.g. match on `Order No + rounded total units + rounded total value` instead of exact per-line string equality, or re-verify against the *source image hash* (perceptual hash of the photo) rather than OCR text. Either requires design discussion before implementing, since looser matching risks false-positive duplicate flags on legitimately-amended orders.
- Never run two different model versions across the same un-deduped dataset without a manual audit in between.

### Failure Mode #2 — API quota/rate limit exhaustion stalls the whole batch
**What happens:** `extractor.py` retries on `429`/`503`/`RESOURCE_EXHAUSTED` with exponential backoff (5s → 10s → 20s → 40s, 4 attempts), which handles short rate-limit blips. It does **not** handle a fully exhausted **daily** quota — retries will just fail identically every time until quota resets, burning through the batch with every image landing in Review Queue as `"Pipeline Extraction Error"`.

**How to tell which one you're hitting:** the raw error message includes a `quotaId` — `PerMinute` means slow down and retry; `PerDay` means stop and wait/rotate keys.

**What to do about it:**
- If it's a `PerDay` quota: stop the run, do not "just wait it out" mid-batch — check remaining images, note which ones failed, and either wait for quota reset or switch to a backup API key/tier.
- **Before treating any Review Queue entries as genuine data-quality issues in a report**, filter out `"Pipeline Extraction Error"` rows first — they represent an infrastructure failure, not a problem with the order form itself. Mixing the two in a metrics report misrepresents extraction quality.
- Recommended: add a distinct "Infra Log" bucket (parallel to Duplicate Log) for extraction-failure retries, separate from genuine Review Queue issues, so this filtering happens automatically instead of by manual audit.

### Failure Mode #3 — Silent misconfiguration (wrong entry point, missing credentials)
**What happens:** Two things bit us today specifically:
- An accidentally-indented `if __name__ == "__main__":` block (inside the function instead of at module level) caused the script to do *nothing* on invocation — no error, no output, just an instant return to the prompt.
- `SheetWriter` failing to reach Google Sheets (missing/expired `credentials.json`, wrong `spreadsheet_name`, or the service account losing share access) fails **silently** — `_init_gspread()` catches the exception, prints a `[WARN]`, and the pipeline continues writing local CSVs only. If nobody reads the console output, the office team will keep believing the Google Sheet is live when it's actually stale.

**What to do about it:**
- Before any unattended/scheduled run, do a 1-image smoke test and confirm the console shows `=== Starting ===` and, if Sheets sync is expected, the `[INFO] Synced N... to Google Sheet tab` lines — their absence is the signal, not an error message.
- Recommend adding a hard failure (not a silent warn) if Sheets sync is expected but `self.sh` is `None` after init, so a broken credential doesn't go unnoticed for days.

---

## 7. Known Limitations (not failures, but worth knowing)

- `get_account_by_name` does substring matching both directions (`name in master_name or master_name in name`) as a fallback when account_code isn't provided. This can mis-resolve accounts with very short or overlapping names — works fine for this dataset, not guaranteed to generalize to a larger account list without a stricter match (e.g. token-based similarity with a minimum score).
- Confirmation messages for duplicates are sent per re-scan. If reps regularly re-photograph orders as a habit, this could generate one email per re-scan. Fine for the trial; worth a "batch and summarize" step before production if that turns out to be frequent.
- Multi-page merge keys purely on Order No + `page_total > 1`. A form missing the "Page X of Y" printed indicator (illegible or absent) will be processed as two separate standalone orders instead of merged — currently silent, no warning is raised for this case.

---

## 8. Who Maintains This

For the purposes of this trial: Muhammad Ikmal Riza. In a production handoff, whoever owns this should be comfortable reading Python, has access to the Gemini API console (for quota/billing) and the Google Cloud service account (for Sheets credentials), and should review Section 6 before the first unattended run.
