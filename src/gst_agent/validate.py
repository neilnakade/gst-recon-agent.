"""Safety net between extraction and matching.

The AI sometimes can't read an invoice (blurry photo, cut-off header) and answers
"unknown" or a made-up placeholder. If those went straight into the matcher, the
invoice would be reported as "missing in 2B", which is a false alarm.

Instead, every extracted invoice is checked with plain rules. Anything doubtful is
set aside in a "needs review" list with the reasons, and only clean invoices are matched.

Rules:
  1. Supplier name, GSTIN and invoice number must not be blank or a placeholder ("unknown").
  2. GSTIN must have the valid 15-character format.
  3. The date must be a real date, not before GST started (2017), not in the future.
  4. The date should fall in the batch's usual month (catches day/month swaps like 05/09 vs 09/05).
  5. If you give your own GSTIN, the supplier GSTIN must not be it (buyer details taken by mistake).
  6. Tax structure: IGST and CGST/SGST must not appear together, and CGST must equal SGST.
  7. Total tax / taxable value must be a standard GST rate (catches misread amounts).
"""
import re
from collections import Counter
from datetime import date

PLACEHOLDERS = {"", "unknown", "not provided", "n/a", "na", "none", "null", "nil", "-",
                "not available", "not found", "not visible", "unreadable", "illegible"}
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
# Current slabs are 0/5/18/40 (since 22 Sep 2025). Older invoices can still carry 12/28, and
# some goods have special rates, so those are accepted too.
STANDARD_RATES = [0, 0.1, 0.25, 1.5, 3, 5, 12, 18, 28, 40]
RATE_TOLERANCE = 0.15  # percentage points
GST_START = date(2017, 7, 1)
TEXT_FIELDS = [("supplier_name", "supplier name"), ("supplier_gstin", "supplier GSTIN"),
               ("invoice_no", "invoice number")]


def _is_blank(value) -> bool:
    return str(value).strip().lower() in PLACEHOLDERS


def _parse_date(text) -> date | None:
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError:
        return None


def usual_month(books: list[dict]) -> str | None:
    """The most common YYYY-MM among valid dates, if there are enough invoices to tell."""
    months = [d.strftime("%Y-%m") for b in books if (d := _parse_date(b.get("invoice_date")))]
    if len(months) < 5:
        return None
    return Counter(months).most_common(1)[0][0]


def check_invoice(book: dict, month: str | None = None, buyer_gstin: str | None = None) -> list[str]:
    reasons = []

    for key, label in TEXT_FIELDS:
        if _is_blank(book.get(key, "")):
            reasons.append(f"{label} is missing or a placeholder ({book.get(key, '')!r})")

    gstin = str(book.get("supplier_gstin", "")).strip().upper()
    if not _is_blank(gstin):
        if not GSTIN_RE.match(gstin):
            reasons.append(f"supplier GSTIN {gstin!r} is not a valid 15-character GSTIN")
        elif buyer_gstin and gstin == buyer_gstin.strip().upper():
            reasons.append("supplier GSTIN is your own GSTIN (buyer details taken by mistake?)")

    d = _parse_date(book.get("invoice_date"))
    if d is None:
        reasons.append(f"invoice date {book.get('invoice_date')!r} is not a real date")
    elif d < GST_START:
        reasons.append(f"invoice date {d} is before GST began (placeholder date?)")
    elif d > date.today():
        reasons.append(f"invoice date {d} is in the future")
    elif month and d.strftime("%Y-%m") != month:
        reasons.append(f"invoice date {d} is outside the usual month {month} "
                       "(check day/month order)")

    taxable = float(book.get("taxable_value", 0) or 0)
    cgst, sgst, igst = (float(book.get(k, 0) or 0) for k in ("cgst", "sgst", "igst"))
    if igst > 0 and (cgst > 0 or sgst > 0):
        reasons.append("IGST and CGST/SGST both present")
    if abs(cgst - sgst) > 0.011:
        reasons.append(f"CGST ({cgst:,.2f}) and SGST ({sgst:,.2f}) differ")
    if taxable <= 0:
        reasons.append("taxable value is zero or negative")
    else:
        rate = (cgst + sgst + igst) / taxable * 100
        if not any(abs(rate - r) <= RATE_TOLERANCE for r in STANDARD_RATES):
            reasons.append(f"tax is {rate:.2f}% of the taxable value, not a standard GST rate "
                           "(check the amounts)")
    return reasons


def split_books(books: list[dict], buyer_gstin: str | None = None) -> tuple[list[dict], list[dict]]:
    """Returns (clean invoices, needs-review items). Each review item says why."""
    month = usual_month(books)
    clean, review = [], []
    for b in books:
        reasons = check_invoice(b, month, buyer_gstin)
        if reasons:
            review.append({"source": b.get("_source", ""), "supplier_name": b.get("supplier_name", ""),
                           "invoice_no": b.get("invoice_no", ""), "reasons": reasons})
        else:
            clean.append(b)
    return clean, review
