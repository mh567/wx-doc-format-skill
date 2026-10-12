"""Verify DOCX conversion with the executable from the delivered Windows ZIP."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import zipfile
from docx import Document
from build_windows_desktop import assert_x64


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--delivery', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    delivery, output = args.delivery.resolve(), args.output.resolve()
    record = json.loads((delivery / 'acceptance.json').read_text())
    archive = delivery / record['archive']
    import hashlib
    if hashlib.sha256(archive.read_bytes()).hexdigest() != record['sha256']:
        raise RuntimeError('Desktop archive SHA256 mismatch')
    output.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle:
        if any(Path(name).is_absolute() or '..' in Path(name).parts for name in bundle.namelist()):
            raise RuntimeError('Unsafe archive member')
        bundle.extractall(output / 'app')
    root = output / 'app' / 'MagicFormat'
    executable = root / 'native_skill' / 'runtime' / 'wx-doc-format.exe'
    assert_x64(executable)
    assert_x64(root / 'MagicFormat.exe')
    observed = subprocess.check_output([str(executable), '--version'], text=True).strip()
    assert observed == record['version'], observed
    source = output / 'source.docx'
    document = Document()
    document.add_heading('Windows DOCX 验收', 0)
    document.add_heading('1 系统设计', 1)
    document.add_paragraph('转换验收包含题注去重、章节编号和表格编号列。')
    for _ in range(2):
        document.add_paragraph('表7 设备接入核对表')
    table = document.add_table(rows=4, cols=2)
    table.cell(0, 0).text = '序号'
    table.cell(0, 1).text = '设备'
    for index in range(1, 4):
        table.cell(index, 0).text = str(index)
        table.cell(index, 1).text = f'设备{index}'
    document.save(source)
    converted, report = output / 'converted.docx', output / 'report.json'
    subprocess.run([str(executable), '--input', str(source), '--output', str(converted),
                    '--template', str(root / 'native_skill' / 'assets' / 'wx_template.docx'),
                    '--report', str(report)], check=True)
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['status'] in ('completed', 'completed_with_warnings'), result['status']
    captions = [p.text for p in Document(converted).paragraphs if '设备接入核对表' in p.text]
    assert captions == ['表 1-1 设备接入核对表'], captions
    with zipfile.ZipFile(converted) as bundle:
        assert bundle.testzip() is None
    record.update(docx_conversion='passed', caption_deduplication='passed', caption=captions[0])
    (output / 'docx-acceptance.json').write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
