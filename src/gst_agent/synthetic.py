"""Synthetic data generator for the GST reconciliation agent.

Creates, under data/synthetic/:
  invoices/*.pdf      purchase invoices (the "books" side)
  gstr2b.json         simulated GSTR-2B (the "portal" side) with injected problems
  ground_truth.json   the correct extracted fields + the expected recon status

All data is fake. GSTINs follow the 15-character format but their checksum
digit is random, so they are NOT real or verifiable numbers.

Run from the repo root:
    python -m src.gst_agent.synthetic --n 60 --seed 42
"""
import argparse
import json
import random
import string
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

STATE_CODES = {"27": "Maharashtra", "29": "Karnataka", "24": "Gujarat", "07": "Delhi"}
RATES = [5, 18]
MY_STATE = "27"  # the CA's client is in Maharashtra
SUPPLIER_NAMES = [
    "Shree Traders", "Om Enterprises", "Sai Packaging Pvt Ltd", "Kalyani Steel Works",
    "Patil Stationers", "Deccan Logistics", "Gupta Electricals", "Bharat Office Supplies",
]


def fake_gstin(state: str, rng: random.Random) -> str:
    letters = "".join(rng.choices(string.ascii_uppercase, k=5))
    digits = "".join(rng.choices(string.digits, k=4))
    pan = letters + digits + rng.choice(string.ascii_uppercase)
    return f"{state}{pan}{rng.randint(1, 9)}Z{rng.choice(string.digits + string.ascii_uppercase)}"


def make_suppliers(rng: random.Random) -> list[dict]:
    suppliers = []
    for name in SUPPLIER_NAMES:
        state = rng.choice(list(STATE_CODES))
        suppliers.append({"name": name, "state": state, "gstin": fake_gstin(state, rng)})
    return suppliers


def make_invoice(i: int, supplier: dict, rng: random.Random) -> dict:
    taxable = round(rng.uniform(2_000, 250_000), 2)
    rate = rng.choice(RATES)
    tax = round(taxable * rate / 100, 2)
    intra = supplier["state"] == MY_STATE
    return {
        "supplier_name": supplier["name"],
        "supplier_gstin": supplier["gstin"],
        "invoice_no": f"INV/{2026}/{i:04d}",
        "invoice_date": (date(2026, 9, 1) + timedelta(days=rng.randint(0, 29))).isoformat(),
        "taxable_value": taxable,
        "gst_rate": rate,
        "cgst": round(tax / 2, 2) if intra else 0.0,
        "sgst": round(tax / 2, 2) if intra else 0.0,
        "igst": 0.0 if intra else tax,
    }


def render_pdf(inv: dict, path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "TAX INVOICE")
    c.setFont("Helvetica", 11)
    rows = [
        inv["supplier_name"],
        f"GSTIN: {inv['supplier_gstin']}",
        f"Invoice No: {inv['invoice_no']}",
        f"Date: {inv['invoice_date']}",
        "",
        f"Taxable Value: {inv['taxable_value']:.2f}",
        f"GST Rate: {inv['gst_rate']}%",
        f"CGST: {inv['cgst']:.2f}",
        f"SGST: {inv['sgst']:.2f}",
        f"IGST: {inv['igst']:.2f}",
        f"Total: {inv['taxable_value'] + inv['cgst'] + inv['sgst'] + inv['igst']:.2f}",
    ]
    for row in rows:
        y -= 22
        c.drawString(50, y, row)
    c.save()


def build_gstr2b(invoices: list[dict], rng: random.Random, suppliers: list[dict]):
    """Derive the portal-side data and label each invoice with its expected status."""
    portal, labels = [], {}
    for inv in invoices:
        roll = rng.random()
        rec = {k: inv[k] for k in (
            "supplier_gstin", "invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst")}
        if roll < 0.10:
            labels[inv["invoice_no"]] = "missing_in_2b"  # supplier never filed
            continue
        if roll < 0.20:
            rec["taxable_value"] = round(rec["taxable_value"] * rng.uniform(0.9, 0.99), 2)
            labels[inv["invoice_no"]] = "amount_mismatch"
        elif roll < 0.25:
            rec["invoice_no"] = inv["invoice_no"].replace("/", "-")  # formatting typo
            labels[inv["invoice_no"]] = "invoice_no_format_diff"
        else:
            labels[inv["invoice_no"]] = "matched"
        portal.append(rec)
    # a few invoices on the portal that are not in the books
    extras = []
    for j in range(3):
        s = rng.choice(suppliers)
        extra = make_invoice(900 + j, s, rng)
        portal.append({k: extra[k] for k in (
            "supplier_gstin", "invoice_no", "invoice_date", "taxable_value", "cgst", "sgst", "igst")})
        extras.append(extra["invoice_no"])
    return portal, labels, extras


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/synthetic")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    (out / "invoices").mkdir(parents=True, exist_ok=True)

    suppliers = make_suppliers(rng)
    invoices = [make_invoice(i + 1, rng.choice(suppliers), rng) for i in range(args.n)]
    for inv in invoices:
        render_pdf(inv, out / "invoices" / f"{inv['invoice_no'].replace('/', '_')}.pdf")

    portal, labels, extras = build_gstr2b(invoices, rng, suppliers)
    rng.shuffle(portal)

    (out / "gstr2b.json").write_text(json.dumps(portal, indent=2))
    (out / "ground_truth.json").write_text(json.dumps(
        {"invoices": invoices, "expected_status": labels, "extra_in_2b": extras}, indent=2))
    print(f"Wrote {len(invoices)} invoices, {len(portal)} portal rows to {out}/")


if __name__ == "__main__":
    main()
