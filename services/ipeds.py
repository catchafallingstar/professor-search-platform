"""University list from the official IPEDS "Institutional Characteristics" file (NCES).

https://nces.ed.gov/ipeds/datacenter/data/HD<year>.zip -> HD<year>.csv, one row per
institution: UNITID (the IPEDS id), INSTNM, CITY, STABBR, WEBADDR, C21BASIC (Carnegie
basic classification). We load the research universities that actually employ research
faculty: Carnegie 15 = R1 (very high research), 16 = R2 (high research), ~280 schools.

Queue order: Michigan first, then the surrounding states, then everything else
(R1 before R2). The hand-picked list in universities.py only adds known faculty-directory
URLs; the institutions themselves come from IPEDS.
"""

import csv
import io
import time
import urllib.request
import zipfile

NCES = "https://nces.ed.gov/ipeds/datacenter/data/HD{year}.zip"
CARNEGIE = {"15": "R1: very high research", "16": "R2: high research"}
NEIGHBORS = {"OH", "IN", "IL", "WI", "MN"}


def _download():
    this_year = time.gmtime().tm_year
    for year in range(this_year - 1, this_year - 6, -1):
        try:
            req = urllib.request.Request(NCES.format(year=year), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()
            z = zipfile.ZipFile(io.BytesIO(raw))
            name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            return year, z.read(name).decode("latin-1")
        except Exception as e:
            print(f"[ipeds] HD{year} not available: {e}")
    raise RuntimeError("Could not download any IPEDS HD file from nces.ed.gov")


def research_universities():
    """Returns (year, rows) where rows are dicts ready for store.upsert_institution."""
    year, text = _download()
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for r in reader:
        r = {k.lstrip("\ufeff").lstrip("ï»¿").strip(): (v or "").strip() for k, v in r.items()}
        basic = r.get("C21BASIC") or r.get("C18BASIC") or ""
        if basic not in CARNEGIE:
            continue
        state = r.get("STABBR", "")
        tier = 1 if state == "MI" else 2 if state in NEIGHBORS else 3
        web = r.get("WEBADDR", "")
        if web and not web.startswith("http"):
            web = "https://" + web.strip("/")
        out.append({
            "ipeds_id": r.get("UNITID", ""), "name": r.get("INSTNM", ""), "city": r.get("CITY", ""),
            "state": state, "official_website": web, "carnegie": CARNEGIE[basic],
            "priority_tier": tier, "_r1": basic == "15",
        })
    out.sort(key=lambda x: (x["priority_tier"], not x["_r1"], x["name"]))
    for i, row in enumerate(out):
        row["priority_rank"] = i + 1
        row["priority_reason"] = {1: "Michigan", 2: "Surrounding state"}.get(row["priority_tier"], "Research university") + " (" + row["carnegie"] + ")"
        row.pop("_r1")
    return year, out
