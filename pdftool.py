#!/usr/bin/env python3
"""
pdftool – A local PDF toolkit CLI.

Subcommands: merge, split, rotate, pagenumbers, reorder,
             compress, watermark, toimage, topdf, protect,
             unlock, extract

Dependencies: pymupdf (fitz), Pillow
"""

import argparse
import io
import math
import sys
import textwrap
from pathlib import Path

import fitz  # PyMuPDF – primary PDF engine


# ══════════════════════════════════════════════════════════════════════════════
# Shared helpers
# ══════════════════════════════════════════════════════════════════════════════


def error(msg: str) -> None:
    """Print *msg* to stderr and exit with code 1."""
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(1)


def require_file(path: str) -> Path:
    """Ensure *path* is an existing regular file; exit clearly on failure."""
    p = Path(path)
    if not p.exists():
        error(f"File not found: {path}")
    if not p.is_file():
        error(f"Not a regular file: {path}")
    return p


def ensure_parent_exists(path: str) -> Path:
    """Ensure the parent directory of *path* exists; exit on failure."""
    p = Path(path)
    parent = p.parent
    if str(parent) not in (".", "") and not parent.exists():
        error(f"Output directory does not exist: {parent}")
    return p


def open_pdf(path: str) -> fitz.Document:
    """Open *path* as a PDF; exit with a clear message on any failure."""
    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        error(f"Cannot open '{path}': {exc}")
    if not doc.is_pdf:
        doc.close()
        error(f"'{path}' does not appear to be a PDF file")
    return doc


def parse_page_ranges(spec: str, total: int,
                      reject_duplicates: bool = False) -> list:
    """
    Parse a comma-separated page-range spec (1-indexed) into an ordered list
    of 0-indexed page numbers.

    Duplicates are silently dropped by default.  Pass *reject_duplicates=True*
    to error out instead (useful for ``reorder``).

    Examples
    --------
    '1-3,5,8-10'  with total=10  →  [0, 1, 2, 4, 7, 8, 9]
    '5,1,3'       with total=5   →  [4, 0, 2]
    """
    pages: list = []
    seen: set = set()

    def _add(idx: int, display: int) -> None:
        if idx in seen:
            if reject_duplicates:
                error(f"Page {display} appears more than once in specification")
            return
        seen.add(idx)
        pages.append(idx)

    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            halves = part.split("-", 1)
            try:
                lo, hi = int(halves[0].strip()), int(halves[1].strip())
            except ValueError:
                error(f"Cannot parse page range: '{part}'")
            if lo < 1 or hi > total or lo > hi:
                error(
                    f"Page range '{part}' is invalid for a {total}-page document"
                )
            for idx in range(lo - 1, hi):
                _add(idx, idx + 1)
        else:
            try:
                num = int(part)
            except ValueError:
                error(f"Cannot parse page number: '{part}'")
            if num < 1 or num > total:
                error(f"Page {num} is out of range (document has {total} pages)")
            _add(num - 1, num)
    if not pages:
        error(f"Page specification '{spec}' produced no pages")
    return pages


def hex_to_rgb(hex_color: str) -> tuple:
    """Convert ``'#RRGGBB'`` to ``(r, g, b)`` floats in [0, 1]."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        error(f"Invalid color '{hex_color}'. Use '#RRGGBB' format.")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        error(f"Invalid color '{hex_color}'. Use '#RRGGBB' format.")
    return r / 255.0, g / 255.0, b / 255.0


def fmt_bytes(n: int) -> str:
    """Return a human-readable file-size string (e.g. ``'3.2 MB'``)."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def rotation_matrix(degrees: float) -> fitz.Matrix:
    """
    Return a ``fitz.Matrix`` that rotates counter-clockwise by *degrees*
    (positive = CCW as viewed on screen).
    """
    rad = math.radians(degrees)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    # PyMuPDF Matrix(a, b, c, d, e, f) convention:
    # x' = a*x + c*y + e,  y' = b*x + d*y + f
    return fitz.Matrix(cos_a, -sin_a, sin_a, cos_a, 0, 0)


# ══════════════════════════════════════════════════════════════════════════════
# merge
# ══════════════════════════════════════════════════════════════════════════════


