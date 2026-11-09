"""Tests for security.py: CSRF, headers, secret key, form validation, safe redirects. No database needed."""
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from flask import Flask, jsonify, render_template_string, request  # noqa: E402
import security                                                      # noqa: E402

GOOD = dict(company_name="ABC", industry="Technology", region="us-east-1", monthly_budget="800", storage_gb="500",
            expected_users="2000", compute_req="Moderate", data_sensitivity="Internal", availability="Standard",
            compliance="None")


def make_app():
    app = Flask("sec_test")
    app.secret_key = "t"
    security.init_security(app)

    @app.get("/page")
    def page():
        return render_template_string('<form method="post" action="/submit"><input name="csrf_token" value="{{ csrf_token() }}"></form>')

    @app.post("/submit")
    def submit():
        return "done"

    @app.post("/api/thing")
    def api():
        return jsonify(ok=True)

    @app.post("/contact")
    def contact():
        return jsonify(ok=True)
    return app


def token(client):
    html = client.get("/page").get_data(as_text=True)
    return re.search(r'value="([^"]+)"', html).group(1)


class TestCsrf(unittest.TestCase):
    def setUp(self):
        self.c = make_app().test_client()

    def test_post_without_token_is_blocked(self):
        token(self.c)                                    # session exists, token not sent
        r = self.c.post("/submit", data={})
        self.assertEqual(r.status_code, 400)
        self.assertIn("security token", r.get_data(as_text=True))

    def test_post_with_wrong_token_is_blocked(self):
        token(self.c)
        self.assertEqual(self.c.post("/submit", data={"csrf_token": "nope"}).status_code, 400)

    def test_post_with_form_token_is_allowed(self):
        self.assertEqual(self.c.post("/submit", data={"csrf_token": token(self.c)}).status_code, 200)

    def test_json_endpoints_use_header_and_return_json_error(self):
        t = token(self.c)
        bad = self.c.post("/api/thing", json={})
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(bad.get_json()["ok"])
        ok = self.c.post("/api/thing", json={}, headers={"X-CSRF-Token": t})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.c.post("/contact", json={}).status_code, 400)

    def test_token_from_another_session_is_rejected(self):
        t = token(self.c)
        other = make_app().test_client()
        token(other)
        self.assertEqual(other.post("/submit", data={"csrf_token": t}).status_code, 400)

    def test_get_requests_need_no_token(self):
        self.assertEqual(self.c.get("/page").status_code, 200)

    def test_csrf_can_be_disabled_for_tests_only(self):
        app = make_app(); app.config["CSRF_ENABLED"] = False
        self.assertEqual(app.test_client().post("/submit", data={}).status_code, 200)

    def test_security_headers_and_cookie_flags(self):
        r = self.c.get("/page")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        cookie = r.headers.get("Set-Cookie", "")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)

    def test_secure_cookie_flag_follows_environment(self):
        os.environ["SESSION_COOKIE_SECURE"] = "1"
        try:
            self.assertTrue(make_app().config.get("SESSION_COOKIE_SECURE"))
        finally:
            os.environ.pop("SESSION_COOKIE_SECURE")


class TestSecretKey(unittest.TestCase):
    def test_env_wins(self):
        os.environ["SECRET_KEY"] = "from-env"
        try:
            self.assertEqual(security.load_secret_key(tempfile.gettempdir()), "from-env")
        finally:
            os.environ.pop("SECRET_KEY")

    def test_generated_once_and_reused(self):
        os.environ.pop("SECRET_KEY", None)
        with tempfile.TemporaryDirectory() as d:
            k1 = security.load_secret_key(d)
            k2 = security.load_secret_key(d)
            self.assertEqual(k1, k2)
            self.assertGreaterEqual(len(k1), 32)
            self.assertNotEqual(k1, "dev-change-me-in-production")

    def test_unwritable_dir_still_returns_a_key(self):
        os.environ.pop("SECRET_KEY", None)
        self.assertGreaterEqual(len(security.load_secret_key("/proc/nonexistent/x")), 32)


class TestValidation(unittest.TestCase):
    def v(self, **kw):
        f = dict(GOOD); f.update(kw)
        return security.validate_analysis_form(f)

    def test_good_form_passes_and_values_are_strings(self):
        clean, err = self.v()
        self.assertIsNone(err)
        self.assertEqual(clean["storage_gb"], "500")
        self.assertEqual(clean["monthly_budget"], "800.0")

    def test_letters_rejected(self):
        self.assertIn("must be a number", self.v(storage_gb="abc")[1])
        self.assertIn("must be a number", self.v(monthly_budget="12x")[1])

    def test_negative_zero_and_huge_rejected(self):
        self.assertIn("between", self.v(storage_gb="-5")[1])
        self.assertIn("between", self.v(storage_gb="0")[1])
        self.assertIn("between", self.v(expected_users="999999999999")[1])
        self.assertIn("between", self.v(storage_gb="999999999999")[1])
        self.assertIn("between", self.v(monthly_budget="-1")[1])

    def test_nan_and_inf_rejected(self):
        for bad in ("nan", "inf", "-inf"):
            self.assertIsNotNone(self.v(storage_gb=bad)[1])

    def test_required_fields(self):
        self.assertIn("required", self.v(company_name="  ")[1])
        self.assertIn("required", self.v(storage_gb="")[1])
        self.assertIn("required", self.v(expected_users="")[1])
        self.assertIsNone(self.v(monthly_budget="")[1])                      # budget is optional
        self.assertEqual(self.v(monthly_budget="")[0]["monthly_budget"], "0")

    def test_long_name_rejected(self):
        self.assertIn("160", self.v(company_name="x" * 161)[1])

    def test_unknown_choices_fall_back_safely(self):
        clean, _ = self.v(industry="<script>", region="mars-1", compliance="FAKE")
        self.assertEqual((clean["industry"], clean["region"], clean["compliance"]),
                         ("Other", "Global / multi-region", "None"))

    def test_commas_and_decimals_in_numbers(self):
        clean, err = self.v(storage_gb="1,500", expected_users="2000.0")
        self.assertIsNone(err)
        self.assertEqual((clean["storage_gb"], clean["expected_users"]), ("1500", "2000"))

    def test_validated_form_runs_the_engine(self):
        os.environ["PRICING_LIVE"] = "0"
        import analysis_engine as engine
        clean, _ = self.v()
        self.assertGreater(engine.run_analysis(clean)["cost"]["primary"]["monthly"], 0)


class TestRedirectsAndDbUrl(unittest.TestCase):
    def test_safe_next(self):
        for ok in ("/dashboard", "/report/3", "/history?x=1"):
            self.assertTrue(security.is_safe_next(ok), ok)
        for bad in (None, "", "http://evil.com", "//evil.com", "/\\evil.com", "javascript:alert(1)", "evil.com", "https:/x"):
            self.assertFalse(security.is_safe_next(bad), bad)

    def test_normalize_db_url(self):
        self.assertEqual(security.normalize_db_url("postgres://u:p@h/db"), "postgresql+psycopg2://u:p@h/db")
        self.assertEqual(security.normalize_db_url("postgresql://u:p@h/db"), "postgresql+psycopg2://u:p@h/db")
        self.assertEqual(security.normalize_db_url("postgresql+psycopg2://u:p@h/db"), "postgresql+psycopg2://u:p@h/db")
        self.assertEqual(security.normalize_db_url("sqlite:///x.db"), "sqlite:///x.db")

if __name__ == "__main__":
    unittest.main(verbosity=2)
