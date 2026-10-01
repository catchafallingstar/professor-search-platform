"""University priority queue.

tier 1 = Michigan, tier 2 = surrounding states (OH, IN, IL, WI, MN),
tier 3 = top 30 national. Tier 4 (everything else) is pulled from OpenAlex
automatically once tiers 1-3 are fully processed, including hiring.

"dirs" = official faculty directory pages [department, url]. Where empty, the
LLM proposes directory URLs on the university's own domain and each one is
fetched to verify before use. Add URLs here for any school that shows
NO_FACULTY_FOUND (some sites block automated requests).
"""

TIER_REASON = {1: "Michigan", 2: "Surrounding state", 3: "Top 30 national", 4: "Expansion"}


def _u(name, city, state, web, ipeds, ror, tier, dirs=None):
    return {"name": name, "city": city, "state": state, "web": web, "ipeds": ipeds,
            "ror": ror, "tier": tier, "dirs": dirs or []}


PRIORITY = [
    # ---- Tier 1: Michigan ----
    # Directory URLs below were fetched and parsed successfully from the sandbox.
    _u("University of Michigan", "Ann Arbor", "MI", "https://umich.edu", "170976", "00jmfr291", 1, [
        # Most umich.edu pages sit behind a bot check; fetch_page reads them through the reader fallback.
        ["Robotics", "https://robotics.umich.edu/people/faculty/"],
        ["Computer Science and Engineering", "https://cse.engin.umich.edu/people/faculty/"],
        ["Electrical and Computer Engineering", "https://ece.engin.umich.edu/people/directory/faculty/"],
        ["Mechanical Engineering", "https://me.engin.umich.edu/people/faculty/"],
        ["Aerospace Engineering", "https://aero.engin.umich.edu/people/faculty/"],
        ["Chemical Engineering", "https://che.engin.umich.edu/people/faculty/"],
        ["Civil and Environmental Engineering", "https://cee.engin.umich.edu/people/faculty/"],
        ["Industrial and Operations Engineering", "https://ioe.engin.umich.edu/people/faculty/"],
        ["Materials Science and Engineering", "https://mse.engin.umich.edu/people/faculty/"],
        ["Nuclear Engineering and Radiological Sciences", "https://ners.engin.umich.edu/people/faculty/"],
        ["Biomedical Engineering", "https://bme.umich.edu/people/faculty/"],
        ["Climate and Space Sciences and Engineering", "https://clasp.engin.umich.edu/people/faculty/"],
        ["Naval Architecture and Marine Engineering", "https://name.engin.umich.edu/people/faculty/"],
        ["Mathematics", "https://lsa.umich.edu/math/people/faculty.html"],
        ["Statistics", "https://lsa.umich.edu/stats/people/faculty.html"],
        ["Physics", "https://lsa.umich.edu/physics/people/faculty.html"],
        ["Chemistry", "https://lsa.umich.edu/chem/people/faculty.directory.html"],
        ["Astronomy", "https://lsa.umich.edu/astro/people/faculty.html"],
        ["Economics", "https://lsa.umich.edu/econ/people/faculty.html"],
        ["Psychology", "https://lsa.umich.edu/psych/people/faculty.directory.html"],
        ["Molecular, Cellular, and Developmental Biology", "https://lsa.umich.edu/mcdb/people/faculty.html"],
        ["Ecology and Evolutionary Biology", "https://lsa.umich.edu/eeb/people/faculty.html"],
        ["Earth and Environmental Sciences", "https://lsa.umich.edu/earth/people/faculty.html"],
        ["Linguistics", "https://lsa.umich.edu/linguistics/people/faculty.html"],
        ["School of Information", "https://www.si.umich.edu/people/directory/faculty"],
    ]),
    _u("Michigan State University", "East Lansing", "MI", "https://msu.edu", "171100", "05hs6h993", 1, [
        ["College of Engineering", "https://engineering.msu.edu/faculty"],
    ]),
    _u("Wayne State University", "Detroit", "MI", "https://wayne.edu", "172644", "01070mq45", 1, [
        ["Computer Science", "https://engineering.wayne.edu/computer-science/faculty"],
    ]),
    _u("Michigan Technological University", "Houghton", "MI", "https://www.mtu.edu", "171128", "0036rpn28", 1, [
        ["Computer Science", "https://www.mtu.edu/cs/department/faculty/"],
    ]),
    _u("Western Michigan University", "Kalamazoo", "MI", "https://wmich.edu", "172699", "04j198w64", 1, [
        ["Computer Science", "https://wmich.edu/cs/directory"],
    ]),
    _u("Oakland University", "Rochester", "MI", "https://www.oakland.edu", "171571", "01ythxj32", 1),
    # ---- Tier 2: surrounding states ----
    _u("Purdue University", "West Lafayette", "IN", "https://www.purdue.edu", "243780", "02dqehb95", 2, [
        ["Computer Science", "https://www.cs.purdue.edu/people/faculty/index.html"],
    ]),
    _u("Indiana University Bloomington", "Bloomington", "IN", "https://www.indiana.edu", "151351", "01kg8sb98", 2),
    _u("University of Notre Dame", "Notre Dame", "IN", "https://www.nd.edu", "152080", "00mkhxb43", 2),
    _u("Ohio State University", "Columbus", "OH", "https://www.osu.edu", "204796", "00rs6vg23", 2),
    _u("Case Western Reserve University", "Cleveland", "OH", "https://case.edu", "201645", "051fd9666", 2),
    _u("University of Cincinnati", "Cincinnati", "OH", "https://www.uc.edu", "201885", "01e3m7079", 2),
    _u("University of Illinois Urbana-Champaign", "Champaign", "IL", "https://illinois.edu", "145637", "047426m28", 2, [
        ["Computer Science", "https://siebelschool.illinois.edu/about/people/all-faculty"],
    ]),
    _u("Northwestern University", "Evanston", "IL", "https://www.northwestern.edu", "147767", "000e0be47", 2, [
        ["Computer Science", "https://www.mccormick.northwestern.edu/computer-science/people/faculty/"],
    ]),
    _u("University of Chicago", "Chicago", "IL", "https://www.uchicago.edu", "144050", "024mw5h28", 2),
    _u("University of Wisconsin-Madison", "Madison", "WI", "https://www.wisc.edu", "240444", "01y2jtd41", 2),
    _u("University of Minnesota Twin Cities", "Minneapolis", "MN", "https://twin-cities.umn.edu", "174066", "017zqws13", 2, [
        ["Computer Science", "https://cse.umn.edu/cs/faculty"],
    ]),
    # ---- Tier 3: top 30 national ----
    _u("Massachusetts Institute of Technology", "Cambridge", "MA", "https://www.mit.edu", "166683", "042nb2s44", 3),
    _u("Stanford University", "Stanford", "CA", "https://www.stanford.edu", "243744", "00f54p054", 3),
    _u("Harvard University", "Cambridge", "MA", "https://www.harvard.edu", "166027", "03vek6s52", 3),
    _u("California Institute of Technology", "Pasadena", "CA", "https://www.caltech.edu", "110404", "05dxps055", 3),
    _u("Princeton University", "Princeton", "NJ", "https://www.princeton.edu", "186131", "00hx57361", 3),
    _u("Yale University", "New Haven", "CT", "https://www.yale.edu", "130794", "03v76x132", 3),
    _u("Columbia University", "New York", "NY", "https://www.columbia.edu", "190150", "00hj8s172", 3),
    _u("University of Pennsylvania", "Philadelphia", "PA", "https://www.upenn.edu", "215062", "00b30xv10", 3),
    _u("Johns Hopkins University", "Baltimore", "MD", "https://www.jhu.edu", "162928", "00za53h95", 3),
    _u("Duke University", "Durham", "NC", "https://duke.edu", "198419", "00py81415", 3),
    _u("Brown University", "Providence", "RI", "https://www.brown.edu", "217156", "05gq02987", 3),
    _u("Cornell University", "Ithaca", "NY", "https://www.cornell.edu", "190415", "05bnh6r87", 3),
    _u("Rice University", "Houston", "TX", "https://www.rice.edu", "227757", "008zs3103", 3),
    _u("Dartmouth College", "Hanover", "NH", "https://home.dartmouth.edu", "182670", "049s0rh22", 3),
    _u("Vanderbilt University", "Nashville", "TN", "https://www.vanderbilt.edu", "221999", "02vm5rt34", 3),
    _u("Washington University in St. Louis", "St. Louis", "MO", "https://wustl.edu", "179867", "01yc7t268", 3),
    _u("University of California, Berkeley", "Berkeley", "CA", "https://www.berkeley.edu", "110635", "01an7q238", 3),
    _u("University of California, Los Angeles", "Los Angeles", "CA", "https://www.ucla.edu", "110662", "046rm7j60", 3),
    _u("Carnegie Mellon University", "Pittsburgh", "PA", "https://www.cmu.edu", "211440", "05x2bcf33", 3),
    _u("Georgia Institute of Technology", "Atlanta", "GA", "https://www.gatech.edu", "139755", "01zkghx44", 3),
    _u("Emory University", "Atlanta", "GA", "https://www.emory.edu", "139658", "03czfpz43", 3),
    _u("University of Virginia", "Charlottesville", "VA", "https://www.virginia.edu", "234076", "0153tk833", 3),
    _u("University of Southern California", "Los Angeles", "CA", "https://www.usc.edu", "123961", "03taz7m60", 3),
    _u("University of California San Diego", "La Jolla", "CA", "https://ucsd.edu", "110680", "0168r3w48", 3),
    _u("University of Texas at Austin", "Austin", "TX", "https://www.utexas.edu", "228778", "00hj54h04", 3),
    _u("University of Washington", "Seattle", "WA", "https://www.washington.edu", "236948", "00cvxb145", 3),
    _u("New York University", "New York", "NY", "https://www.nyu.edu", "193900", "0190ak572", 3),
    _u("University of North Carolina at Chapel Hill", "Chapel Hill", "NC", "https://www.unc.edu", "199120", "0130frc33", 3),
]

