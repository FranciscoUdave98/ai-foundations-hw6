"""Defense layers for the vendor desk agent (Problem 6).

Code layer: visible_text() turns a vendor document into only the text a person would
actually see, before any model reads it. It drops hidden HTML (comments, display:none,
0px or tiny fonts, white or transparent text, off-screen boxes), hidden PDF text (white,
sub-4pt, transparent, off-page, or covered spans), hidden email parts, and zero-width
characters. Everything it drops is returned, so the audit trail shows what was removed.

Design layer: decide() applies the four purchasing checks from the handbook in Python,
and ground() throws out any extracted value that is not actually in the text the model
was given. The model only extracts facts; it never makes the call and never sees a
tool that can send or pay.
"""

from __future__ import annotations

import email
import re
import unicodedata
from collections import Counter
from email import policy as email_policy
from pathlib import Path

import pymupdf
from bs4 import BeautifulSoup, Comment

from models import ExtractedFacts, VendorDecision

HW_DIR = Path(__file__).resolve().parent
HANDBOOK_PATH = HW_DIR / "data" / "shop_handbook.md"

# ---------------------------------------------------------------- code layer

# Zero-width and other invisible characters used to split words past keyword filters.
INVISIBLE_CHARS = re.compile("[​-‏⁠-⁤﻿­͏᠎]")

MIN_FONT_PX = 4.0       # HTML text smaller than this is treated as hidden
MIN_FONT_PT = 4.0       # PDF text smaller than this is treated as hidden (body text is 9-20 pt)
MIN_CONTRAST = 1.5      # WCAG contrast; 1 = same color as background, 21 = black on white


def _clean(text: str) -> str:
    text = INVISIBLE_CHARS.sub("", unicodedata.normalize("NFKC", text))
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _snippet(text: str) -> str:
    text = " ".join(INVISIBLE_CHARS.sub("", text).split())
    return text if len(text) <= 160 else text[:160] + "..."


def _rgb(value: str) -> tuple[int, int, int] | None:
    v = value.strip().lower()
    named = {"white": (255, 255, 255), "black": (0, 0, 0)}
    if v in named:
        return named[v]
    if m := re.fullmatch(r"#([0-9a-f]{3})", v):
        return tuple(int(c * 2, 16) for c in m.group(1))
    if m := re.fullmatch(r"#([0-9a-f]{6})", v):
        return tuple(int(m.group(1)[i : i + 2], 16) for i in (0, 2, 4))
    if m := re.fullmatch(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)", v):
        if m.group(4) is not None and float(m.group(4)) == 0:
            return None  # fully transparent
        return tuple(int(m.group(i)) for i in (1, 2, 3))
    return None


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(c: int) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _declarations(style: str) -> dict[str, str]:
    out = {}
    for part in style.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip().lower()
    return out


def _font_px(value: str) -> float | None:
    if m := re.match(r"([\d.]+)\s*(px|pt|em|rem|%)?", value):
        n, unit = float(m.group(1)), m.group(2) or "px"
        return {"px": n, "pt": n * 4 / 3, "em": n * 16, "rem": n * 16, "%": n * 0.16}[unit]
    return None


def _hidden_style(decl: dict[str, str], background: tuple[int, int, int]) -> str | None:
    """Why this CSS hides its text, or None if it is visible."""
    if decl.get("display") == "none":
        return "display:none"
    if decl.get("visibility") in ("hidden", "collapse"):
        return "visibility:hidden"
    if (op := decl.get("opacity")) and re.fullmatch(r"0*\.?0*", op):
        return "opacity:0"
    if (fs := decl.get("font-size")) and (px := _font_px(fs)) is not None and px < MIN_FONT_PX:
        return f"font-size:{fs}"
    if "color" in decl:
        fg = _rgb(decl["color"])
        bg = _rgb(decl.get("background-color", decl.get("background", ""))) or background
        if fg is None or contrast(fg, bg) < MIN_CONTRAST:
            return f"color:{decl['color']} on background"
    for side in ("left", "top", "text-indent", "margin-left"):
        if (v := decl.get(side)) and (m := re.match(r"-\s*(\d+)", v)) and int(m.group(1)) >= 500:
            return f"off-screen {side}:{v}"
    if decl.get("overflow") == "hidden" and any(re.fullmatch(r"0+(px)?", decl.get(k, "x")) for k in ("height", "width", "max-height")):
        return "zero-size box"
    if "clip" in decl and "rect(0" in decl["clip"].replace(" ", ""):
        return "clip:rect(0)"
    return None


