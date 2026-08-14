from __future__ import annotations

from copy import deepcopy


def request_payload(*, mode: str = "quick", budget: float | None = 5000) -> dict:
    return {
        "schema_version": "1",
        "origin": "重庆",
        "destinations": ["大理"],
        "start_date": "2026-10-01",
        "end_date": "2026-10-02",
        "date_flexibility": "前后可浮动一天",
        "duration_days": 2,
        "travellers": [{"type": "大学生", "count": 2}],
        "budget_total_cny": budget,
        "transport_preferences": ["火车优先"],
        "stay_preferences": ["交通方便", "性价比高"],
        "interest_tags": ["自然风光", "美食"],
        "pace": "balanced",
        "hard_constraints": ["不租车"],
        "soft_preferences": ["少折返"],
        "planning_mode": mode,
    }


def plan_payload(*, mode: str = "quick", budget: float | None = 5000) -> dict:
    return {
        "schema_version": "1",
        "plan_id": "model-must-not-own-this",
        "owner_user_id": "forged-owner",
        "request": request_payload(mode=mode, budget=budget),
        "assumptions": ["酒店价格为区间估算，预订前复核"],
        "freshness_summary": {"weather": "live", "guides": "snapshot"},
        "transport_options": [
            {
                "title": "重庆至大理铁路方案",
                "duration": "以查询班次为准",
                "price_note": "实时票价需复核",
                "status": "not_on_sale",
                "reason": "不把未开售误称为无票",
            }
        ],
        "stay_recommendations": [
            {
                "area": "大理古城南门",
                "reason": "公交与餐饮方便",
                "price_note": "POI 不代表实时房价或房态",
            }
        ],
        "days": [
            {
                "date": "2026-10-01",
                "city_or_area": "大理古城",
                "activities": [
                    {
                        "start": "14:00",
                        "end": "17:00",
                        "place": "大理古城",
                        "reason": "抵达日控制强度",
                        "evidence_ids": ["ev-map"],
                        "opening_hours": "全天开放区域",
                        "location": {"longitude": 100.165, "latitude": 25.694},
                    }
                ],
                "route_segments": [
                    {
                        "mode": "公交",
                        "from": "大理站",
                        "to": "大理古城",
                        "duration": 50,
                        "distance": 18,
                        "source": "高德地图",
                        "evidence_ids": ["ev-map"],
                        "path": [
                            {"longitude": 100.25, "latitude": 25.59},
                            {"longitude": 100.165, "latitude": 25.694},
                        ],
                    }
                ],
                "meal_suggestions": ["古城周边本地小吃"],
                "daily_budget": 420,
                "weather_adjustment": "降雨时缩短步行",
                "fallback_plan": "改为室内展馆",
                "intensity_score": 4.2,
            },
            {
                "date": "2026-10-02",
                "city_or_area": "大理古城",
                "activities": [
                    {
                        "start": "09:00",
                        "end": "12:00",
                        "place": "洱海生态廊道",
                        "reason": "上午天气较稳定",
                        "evidence_ids": ["ev-weather"],
                        "opening_hours": "09:00-18:00",
                        "location": {"longitude": 100.14, "latitude": 25.73},
                    },
                    {
                        "start": "14:00",
                        "end": "17:00",
                        "place": "喜洲古镇",
                        "reason": "顺路体验白族文化",
                        "evidence_ids": ["ev-guide"],
                        "opening_hours": "08:30-18:00",
                        "location": {"longitude": 100.13, "latitude": 25.85},
                    },
                ],
                "route_segments": [
                    {
                        "mode": "公交",
                        "from": "洱海生态廊道",
                        "to": "喜洲古镇",
                        "duration": 70,
                        "distance": 28,
                        "source": "高德地图",
                        "evidence_ids": ["ev-map"],
                        "path": [],
                    }
                ],
                "meal_suggestions": ["喜洲粑粑"],
                "daily_budget": 520,
                "weather_adjustment": "强风时取消骑行",
                "fallback_plan": "改为扎染体验",
                "intensity_score": 6.8,
            },
        ],
        "budget": {
            "lower": 3600,
            "expected": 4400,
            "upper": 5200,
            "items": [
                {"name": "交通", "lower": 1600, "expected": 1900, "upper": 2300},
                {"name": "住宿", "lower": 900, "expected": 1200, "upper": 1500},
            ],
        },
        "weather_summary": [
            {"date": "2026-10-01", "summary": "短期预报示例", "freshness": "live", "provider": "Open-Meteo"},
            {"date": "2026-10-02", "summary": "短期预报示例", "freshness": "live", "provider": "Open-Meteo"},
        ],
        "fallbacks": ["降雨时使用室内替代"],
        "avoidance_tips": ["社交平台单帖仅作为个体体验"],
        "evidence": [
            {
                "evidence_id": "ev-map",
                "source_type": "official_api",
                "provider": "高德地图",
                "title": "大理路线查询",
                "source_url": "https://ditu.amap.com/route",
                "published_at": "",
                "retrieved_at": "2026-09-28T08:00:00Z",
                "data_as_of": "2026-09-28T08:00:00Z",
                "excerpt": "路线距离与时间快照",
                "facts": ["大理站到古城约 18 公里"],
                "confidence": 0.95,
                "freshness": "live",
                "content_hash": "",
            },
            {
                "evidence_id": "ev-weather",
                "source_type": "official_api",
                "provider": "Open-Meteo",
                "title": "大理短期天气",
                "source_url": "https://open-meteo.com/en/docs",
                "published_at": "",
                "retrieved_at": "2026-09-28T08:01:00Z",
                "data_as_of": "2026-09-28T08:00:00Z",
                "excerpt": "预报窗口内天气",
                "facts": ["午后有降雨概率"],
                "confidence": 0.9,
                "freshness": "live",
                "content_hash": "",
            },
            {
                "evidence_id": "ev-guide",
                "source_type": "social_post",
                "provider": "xiaohongshu-readonly",
                "title": "喜洲体验笔记",
                "source_url": "https://www.xiaohongshu.com/explore/example",
                "published_at": "2026-08-01T00:00:00Z",
                "retrieved_at": "2026-09-28T08:02:00Z",
                "data_as_of": "",
                "excerpt": "个体体验：下午游客较多",
                "facts": ["单一来源，只作为个体体验"],
                "confidence": 0.55,
                "freshness": "snapshot",
                "content_hash": "",
            },
        ],
        "unknowns": ["车票尚未开售，出发前重新查询余票"],
        "generated_at": "2026-09-28T08:10:00Z",
    }


def clone_plan() -> dict:
    return deepcopy(plan_payload())

