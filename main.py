import os
import time
import argparse
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

from master_data import MasterDataLoader
from extractor import OrderExtractor
from validator import OrderValidator
from confirmation import ConfirmationGenerator
from sheet_writer import SheetWriter

def run_pipeline(batch_dir: str):
    load_dotenv()
    if not os.environ.get("GEMINI_API_KEY"):
        raise ValueError("GEMINI_API_KEY Not found. Please add at .env")

    print(f"=== Starting Driftwood Order Intake Automation: {batch_dir} ===")

    master_loader = MasterDataLoader()
    extractor = OrderExtractor()
    validator = OrderValidator(master_loader)
    sheet_writer = SheetWriter()

    image_extensions = (".jpg", ".jpeg", ".png", ".webp")
    image_files = sorted([f for f in Path(batch_dir).iterdir() if f.suffix.lower() in image_extensions])

    if not image_files:
        print(f"[WARN] No image found in folder '{batch_dir}'")
        return

    print(f"[INFO] Found {len(image_files)} order form(s) to process.\n")

    extracted_orders = []
    review_orders = []          
    duplicate_log_orders = []   
    confirmations = []

    for idx, img_path in enumerate(image_files, start=1):
        print(f"[{idx}/{len(image_files)}] Extracting: {img_path.name} ...")
        try:
            raw_order = extractor.extract_order_from_image(img_path)
            extracted_orders.append((img_path.name, raw_order))
        except Exception as e:
            print(f"    -> [ERROR] Failed to extract {img_path.name}: {e}")
            failed_order = {
                "file_source": img_path.name,
                "order_no": "FAILED_TO_PROCESS",
                "sales_rep": "",
                "account_code": "",
                "account_name": "",
                "ship_date": "",
                "confidence": "low",
                "total_units": 0,
                "order_value": 0.0,
                "review_reasons": [f"Pipeline Extraction Error: {str(e)}"],
                "extraction_notes": []
            }
            review_orders.append(failed_order)
            confirmations.append({
                "order_no": f"FAILED::{img_path.name}",
                "sales_rep": "Sales Rep",
                "file_source": img_path.name,
                "message": ConfirmationGenerator.generate_message(
                    {**failed_order, "line_count": 0}, is_clean=False
                )
            })
        time.sleep(4.0)

    # Step 2: Multi-page merge
    page_groups = defaultdict(list)
    standalone = []
    for file_source, raw_order in extracted_orders:
        if raw_order.page_total and raw_order.page_total > 1 and raw_order.order_no:
            page_groups[raw_order.order_no.strip().upper()].append((file_source, raw_order))
        else:
            standalone.append((file_source, raw_order))

    merged_orders = []
    for order_no, pages in page_groups.items():
        pages.sort(key=lambda p: p[1].page_current or 0)
        base = pages[0][1]
        if base.page_total and len(pages) < base.page_total:
            base.extraction_notes.append(
                f"Incomplete Multi-page Order: Received {len(pages)} of {base.page_total} pages"
            )
            base.requires_human_review = True
        for _, extra in pages[1:]:
            base.line_items.extend(extra.line_items)
            base.extraction_notes.extend(extra.extraction_notes)
            if extra.notes:
                base.notes = f"{base.notes} | {extra.notes}" if base.notes else extra.notes
            if extra.confidence == "low" or base.confidence == "low":
                base.confidence = "low"
            elif extra.confidence == "medium" or base.confidence == "medium":
                base.confidence = "medium"
        written_units = [p[1].written_total_units for p in pages if p[1].written_total_units is not None]
        base.written_total_units = sum(written_units) if written_units else None
        written_values = [p[1].written_order_value for p in pages if p[1].written_order_value is not None]
        base.written_order_value = sum(written_values) if written_values else None
        merged_source = " + ".join(p[0] for p in pages)
        merged_orders.append((merged_source, base))

    all_orders_to_validate = standalone + merged_orders
    print(f"\n[INFO] Validating {len(all_orders_to_validate)} consolidated order(s)...\n")

    # Step 3: Validation
    clean_orders = []

    for file_source, raw_order in all_orders_to_validate:
        is_clean, reasons, processed_data = validator.validate_order(raw_order, file_source=file_source)
        is_duplicate = processed_data.get("is_duplicate", False)

        conf_msg = ConfirmationGenerator.generate_message(processed_data, is_clean, is_duplicate)
        confirmations.append({
            "order_no": processed_data.get("order_no", "UNKNOWN"),
            "sales_rep": processed_data.get("sales_rep", "Sales Rep"),
            "file_source": file_source,
            "message": conf_msg
        })

        if is_duplicate:
            duplicate_log_orders.append(processed_data)
            print(f"    -> [DUPLICATE LOG] Order No: {processed_data.get('order_no')} ({file_source})")
        elif is_clean:
            clean_orders.append(processed_data)
            print(f"    -> [CLEAN] Order No: {processed_data.get('order_no')} ({file_source})")
        else:
            review_orders.append(processed_data)
            print(f"    -> [REVIEW QUEUE] Order No: {processed_data.get('order_no')} | Issues: {len(reasons)}")

    # Step 4: Save
    sheet_writer.save_results(clean_orders, review_orders, confirmations, duplicate_log_orders)

    print("\n=== Processing Complete ===")
    print(f"Clean Orders: {len(clean_orders)}")
    print(f"Review Queue (genuine issues): {len(review_orders)}")
    print(f"Duplicate Log: {len(duplicate_log_orders)}")

    total_clean_lines = sum(len(o.get("line_items", [])) for o in clean_orders)
    issue_counts = {}
    for o in review_orders:
        for r in o.get("review_reasons", []):
            issue_counts[r] = issue_counts.get(r, 0) + 1

    print("\n" + "="*40)
    print("=== FINAL EXECUTION METRICS ===")
    print(f"Images In                : {len(image_files)}")
    print(f"Consolidated Records     : {len(all_orders_to_validate)}")
    print(f"Distinct Orders          : {len(clean_orders) + len(review_orders)}")
    print(f"  - Clean                : {len(clean_orders)} (Total Lines: {total_clean_lines})")
    print(f"  - Review (genuine)     : {len(review_orders)}")
    print(f"Duplicate Re-scans Logged: {len(duplicate_log_orders)}")
    print("\nBreakdown Review Reasons:")
    for reason, count in sorted(issue_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  - [{count}x] {reason}")
    print("="*40 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Driftwood Order Intake Pipeline")
    parser.add_argument("--batch", type=str, default="batch1", help="Folder path batch order (default: batch1)")
    args = parser.parse_args()

    run_pipeline(args.batch)