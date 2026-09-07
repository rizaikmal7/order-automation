import os
import re
import pandas as pd
from typing import List, Dict, Any, Tuple, Set, Optional
from schemas import OrderFormRaw
from master_data import MasterDataLoader

class OrderValidator:
    def __init__(
        self,
        master_data: MasterDataLoader,
        clean_csv_path: str = "clean_orders.csv",
        review_csv_path: str = "review_queue.csv",
        duplicate_log_csv_path: str = "duplicate_log.csv"
    ):
        self.master = master_data
        self.seen_order_fingerprints: Set[str] = set()
        self.seen_order_numbers: Dict[str, str] = {}
        self._load_existing_fingerprints_from_clean(clean_csv_path)
        self._load_existing_fingerprints_from_review(review_csv_path)
        self._load_existing_fingerprints_from_duplicate_log(duplicate_log_csv_path)

    def _load_existing_fingerprints_from_clean(self, clean_csv_path: str):
        if os.path.exists(clean_csv_path) and os.path.getsize(clean_csv_path) > 0:
            try:
                df = pd.read_csv(clean_csv_path)
                if not df.empty and "Order No" in df.columns:
                    for order_no, group in df.groupby("Order No"):
                        items_sig = []
                        for _, row in group.iterrows():
                            clean_style = self.master._clean_style_code(row.get('Style Code'))
                            norm_color = self.master.normalize_color(row.get('Color')) or str(row.get('Color')).strip()
                            items_sig.append(f"{clean_style}_{norm_color}_{row.get('Qty Total')}")
                        fp = f"{str(order_no).strip().upper()}::{'|'.join(sorted(items_sig))}"
                        self.seen_order_fingerprints.add(fp)
                        self.seen_order_numbers[str(order_no).strip().upper()] = fp
            except Exception:
                pass

    def _load_existing_fingerprints_from_review(self, review_csv_path: str):
        """Load fingerprint dari review_queue.csv (order genuine-issue, bukan duplikat)."""
        if os.path.exists(review_csv_path) and os.path.getsize(review_csv_path) > 0:
            try:
                df = pd.read_csv(review_csv_path)
                if not df.empty and "_Fingerprint" in df.columns:
                    for _, row in df.iterrows():
                        fp = str(row.get("_Fingerprint", "")).strip()
                        if fp and fp.lower() != "nan":
                            self.seen_order_fingerprints.add(fp)
            except Exception:
                pass

    def _load_existing_fingerprints_from_duplicate_log(self, duplicate_log_csv_path: str):
        """[BARU] Load fingerprint dari duplicate_log.csv juga, supaya duplikat-dari-duplikat
        (re-scan ke-3, ke-4, dst dari order yang sama) tetap terdeteksi lintas proses/batch."""
        if os.path.exists(duplicate_log_csv_path) and os.path.getsize(duplicate_log_csv_path) > 0:
            try:
                df = pd.read_csv(duplicate_log_csv_path)
                if not df.empty and "_Fingerprint" in df.columns:
                    for _, row in df.iterrows():
                        fp = str(row.get("_Fingerprint", "")).strip()
                        if fp and fp.lower() != "nan":
                            self.seen_order_fingerprints.add(fp)
            except Exception:
                pass

    def _generate_order_fingerprint(self, raw_order: OrderFormRaw) -> str:
        if not raw_order.order_no:
            return ""
        items_sig = []
        if raw_order.line_items:
            for item in raw_order.line_items:
                norm_color = self.master.normalize_color(item.color) or str(item.color).strip()
                clean_style = self.master._clean_style_code(item.style_code)
                items_sig.append(f"{clean_style}_{norm_color}_{item.qty_total}")
        return f"{raw_order.order_no.strip().upper()}::{'|'.join(sorted(items_sig))}"

    def validate_order(self, raw_order: OrderFormRaw, file_source: str = "") -> Tuple[bool, List[str], Dict[str, Any]]:
        reasons: List[str] = []
        skipped_notes: Set[str] = set()

        # 1. Header Fields & Account Resolution
        account_info = None
        if raw_order.account_code:
            account_info = self.master.get_account(raw_order.account_code)
            if not account_info:
                reasons.append(f"Account Code '{raw_order.account_code}' not found in accounts_master.csv")
        elif raw_order.account_name:
            account_info = self.master.get_account_by_name(raw_order.account_name)
            if account_info:
                raw_order.account_code = account_info["account_code"]
            else:
                reasons.append(f"Account Name '{raw_order.account_name}' not found in accounts_master.csv")
        else:
            reasons.append("Missing Account Code and Account Name")

        if not raw_order.order_no:
            reasons.append("Missing Order Number")
        if not raw_order.ship_date:
            reasons.append("Missing Ship Date")

        if account_info and raw_order.account_name:
            form_acc_name = raw_order.account_name.lower().strip()
            master_acc_name = account_info["account_name"].lower().strip()
            if form_acc_name and form_acc_name not in master_acc_name and master_acc_name not in form_acc_name:
                reasons.append(
                    f"Account Name Mismatch: Form says '{raw_order.account_name}', but {raw_order.account_code} is '{account_info['account_name']}'"
                )

        # 2. Actionable OCR Notes
        SUPPRESSED_MARKERS = (
            "does not match", "doesn't match", "mismatch",
            "calculated order value", "sum of", "qty_total",
            "total units", "order value:", "visual check confirms",
            "is null: visual check", "is empty"
        )

        if raw_order.confidence in ["low", "medium"]:
            if raw_order.extraction_notes:
                for note in raw_order.extraction_notes:
                    note_lower = note.lower()
                    if "account code is missing" in note_lower and account_info:
                        skipped_notes.add(note)
                        continue
                    if any(m in note_lower for m in SUPPRESSED_MARKERS):
                        skipped_notes.add(note)
                        continue
                    reasons.append(note.strip())
            else:
                reasons.append("Visual ambiguity detected on form scan/handwriting")

        # 3. Operational Notes Guard
        order_notes = getattr(raw_order, "notes", None)
        if order_notes:
            notes_upper = str(order_notes).upper()
            flag_keywords = ["HOLD", "DO NOT RELEASE", "CANCEL", "CONFIRM", "PENDING"]
            if any(kw in notes_upper for kw in flag_keywords):
                reasons.append(f"Operational Note Requires Human Review: '{order_notes}'")

        # 4. Duplicate Order / Re-scan Detection & PO Collision Guard
        fingerprint = self._generate_order_fingerprint(raw_order)
        order_no_key = raw_order.order_no.strip().upper() if raw_order.order_no else ""
        is_duplicate = False

        if fingerprint and fingerprint in self.seen_order_fingerprints:
            reasons.append(f"Duplicate Order Detected: Order '{raw_order.order_no}' with identical items has already been processed")
            is_duplicate = True
        elif order_no_key and order_no_key in self.seen_order_numbers and self.seen_order_numbers[order_no_key] != fingerprint:
            reasons.append(
                f"Order Number Reused With Different Content: '{raw_order.order_no}' already exists in the "
                f"system with different line items/totals. Requires manual reconciliation before fulfillment "
                f"(possible duplicate PO number, amended order, or data entry error)."
            )

        # 5. Line Items & Master Product Validation
        calculated_order_units = 0
        calculated_order_value = 0.0
        validated_lines = []

        if not raw_order.line_items:
            reasons.append("Order contains no line items")

        for idx, line in enumerate(raw_order.line_items or [], start=1):
            s = line.qty_s or 0
            m = line.qty_m or 0
            l = line.qty_l or 0
            xl = line.qty_xl or 0
            sum_sizes = s + m + l + xl

            if line.qty_total is not None and line.qty_total != sum_sizes:
                msg = f"Line {idx} Qty Mismatch: written qty_total ({line.qty_total}) != S+M+L+XL sum ({sum_sizes})"
                if msg not in reasons:
                    reasons.append(msg)
            elif line.qty_total is None and sum_sizes > 0:
                msg = f"Line {idx} written qty_total is blank (computed from sizes: {sum_sizes})"
                if msg not in reasons:
                    reasons.append(msg)

            line_units = line.qty_total if line.qty_total is not None else sum_sizes

            if line_units == 0:
                msg = f"Line {idx} has zero total quantity — possible phantom row"
                if msg not in reasons:
                    reasons.append(msg)

            calculated_order_units += line_units

            if line.unit_price is None:
                reasons.append(f"Line {idx} missing Unit Price")

            product = self.master.get_product(line.style_code)
            normalized_color = self.master.normalize_color(line.color)

            if not product:
                reasons.append(f"Line {idx} Style Code '{line.style_code}' not found in products_master.csv")
            else:
                if line.unit_price is not None and abs(float(line.unit_price) - product["wholesale_price_usd"]) > 0.01:
                    reasons.append(
                        f"Line {idx} Price Discrepancy: Form price ${line.unit_price} != Catalog price ${product['wholesale_price_usd']}"
                    )
                if normalized_color not in product["available_colors"]:
                    reasons.append(
                        f"Line {idx} Color '{line.color}' (normalized: '{normalized_color}') invalid for Style {line.style_code}. Available: {sorted(list(product['available_colors']))}"
                    )

            line_val = (line_units * float(line.unit_price)) if line.unit_price is not None else 0.0
            calculated_order_value += line_val

            validated_lines.append({
                "style_code": line.style_code,
                "description": product["style_name"] if product else (line.description or ""),
                "color_raw": line.color,
                "color_normalized": normalized_color,
                "qty_s": line.qty_s,
                "qty_m": line.qty_m,
                "qty_l": line.qty_l,
                "qty_xl": line.qty_xl,
                "qty_total": line_units,
                "unit_price": line.unit_price,
                "catalog_price": product["wholesale_price_usd"] if product else None,
                "line_total": line_val
            })

        # 6. Order Totals Arithmetic Check
        if raw_order.written_total_units is not None and raw_order.written_total_units != calculated_order_units:
            msg = f"Total Units Mismatch: written ({raw_order.written_total_units}) != sum of lines ({calculated_order_units})"
            if msg not in reasons:
                reasons.append(msg)

        if raw_order.written_order_value is not None and abs(raw_order.written_order_value - calculated_order_value) > 0.01:
            msg = f"Order Value Mismatch: written (${raw_order.written_order_value}) != sum of lines (${calculated_order_value:.2f})"
            if msg not in reasons:
                reasons.append(msg)

        # 7. Fallback Human Review Check
        if raw_order.requires_human_review and len(reasons) == 0:
            if raw_order.extraction_notes:
                for note in raw_order.extraction_notes:
                    if note in skipped_notes:
                        continue
                    note_lower = note.lower()
                    if "account code is missing" in note_lower and account_info:
                        continue
                    if any(m in note_lower for m in SUPPRESSED_MARKERS):
                        continue
                    reasons.append(note.strip())
            elif raw_order.confidence != "high":
                reasons.append("Visual ambiguity detected on form scan/handwriting")

        reasons = [r.strip() for r in reasons if r and r.strip()]
        unique_reasons = list(dict.fromkeys(reasons))
        is_clean = len(unique_reasons) == 0

        final_order_value = calculated_order_value if calculated_order_value > 0 else (raw_order.written_order_value or 0.0)

        processed_data = {
            "file_source": file_source,
            "order_no": raw_order.order_no,
            "sales_rep": raw_order.sales_rep,
            "account_code": raw_order.account_code,
            "account_name": account_info["account_name"] if account_info else (raw_order.account_name or ""),
            "city": account_info["city"] if account_info else (raw_order.ship_to or ""),
            "terms": account_info["terms"] if account_info else "",
            "order_date": raw_order.order_date,
            "ship_date": raw_order.ship_date,
            "confidence": raw_order.confidence,
            "line_count": len(validated_lines),
            "total_units": calculated_order_units,
            "order_value": final_order_value,
            "written_order_value": raw_order.written_order_value,
            "notes": order_notes,
            "extraction_notes": raw_order.extraction_notes,
            "review_reasons": unique_reasons,
            "line_items": validated_lines,
            "is_duplicate": is_duplicate,
            "_fingerprint": fingerprint,
        }

        if fingerprint:
            self.seen_order_fingerprints.add(fingerprint)
            if is_clean and order_no_key:
                self.seen_order_numbers[order_no_key] = fingerprint

        return is_clean, unique_reasons, processed_data