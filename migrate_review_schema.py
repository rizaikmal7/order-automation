"""
One-time migration: run this ONCE before your Day 3 full re-run, if
review_queue.csv already exists with the old single "Order Value" column.

Safe to run multiple times (idempotent) — it no-ops if already migrated.
Does NOT touch clean_orders.csv (its schema didn't change).
"""
import os
import shutil
import pandas as pd

REVIEW_CSV = "review_queue.csv"

# Target column sequence matching SheetWriter
EXPECTED_COLUMNS = [
    "Order No", "File Source", "Sales Rep", "Account Code", "Account Name",
    "Ship Date", "Confidence", "Total Units",
    "Order Value (Calculated)", "Order Value (Written on Form)",
    "Review Reasons", "Extraction Notes"
]

def migrate():
    if not os.path.exists(REVIEW_CSV):
        print(f"[SKIP] {REVIEW_CSV} does not exist yet — nothing to migrate.")
        return

    # Always back up before touching anything
    backup_path = REVIEW_CSV + ".bak"
    shutil.copy(REVIEW_CSV, backup_path)
    print(f"[INFO] Backed up existing file to {backup_path}")

    df = pd.read_csv(REVIEW_CSV)

    if "Order Value (Calculated)" in df.columns:
        print("[SKIP] Already migrated — no old 'Order Value' column found.")
        return

    if "Order Value" not in df.columns:
        print("[WARN] Neither old nor new column found — check the file manually.")
        return

    df = df.rename(columns={"Order Value": "Order Value (Calculated)"})
    df["Order Value (Written on Form)"] = ""

    # Warn before dropping anything — don't let unexpected columns
    # (e.g. a manual "Reviewed By" note someone added by hand) vanish
    # silently just because they're not in EXPECTED_COLUMNS. They're
    # still safe in the backup either way, but the terminal output
    # should say so explicitly.
    dropped = [c for c in df.columns if c not in EXPECTED_COLUMNS]
    if dropped:
        print(f"[WARN] These columns are not in EXPECTED_COLUMNS and will be "
              f"dropped from the reordered file: {dropped}")
        print(f"[WARN] They are still preserved in the backup at {backup_path}.")

    # Reorder columns to ensure proper alignment
    existing_expected_cols = [c for c in EXPECTED_COLUMNS if c in df.columns]
    df = df[existing_expected_cols]

    df.to_csv(REVIEW_CSV, index=False)
    print(f"[DONE] Migrated {len(df)} rows to the new schema.")

if __name__ == "__main__":
    migrate()