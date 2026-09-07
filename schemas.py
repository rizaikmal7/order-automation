from typing import List, Optional, Literal
from pydantic import BaseModel, Field

class OrderLineItemRaw(BaseModel):
    style_code: Optional[str] = Field(None, description="Style code, e.g., 800")
    description: Optional[str] = Field(None, description="Style name or written description")
    color: Optional[str] = Field(None, description="Color string as written, e.g., Ntl, Sea Glass")
    qty_s: Optional[int] = Field(None, description="Quantity for Size S, null if empty")
    qty_m: Optional[int] = Field(None, description="Quantity for Size M, null if empty")
    qty_l: Optional[int] = Field(None, description="Quantity for Size L, null if empty")
    qty_xl: Optional[int] = Field(None, description="Quantity for Size XL, null if empty")
    qty_total: Optional[int] = Field(None, description="Written total quantity for this line")
    unit_price: Optional[float] = Field(None, description="Unit price without currency symbol")

class OrderFormRaw(BaseModel):
    order_no: Optional[str] = Field(None, description="Order number")
    order_date: Optional[str] = Field(None, description="Order date in YYYY-MM-DD or null")
    account_name: Optional[str] = Field(None, description="Account name")
    account_code: Optional[str] = Field(None, description="Account code, e.g., ACC-1042")
    ship_to: Optional[str] = Field(None, description="Shipping destination or store location")
    ship_date: Optional[str] = Field(None, description="Ship date in YYYY-MM-DD or null")
    sales_rep: Optional[str] = Field(None, description="Sales representative name")
    line_items: List[OrderLineItemRaw] = Field(default_factory=list, description="Extracted line items")
    written_total_units: Optional[int] = Field(None, description="Written total units at bottom")
    written_order_value: Optional[float] = Field(None, description="Written total value without currency symbol")
    notes: Optional[str] = Field(None, description="Special instructions or notes written on the form")
    
    # [FIX F] Multi-page indicators
    page_current: Optional[int] = Field(None, description="Page number if form says 'Page X of Y', else null")
    page_total: Optional[int] = Field(None, description="Total pages if form says 'Page X of Y', else null")
    
    extraction_notes: List[str] = Field(default_factory=list, description="Ambiguities or notes")
    confidence: Literal["high", "medium", "low"] = Field("low", description="Confidence assessment")
    requires_human_review: bool = Field(True, description="Flag indicating human review requirement")
    review_reasons: List[str] = Field(default_factory=list, description="Reasons for review")