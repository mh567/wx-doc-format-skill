"""Acceptance smoke for the interpreted offline package."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parents[1]
MACHO_MAGIC = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe"}

SMOKE_MARKDOWN = """# 1 概述

本文用于验证离线运行时的真实转换。

## 1.1 范围

- 第一条要求
- 第二条要求

| 序号 | 名称 | 说明 |
| --- | --- | --- |
| 1 | 示例 | 说明文本 |
"""


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: int = 600) -> tuple[int, str, str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, env=merged, timeout=timeout)
    return result.returncode, result.stdout, result.stderr


class SmokeFailure(RuntimeError):
    pass


def check(records: list[dict], name: str, passed: bool, detail: str = "") -> None:
    records.append({"name": name, "passed": bool(passed), "detail": detail.strip()[:2000]})
    if not passed:
        raise SmokeFailure(name)


def macho_outside_vendor(package: Path) -> list[str]:
    found = []
    for path in package.rglob("*"):
        if not path.is_file():
            continue
        if "vendor" in path.parts:
            continue
        try:
            with path.open("rb") as stream:
                if stream.read(4) in MACHO_MAGIC:
                    found.append(path.relative_to(package).as_posix())
        except OSError:
            continue
    return found


def restricted_tools(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("sh", "od", "awk", "sed", "grep", "tr", "uname", "dirname", "basename", "cat"):
        resolved = shutil.which(name)
        if resolved:
            (directory / name).symlink_to(resolved)
    fake = directory / "python3"
    fake.write_text("#!/bin/sh\nexit 1\n", encoding="ascii")
    fake.chmod(0o755)
    return directory


def checksum_command() -> str:
    if shutil.which("sha256sum"):
        return "sha256sum -c SHA256SUMS.txt"
    return "shasum -a 256 -c SHA256SUMS.txt"


def acceptance_checks(package: Path, evidence: Path, records: list[dict], scratch: Path) -> list[str]:
    run_script = package / "scripts" / "run.sh"
    version = (package / "VERSION").read_text(encoding="utf-8").strip()

    code, out, err = run(["sh", "-c", checksum_command()], cwd=package)
    check(records, "package-checksums", code == 0, out + err)

    code, out, err = run(["sh", str(run_script), "--version"], cwd=package)
    check(records, "runtime-version", code == 0 and out.strip() == version, out + err)

    macho = macho_outside_vendor(package)
    check(records, "no-first-party-macho", not macho, ", ".join(macho))

    work = evidence / "work"
    work.mkdir(exist_ok=True)
    markdown = work / "smoke.md"
    markdown.write_text(SMOKE_MARKDOWN, encoding="utf-8")
    output = work / "smoke.docx"
    report = work / "report.json"
    template = package / "assets" / "wx_template.docx"
    code, out, err = run(
        ["sh", str(run_script), "--input", str(markdown), "--output", str(output), "--template", str(template), "--report", str(report)],
        cwd=package,
    )
    check(records, "convert", code == 0 and output.is_file(), out + err)
    if report.is_file():
        data = json.loads(report.read_text(encoding="utf-8"))
        check(records, "convert-status", data.get("status") in {"completed", "completed_with_warnings"}, str(data.get("status")))

    review = work / "review.md"
    code, out, err = run(
        ["sh", str(run_script), "--review", "--input", str(output), "--template", str(template), "--report-md", str(review)],
        cwd=package,
    )
    check(records, "review", code == 0 and review.is_file() and review.stat().st_size > 0, out + err)

    install_root = scratch / "install"
    install_root.mkdir()
    destination = install_root / "skill"
    code, out, err = run(["sh", str(package / "scripts" / "install.sh"), str(destination)], cwd=package)
    check(records, "offline-install", code == 0 and (destination / "scripts" / "run.sh").is_file(), out + err)
    code, out, err = run(["sh", str(destination / "scripts" / "run.sh"), "--version"], cwd=destination)
    check(records, "installed-version", code == 0 and out.strip() == version, out + err)

    nonascii = scratch / "安装路径" / "skill"
    code, out, err = run(["sh", str(package / "scripts" / "install.sh"), str(nonascii)], cwd=package)
    check(records, "non-ascii-install", code == 0 and (nonascii / "scripts" / "run.sh").is_file(), out + err)
    code, out, err = run(["sh", str(nonascii / "scripts" / "run.sh"), "--version"], cwd=nonascii)
    check(records, "non-ascii-run", code == 0 and out.strip() == version, out + err)

    tools = restricted_tools(scratch / "tools")
    bundled = package / "runtime" / "python" / "bin" / "python3"
    if bundled.is_file():
        code, out, err = run(
            ["sh", str(run_script), "--version"],
            cwd=package,
            env={"PATH": str(tools), "WXDF_PYTHON": ""},
        )
        check(records, "bundled-python", code == 0 and out.strip() == version, out + err)
    else:
        code, out, err = run(
            ["sh", str(run_script), "--version"],
            cwd=package,
            env={"PATH": str(tools), "WXDF_PYTHON": "/usr/bin/python3"},
        )
        check(records, "python-gate", code == 3 and "WXDF-E-PYTHON-UNAVAILABLE" in (out + err), out + err)

    code, out, err = run(["sh", "-c", checksum_command()], cwd=package)
    check(records, "package-stable", code == 0, out + err)
    return macho


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run acceptance checks on an interpreted package.")
    parser.add_argument("package", type=Path)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, default=None)
    args = parser.parse_args(argv)

    package = args.package.resolve()
    evidence = args.evidence_dir.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    version = (package / "VERSION").read_text(encoding="utf-8").strip()
    records: list[dict] = []
    macho: list[str] = []
    scratch = Path(tempfile.mkdtemp(prefix="wxdf-smoke-"))
    try:
        macho = acceptance_checks(package, evidence, records, scratch)
    except SmokeFailure:
        pass
    except Exception as exc:
        records.append({"name": "harness-error", "passed": False, "detail": f"{type(exc).__name__}: {exc}"[:2000]})
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    acceptance = {
        "schema_version": 1,
        "version": version,
        "package": package.name,
        "runtime_kind": "interpreted",
        "checks": records,
        "first_party_macho": macho,
        "passed": all(record["passed"] for record in records),
    }
    (evidence / "acceptance.json").write_text(json.dumps(acceptance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failed = [record["name"] for record in records if not record["passed"]]
    print(json.dumps({"passed": acceptance["passed"], "checks": len(records), "failed": failed}, ensure_ascii=False))
    return 0 if acceptance["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
