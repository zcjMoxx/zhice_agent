from __future__ import annotations

import pytest

from agent.applications.travel.schemas import (
    EvidenceItemV1,
    TravelPlanV1,
    TravelRequestV1,
    TravelValidationError,
)
from tests.unit_test.travel.fixtures import plan_payload, request_payload


def test_travel_request_validates_required_dates_budget_and_people():
    request = TravelRequestV1.from_dict(request_payload())

    assert request.duration_days == 2
    assert request.traveller_count == 2
    assert request.planning_mode == "quick"

    for field, value in (
        ("destinations", []),
        ("duration_days", 3),
        ("travellers", [{"type": "学生", "count": 0}]),
        ("budget_total_cny", 10),
    ):
        invalid = request_payload()
        invalid[field] = value
        with pytest.raises(TravelValidationError):
            TravelRequestV1.from_dict(invalid)


def test_request_allows_explicit_budget_tier_path_without_fake_exact_total():
    request = TravelRequestV1.from_dict(request_payload(budget=None))

    assert request.budget_total_cny is None


def test_evidence_validates_url_freshness_hash_and_truncates_excerpt():
    raw = plan_payload()["evidence"][0]
    raw["excerpt"] = "x" * 2000
    item = EvidenceItemV1.from_dict(raw)

    assert len(item.excerpt) == 1200
    assert len(item.content_hash) == 64

    invalid_url = dict(raw, source_url="javascript:alert(1)")
    with pytest.raises(TravelValidationError):
        EvidenceItemV1.from_dict(invalid_url)
    invalid_freshness = dict(raw, source_type="model_estimate", freshness="live", source_url="")
    with pytest.raises(TravelValidationError):
        EvidenceItemV1.from_dict(invalid_freshness)


def test_deduplicates_tracking_urls_and_remaps_references():
    raw = plan_payload()
    duplicate = dict(raw["evidence"][0])
    duplicate["evidence_id"] = "ev-map-copy"
    duplicate["source_url"] = "https://ditu.amap.com/route?utm_source=copy"
    raw["evidence"].append(duplicate)
    raw["days"][0]["activities"][0]["evidence_ids"] = ["ev-map-copy"]

    plan = TravelPlanV1.from_dict(raw)

    assert len(plan.data["evidence"]) == 3
    assert plan.data["days"][0]["activities"][0]["evidence_ids"] == ["ev-map"]


def test_plan_rejects_overlap_unknown_evidence_illegal_sources_budget_and_size():
    cases = []
    overlap = plan_payload()
    overlap["days"][1]["activities"][1]["start"] = "11:00"
    cases.append(overlap)
    unknown = plan_payload()
    unknown["days"][0]["activities"][0]["evidence_ids"] = ["missing"]
    cases.append(unknown)
    illegal = plan_payload()
    illegal["evidence"][0]["source_url"] = "file:///secret"
    cases.append(illegal)
    over_budget = plan_payload()
    over_budget["budget"]["lower"] = 6000
    over_budget["budget"]["expected"] = 6500
    over_budget["budget"]["upper"] = 7000
    cases.append(over_budget)

    for item in cases:
        with pytest.raises(TravelValidationError) as captured:
            TravelPlanV1.from_dict(item)
        assert captured.value.code == "TRAVEL_PLAN_SCHEMA_INVALID"

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(plan_payload(), max_plan_bytes=100)
    assert captured.value.code == "TRAVEL_PLAN_TOO_LARGE"


def test_plan_keeps_freshness_labels_distinct():
    plan = TravelPlanV1.from_dict(plan_payload())

    by_provider = {item["provider"]: item["freshness"] for item in plan.data["evidence"]}
    assert by_provider == {
        "高德地图": "live",
        "Open-Meteo": "live",
        "xiaohongshu-readonly": "snapshot",
    }


def test_plan_rejects_an_activity_without_drawable_coordinates():
    raw = plan_payload()
    raw["days"][0]["activities"][0]["location"] = None

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.code == "TRAVEL_PLAN_SCHEMA_INVALID"
    assert captured.value.field == "plan.days[0].activities[0].location"
