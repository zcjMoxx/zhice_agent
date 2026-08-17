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

    assert len(plan.data["evidence"]) == 4
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


def test_verified_amap_transit_preserves_line_and_stop_details():
    raw = plan_payload()
    segment = raw["days"][0]["route_segments"][0]
    segment["source"] = "amap_transit"
    segment.pop("transit_legs", None)

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)
    assert captured.value.field == "plan.days[0].route_segments[0].transit_legs"

    segment["transit_legs"] = [{
        "mode": "地铁",
        "line_name": "地铁1号线",
        "departure_stop": "沈阳站",
        "arrival_stop": "中街站",
        "via_stops": ["太原街", "青年大街"],
    }]
    segment["walking_distance"] = 0.6
    segment["fare_cny"] = 3

    plan = TravelPlanV1.from_dict(raw)
    normalized = plan.data["days"][0]["route_segments"][0]
    assert normalized["transit_legs"][0]["line_name"] == "地铁1号线"
    assert normalized["transit_legs"][0]["departure_stop"] == "沈阳站"
    assert normalized["walking_distance"] == 0.6


def test_amap_transit_alias_cannot_bypass_line_and_stop_validation():
    raw = plan_payload()
    segment = raw["days"][0]["route_segments"][0]
    segment["source"] = "高德公交规划"
    segment["mode"] = "地铁+步行"
    segment.pop("transit_legs", None)

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.field == "plan.days[0].route_segments[0].transit_legs"


def test_planning_estimate_rejects_external_poi_as_price_evidence():
    raw = plan_payload()
    stay = raw["stay_recommendations"][0]
    stay["price_source_evidence_ids"] = ["ev-map"]

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.field == (
        "plan.stay_recommendations[0].price_source_evidence_ids"
    )


def test_stay_identity_evidence_must_match_hotel_or_address():
    raw = plan_payload()
    raw["stay_recommendations"][0]["evidence_ids"] = ["ev-weather"]

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.field == "plan.stay_recommendations[0].evidence_ids"


def test_overnight_plan_requires_a_concrete_stay_unless_request_explicitly_exempts_it():
    raw = plan_payload()
    raw["stay_recommendations"] = []

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.field == "plan.stay_recommendations"

    raw["request"]["hard_constraints"].append("全程住亲友家，无需住宿")
    normalized = TravelPlanV1.from_dict(raw).data
    assert normalized["stay_recommendations"] == []


def test_multi_area_stays_may_partition_the_confirmed_overnight_dates():
    raw = plan_payload()
    raw["request"]["end_date"] = "2026-10-03"
    raw["request"]["duration_days"] = 3
    raw["days"].append({**raw["days"][1], "date": "2026-10-03"})
    first = raw["stay_recommendations"][0]
    first["check_out"] = "2026-10-02"
    second = {**first, "check_in": "2026-10-02", "check_out": "2026-10-03"}
    raw["stay_recommendations"] = [first, second]

    normalized = TravelPlanV1.from_dict(raw).data

    assert [(item["check_in"], item["check_out"]) for item in normalized["stay_recommendations"]] == [
        ("2026-10-01", "2026-10-02"),
        ("2026-10-02", "2026-10-03"),
    ]


def test_segmented_stays_reject_gaps_or_overlapping_nights():
    raw = plan_payload()
    raw["request"]["end_date"] = "2026-10-04"
    raw["request"]["duration_days"] = 4
    raw["days"].extend(
        [
            {**raw["days"][1], "date": "2026-10-03"},
            {**raw["days"][1], "date": "2026-10-04"},
        ]
    )
    first = raw["stay_recommendations"][0]
    first["check_out"] = "2026-10-02"
    second = {**first, "check_in": "2026-10-03", "check_out": "2026-10-04"}
    raw["stay_recommendations"] = [first, second]

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.field == "plan.stay_recommendations"


def test_intercity_train_times_must_enclose_first_and_last_day_activities():
    raw = plan_payload()
    raw["transport_options"] = [
        {
            "name": "去程估算",
            "mode": "rail",
            "from": "重庆北",
            "to": "大理",
            "service_name": "G1",
            "departure": "2026-10-01T09:00:00+08:00",
            "arrival": "2026-10-01T15:00:00+08:00",
            "duration_minutes": 360,
            "seat": "二等座",
            "price_cny_per_person": 300,
            "price_cny_total": 600,
            "source": "规划估算",
            "summary": "estimated schedule",
            "evidence_ids": [],
        }
    ]

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.field == "plan.transport_options[0].arrival"

    raw["transport_options"][0]["arrival"] = "2026-10-01T13:00:00+08:00"
    raw["transport_options"].append(
        {
            **raw["transport_options"][0],
            "name": "返程估算",
            "from": "大理",
            "to": "重庆北",
            "departure": "2026-10-02T08:00:00+08:00",
            "arrival": "2026-10-02T14:00:00+08:00",
        }
    )

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)

    assert captured.value.field == "plan.transport_options[1].departure"


def test_verified_rail_evidence_requires_service_times_seat_and_prices():
    raw = plan_payload()
    rail = dict(raw["evidence"][0])
    rail.update(
        {
            "evidence_id": "ev-rail",
            "provider": "铁路 12306",
            "title": "G123 北京朝阳至沈阳北",
            "source_url": "https://www.12306.cn/index/",
            "excerpt": "G123 二等座查询结果",
            "facts": ["G123 08:00 出发 10:45 到达，二等座 320 元"],
            "content_hash": "",
        }
    )
    raw["evidence"].append(rail)
    transport = raw["transport_options"][0]
    transport.update(
        {
            "source": "铁路 12306",
            "evidence_ids": ["ev-rail"],
            "service_name": "",
            "departure": "",
            "arrival": "",
            "price_cny_per_person": None,
            "price_cny_total": None,
        }
    )

    with pytest.raises(TravelValidationError) as captured:
        TravelPlanV1.from_dict(raw)
    assert captured.value.field == "plan.transport_options[0]"

    transport.update(
        {
            "service_name": "G123",
            "departure": "2026-10-01T08:00:00+08:00",
            "arrival": "2026-10-01T10:45:00+08:00",
            "seat": "二等座",
            "price_cny_per_person": 320,
            "price_cny_total": 640,
        }
    )

    plan = TravelPlanV1.from_dict(raw)
    assert plan.data["transport_options"][0]["service_name"] == "G123"


def test_12306_not_on_sale_evidence_allows_explicit_estimate_without_fake_ticket_fields():
    raw = plan_payload()
    rail = dict(raw["evidence"][0])
    rail.update(
        {
            "evidence_id": "ev-rail-not-on-sale",
            "provider": "铁路 12306",
            "title": "重庆至大理车票未开售",
            "source_url": "https://www.12306.cn/index/",
            "excerpt": "2026-10-01 车票 not_on_sale",
            "facts": ["not_on_sale", "sale_open_date=2026-09-17"],
            "content_hash": "",
        }
    )
    raw["evidence"].append(rail)
    raw["transport_options"][0].update(
        {
            "service_name": "待开售",
            "departure": "",
            "arrival": "",
            "price_cny_per_person": None,
            "price_cny_total": None,
            "source": "12306 not_on_sale planning_estimate",
            "summary": "未开售，2026-09-17 起售后复核",
            "evidence_ids": ["ev-rail-not-on-sale"],
        }
    )

    plan = TravelPlanV1.from_dict(raw)

    assert plan.data["transport_options"][0]["evidence_ids"] == [
        "ev-rail-not-on-sale"
    ]