# Extra directory pages verified from the sandbox (fetched + rule parser found 5+ professors).
# Keyed by IPEDS id; merged into PRIORITY below.
EXTRA_DIRS = {
    "171571": [["Engineering and Computer Science", "https://www.oakland.edu/secs/directory/"]],
    "166683": [["Electrical Engineering and Computer Science", "https://www.eecs.mit.edu/role/faculty/"]],
    "243744": [["Computer Science", "https://www.cs.stanford.edu/people/faculty"]],
    "166027": [["Computer Science", "https://seas.harvard.edu/computer-science/people"]],
    "110404": [["Computing and Mathematical Sciences", "https://www.cms.caltech.edu/people"]],
    "186131": [["Computer Science", "https://www.cs.princeton.edu/people/faculty"]],
    "190150": [["Computer Science", "https://www.cs.columbia.edu/people/faculty/"]],
    "162928": [["Computer Science", "https://www.cs.jhu.edu/faculty/"]],
    "198419": [["Computer Science", "https://cs.duke.edu/people/faculty"]],
    "190415": [["Computer Science", "https://www.cs.cornell.edu/people/faculty"]],
    "182670": [["Computer Science", "https://web.cs.dartmouth.edu/people"]],
    "110635": [["Electrical Engineering and Computer Sciences", "https://www2.eecs.berkeley.edu/Faculty/Lists/CS/faculty.html"]],
    "211440": [["Computer Science", "https://csd.cmu.edu/people/faculty"]],
    "139755": [["Computing", "https://www.cc.gatech.edu/people/faculty"]],
    "139658": [["Computer Science", "https://www.cs.emory.edu/people/faculty/"]],
    "236948": [["Computer Science and Engineering", "https://www.cs.washington.edu/people/faculty"]],
    "193900": [["Computer Science", "https://cs.nyu.edu/dynamic/people/faculty/"]],
}

