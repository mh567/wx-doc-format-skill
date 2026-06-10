# WX Doc Format Skill

`wx-doc-format` is a portable skill for converting Markdown and DOCX files into WX-style formatted Word documents.

## Features

- Converts `.md`, `.markdown`, and `.docx` inputs to `.docx`.
- Uses built-in WX document formatting rules.
- Normalizes messy Word headings, body text, lists, notes, captions, and tables.
- Materializes Word automatic heading and list numbering so visible numbers are preserved after conversion.
- Applies WX heading hanging indents, body first-line indent, list indents, table body style, and fixed table row height.
- Generates JSON and Markdown audit reports.
- Detects conversion risks such as images, drawings, fields, headers, footers, comments, tracked changes, and possible table clipping.

## Requirements

```bash
python -m pip install -r requirements.txt
```

If `lxml` import, signature, or dynamic library errors occur on macOS, repair dependencies with:

```bash
python -m pip install --upgrade --force-reinstall --no-cache-dir python-docx lxml
```

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

See `SKILL.md` for the full skill instructions.
