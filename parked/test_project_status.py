"""Consistency tests for the Project Overview status data. Run from the project root:

    python -m unittest discover -s tests -v

These do not decide whether a feature is complete - a person does that by editing
data/project_status.json after verified work. They stop the file from contradicting itself or the
code base: unknown statuses, evidence files that do not exist, 'verified' features with no tests,
or cited test IDs that are not in the test suite.
"""
import os
import re
import sys
import unittest
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analysis_engine as engine          # noqa: E402
import project_status as ps               # noqa: E402

TEST_SRC = open(os.path.join(ROOT, "tests", "test_cost_pricing.py"), encoding="utf-8").read()
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class TestStatusData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = ps.load()
        cls.feats = cls.data["features"]

    def test_P01_statuses_are_known_and_ids_unique(self):
        known = set(self.data["status_order"])
        self.assertEqual(known, set(self.data["status_labels"]))
        ids = [f["id"] for f in self.feats]
        self.assertEqual(len(ids), len(set(ids)))
        for f in self.feats:
            self.assertIn(f["status"], known, f["id"])
            for key in ("name", "summary", "remaining", "verification", "evidence"):
                self.assertTrue(f.get(key) is not None, f"{f['id']} missing {key}")

    def test_P02_evidence_files_exist(self):
        for f in self.feats:
            for rel in f["evidence"]["code"]:
                self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), f"{f['id']}: {rel} not found")

    def test_P03_cited_tests_really_exist(self):
        for f in self.feats:
            for tid in f["evidence"]["tests"]:
                self.assertRegex(TEST_SRC, r"def test_%s_" % re.escape(tid), f"{f['id']} cites missing test {tid}")

    def test_P04_verified_requires_tests_and_a_date(self):
        for f in self.feats:
            if f["status"] == "completed_verified":
                self.assertTrue(f["evidence"]["tests"], f"{f['id']} is 'verified' but cites no tests")
                self.assertRegex(f["last_verified"] or "", DATE, f"{f['id']} needs a last_verified date")
            if f["last_verified"]:
                self.assertRegex(f["last_verified"], DATE)

    def test_P05_not_implemented_has_no_code_evidence(self):
        for f in self.feats:
            if f["status"] == "not_implemented":
                self.assertEqual(f["evidence"]["code"], [], f["id"])

    def test_P06_module_overview_references_resolve(self):
        ids = {f["id"] for f in self.feats}
        for m in self.data["modules_overview"]:
            self.assertTrue(m["status_refs"], m["name"])
            for r in m["status_refs"]:
                self.assertIn(r, ids, m["name"])

    def test_P07_roadmap_states_valid(self):
        self.assertEqual(len(self.data["roadmap"]), 12)
        for r in self.data["roadmap"]:
            self.assertIn(r["state"], ("completed", "ongoing", "pending"))

    def test_P08_summary_matches_documented_method(self):
        s = ps.summarize(self.data)
        self.assertEqual(s["total"], len(self.feats))
        self.assertEqual(sum(s["counts"].values()), s["total"])
        v, i = s["counts"]["completed_verified"], s["counts"]["needs_improvement"]
        self.assertEqual(s["verified_pct"], round(100 * v / s["total"]))
        self.assertEqual(s["implemented_pct"], round(100 * (v + i) / s["total"]))
        self.assertLess(s["verified_pct"], 100)      # never claim 100% without every module verified

    def test_P09_live_pricing_claim_matches_code(self):
        # The status text says live pricing exists for Azure only; fail if the code changes and this file does not.
        self.assertEqual(sorted(ps.live_pricing_providers()), ["Microsoft Azure"])


class TestOverviewPage(unittest.TestCase):
    """Renders the real template inside a minimal Flask app (no database needed)."""

    def _client(self, authed):
        from flask import Flask, render_template
        app = Flask(__name__, root_path=ROOT)
        for name, rule in (("index", "/"), ("login_route", "/login"), ("dashboard", "/dashboard"), ("analysis", "/analysis")):
            app.add_url_rule(rule, name, lambda: "")
        app.add_url_rule("/project-overview", "project_overview",
                         lambda: render_template("project_overview.html", **ps.build_context(engine.PROVIDERS)))

        @app.context_processor
        def _ctx():
            return dict(current_user=SimpleNamespace(is_authenticated=authed), brand="CloudInsight AI",
                        tagline="tagline", year=2026)
        return app.test_client()

    def test_P10_page_renders_publicly_with_all_sections(self):
        r = self._client(False).get("/project-overview")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        for needle in ("Project Overview", "Current project status", "Development roadmap", "Ideas, not features",
                       "Completed and Verified", "Not Yet Implemented", "<svg", "Log in or register"):
            self.assertIn(needle, html)
        for provider in engine.PROVIDERS:
            self.assertIn(provider, html)

    def test_P11_page_for_logged_in_user_links_to_dashboard(self):
        html = self._client(True).get("/project-overview").get_data(as_text=True)
        self.assertIn("Back to dashboard", html)

    def test_P12_page_exposes_no_secrets(self):
        html = self._client(False).get("/project-overview").get_data(as_text=True)
        for bad in ("gemini_key", "dev-change-me", "SECRET_KEY", "GEMINI_API_KEY", "password_hash"):
            self.assertNotIn(bad, html)


if __name__ == "__main__":
    unittest.main()
