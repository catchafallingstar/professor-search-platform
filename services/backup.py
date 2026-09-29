"""Optional JSON copy of the MongoDB directory (download / upload on Staff > Pipeline)."""

import json

from services import store as st


def export_json():
    d = st.db()
    out = {"version": 2, "saved_at": st.now_iso()}
    for name in ("institutions", "professors", "papers", "grants"):
        out[name] = [dict(doc, _id=str(doc["_id"])) for doc in d[name].find({})]
    return json.dumps(out, default=str)


def import_json(content):
    try:
        data = json.loads(content)
    except ValueError:
        return {"ok": False, "message": "That file is not valid JSON."}
    if not isinstance(data, dict) or data.get("version") != 2:
        return {"ok": False, "message": "That file is not a Professor Atlas backup (version 2)."}
    d = st.db()
    counts = {}
    for name in ("institutions", "professors", "papers", "grants"):
        n = 0
        for doc in data.get(name) or []:
            if "_id" not in doc:
                continue
            d[name].replace_one({"_id": doc["_id"]}, doc, upsert=True)
            n += 1
        counts[name] = n
    st.invalidate_search()
    return {"ok": True, "message": f"Restored {counts['institutions']} universities, {counts['professors']} professors, {counts['papers']} papers."}
