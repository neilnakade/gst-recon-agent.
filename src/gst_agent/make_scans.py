"""Turn the hard-mode invoices into "scanned" PDFs: pictures of pages with no text inside.

Three quality levels, roughly like real life:
  light   slightly tilted flatbed scan
  medium  tilted, a bit blurry and noisy, lower contrast
  heavy   phone-photo style: more tilt, blur, noise, an uneven shadow, heavy JPEG (still readable by a person)

Reads data/hard/ground_truth.json and data/hard/invoices/, writes
data/scanned/invoices/S_###.pdf and data/scanned/ground_truth.json
(same answers as the hard set, plus a "scan_quality" field).

Run from the repo root (after synthetic_hard.py):
    python -m src.gst_agent.make_scans
"""
import argparse
import io
import json
import random
from pathlib import Path

import pdfplumber
from PIL import Image, ImageChops, ImageEnhance, ImageFilter, ImageOps

LEVELS = ["light", "medium", "heavy"]
SETTINGS = {  # tilt degrees, blur radius, noise strength, contrast, dpi, jpeg quality, shadow
    "light":  dict(tilt=1.0, blur=0.4, noise=8,  contrast=1.0,  dpi=150, jpeg=85, shadow=0.0),
    "medium": dict(tilt=2.5, blur=0.7, noise=12, contrast=0.88, dpi=140, jpeg=65, shadow=0.0),
    "heavy":  dict(tilt=3.5, blur=0.9, noise=16, contrast=0.8,  dpi=130, jpeg=45, shadow=0.25),
}


def render_page(pdf_path: Path, dpi: int) -> Image.Image:
    with pdfplumber.open(pdf_path) as pdf:
        return pdf.pages[0].to_image(resolution=dpi).original.convert("L")


def degrade(img: Image.Image, level: str, rng: random.Random) -> Image.Image:
    s = SETTINGS[level]
    img = img.rotate(rng.uniform(-s["tilt"], s["tilt"]), resample=Image.BICUBIC,
                     expand=True, fillcolor=255)
    img = img.filter(ImageFilter.GaussianBlur(s["blur"]))
    img = ImageEnhance.Contrast(img).enhance(s["contrast"])
    if s["shadow"]:
        base = Image.linear_gradient("L")  # 256x256, dark at the top, bright at the bottom
        direction = rng.choice([0, 90, 180, 270])  # which side of the page the shadow falls on
        if direction in (90, 270):  # sideways shadow: turn the square gradient BEFORE resizing
            base = base.transpose(Image.Transpose.ROTATE_90)
        grad = base.resize(img.size).point(lambda v: int(255 - v * s["shadow"]))
        if direction in (180, 270):
            grad = ImageOps.flip(grad) if direction == 180 else ImageOps.mirror(grad)
        img = ImageChops.multiply(img, grad)
    noise = Image.effect_noise(img.size, s["noise"])  # centred on 128
    img = ImageChops.add(img, noise, scale=1.0, offset=-128)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=s["jpeg"])  # compression artefacts, like a real scan or photo
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="data/hard")
    ap.add_argument("--out", default="data/scanned")
    ap.add_argument("--seed", type=int, default=3)
    args = ap.parse_args()

    src, out = Path(args.src), Path(args.out)
    truth = json.loads((src / "ground_truth.json").read_text())["invoices"]
    (out / "invoices").mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    records = []
    for i, rec in enumerate(truth, 1):
        level = LEVELS[i % len(LEVELS)]
        page = render_page(src / "invoices" / f"H_{i:03d}.pdf", SETTINGS[level]["dpi"])
        scan = degrade(page, level, rng)
        scan.save(out / "invoices" / f"S_{i:03d}.pdf", "PDF", resolution=SETTINGS[level]["dpi"])
        records.append({**rec, "scan_quality": level})

    (out / "ground_truth.json").write_text(json.dumps({"invoices": records}, indent=2))
    counts = {lv: sum(r["scan_quality"] == lv for r in records) for lv in LEVELS}
    print(f"Wrote {len(records)} scanned invoices to {out}/ {counts}")


if __name__ == "__main__":
    main()
