"""Hard-mode synthetic invoices: the same task, but messier.

Differences from the easy set (synthetic.py):
  - 4 different invoice layouts (labels, structure and date formats all change)
  - Indian number format (1,23,456.78) in some layouts, plain numbers in others
  - several line items; the taxable value is the SUM of the items
  - distractors: the buyer's GSTIN, PO numbers, bank details, phone numbers
  - different invoice-number styles per supplier

Creates data/hard/invoices/*.pdf and data/hard/ground_truth.json
(same format as the easy set, plus a "layout" field for per-layout accuracy).

Run from the repo root:
    python -m src.gst_agent.synthetic_hard --n 40 --seed 7
"""
import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from .synthetic import MY_STATE, RATES, STATE_CODES, fake_gstin, make_suppliers

DESCS = ["Copier paper A4 (500 sheets)", "Steel fasteners assorted", "Packaging carton 5-ply",
         "Transport charges", "LED tube light 20W", "Office chairs", "Thermal roll 80mm",
         "Electrical cable 2.5 sq mm", "Printer cartridge", "Cleaning supplies"]
HSN = ["4802", "7318", "4819", "9965", "9405", "9401", "4811", "8544", "8443", "3402"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
LAYOUTS = ["classic", "modern", "compact", "noisy"]


def indian(x: float) -> str:
    whole, dec = f"{x:.2f}".split(".")
    head, last3 = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [last3]) + "." + dec


def plain(x: float) -> str:
    return f"{x:.2f}"


def fmt_date(d: date, layout: str) -> str:
    return {"classic": d.strftime("%d/%m/%Y"),
            "modern": f"{d.day:02d}-{MONTHS[d.month - 1]}-{d.year}",
            "compact": d.isoformat(),
            "noisy": d.strftime("%d.%m.%Y")}[layout]


def make_invoice_no(supplier_idx: int, counter: int, rng: random.Random) -> str:
    styles = [f"SH/26-27/{counter:04d}", f"OE-{1000 + counter}", f"2026/INV/{counter:03d}",
              f"B-{counter:04d}", f"KS{counter:05d}", f"PS/{counter}/2026", f"DL-{counter:04d}-26",
              f"GE{rng.randint(10, 99)}{counter:03d}"]
    return styles[supplier_idx % len(styles)]


def make_invoice(i: int, idx: int, supplier: dict, rng: random.Random) -> dict:
    total = round(rng.uniform(2_000, 250_000), 2)
    n_items = rng.randint(1, 3)
    cuts = sorted(rng.uniform(0.2, 0.8) for _ in range(n_items - 1))
    amounts, prev = [], 0.0
    for c in cuts + [1.0]:
        amounts.append(round(total * (c - prev), 2))
        prev = c
    amounts[-1] = round(total - sum(amounts[:-1]), 2)
    items = [{"desc": rng.choice(DESCS), "hsn": rng.choice(HSN),
              "qty": rng.randint(1, 50), "amount": a} for a in amounts]
    taxable = round(sum(amounts), 2)
    rate = rng.choice(RATES)
    tax = round(taxable * rate / 100, 2)
    intra = supplier["state"] == MY_STATE
    return {
        "supplier_name": supplier["name"], "supplier_gstin": supplier["gstin"],
        "invoice_no": make_invoice_no(idx, i, rng),
        "invoice_date": (date(2026, 9, 1) + timedelta(days=rng.randint(0, 29))).isoformat(),
        "taxable_value": taxable, "gst_rate": rate,
        "cgst": round(tax / 2, 2) if intra else 0.0,
        "sgst": round(tax / 2, 2) if intra else 0.0,
        "igst": 0.0 if intra else tax,
        "_items": items,
    }


def tax_lines(inv: dict, money, style: str) -> list[tuple[str, float]]:
    r = inv["gst_rate"]
    if inv["igst"]:
        names = {"short": f"IGST @ {r}%", "long": f"Integrated GST (IGST) {r}%"}
        return [(names[style], inv["igst"])]
    names = {"short": (f"CGST @ {r / 2:g}%", f"SGST @ {r / 2:g}%"),
             "long": (f"Central GST (CGST) {r / 2:g}%", f"State GST (SGST) {r / 2:g}%")}[style]
    return [(names[0], inv["cgst"]), (names[1], inv["sgst"])]


