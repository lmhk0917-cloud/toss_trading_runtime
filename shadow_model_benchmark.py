"""Separated fitting, calibration, policy selection and OOS shadow comparison.

Run using the offline 64-bit Python. Never imported by live entrypoints.
"""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from purged_walk_forward import build_purged_splits
from payoff_model import forecast, make_imputer, POLICY_VERSION

FEATURES = ("final_quant_score", "expected_value_score", "market_risk_score",
            "long_score", "caution_score", "trend_score", "volume_flow_score")
HORIZONS = (5, 10, 30, 60)


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":"))


def verify(dataset):
    payload = dict(dataset)
    expected = payload.pop("sha256")
    if hashlib.sha256(encode(payload).encode("utf-8")).hexdigest() != expected:
        raise ValueError("Dataset hash mismatch")


def metrics(values):
    import numpy as np
    values = np.asarray(values, dtype=float)
    gain = float(values[values > 0].sum())
    loss = -float(values[values < 0].sum())
    return {"n": len(values), "mean_net_pct": float(values.mean()) if len(values) else None,
            "win_rate": float((values > 0).mean()) if len(values) else None,
            "profit_factor": gain / loss if loss else None}


def matrix(rows, feature_names=FEATURES):
    import numpy as np
    return np.array([[r["features"].get(f) if r["features"].get(f) is not None else np.nan
                      for f in feature_names] for r in rows], dtype=float)


def fit_predict(fit, calibration, selection, test, horizon, kind, feature_names=FEATURES):
    import numpy as np
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import HistGradientBoostingClassifier
    y = np.array([r["net_returns"][horizon] > 0 for r in fit], dtype=int)
    yc = np.array([r["net_returns"][horizon] > 0 for r in calibration], dtype=int)
    if len(set(y)) < 2 or len(set(yc)) < 2:
        return None
    classifier = (LogisticRegression(C=0.1, max_iter=500, random_state=17)
                  if kind == "logistic" else
                  HistGradientBoostingClassifier(max_iter=60, max_leaf_nodes=7,
                                                 min_samples_leaf=15, l2_regularization=5,
                                                 early_stopping=False, random_state=17))
    model = make_pipeline(make_imputer(),
                          StandardScaler(), classifier)
    model.fit(matrix(fit, feature_names), y)

    def logits(rows):
        p = np.clip(model.predict_proba(matrix(rows, feature_names))[:, 1], 1e-6, 1-1e-6)
        return np.log(p/(1-p)).reshape(-1, 1)

    calibrator = LogisticRegression(C=0.1, random_state=17)
    calibrator.fit(logits(calibration), yc)
    return (calibrator.predict_proba(logits(selection))[:, 1],
            calibrator.predict_proba(logits(test))[:, 1],
            model.predict_proba(matrix(test, feature_names))[:, 1])


def choose_policy(options, minimum):
    eligible = [o for o in options if o["stats"]["n"] >= minimum
                and o["stats"]["mean_net_pct"] > 0]
    return max(eligible, key=lambda o: (o["stats"]["mean_net_pct"], o["threshold"],
                                       -int(o["horizon"]))) if eligible else None


def summarize(predictions, cost):
    by_model = defaultdict(list)
    for p in predictions:
        by_model[p["model"]].append(p)
    summary = {}
    for name, rows in by_model.items():
        trades = [r for r in rows if r["take"]]
        per_session = defaultdict(list)
        for r in rows:
            per_session[r["session"]].append(r["net_pct"] if r["take"] else 0.0)
        sessions = [sum(v)/len(v) for v in per_session.values()]
        mean = sum(sessions)/len(sessions)
        variance = sum((v-mean)**2 for v in sessions)/(len(sessions)-1) if len(sessions)>1 else None
        lcb = mean - 1.96*math.sqrt(variance/len(sessions)) if variance is not None else None
        scored = [r for r in rows if r["probability"] is not None]
        payoff_scored = [r for r in rows if "expected_net_pct" in r]
        summary[name] = {
            "closed_episode_proxy": metrics([r["net_pct"] for r in trades]),
            "moderate_cost_proxy": metrics([r["net_pct"]-cost*0.5 for r in trades]),
            "test_sessions": len(sessions), "test_episodes": len(rows),
            "coverage": len(trades)/len(rows), "session_mean_lcb95_normal_approx": lcb,
            "brier_calibrated": sum((r["probability"]-r["target"])**2 for r in scored)/len(scored) if scored else None,
            "brier_raw": sum((r["raw_probability"]-r["target"])**2 for r in scored)/len(scored) if scored else None,
            "payoff_mae_pct": sum(abs(r["expected_net_pct"]-r["net_pct"]) for r in payoff_scored)/len(payoff_scored) if payoff_scored else None,
            "payoff_bias_pct": sum(r["expected_net_pct"]-r["net_pct"] for r in payoff_scored)/len(payoff_scored) if payoff_scored else None,
            "downside_mae_pct": sum(abs(r["expected_downside_pct"]-max(-r["net_pct"],0)) for r in payoff_scored)/len(payoff_scored) if payoff_scored else None,
        }
    return summary


