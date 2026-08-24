from __future__ import annotations

import argparse
import json
import platform
import subprocess
import tempfile
import zipfile
from pathlib import Path


def runtime_executable(package_dir: Path) -> Path:
    name = "wx-doc-format.exe" if platform.system().lower() == "windows" else "wx-doc-format"
    return package_dir / "runtime" / name


def report_keys(value) -> set[str]:
    keys = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key).lower())
            keys.update(report_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(report_keys(child))
    return keys


def smoke(package_dir: Path) -> None:
    executable = runtime_executable(package_dir)
    version = (package_dir / "VERSION").read_text(encoding="utf-8").strip()
    version_result = subprocess.run([str(executable), "--version"], text=True, capture_output=True, check=True)
    if version_result.stdout.strip() != version:
        raise RuntimeError("Runtime version does not match package VERSION")
    with tempfile.TemporaryDirectory(prefix="wx-doc-format-smoke-") as temporary:
        temp_dir = Path(temporary)
        source = temp_dir / "source.md"
        output = temp_dir / "output.docx"
        report = temp_dir / "report.json"
        source.write_text("# 冒烟测试\n\n这是离线发布包转换测试。\n", encoding="utf-8")
        subprocess.run(
            [
                str(executable),
                "--input", str(source),
                "--output", str(output),
                "--template", str(package_dir / "assets" / "wx_template.docx"),
                "--report", str(report),
            ],
            check=True,
        )
        if not output.is_file() or not zipfile.is_zipfile(output):
            raise RuntimeError("Runtime smoke conversion did not create a valid DOCX")
        if not report.is_file():
            raise RuntimeError("Runtime smoke conversion did not create a report")
        public_report = json.loads(report.read_text(encoding="utf-8"))
        keys = report_keys(public_report)
        exact_forbidden = {"review_packet", "unknown_pattern_packet", "blocks"}
        forbidden_fragments = (
            "candidate", "confidence", "decision", "evidence", "frame",
            "score", "stack", "traceback",
        )
        leaked = {
            key for key in keys
            if key in exact_forbidden
            or key == "ast"
            or key.startswith("ast_")
            or key.endswith("_ast")
            or any(fragment in key for fragment in forbidden_fragments)
        }
        if leaked:
            raise RuntimeError(f"Release report exposes sensitive fields: {sorted(leaked)}")
        if "unexpected_styles" not in keys:
            raise RuntimeError("Release report does not expose unexpected_styles")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke test an assembled offline package.")
    parser.add_argument("package_dir", type=Path)
    args = parser.parse_args()
    package_dir = args.package_dir.resolve()
    smoke(package_dir)


if __name__ == "__main__":
    main()
