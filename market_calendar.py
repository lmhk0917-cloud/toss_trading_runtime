"""Market-session helpers for Toss calendar responses."""

from datetime import datetime, time, timezone


def current_us_session(calendar_response, now=None):
    session = _current_session(calendar_response, ["dayMarket", "preMarket", "regularMarket", "afterMarket"], now=now)
    if session.get("session") != "closed":
        return session
    inferred = _infer_us_overnight_kst_session(now=now, known_sessions=session.get("known_sessions") or [])
    return inferred or session


def current_kr_session(calendar_response, now=None):
    result = (calendar_response or {}).get("result") or {}
    today = result.get("today") or {}
    integrated = today.get("integrated") or {}
    session = _current_session({"result": {"today": integrated}}, ["preMarket", "regularMarket", "afterMarket"], now=now)
    if session["session"] == "closed":
        session["market"] = "KR"
    else:
        session["market"] = "KRX+NXT"
    return session


def _current_session(calendar_response, session_names, now=None):
    result = (calendar_response or {}).get("result") or {}
    today = result.get("today") or {}
    now_dt = now or datetime.now(timezone.utc).astimezone()
    sessions = []
    for name in session_names:
        session = today.get(name)
        if not session:
            continue
        start = _parse_time(session.get("startTime"))
        end = _parse_time(session.get("endTime"))
        item = {
            "session": name,
            "startTime": session.get("startTime"),
            "endTime": session.get("endTime"),
            "is_open": bool(start and end and start <= now_dt <= end),
        }
        sessions.append(item)
        if item["is_open"]:
            return item
    return {
        "session": "closed",
        "startTime": None,
        "endTime": None,
        "is_open": False,
        "known_sessions": sessions,
    }


def _parse_time(value):
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _infer_us_overnight_kst_session(now=None, known_sessions=None):
    now_dt = now or datetime.now(timezone.utc).astimezone()
    if now_dt.tzinfo is None:
        local_dt = now_dt
    else:
        local_dt = now_dt.astimezone()
    # Toss US calendar can return the next KST trading window after midnight,
    # while the prior US regular/after-hours session is still in progress.
    if not _known_sessions_are_future(local_dt, known_sessions or []):
        return None
    weekday = local_dt.isoweekday()
    current_time = local_dt.time()
    if weekday in (2, 3, 4, 5, 6) and time(0, 0) <= current_time < time(5, 0):
        return {
            "session": "regularMarket",
            "startTime": None,
            "endTime": None,
            "is_open": True,
            "inferred": True,
            "reason": "kst_overnight_previous_us_regular_session",
            "known_sessions": known_sessions or [],
        }
    if weekday in (2, 3, 4, 5, 6) and time(5, 0) <= current_time < time(8, 50):
        return {
            "session": "afterMarket",
            "startTime": None,
            "endTime": None,
            "is_open": True,
            "inferred": True,
            "reason": "kst_overnight_previous_us_after_market",
            "known_sessions": known_sessions or [],
        }
    return None


def _known_sessions_are_future(now_dt, known_sessions):
    starts = [_parse_time(item.get("startTime")) for item in known_sessions if item.get("startTime")]
    starts = [item.astimezone(now_dt.tzinfo) if item.tzinfo and now_dt.tzinfo else item for item in starts]
    if not starts:
        return False
    return min(starts) > now_dt