def visible_html(html: str) -> tuple[str, list[dict]]:
    """Text a browser would show, plus a list of everything hidden that was removed."""
    soup = BeautifulSoup(html, "html.parser")
    removed: list[dict] = []

    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        if c.strip():
            removed.append({"why": "HTML comment", "text": _snippet(c)})
        c.extract()
    for tag in soup(["script", "style", "noscript", "template", "head", "meta", "iframe", "object"]):
        if tag.name in ("script", "noscript", "template", "iframe", "object") and tag.get_text(strip=True):
            removed.append({"why": f"<{tag.name}>", "text": _snippet(tag.get_text(" "))})

    # Page background from the body's inline style or a body/html rule in <style>.
    css = " ".join(s.get_text() for s in soup.find_all("style"))
    background = (255, 255, 255)
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        if any(s.strip() in ("body", "html") for s in sel.split(",")):
            d = _declarations(body)
            background = _rgb(d.get("background-color", d.get("background", ""))) or background

    # Hidden-by-class/id rules in <style>, e.g. .note { display:none }.
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        if why := _hidden_style(_declarations(body), background):
            for s in sel.split(","):
                s = s.strip()
                if s and s not in ("body", "html"):
                    try:
                        for el in soup.select(s):
                            if el.get_text(strip=True):
                                removed.append({"why": f"{why} (CSS rule {s})", "text": _snippet(el.get_text(" "))})
                            el.decompose()
                    except Exception:
                        pass  # selector BeautifulSoup can't parse; inline checks below still run

    for tag in soup(["script", "style", "noscript", "template", "head", "meta", "iframe", "object"]):
        tag.decompose()

    for el in soup.find_all(True):
        if el.decomposed:
            continue
        why = None
        if el.has_attr("hidden"):
            why = "hidden attribute"
        elif str(el.get("aria-hidden", "")).lower() == "true":
            why = "aria-hidden"
        elif el.has_attr("style"):
            why = _hidden_style(_declarations(el["style"]), background)
        if why:
            if el.get_text(strip=True):
                removed.append({"why": why, "text": _snippet(el.get_text(" "))})
            el.decompose()

    text = soup.get_text("\n")
    if INVISIBLE_CHARS.search(text):
        removed.append({"why": "zero-width characters", "text": f"{len(INVISIBLE_CHARS.findall(text))} removed"})
    return _clean(text), removed


def _ink_and_background(page: pymupdf.Page, bbox: pymupdf.Rect) -> tuple[tuple[int, int, int], float]:
    """Most common rendered color in the span's box, and the share of pixels that differ from it."""
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=bbox, colorspace=pymupdf.csRGB, alpha=False)
    data = pix.samples
    pixels = Counter(data[i : i + 3] for i in range(0, len(data), 3))
    if not pixels:
        return (255, 255, 255), 0.0
    bg, count = pixels.most_common(1)[0]
    return tuple(bg), 1 - count / sum(pixels.values())


