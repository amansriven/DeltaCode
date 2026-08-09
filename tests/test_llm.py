from app import llm


def test_unavailable_llm_never_blocks_case_generation(monkeypatch):
    monkeypatch.setattr(llm, "_available", lambda: False)
    assert llm.suggest_extra_cases({}, {}, [{"name": "baseline"}]) == []


def test_unavailable_llm_never_blocks_explanations(monkeypatch):
    monkeypatch.setattr(llm, "_available", lambda: False)
    assert llm.explain_findings([{"case": "example"}]) == {}


def test_llm_cases_are_normalized_into_executable_cases(monkeypatch):
    monkeypatch.setattr(llm, "_available", lambda: True)
    monkeypatch.setattr(
        llm,
        "_generate_json",
        lambda **_kwargs: {
            "cases": [
                {
                    "name": "negative_price",
                    "method": "post",
                    "path": "/items",
                    "json_text": '{"price":-1}',
                    "rationale": "Prices are normally non-negative.",
                }
            ]
        },
    )

    cases = llm.suggest_extra_cases(
        {"paths": {"/items": {"post": {}}}},
        {"paths": {"/items": {"post": {}}}},
        [{"name": "operation_baseline"}],
    )

    assert cases == [
        {
            "id": "post:/items:negative_price",
            "name": "negative_price",
            "method": "POST",
            "path": "/items",
            "source": "llm",
            "rationale": "Prices are normally non-negative.",
            "json": {"price": -1},
            "query": {},
        }
    ]


def test_explanations_are_keyed_by_stable_case_id(monkeypatch):
    monkeypatch.setattr(llm, "_available", lambda: True)
    monkeypatch.setattr(
        llm,
        "_generate_json",
        lambda **_kwargs: {
            "summaries": [
                {
                    "case_id": "post:/items:omit_discount",
                    "impact": "Existing clients can no longer omit discount.",
                    "likely_cause": "The field became required.",
                }
            ]
        },
    )

    assert llm.explain_findings(
        [{"case_id": "post:/items:omit_discount", "case": "omit_discount"}]
    ) == {
        "post:/items:omit_discount": {
            "impact": "Existing clients can no longer omit discount.",
            "likely_cause": "The field became required.",
        }
    }
