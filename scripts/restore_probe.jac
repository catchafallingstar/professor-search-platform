"""Diagnostic: time the pieces of a directory restore outside the web server.
Run: jac run scripts/restore_probe.jac"""

import time;
import services.snapshot as snap;
import from services.models { DataRoot, Institution, Professor, Paper, HasInstitution, HasProfessor, Authored, HasPaperIndex, get_data_root }
import from services.access { _registry }

with entry {
    t0 = time.time();
    raw = snap.load();
    print(f"snapshot load: {round(time.time() - t0, 1)}s");
    t0 = time.time();
    data = get_data_root();
    print(f"get_data_root: {round(time.time() - t0, 2)}s");
    t0 = time.time();
    reg = _registry();
    print(f"_registry: {round(time.time() - t0, 2)}s");
    t0 = time.time();
    n = 0;
    for row in raw["institutions"][:3] {
        inst = Institution(name=str(row["name"]));
        data +>:HasInstitution():+> inst;
        for pr in row["professors"][:40] {
            p = Professor(name=str(pr["name"]));
            inst +>:HasProfessor():+> p;
            n += 1;
            for pd in (pr.get("papers", []) or [])[:10] {
                pa = Paper(title=str(pd.get("title", "")));
                data +>:HasPaperIndex():+> pa;
                p +>:Authored():+> pa;
            }
        }
    }
    print(f"{n} professors with papers: {round(time.time() - t0, 1)}s");
}