def cmd_merge(args) -> None:
    """Concatenate multiple PDFs into one output PDF, in the order given."""
    if len(args.inputs) < 2:
        error("Provide at least two input PDFs to merge")
    for f in args.inputs:
        require_file(f)
    ensure_parent_exists(args.output)

    out = fitz.open()
    for path in args.inputs:
        src = open_pdf(path)
        out.insert_pdf(src)
        src.close()

    total_pages = len(out)
    out.save(args.output, garbage=3, deflate=True)
    out.close()
    print(f"Merged {len(args.inputs)} files → {args.output} ({total_pages} pages)")


# ══════════════════════════════════════════════════════════════════════════════
# split
# ══════════════════════════════════════════════════════════════════════════════


def cmd_split(args) -> None:
    """Extract specific page ranges from a PDF into a new file."""
    require_file(args.input)
    ensure_parent_exists(args.output)

    doc = open_pdf(args.input)
    pages = parse_page_ranges(args.pages, len(doc))

    out = fitz.open()
    for p in pages:
        out.insert_pdf(doc, from_page=p, to_page=p)

    out.save(args.output, garbage=3, deflate=True)
    out.close()
    doc.close()
    print(f"Extracted {len(pages)} page(s) from '{args.input}' → {args.output}")


# ══════════════════════════════════════════════════════════════════════════════
# rotate
# ══════════════════════════════════════════════════════════════════════════════


def cmd_rotate(args) -> None:
    """Rotate specified pages (or all pages) by 90, 180, or 270 degrees."""
    if args.degrees not in (90, 180, 270):
        error("--degrees must be 90, 180, or 270")

    require_file(args.input)
    ensure_parent_exists(args.output)

    doc = open_pdf(args.input)
    pages = (
        parse_page_ranges(args.pages, len(doc))
        if args.pages
        else list(range(len(doc)))
    )

    for idx in pages:
        page = doc[idx]
        page.set_rotation((page.rotation + args.degrees) % 360)

    doc.save(args.output, garbage=3, deflate=True)
    doc.close()
    print(f"Rotated {len(pages)} page(s) by {args.degrees}° → {args.output}")


# ══════════════════════════════════════════════════════════════════════════════
# pagenumbers
# ══════════════════════════════════════════════════════════════════════════════


_VALID_POSITIONS = {
    "bottom-left", "bottom-center", "bottom-right",
    "top-left",    "top-center",    "top-right",
}


def _pn_insertion_point(
    position: str,
    page_rect: fitz.Rect,
    margin: float,
    font_size: float,
    text_width: float,
) -> fitz.Point:
    """Compute the text-baseline insertion point for a page-number label."""
    w, h = page_rect.width, page_rect.height

    # Vertical: bottom uses (h - margin) as baseline; top adds cap-height
    y = h - margin if "bottom" in position else margin + font_size * 0.8

    # Horizontal: align by text width
    if "left" in position:
        x = margin
    elif "center" in position:
        x = (w - text_width) / 2.0
    else:  # right
        x = w - margin - text_width

    return fitz.Point(x, y)


def cmd_pagenumbers(args) -> None:
    """Stamp page numbers onto each page as a text overlay."""
    if args.position not in _VALID_POSITIONS:
        error(
            f"Unknown position '{args.position}'. "
            f"Valid choices: {', '.join(sorted(_VALID_POSITIONS))}"
        )

    require_file(args.input)
    ensure_parent_exists(args.output)

    doc = open_pdf(args.input)
    total = len(doc)

    skip: set = set()
    if args.skip:
        skip = set(parse_page_ranges(args.skip, total))

    stamped = 0
    for i, page in enumerate(doc):
        if i in skip:
            continue

        display_n = args.start + i
        label = (
            args.format
            .replace("{n}", str(display_n))
            .replace("{total}", str(total))
        )

        tw = fitz.get_text_length(label, fontname="helv", fontsize=args.font_size)
        pt = _pn_insertion_point(
            args.position, page.rect, args.margin, args.font_size, tw
        )

        page.insert_text(
            pt,
            label,
            fontsize=args.font_size,
            fontname="helv",
            color=(0.0, 0.0, 0.0),
        )
        stamped += 1

    doc.save(args.output, garbage=3, deflate=True)
    doc.close()
    print(f"Added page numbers to {stamped} page(s) → {args.output}")


