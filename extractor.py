import os
import re
import json
import time
from pathlib import Path
from typing import Optional
from google import genai
from google.genai import types
from PIL import Image
from schemas import OrderFormRaw

SYSTEM_PROMPT = """
You are an expert OCR and data extraction system for Driftwood Apparel, a wholesale knitwear brand.

Your task is to extract all fields accurately from the wholesale order form image into clean, structured JSON.

Return ONLY valid JSON matching this schema:

{
  "order_no": "string or null",
  "order_date": "YYYY-MM-DD or null",
  "account_name": "string or null",
  "account_code": "string or null",
  "ship_to": "string or null",
  "ship_date": "YYYY-MM-DD or null",
  "sales_rep": "string or null",
  "page_current": integer or null,
  "page_total": integer or null,
  "line_items": [
    {
      "style_code": "string",
      "description": "string",
      "color": "string",
      "qty_s": integer or null,
      "qty_m": integer or null,
      "qty_l": integer or null,
      "qty_xl": integer or null,
      "qty_total": integer or null,
      "unit_price": number or null
    }
  ],
  "written_total_units": integer or null,
  "written_order_value": number or null,
  "notes": "string or null",
  "extraction_notes": [
    "Specific ambiguities, crossed-out text, or unreadable words, e.g., 'Color on line 1 is abbreviated as Ntl'"
  ],
  "confidence": "high" | "medium" | "low",
  "requires_human_review": boolean,
  "review_reasons": [
    "Specific reasons why human review is required, or empty array if clean"
  ]
}

Extraction & Parsing Rules:
1. Extract exact values as written on the paper form. Do not invent missing values.
2. For dates, standardize to YYYY-MM-DD format. If month/day order is ambiguous, record it in extraction_notes.
3. If a size cell is empty/blank, set it to null (not 0). If you are uncertain whether a size cell is blank or contains a faint number, re-examine the image region closely before defaulting to null; only mark a cell as null if it is genuinely empty in the image.
4. Remove currency symbols ($) and commas from numeric fields.
5. In 'notes', extract any special handwritten or printed instructions written at the bottom or margin (e.g., 'HOLD shipment until customer confirms credit terms', 'CANCEL style...').
6. Look for a "Page X of Y" indicator anywhere on the form (often top-right corner). If present, set "page_current" and "page_total" accordingly. If absent, leave both null.
7. In 'extraction_notes', record only genuine visual ambiguities, illegible handwriting, corrections, or abbreviations.

Validation & Confidence Rules:
- If qty_total != sum of S+M+L+XL for any line item, append to review_reasons.
- If written_total_units != sum of all line item qty_total, append to review_reasons.
- If written_order_value != sum of all (qty_total * unit_price), append to review_reasons.
- confidence = "high" if all fields and handwriting are clear and math checks out.
- confidence = "medium" if 1-2 fields are ambiguous or contain abbreviations/corrections.
- confidence = "low" if 3+ fields are ambiguous, unreadable, or any math verification fails.
- If any critical field is null (ship_date, unit_price, or missing both account_code and account_name), confidence must be at least "medium" and requires_human_review = true.
- requires_human_review = true if confidence is "medium" or "low". This is non-negotiable — never set requires_human_review = false when confidence is medium or low.

STRICT RULES FOR CONFIDENCE & EXTRACTION NOTES:
1. 'confidence' can be 'high', 'medium', or 'low'.
   - Set to 'high' if all handwritten or printed text, line items, quantities, and prices are clearly readable.
   - Set to 'medium' or 'low' ONLY if specific characters, numbers, or words are smudged, crossed out, or visually ambiguous.
2. MANDATORY: Whenever 'confidence' is 'medium' or 'low', or 'requires_human_review' is true, you MUST populate 'extraction_notes' with explicit, field-level descriptions.
   - Format: "Field '[field_name]' on Line [X] is ambiguous: '[unclear_value]' (could be '[alternative]')"
   - Example: "Field 'unit_price' on Line 2 is smudged: looks like '$78' or '$70'"
   - Example: "Field 'qty_m' on Line 1 has a handwriting correction: written '4' over '2'"
3. Do NOT perform or narrate arithmetic verification (adding up totals, cross-checking S+M+L+XL sums, or recomputing order value) inside 'extraction_notes'. That verification is done by the downstream system using your raw extracted numbers — your job is ONLY to report what is written on the page.
4. NEVER include your internal reasoning, verification narration, or self-corrections (e.g. "Wait, let me recompute", "Actually that's correct", "Calculated value matches written value") inside 'extraction_notes'.
5. Each entry must strictly follow the format: "Field '[field_name]' on Line [X] is ambiguous: '[unclear_value]' (could be '[alternative]')". If a field is unambiguous, do NOT create a note about it — omit it entirely.
6. NEVER leave 'extraction_notes' empty if confidence is not 'high'.

CRITICAL NEGATIVE CONSTRAINTS FOR 'extraction_notes':
1. STRICTLY FORBIDDEN: Do NOT write any math calculations, arithmetic verifications, sum cross-checks, self-correction monologues, or words like "Wait", "The math is correct", "mismatch", "inconsistent", or "no discrepancy". You are strictly an OCR data transcriber, NOT a calculator.
2. STRICTLY FORBIDDEN: Do NOT write confirmation phrases such as "visual check confirms...", "visual check confirms cell is empty", or "cell is blank". Blank size cells must simply be extracted as null without creating a note.
3. STRICT MINIMALISM: 'extraction_notes' must ONLY contain genuine physical defects on paper (e.g., physical ink smudges, crossed-out handwritten numbers, or illegible handwriting). If the page is clear, 'extraction_notes' MUST BE an empty list [].
"""

class OrderExtractor:
    def __init__(self, api_key: Optional[str] = None, model_name: str = "gemini-3.5-flash-lite"):
        self.client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))
        self.model_name = model_name

    def extract_order_from_image(self, image_path: str | Path, max_retries: int = 4) -> OrderFormRaw:
        img = Image.open(image_path)
        delay = 5

        for attempt in range(1, max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=[img, "Extract all order details from this wholesale form following the system instructions strictly."],
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        response_mime_type="application/json",
                        temperature=0.0
                    )
                )
                
                raw_text = response.text.strip()
                if raw_text.startswith("```json"):
                    raw_text = raw_text[7:]
                if raw_text.startswith("```"):
                    raw_text = raw_text[3:]
                if raw_text.endswith("```"):
                    raw_text = raw_text[:-3]
                raw_text = raw_text.strip()

                parsed_json = json.loads(raw_text)
                return OrderFormRaw.model_validate(parsed_json)

            except Exception as e:
                err_msg = str(e)
                if ("503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg) and attempt < max_retries:
                    print(f"      [RETRY] Server sibuk ({err_msg[:40]}...). Mencoba ulang dalam {delay} detik (Percobaan {attempt}/{max_retries})...")
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise e