"""Template-level tests for the dashboard / history / analysis-interface / assistant / pricing views.

These render the real templates with a bare Flask app and plain objects, so they run WITHOUT a database
or flask-login. They check what each page shows (and, just as important, does not show). They do not
exercise login, ownership or SQL - that is tests/test_routes.py.
"""
import json
import os
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PRICING_LIVE"] = "0"

from flask import Flask                      # noqa: E402
import analysis_engine as engine             # noqa: E402
import ui_filters                            # noqa: E402
import security                              # noqa: E402

FORM = dict(company_name="ABC Technologies", industry="Technology", region="us-east-1", monthly_budget="800",
            storage_gb="500", expected_users="2000", compute_req="Moderate", data_sensitivity="Internal",
            availability="Standard", compliance="None")


def make_app():
    app = Flask("ui_test", root_path=ROOT, template_folder=os.path.join(ROOT, "templates"),
                static_folder=os.path.join(ROOT, "static"))
    app.secret_key = "t"
    ui_filters.register(app)
    security.init_security(app)
    app.add_template_filter(lambda v: f"${float(v):,.2f}", "money")
    for ep, rule in [("index", "/"), ("dashboard", "/dashboard"), ("analysis", "/analysis"), ("assistant", "/assistant"),
                     ("history", "/history"), ("security_page", "/security"), ("cost_page", "/cost"),
                     ("storage_page", "/storage"), ("profile", "/profile"), ("logout", "/logout"),
                     ("report", "/report/<int:rid>"), ("report_pdf", "/report/<int:rid>/pdf")]:
        app.add_url_rule(rule, ep, lambda **k: "")
    user = SimpleNamespace(name="Vidhya N", email="v@example.com")
    app.context_processor(lambda: dict(current_user=user, brand="CloudInsight AI", tagline="t",
                                       year=2026, PCONFIG=engine.PROVIDERS))
    return app


def analysis_row(i, company, when, has=True):
    return SimpleNamespace(id=i, company_name=company, provider="Google Cloud", industry="Technology",
                           region="eu-west-1", monthly_budget=800.0, storage_gb=500, expected_users=2000,
                           compute_req="Moderate", data_sensitivity="Internal", availability_req="Standard",
                           compliance_req="None", created_at=when, ai_insights=None, has_report=has,
                           status="Completed" if has else "Incomplete", title=f"Cloud Analysis — {company}")


class UiBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = make_app()

    def render(self, name, **ctx):
        with self.app.test_request_context("/"):
            from flask import render_template
            return render_template(name, **ctx)


class TestDashboard(UiBase):
    def test_summary_only_no_detailed_results(self):
        recent = [analysis_row(2, "Beta Ltd", datetime(2026, 11, 7, 11, 0)), analysis_row(1, "ABC Technologies", datetime(2026, 10, 1, 9, 30))]
        stats = dict(total=2, reports=2, this_month=1, ai_insights=0, ai_questions=3, top_provider="Google Cloud",
                     last=recent[0].created_at)
        html = self.render("dashboard.html", stats=stats, recent=recent)
        self.assertIn("Welcome back, Vidhya", html)
        for label in ("Total Analyses", "Reports Generated", "Start New Analysis", "AI Assistant", "Analysis History"):
            self.assertIn(label, html)
        for banned in ("Estimated Monthly Cost", "Security Score", "Required Storage", "Compute Cost",
                       "dashCost", "dashRisk", "trendChart", "Storage Usage"):
            self.assertNotIn(banned, html)
        self.assertIn("Beta Ltd", html)
        self.assertIn('method="post" action="/logout"', html)                 # logout is a POST form (CSRF-safe)
        self.assertIn('name="csrf_token"', html)

    def test_empty_state_for_new_user(self):
        stats = dict(total=0, reports=0, this_month=0, ai_insights=0, ai_questions=0, top_provider=None, last=None)
        html = self.render("dashboard.html", stats=stats, recent=[])
        self.assertIn("No cloud analysis yet", html)
        self.assertIn("Start New Analysis", html)


