# WX Doc Format Skill

`wx-doc-format` is a portable skill for converting Markdown and DOCX files into WX-style formatted Word documents.

## Features

- Converts `.md`, `.markdown`, and `.docx` inputs to `.docx`.
- DOCX inputs can use a stdlib-only OOXML path without `python-docx` or `lxml`.
- Uses built-in WX document formatting rules.
- Normalizes messy Word headings, body text, lists, notes, captions, and tables.
- Creates Word automatic numbering for headings and lists so visible numbers are preserved without writing numbers into paragraph text.
- Applies WX heading hanging indents, body first-line indent, list indents, table body style, and fixed table row height.
- Generates JSON and Markdown audit reports.
- Detects conversion risks such as images, drawings, fields, headers, footers, comments, tracked changes, and possible table clipping.

## Requirements

DOCX input only needs Python 3. Markdown input and full document rebuild mode require `python-docx` and `lxml`:

```bash
python -m pip install -r requirements.txt
```

If `lxml` import, signature, or dynamic library errors occur on macOS, use the stdlib OOXML path for DOCX instead of reinstalling repeatedly.

## Usage

```bash
python scripts/format_document.py \
  --input "/path/to/input.docx" \
  --output "/path/to/output.docx" \
  --report "/path/to/report.json" \
  --report-md "/path/to/report.md"
```

For automated checks, fail when the source contains high-risk objects:

```bash
python scripts/format_document.py \
  --input "/path/to/input.docx" \
  --output "/path/to/output.docx" \
  --report "/path/to/report.json" \
  --report-md "/path/to/report.md" \
  --fail-on-risk
```

Run the stdlib-only DOCX path directly:

```bash
python scripts/format_docx_ooxml.py \
  --input "/path/to/input.docx" \
  --output "/path/to/output.docx" \
  --report "/path/to/report.json" \
  --report-md "/path/to/report.md"
```

See `SKILL.md` for the full skill instructions.
