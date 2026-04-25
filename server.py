#!/usr/bin/env python3
"""
server.py – Flask web server for pdftool.

Serves the browser UI and wraps every pdftool subcommand as an HTTP endpoint.
Each endpoint accepts multipart/form-data, runs the corresponding pdftool
function in-process, and returns the processed file for download.

Usage (activate the lovepdf venv first):
    python server.py
Then open  http://localhost:3003  in your browser.
"""

import base64
import io
import os
import shutil
import sys
import tempfile
import zipfile
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import fitz as _fitz  # PyMuPDF – used directly for preview
import json as _json
APP_VERSION = _json.load(open(os.path.join(os.path.dirname(__file__), "version.json")))["version"]
from flask import Flask, jsonify, render_template, request, send_file

# Make pdftool importable from the same directory
sys.path.insert(0, str(Path(__file__).parent))
import pdftool  # noqa: E402

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB upload cap


@app.route("/api/health", methods=["GET"])
def api_health():
    port = int(os.environ.get("PORT", 3003))
    return jsonify(service="pdf", status="ok", version=APP_VERSION, port=port), 200


# ══════════════════════════════════════════════════════════════════════════════
# Internal helpers
# ══════════════════════════════════════════════════════════════════════════════


def run_cmd(func, kwargs: dict) -> tuple:
    """
    Call a pdftool handler with a synthetic argparse Namespace built from
    *kwargs*.  Captures stdout (success message) and stderr (error text).
    Converts sys.exit(1) → (False, error_string).

    Returns (success: bool, message: str).
    """
    args = Namespace(**kwargs)
    out_buf, err_buf = io.StringIO(), io.StringIO()
    try:
        with redirect_stdout(out_buf), redirect_stderr(err_buf):
            func(args)
        return True, out_buf.getvalue().strip()
    except SystemExit:
        raw = err_buf.getvalue().strip()
        err = raw.removeprefix("Error: ").strip()
        return False, err or "An unknown error occurred"


def bad_request(message: str):
    """Return a standard JSON 400 response."""
    return jsonify(error=message), 400


def parse_int_field(
    name: str,
    default: int,
    *,
    label: Optional[str] = None,
    allowed: Optional[set[int]] = None,
    min_value: Optional[int] = None,
    max_value: Optional[int] = None,
):
    """
    Parse an integer field from request.form with optional bounds/choices.
    Returns (value, error_response_or_None).
    """
    text = request.form.get(name, "").strip()
    value_name = label or name
    if text == "":
        value = default
    else:
        try:
            value = int(text)
        except ValueError:
            return None, bad_request(f"{value_name} must be an integer")

    if allowed is not None and value not in allowed:
        options = ", ".join(str(v) for v in sorted(allowed))
        return None, bad_request(f"{value_name} must be one of: {options}")
    if min_value is not None and value < min_value:
        return None, bad_request(f"{value_name} must be at least {min_value}")
    if max_value is not None and value > max_value:
        return None, bad_request(f"{value_name} must be at most {max_value}")
    return value, None


def parse_float_field(
    name: str,
    default: float,
    *,
    label: Optional[str] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
):
    """
    Parse a float field from request.form with optional bounds.
    Returns (value, error_response_or_None).
    """
    text = request.form.get(name, "").strip()
    value_name = label or name
    if text == "":
        value = default
    else:
        try:
            value = float(text)
        except ValueError:
            return None, bad_request(f"{value_name} must be a number")

    if min_value is not None and value < min_value:
        return None, bad_request(f"{value_name} must be at least {min_value}")
    if max_value is not None and value > max_value:
        return None, bad_request(f"{value_name} must be at most {max_value}")
    return value, None


def parse_choice_field(
    name: str,
    default: str,
    allowed: set[str],
    *,
    label: Optional[str] = None,
):
    """Parse a lower-cased choice field from request.form."""
    text = request.form.get(name, "").strip()
    value_name = label or name
    value = (text or default).lower()
    if value not in allowed:
        options = ", ".join(sorted(allowed))
        return None, bad_request(f"{value_name} must be one of: {options}")
    return value, None


