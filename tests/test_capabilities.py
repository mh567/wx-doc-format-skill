"""The capability registry and the mode that selects capabilities."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from wxdf.capabilities import (
    AUTO_TRIGGER_THRESHOLD,
    CAPABILITY_REGISTRY,
    CONVERSION_PHASES,
    CapabilityConfig,
    REVIEW_CAPABILITIES,
    REVIEW_PHASES,
    _resolve_capability,
    _resolve_legacy_phase,
    compute_suspicion_score,
    normalize_mode,
    register_capability,
    should_enhance,
)


def test_the_registry_declares_every_capability_with_its_phase_letter() -> None:
    assert sorted(CAPABILITY_REGISTRY) == [
        "caption_gen",
        "citation_order_review",
        "document_review",
        "glossary_review",
        "list_detect",
        "list_prose_review",
        "toc_region_review",
    ]
    for capability, phase in (
        ("toc_region_review", "S"),
        ("list_detect", "A"),
        ("caption_gen", "B"),
        ("document_review", "C"),
        ("citation_order_review", "D"),
        ("glossary_review", "E"),
        ("list_prose_review", "F"),
    ):
        assert _resolve_legacy_phase(capability) == phase
        assert _resolve_capability(phase) == capability
        assert _resolve_capability(capability) == capability


def test_the_review_capabilities_are_disjoint_from_the_conversion_ones() -> None:
    assert set(REVIEW_CAPABILITIES) == {"citation_order_review", "glossary_review", "list_prose_review"}
    assert REVIEW_PHASES == {"D", "E", "F"}
    assert REVIEW_PHASES.isdisjoint(CONVERSION_PHASES)


def test_an_unknown_name_resolves_to_nothing_and_passes_through_unchanged() -> None:
    assert _resolve_capability("unregistered") is None
    assert _resolve_legacy_phase("unregistered") == "unregistered"


def test_registering_a_taken_name_is_refused() -> None:
    with pytest.raises(ValueError, match="Duplicate capability"):
        register_capability(CapabilityConfig("list_detect"))


def test_capability_names_normalize_to_their_compact_mode_letters() -> None:
    assert normalize_mode("list_detect") == "a"
    assert normalize_mode("toc_region_review") == "s"
    assert normalize_mode("caption_gen") == "b"
    assert normalize_mode("document_review") == "c"
    assert normalize_mode("citation_order_review") == "d"
    assert normalize_mode("glossary_review") == "e"
    assert normalize_mode("list_prose_review") == "f"
    # ``all`` names every registered capability; each surface then takes the
    # phases it owns, so one word serves the conversion and the review alike.
    assert normalize_mode("all") == "sabcdef"


def test_unknown_modes_are_returned_as_written() -> None:
    assert normalize_mode("off") == "off"
    assert normalize_mode("sabc") == "sabc"
    assert normalize_mode("nonsense") == "nonsense"


def test_off_selects_nothing_and_compact_modes_select_their_letters() -> None:
    for phase in ("S", "A", "B", "C"):
        assert should_enhance({}, phase, "off") is False
    assert should_enhance({}, "S", "s") is True
    assert should_enhance({}, "A", "s") is False
    assert should_enhance({}, "A", "sabc") is True
    assert should_enhance({}, "C", "sabc") is True
    assert should_enhance({}, "C", "abc") is False
    assert should_enhance({}, "S", "abc") is False


def test_a_capability_name_selects_the_same_phase_its_letter_selects() -> None:
    assert should_enhance({}, "list_detect", "a") is True
    assert should_enhance({}, "caption_gen", "b") is True
    assert should_enhance({}, "document_review", "c") is True


def test_the_force_variants_behave_as_their_plain_counterparts() -> None:
    for plain, forced in (("a", "force-a"), ("ab", "force-ab"), ("sabc", "force-sabc")):
        for phase in ("S", "A", "B", "C"):
            assert should_enhance({}, phase, plain) == should_enhance({}, phase, forced)


def test_auto_selects_the_post_audit_review_only_when_the_packet_has_targets() -> None:
    assert should_enhance({"review_packet": {"targets": [{"block_id": "b1"}]}}, "C", "auto") is True
    assert should_enhance({"review_packet": {"targets": []}}, "C", "auto") is False
    assert should_enhance({}, "C", "auto") is False


def test_the_suspicion_score_is_bounded_and_rises_with_ambiguity() -> None:
    clean = {
        "source_document_model_summary": {"block_count": 20, "block_counts": {"body": 10, "heading": 2, "list_item": 1}},
        "parse_report": {},
    }
    messy = {
        "source_document_model_summary": {"block_count": 20, "block_counts": {"body": 10, "heading": 2, "list_item": 1}},
        "parse_report": {
            "suspect_visual_headings": 10,
            "ambiguous_short_paragraphs": 10,
            "inferred_headings": 2,
            "inferred_lists": 1,
            "unstyled_paragraphs": 10,
            "ambiguous_unordered_paragraphs": 4,
        },
    }

    assert compute_suspicion_score(clean) == 0.0
    # Every weighted dimension saturates except the table and image densities.
    assert compute_suspicion_score(messy) == pytest.approx(0.85)
    assert 0.0 <= compute_suspicion_score({"parse_report": {}}) <= 1.0


def test_the_suspicion_score_reads_list_shaped_signals_as_their_length() -> None:
    report = {
        "source_document_model_summary": {"block_count": 10, "block_counts": {"body": 4}},
        "parse_report": {"suspect_visual_headings": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d"}]},
    }

    assert compute_suspicion_score(report) == pytest.approx(0.25)


def test_auto_gates_the_classifier_on_suspicion_above_the_threshold() -> None:
    summary = {"block_count": 20, "block_counts": {"body": 10, "heading": 2, "list_item": 1}}
    below = {"source_document_model_summary": summary, "parse_report": {"suspect_visual_headings": []}}
    above = {"source_document_model_summary": summary, "parse_report": {"suspect_visual_headings": 10}}

    assert should_enhance({**below, "template_profile": {"x": 1}}, "A", "auto") is False
    assert should_enhance({**above, "template_profile": {"x": 1}}, "A", "auto") is True
    assert AUTO_TRIGGER_THRESHOLD == 0.15


def test_auto_skips_the_classifier_without_a_template_or_on_a_short_document() -> None:
    signals = {"suspect_visual_headings": 10}
    summary = {"block_count": 20, "block_counts": {"body": 10, "heading": 2, "list_item": 1}}

    assert should_enhance({"source_document_model_summary": summary, "parse_report": signals}, "A", "auto") is False
    assert should_enhance(
        {"source_document_model_summary": {"block_count": 2}, "parse_report": signals, "template_profile": {"x": 1}},
        "A",
        "auto",
    ) is False


def test_auto_gates_the_toc_review_on_an_ambiguous_source_toc() -> None:
    assert should_enhance({"source_toc": {"status": "ambiguous"}}, "S", "auto") is True
    assert should_enhance({"source_toc": {"status": "absent"}}, "S", "auto") is False
    assert should_enhance({}, "S", "auto") is False


def test_auto_gates_caption_generation_on_the_first_phases_modification_rate() -> None:
    assert should_enhance({"llm_enhancer": {"phase_a_applied_rate": 0.2}}, "B", "auto") is True
    assert should_enhance({"llm_enhancer": {"phase_a_applied_rate": 0.01}}, "B", "auto") is False
    assert should_enhance({}, "B", "auto") is False
