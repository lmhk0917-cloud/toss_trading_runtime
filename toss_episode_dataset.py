"""Read-only point-in-time GPT decision episodes with real trade-tick labels."""
import argparse
import hashlib
import json
import math
from collections import Counter
from datetime import timedelta, timezone
from pathlib import Path

from exit_research_core import clean_path, simulate, utc
from holding_exit_research import connect, loader, local, session_bounds, dbtime
from shadow_model_benchmark import encode

FEATURES = ("long_score", "interest_score", "risk_rank", "confidence_rank", "score_delta")
HORIZONS = (5, 10, 30, 60)


def digest(data):
    return hashlib.sha256(encode(data).encode("utf-8")).hexdigest()


def build_dataset(records, path_loader, asof, cost_pct, spacing_minutes=62):
    if not math.isfinite(cost_pct) or cost_pct < 0 or spacing_minutes < 62:
        raise ValueError("Invalid cost or independence spacing")
    cutoff = utc(asof)
    rows, rejected, seen, reserved = [], Counter(), set(), {}
    latest_formula, previous = None, None
    for record in records:
        identity = (record["analysis_id"], record["symbol"])
        if identity in seen:
            rejected["duplicate_analysis_symbol"] += 1
            continue
        seen.add(identity)
        try:
            decision = local(record["analyzed_at"])
            if previous is not None and decision < previous:
                raise ValueError("Unordered decisions")
            previous = decision
            evidence = json.loads(record["evidence_json"])
            item = json.loads(record["payload_json"])
            source = local(evidence["collected_at"])
            score = item["interest_score"]
            if isinstance(score, bool) or not math.isfinite(float(score)):
                raise ValueError("Invalid decision score")
            calibration = item.get("calibration") or {}
            delta = float(calibration.get("score_delta") or 0)
            if not math.isfinite(delta):
                raise ValueError("Invalid calibration delta")
            formula = "toss:{}:{}".format(record.get("model") or "unknown_model",
                calibration.get("policy_version") or "legacy_unversioned")
            latest_formula = formula
        except (ValueError, TypeError, KeyError, AttributeError):
            rejected["invalid_decision_metadata"] += 1
            continue
        if source > decision or (decision-source).total_seconds() > 120 or decision > cutoff:
            rejected["future_stale_or_unavailable_source"] += 1
            continue
        session = session_bounds(decision, "toss")
        if not session:
            rejected["outside_regular_session"] += 1
            continue
        key = (record["symbol"], session[0])
        if decision < reserved.get(key, decision):
            rejected["overlapping_episode"] += 1
            continue
        reserved[key] = decision+timedelta(minutes=spacing_minutes)
        seed = {"episode_id": "toss:{}:{}".format(*identity), "symbol": record["symbol"],
                "entry_at": decision.astimezone(timezone.utc).isoformat(),
                "session": session[0], "session_end": session[1]}
        raw = path_loader(seed)
        points, error = clean_path(raw, seed["entry_at"], asof, session[1])
        outcomes = {}
        if error:
            rejected[error] += 1
            continue
        for h in HORIZONS:
            result, error = simulate(points, {"family":"fixed_time", "minutes":h}, cost_pct)
            if error:
                break
            outcomes[str(h)] = result
        if error:
            rejected[error] += 1
            continue
        import pytz
        zone = pytz.timezone("America/New_York")
        def exchange_time(t):
            return t.astimezone(zone).isoformat()
        decision_name = str(item.get("final_decision") or "UNKNOWN")
        rank = {"LOW":0, "MEDIUM":1, "HIGH":2}
        rows.append({"episode_id":seed["episode_id"], "symbol":seed["symbol"],
            "session":session[0], "decision_at":exchange_time(decision),
            "source_at":exchange_time(source), "formula_version":formula,
            "label_entry_at":exchange_time(points[0][0]),
            "label_available_at":exchange_time(utc(outcomes["60"]["exit_at"])),
            "entry_status": (item.get("trade_plan") or {}).get("entry", {}).get("status", "unknown"),
            "features": {"interest_score":float(score),
                "long_score":float(score) if decision_name=="WATCH" else 0.0,
                "risk_rank":rank.get(item.get("risk_level")),
                "confidence_rank":rank.get(item.get("confidence")), "score_delta":delta},
            "net_returns": {h:r["net_pct"] for h,r in outcomes.items()},
            "gross_returns": {h:r["gross_pct"] for h,r in outcomes.items()},
            "path_sha256":digest(raw)})
    data = {"schema":"episode_dataset_v1", "broker":"toss", "asof":asof,
            "features":list(FEATURES), "rows":rows, "latest_formula":latest_formula,
            "cost_pct":cost_pct, "spacing_minutes":spacing_minutes,
            "rejected":dict(rejected), "orders_enabled":False,
            "scope":"all_saved_gpt_decisions_not_eligible_entries",
            "limitations":["First trade after GPT completion, not evidence-collection price.",
                "Complete 60m tick coverage required; not an executable-entry dataset.",
                "Legacy policy versions and prompts are not proven immutable.",
                "Evidence age measures collection time, not freshness of every underlying feed.",
                "No legacy paper-return labels or price-poll fallback used."]}
    data["sha256"] = digest(data)
    return data


def main():
    parser = argparse.ArgumentParser()
    for option in ("db", "since", "asof", "output"):
        parser.add_argument("--"+option, required=True)
    parser.add_argument("--max-decisions", type=int, default=200)
    args = parser.parse_args()
    if not 1<=args.max_decisions<=5000 or utc(args.since)>=utc(args.asof):
        parser.error("Invalid range or sample limit")
    import config
    conn, counters = connect(args.db), Counter()
    try:
        conn.execute("BEGIN")
        records = [dict(r) for r in conn.execute("""SELECT s.analysis_id,s.symbol,s.payload_json,
            a.analyzed_at,a.evidence_json,a.model FROM structured_analysis s
            JOIN analysis_results a ON a.id=s.analysis_id
            WHERE a.analyzed_at>=? AND a.analyzed_at<=?
            ORDER BY a.analyzed_at,a.id,s.id LIMIT ?""",
            (dbtime(args.since),dbtime(args.asof),args.max_decisions+1))]
        data = build_dataset(records[:args.max_decisions], loader(conn,"toss",args.asof,counters),
                             args.asof,float(config.PAPER_ROUND_TRIP_COST_PCT))
    finally:
        conn.close()
    data.pop("sha256")
    data.update(source_exclusions=dict(counters), max_decisions=args.max_decisions,
                limit_reached=len(records)>args.max_decisions,
                source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    data["sha256"] = digest(data)
    with open(args.output,"x",encoding="utf-8") as handle:
        handle.write(encode(data)+"\n")
    print(encode({"episodes":len(data["rows"]),"rejected":data["rejected"],"sha256":data["sha256"]}))


if __name__ == "__main__":
    main()
