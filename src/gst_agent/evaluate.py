"""Measure extraction accuracy against the ground-truth answer key.

For every invoice that has been extracted, compare each field with the correct
value from data/synthetic/ground_truth.json and print:
  - accuracy per field
  - how many invoices were extracted perfectly (every field right)
  - the exact mistakes, so you can see what to fix

Run from the repo root:
    python -m src.gst_agent.evaluate
"""
import argparse
import json
from pathlib import Path

TEXT_FIELDS = ["supplier_name", "supplier_gstin", "invoice_no", "invoice_date"]
NUMBER_FIELDS = ["taxable_value", "cgst", "sgst", "igst"]
FIELDS = TEXT_FIELDS + NUMBER_FIELDS


def same(field: str, expected, got) -> bool:
    if field in NUMBER_FIELDS:
        return abs(float(expected) - float(got)) < 0.01
    return str(expected).strip() == str(got).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--truth", default="data/synthetic/ground_truth.json")
    ap.add_argument("--extracted", default="data/extracted")
    args = ap.parse_args()

    truth = json.loads(Path(args.truth).read_text())["invoices"]
    truth_by_no = {inv["invoice_no"]: inv for inv in truth}

    files = sorted(Path(args.extracted).glob("*.json"))
    if not files:
        raise SystemExit("No extracted files found. Run the extractor first.")

    correct = {f: 0 for f in FIELDS}
    perfect = 0
    mistakes = []

    for path in files:
        got = json.loads(path.read_text())
        expected = truth_by_no.get(got["invoice_no"])
        if expected is None:
            mistakes.append((path.name, "invoice_no", "(not in answer key)", got["invoice_no"]))
            continue
        all_ok = True
        for f in FIELDS:
            if same(f, expected[f], got[f]):
                correct[f] += 1
            else:
                all_ok = False
                mistakes.append((path.name, f, expected[f], got[f]))
        perfect += all_ok

    n = len(files)
    print(f"\nInvoices evaluated: {n}\n")
    print(f"{'Field':<16}{'Correct':>9}{'Accuracy':>11}")
    print("-" * 36)
    for f in FIELDS:
        print(f"{f:<16}{correct[f]:>6}/{n:<3}{correct[f] / n:>9.1%}")
    print("-" * 36)
    print(f"Perfect invoices (all fields right): {perfect}/{n} = {perfect / n:.1%}\n")

    if mistakes:
        print("Mistakes:")
        for name, field, exp, got in mistakes:
            print(f"  {name}  {field}: expected {exp!r}, got {got!r}")
    else:
        print("No mistakes found.")


if __name__ == "__main__":
    main()