def grand_total(inv: dict) -> float:
    return round(inv["taxable_value"] + inv["cgst"] + inv["sgst"] + inv["igst"], 2)


def draw_classic(c, inv, buyer, rng):
    d = fmt_date(date.fromisoformat(inv["invoice_date"]), "classic")
    c.setFont("Helvetica-Bold", 16); c.drawCentredString(298, 800, "TAX INVOICE")
    c.setFont("Helvetica-Bold", 13); c.drawString(50, 770, inv["supplier_name"])
    c.setFont("Helvetica", 10)
    c.drawString(50, 754, f"GSTIN: {inv['supplier_gstin']}")
    c.drawString(50, 730, f"Bill No: {inv['invoice_no']}"); c.drawString(300, 730, f"Bill Date: {d}")
    c.drawString(50, 706, f"Bill To: {buyer['name']}"); c.drawString(50, 692, f"GSTIN: {buyer['gstin']}")
    y = 660
    for x, h in zip((50, 90, 330, 390, 470), ("Sl", "Description", "HSN", "Qty", "Amount")):
        c.drawString(x, y, h)
    for n, it in enumerate(inv["_items"], 1):
        y -= 18
        for x, v in zip((50, 90, 330, 390, 470), (n, it["desc"], it["hsn"], it["qty"], indian(it["amount"]))):
            c.drawString(x, y, str(v))
    y -= 34
    c.drawString(330, y, "Taxable Value"); c.drawString(470, y, indian(inv["taxable_value"]))
    for name, amt in tax_lines(inv, indian, "short"):
        y -= 16; c.drawString(330, y, name); c.drawString(470, y, indian(amt))
    y -= 22; c.setFont("Helvetica-Bold", 11)
    c.drawString(330, y, "Grand Total"); c.drawString(470, y, indian(grand_total(inv)))


def draw_modern(c, inv, buyer, rng):
    d = fmt_date(date.fromisoformat(inv["invoice_date"]), "modern")
    c.setFont("Helvetica-Bold", 22); c.drawString(50, 800, "INVOICE")
    c.setFont("Helvetica", 10)
    c.drawString(380, 800, f"Invoice #: {inv['invoice_no']}"); c.drawString(380, 785, f"Date: {d}")
    c.drawString(380, 770, f"Place of Supply: {STATE_CODES.get(inv['supplier_gstin'][:2], 'India')}")
    c.setFont("Helvetica-Bold", 10); c.drawString(50, 740, "From:")
    c.setFont("Helvetica", 10)
    c.drawString(50, 726, inv["supplier_name"]); c.drawString(50, 712, f"GSTIN/UIN: {inv['supplier_gstin']}")
    c.setFont("Helvetica-Bold", 10); c.drawString(300, 740, "Billed To:")
    c.setFont("Helvetica", 10)
    c.drawString(300, 726, buyer["name"]); c.drawString(300, 712, f"GSTIN/UIN: {buyer['gstin']}")
    y = 670
    for x, h in zip((50, 280, 340, 400, 480), ("Item", "HSN/SAC", "Qty", "Rate", "Amount")):
        c.drawString(x, y, h)
    for it in inv["_items"]:
        y -= 18
        rate = it["amount"] / it["qty"]
        for x, v in zip((50, 280, 340, 400, 480), (it["desc"], it["hsn"], it["qty"], plain(rate), plain(it["amount"]))):
            c.drawString(x, y, str(v))
    y -= 34
    c.drawRightString(540, y, f"Sub Total: {plain(inv['taxable_value'])}")
    for name, amt in tax_lines(inv, plain, "long"):
        y -= 16; c.drawRightString(540, y, f"{name}: {plain(amt)}")
    y -= 22; c.setFont("Helvetica-Bold", 11)
    c.drawRightString(540, y, f"Total Payable: {plain(grand_total(inv))}")


