import copy
import json
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from exit_research_core import utc
from toss_episode_dataset import build_dataset
from shadow_model_benchmark import benchmark


def record():
    return {"analysis_id":1,"symbol":"NVDA","analyzed_at":"2026-09-01 23:00:00.000000",
        "model":"test", "evidence_json":json.dumps({"collected_at":"2026-09-01 22:59:55.000000"}),
        "payload_json":json.dumps({"interest_score":80,"final_decision":"WATCH",
            "risk_level":"MEDIUM","confidence":"HIGH",
            "calibration":{"policy_version":"test_v1","score_delta":-2}})}


def path(seed):
    start=utc(seed["entry_at"])
    return [{"id":i,"price":100+i/1000,
        "observed_at":(start+timedelta(seconds=i*30)).isoformat()} for i in range(129)]


def test_labels_start_after_gpt_completion_and_shared_benchmark_accepts_schema():
    data=build_dataset([record()],path,"2026-09-02T21:00:00Z",0.31)
    assert len(data["rows"])==1
    row=data["rows"][0]
    assert row["decision_at"].startswith("2026-09-01T10:00")
    assert utc(row["label_entry_at"])>=utc(row["decision_at"])
    assert data["features"]==["long_score","interest_score","risk_rank","confidence_rank","score_delta"]
    report=benchmark(data)
    assert report["status"]=="insufficient_evidence"
    assert report["orders_enabled"] is False


def test_future_or_stale_evidence_and_duplicate_decisions_are_excluded():
    for stamp in ("2026-09-01 23:00:01", "2026-09-01 22:55:00"):
        r=record();r["evidence_json"]=json.dumps({"collected_at":stamp})
        data=build_dataset([r],path,"2026-09-02T21:00:00Z",0.31)
        assert data["rows"]==[]
        assert data["rejected"]["future_stale_or_unavailable_source"]==1
    data=build_dataset([record(),record()],path,"2026-09-02T21:00:00Z",0.31)
    assert data["rejected"]["duplicate_analysis_symbol"]==1


def test_finite_features_and_full_real_tick_coverage_are_required():
    r=record();p=json.loads(r["payload_json"]);p["interest_score"]=float("nan")
    r["payload_json"]=json.dumps(p)
    assert build_dataset([r],path,"2026-09-02T21:00:00Z",0.31)["rejected"]["invalid_decision_metadata"]==1
    data=build_dataset([record()],lambda s:path(s)[:20],"2026-09-02T21:00:00Z",0.31)
    assert data["rows"]==[]
    assert data["rejected"]["incomplete_exit"]==1