def save_upload(storage, suffix: str = ".pdf") -> str:
    """Save a Werkzeug FileStorage to a named temp file; return the path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    storage.save(path)
    return path


def tmp_path(suffix: str = ".pdf") -> str:
    """Allocate an empty temp file and return its path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return path


def file_resp(path: str, name: str, mime: str, msg: str = ""):
    """
    Read *path* into memory, delete it, return a send_file response.
    The success message (if any) is passed in the X-Message header,
    URL-encoded to survive HTTP header restrictions.
    """
    data = Path(path).read_bytes()
    _rm(path)
    resp = send_file(io.BytesIO(data), mimetype=mime,
                     as_attachment=True, download_name=name)
    if msg:
        resp.headers["X-Message"] = quote(msg, safe=" ")
    return resp


def _rm(*paths):
    """Delete files silently."""
    for p in paths:
        if p:
            try:
                os.unlink(p)
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# Routes
# ══════════════════════════════════════════════════════════════════════════════


@app.route("/")
def index():
    return render_template("index.html")


# ── preview ──────────────────────────────────────────────────────────────────

@app.route("/api/preview", methods=["POST"])
def api_preview():
    """Render PDF pages as low-res thumbnails and return as base64 JSON."""
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    max_pages = min(int(request.form.get("max_pages", "20")), 50)

    inp = save_upload(f)
    try:
        doc = _fitz.open(inp)
    except Exception:
        _rm(inp)
        return bad_request("Cannot open this file as a PDF")

    if not doc.is_pdf:
        doc.close()
        _rm(inp)
        return bad_request("Not a valid PDF file")

    total = len(doc)
    pages_to_render = min(total, max_pages)
    scale = 96 / 72.0  # 96 DPI thumbnails
    mat = _fitz.Matrix(scale, scale)

    thumbnails = []
    for i in range(pages_to_render):
        page = doc[i]
        pix = page.get_pixmap(matrix=mat, alpha=False)
        png_data = pix.tobytes("png")
        b64 = base64.b64encode(png_data).decode("ascii")
        thumbnails.append({
            "page": i + 1,
            "data": f"data:image/png;base64,{b64}",
            "width": pix.width,
            "height": pix.height,
        })

    doc.close()
    _rm(inp)

    return jsonify(total_pages=total, pages=thumbnails)


# ── merge ─────────────────────────────────────────────────────────────────────

@app.route("/api/merge", methods=["POST"])
def api_merge():
    files = request.files.getlist("files")
    if len(files) < 2:
        return bad_request("Upload at least two PDF files")

    inputs = [save_upload(f) for f in files]
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_merge, {"inputs": inputs, "output": out})
    _rm(*inputs)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "merged.pdf", "application/pdf", msg)


# ── split ─────────────────────────────────────────────────────────────────────

@app.route("/api/split", methods=["POST"])
def api_split():
    f = request.files.get("file")
    pages = request.form.get("pages", "").strip()
    if not f:
        return bad_request("No PDF file provided")
    if not pages:
        return bad_request("Enter page ranges (e.g. 1-3,5)")

    inp = save_upload(f)
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_split, {"input": inp, "pages": pages, "output": out})
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "split.pdf", "application/pdf", msg)


# ── rotate ────────────────────────────────────────────────────────────────────

@app.route("/api/rotate", methods=["POST"])
def api_rotate():
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    degrees, err = parse_int_field(
        "degrees",
        90,
        label="degrees",
        allowed={90, 180, 270},
    )
    if err:
        return err

    pages = request.form.get("pages", "").strip() or None
    inp = save_upload(f)
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_rotate, {
        "input": inp, "output": out,
        "degrees": degrees,
        "pages": pages,
    })
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "rotated.pdf", "application/pdf", msg)