def draw_compact(c, inv, buyer, rng):
    d = fmt_date(date.fromisoformat(inv["invoice_date"]), "compact")
    c.setFont("Courier", 10)
    y = 800
    lines = [inv["supplier_name"].upper(), f"GSTIN/UIN : {inv['supplier_gstin']}",
             f"INV NO. {inv['invoice_no']}   DT. {d}", "-" * 60]
    for it in inv["_items"]:
        lines.append(f"{it['desc'][:28]:<28} {it['qty']:>3} {plain(it['amount']):>14}")
    lines += ["-" * 60, f"TAXABLE {plain(inv['taxable_value'])}"]
    parts = [f"{n.replace(' @ ', '(')}%) {plain(a)}".replace("%%", "%")
             for n, a in tax_lines(inv, plain, "short")]
    lines += ["  |  ".join(parts), f"TOTAL {plain(grand_total(inv))}"]
    for ln in lines:
        c.drawString(50, y, ln); y -= 14


def draw_noisy(c, inv, buyer, rng):
    d = fmt_date(date.fromisoformat(inv["invoice_date"]), "noisy")
    c.setFont("Helvetica-Bold", 15); c.drawString(50, 805, inv["supplier_name"])
    c.setFont("Helvetica", 8)
    c.drawString(50, 793, "Plot 14, MIDC Industrial Area, Pune 411019  |  Ph: 020-2745" + f"{rng.randint(1000, 9999)}"
                 f"  |  accounts@{inv['supplier_name'].split()[0].lower()}.example")
    c.setFont("Helvetica", 10)
    c.drawString(50, 770, f"Seller GSTIN: {inv['supplier_gstin']}")
    c.drawString(330, 770, f"Document No.: {inv['invoice_no']}"); c.drawString(330, 756, f"Document Date: {d}")
    c.drawString(330, 742, f"Your PO Ref: PO-{rng.randint(10000, 99999)}"); c.drawString(330, 728, f"Challan No: {rng.randint(1000, 9999)}")
    c.drawString(50, 742, f"Buyer: {buyer['name']}"); c.drawString(50, 728, f"Buyer GSTIN: {buyer['gstin']}")
    y = 690
    for x, h in zip((50, 300, 360, 470), ("Particulars", "HSN", "Qty", "Value")):
        c.drawString(x, y, h)
    for it in inv["_items"]:
        y -= 16
        for x, v in zip((50, 300, 360, 470), (it["desc"], it["hsn"], it["qty"], indian(it["amount"]))):
            c.drawString(x, y, str(v))
    y -= 30
    c.drawString(300, y, "Total Taxable Value"); c.drawString(470, y, indian(inv["taxable_value"]))
    for name, amt in tax_lines(inv, indian, "long"):
        y -= 16; c.drawString(300, y, name); c.drawString(470, y, indian(amt))
    y -= 20; c.setFont("Helvetica-Bold", 10)
    c.drawString(300, y, "Invoice Value"); c.drawString(470, y, indian(grand_total(inv)))
    c.setFont("Helvetica", 8)
    c.drawString(50, 160, f"Bank: State Bank (example)  A/c No: {rng.randint(10**11, 10**12 - 1)}  IFSC: SBIN0{rng.randint(100000, 999999)}")
    c.drawString(50, 148, "Terms: Payment within 30 days. Subject to Pune jurisdiction. E&OE.")


DRAWERS = {"classic": draw_classic, "modern": draw_modern, "compact": draw_compact, "noisy": draw_noisy}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="data/hard")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out = Path(args.out)
    (out / "invoices").mkdir(parents=True, exist_ok=True)
    suppliers = make_suppliers(rng)
    buyer = {"name": "ABC Traders Pvt Ltd", "gstin": fake_gstin(MY_STATE, rng)}

    records = []
    for i in range(1, args.n + 1):
        idx = rng.randrange(len(suppliers))
        inv = make_invoice(i, idx, suppliers[idx], rng)
        layout = LAYOUTS[(i - 1) % len(LAYOUTS)]
        c = canvas.Canvas(str(out / "invoices" / f"H_{i:03d}.pdf"), pagesize=A4)
        DRAWERS[layout](c, inv, buyer, rng)
        c.save()
        rec = {k: v for k, v in inv.items() if k != "_items"}
        rec["layout"] = layout
        records.append(rec)

    (out / "ground_truth.json").write_text(json.dumps({"invoices": records}, indent=2))
    print(f"Wrote {len(records)} hard-mode invoices to {out}/ ({', '.join(LAYOUTS)})")


if __name__ == "__main__":
    main()
