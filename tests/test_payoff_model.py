import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from payoff_model import forecast


def row(day, score, net=0.5):
    return {"episode_id": str(day), "session": "2026-09-{:02d}".format(day),
            "decision_at": "2026-09-{:02d} 09:00:00".format(day),
            "source_at": "2026-09-{:02d} 08:59:00".format(day),
            "label_available_at": "2026-09-{:02d} 10:01:00".format(day),
            "features": {"score": score}, "net_returns": {"5": net}}


def inputs():
    return [row(1, 10, -1), row(2, 90, 1)], [row(3, 10, -1), row(4, 90, 1)], [row(5, 10), row(6, 90)]


def test_positive_payoff_and_downside_are_separate_and_orders_disabled():
    fit, cal, target = inputs()
    output = forecast(fit, cal, target, "5", ["score"], 0.33)
    low, high = output["predictions"]
    assert low["expected_net_pct"] < high["expected_net_pct"]
    assert low["expected_downside_pct"] > high["expected_downside_pct"] >= 0
    assert low["take"] is False
    assert output["orders_enabled"] is False
    assert output["live_settings_changed"] is False
    assert high["utility_pct"] == pytest.approx(high["expected_net_pct"] - high["expected_downside_pct"] - 0.165)


def test_target_outcomes_cannot_change_forecasts_or_decisions():
    fit, cal, target = inputs()
    first = forecast(fit, cal, target, "5", ["score"], 0.33)
    changed = copy.deepcopy(target)
    for r in changed:
        r.pop("net_returns")
        r.pop("label_available_at")
    assert first == forecast(fit, cal, changed, "5", ["score"], 0.33)


@pytest.mark.parametrize("violation", ["future_source", "future_label", "overlap", "infinite_feature"])
def test_leakage_and_invalid_features_fail_closed(violation):
    fit, cal, target = inputs()
    if violation == "future_source":
        target[0]["source_at"] = "2026-09-05 09:00:01"
    elif violation == "future_label":
        fit[0]["label_available_at"] = cal[0]["decision_at"]
    elif violation == "overlap":
        cal[0] = copy.deepcopy(fit[0])
    else:
        target[0]["features"]["score"] = float("inf")
    with pytest.raises(ValueError):
        forecast(fit, cal, target, "5", ["score"], 0.33)


def test_single_class_and_missing_feature_remain_valid_for_regression():
    fit, cal, target = inputs()
    for r in fit + cal:
        r["net_returns"]["5"] = -0.33
    for r in fit + cal + target:
        r["features"]["missing"] = None
    output = forecast(fit, cal, target, "5", ["missing"], 0.33)
    assert all(p["take"] is False for p in output["predictions"])
    assert all(p["expected_net_pct"] == pytest.approx(-0.33) for p in output["predictions"])
