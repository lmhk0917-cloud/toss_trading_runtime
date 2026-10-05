"""Offline expected net payoff and downside model; no broker dependencies."""
import math


POLICY_VERSION = "ridge_payoff_downside_v1"


def make_imputer():
    """Preserve empty columns on both offline and legacy Kiwoom sklearn."""
    import inspect
    from sklearn.impute import SimpleImputer
    options = {"strategy": "constant", "fill_value": 0, "add_indicator": True}
    if "keep_empty_features" in inspect.signature(SimpleImputer).parameters:
        options["keep_empty_features"] = True
    return SimpleImputer(**options)


def forecast(fit, calibration, targets, horizon, feature_names, cost_pct):
    """Fit only on fit rows; calibrate bias only on earlier calibration rows.

    Returns are percent units and already net of the dataset's round-trip cost.
    Targets' outcomes are deliberately never read. This is a signal-return
    proxy, not an executable order or portfolio model.
    """
    import numpy as np
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    if not math.isfinite(cost_pct) or cost_pct < 0:
        raise ValueError("Require finite nonnegative cost")
    if not fit or not calibration or not targets or not feature_names:
        raise ValueError("Require fit, calibration, targets and features")
    if len(set(feature_names)) != len(feature_names):
        raise ValueError("Duplicate features")
    ids = [r["episode_id"] for r in fit + calibration + targets]
    if len(ids) != len(set(ids)):
        raise ValueError("Repeated episode across research blocks")

    def timing(row):
        from datetime import datetime
        decision = datetime.fromisoformat(row["decision_at"])
        source = datetime.fromisoformat(row["source_at"])
        if source > decision:
            raise ValueError("Future feature source")
        if decision.date().isoformat() != row["session"]:
            raise ValueError("Session mismatch")
        return decision

    fit_times = [timing(r) for r in fit]
    cal_times = [timing(r) for r in calibration]
    target_times = [timing(r) for r in targets]
    if max(fit_times) >= min(cal_times) or max(cal_times) >= min(target_times):
        raise ValueError("Overlapping chronological blocks")
    from datetime import datetime
    for rows, cutoff in ((fit, min(cal_times)), (calibration, min(target_times))):
        for row in rows:
            if datetime.fromisoformat(row["label_available_at"]) >= cutoff:
                raise ValueError("Training label unavailable at next block")

    def matrix(rows):
        values = []
        for row in rows:
            vector = []
            for name in feature_names:
                value = row["features"].get(name)
                if value is None:
                    vector.append(np.nan)
                elif isinstance(value, bool) or not math.isfinite(float(value)):
                    raise ValueError("Invalid feature")
                else:
                    vector.append(float(value))
            values.append(vector)
        return np.asarray(values, dtype=float)

    def labels(rows):
        values = [float(r["net_returns"][horizon]) for r in rows]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Invalid payoff label")
        return np.asarray(values, dtype=float)

    xfit, xcal, xtarget = matrix(fit), matrix(calibration), matrix(targets)
    yfit, ycal = labels(fit), labels(calibration)
    predictions = []
    biases = {}
    for name, train_y, cal_y in (
        ("expected_net_pct", yfit, ycal),
        ("expected_downside_pct", np.maximum(-yfit, 0), np.maximum(-ycal, 0)),
    ):
        model = make_pipeline(
            make_imputer(),
            StandardScaler(), Ridge(alpha=10.0),
        )
        model.fit(xfit, train_y)
        bias = float(np.mean(cal_y - model.predict(xcal)))
        biases[name] = bias
        values = model.predict(xtarget) + bias
        predictions.append(np.maximum(values, 0) if name == "expected_downside_pct" else values)
    results = []
    for net, downside in zip(*predictions):
        # A frozen conservative research gate, never selected on target outcomes.
        utility = float(net - downside - cost_pct * 0.5)
        results.append({
            "expected_net_pct": float(net), "expected_downside_pct": float(downside),
            "stress_extra_cost_pct": cost_pct * 0.5, "utility_pct": utility,
            "take": utility > 0, "orders_enabled": False,
        })
    return {"policy_version": POLICY_VERSION, "predictions": results,
            "calibration_bias": biases, "settings": {"ridge_alpha": 10.0,
            "downside_weight": 1.0, "extra_cost_multiplier": 0.5},
            "orders_enabled": False, "live_settings_changed": False}
