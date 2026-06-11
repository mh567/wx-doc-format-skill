from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from format_document import existing_heading_number, heading_level_from_text, strip_heading_marker


def test_chinese_number_heading_is_level_one():
    assert heading_level_from_text("一、核心定位") == 1
    assert heading_level_from_text("二、总体设计思路") == 1


def test_numbered_decimal_heading_levels_still_work():
    assert heading_level_from_text("1 总则") == 1
    assert heading_level_from_text("1.1 总体架构") == 2
    assert heading_level_from_text("11.1.1 全局视角") == 3


def test_chinese_number_marker_is_stripped():
    assert strip_heading_marker("一、核心定位") == "核心定位"
    assert strip_heading_marker("十五、最后收尾备忘") == "最后收尾备忘"
    assert existing_heading_number("一、核心定位")
