"""Session-level purged and embargoed walk-forward split construction."""


SCHEMA = "purged_walk_forward_splits_v1"


def build_purged_splits(
    sessions,
    training_sessions,
    test_sessions=1,
    purge_sessions=0,
    embargo_sessions=1,
    expanding=True,
):
    """Build expanding-window splits with explicit purge and embargo gaps."""
    ordered = list(dict.fromkeys(sessions or ()))
    training_sessions = _positive_int(training_sessions, "training_sessions")
    test_sessions = _positive_int(test_sessions, "test_sessions")
    purge_sessions = _non_negative_int(purge_sessions, "purge_sessions")
    embargo_sessions = _non_negative_int(embargo_sessions, "embargo_sessions")
    splits = []
    test_start = training_sessions + purge_sessions
    split_id = 1
    while test_start + test_sessions <= len(ordered):
        train_end = test_start - purge_sessions
        train_start = 0 if expanding else max(0, train_end - training_sessions)
        train = ordered[train_start:train_end]
        purged = ordered[train_end:test_start]
        test = ordered[test_start:test_start + test_sessions]
        embargo = ordered[
            test_start + test_sessions:
            min(len(ordered), test_start + test_sessions + embargo_sessions)
        ]
        splits.append({
            "split_id": split_id,
            "train_sessions": train,
            "purged_sessions": purged,
            "test_sessions": test,
            "embargo_sessions": embargo,
            "train_end": train[-1] if train else None,
            "test_start": test[0] if test else None,
        })
        split_id += 1
        test_start += test_sessions + embargo_sessions
    return {
        "schema": SCHEMA,
        "session_count": len(ordered),
        "parameters": {
            "training_sessions": training_sessions,
            "test_sessions": test_sessions,
            "purge_sessions": purge_sessions,
            "embargo_sessions": embargo_sessions,
            "expanding": bool(expanding),
        },
        "split_count": len(splits),
        "splits": splits,
        "audit": audit_splits(splits),
    }


def audit_splits(splits):
    violations = []
    for split in splits or ():
        train = set(split.get("train_sessions") or ())
        test = set(split.get("test_sessions") or ())
        purged = set(split.get("purged_sessions") or ())
        embargo = set(split.get("embargo_sessions") or ())
        overlaps = {
            "train_test": train.intersection(test),
            "train_purged": train.intersection(purged),
            "test_embargo": test.intersection(embargo),
        }
        for name, values in overlaps.items():
            if values:
                violations.append({
                    "split_id": split.get("split_id"),
                    "type": name,
                    "sessions": sorted(values),
                })
    return {
        "passed": not violations,
        "violation_count": len(violations),
        "violations": violations,
    }


def _positive_int(value, name):
    result = int(value)
    if result <= 0:
        raise ValueError("{} must be positive".format(name))
    return result


def _non_negative_int(value, name):
    result = int(value)
    if result < 0:
        raise ValueError("{} must be non-negative".format(name))
    return result