# More department directories, verified with services/probe_dirs.py (each parsed 5+ professors).
EXTRA_DIRS.update({
    "243780": [  # Purdue (Computer Science is already in PRIORITY)
        ["Electrical and Computer Engineering", "https://engineering.purdue.edu/ECE/People/Faculty"],
        ["Mechanical Engineering", "https://engineering.purdue.edu/ME/People/Faculty"],
        ["Aeronautics and Astronautics", "https://engineering.purdue.edu/AAE/people/faculty"],
        ["Chemical Engineering", "https://engineering.purdue.edu/ChE/people/faculty"],
        ["Civil Engineering", "https://engineering.purdue.edu/CE/People/Faculty"],
        ["Industrial Engineering", "https://engineering.purdue.edu/IE/people/faculty"],
        ["Biomedical Engineering", "https://engineering.purdue.edu/BME/People/Faculty"],
        ["Materials Engineering", "https://engineering.purdue.edu/MSE/people/faculty"],
        ["Mathematics", "https://www.math.purdue.edu/people/faculty.html"],
        ["Statistics", "https://www.stat.purdue.edu/people/faculty/"],
        ["Chemistry", "https://www.chem.purdue.edu/people/faculty/index.html"],
    ],
    "145637": [  # UIUC (Computer Science already listed)
        ["Electrical and Computer Engineering", "https://ece.illinois.edu/about/directory/faculty"],
        ["Mechanical Science and Engineering", "https://mechse.illinois.edu/people/faculty"],
        ["Aerospace Engineering", "https://aerospace.illinois.edu/directory/faculty"],
        ["Civil and Environmental Engineering", "https://cee.illinois.edu/directory/faculty"],
        ["Bioengineering", "https://bioengineering.illinois.edu/directory/faculty"],
        ["Statistics", "https://stat.illinois.edu/directory/faculty"],
        ["Mathematics", "https://math.illinois.edu/directory/faculty"],
        ["Physics", "https://physics.illinois.edu/people/directory/faculty"],
    ],
    "147767": [  # Northwestern (Computer Science already listed)
        ["Mechanical Engineering", "https://www.mccormick.northwestern.edu/mechanical/people/faculty/"],
        ["Biomedical Engineering", "https://www.mccormick.northwestern.edu/biomedical/people/faculty/"],
        ["Chemical and Biological Engineering", "https://www.mccormick.northwestern.edu/chemical-biological/people/faculty/"],
        ["Civil and Environmental Engineering", "https://www.mccormick.northwestern.edu/civil-environmental/people/faculty/"],
        ["Industrial Engineering and Management Sciences", "https://www.mccormick.northwestern.edu/industrial/people/faculty/"],
        ["Materials Science and Engineering", "https://www.mccormick.northwestern.edu/materials-science/people/faculty/"],
    ],
    "174066": [  # Minnesota (Computer Science already listed)
        ["Mechanical Engineering", "https://cse.umn.edu/me/faculty"],
        ["Biomedical Engineering", "https://cse.umn.edu/bme/faculty"],
        ["Industrial and Systems Engineering", "https://cse.umn.edu/isye/faculty"],
        ["Mathematics", "https://cse.umn.edu/math/faculty"],
    ],
    "171128": [  # Michigan Tech (Computer Science already listed)
        ["Electrical and Computer Engineering", "https://www.mtu.edu/ece/department/faculty/"],
        ["Mechanical Engineering", "https://www.mtu.edu/mechanical/department/faculty/"],
        ["Chemical Engineering", "https://www.mtu.edu/chemical/department/faculty/"],
        ["Materials Science and Engineering", "https://www.mtu.edu/materials/department/faculty/"],
        ["Biomedical Engineering", "https://www.mtu.edu/biomedical/department/faculty/"],
        ["Mathematical Sciences", "https://www.mtu.edu/math/department/faculty/"],
        ["Physics", "https://www.mtu.edu/physics/department/faculty/"],
    ],
})

# Humanities, arts and social sciences (all departments are in scope, not just STEM).
# UMich LSA serves the full list on faculty.directory.html; each verified to parse 8+ professors.
_LSA = "https://lsa.umich.edu/{}/people/faculty.directory.html"
EXTRA_DIRS.setdefault("170976", []).extend([[d, _LSA.format(s)] for d, s in [
    ("English Language and Literature", "english"), ("History", "history"), ("Philosophy", "philosophy"),
    ("Political Science", "polisci"), ("Sociology", "soc"), ("Romance Languages and Literatures", "rll"),
    ("Germanic Languages and Literatures", "german"), ("Slavic Languages and Literatures", "slavic"),
    ("Asian Languages and Cultures", "asian"), ("American Culture", "ac"), ("Comparative Literature", "complit"),
    ("History of Art", "histart"), ("Film, Television, and Media", "ftvm"), ("Linguistics", "linguistics"),
    ("Economics", "econ"), ("Physics", "physics"), ("Statistics", "stats"),
]])

for _row in PRIORITY:
    _have = {u for _, u in _row["dirs"]}
    _row["dirs"] = _row["dirs"] + [d for d in EXTRA_DIRS.get(_row["ipeds"], []) if d[1] not in _have]
