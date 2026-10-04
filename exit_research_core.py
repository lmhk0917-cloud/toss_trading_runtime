"""Broker-independent multi-horizon exit research, compatible with Python 3.7."""
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone

SCHEMA = "multi_horizon_exit_v1"
HORIZONS = (5, 10, 15, 30, 60)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utc(value):
    t=datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if t.tzinfo is None:
        raise ValueError("Explicit timezone required")
    return t.astimezone(timezone.utc)


def catalog():
    policies=[]
    for h in HORIZONS:
        for family in ("fixed_time", "bracket", "time_stop", "trailing"):
            policy={"name":"{}_{}m".format(family,h),"family":family,"minutes":h}
            if family!="fixed_time":
                policy["stop_pct"]=0.6
            if family in ("bracket","time_stop"):
                policy["target_pct"]=0.8
            if family=="time_stop":
                policy.update(check_minutes=min(5,h/2.0),min_progress_pct=0.10)
            if family=="trailing":
                policy.update(activate_pct=0.8,trail_pct=0.4)
            policies.append(policy)
    return policies


def stats(trades):
    trades=list(trades)
    returns=[r["net_pct"] for r in trades]
    gains=sum(max(v,0) for v in returns)
    losses=-sum(min(v,0) for v in returns)
    positive=[v for v in returns if v>0]
    negative=[v for v in returns if v<0]
    return {"n":len(returns),"mean_net_pct":sum(returns)/len(returns) if returns else None,
            "win_rate_pct":100*len(positive)/len(returns) if returns else None,
            "profit_factor":gains/losses if losses else None,
            "average_win_pct":sum(positive)/len(positive) if positive else None,
            "average_loss_pct":sum(negative)/len(negative) if negative else None,
            "worst_trade_net_pct":min(returns) if returns else None,
            "mean_mfe_pct":sum(r["mfe_pct"] for r in trades)/len(trades) if trades else None,
            "mean_mae_pct":sum(r["mae_pct"] for r in trades)/len(trades) if trades else None,
            "mean_hold_minutes":sum(r["hold_minutes"] for r in trades)/len(trades) if trades else None,
            "exit_reasons":dict(Counter(r["reason"] for r in trades))}


def clean_path(raw, anchor, cutoff, session_end, max_gap=60, max_entry_delay=60):
    anchor,cutoff,session_end=utc(anchor),utc(cutoff),utc(session_end)
    points=[]
    previous=None
    for r in raw:
        t=utc(r["observed_at"])
        event=utc(r.get("event_at",r["observed_at"]))
        p=r["price"]
        key=(t,event,r["id"])
        if previous is not None and key<=previous:
            return [],"unordered_ticks"
        previous=key
        if not isinstance(p,(int,float)) or isinstance(p,bool) or not math.isfinite(p) or p<=0:
            return [],"invalid_price"
        if event>t:
            return [],"future_trade_timestamp"
        if t<anchor or event<anchor or t>cutoff or t>session_end:
            continue
        points.append((t,p,r["id"]))
    if not points or (points[0][0]-anchor).total_seconds()>max_entry_delay:
        return [],"entry_tick_missing"
    # Per-horizon gap checks happen below, so a later gap cannot disqualify 5m.
    return points,None


def simulate(points, policy, cost_pct, latency=1, max_gap=60):
    if not points:
        return None,"empty_path"
    start,price,_=points[0]
    deadline=start+timedelta(minutes=policy["minutes"])
    peak=price
    trough=price
    reason,trigger=None,None
    previous=start
    for t,p,tid in points[1:]:
        if (t-previous).total_seconds()>max_gap:
            return None,"tick_gap"
        previous=t
        peak=max(peak,p)
        trough=min(trough,p)
        if trigger is not None:
            if t>=trigger+timedelta(seconds=latency):
                gross=(p/price-1)*100
                return {"net_pct":gross-cost_pct,"gross_pct":gross,"entry_price":price,
                        "exit_price":p,"reason":reason,"trigger_at":trigger.isoformat(),
                        "exit_at":t.isoformat(),"exit_tick_id":tid,
                        "mfe_pct":(peak/price-1)*100,"mae_pct":(trough/price-1)*100,
                        "hold_minutes":(t-start).total_seconds()/60},None
            continue
        peak=max(peak,p)
        gain=(p/price-1)*100
        if t>=deadline:
            reason="timeout"
        elif gain<=-policy.get("stop_pct",float("inf")):
            reason="stop"
        elif gain>=policy.get("target_pct",float("inf")):
            reason="target"
        elif (policy["family"]=="time_stop" and
              (t-start).total_seconds()>=policy["check_minutes"]*60 and
              gain<policy["min_progress_pct"]):
            reason="time_stop"
        elif (policy["family"]=="trailing" and (peak/price-1)*100>=policy["activate_pct"]
              and (p/peak-1)*100<=-policy["trail_pct"]):
            reason="trailing"
        if reason:
            trigger=t
    return None,"incomplete_exit"


