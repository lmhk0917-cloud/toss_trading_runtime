"""Tiny .env loader for local smoke scripts.

This intentionally avoids printing or returning secret values.
"""

import os


def load_local_env(paths=None):
    loaded = []
    for path in paths or [".env.local", ".env"]:
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        loaded.append(path)
    return loaded

