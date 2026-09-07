from typing import Dict, Any

class ConfirmationGenerator:
    @staticmethod
    def generate_message(order_data: Dict[str, Any], is_clean: bool, is_duplicate: bool = False) -> str:
        order_no = order_data.get("order_no") or "[NO ORDER NO]"
        sales_rep = order_data.get("sales_rep") or "Sales Team"
        account_name = order_data.get("account_name") or "[UNKNOWN ACCOUNT]"
        line_count = order_data.get("line_count", 0)
        total_units = order_data.get("total_units", 0)
        order_val = order_data.get("order_value") or 0.0

        if is_duplicate:
            msg = (
                f"Hi {sales_rep},\n\n"
                f"We received another copy of Order {order_no} for {account_name}. "
                f"This order was already submitted and processed previously — no action needed on your end, "
                f"and nothing further will be entered from this duplicate scan.\n\n"
                f"If you believe this is a different order that happens to share the same Order Number, "
                f"please reach out to the office so we can reconcile it manually.\n\n"
                f"Status: Duplicate — Logged, No Action Required."
            )
        elif is_clean:
            msg = (
                f"Hi {sales_rep},\n\n"
                f"Order {order_no} for {account_name} has been successfully processed and verified.\n"
                f"- Line Count: {line_count}\n"
                f"- Total Units: {total_units}\n"
                f"- Total Value: ${order_val:,.2f}\n\n"
                f"Status: Clean / Ready for Fulfillment."
            )
        else:
            reasons = order_data.get("review_reasons", [])
            reasons_formatted = "\n".join([f"  * {r}" for r in reasons]) if reasons else "  * Requires manual review"
            msg = (
                f"Hi {sales_rep},\n\n"
                f"Order {order_no} for {account_name} has been received but flagged for review:\n"
                f"- Line Count: {line_count}\n"
                f"- Total Units (extracted): {total_units}\n"
                f"- Flagged Issues:\n{reasons_formatted}\n\n"
                f"Status: In Review Queue. Our team will verify the details shortly."
            )
        return msg