def visible_pdf(path: Path) -> tuple[str, list[dict]]:
    """PDF text minus spans a reader can't see: white or low-contrast, tiny, transparent, off-page, or covered."""
    removed: list[dict] = []
    pages: list[str] = []
    with pymupdf.open(path) as pdf:
        for page in pdf:
            lines_out: list[str] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    kept = ""
                    for span in line["spans"]:
                        text = span["text"]
                        if not text.strip():
                            kept += text
                            continue
                        bbox = pymupdf.Rect(span["bbox"])
                        c = span["color"]
                        fg = (c >> 16 & 255, c >> 8 & 255, c & 255)
                        why = None
                        if span.get("alpha", 255) == 0:
                            why = "transparent text"
                        elif span["size"] < MIN_FONT_PT:
                            why = f"{span['size']:.1f} pt text"
                        elif not bbox.intersects(page.rect):
                            why = "off the page"
                        else:
                            bg, ink = _ink_and_background(page, bbox & page.rect)
                            if contrast(fg, bg) < MIN_CONTRAST:
                                why = f"text color #{c:06x} on background #{bytes(bg).hex()}"
                            elif ink < 0.005:
                                why = "covered by a shape (no ink drawn)"
                        if why:
                            removed.append({"why": why, "text": _snippet(text)})
                        else:
                            kept += text
                    if kept.strip():
                        lines_out.append(kept)
            pages.append("\n".join(lines_out))
    return _clean("\n\n".join(pages)), removed


def visible_email(path: Path) -> tuple[str, list[dict]]:
    """Headers plus the body a mail app shows (the HTML part, cleaned, when there is one)."""
    msg = email.message_from_bytes(path.read_bytes(), policy=email_policy.default)
    headers = "\n".join(f"{h}: {msg[h]}" for h in ("From", "To", "Subject", "Date") if msg[h])
    removed: list[dict] = []
    html_part = msg.get_body(preferencelist=("html",))
    plain_part = msg.get_body(preferencelist=("plain",))
    if html_part is not None:
        body, removed = visible_html(html_part.get_content())
        if plain_part is not None:
            removed.append({"why": "plain-text alternative not shown by mail apps (HTML part used)", "text": _snippet(plain_part.get_content())})
    elif plain_part is not None:
        raw = plain_part.get_content()
        body = _clean(raw)
        if INVISIBLE_CHARS.search(raw):
            removed.append({"why": "zero-width characters", "text": f"{len(INVISIBLE_CHARS.findall(raw))} removed"})
    else:
        body = ""
    for part in msg.walk():
        if part.get_content_maintype() != "multipart" and part not in (html_part, plain_part):
            removed.append({"why": f"extra MIME part ({part.get_content_type()})", "text": _snippet(str(part.get_payload())[:200])})
    return f"{_clean(headers)}\n\n{body}", removed


