from pathlib import Path
import sys
from docx import Document
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from wxdf.kernel import ConversionKernel, ConversionRequest

@pytest.fixture
def converted(tmp_path):
    source = tmp_path / 'source.md'
    source.write_text('''# 演示标题

## 目录

# 3 项目概况

## 日志采集要求

1. 1）应具备设备日志接入能力；
2. 2）应具备字段检查能力；
3. 2）应具备连接状态记录能力；

## 运行交接要求

a) 交接设备清单；\\
b) 核对接口状态；\\
c) 登记未完成问题。

## 运行记录核对

注2  **演示样例**用于核对。

# 附录A **运行交接检查清单**

### A.8 接入状态核对

表A.1 运行交接记录

| 核对项目 | 结果 |
| --- | --- |
| 设备接入 | 待核对 |

#### A.3.2 留存要求

正文保留 /api/v1/events 和 192.0.2.0/24。
''', encoding='utf-8')
    output = tmp_path / 'result.docx'
    result = ConversionKernel().convert(ConversionRequest(input_path=source, output_path=output, template_path=ROOT/'assets/wx_template.docx'))
    assert result.exit_code == 0, result.diagnostics
    return Document(output)

def test_toc_title_does_not_become_body_heading(converted):
    assert not any(p.text == '目录' and p.style.name.startswith('Heading') for p in converted.paragraphs)

def test_redundant_manual_list_markers_are_consumed(converted):
    paragraphs = [p for p in converted.paragraphs if '应具备' in p.text]
    assert len(paragraphs) == 3
    assert all(p.text.startswith('应具备') for p in paragraphs)

def test_hardbreak_letter_list_has_three_equal_style_items(converted):
    items = [p for p in converted.paragraphs if any(t in p.text for t in ('交接设备清单', '核对接口状态', '登记未完成问题'))]
    assert [p.text for p in items] == ['交接设备清单；', '核对接口状态；', '登记未完成问题。']
    assert len({p.style.name for p in items}) == 1
    assert all(p._p.pPr.numPr is not None for p in items)
    assert len({str(p._p.pPr.xml) for p in items}) == 1

def test_note_marker_is_consumed_without_losing_marks(converted):
    p = next(p for p in converted.paragraphs if '演示样例' in p.text)
    assert p.text == '演示样例用于核对。'
    assert any(r.bold for r in p.runs if '演示样例' in r.text)

def test_appendix_markers_are_consumed_but_caption_is_preserved(converted):
    paragraphs = converted.paragraphs
    p = next(p for p in paragraphs if p.style.name == '附录标题')
    assert p.text == '运行交接检查清单'
    assert any(r.bold for r in p.runs if '运行交接检查清单' in r.text)
    assert next(p.text for p in paragraphs if p.style.name == '附录一级标题') == '接入状态核对'
    assert next(p.text for p in paragraphs if p.style.name == '附录二级标题') == '留存要求'
    assert any(p.text == '表A.1 运行交接记录' for p in paragraphs)

@pytest.mark.parametrize('text', [
    '# 目录\n\n正文内容\n',
    '# 文档\n\n## 正文\n\n## 目录\n\n正文目录说明\n',
    '# 文档\n\n> 目录\n',
    '# 文档\n\n## 目录\n\n这是正文目录说明。\n',
])
def test_body_toc_labels_are_preserved(tmp_path, text):
    from wxdf.parsers import parse
    from wxdf.semantics.regions import resolve_source_regions
    source = tmp_path / 'source.md'
    source.write_text(text, encoding='utf-8')
    assert resolve_source_regions(source, parse(source))['excluded_source_positions'] == []

@pytest.mark.parametrize('text', [
    '普通正文  \n继续正文',
    'a) **第一条**  \nc) **第三条**',
    'a) 第一条\nb) 第二条',
])
def test_unconfirmed_break_paragraph_is_not_split(tmp_path, text):
    from wxdf.parsers import parse
    source = tmp_path / 'source.md'
    source.write_text(text, encoding='utf-8')
    facts = parse(source)
    assert len(facts['units']) == 1


def test_atomic_marker_consumption_preserves_unconsumable_inline():
    from wxdf.semantics.lists import strip_visible_list_marker
    content = [{'kind': 'link', 'children': [{'kind': 'text', 'text': 'a) '}]}, {'kind': 'text', 'text': '正文'}]
    assert strip_visible_list_marker(content, {'visible_marker': {'marker_text': 'a)'}}) == content


def test_docx_manual_markers_and_hardbreak_items_are_consumed(tmp_path):
    from docx.enum.text import WD_BREAK
    source = tmp_path / 'source.docx'
    document = Document()
    document.add_heading('日志采集要求', level=2)
    for marker, body in [('1）', '日志接入'), ('2）', '字段检查'), ('2）', '连接状态')]:
        p = document.add_paragraph(style='List Number')
        p.add_run(marker + body)
    document.add_heading('运行交接要求', level=2)
    p = document.add_paragraph()
    p.add_run('a) 交接设备清单；').add_break(WD_BREAK.LINE)
    p.add_run('b) 核对接口状态；').add_break(WD_BREAK.LINE)
    p.add_run('c) 登记未完成问题。')
    document.save(source)

    output = tmp_path / 'converted.docx'
    result = ConversionKernel().convert(ConversionRequest(
        input_path=source, output_path=output,
        template_path=ROOT / 'assets/wx_template.docx',
    ))
    assert result.exit_code == 0, result.diagnostics
    converted = Document(output)
    numbered = [p for p in converted.paragraphs if any(word in p.text for word in ('日志接入', '字段检查', '连接状态'))]
    assert [p.text for p in numbered] == ['日志接入', '字段检查', '连接状态']
    items = [p for p in converted.paragraphs if any(word in p.text for word in ('交接设备清单', '核对接口状态', '登记未完成问题'))]
    assert [p.text for p in items] == ['交接设备清单；', '核对接口状态；', '登记未完成问题。']
    assert len({str(p._p.pPr.xml) for p in items}) == 1
