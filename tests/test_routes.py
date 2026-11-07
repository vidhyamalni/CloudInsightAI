"""Route-level tests: login, ownership, dashboard summary, history, analysis interfaces, assistant scope.

Run it on your machine:   python -m unittest tests.test_routes -v
Uses an in-memory SQLite database; Gemini is replaced by a stub; live pricing is off.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["DATABASE_URL"] = "sqlite://"
os.environ["PRICING_LIVE"] = "0"
os.environ["SECRET_KEY"] = "test"
os.environ.pop("GEMINI_API_KEY", None)

try:
    import flask_login, flask_sqlalchemy  # noqa: F401
    HAVE = True
except ImportError:
    HAVE = False

FORM = dict(company_name="ABC Technologies", industry="Technology", region="us-east-1", monthly_budget="800",
            storage_gb="500", expected_users="2000", compute_req="Moderate", data_sensitivity="Internal",
            availability="Standard", compliance="None")


@unittest.skipUnless(HAVE, "flask-login / flask-sqlalchemy not installed")
class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as appmod
        cls.m = appmod
        cls.app = appmod.app
        cls.app.config.update(TESTING=True, CSRF_ENABLED=False)   # CSRF itself is covered in test_security.py and the test below
        # stub Gemini: no network, deterministic (originals are restored in tearDownClass so other test modules are unaffected)
        cls._orig_ai = {n: getattr(cls.m.ai, n) for n in ("gemini_ready", "ask", "suitability_explanation", "last_error")}
        cls.m.ai.gemini_ready = lambda: True
        cls.m.ai.ask = lambda q, report, history: (True, f"stub answer ({'report' if report else 'general'}, {len(history)} prior)")
        cls.m.ai.suitability_explanation = lambda *a, **k: (False, None)
        cls.m.ai.last_error = lambda: None

    @classmethod
    def tearDownClass(cls):
        for n, fn in cls._orig_ai.items():
            setattr(cls.m.ai, n, fn)
        with cls.app.app_context():
            cls.m.db.session.remove()
            cls.m.db.engine.dispose()

    def setUp(self):
        with self.app.app_context():
            self.m.db.drop_all()
            self.m.db.create_all()

    def client(self, email, name="User"):
        c = self.app.test_client()
        c.post("/login", data=dict(action="register", name=name, email=email, password="password123"))
        return c

    def analyse(self, c, **extra):
        data = dict(FORM); data.update(extra)
        return c.post("/analysis", data=data)

    def test_dashboard_is_summary_only(self):
        c = self.client("a@x.com", "Asha Rao")
        self.analyse(c)
        html = c.get("/dashboard").get_data(as_text=True)
        self.assertIn("Welcome back, Asha", html)
        self.assertIn("Total Analyses", html)
        for banned in ("Estimated Monthly Cost", "Security Score", "Required Storage", "dashCost"):
            self.assertNotIn(banned, html)

    def test_new_analysis_saved_and_listed_newest_first(self):
        c = self.client("a@x.com")
        r1 = self.analyse(c, company_name="First Co")
        r2 = self.analyse(c, company_name="Second Co")
        self.assertEqual(r1.status_code, 302)
        html = c.get("/history").get_data(as_text=True)
        self.assertLess(html.index("Second Co"), html.index("First Co"))
        self.assertIn("Completed", html)
        self.assertIn("/report/1/pdf", html)

    def test_focus_pages_do_not_show_old_results(self):
        c = self.client("a@x.com")
        self.analyse(c)
        for url, word in (("/security", "Security Analysis"), ("/cost", "Cost Analysis"), ("/storage", "Storage Analysis")):
            html = c.get(url).get_data(as_text=True)
            self.assertIn(word, html)
            self.assertIn("analysis-form", html)
            self.assertNotIn("Provider cost comparison", html)
            self.assertNotIn("Pricing sources and timestamps", html)

    def test_focus_submit_redirects_to_matching_report_section(self):
        c = self.client("a@x.com")
        r = c.post("/analysis", data=dict(FORM, focus="cost"))
        self.assertTrue(r.headers["Location"].endswith("/report/1#sec-6"))
        r = c.post("/analysis", data=dict(FORM, focus="bogus"))
        self.assertTrue(r.headers["Location"].endswith("/report/2"))     # unknown focus is ignored

    def test_invalid_numbers_show_error_not_500(self):
        c = self.client("a@x.com")
        r = c.post("/analysis", data=dict(FORM, storage_gb="abc"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("must be a number", r.get_data(as_text=True))

    def test_two_users_are_isolated(self):
        a, b = self.client("a@x.com"), self.client("b@x.com")
        self.analyse(a, company_name="A Secret Co")
        self.assertEqual(b.get("/report/1").status_code, 404)
        self.assertEqual(b.get("/report/1/pdf").status_code, 404)
        self.assertNotIn("A Secret Co", b.get("/history").get_data(as_text=True))
        self.assertNotIn("A Secret Co", b.get("/security").get_data(as_text=True))
        self.assertEqual(b.get("/assistant?report=1").status_code, 404)
        r = b.post("/api/ai/ask", json=dict(question="hi", report_id=1))
        self.assertEqual(r.status_code, 404)

    def test_old_report_opens_and_pdf_downloads(self):
        c = self.client("a@x.com")
        self.analyse(c)
        self.analyse(c, company_name="Newer Co")
        self.assertIn("ABC Technologies", c.get("/report/1").get_data(as_text=True))
        pdf = c.get("/report/1/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.data.startswith(b"%PDF"))

    def test_assistant_free_form_followups_and_scopes(self):
        c = self.client("a@x.com")
        self.analyse(c)
        j1 = c.post("/api/ai/ask", json=dict(question="Explain VPC peering simply", report_id=0)).get_json()
        self.assertIn("general", j1["answer"]); self.assertIn("0 prior", j1["answer"])
        j2 = c.post("/api/ai/ask", json=dict(question="and why does it matter?", report_id=0)).get_json()
        self.assertIn("2 prior", j2["answer"])                      # follow-up sees the earlier turn
        j3 = c.post("/api/ai/ask", json=dict(question="why this provider?", report_id=1)).get_json()
        self.assertIn("report", j3["answer"]); self.assertIn("0 prior", j3["answer"])   # separate thread per report
        self.assertIn("Explain VPC peering", c.get("/assistant?report=0").get_data(as_text=True))
        self.assertNotIn("Explain VPC peering", c.get("/assistant?report=1").get_data(as_text=True))

    def test_csrf_blocks_forged_post_on_real_routes(self):
        c = self.client("a@x.com")
        self.app.config["CSRF_ENABLED"] = True
        try:
            self.assertEqual(c.post("/analysis", data=FORM).status_code, 400)
            self.assertEqual(c.post("/logout").status_code, 400)
            self.assertEqual(c.post("/api/ai/ask", json=dict(question="hi")).status_code, 400)
            html = c.get("/analysis").get_data(as_text=True)
            tok = __import__("re").search(r'name="csrf_token" value="([^"]+)"', html).group(1)
            self.assertEqual(c.post("/analysis", data=dict(FORM, csrf_token=tok)).status_code, 302)
        finally:
            self.app.config["CSRF_ENABLED"] = False

    def test_out_of_range_values_are_rejected_with_message(self):
        c = self.client("a@x.com")
        for bad in (dict(storage_gb="-5"), dict(storage_gb="999999999999"), dict(expected_users="0")):
            r = c.post("/analysis", data=dict(FORM, **bad))
            self.assertEqual(r.status_code, 200)
            self.assertIn("between", r.get_data(as_text=True))
        self.assertEqual(c.get("/history").get_data(as_text=True).count("ABC Technologies"), 0)   # nothing saved

    def test_logout_is_post_only_and_get_is_not_allowed(self):
        c = self.client("a@x.com")
        self.assertEqual(c.get("/logout").status_code, 405)
        self.assertEqual(c.post("/logout").status_code, 302)
        self.assertEqual(c.get("/dashboard").status_code, 302)          # logged out

    def test_login_next_cannot_redirect_off_site(self):
        self.client("a@x.com")
        c = self.app.test_client()
        r = c.post("/login?next=//evil.com", data=dict(action="login", email="a@x.com", password="password123"))
        self.assertEqual(r.headers["Location"], "/dashboard")

    def test_health_check_and_duration_recorded(self):
        c = self.client("a@x.com")
        self.assertEqual(c.get("/healthz").get_json(), {"status": "ok"})
        self.analyse(c)
        self.assertIn("analysis took", c.get("/report/1").get_data(as_text=True))

    def test_reports_url_redirects_to_history(self):
        c = self.client("a@x.com")
        self.assertEqual(c.get("/reports").headers["Location"], "/history")


if __name__ == "__main__":
    unittest.main(verbosity=2)