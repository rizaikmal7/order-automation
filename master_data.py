import os
import re
import pandas as pd
from typing import Dict, Any, Optional, Set

class MasterDataLoader:
    def __init__(self, accounts_path: str = "accounts_master.csv", products_path: str = "products_master.csv"):
        self.accounts_path = accounts_path
        self.products_path = products_path
        self.accounts_by_code: Dict[str, Dict[str, Any]] = {}
        self.accounts_by_name: Dict[str, Dict[str, Any]] = {}
        self.products: Dict[str, Dict[str, Any]] = {}
        
        # Kamus normalisasi singkatan & variasi warna resmi Driftwood Apparel
        self.color_map = {
            "ntl": "Natural",
            "nat": "Natural",
            "natural": "Natural",
            "blk": "Black",
            "black": "Black",
            "wht": "White",
            "white": "White",
            "nvy": "Night Navy",
            "navy": "Night Navy",
            "night navy": "Night Navy",
            "sea glass": "Sea Glass",
            "seaglass": "Sea Glass",
            "sea-glass": "Sea Glass",
            "sg": "Sea Glass",
            "h. grey": "Heather Grey",
            "h grey": "Heather Grey",
            "h.grey": "Heather Grey",
            "heather grey": "Heather Grey",
            "heather gray": "Heather Grey",
            "h gray": "Heather Grey",
            "grey": "Heather Grey",
            "gray": "Heather Grey",
            "sage": "Sage",
            "terracotta": "Terracotta",
            "terra cotta": "Terracotta",
            "terra-cotta": "Terracotta",
            "oatmeal": "Oatmeal Heather",
            "oat": "Oatmeal Heather",
            "oatmeal heather": "Oatmeal Heather",
            "charcoal": "Charcoal",
            "chrc": "Charcoal",
            "olive": "Olive",
            "olv": "Olive",
            "dusty rose": "Dusty Rose",
            "rose": "Dusty Rose",
            "mustard": "Mustard",
            "taupe": "Taupe",
            "rust": "Rust Clay",
            "rust clay": "Rust Clay",
            "sand": "Sand",
            "fog grey": "Fog Grey",
            "cream": "Cream",
            "moss": "Moss"
        }
        
        self._load_accounts()
        self._load_products()

    def _clean_style_code(self, style_raw: Any) -> str:
        """Membersihkan style code dari spasi, prefix '#', atau kata 'Style'."""
        if not style_raw:
            return ""
        s = str(style_raw).strip()
        s = re.sub(r"^(style|st|#)\s*", "", s, flags=re.IGNORECASE)
        return s.strip().upper()

    def normalize_color(self, color_raw: Any) -> str:
        """Menstandardisasi singkatan dan variasi penulisan warna."""
        if not color_raw:
            return ""
        c = str(color_raw).strip().lower()
        c = re.sub(r"[\s\-_.]+", " ", c).strip()
        return self.color_map.get(c, str(color_raw).strip().title())

    def _load_accounts(self):
        """Memuat master data akun dari accounts_master.csv."""
        if not os.path.exists(self.accounts_path):
            return
        try:
            df = pd.read_csv(self.accounts_path)
            for _, row in df.iterrows():
                code = str(row.get("account_code", "")).strip().upper()
                name = str(row.get("account_name", "")).strip()
                city = str(row.get("city", "")).strip()
                terms = str(row.get("terms", "")).strip()
                
                info = {
                    "account_code": code,
                    "account_name": name,
                    "city": city,
                    "terms": terms
                }
                
                if code:
                    self.accounts_by_code[code] = info
                if name:
                    self.accounts_by_name[name.lower()] = info
        except Exception as e:
            print(f"[WARN] Failed to load accounts master: {e}")

    def _load_products(self):
        """Memuat master data produk dari products_master.csv."""
        if not os.path.exists(self.products_path):
            return
        try:
            df = pd.read_csv(self.products_path)
            for _, row in df.iterrows():
                raw_code = row.get("style_code")
                clean_code = self._clean_style_code(raw_code)
                name = str(row.get("style_name", "")).strip()
                
                try:
                    price = float(str(row.get("wholesale_price_usd", "0")).replace("$", "").replace(",", ""))
                except Exception:
                    price = 0.0
                
                # FIX: Mendukung delimiter pipa (|) maupun koma (,)
                colors_raw = re.split(r"[,|]", str(row.get("available_colors", "")))
                available_colors = set()
                for cr in colors_raw:
                    cr_clean = cr.strip()
                    if cr_clean:
                        norm = self.normalize_color(cr_clean)
                        if norm:
                            available_colors.add(norm)
                
                self.products[clean_code] = {
                    "style_code": clean_code,
                    "style_name": name,
                    "wholesale_price_usd": price,
                    "available_colors": available_colors
                }
        except Exception as e:
            print(f"[WARN] Failed to load products master: {e}")

    def get_account(self, account_code: Optional[str]) -> Optional[Dict[str, Any]]:
        """Mencari data akun berdasarkan kode akun (ACC-XXXX)."""
        if not account_code:
            return None
        code = str(account_code).strip().upper()
        return self.accounts_by_code.get(code)

    def get_account_by_name(self, account_name: Optional[str]) -> Optional[Dict[str, Any]]:
        """Mencari data akun berdasarkan nama toko (fallback form Rev. 2026-08)."""
        if not account_name:
            return None
        name_clean = str(account_name).strip().lower()
        
        if name_clean in self.accounts_by_name:
            return self.accounts_by_name[name_clean]
        
        for master_name_lower, info in self.accounts_by_name.items():
            if name_clean in master_name_lower or master_name_lower in name_clean:
                return info
                
        return None

    def get_product(self, style_code: Optional[str]) -> Optional[Dict[str, Any]]:
        """Mencari data produk dan daftar warna yang tersedia berdasarkan style code."""
        if not style_code:
            return None
        clean_code = self._clean_style_code(style_code)
        return self.products.get(clean_code)