def visible_text(path: Path) -> tuple[str, list[dict]]:
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return visible_html(path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".pdf":
        return visible_pdf(path)
    if suffix == ".eml":
        return visible_email(path)
    raise ValueError(f"Unsupported document type: {suffix}")


# ---------------------------------------------------------------- design layer

MAX_LEAD_DAYS = 21
MAX_MIN_ORDER = 600


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", name.lower()).split())


def _table_rows(section: str) -> list[list[str]]:
    rows = []
    for line in section.splitlines():
        if line.startswith("|") and not re.match(r"\|\s*-", line):
            rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows[1:]  # drop the header row


def load_rules(handbook: str | None = None) -> dict:
    """Approved vendors, removed/pending vendors, and max unit cost per SKU, read from the handbook itself."""
    text = handbook or HANDBOOK_PATH.read_text(encoding="utf-8")
    sections = re.split(r"\n## ", text)
    get = lambda n: next(s for s in sections if s.startswith(f"{n}. "))
    approved = {_norm(r[1]): r[1] for r in _table_rows(get(5))}
    removed = {_norm(r[0]): r[0] for r in _table_rows(get(6))}
    max_cost = {r[0]: float(r[4].lstrip("$")) for r in _table_rows(get(9))}
    return {"approved": approved, "removed": removed, "max_cost": max_cost}


RULES = load_rules()


def ground(facts: ExtractedFacts, text: str) -> tuple[ExtractedFacts, list[str]]:
    """Blank out any value that does not literally appear in the text the model was given (never guess)."""
    flat = " ".join(text.split())
    digits = re.sub(r"[,\s]", "", flat)
    dropped: list[str] = []
    data = facts.model_dump()
    if data["vendor"] and _norm(data["vendor"]) not in _norm(flat):
        dropped.append("vendor")
    if data["sku"] and data["sku"].upper() not in flat.upper():
        dropped.append("sku")
    if data["unit_price"] is not None and not re.search(rf"\$?{data['unit_price']:.2f}(?!\d)", digits):
        dropped.append("unit_price")
    for f in ("lead_time_days", "min_order"):
        if data[f] is not None and not re.search(rf"(?<![\d.]){data[f]}(?![\d.])", flat.replace(",", "")):
            dropped.append(f)
    for f in dropped:
        data[f] = None
    return ExtractedFacts(**data), dropped


LABELED = {
    "sku": r"\b(CC-[A-Z]{2}-\d{3})\b",
    "unit_price": r"unit\s*price\s*:?\s*\$\s*([\d,]+(?:\.\d{1,2})?)",
    "min_order": r"minimum\s*(?:order)?\s*(?:quantity)?\s*:?\s*([\d,]+)\s*(?:units?|pcs|pieces)",
    "lead_time_days": r"lead\s*time\s*:?\s*(\d+)\s*(?:business\s+|calendar\s+|working\s+)?days?",
}


def labeled_facts(text: str) -> dict:
    """Values from the document's own labeled quote lines ("Unit price: $9.83"), only when exactly one is given."""
    flat = " ".join(text.split())
    out = {}
    for field, pattern in LABELED.items():
        found = {m.replace(",", "") for m in re.findall(pattern, flat, flags=re.IGNORECASE)}
        if len(found) == 1:
            v = found.pop()
            out[field] = v.upper() if field == "sku" else float(v) if field == "unit_price" else int(v)
    return out


def reconcile(facts: ExtractedFacts, text: str) -> tuple[ExtractedFacts, list[str]]:
    """Cross-check the model against the labeled quote lines: fill blanks and fix disagreements, and say so."""
    data, notes = facts.model_dump(), []
    for field, value in labeled_facts(text).items():
        if data[field] is None:
            data[field] = value
            notes.append(f"{field} filled from the quote line ({value})")
        elif data[field] != value:
            notes.append(f"{field}: model said {data[field]}, quote line says {value}; used the quote line")
            data[field] = value
    return ExtractedFacts(**data), notes


def decide(facts: ExtractedFacts) -> VendorDecision:
    """The four purchasing checks from handbook section 4, in order. The first failure decides."""
    f = facts
    vendor = _norm(f.vendor or "")
    fail = None
    if not f.vendor:
        fail = "Approved-vendor check fails: the document does not name the vendor."
    elif vendor in RULES["removed"]:
        fail = f"Approved-vendor check fails: {f.vendor} is on the removed or pending vendor list."
    elif vendor not in RULES["approved"]:
        fail = f"Approved-vendor check fails: {f.vendor} is not on the approved vendor list."
    elif f.unit_price is None:
        fail = "Price check fails: the document does not state a unit price."
    elif not f.sku or f.sku.upper() not in RULES["max_cost"]:
        fail = f"Price check fails: SKU {f.sku or '(none given)'} is not in the Campus Customs catalogue."
    elif f.unit_price > RULES["max_cost"][f.sku.upper()]:
        fail = f"Price check fails: the quoted ${f.unit_price:.2f} is above our max unit cost for {f.sku}."
    elif f.lead_time_days is None:
        fail = "Lead-time check fails: the document does not state a lead time."
    elif f.lead_time_days > MAX_LEAD_DAYS:
        fail = f"Lead-time check fails: {f.lead_time_days} days is more than the {MAX_LEAD_DAYS}-day limit."
    elif f.min_order is None:
        fail = "Minimum-order check fails: the document does not state a minimum order."
    elif f.min_order > MAX_MIN_ORDER:
        fail = f"Minimum-order check fails: {f.min_order} units is more than the {MAX_MIN_ORDER}-unit limit."
    if fail:
        return VendorDecision(**f.model_dump(), decision="do_not_recommend", reason=fail)
    return VendorDecision(
        **f.model_dump(),
        decision="recommend",
        reason=(
            f"All four checks pass: approved vendor, price within our max unit cost for {f.sku}, "
            f"{f.lead_time_days}-day lead time, and {f.min_order}-unit minimum."
        ),
    )
