"""Staff tool: check which candidate department directory pages the faculty parser can read.

Usage (from project root):  python3 services/probe_dirs.py
Prints each URL with how many professor-rank faculty it found, and writes the readable
ones (5+ professors) to services/probe_result.json for adding to universities.py.
"""
import sys, os, json, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetchers as f

C = {
 "170976": [
  ["Robotics", "https://robotics.umich.edu/people/faculty/"],
  ["Mechanical Engineering", "https://me.engin.umich.edu/people/faculty/"],
  ["Aerospace Engineering", "https://aero.engin.umich.edu/people/faculty/"],
  ["Biomedical Engineering", "https://bme.umich.edu/people/faculty/"],
  ["Chemical Engineering", "https://che.engin.umich.edu/people/faculty/"],
  ["Civil and Environmental Engineering", "https://cee.engin.umich.edu/people/faculty/"],
  ["Industrial and Operations Engineering", "https://ioe.engin.umich.edu/people/faculty/"],
  ["Materials Science and Engineering", "https://mse.engin.umich.edu/people/faculty/"],
  ["Nuclear Engineering and Radiological Sciences", "https://ners.engin.umich.edu/people/faculty/"],
  ["Climate and Space Sciences", "https://clasp.engin.umich.edu/people/faculty/"],
  ["Statistics", "https://lsa.umich.edu/stats/people/faculty.html"],
  ["Mathematics", "https://lsa.umich.edu/math/people/faculty.html"],
  ["Physics", "https://lsa.umich.edu/physics/people/faculty.html"],
  ["Chemistry", "https://lsa.umich.edu/chem/people/faculty.html"],
  ["Economics", "https://lsa.umich.edu/econ/people/faculty.html"],
  ["Psychology", "https://lsa.umich.edu/psych/people/faculty.html"],
  ["School of Information", "https://www.si.umich.edu/people/directory/faculty"],
  ["Biostatistics", "https://sph.umich.edu/biostat/faculty-staff/"],
 ],
 "171100": [
  ["College of Engineering", "https://engineering.msu.edu/faculty"],
  ["Physics and Astronomy", "https://pa.msu.edu/people/faculty/"],
  ["Mathematics", "https://math.msu.edu/directory/faculty.aspx"],
  ["Statistics and Probability", "https://stt.natsci.msu.edu/people/faculty/"],
  ["Chemistry", "https://www.chemistry.msu.edu/faculty-research/faculty-members/"],
  ["Economics", "https://econ.msu.edu/people/faculty/"],
  ["Psychology", "https://psychology.msu.edu/people/faculty/"],
 ],
 "243780": [
  ["Computer Science", "https://www.cs.purdue.edu/people/faculty/index.html"],
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
  ["Physics and Astronomy", "https://www.physics.purdue.edu/people/faculty/"],
  ["Chemistry", "https://www.chem.purdue.edu/people/faculty/index.html"],
 ],
 "145637": [
  ["Computer Science", "https://siebelschool.illinois.edu/about/people/all-faculty"],
  ["Electrical and Computer Engineering", "https://ece.illinois.edu/about/directory/faculty"],
  ["Mechanical Science and Engineering", "https://mechse.illinois.edu/people/faculty"],
  ["Aerospace Engineering", "https://aerospace.illinois.edu/directory/faculty"],
  ["Civil and Environmental Engineering", "https://cee.illinois.edu/directory/faculty"],
  ["Materials Science and Engineering", "https://matse.illinois.edu/directory/faculty"],
  ["Bioengineering", "https://bioengineering.illinois.edu/directory/faculty"],
  ["Statistics", "https://stat.illinois.edu/directory/faculty"],
  ["Mathematics", "https://math.illinois.edu/directory/faculty"],
  ["Physics", "https://physics.illinois.edu/people/directory/faculty"],
 ],
 "147767": [
  ["Computer Science", "https://www.mccormick.northwestern.edu/computer-science/people/faculty/"],
  ["Electrical and Computer Engineering", "https://www.mccormick.northwestern.edu/electrical-computer/people/faculty/"],
  ["Mechanical Engineering", "https://www.mccormick.northwestern.edu/mechanical/people/faculty/"],
  ["Biomedical Engineering", "https://www.mccormick.northwestern.edu/biomedical/people/faculty/"],
  ["Chemical and Biological Engineering", "https://www.mccormick.northwestern.edu/chemical-biological/people/faculty/"],
  ["Civil and Environmental Engineering", "https://www.mccormick.northwestern.edu/civil-environmental/people/faculty/"],
  ["Industrial Engineering and Management Sciences", "https://www.mccormick.northwestern.edu/industrial/people/faculty/"],
  ["Materials Science and Engineering", "https://www.mccormick.northwestern.edu/materials-science/people/faculty/"],
 ],
 "174066": [
  ["Computer Science", "https://cse.umn.edu/cs/faculty"],
  ["Electrical and Computer Engineering", "https://cse.umn.edu/ece/faculty"],
  ["Mechanical Engineering", "https://cse.umn.edu/me/faculty"],
  ["Aerospace Engineering and Mechanics", "https://cse.umn.edu/aem/faculty"],
  ["Chemical Engineering and Materials Science", "https://cse.umn.edu/cems/faculty"],
  ["Civil, Environmental, and Geo- Engineering", "https://cse.umn.edu/cege/faculty"],
  ["Biomedical Engineering", "https://cse.umn.edu/bme/faculty"],
  ["Industrial and Systems Engineering", "https://cse.umn.edu/isye/faculty"],
  ["Mathematics", "https://cse.umn.edu/math/faculty"],
  ["Physics and Astronomy", "https://cse.umn.edu/physics/faculty"],
  ["Statistics", "https://cse.umn.edu/stat/faculty"],
  ["Chemistry", "https://cse.umn.edu/chem/faculty"],
 ],
 "172644": [
  ["Computer Science", "https://engineering.wayne.edu/computer-science/faculty"],
  ["Electrical and Computer Engineering", "https://engineering.wayne.edu/ece/faculty"],
  ["Mechanical Engineering", "https://engineering.wayne.edu/me/faculty"],
  ["Biomedical Engineering", "https://engineering.wayne.edu/bme/faculty"],
  ["Civil and Environmental Engineering", "https://engineering.wayne.edu/cee/faculty"],
  ["Chemical Engineering and Materials Science", "https://engineering.wayne.edu/chemical/faculty"],
  ["Industrial and Systems Engineering", "https://engineering.wayne.edu/ise/faculty"],
 ],
 "171128": [
  ["Computer Science", "https://www.mtu.edu/cs/department/faculty/"],
  ["Electrical and Computer Engineering", "https://www.mtu.edu/ece/department/faculty/"],
  ["Mechanical Engineering", "https://www.mtu.edu/mechanical/department/faculty/"],
  ["Civil, Environmental, and Geospatial Engineering", "https://www.mtu.edu/cege/department/faculty/"],
  ["Chemical Engineering", "https://www.mtu.edu/chemical/department/faculty/"],
  ["Materials Science and Engineering", "https://www.mtu.edu/materials/department/faculty/"],
  ["Biomedical Engineering", "https://www.mtu.edu/biomedical/department/faculty/"],
  ["Mathematical Sciences", "https://www.mtu.edu/math/department/faculty/"],
  ["Physics", "https://www.mtu.edu/physics/department/faculty/"],
 ],
}


def probe(item):
    ipeds, dept, url = item
    p = f.fetch_page(url)
    n = len(f.extract_faculty_rules(p, dept)) if p["ok"] else 0
    return ipeds, dept, url, p["ok"], n


if __name__ == "__main__":
    items = [(k, d, u) for k, v in C.items() for d, u in v]
    good = {}
    with cf.ThreadPoolExecutor(8) as ex:
        for ipeds, dept, url, ok, n in ex.map(probe, items):
            print(f"{ipeds} ok={str(ok):5} n={n:4}  {dept}  {url}")
            if n >= 5:
                good.setdefault(ipeds, []).append([dept, url])
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe_result.json")
    json.dump(good, open(out, "w"), indent=1)
    print("GOOD:", {k: len(v) for k, v in good.items()})