# ══════════════════════════════════════════════════════════════════════════════
# reorder
# ══════════════════════════════════════════════════════════════════════════════


def cmd_reorder(args) -> None:
    """Reorder pages by specifying the desired page sequence."""
    require_file(args.input)
    ensure_parent_exists(args.output)

    doc = open_pdf(args.input)
    total = len(doc)
    pages = parse_page_ranges(args.pages, total, reject_duplicates=True)

    out = fitz.open()
    for p in pages:
        out.insert_pdf(doc, from_page=p, to_page=p)

    out.save(args.output, garbage=3, deflate=True)
    out.close()
    doc.close()
    print(f"Reordered {len(pages)} page(s) → {args.output}")


# ══════════════════════════════════════════════════════════════════════════════
# compress
# ══════════════════════════════════════════════════════════════════════════════


_JPEG_QUALITY = {"low": 40, "medium": 65, "high": 85}


def _recompress_images(doc: fitz.Document, jpeg_quality: int) -> int:
    """
    Walk the PDF's image xrefs, re-encode each with Pillow at *jpeg_quality*,
    and replace the stream in-place if the result is smaller.

    Returns the number of images processed.
    """
    from PIL import Image  # Pillow – only needed here

    seen: set = set()
    count = 0

    for page_num in range(len(doc)):
        for img_tuple in doc.get_page_images(page_num, full=True):
            xref = img_tuple[0]
            if xref in seen:
                continue
            seen.add(xref)

            try:
                extracted = doc.extract_image(xref)
            except Exception:
                continue
            if not extracted:
                continue

            raw = extracted["image"]
            # Skip very small images – overhead not worth it
            if len(raw) < 4096:
                continue

            try:
                img = Image.open(io.BytesIO(raw))
            except Exception:
                continue

            buf = io.BytesIO()
            # Preserve alpha by encoding as PNG; otherwise use JPEG
            if img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            ):
                img.convert("RGBA").save(buf, "PNG", optimize=True)
            else:
                img.convert("RGB").save(
                    buf, "JPEG", quality=jpeg_quality, optimize=True
                )

            new_data = buf.getvalue()
            if len(new_data) < len(raw):
                try:
                    doc.update_stream(xref, new_data)
                    count += 1
                except Exception:
                    pass

    return count


def cmd_compress(args) -> None:
    """Compress a PDF by resampling images and stripping unused data."""
    require_file(args.input)
    ensure_parent_exists(args.output)

    quality = _JPEG_QUALITY.get(args.quality)
    if quality is None:
        error(f"--quality must be one of: {', '.join(_JPEG_QUALITY)}")

    original_size = Path(args.input).stat().st_size
    doc = open_pdf(args.input)

    imgs_processed = _recompress_images(doc, quality)

    # Strip all metadata
    doc.set_metadata({})

    # Save with maximum garbage collection and stream compression
    doc.save(
        args.output,
        garbage=4,     # remove unreferenced objects, deduplicate streams
        deflate=True,  # zlib-compress all content streams
        clean=True,    # sanitise content streams
        linear=False,  # non-linearised is typically smaller
    )
    doc.close()

    final_size = Path(args.output).stat().st_size
    saved = original_size - final_size
    pct = (saved / original_size * 100.0) if original_size else 0.0

    if saved >= 0:
        delta_line = f"  Saved     : {fmt_bytes(saved)} ({pct:.1f}% reduction)"
    else:
        delta_line = (
            f"  Grew by   : {fmt_bytes(-saved)} "
            f"(output is {-pct:.1f}% larger — file has little to compress)"
        )

    print(
        f"Compressed '{args.input}' → {args.output}\n"
        f"  Original  : {fmt_bytes(original_size)}\n"
        f"  Output    : {fmt_bytes(final_size)}\n"
        f"{delta_line}\n"
        f"  Images    : {imgs_processed} re-encoded at '{args.quality}' quality"
    )


# ══════════════════════════════════════════════════════════════════════════════
# watermark
# ══════════════════════════════════════════════════════════════════════════════


