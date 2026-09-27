"""Diagnostic helper: reports which storage drivers the runtime can load."""

import importlib


def drivers():
    out = {}
    for mod in ("pymongo", "redis", "motor"):
        try:
            m = importlib.import_module(mod)
            out[mod] = getattr(m, "__version__", "ok")
        except Exception as e:
            out[mod] = f"missing ({type(e).__name__})"
    return out


if __name__ == "__main__":
    print(drivers())
