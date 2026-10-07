"""Academic-map diagnostics.
    python services/unit_probe.py <institution _id>          build the map (no save)
    python services/unit_probe.py --links <url>              show the links a page exposes
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import store as st, units, fetchers as fx  # noqa: E402


def links(url):
    page = fx.fetch_page(url)
    print("ok", page.get("ok"), page.get("via"), len(page.get("text", "")), flush=True)
    for t, u in re.findall(r"\[([^\]]{3,120})\]\((https?://[^)\s]+)\)", page.get("text", "")):
        if re.search(r"(?i)college|school|department|division|program|academ", t + " " + u):
            print(f"  {t[:70]!r:72} {u}", flush=True)


def main(iid):
    inst = st.get_institution(iid)
    t = time.time()
    unit_list, dirs, cov = units.build(dict(inst, units=[]), inst.get("directories") or [],
                                       log=lambda m: print("  log:", m, flush=True))
    print(f"== {inst['name']}: {len(unit_list)} units, {len(dirs)} directories, {round(time.time() - t)} s", flush=True)
    for u in unit_list:
        print(f"  [{u['type']:10}] {u['name'][:55]:55} {u['status']:18} parent={u.get('parent', '')[:30]} dirs={u.get('directories')}", flush=True)
    print("coverage:", {k: v for k, v in cov.items() if k != "missing"}, flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "--links":
        links(sys.argv[2])
    else:
        main(sys.argv[1])