def _stamp_text_watermark(
    page: fitz.Page,
    text: str,
    font_size: float,
    opacity: float,
    angle: float,
    color: tuple,
) -> None:
    """
    Draw a centred, rotated text watermark on *page*.

    The text is first laid out horizontally at the page centre, then morphed
    (rotated about the page centre) to the requested angle.
    """
    rect = page.rect
    cx, cy = rect.width / 2.0, rect.height / 2.0

    tw = fitz.get_text_length(text, fontname="helv", fontsize=font_size)

    # Horizontal insertion point so the text is centred before rotation
    x0 = cx - tw / 2.0
    # Baseline sits at the vertical centre; small upward nudge for visual balance
    y0 = cy + font_size * 0.25

    mat = rotation_matrix(angle)

    page.insert_text(
        fitz.Point(x0, y0),
        text,
        fontsize=font_size,
        fontname="helv",
        color=color,
        fill_opacity=opacity,
        stroke_opacity=opacity,
        morph=(fitz.Point(cx, cy), mat),
    )


def _stamp_image_watermark(
    page: fitz.Page,
    img_path: str,
    opacity: float,
    scale: float,
) -> None:
    """
    Overlay a semi-transparent, centred image watermark on *page*.

    Pillow is used to bake the alpha-channel opacity; the result is inserted
    via PyMuPDF's ``insert_image``.
    """
    from PIL import Image  # Pillow – only needed here

    img = Image.open(img_path).convert("RGBA")
    r_ch, g_ch, b_ch, a_ch = img.split()
    # Scale the alpha channel to achieve the requested opacity
    a_ch = a_ch.point(lambda v: int(v * opacity))
    img.putalpha(a_ch)

    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)

    pw, ph = page.rect.width, page.rect.height
    iw, ih = img.size

    target_w = pw * scale
    target_h = ih * (target_w / iw)

    x0 = (pw - target_w) / 2.0
    y0 = (ph - target_h) / 2.0
    dest = fitz.Rect(x0, y0, x0 + target_w, y0 + target_h)

    page.insert_image(dest, stream=buf.read(), overlay=True)


