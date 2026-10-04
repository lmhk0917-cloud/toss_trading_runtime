"""Identical broker adapter CLI for offline Kiwoom/Toss holding-exit research."""
import argparse
import hashlib
import sqlite3
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from exit_research_core import canonical, evaluate, utc

KST=timezone(timedelta(hours=9))


def local(value):
    t=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    return t.replace(tzinfo=KST) if t.tzinfo is None else t


def dbtime(value):
    return utc(value).astimezone(KST).strftime("%Y-%m-%d %H:%M:%S.%f")


def connect(db):
    c=sqlite3.connect(Path(db).resolve().as_uri()+"?mode=ro",uri=True,timeout=30)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA query_only=ON")
    return c


def session_bounds(t,broker):
    if broker=="kiwoom":
        zone=KST
    else:
        # pytz works in both the offline 64-bit and Kiwoom 32-bit environments.
        import pytz
        zone=pytz.timezone("America/New_York")
    t=t.astimezone(zone)
    opening=time(9,0) if broker=="kiwoom" else time(9,30)
    closing=time(15,30) if broker=="kiwoom" else time(16,0)
    if t.weekday()>=5 or not opening<=t.time()<closing:
        return None
    naive=datetime.combine(t.date(),closing)
    end=naive.replace(tzinfo=zone) if broker=="kiwoom" else zone.localize(naive)
    return t.date().isoformat(),end.astimezone(timezone.utc).isoformat()


def seeds_from_db(conn,broker,since,asof,limit):
    if broker=="kiwoom":
        sql="SELECT id,code AS symbol,detected_at AS created_at FROM signal_logs WHERE detected_at>=? AND detected_at<=? ORDER BY detected_at,id"
    else:
        # Each analysis/symbol is one decision, independent of stored horizon labels.
        sql="""SELECT MIN(id) AS id,symbol,created_at FROM paper_trade_candidates
               WHERE created_at>=? AND created_at<=? GROUP BY analysis_id,symbol,created_at
               ORDER BY created_at,id"""
    seeds,seen_until,rejects=[],{},Counter()
    examined=0
    for row in conn.execute(sql,(dbtime(since),dbtime(asof))):
        examined+=1
        t=local(row["created_at"])
        session=session_bounds(t,broker)
        if not session:
            rejects["outside_regular_session"]+=1
            continue
        key=(row["symbol"],session[0])
        if key in seen_until and t<seen_until[key]:
            rejects["overlapping_decision"]+=1
            continue
        seen_until[key]=t+timedelta(minutes=62)
        seeds.append({"episode_id":"{}:{}".format(broker,row["id"]),"symbol":row["symbol"],
                      "entry_at":t.astimezone(timezone.utc).isoformat(),"session":session[0],
                      "session_end":session[1]})
        if len(seeds)>=limit:
            break
    return seeds,{"examined":examined,"rejected":dict(rejects),"limit":limit,
                  "limit_reached":len(seeds)>=limit,"scope":"all_saved_decisions_not_entry_filtered"}


def loader(conn,broker,asof,counters):
    def load(seed):
        end=min(utc(seed["entry_at"])+timedelta(minutes=64),utc(seed["session_end"]),utc(asof))
        params=(seed["symbol"],dbtime(seed["entry_at"]),dbtime(end.isoformat()))
        if broker=="kiwoom":
            records=conn.execute("SELECT id,price,received_at FROM ticks WHERE code=? AND received_at>=? AND received_at<=? ORDER BY received_at,id",params)
            return [{"id":r["id"],"price":r["price"],"observed_at":local(r["received_at"]).astimezone(timezone.utc).isoformat()} for r in records]
        records=conn.execute("SELECT id,price,collected_at,trade_timestamp,source FROM trade_ticks WHERE symbol=? AND collected_at>=? AND collected_at<=? ORDER BY collected_at,id",params)
        points=[]
        for r in records:
            if r["source"]!="toss_trades":
                counters["non_trade_source_excluded"]+=1
                continue
            try:
                event=utc(r["trade_timestamp"])
                observed=local(r["collected_at"]).astimezone(timezone.utc)
            except (ValueError,TypeError):
                counters["invalid_trade_timestamp"]+=1
                continue
            if event>observed:
                counters["future_trade_timestamp_excluded"]+=1
                continue
            if event<utc(seed["entry_at"]):
                counters["pre_decision_trade_excluded"]+=1
                continue
            if (observed-event).total_seconds()>60:
                counters["delayed_trade_excluded"]+=1
                continue
            points.append({"id":r["id"],"price":r["price"],"event_at":event.isoformat(),"observed_at":observed.isoformat()})
        points.sort(key=lambda p:(utc(p["observed_at"]),utc(p["event_at"]),p["id"]))
        return points
    return load


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--broker",choices=("kiwoom","toss"),required=True)
    parser.add_argument("--db",required=True)
    parser.add_argument("--since",required=True)
    parser.add_argument("--asof",required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--max-seeds",type=int,default=60)
    args=parser.parse_args()
    if args.max_seeds<1 or utc(args.since)>=utc(args.asof):
        parser.error("Require positive limit and since < asof")
    import config
    if args.broker=="toss":
        cost=float(config.PAPER_ROUND_TRIP_COST_PCT)
    else:
        cost=float(config.TRADE_BUY_FEE_PCT+config.TRADE_SELL_FEE_PCT+config.TRADE_SELL_TAX_PCT+2*config.TRADE_SLIPPAGE_PCT)
    c=connect(args.db)
    exclusions=Counter()
    try:
        c.execute("BEGIN")
        seeds,audit=seeds_from_db(c,args.broker,args.since,args.asof,args.max_seeds)
        report=evaluate(seeds,loader(c,args.broker,args.asof,exclusions),args.asof,cost,args.broker)
    finally:
        c.close()
    report.update(adapter_audit=audit,source_exclusions=dict(exclusions),
                  core_sha256=hashlib.sha256(Path(__file__).with_name("exit_research_core.py").read_bytes()).hexdigest(),
                  adapter_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  session_mode="regular_only",database_local_timezone="Asia/Seoul")
    with open(args.output,"x",encoding="utf-8") as handle:
        handle.write(canonical(report)+"\n")
    print(canonical({k:report[k] for k in ("broker","cost_pct","seed_count","session_count","rejected","descriptive","source_exclusions")}))


if __name__=="__main__":
    main()