def benchmark(dataset, fit_sessions=10, calibration_sessions=3, selection_sessions=3,
              min_samples=20):
    verify(dataset)
    if min(fit_sessions, calibration_sessions, selection_sessions, min_samples) < 1:
        raise ValueError("Require positive sample and session counts")
    version = dataset["latest_formula"]
    feature_names = tuple(dataset.get("features") or FEATURES)
    if len(set(feature_names)) != len(feature_names) or not all(isinstance(f, str) for f in feature_names):
        raise ValueError("Invalid declared features")
    rows = [r for r in dataset["rows"] if r["formula_version"] == version]
    sessions = sorted(set(r["session"] for r in rows))
    train_count = fit_sessions + calibration_sessions + selection_sessions + 2
    splits = build_purged_splits(sessions, train_count, purge_sessions=1,
                                 embargo_sessions=1, expanding=False)
    folds, predictions, selection_predictions = [], [], []
    for split in splits["splits"]:
        train = split["train_sessions"]
        fit_dates = train[:fit_sessions]
        cal_dates = train[fit_sessions+1:fit_sessions+1+calibration_sessions]
        select_dates = train[-selection_sessions:]
        test_dates = split["test_sessions"]
        def subset(dates, known_before=None):
            return [r for r in rows if r["session"] in dates
                    and (known_before is None or r["label_available_at"][:10] < known_before)]
        fit = subset(fit_dates, cal_dates[0])
        cal = subset(cal_dates, select_dates[0])
        select = subset(select_dates, test_dates[0])
        test = subset(test_dates)
        fold = {"test_sessions": test_dates, "fit_sessions": fit_dates,
                "calibration_sessions": cal_dates, "selection_sessions": select_dates,
                "counts": [len(fit), len(cal), len(select), len(test)], "policies": {},
                "policy_candidates": {}, "untrainable_horizons": {}}
        folds.append(fold)
        if min(len(fit), len(cal), len(select)) < min_samples or not test:
            fold["status"] = "insufficient_samples"
            continue
        fold["status"] = "evaluated"
        def append(name, horizon, probs=None, raw=None, threshold=None, fixed=False):
            for i,r in enumerate(test):
                take = ((r["features"].get("long_score") or 0)>=65 if fixed else
                        probs is not None and threshold is not None and probs[i]>=threshold)
                predictions.append({"episode_id": r["episode_id"], "session": r["session"],
                                    "model": name, "horizon": horizon, "take": bool(take),
                                    "net_pct": r["net_returns"][horizon],
                                    "target": int(r["net_returns"][horizon]>0),
                                    "probability": float(probs[i]) if probs is not None else None,
                                    "raw_probability": float(raw[i]) if raw is not None else None})
        append("cash", "60")
        for h in map(str,HORIZONS):
            append("fixed_long65_"+h, h, fixed=True)
            for r in select:
                selection_predictions.append({"episode_id":r["episode_id"],"session":r["session"],
                    "test_session":test_dates[0],"model":"fixed_long65_"+h,
                    "take":bool((r["features"].get("long_score") or 0)>=65)})
        for kind in ("logistic", "small_tree"):
            options, outputs = [], {}
            fold["untrainable_horizons"][kind] = []
            for h in map(str,HORIZONS):
                result = fit_predict(fit, cal, select, test, h, kind, feature_names)
                if result is None:
                    fold["untrainable_horizons"][kind].append(h)
                    continue
                policy_p, test_p, raw_p = result
                outputs[h] = (test_p, raw_p, policy_p)
                for threshold in (0.50, 0.60, 0.70, 0.80):
                    stats = metrics([r["net_returns"][h] for i,r in enumerate(select)
                                     if policy_p[i]>=threshold])
                    options.append({"threshold": threshold,"horizon": h,"stats": stats})
            policy = choose_policy(options, min_samples)
            fold["policy_candidates"][kind] = options
            fold["policies"][kind] = policy
            selected_h = policy["horizon"] if policy else "60"
            for i,r in enumerate(select):
                selection_predictions.append({"episode_id":r["episode_id"],"session":r["session"],
                    "test_session":test_dates[0],"model":kind,
                    "take":bool(policy and outputs[selected_h][2][i]>=policy["threshold"])})
            if policy:
                h = policy["horizon"]
                append(kind,h,*outputs[h][:2],threshold=policy["threshold"])
            elif "60" in outputs:
                append(kind,"60",*outputs["60"][:2])
            else:
                append(kind,"60")
        fold["payoff_models"] = {}
        for h in map(str, HORIZONS):
            name = "payoff_downside_" + h
            output = forecast(fit, cal, select + test, h, feature_names, dataset["cost_pct"])
            selected = output["predictions"][:len(select)]
            tested = output["predictions"][len(select):]
            fold["payoff_models"][name] = {
                "policy_version": POLICY_VERSION,
                "settings": output["settings"],
                "calibration_bias": output["calibration_bias"],
                "selection_stats": metrics([r["net_returns"][h] for r,p in zip(select, selected) if p["take"]]),
                "selection_used_for_tuning": False,
            }
            for r,p in zip(select, selected):
                selection_predictions.append({"episode_id":r["episode_id"], "session":r["session"],
                    "test_session":test_dates[0], "model":name, "take":p["take"]})
            for r,p in zip(test, tested):
                predictions.append({"episode_id":r["episode_id"], "session":r["session"],
                    "model":name, "horizon":h, "take":p["take"],
                    "net_pct":r["net_returns"][h], "target":int(r["net_returns"][h]>0),
                    "probability":None, "raw_probability":None,
                    "expected_net_pct":p["expected_net_pct"],
                    "expected_downside_pct":p["expected_downside_pct"],
                    "utility_pct":p["utility_pct"]})
    import sklearn
    return {"schema": "shadow_model_benchmark_v2_payoff", "dataset_sha256": dataset["sha256"],
            "formula_version": version, "session_count": len(sessions), "episode_count": len(rows),
            "minimum_sessions_for_first_fold": train_count + 2,
            "split_audit": splits["audit"], "folds": folds, "predictions": predictions,
            "selection_predictions": selection_predictions,
            "summary": summarize(predictions,dataset["cost_pct"]),
            "status": "completed" if predictions else "insufficient_evidence",
            "mode": "shadow_only", "orders_enabled": False, "live_settings_changed": False,
            "settings": {"fit_sessions": fit_sessions,"calibration_sessions": calibration_sessions,
                         "selection_sessions": selection_sessions,"min_samples": min_samples,
                         "cost_pct": dataset["cost_pct"],"seed":17,"features":list(feature_names)},
            "sklearn_version": sklearn.__version__,
            "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in (Path(__file__),
                                        Path(__file__).with_name("payoff_model.py"))},
            "limitations": ["Fixed horizon selection only; no intrahorizon stop/trailing simulation.",
                            "Mean episode returns are not portfolio returns.",
                            "Session confidence bounds are approximate and are not a deployment gate.",
                            "Current score features inherit the provenance limits of the source database.",
                            "No automatic promotion or intraday refitting.",
                            "Payoff horizons are separate frozen hypotheses, not a best-horizon selector."]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--diagnostic", action="store_true",
                        help="Use short 5/2/2 session blocks; never promotion evidence")
    args = parser.parse_args()
    with open(args.dataset,encoding="utf-8") as handle:
        data = json.load(handle)
    report = (benchmark(data,fit_sessions=5,calibration_sessions=2,selection_sessions=2)
              if args.diagnostic else benchmark(data))
    report["diagnostic_only"] = args.diagnostic
    with open(args.output,"x",encoding="utf-8") as handle:
        handle.write(encode(report)+"\n")
    print(encode({k:report[k] for k in ("status","episode_count","session_count","summary")}))


if __name__ == "__main__":
    main()
