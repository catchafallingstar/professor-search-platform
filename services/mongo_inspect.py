"""Inspect (and optionally clean) the MongoDB cluster behind MONGODB_URI.

List every database and collection with its size:
    .jac/venv/bin/python services/mongo_inspect.py

Drop whole databases you have confirmed are useless (e.g. Atlas sample data):
    .jac/venv/bin/python services/mongo_inspect.py --drop sample_mflix sample_airbnb

Databases this app may use are protected and are never dropped by this script.
"""

import os
import sys

from pymongo import MongoClient

PROTECTED = {"admin", "local", "config"}
SAMPLE_PREFIX = "sample_"


def client():
    uri = os.environ.get("MONGODB_URI", "").strip()
    if not uri:
        sys.exit("MONGODB_URI is not set (Settings > Environment, then restart the preview).")
    return MongoClient(uri, serverSelectionTimeoutMS=15000)


def report(c):
    total = 0.0
    for name in sorted(c.list_database_names()):
        db = c[name]
        try:
            s = db.command("dbstats")
        except Exception as e:
            print(f"{name}: cannot read stats ({e})")
            continue
        mb = s.get("dataSize", 0) / 1e6
        total += mb
        tag = "  <- Atlas sample data" if name.startswith(SAMPLE_PREFIX) else ""
        print(f"DB {name}: {mb:.1f} MB data, {s.get('indexSize', 0) / 1e6:.1f} MB indexes{tag}")
        for col in sorted(db.list_collection_names()):
            try:
                cs = db.command("collstats", col)
                print(f"    {col}: {cs.get('count', 0)} docs, {cs.get('size', 0) / 1e6:.2f} MB")
            except Exception as e:
                print(f"    {col}: cannot read stats ({str(e)[:80]})")
    print(f"Total data: {total:.1f} MB")


def drop(c, names):
    for name in names:
        if name in PROTECTED:
            print(f"skip {name}: system database")
            continue
        if not name.startswith(SAMPLE_PREFIX) and "--force" not in sys.argv:
            print(f"skip {name}: not a sample_* database (add --force if you are sure)")
            continue
        c.drop_database(name)
        print(f"dropped {name}")


if __name__ == "__main__":
    c = client()
    if "--drop" in sys.argv:
        drop(c, [a for a in sys.argv[sys.argv.index("--drop") + 1:] if not a.startswith("--")])
    report(c)