class TestHistory(UiBase):
    def test_columns_date_month_year_time_status(self):
        rows = [analysis_row(2, "ABC Technologies", datetime(2026, 11, 7, 16, 30)),
                analysis_row(1, "Old Co", datetime(2026, 3, 2, 8, 5), has=False)]
        html = self.render("history.html", rows=rows)
        for text in ("07 November 2026", "November", "2026", "04:30 PM", "Completed", "Incomplete", "Available",
                     "Unavailable", "ABC Technologies", "2026-11-07T16:30:00Z", "Download", "Open"):
            self.assertIn(text, html)
        self.assertLess(html.index("ABC Technologies"), html.index("Old Co"))     # order given = newest first
        self.assertIn("/report/2/pdf", html)
        self.assertNotIn("/report/1/pdf", html)                                    # no download for an incomplete record

    def test_history_shows_inputs_not_results(self):
        html = self.render("history.html", rows=[analysis_row(5, "X", datetime(2026, 1, 1, 0, 0))])
        self.assertIn("Inputs only", html)
        for banned in ("Estimated Monthly Cost", "Compute Cost", "Security Score"):
            self.assertNotIn(banned, html)

    def test_empty_history(self):
        self.assertIn("No cloud analysis yet", self.render("history.html", rows=[]))


class TestAnalysisInterfaces(UiBase):
    def test_focus_pages_show_form_and_no_report_content(self):
        for focus, title, anchor_word in (("security", "Security Analysis", "security"),
                                          ("cost", "Cost Analysis", "cost"), ("storage", "Storage Analysis", "storage")):
            html = self.render("analysis.html", focus=focus, page_title=title, page_sub="s", button="Go",
                               profiles=[dict(id=1, company_name="ABC", industry="Technology", region="us-east-1",
                                              monthly_budget=800.0, storage_gb=500, expected_users=2000,
                                              compute_req="Moderate", data_sensitivity="Internal",
                                              availability="Standard", compliance="None")])
            self.assertIn(title, html)
            self.assertIn(f'name="focus" value="{focus}"', html)
            self.assertIn('id="profileSelect"', html)
            self.assertIn("not an old result", html)
            self.assertIn("COMPANY_PROFILES", html)
            self.assertIn('name="csrf_token"', html)
            self.assertIn('id="loadTimer"', html)                              # real elapsed-time counter
            self.assertNotIn("Evaluating storage requirements", html)          # old fake progress text is gone
            for banned in ("Provider cost comparison", "Pricing sources", "Security Score", "kpis"):
                self.assertNotIn(banned, html)

    def test_plain_analysis_page_has_no_focus_field(self):
        html = self.render("analysis.html", profiles=[])
        self.assertNotIn('name="focus"', html)
        self.assertNotIn("profileSelect", html)
        self.assertIn("Analyze Cloud Environment", html)


class TestAssistant(UiBase):
    def _r(self):
        return SimpleNamespace(id=7, company_name="ABC Technologies", provider="Google Cloud")

    def test_general_mode_has_avatar_scope_and_freeform_input(self):
        html = self.render("assistant.html", r=None, msgs=[], options=[analysis_row(7, "ABC Technologies", datetime(2026, 11, 7, 1, 0))],
                           gemini_ok=True, suggestions=["a"])
        self.assertIn('id="aiAvatar"', html)
        self.assertIn('data-report="0"', html)
        self.assertIn("General cloud questions", html)
        self.assertIn("Type your own question", html)
        self.assertIn("ABC Technologies", html)

    def test_report_mode_marks_selected_report(self):
        html = self.render("assistant.html", r=self._r(), msgs=[SimpleNamespace(role="user", content="hi"),
                                                                 SimpleNamespace(role="assistant", content="hello <b>x</b>")],
                           options=[analysis_row(7, "ABC Technologies", datetime(2026, 11, 7, 1, 0))],
                           gemini_ok=False, suggestions=["a"])
        self.assertIn('data-report="7"', html)
        self.assertIn("selected", html)
        self.assertIn("Offline mode", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)            # chat content is escaped, never raw HTML

    def test_avatar_animation_respects_reduced_motion(self):
        css = open(os.path.join(ROOT, "static", "css", "style.css"), encoding="utf-8").read()
        self.assertIn("avFloat", css)
        self.assertRegex(css, r"prefers-reduced-motion:reduce\)\{\.ai-avatar")


