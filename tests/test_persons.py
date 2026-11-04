"""Offline tests for the persons package.

Payloads are SYNTHETIC: they follow the field and link shapes that
sources/gtr.py documents as confirmed against live GtR responses, but the
content is invented. They test our logic, not GtR. Real-data verification
happens in the GitHub Action (see `run_persons.py --probe`).

Run:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from persons import pipeline  # noqa: E402
from persons.build import lead_is_company  # noqa: E402
from persons.extract import extract_grant, extract_roles, grants_from_projects  # noqa: E402
from persons.regions import in_london_cambridge  # noqa: E402

TODAY = date(2026, 11, 4)
BASE = "http://gtr.ukri.org/gtr/api"


def ms(y: int, m: int = 1, d: int = 1) -> int:
    from datetime import datetime, timezone
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp() * 1000)


def project(pid, title, *, funder="Innovate UK", category="Feasibility Studies",
            lead="ORG-IMP", pi=None, cois=(), students=(), pms=(), others=(), start=ms(2025, 6)):
    links = [{"rel": "LEAD_ORG", "href": f"{BASE}/organisations/{lead}"}]
    links += [{"rel": "PARTICIPANT_ORG", "href": f"{BASE}/organisations/{o}"} for o in others]
    if pi:
        links.append({"rel": "PI_PER", "href": f"{BASE}/persons/{pi}"})
    links += [{"rel": "COI_PER", "href": f"{BASE}/persons/{c}"} for c in cois]
    links += [{"rel": "STUDENT_PER", "href": f"{BASE}/persons/{s}"} for s in students]
    links += [{"rel": "PM_PER", "href": f"{BASE}/persons/{m}"} for m in pms]
    links.append({"rel": "FUND", "start": start, "end": start + 10**10})
    return {
        "id": pid, "title": title, "leadFunder": funder, "grantCategory": category,
        "abstractText": "A microfluidic diagnostic for early sepsis detection.",
        "identifiers": {"identifier": [{"type": "RCUK", "value": f"REF-{pid}"}]},
        "links": {"link": links},
    }


ORGS = {
    "ORG-IMP": {"name": "Imperial College London", "has_regnum": False, "reg_number": ""},
    "ORG-LEEDS": {"name": "University of Leeds", "has_regnum": False, "reg_number": ""},
    "ORG-CO": {"name": "Acme Diagnostics Ltd", "has_regnum": True, "reg_number": "01234567"},
    "ORG-CAM": {"name": "University of Cambridge", "has_regnum": False, "reg_number": ""},
}
NAMES = {"P-A": "Ada Alpha", "P-B": "Ben Beta", "P-C": "Cy Gamma", "P-D": "Di Delta",
         "P-E": "Eve Echo", "P-S": "Stu Student", "P-M": "Pam Manager"}


def cache():
    return {"organisations": {k: dict(v) for k, v in ORGS.items()}, "persons": dict(NAMES)}


class ExtractTests(unittest.TestCase):
    def test_roles_preserved_and_students_pms_ignored(self):
        p = project("1", "Microfluidic diagnostic", pi="P-A", cois=["P-B"], students=["P-S"], pms=["P-M"])
        self.assertEqual(sorted(extract_roles(p)), [("P-A", "PI"), ("P-B", "Co-I")])

    def test_pi_wins_when_also_listed_as_coi(self):
        p = project("1", "Microfluidic diagnostic", pi="P-A", cois=["P-A", "P-B"])
        self.assertEqual(dict(extract_roles(p))["P-A"], "PI")

    def test_out_of_domain_and_personless_dropped(self):
        off = project("2", "Medieval poetry", pi="P-A")
        off["abstractText"] = "Poetry."
        self.assertIsNone(extract_grant(off))
        self.assertIsNone(extract_grant(project("3", "Microfluidic diagnostic")))

    def test_grant_fields(self):
        g = extract_grant(project("1", "Microfluidic diagnostic", pi="P-A", others=["ORG-LEEDS"]))
        self.assertEqual((g.track, g.funder, g.n_orgs, g.lead_org_id), ("commercial", "Innovate UK", 2, "ORG-IMP"))
        self.assertEqual(g.start, date(2025, 6, 1))
        self.assertTrue(g.url.endswith("REF-1"))


class HelperTests(unittest.TestCase):
    def test_company_detection(self):
        self.assertTrue(lead_is_company("EPSRC", ORGS["ORG-CO"]))
        self.assertFalse(lead_is_company("EPSRC", ORGS["ORG-IMP"]))
        # Innovate UK lead with no suffix, no regnum, no academic marker -> inferred company
        self.assertTrue(lead_is_company("Innovate UK", {"name": "VETERINARY ISOTHERMAL DIAGNOSTICS", "has_regnum": False}))
        self.assertFalse(lead_is_company("Innovate UK", ORGS["ORG-IMP"]))

    def test_region(self):
        self.assertTrue(in_london_cambridge("Imperial College London"))
        self.assertTrue(in_london_cambridge("University of Cambridge"))
        self.assertFalse(in_london_cambridge("University of Leeds"))
        self.assertFalse(in_london_cambridge("University College Cork"))  # \bucl\b / UCL name must not over-match


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.projects = [
            # P-A: academic PI, two recent commercial grants at Imperial
            project("1", "Microfluidic diagnostic", pi="P-A", cois=["P-B"], start=ms(2025, 6)),
            project("2", "Point-of-care biosensor diagnostic", pi="P-A", start=ms(2024, 3)),
            # P-C: academic PI, studentship only, Leeds
            project("3", "Lab-on-chip diagnostic", funder="EPSRC", category="Studentship",
                    lead="ORG-LEEDS", pi="P-C", start=ms(2025, 1)),
            # P-D: PI on a company-led grant -> company-linked
            project("4", "Microfluidic diagnostic", lead="ORG-CO", pi="P-D", cois=["P-E"], start=ms(2025, 9)),
            # P-E also Co-I at Cambridge via another project (PI P-E on Cambridge grant? no: Co-I only on company)
            # P-B Co-I only at Imperial project
        ]
        self.grants = grants_from_projects(self.projects)

    def run_pipeline(self, **kw):
        return pipeline.run(self.grants, cache(), today=TODAY,
                            fetch_org=lambda _i: None, fetch_name=lambda _t, _i: None, **kw)

    def test_identity_by_uuid_and_company_flag(self):
        r = self.run_pipeline()
        ids = {p.person_id for p in r.ranked}
        self.assertEqual(r.total_people, 5)
        self.assertNotIn("P-D", ids)
        self.assertEqual([p.person_id for p in r.company_linked], ["P-D"])

    def test_ranking_is_differentiated_and_sensible(self):
        r = self.run_pipeline()
        by = {p.person_id: p for p in r.ranked}
        self.assertGreater(by["P-A"].score, by["P-C"].score)   # commercial repeat PI > studentship PI
        self.assertGreater(by["P-A"].score, by["P-B"].score)   # PI > Co-I on same project
        # Different evidence -> different scores; identical evidence (B and E: one Co-I role on one
        # commercial grant each) legitimately ties. Guards against a flat ranking, not against ties.
        self.assertEqual(by["P-B"].score, by["P-E"].score)
        self.assertEqual(len({p.score for p in r.ranked}), 3)
        self.assertTrue(all(p.score_reasons for p in r.ranked))

    def test_coi_on_company_project_is_flagged_ambiguous_not_scored_or_penalised(self):
        r = self.run_pipeline()
        self.assertEqual([p.person_id for p in r.company_linked], ["P-D"])   # only the PI is company-linked
        e = next(p for p in r.ranked if p.person_id == "P-E")
        self.assertTrue(any("may be an academic collaborator or company staff" in x for x in e.score_reasons))
        self.assertFalse(e.company_linked)
        self.assertFalse(any("industry link" in x for x in e.score_reasons))

    def test_institutions_only_from_pi_role(self):
        r = self.run_pipeline()
        by = {p.person_id: p for p in r.ranked}
        self.assertEqual(by["P-A"].institutions, ["Imperial College London"])
        self.assertEqual(by["P-B"].institutions, [])   # Co-I only: unknown, not guessed

    def test_region_filter_keeps_only_known_in_region_pis(self):
        r = self.run_pipeline(region="london-cambridge")
        self.assertEqual({p.person_id for p in r.ranked}, {"P-A"})
        self.assertEqual(r.total_people, 5)   # total counted before the filter

    def test_stale_and_future_dates(self):
        old = grants_from_projects([project("9", "Microfluidic diagnostic", pi="P-A", start=ms(2015, 1))])
        fut = grants_from_projects([project("8", "Microfluidic diagnostic", pi="P-A", start=ms(2028, 1))])
        now = grants_from_projects([project("7", "Microfluidic diagnostic", pi="P-A", start=ms(2026, 1))])
        score = lambda g: pipeline.run(g, cache(), today=TODAY, fetch_org=lambda _i: None,
                                       fetch_name=lambda _t, _i: None).ranked[0].score
        self.assertLess(score(old), score(fut))
        self.assertLess(score(fut), score(now))   # future-dated is capped, not treated as fresh


class RegionTests(unittest.TestCase):
    def test_edinburgh_names(self):
        from persons.regions import in_regions
        for name in ("University of Edinburgh", "Heriot-Watt University", "Edinburgh Napier University",
                     "The Roslin Institute", "NHS Lothian"):
            self.assertTrue(in_regions(name, ["edinburgh"]), name)
        # Bare "Edinburgh" must not match companies; other cities must not match.
        for name in ("Edinburgh Instruments Ltd", "University of Glasgow", "University of Bristol"):
            self.assertFalse(in_regions(name, ["edinburgh"]), name)
        self.assertFalse(in_regions("University of Edinburgh", ["london-cambridge"]))

    def test_parse_regions(self):
        from persons.regions import parse_regions
        self.assertEqual(parse_regions("all"), [])
        self.assertEqual(parse_regions(""), [])
        self.assertEqual(parse_regions("london-cambridge, Edinburgh"), ["london-cambridge", "edinburgh"])
        self.assertEqual(parse_regions("edinburgh,edinburgh"), ["edinburgh"])
        with self.assertRaises(ValueError):
            parse_regions("london-cambridge,glasgow")

    def test_pipeline_combines_regions(self):
        orgs = dict(ORGS)
        orgs["ORG-EDI"] = {"name": "University of Edinburgh", "has_regnum": False, "reg_number": ""}
        c = {"organisations": orgs, "persons": dict(NAMES)}
        grants = grants_from_projects([
            project("1", "Microfluidic diagnostic", pi="P-A", lead="ORG-IMP"),
            project("2", "Microfluidic diagnostic", pi="P-C", lead="ORG-EDI"),
            project("3", "Microfluidic diagnostic", pi="P-E", lead="ORG-LEEDS"),
        ])
        run = lambda region: {p.person_id for p in pipeline.run(
            grants, c, region=region, today=TODAY,
            fetch_org=lambda _i: None, fetch_name=lambda _t, _i: None).ranked}
        self.assertEqual(run("edinburgh"), {"P-C"})
        self.assertEqual(run("london-cambridge"), {"P-A"})
        self.assertEqual(run("london-cambridge,edinburgh"), {"P-A", "P-C"})
        self.assertEqual(run("all"), {"P-A", "P-C", "P-E"})


class InnovateUKWeightTests(unittest.TestCase):
    def _score(self, funder):
        g = grants_from_projects([project("1", "Microfluidic diagnostic", funder=funder,
                                          category="Research Grant", pi="P-A", start=ms(2025, 6))])
        r = pipeline.run(g, cache(), today=TODAY, fetch_org=lambda _i: None, fetch_name=lambda _t, _i: None)
        return r.ranked[0]

    def test_innovate_uk_outranks_identical_research_council_grant(self):
        from persons.score import INNOVATE_UK_BONUS
        iuk, epsrc = self._score("Innovate UK"), self._score("EPSRC")
        self.assertAlmostEqual(iuk.score - epsrc.score, INNOVATE_UK_BONUS, places=2)
        self.assertTrue(any("Innovate UK funder" in r for r in iuk.score_reasons))
        self.assertFalse(any("Innovate UK funder" in r for r in epsrc.score_reasons))

    def test_innovate_uk_does_not_rescue_a_company_led_pi(self):
        # An Innovate UK grant led by a company still leaves its PI company-linked and demoted.
        g = grants_from_projects([project("4", "Microfluidic diagnostic", lead="ORG-CO", pi="P-D")])
        r = pipeline.run(g, cache(), today=TODAY, fetch_org=lambda _i: None, fetch_name=lambda _t, _i: None)
        self.assertEqual([p.person_id for p in r.company_linked], ["P-D"])
        self.assertEqual(r.ranked, [])


class CliTests(unittest.TestCase):
    def test_unknown_region_is_rejected(self):
        r = subprocess.run([sys.executable, str(ROOT / "run_persons.py"), "--region", "glasgow"],
                           capture_output=True, text=True, cwd=ROOT,
                           env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("Unknown region", r.stderr)

    def test_offline_fixture_run_writes_report_without_touching_state(self):
        projects = [
            project("1", "Microfluidic diagnostic", pi="P-A", cois=["P-B"], pms=["P-M"], students=["P-S"]),
            project("4", "Microfluidic diagnostic", lead="ORG-CO", pi="P-D"),
        ]
        with tempfile.TemporaryDirectory() as td:
            fx, out = Path(td) / "fx.json", Path(td) / "out.md"
            fx.write_text(json.dumps({"projects": projects, "organisations": ORGS, "persons": NAMES}))
            seen = ROOT / "state" / "persons_seen.json"
            before = seen.exists()
            r = subprocess.run([sys.executable, str(ROOT / "run_persons.py"),
                                "--fixture", str(fx), "--out", str(out)],
                               capture_output=True, text=True, cwd=ROOT,
                               env={"PYTHONPATH": str(ROOT), "PATH": "/usr/bin:/bin"})
            self.assertEqual(r.returncode, 0, r.stderr)
            text = out.read_text()
            self.assertIn("Ada Alpha", text)
            self.assertIn("Likely already in a company", text)
            self.assertNotIn("Pam Manager", text)   # PM / student links are never person records
            self.assertEqual(seen.exists(), before)


if __name__ == "__main__":
    unittest.main()
