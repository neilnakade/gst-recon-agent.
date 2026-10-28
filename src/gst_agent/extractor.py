"""Extract structured fields from invoice PDFs using Gemini.

Two ways in, chosen automatically per file:
  digital PDF (has a text layer):  PDF -> text (pdfplumber) -> Gemini
  scanned PDF / photo (no text):   PDF page -> image -> Gemini reads the picture

Either way the answer is validated against a strict schema (Pydantic).
Results are cached in data/extracted/ so each invoice is only sent to the API once
(important on the free tier, which has low rate limits).

Run from the repo root (start small!):
    python -m src.gst_agent.extractor --limit 3
Scanned set:
    python -m src.gst_agent.extractor --invoices data/scanned/invoices --out data/scanned/extracted --limit 40
"""
import argparse
import io
import json
import os
import time
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

MODEL = "gemini-3.5-flash-lite"  # change here if Google renames models again

RULES = """You extract data from Indian GST purchase invoices.
Return only the fields in the schema. Rules:
- Dates must be YYYY-MM-DD. Dates on Indian invoices are day-first (dd/mm/yyyy).
- Amounts are plain numbers (no currency symbols or commas).
- If a tax field is not present on the invoice, use 0.
- The supplier is the party that issued the invoice. Do not use the buyer's details.
- Copy the GSTIN and invoice number exactly as printed. Do not fix or guess them.
"""

PROMPT = RULES + "\nINVOICE TEXT:\n"
PROMPT_IMAGE = RULES + "\nThe invoice is the attached image. It may be tilted, blurry or noisy; read it carefully."


class InvoiceFields(BaseModel):
    supplier_name: str
    supplier_gstin: str = Field(description="15-character GSTIN of the supplier")
    invoice_no: str
    invoice_date: str = Field(description="YYYY-MM-DD")
    taxable_value: float
    cgst: float = 0.0
    sgst: float = 0.0
    igst: float = 0.0


def read_pdf_text(path) -> str:
    with pdfplumber.open(path) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages).strip()


def page_to_png(path, dpi: int = 150) -> bytes:
    """First page of a PDF as PNG bytes (for scanned invoices)."""
    with pdfplumber.open(path) as pdf:
        image = pdf.pages[0].to_image(resolution=dpi).original
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _generate(client: genai.Client, contents, retries: int = 3) -> InvoiceFields:
    for attempt in range(1, retries + 1):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=InvoiceFields,
                    temperature=0,
                ),
            )
            return InvoiceFields.model_validate_json(response.text)
        except Exception as e:  # rate limit or a bad response: wait and retry
            if attempt == retries:
                raise
            wait = 30 * attempt
            print(f"  attempt {attempt} failed ({type(e).__name__}); waiting {wait}s...")
            time.sleep(wait)


def extract_invoice(client: genai.Client, text: str, retries: int = 3) -> InvoiceFields:
    return _generate(client, PROMPT + text, retries)


def extract_invoice_image(client: genai.Client, png_bytes: bytes, retries: int = 3) -> InvoiceFields:
    image = types.Part.from_bytes(data=png_bytes, mime_type="image/png")
    return _generate(client, [image, PROMPT_IMAGE], retries)


def extract_folder(invoice_dir: Path, out_dir: Path, limit: int, delay: float) -> None:
    load_dotenv()
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY not found. Check your .env file.")
    client = genai.Client(api_key=key)

    out_dir.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(invoice_dir.glob("*.pdf"))[:limit]
    for pdf_path in pdfs:
        out_path = out_dir / f"{pdf_path.stem}.json"
        if out_path.exists():
            print(f"skip (cached): {pdf_path.name}")
            continue
        text = read_pdf_text(pdf_path)
        if text:
            fields, how = extract_invoice(client, text), "text"
        else:
            fields, how = extract_invoice_image(client, page_to_png(pdf_path)), "image (scanned)"
        out_path.write_text(fields.model_dump_json(indent=2))
        print(f"extracted [{how}]: {pdf_path.name} -> {fields.invoice_no}")
        time.sleep(delay)  # stay under the free-tier requests-per-minute limit


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--invoices", default="data/synthetic/invoices")
    ap.add_argument("--out", default="data/extracted")
    ap.add_argument("--limit", type=int, default=3, help="how many PDFs to process")
    ap.add_argument("--delay", type=float, default=8.0, help="seconds between API calls")
    args = ap.parse_args()
    extract_folder(Path(args.invoices), Path(args.out), args.limit, args.delay)