class TestPricingLabels(UiBase):
    def _html(self, d):
        return self.render("partials/_cost.html", d=d)

    def test_coverage_table_and_component_status_for_new_report(self):
        d = engine.run_analysis(FORM)
        html = self._html(d)
        self.assertIn("Where each provider's prices come from", html)
        self.assertIn("Official price API in this app", html)
        self.assertIn("No &mdash; stored estimate only", html)
        self.assertEqual(d["pricing"]["live_supported"], {"Microsoft Azure": ["storage", "compute"]})
        self.assertNotIn("badge-ok", html)            # live switched off: no price may carry the "live" badge

    def test_azure_mixed_when_live_storage_compute_but_estimated_egress(self):
        d = engine.run_analysis(FORM)
        azure = next(c for c in d["cost"]["comparison"] if c["provider"] == "Microsoft Azure")
        azure["component_status"] = dict(storage="live", compute="live", network="estimated")
        azure["pricing_status"] = "mixed"
        html = self._html(d)
        self.assertIn("Storage: <b class=\"st-live\">live</b>", html)
        self.assertIn("Egress: <b class=\"st-estimated\">estimated</b>", html)
        for other in ("AWS", "Google Cloud", "IBM Cloud", "Oracle Cloud", "Tencent Cloud"):
            self.assertEqual(next(c for c in d["cost"]["comparison"] if c["provider"] == other)["pricing_status"], "estimated")

    def test_old_report_without_new_fields_still_renders(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "old_report.json")) as fh:
            old = json.load(fh)
        self.assertIn("older report", self._html(old).lower())
        d = engine.run_analysis(FORM)
        d["pricing"].pop("live_supported")
        self.assertIn("not recorded for this report", self._html(d))


class TestAssistantPrompt(unittest.TestCase):
    def test_prompt_is_freeform_multi_turn_and_grounded(self):
        import ai_service as ai
        hist = [dict(role="user", content="What is egress?"), dict(role="assistant", content="Outbound data transfer.")]
        txt = ai._format_history(hist)
        self.assertIn("User: What is egress?", txt)
        self.assertIn("Assistant: Outbound data transfer.", txt)
        self.assertIn("first message", ai._format_history([]))
        self.assertIn("Never invent prices", ai.ASK_PROMPT)
        self.assertIn("{history}", ai.ASK_PROMPT)

    def test_ask_sends_history_and_report_context_to_model(self):
        import ai_service as ai
        d = engine.run_analysis(FORM)
        seen = {}
        orig_ready, orig_call = ai.gemini_ready, ai._call_gemini
        ai.gemini_ready = lambda: True
        ai._call_gemini = lambda prompt: seen.setdefault("p", prompt) and "  A natural answer.  "
        try:
            ok, text = ai.ask("and for Azure?", d, [dict(role="user", content="Why Google?")])
            self.assertTrue(ok)
            self.assertEqual(text, "A natural answer.")
            self.assertIn("User: Why Google?", seen["p"])
            self.assertIn("and for Azure?", seen["p"])
            self.assertIn("ABC Technologies", seen["p"])
            seen.clear()
            ok, _ = ai.ask("What is S3?", None, [])
            self.assertIn("No report is selected", seen["p"])
            self.assertNotIn("ABC Technologies", seen["p"])
        finally:
            ai.gemini_ready, ai._call_gemini = orig_ready, orig_call

    def test_gemini_error_is_recorded_without_leaking_the_key(self):
        import ai_service as ai
        os.environ["GEMINI_API_KEY"] = "SECRETKEY123"
        try:
            ai._record(RuntimeError("401 bad key SECRETKEY123"), "SECRETKEY123")
            self.assertNotIn("SECRETKEY123", ai.last_error())
            self.assertIn("***", ai.last_error())
        finally:
            os.environ.pop("GEMINI_API_KEY", None)


class TestTimeFilters(unittest.TestCase):
    def test_filters(self):
        dt = datetime(2026, 11, 7, 16, 30, 5)
        self.assertEqual(ui_filters.d_date(dt), "07 November 2026")
        self.assertEqual(ui_filters.d_time(dt), "04:30 PM")
        self.assertEqual(ui_filters.utc_iso(dt), "2026-11-07T16:30:05Z")
        self.assertEqual(ui_filters.utc_iso(None), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
