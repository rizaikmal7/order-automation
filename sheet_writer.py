import os
import csv
import pandas as pd
from typing import List, Dict, Any, Optional

try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False

class SheetWriter:
    def __init__(
        self,
        credentials_path: str = "credentials.json",
        spreadsheet_name: str = "Driftwood Order Intake",
        clean_csv_path: str = "clean_orders.csv",
        review_csv_path: str = "review_queue.csv",
        duplicate_log_csv_path: str = "duplicate_log.csv",
        confirmations_txt_path: str = "confirmations.txt"
    ):
        self.credentials_path = credentials_path
        self.spreadsheet_name = spreadsheet_name
        self.clean_csv_path = clean_csv_path
        self.review_csv_path = review_csv_path
        self.duplicate_log_csv_path = duplicate_log_csv_path
        self.confirmations_txt_path = confirmations_txt_path
        self.gc = None
        self.sh = None
        self._init_gspread()

    def _init_gspread(self):
        """Menginisialisasi koneksi OAuth Service Account ke Google Sheets API."""
        if not GSPREAD_AVAILABLE:
            return
        if not os.path.exists(self.credentials_path):
            return
        try:
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
            creds = Credentials.from_service_account_file(self.credentials_path, scopes=scopes)
            self.gc = gspread.authorize(creds)
            self.sh = self.gc.open(self.spreadsheet_name)
        except Exception as e:
            print(f"[WARN] Could not connect to Google Sheets: {e}")

    def save_results(
        self,
        clean_orders: List[Dict[str, Any]],
        review_orders: List[Dict[str, Any]],
        confirmations: List[Dict[str, Any]],
        duplicate_log_orders: Optional[List[Dict[str, Any]]] = None
    ):
        """Menyimpan seluruh hasil pipeline ke CSV lokal, TXT konfirmasi, dan Google Sheets."""
        duplicate_log_orders = duplicate_log_orders or []
        self._save_clean_csv(clean_orders)
        self._save_review_csv(review_orders)
        self._save_duplicate_log_csv(duplicate_log_orders)
        self._save_confirmations_txt(confirmations)
        self._sync_google_sheets(clean_orders, review_orders, duplicate_log_orders)

    def _save_clean_csv(self, clean_orders: List[Dict[str, Any]]):
        """Menuliskan data baris item dari order yang lolos validasi ke clean_orders.csv."""
        rows = []
        for order in clean_orders:
            for item in order.get("line_items", []):
                rows.append({
                    "Order No": order.get("order_no", ""),
                    "Order Date": order.get("order_date", ""),
                    "Account Code": order.get("account_code", ""),
                    "Account Name": order.get("account_name", ""),
                    "Ship To": order.get("city", ""),
                    "Ship Date": order.get("ship_date", ""),
                    "Sales Rep": order.get("sales_rep", ""),
                    "Terms": order.get("terms", ""),
                    "Style Code": item.get("style_code", ""),
                    "Description": item.get("description", ""),
                    "Color": item.get("color_normalized", item.get("color_raw", "")),
                    "Qty S": item.get("qty_s") if item.get("qty_s") is not None else "",
                    "Qty M": item.get("qty_m") if item.get("qty_m") is not None else "",
                    "Qty L": item.get("qty_l") if item.get("qty_l") is not None else "",
                    "Qty XL": item.get("qty_xl") if item.get("qty_xl") is not None else "",
                    "Qty Total": item.get("qty_total", 0),
                    "Unit Price": f"${item.get('unit_price', 0.0):.2f}" if item.get("unit_price") is not None else "",
                    "Line Total": f"${item.get('line_total', 0.0):.2f}" if item.get("line_total") is not None else "",
                    "Confidence": str(order.get("confidence", "high")).capitalize(),
                    "File Source": order.get("file_source", "")
                })

        df = pd.DataFrame(rows)
        file_exists = os.path.exists(self.clean_csv_path) and os.path.getsize(self.clean_csv_path) > 0
        if not df.empty:
            df.to_csv(self.clean_csv_path, mode="a", index=False, header=not file_exists)
        elif not file_exists:
            cols = [
                "Order No", "Order Date", "Account Code", "Account Name", "Ship To",
                "Ship Date", "Sales Rep", "Terms", "Style Code", "Description",
                "Color", "Qty S", "Qty M", "Qty L", "Qty XL", "Qty Total",
                "Unit Price", "Line Total", "Confidence", "File Source"
            ]
            pd.DataFrame(columns=cols).to_csv(self.clean_csv_path, index=False)

    def _save_review_csv(self, review_orders: List[Dict[str, Any]]):
        """Menuliskan ringkasan order tertahan (genuine issues) beserta reason ke review_queue.csv."""
        rows = []
        for order in review_orders:
            reasons = order.get("review_reasons", [])
            reasons_str = " | ".join(reasons) if isinstance(reasons, list) else str(reasons)
            rows.append({
                "Order No": order.get("order_no", ""),
                "File Source": order.get("file_source", ""),
                "Sales Rep": order.get("sales_rep", ""),
                "Account Code": order.get("account_code", ""),
                "Account Name": order.get("account_name", ""),
                "Ship Date": order.get("ship_date", ""),
                "Confidence": str(order.get("confidence", "low")).capitalize(),
                "Total Units": order.get("total_units", 0),
                "Order Value (Calculated)": f"${order.get('order_value', 0.0):.2f}" if order.get("order_value") is not None else "$0.00",
                "Order Value (Written on Form)": f"${order.get('written_order_value', 0.0):.2f}" if order.get("written_order_value") is not None else "",
                "Review Reasons": reasons_str,
                "Extraction Notes": " | ".join(order.get("extraction_notes", [])),
                "_Fingerprint": order.get("_fingerprint", ""),
            })

        expected_cols = [
            "Order No", "File Source", "Sales Rep", "Account Code", "Account Name",
            "Ship Date", "Confidence", "Total Units",
            "Order Value (Calculated)", "Order Value (Written on Form)",
            "Review Reasons", "Extraction Notes", "_Fingerprint"
        ]

        df = pd.DataFrame(rows)
        file_exists = os.path.exists(self.review_csv_path) and os.path.getsize(self.review_csv_path) > 0

        if not df.empty:
            df = df[expected_cols]
            df.to_csv(self.review_csv_path, mode="a", index=False, header=not file_exists)
        elif not file_exists:
            pd.DataFrame(columns=expected_cols).to_csv(self.review_csv_path, index=False)

    def _save_duplicate_log_csv(self, duplicate_orders: List[Dict[str, Any]]):
        """Log re-scan yang terdeteksi identik dengan order yang sudah pernah diproses."""
        rows = []
        for order in duplicate_orders:
            reasons = order.get("review_reasons", [])
            reasons_str = " | ".join(reasons) if isinstance(reasons, list) else str(reasons)
            rows.append({
                "Order No": order.get("order_no", ""),
                "File Source": order.get("file_source", ""),
                "Sales Rep": order.get("sales_rep", ""),
                "Detected Reason": reasons_str,
                "_Fingerprint": order.get("_fingerprint", ""),
            })
        cols = ["Order No", "File Source", "Sales Rep", "Detected Reason", "_Fingerprint"]
        df = pd.DataFrame(rows)
        file_exists = os.path.exists(self.duplicate_log_csv_path) and os.path.getsize(self.duplicate_log_csv_path) > 0
        if not df.empty:
            df = df[cols]
            df.to_csv(self.duplicate_log_csv_path, mode="a", index=False, header=not file_exists)
        elif not file_exists:
            pd.DataFrame(columns=cols).to_csv(self.duplicate_log_csv_path, index=False)

    def _save_confirmations_txt(self, confirmations: List[Dict[str, Any]]):
        """Menyimpan seluruh draft konfirmasi pesanan (Clean, Review, maupun Duplicate) ke confirmations.txt."""
        divider = "=" * 80
        with open(self.confirmations_txt_path, "a", encoding="utf-8") as f:
            for conf in confirmations:
                f.write(conf.get("message", "") + "\n\n" + divider + "\n\n")

    def _sync_google_sheets(
        self,
        clean_orders: List[Dict[str, Any]],
        review_orders: List[Dict[str, Any]],
        duplicate_log_orders: Optional[List[Dict[str, Any]]] = None
    ):
        """Menyinkronkan data langsung ke Google Sheets Spreadsheet."""
        duplicate_log_orders = duplicate_log_orders or []
        if not self.sh:
            return

        # 1. Sinkronisasi Tab Clean Orders
        if clean_orders:
            try:
                ws_clean = self.sh.worksheet("Clean Orders")
            except Exception:
                ws_clean = self.sh.add_worksheet(title="Clean Orders", rows=1000, cols=20)

            if not ws_clean.row_values(1):
                ws_clean.append_row([
                    "Order No", "Order Date", "Account Code", "Account Name", "Ship To",
                    "Ship Date", "Sales Rep", "Terms", "Style Code", "Description",
                    "Color", "Qty S", "Qty M", "Qty L", "Qty XL", "Qty Total",
                    "Unit Price", "Line Total", "Confidence", "File Source"
                ])

            clean_rows = []
            for order in clean_orders:
                for item in order.get("line_items", []):
                    clean_rows.append([
                        order.get("order_no", ""),
                        order.get("order_date", ""),
                        order.get("account_code", ""),
                        order.get("account_name", ""),
                        order.get("city", ""),
                        order.get("ship_date", ""),
                        order.get("sales_rep", ""),
                        order.get("terms", ""),
                        item.get("style_code", ""),
                        item.get("description", ""),
                        item.get("color_normalized", item.get("color_raw", "")),
                        item.get("qty_s", ""),
                        item.get("qty_m", ""),
                        item.get("qty_l", ""),
                        item.get("qty_xl", ""),
                        item.get("qty_total", 0),
                        f"${item.get('unit_price', 0.0):.2f}" if item.get("unit_price") is not None else "",
                        f"${item.get('line_total', 0.0):.2f}" if item.get("line_total") is not None else "",
                        str(order.get("confidence", "high")).capitalize(),
                        order.get("file_source", "")
                    ])
            if clean_rows:
                ws_clean.append_rows(clean_rows)
                print(f"[INFO] Synced {len(clean_rows)} line(s) to Google Sheet tab 'Clean Orders'")

        # 2. Sinkronisasi Tab Review Queue
        if review_orders:
            try:
                ws_review = self.sh.worksheet("Review Queue")
            except Exception:
                ws_review = self.sh.add_worksheet(title="Review Queue", rows=1000, cols=12)

            if not ws_review.row_values(1):
                ws_review.append_row([
                    "Order No", "File Source", "Sales Rep", "Account Code", "Account Name",
                    "Ship Date", "Confidence", "Total Units",
                    "Order Value (Calculated)", "Order Value (Written on Form)",
                    "Review Reasons", "Extraction Notes"
                ])

            review_rows = []
            for order in review_orders:
                reasons = order.get("review_reasons", [])
                reasons_str = " | ".join(reasons) if isinstance(reasons, list) else str(reasons)
                review_rows.append([
                    order.get("order_no", ""),
                    order.get("file_source", ""),
                    order.get("sales_rep", ""),
                    order.get("account_code", ""),
                    order.get("account_name", ""),
                    order.get("ship_date", ""),
                    str(order.get("confidence", "low")).capitalize(),
                    order.get("total_units", 0),
                    f"${order.get('order_value', 0.0):.2f}" if order.get("order_value") is not None else "$0.00",
                    f"${order.get('written_order_value', 0.0):.2f}" if order.get("written_order_value") is not None else "",
                    reasons_str,
                    " | ".join(order.get("extraction_notes", []))
                ])

            if review_rows:
                ws_review.append_rows(review_rows)
                print(f"[INFO] Synced {len(review_rows)} rows to Google Sheet tab 'Review Queue'")

        # 3. Sinkronisasi Tab Duplicate Log
        if duplicate_log_orders:
            try:
                ws_dup = self.sh.worksheet("Duplicate Log")
            except Exception:
                ws_dup = self.sh.add_worksheet(title="Duplicate Log", rows=500, cols=6)

            if not ws_dup.row_values(1):
                ws_dup.append_row(["Order No", "File Source", "Sales Rep", "Detected Reason"])

            dup_rows = []
            for order in duplicate_log_orders:
                reasons = order.get("review_reasons", [])
                reasons_str = " | ".join(reasons) if isinstance(reasons, list) else str(reasons)
                dup_rows.append([
                    order.get("order_no", ""),
                    order.get("file_source", ""),
                    order.get("sales_rep", ""),
                    reasons_str,
                ])

            if dup_rows:
                ws_dup.append_rows(dup_rows)
                print(f"[INFO] Synced {len(dup_rows)} rows to Google Sheet tab 'Duplicate Log'")