def evaluate(seeds, path_loader, asof, cost_pct, broker, max_gap=60, latency=1,
             training_sessions=10, minimum_samples=20):
    if not math.isfinite(cost_pct) or cost_pct<0 or max_gap<=0 or latency<0:
        raise ValueError("Invalid cost, gap or latency")
    utc(asof)
    policies=catalog()
    rows=[]
    rejects=Counter()
    sessions=sorted(set(s["session"] for s in seeds))
    seen=set()
    for seed in seeds:
        if seed["episode_id"] in seen:
            raise ValueError("Duplicate episode")
        seen.add(seed["episode_id"])
        raw=path_loader(seed)
        path,error=clean_path(raw,seed["entry_at"],asof,seed["session_end"],max_gap)
        if error:
            rejects[error]+=1
            continue
        for h in HORIZONS:
            group=[p for p in policies if p["minutes"]==h]
            # Require complete fixed-time coverage for this horizon even if a
            # bracket happens to exit early; comparable cohorts are essential.
            base,err=simulate(path,group[0],cost_pct,latency,max_gap)
            if err:
                rejects["{}m:{}".format(h,err)]+=1
                continue
            results={}
            for p in group:
                outcome,err=simulate(path,p,cost_pct,latency,max_gap)
                if err:
                    break
                results[p["name"]]=outcome
            if err:
                rejects["{}m:{}".format(h,err)]+=1
                continue
            rows.append({"episode_id":seed["episode_id"],"session":seed["session"],
                         "symbol":seed["symbol"],"horizon":h,"outcomes":results,
                         "available_at":base["exit_at"],"path_sha256":sha(raw)})
    descriptive={p["name"]:stats(r["outcomes"][p["name"]] for r in rows
                                 if r["horizon"]==p["minutes"]) for p in policies}
    folds=[]
    # Session labels are supplied by the exchange adapter; never UTC day labels.
    for index in range(training_sessions+1,len(sessions),2):
        train=set(sessions[index-training_sessions-1:index-1])
        test=sessions[index]
        for h in HORIZONS:
            train_rows=[r for r in rows if r["session"] in train and r["horizon"]==h
                        and utc(r["available_at"])<min(utc(s["entry_at"]) for s in seeds if s["session"]==test)]
            test_rows=[r for r in rows if r["session"]==test and r["horizon"]==h]
            candidates={p["name"]:stats(r["outcomes"][p["name"]] for r in train_rows)
                        for p in policies if p["minutes"]==h}
            eligible=[k for k,v in candidates.items() if v["n"]>=minimum_samples and v["mean_net_pct"]>0]
            chosen=max(eligible,key=lambda k:candidates[k]["mean_net_pct"]) if eligible else None
            folds.append({"test_session":test,"horizon":h,"train_sessions":sorted(train),
                          "training_candidates":candidates,"selected_policy":chosen,
                          "test":stats(r["outcomes"][chosen] for r in test_rows) if chosen else stats([]),
                          "status":"evaluate" if chosen else "abstain"})
    return {"schema":SCHEMA,"broker":broker,"asof":asof,"cost_pct":cost_pct,
            "horizons":list(HORIZONS),"policies":policies,"seed_count":len(seeds),
            "session_count":len(sessions),"rejected":dict(rejects),"rows":rows,
            "descriptive":descriptive,"walk_forward":folds,"orders_enabled":False,
            "live_settings_changed":False,"seed_sha256":sha(seeds),
            "parameters":{"max_gap_seconds":max_gap,"latency_seconds":latency,
                          "training_sessions":training_sessions,"minimum_samples":minimum_samples},
            "limitations":["Research parameters, not promoted live settings.",
                           "Cohorts differ across horizons; compare families within each horizon.",
                           "No portfolio compounding, bid/ask queue or partial-fill model.",
                           "No global best horizon selected from held-out results."]}
