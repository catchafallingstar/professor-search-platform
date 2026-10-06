"""Crawl one university right now (ahead of the queue) and print what discovery found.

    python services/run_crawl.py <institution _id> [--no-save]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import store as st, pipe  # noqa: E402


def main(iid, save=True):
    inst = st.get_institution(iid)
    print(f"== {inst['name']} ({inst.get('official_website')}) state={inst.get('pipeline_state')}", flush=True)
    t = time.time()
    if save:
        st.update_institution(iid, {"directories": [], "pipeline_state": "CRAWLING", "pipeline_note": ""})
        inst = st.get_institution(iid)
        added, report, disc = pipe.crawl(inst)
    else:
        found, disc = pipe.find_directories(inst)
        report, added = [{"department": d["department"], "url": d["url"], "found": d.get("name_count"),
                          "via": d.get("discovery_method")} for d in found], 0
    print(f"discovery status: {disc}  ({round(time.time() - t)} s)", flush=True)
    for r in report:
        print(f"  {r.get('department')!s:40.40} {r.get('found')!s:>4} found {r.get('added', '-')!s:>4} added  "
              f"{r.get('via')}  {r.get('url')}", flush=True)
    print(f"professors added: {added}", flush=True)
    if save:
        total = st.count_professors({"institution_id": iid})
        state = "PROCESSING" if total else ("STAFF_REVIEW" if disc == "STAFF_REVIEW" else "NO_FACULTY_FOUND")
        st.update_institution(iid, {"pipeline_state": state,
                                    "pipeline_note": "" if total else "Directory discovery found no faculty pages."})
        print(f"pipeline_state -> {state} ({total} professors)", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], save="--no-save" not in sys.argv)
