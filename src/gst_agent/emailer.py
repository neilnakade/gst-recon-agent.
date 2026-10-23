"""Draft one follow-up email per supplier for every problem invoice.

Emails are written by Gemini from verified facts (your reconciliation result),
then checked by a simple guardrail: every invoice number (plus amounts for
mismatches and the portal's number for format differences) must appear in the email, otherwise we fall back to a plain template. Nothing is ever sent
automatically. These are DRAFTS for a human to review.

Run from the repo root (after the matcher):
    python -m src.gst_agent.emailer --company "Your Company Name"
    python -m src.gst_agent.emailer --no-ai        # template only, no API calls
"""
import argparse
import json
import os
import re
import time
from pathlib import Path

from .extractor import MODEL

PROBLEM_TEXT = {
    "missing_in_2b": "not showing in our GSTR-2B (it appears not to have been filed)",
    "amount_mismatch": "showing a different amount in our GSTR-2B than on your invoice",
    "invoice_no_format_diff": "showing in GSTR-2B with the invoice number written differently",
}


def money(x: float) -> str:
    """Rupee amount with Indian digit grouping: Rs. 1,69,452.71"""
    whole, dec = f"{float(x):.2f}".split(".")
    head, last3 = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "Rs. " + ",".join(groups + [last3]) + "." + dec


def build_facts(results: list[dict], books_by_no: dict) -> dict:
    """Group problem invoices by supplier, with the facts the email may use."""
    suppliers: dict[str, dict] = {}
    for r in results:
        if r["status"] == "matched":
            continue
        book = books_by_no[r["invoice_no"]]
        issue = {
            "problem": PROBLEM_TEXT[r["status"]],
            "invoice_no": r["invoice_no"],
            "invoice_date": book["invoice_date"],
            "taxable_value": money(book["taxable_value"]),
        }
        d = r["details"]
        if r["status"] == "amount_mismatch" and "taxable_value" in d:
            issue["our_taxable_value"] = money(d["taxable_value"]["books"])
            issue["portal_taxable_value"] = money(d["taxable_value"]["portal"])
        if r["status"] == "invoice_no_format_diff":
            issue["portal_invoice_no"] = d["portal"]
        s = suppliers.setdefault(r["supplier_name"], {"gstin": r["supplier_gstin"], "issues": []})
        s["issues"].append(issue)
    return suppliers


def template_email(supplier: str, facts: dict, company: str) -> dict:
    lines = []
    for i in facts["issues"]:
        line = (f"- Invoice {i['invoice_no']} dated {i['invoice_date']} "
                f"(taxable value {i['taxable_value']}): {i['problem']}.")
        if "portal_taxable_value" in i:
            line += f" Our books show {i['our_taxable_value']}; GSTR-2B shows {i['portal_taxable_value']}."
        if "portal_invoice_no" in i:
            line += f" GSTR-2B shows it as {i['portal_invoice_no']}."
        lines.append(line)
    body = (f"Dear {supplier},\n\nDuring our GST reconciliation for your GSTIN {facts['gstin']}, "
            f"we found the following:\n\n" + "\n".join(lines) +
            "\n\nKindly check and correct or file as needed, and let us know once done.\n\n"
            f"Thank you,\n{company}")
    return {"subject": f"GST reconciliation: action needed on {len(lines)} invoice(s)", "body": body}


def ai_email(client, supplier: str, facts: dict, company: str, retries: int = 3) -> dict:
    from google.genai import types
    from pydantic import BaseModel

    class EmailDraft(BaseModel):
        subject: str
        body: str

    prompt = (
        "Write a short, polite, professional email from the accounts team of "
        f"'{company}' to the supplier '{supplier}' about GST reconciliation problems.\n"
        "Rules:\n"
        "- Use ONLY the facts below. Do not invent amounts, dates, invoice numbers or deadlines.\n"
        "- Mention every invoice number exactly as given.\n"
        "- List the invoices as a bulleted list, one invoice per line, each line starting with '- '. "
        "Put the list on its own lines, separated from the paragraphs by blank lines.\n"
        "- Each bullet must say what is wrong with THAT invoice, in a few words "
        "(for example: 'not found in our GSTR-2B', 'different amount', 'shown as <portal_invoice_no>').\n"
        "- Do not mention a month, period or year anywhere, including the subject line.\n"
        "- For an amount mismatch, include BOTH amounts exactly as written in the facts "
        "(our_taxable_value and portal_taxable_value), including the 'Rs.' prefix.\n"
        "- Ask them to check and correct or file, and to confirm once done.\n"
        "- Do not threaten or blame. Keep the wording under 100 words plus the invoice list.\n"
        "- Sign off with the company name. Do not include an email address or phone number.\n\n"
        f"FACTS:\n{json.dumps(facts, indent=2)}"
    )
    for attempt in range(1, retries + 1):
        try:
            resp = client.models.generate_content(
                model=MODEL, contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=EmailDraft, temperature=0.3))
            return EmailDraft.model_validate_json(resp.text).model_dump()
        except Exception as e:
            if attempt == retries:
                raise
            wait = 30 * attempt
            print(f"  attempt {attempt} failed ({type(e).__name__}); waiting {wait}s...")
            time.sleep(wait)


def passes_guardrail(email: dict, facts: dict) -> bool:
    """Every invoice number must appear, and for amount mismatches both amounts too."""
    body = email["body"]
    for i in facts["issues"]:
        if i["invoice_no"] not in body:
            return False
        if "portal_invoice_no" in i and i["portal_invoice_no"] not in body:
            return False
        for key in ("our_taxable_value", "portal_taxable_value"):
            if key in i and i[key].replace("Rs. ", "") not in body:
                return False
    return True


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--recon", default="data/recon_result.json")
    ap.add_argument("--extracted", default="data/extracted")
    ap.add_argument("--out", default="output/emails")
    ap.add_argument("--company", default="[Your Company Name]")
    ap.add_argument("--no-ai", action="store_true", help="use the plain template, no API calls")
    ap.add_argument("--delay", type=float, default=8.0)
    args = ap.parse_args()

    recon = json.loads(Path(args.recon).read_text())
    books = [json.loads(p.read_text()) for p in sorted(Path(args.extracted).glob("*.json"))]
    suppliers = build_facts(recon["results"], {b["invoice_no"]: b for b in books})

    client = None
    if not args.no_ai:
        from dotenv import load_dotenv
        from google import genai
        load_dotenv()
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise SystemExit("GEMINI_API_KEY not found. Check your .env file, or use --no-ai.")
        client = genai.Client(api_key=key)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for supplier, facts in suppliers.items():
        if client:
            try:
                email = ai_email(client, supplier, facts, args.company)
                source = "AI"
                if not passes_guardrail(email, facts):
                    email, source = template_email(supplier, facts, args.company), "template (AI draft failed check)"
            except Exception as e:
                email, source = template_email(supplier, facts, args.company), f"template (AI error: {type(e).__name__})"
            time.sleep(args.delay)
        else:
            email, source = template_email(supplier, facts, args.company), "template"
        text = f"To: [supplier email]\nSubject: {email['subject']}\n\n{email['body']}\n"
        (out_dir / f"{safe_name(supplier)}.txt").write_text(text, encoding="utf-8")
        print(f"{supplier}: {len(facts['issues'])} invoice(s) [{source}]")


if __name__ == "__main__":
    main()
