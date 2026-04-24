# pdftool

A local PDF toolkit with both a CLI and a browser UI. Merge, split, rotate, compress, watermark, number pages, reorder, and convert between PDF and images.

No cloud services, no uploads. Everything runs locally.

## Requirements

- Python 3.9+
- macOS / Linux / Windows

## Setup

```bash
git clone <repo-url> && cd idolovepdf
bash setup.sh
```

This creates a `lovepdf` virtual environment and installs dependencies from `requirements.txt` (`pymupdf`, `Pillow`, and `Flask`).

To activate the environment (required before each session):

```bash
source lovepdf/bin/activate
```

Then run:

```bash
python pdftool.py --help
```

## Web UI

Run the local web server:

```bash
python server.py
```

Then open:

```text
http://localhost:5000
```

The web UI exposes the same core operations as the CLI: merge, split, rotate, page numbers, reorder, compress, watermark, PDF to images, and images to PDF.

## Commands

### merge

Concatenate multiple PDFs into one.

```bash
python pdftool.py merge file1.pdf file2.pdf file3.pdf -o combined.pdf
```

### split

Extract specific pages into a new PDF. Pages are 1-indexed. Supports ranges and individual pages.

```bash
python pdftool.py split input.pdf -p 1-3,5,8-10 -o output.pdf
```

### rotate

Rotate pages by 90, 180, or 270 degrees. Omit `-p` to rotate all pages.

```bash
python pdftool.py rotate input.pdf -d 90 -o rotated.pdf
python pdftool.py rotate input.pdf -p 1,3,5 -d 180 -o rotated.pdf
```

### pagenumbers

Add page number labels as a text overlay.

```bash
# Default: bottom-center, starting at 1
python pdftool.py pagenumbers input.pdf -o numbered.pdf

# Custom format, skip cover page
python pdftool.py pagenumbers input.pdf \
  --position top-right \
  --format "Page {n} of {total}" \
  --skip 1 \
  --font-size 10 \
  --margin 36 \
  -o numbered.pdf
```

**Options:** `--position` (bottom-left, bottom-center, bottom-right, top-left, top-center, top-right), `--font-size`, `--start`, `--format`, `--margin`, `--skip`

### reorder

Reorder pages by specifying the new sequence.

```bash
python pdftool.py reorder input.pdf -p 3,1,2,5,4 -o reordered.pdf
```

### compress

Reduce file size by re-encoding images, removing duplicates, stripping metadata, and garbage-collecting unused objects.

```bash
python pdftool.py compress input.pdf -o smaller.pdf --quality medium
```

**Quality levels:** `low` (JPEG 40), `medium` (JPEG 65, default), `high` (JPEG 85)

### watermark

Add a text or image watermark.

```bash
# Text watermark — centered, diagonal, semi-transparent
python pdftool.py watermark input.pdf \
  --text "CONFIDENTIAL" \
  --font-size 60 \
  --opacity 0.3 \
  --angle 45 \
  --color "#FF0000" \
  -o watermarked.pdf

# Image watermark — centered, scaled
python pdftool.py watermark input.pdf \
  --image logo.png \
  --opacity 0.3 \
  --scale 0.5 \
  -o watermarked.pdf

# Only watermark specific pages
python pdftool.py watermark input.pdf --text "DRAFT" --pages 2-5 -o out.pdf
```

### toimage

Rasterize PDF pages to image files.

```bash
python pdftool.py toimage input.pdf -o output_dir/ --format png --dpi 150
python pdftool.py toimage input.pdf -o output_dir/ --format jpg --dpi 300 --pages 1,3,5
```

Output files are named `page_001.png`, `page_002.png`, etc.

### topdf

Combine images into a PDF. Each image becomes one page, fitted and centered within margins.

```bash
python pdftool.py topdf scan1.jpg scan2.png -o scans.pdf --page-size A4 --margin 20
python pdftool.py topdf *.png -o slides.pdf --page-size letter
```

**Page sizes:** `A4` (default), `Letter`

## Notes

- All page numbers in arguments are **1-indexed** (first page is 1, not 0).
- Every subcommand has detailed help: `python pdftool.py <command> --help`
- Operations preserve PDF metadata unless explicitly stripping it (`compress`).
- Pillow is only loaded for commands that need it (`compress`, `watermark --image`, `topdf`), so pure-PDF operations start fast.