# ── pagenumbers ───────────────────────────────────────────────────────────────

@app.route("/api/pagenumbers", methods=["POST"])
def api_pagenumbers():
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    position = request.form.get("position", "bottom-center").strip()
    if not position:
        position = "bottom-center"
    if position not in pdftool._VALID_POSITIONS:
        valid = ", ".join(sorted(pdftool._VALID_POSITIONS))
        return bad_request(f"position must be one of: {valid}")

    font_size, err = parse_float_field("font_size", 12, label="font_size", min_value=1)
    if err:
        return err
    start, err = parse_int_field("start", 1, label="start")
    if err:
        return err
    margin, err = parse_float_field("margin", 36, label="margin", min_value=0)
    if err:
        return err

    inp = save_upload(f)
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_pagenumbers, {
        "input": inp, "output": out,
        "position": position,
        "font_size": font_size,
        "start": start,
        "format": request.form.get("format", "{n}"),
        "margin": margin,
        "skip": request.form.get("skip", "").strip() or None,
    })
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "numbered.pdf", "application/pdf", msg)


# ── reorder ───────────────────────────────────────────────────────────────────

@app.route("/api/reorder", methods=["POST"])
def api_reorder():
    f = request.files.get("file")
    pages = request.form.get("pages", "").strip()
    if not f:
        return bad_request("No PDF file provided")
    if not pages:
        return bad_request("Enter the new page order (e.g. 3,1,2)")

    inp = save_upload(f)
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_reorder, {"input": inp, "pages": pages, "output": out})
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "reordered.pdf", "application/pdf", msg)


# ── compress ──────────────────────────────────────────────────────────────────

@app.route("/api/compress", methods=["POST"])
def api_compress():
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    quality, err = parse_choice_field(
        "quality", "medium", {"low", "medium", "high"}, label="quality"
    )
    if err:
        return err

    inp = save_upload(f)
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_compress, {
        "input": inp, "output": out,
        "quality": quality,
    })
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "compressed.pdf", "application/pdf", msg)


# ── watermark ─────────────────────────────────────────────────────────────────

@app.route("/api/watermark", methods=["POST"])
def api_watermark():
    f = request.files.get("file")
    wm_img = request.files.get("watermark_image")
    text = request.form.get("text", "").strip() or None

    if not f:
        return bad_request("No PDF file provided")
    if not text and (not wm_img or not wm_img.filename):
        return bad_request("Provide watermark text or an image file")
    if text and wm_img and wm_img.filename:
        return bad_request("Provide either watermark text or an image file, not both")

    font_size, err = parse_float_field("font_size", 60, label="font_size", min_value=1)
    if err:
        return err
    opacity, err = parse_float_field("opacity", 0.3, label="opacity", min_value=0.01, max_value=1.0)
    if err:
        return err
    angle, err = parse_float_field("angle", 45, label="angle")
    if err:
        return err
    scale, err = parse_float_field("scale", 0.5, label="scale", min_value=0.01)
    if err:
        return err

    inp = save_upload(f)
    out = tmp_path()
    wm_path = None

    if wm_img and wm_img.filename:
        suffix = Path(wm_img.filename).suffix or ".png"
        wm_path = save_upload(wm_img, suffix=suffix)

    ok, msg = run_cmd(pdftool.cmd_watermark, {
        "input": inp, "output": out,
        "text": text,
        "image": wm_path,
        "font_size": font_size,
        "opacity": opacity,
        "angle": angle,
        "color": request.form.get("color", "#FF0000"),
        "scale": scale,
        "pages": request.form.get("pages", "").strip() or None,
    })
    _rm(inp, wm_path)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "watermarked.pdf", "application/pdf", msg)


# ── toimage ───────────────────────────────────────────────────────────────────

