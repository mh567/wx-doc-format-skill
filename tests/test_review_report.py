"""HTML review report escaping tests.

Report fields can carry document text, and the HTML writer must never let that
text become markup.  Static template tags stay literal; every dynamic value is
escaped once.
"""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from review_report import write_review_report_html


def _result() -> dict:
    return {
        "score": 90,
        "grade": "A",
        "issues": [
            {
                "level": '"><script>alert(1)</script>',
                "title": "<svg/onload=alert(1)>",
                "location": "<img src=x onerror=alert(1)>",
                "suggestion": "<script>alert('suggestion')</script>",
            }
        ],
        "compliant_items": ["<b>合规</b>"],
        "risk_warnings": ["<img src=x onerror=alert(1)>"],
    }


def test_html_report_escapes_every_dynamic_value(tmp_path: Path) -> None:
    path = tmp_path / "report.html"
    write_review_report_html(_result(), path)

    html = path.read_text(encoding="utf-8")

    assert "<svg/onload=alert(1)>" not in html
    assert "&lt;svg/onload=alert(1)&gt;" in html
    assert "<script>alert('suggestion')</script>" not in html
    assert "&lt;script&gt;alert(&#x27;suggestion&#x27;)&lt;/script&gt;" in html
    assert "<img src=x onerror=alert(1)>" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "<b>合规</b>" not in html
    assert "&lt;b&gt;合规&lt;/b&gt;" in html


def test_html_report_keeps_only_known_severity_classes(tmp_path: Path) -> None:
    path = tmp_path / "report.html"
    write_review_report_html(_result(), path)

    html = path.read_text(encoding="utf-8")

    assert 'class="issue-card issue-critical"' not in html
    assert 'class="issue-card issue-low"' in html


def test_html_report_stringifies_non_string_score_and_grade(tmp_path: Path) -> None:
    result = _result()
    result["score"] = 75
    result["grade"] = 42
    path = tmp_path / "report.html"

    write_review_report_html(result, path)

    html = path.read_text(encoding="utf-8")
    assert '<div class="score-value">75</div>' in html
    assert "等级：42" in html
