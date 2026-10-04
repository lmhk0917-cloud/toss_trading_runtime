import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta, timezone
import sqlite3
import pytest

from exit_research_core import catalog, evaluate, simulate, utc, clean_path
from holding_exit_research import session_bounds, seeds_from_db, loader


def points(seconds=3600):
    start=utc("2026-09-01T00:00:00Z")
    return [(start+timedelta(seconds=s),100.0,s) for s in range(0,seconds+1,30)]


def test_time_stop_and_latency_use_observed_price():
    policy=next(p for p in catalog() if p["name"]=="time_stop_10m")
    result,error=simulate(points(400),policy,0.33)
    assert error is None
    assert result["reason"]=="time_stop"
    assert result["hold_minutes"]==5.5
    assert result["net_pct"]==pytest.approx(-0.33)


def test_short_horizons_survive_missing_60_minute_labels():
    start=utc("2026-09-01T00:00:00Z")
    seed={"episode_id":"one","symbol":"X","entry_at":start.isoformat(),
          "session":"2026-09-01","session_end":(start+timedelta(hours=6)).isoformat()}
    raw=[{"id":i,"price":p,"observed_at":t.isoformat()} for t,p,i in points(700)]
    r=evaluate([seed],lambda s:raw,"2026-09-02T00:00:00Z",0.33,"test")
    assert sorted(x["horizon"] for x in r["rows"])==[5,10]
    assert all(len(x["outcomes"])==4 for x in r["rows"])


def test_future_timestamp_timezone_and_gap_fail_closed():
    with pytest.raises(ValueError): utc("2026-09-01 09:00:00")
    raw=[{"id":1,"price":100,"observed_at":"2026-09-01T00:00:00Z",
          "event_at":"2026-09-01T00:00:01Z"}]
    assert clean_path(raw,"2026-09-01T00:00:00Z","2026-09-02T00:00:00Z","2026-09-01T06:00:00Z")[1]=="future_trade_timestamp"
    p=points(0)+[(utc("2026-09-01T00:02:00Z"),99,2)]
    assert simulate(p,catalog()[0],0.33)[1]=="tick_gap"


def test_exchange_session_handles_us_dst_and_kst_midnight():
    pytest.importorskip("pytz")
    summer=session_bounds(utc("2026-07-01T01:00:00+09:00"),"toss")
    winter=session_bounds(utc("2026-01-07T01:00:00+09:00"),"toss")
    assert summer[0]=="2026-06-30"
    assert summer[1].endswith("20:00:00+00:00")
    assert winter[1].endswith("21:00:00+00:00")


def test_toss_dedup_horizons_and_exclude_price_poll_and_old_trade():
    pytest.importorskip("pytz")
    c=sqlite3.connect(":memory:"); c.row_factory=sqlite3.Row
    c.execute("create table paper_trade_candidates(id integer,analysis_id integer,symbol text,created_at text)")
    c.executemany("insert into paper_trade_candidates values(?,?,?,?)",[(1,1,"NVDA","2026-09-02 00:00:00.000000"),(2,1,"NVDA","2026-09-02 00:00:00.000000")])
    c.execute("create table trade_ticks(id integer,price real,collected_at text,trade_timestamp text,source text,symbol text)")
    for i,source,event in [(1,"price_poll_fallback","2026-09-02T00:00:00+09:00"),(2,"toss_trades","2026-09-01T23:59:59+09:00"),(3,"toss_trades","2026-09-02T00:00:00+09:00"),(4,"toss_trades","2026-09-02T00:00:02+09:00")]:
        c.execute("insert into trade_ticks values(?,100,?,?,?,?)",(i,"2026-09-02 00:00:01.000000",event,source,"NVDA"))
    c.commit(); c.execute("pragma query_only=on")
    from collections import Counter
    seeds,audit=seeds_from_db(c,"toss","2026-09-01T00:00:00Z","2026-09-03T00:00:00Z",10)
    assert len(seeds)==1
    counts=Counter()
    path=loader(c,"toss","2026-09-03T00:00:00Z",counts)(seeds[0])
    assert len(path)==1 and path[0]["id"]==3
    assert counts["non_trade_source_excluded"]==counts["pre_decision_trade_excluded"]==1
    assert counts["future_trade_timestamp_excluded"]==1
    c.close()
