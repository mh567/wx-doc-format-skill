# WX Doc Format Skill

`wx-doc-format` is a portable skill for converting Markdown and DOCX files into WX-style formatted Word documents.

## Features

- Converts `.md`, `.markdown`, and `.docx` inputs to `.docx`.
- Uses `python-docx` and `lxml` as the best-effect conversion path.
- Provides a macOS bootstrap script that creates an isolated `.venv`, installs binary wheels, removes quarantine attributes, signs native modules ad hoc, and verifies imports.
- Includes an internal DOCX emergency fallback for dependency failures.
- Uses built-in WX document formatting rules.
- Normalizes messy Word headings, body text, lists, notes, captions, and tables.
- Creates Word automatic numbering for headings and lists so visible numbers are preserved without writing numbers into paragraph text.
- Applies WX heading hanging indents, body first-line indent, list indents, table body style, and fixed table row height.
- Generates JSON and Markdown audit reports.
- Detects conversion risks such as images, drawings, fields, headers, footers, comments, tracked changes, and possible table clipping.

## Requirements

Best-effect conversion requires Python 3, `python-docx`, and `lxml`.

On macOS or restricted Python environments, run the bundled bootstrap first:

```bash
./scripts/bootstrap_macos_lxml.sh
```

The main script automatically re-executes with `./.venv/bin/python` when the current Python cannot import `python-docx` or `lxml`.

You can also install dependencies manually:

```bash
python -m pip install -r requirements.txt
```

If `lxml` import, signature, or dynamic library errors occur on macOS, rerun the bootstrap script to repair the isolated environment. For DOCX inputs, the main script can still use an internal emergency fallback when the isolated environment is unavailable.

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