@app.route("/api/toimage", methods=["POST"])
def api_toimage():
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    fmt, err = parse_choice_field("format", "png", {"png", "jpg"}, label="format")
    if err:
        return err
    dpi, err = parse_int_field("dpi", 150, label="dpi", min_value=1)
    if err:
        return err

    inp = save_upload(f)
    out_dir = tempfile.mkdtemp()

    ok, msg = run_cmd(pdftool.cmd_toimage, {
        "input": inp, "output": out_dir,
        "format": fmt,
        "dpi": dpi,
        "pages": request.form.get("pages", "").strip() or None,
    })
    _rm(inp)

    if not ok:
        shutil.rmtree(out_dir, ignore_errors=True)
        return jsonify(error=msg), 400

    # Bundle all rendered images into a single ZIP for download
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(Path(out_dir).iterdir()):
            if p.is_file():
                zf.write(p, p.name)
    buf.seek(0)
    shutil.rmtree(out_dir, ignore_errors=True)

    resp = send_file(buf, mimetype="application/zip",
                     as_attachment=True, download_name="images.zip")
    if msg:
        resp.headers["X-Message"] = quote(msg, safe=" ")
    return resp


# ── topdf ─────────────────────────────────────────────────────────────────────

@app.route("/api/topdf", methods=["POST"])
def api_topdf():
    files = request.files.getlist("files")
    if not files or not files[0].filename:
        return bad_request("No image files provided")

    page_size, err = parse_choice_field("page_size", "a4", {"a4", "letter"}, label="page_size")
    if err:
        return err
    margin, err = parse_float_field("margin", 20, label="margin", min_value=0)
    if err:
        return err

    inputs = [save_upload(img, suffix=Path(img.filename).suffix or ".jpg")
              for img in files]
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_topdf, {
        "inputs": inputs, "output": out,
        "page_size": page_size,
        "margin": margin,
    })
    _rm(*inputs)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "output.pdf", "application/pdf", msg)


# ── protect ───────────────────────────────────────────────────────────────────

@app.route("/api/protect", methods=["POST"])
def api_protect():
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    user_pw = request.form.get("user_password", "").strip() or None
    owner_pw = request.form.get("owner_password", "").strip() or None

    if not user_pw and not owner_pw:
        return bad_request("Provide at least one password")

    encryption, err = parse_choice_field(
        "encryption", "AES-256", {"AES-128", "AES-256"}, label="encryption"
    )
    if err:
        return err

    permissions = request.form.get("permissions", "").strip() or None

    inp = save_upload(f)
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_protect, {
        "input": inp, "output": out,
        "user_password": user_pw,
        "owner_password": owner_pw,
        "encryption": encryption,
        "permissions": permissions,
    })
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "protected.pdf", "application/pdf", msg)


# ── unlock ────────────────────────────────────────────────────────────────────

@app.route("/api/unlock", methods=["POST"])
def api_unlock():
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    password = request.form.get("password", "").strip()
    if not password:
        return bad_request("Enter the PDF password")

    inp = save_upload(f)
    out = tmp_path()

    ok, msg = run_cmd(pdftool.cmd_unlock, {
        "input": inp, "output": out,
        "password": password,
    })
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "unlocked.pdf", "application/pdf", msg)


# ── extract ───────────────────────────────────────────────────────────────────

@app.route("/api/extract", methods=["POST"])
def api_extract():
    f = request.files.get("file")
    if not f:
        return bad_request("No PDF file provided")

    inp = save_upload(f)
    out = tmp_path(suffix=".txt")

    ok, msg = run_cmd(pdftool.cmd_extract, {
        "input": inp, "output": out,
        "pages": request.form.get("pages", "").strip() or None,
    })
    _rm(inp)

    if not ok:
        _rm(out)
        return jsonify(error=msg), 400
    return file_resp(out, "extracted.txt", "text/plain; charset=utf-8", msg)


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3003))
    print(f"\n  pdftool web UI  →  http://localhost:{port}\n")
    app.run(host="127.0.0.1", port=port,
            debug=os.environ.get("FLASK_DEBUG", "0") == "1")