def cmd_watermark(args) -> None:
    """Add a text or image watermark to a PDF."""
    if not args.text and not args.image:
        error("Provide either --text TEXT or --image FILE")
    if args.text and args.image:
        error("Use only one of --text or --image, not both")
    if args.image:
        require_file(args.image)
    if not (0.0 < args.opacity <= 1.0):
        error("--opacity must be in the range (0, 1]")

    require_file(args.input)
    ensure_parent_exists(args.output)

    doc = open_pdf(args.input)
    total = len(doc)

    target_pages: set = (
        set(parse_page_ranges(args.pages, total)) if args.pages else set(range(total))
    )

    color = hex_to_rgb(args.color) if args.text else (0.0, 0.0, 0.0)

    for i, page in enumerate(doc):
        if i not in target_pages:
            continue
        if args.text:
            _stamp_text_watermark(
                page,
                args.text,
                font_size=args.font_size,
                opacity=args.opacity,
                angle=args.angle,
                color=color,
            )
        else:
            _stamp_image_watermark(
                page,
                args.image,
                opacity=args.opacity,
                scale=args.scale,
            )

    doc.save(args.output, garbage=3, deflate=True)
    doc.close()

    wm_kind = "text" if args.text else "image"
    print(
        f"Added {wm_kind} watermark to {len(target_pages)} page(s) → {args.output}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# toimage
# ══════════════════════════════════════════════════════════════════════════════


def cmd_toimage(args) -> None:
    """Rasterize PDF pages to individual image files."""
    require_file(args.input)

    fmt = args.format.lower()
    if fmt not in ("png", "jpg", "jpeg"):
        error("--format must be 'png' or 'jpg'")
    ext = "jpg" if fmt in ("jpg", "jpeg") else "png"

    if args.dpi < 1:
        error("--dpi must be a positive integer")

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = open_pdf(args.input)
    total = len(doc)

    pages = (
        parse_page_ranges(args.pages, total) if args.pages else list(range(total))
    )

    # PyMuPDF's internal resolution is 72 dpi; scale accordingly
    scale = args.dpi / 72.0
    mat = fitz.Matrix(scale, scale)

    saved_files = []
    for idx in pages:
        page = doc[idx]
        pix = page.get_pixmap(matrix=mat, alpha=(ext == "png"))
        filename = out_dir / f"page_{idx + 1:03d}.{ext}"
        pix.save(str(filename))
        saved_files.append(filename)

    doc.close()
    print(
        f"Saved {len(saved_files)} image(s) to '{out_dir}/' "
        f"({args.dpi} dpi, {ext.upper()})"
    )


# ══════════════════════════════════════════════════════════════════════════════
# topdf
# ══════════════════════════════════════════════════════════════════════════════


_PAGE_SIZES = {
    "a4":     (595.0, 842.0),   # width × height in PDF points (72 dpi)
    "letter": (612.0, 792.0),
}


def cmd_topdf(args) -> None:
    """Combine images into a PDF with one image per page."""
    from PIL import Image as PILImage  # Pillow – only needed here

    for f in args.inputs:
        require_file(f)
    ensure_parent_exists(args.output)

    page_key = args.page_size.lower()
    if page_key not in _PAGE_SIZES:
        error(f"--page-size must be 'a4' or 'letter'")

    pw, ph = _PAGE_SIZES[page_key]
    margin = args.margin
    if margin < 0:
        error("--margin must be non-negative")

    avail_w = pw - 2.0 * margin
    avail_h = ph - 2.0 * margin
    if avail_w <= 0 or avail_h <= 0:
        error("Margin is too large for the chosen page size")

    out = fitz.open()

    for img_path in args.inputs:
        try:
            pil_img = PILImage.open(img_path)
        except Exception as exc:
            error(f"Cannot open image '{img_path}': {exc}")

        iw, ih = pil_img.size
        # Fit image within available area, preserving aspect ratio; never upscale
        scale = min(avail_w / iw, avail_h / ih, 1.0)
        sw, sh = iw * scale, ih * scale

        # Centre on the page
        x0 = margin + (avail_w - sw) / 2.0
        y0 = margin + (avail_h - sh) / 2.0
        dest = fitz.Rect(x0, y0, x0 + sw, y0 + sh)

        page = out.new_page(width=pw, height=ph)

        # Encode image as PNG stream; preserve alpha if present
        buf = io.BytesIO()
        if pil_img.mode in ("RGBA", "LA"):
            pil_img.convert("RGBA").save(buf, "PNG")
        else:
            pil_img.convert("RGB").save(buf, "PNG")
        buf.seek(0)

        page.insert_image(dest, stream=buf.read())

    out.save(args.output, garbage=3, deflate=True)
    out.close()
    print(
        f"Created PDF from {len(args.inputs)} image(s) → {args.output} "
        f"({page_key.upper()}, {margin:.0f}pt margin)"
    )


# ══════════════════════════════════════════════════════════════════════════════
# protect
# ══════════════════════════════════════════════════════════════════════════════


_ENCRYPT_METHODS = {
    "AES-128": fitz.PDF_ENCRYPT_AES_128,
    "AES-256": fitz.PDF_ENCRYPT_AES_256,
}

# PyMuPDF permission flags
_PERM_FLAGS = {
    "print":    fitz.PDF_PERM_PRINT,
    "modify":   fitz.PDF_PERM_MODIFY,
    "copy":     fitz.PDF_PERM_COPY,
    "annotate": fitz.PDF_PERM_ANNOTATE,
}


def cmd_protect(args) -> None:
    """Encrypt a PDF with a password."""
    require_file(args.input)
    ensure_parent_exists(args.output)

    if not args.user_password and not args.owner_password:
        error("Provide at least one of --user-password or --owner-password")

    method = _ENCRYPT_METHODS.get(args.encryption)
    if method is None:
        error(f"--encryption must be one of: {', '.join(_ENCRYPT_METHODS)}")

    # Build permission bitmask
    perm = 0
    if args.permissions:
        perm_list = [x.strip() for x in args.permissions.split(",")]
        for p in perm_list:
            if p not in _PERM_FLAGS:
                error(f"Unknown permission '{p}'. Valid: {', '.join(_PERM_FLAGS)}")
            perm |= _PERM_FLAGS[p]
    else:
        # Default: grant all permissions
        for flag in _PERM_FLAGS.values():
            perm |= flag

    doc = open_pdf(args.input)

    doc.save(
        args.output,
        encryption=method,
        user_pw=args.user_password or "",
        owner_pw=args.owner_password or args.user_password or "",
        permissions=perm,
        garbage=3,
        deflate=True,
    )
    doc.close()

    parts = []
    if args.user_password:
        parts.append("user password set")
    if args.owner_password:
        parts.append("owner password set")
    print(
        f"Protected '{args.input}' → {args.output} "
        f"({', '.join(parts)}, {args.encryption})"
    )


# ══════════════════════════════════════════════════════════════════════════════
# unlock
# ══════════════════════════════════════════════════════════════════════════════


def cmd_unlock(args) -> None:
    """Remove encryption from a PDF given the correct password."""
    require_file(args.input)
    ensure_parent_exists(args.output)

    try:
        doc = fitz.open(str(args.input))
    except Exception as exc:
        error(f"Cannot open '{args.input}': {exc}")

    if not doc.is_pdf:
        doc.close()
        error(f"'{args.input}' does not appear to be a PDF file")

    if not doc.is_encrypted:
        doc.close()
        error("This PDF is not encrypted")

    if not doc.authenticate(args.password):
        doc.close()
        error("Incorrect password")

    # Save without encryption
    doc.save(args.output, garbage=3, deflate=True)
    doc.close()
    print(f"Unlocked '{args.input}' → {args.output}")


# ══════════════════════════════════════════════════════════════════════════════
# extract
# ══════════════════════════════════════════════════════════════════════════════


def cmd_extract(args) -> None:
    """Extract text content from PDF pages."""
    require_file(args.input)

    doc = open_pdf(args.input)
    total = len(doc)

    pages = (
        parse_page_ranges(args.pages, total)
        if args.pages
        else list(range(total))
    )

    out_path = Path(args.output) if args.output else None
    if out_path:
        ensure_parent_exists(args.output)

    chunks = []
    for idx in pages:
        page = doc[idx]
        text = page.get_text("text").strip()
        if text:
            chunks.append(f"--- Page {idx + 1} ---\n{text}")

    doc.close()

    result = "\n\n".join(chunks) if chunks else "(No text found)"

    if out_path:
        out_path.write_text(result, encoding="utf-8")
        print(
            f"Extracted text from {len(pages)} page(s) → {args.output} "
            f"({len(result)} characters)"
        )
    else:
        print(result)


# ══════════════════════════════════════════════════════════════════════════════
# CLI – argument parser
# ══════════════════════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="pdftool",
        description="A local PDF toolkit: merge, split, rotate, compress, watermark, and more.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    # ── merge ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "merge",
        help="Concatenate multiple PDFs into one",
        description="Concatenate multiple PDFs into one, in the order given.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool merge a.pdf b.pdf c.pdf -o combined.pdf
              pdftool merge chapter*.pdf -o book.pdf
        """),
    )
    p.add_argument("inputs", nargs="+", metavar="file.pdf",
                   help="Input PDF files (two or more)")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.set_defaults(func=cmd_merge)

    # ── split ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "split",
        help="Extract specific pages into a new PDF",
        description="Extract specific page ranges from a PDF into a new file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool split report.pdf -p 1-3,5 -o pages.pdf
              pdftool split book.pdf -p 10-20 -o chapter2.pdf
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-p", "--pages", required=True, metavar="RANGES",
                   help="Pages to extract, e.g. '1-3,5,8-10' (1-indexed)")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.set_defaults(func=cmd_split)

    # ── rotate ────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "rotate",
        help="Rotate pages by 90, 180, or 270 degrees",
        description="Rotate specified pages (or all pages if -p is omitted).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool rotate scanned.pdf -d 90 -o fixed.pdf
              pdftool rotate doc.pdf -p 1,3 -d 180 -o flipped.pdf
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-p", "--pages", metavar="PAGES",
                   help="Pages to rotate (default: all), e.g. '1,3,5'")
    p.add_argument("-d", "--degrees", type=int, required=True,
                   choices=[90, 180, 270],
                   help="Rotation angle: 90, 180, or 270")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.set_defaults(func=cmd_rotate)

    # ── pagenumbers ───────────────────────────────────────────────────────────
    p = sub.add_parser(
        "pagenumbers",
        help="Add page numbers to every page",
        description=(
            "Stamp page numbers onto pages as a text overlay. "
            "Use {n} for the current number and {total} for the total page count."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool pagenumbers doc.pdf -o out.pdf
              pdftool pagenumbers doc.pdf --position top-right --format "Page {n} of {total}" -o out.pdf
              pdftool pagenumbers doc.pdf --start 5 --skip 1 --font-size 10 -o out.pdf
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.add_argument("--position", default="bottom-center",
                   choices=sorted(_VALID_POSITIONS),
                   help="Label position (default: bottom-center)")
    p.add_argument("--font-size", type=float, default=12, metavar="PT",
                   help="Font size in points (default: 12)")
    p.add_argument("--start", type=int, default=1,
                   help="Starting page number (default: 1)")
    p.add_argument("--format", default="{n}",
                   help="Format string: {n}=page, {total}=total (default: '{n}')")
    p.add_argument("--margin", type=float, default=36, metavar="PT",
                   help="Distance from page edge in points (default: 36)")
    p.add_argument("--skip", metavar="PAGES",
                   help="Page numbers to skip, e.g. '1' to skip the cover")
    p.set_defaults(func=cmd_pagenumbers)

    # ── reorder ───────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "reorder",
        help="Reorder pages by specifying the new sequence",
        description=(
            "Emit pages in the order you specify. "
            "You may list a subset to drop unwanted pages."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool reorder doc.pdf -p 3,1,2,5,4 -o reordered.pdf
              pdftool reorder doc.pdf -p 2-5,1 -o reordered.pdf
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-p", "--pages", required=True, metavar="ORDER",
                   help="New page order as 1-indexed list, e.g. '3,1,2,5,4'")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.set_defaults(func=cmd_reorder)

    # ── compress ──────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "compress",
        help="Reduce PDF file size",
        description=(
            "Compress a PDF by re-encoding images, removing duplicate objects, "
            "stripping metadata, and garbage-collecting unused resources. "
            "Prints original and final sizes with the compression ratio."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            quality levels:
              low    – JPEG quality 40 (smallest file, visible artefacts)
              medium – JPEG quality 65 (default, good balance)
              high   – JPEG quality 85 (near-lossless images)

            examples:
              pdftool compress big.pdf -o small.pdf
              pdftool compress report.pdf --quality high -o report_small.pdf
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.add_argument("--quality", choices=["low", "medium", "high"], default="medium",
                   help="Image re-compression quality (default: medium)")
    p.set_defaults(func=cmd_compress)

    # ── watermark ─────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "watermark",
        help="Add a text or image watermark",
        description=(
            "Stamp a centred diagonal text watermark or a centred image "
            "watermark onto every page (or specific pages with --pages)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool watermark doc.pdf --text "CONFIDENTIAL" -o out.pdf
              pdftool watermark doc.pdf --text "DRAFT" --opacity 0.2 --angle 30 --color "#0000FF" -o out.pdf
              pdftool watermark doc.pdf --image logo.png --opacity 0.3 --scale 0.4 -o out.pdf
              pdftool watermark doc.pdf --text "SECRET" --pages 2-5 -o out.pdf
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.add_argument("--text", metavar="TEXT", help="Watermark text")
    p.add_argument("--image", metavar="FILE", help="Watermark image file (PNG/JPG)")
    p.add_argument("--font-size", type=float, default=60, metavar="PT",
                   help="Font size for text watermark (default: 60)")
    p.add_argument("--opacity", type=float, default=0.3,
                   help="Watermark opacity 0–1 (default: 0.3)")
    p.add_argument("--angle", type=float, default=45,
                   help="Text rotation angle in degrees, CCW (default: 45)")
    p.add_argument("--color", default="#FF0000", metavar="#RRGGBB",
                   help="Text colour (default: #FF0000)")
    p.add_argument("--scale", type=float, default=0.5,
                   help="Image width as fraction of page width (default: 0.5)")
    p.add_argument("--pages", metavar="PAGES",
                   help="Pages to watermark (default: all)")
    p.set_defaults(func=cmd_watermark)

    # ── toimage ───────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "toimage",
        help="Rasterize PDF pages to image files",
        description=(
            "Render each page to an image file. "
            "Output files are named page_001.png, page_002.png, etc."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool toimage doc.pdf -o images/
              pdftool toimage doc.pdf --dpi 300 --format jpg -o thumbs/
              pdftool toimage doc.pdf --pages 1,3,5 -o selected/
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-o", "--output", required=True, metavar="DIR",
                   help="Output directory (created if it does not exist)")
    p.add_argument("--format", default="png", choices=["png", "jpg"],
                   help="Image format (default: png)")
    p.add_argument("--dpi", type=int, default=150,
                   help="Rendering resolution in DPI (default: 150)")
    p.add_argument("--pages", metavar="PAGES",
                   help="Pages to convert (default: all)")
    p.set_defaults(func=cmd_toimage)

    # ── topdf ─────────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "topdf",
        help="Combine images into a PDF (one image per page)",
        description=(
            "Wrap each input image in a PDF page. "
            "Images are scaled to fit the page while preserving aspect ratio, "
            "centred within the specified margin."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool topdf scan1.jpg scan2.jpg -o scans.pdf
              pdftool topdf *.png -o slides.pdf --page-size letter --margin 10
        """),
    )
    p.add_argument("inputs", nargs="+", metavar="image",
                   help="Input image files (JPEG, PNG, …)")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.add_argument("--page-size", default="a4",
                   choices=["a4", "A4", "letter", "Letter"],
                   help="Page size (default: a4)")
    p.add_argument("--margin", type=float, default=20, metavar="PT",
                   help="Margin in PDF points (default: 20)")
    p.set_defaults(func=cmd_topdf)

    # ── protect ──────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "protect",
        help="Password-protect (encrypt) a PDF",
        description=(
            "Encrypt a PDF with a user password (required to open) "
            "and/or an owner password (required to change permissions)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool protect doc.pdf --user-password secret -o secured.pdf
              pdftool protect doc.pdf --owner-password admin --permissions print,copy -o secured.pdf
              pdftool protect doc.pdf --user-password open --owner-password admin --encryption AES-256 -o secured.pdf
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.add_argument("--user-password", metavar="PW",
                   help="Password required to open the PDF")
    p.add_argument("--owner-password", metavar="PW",
                   help="Password required to change permissions")
    p.add_argument("--encryption", default="AES-256",
                   choices=["AES-128", "AES-256"],
                   help="Encryption method (default: AES-256)")
    p.add_argument("--permissions", metavar="LIST",
                   help="Comma-separated: print,modify,copy,annotate (default: all)")
    p.set_defaults(func=cmd_protect)

    # ── unlock ───────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "unlock",
        help="Remove password protection from a PDF",
        description="Decrypt a password-protected PDF and save an unencrypted copy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool unlock secured.pdf --password secret -o unlocked.pdf
        """),
    )
    p.add_argument("input", help="Input (encrypted) PDF file")
    p.add_argument("-o", "--output", required=True, metavar="OUTPUT",
                   help="Output PDF path")
    p.add_argument("--password", required=True, metavar="PW",
                   help="Password to unlock the PDF")
    p.set_defaults(func=cmd_unlock)

    # ── extract ──────────────────────────────────────────────────────────────
    p = sub.add_parser(
        "extract",
        help="Extract text content from a PDF",
        description=(
            "Extract all text from each page and output as plain text. "
            "Optionally select specific pages."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              pdftool extract doc.pdf
              pdftool extract doc.pdf -o text.txt
              pdftool extract doc.pdf --pages 1-5 -o chapter1.txt
        """),
    )
    p.add_argument("input", help="Input PDF file")
    p.add_argument("-o", "--output", metavar="OUTPUT",
                   help="Output text file (omit to print to stdout)")
    p.add_argument("--pages", metavar="PAGES",
                   help="Pages to extract text from (default: all)")
    p.set_defaults(func=cmd_extract)

    return parser


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    """Parse arguments and dispatch to the appropriate subcommand handler."""
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
