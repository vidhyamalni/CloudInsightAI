"""Automated tests for Issue 1 (cost estimation). Run from the project root:

    python -m unittest discover -s tests -v

No internet is needed: the Azure HTTP call is replaced by a SYNTHETIC fixture that imitates the
shape of the Azure Retail Prices API. These tests prove the code's logic (parsing, caching, fallback,
labelling). They do NOT prove that Azure's real API currently returns what the fixture returns -
see tests/live_azure_check.py for the one-off real-network check.
"""
import json
import os
import re
import sys
import tempfile
import time
import unittest
import urllib.error
from urllib.parse import unquote

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import analysis_engine as engine          # noqa: E402
import azure_pricing                      # noqa: E402
import pricing_service as ps              # noqa: E402

FORM = dict(company_name="Test Co", industry="Fintech", region="us-east-1", monthly_budget="500",
            storage_gb="1000", expected_users="5000", compute_req="Moderate", data_sensitivity="Internal",
            availability="Standard", compliance="None")

# --- SYNTHETIC Azure-shaped data (not a recorded real response) ---------------------------------
STORAGE_PRICE = {"eastus": 0.0184, "northeurope": 0.0196, "japaneast": 0.0206}
VM_PRICE = {"eastus": 0.0416, "northeurope": 0.0457, "japaneast": 0.0536}


def _item(region, product, sku, meter, unit, price, tier=0, cur="USD"):
    return dict(currencyCode=cur, retailPrice=price, armRegionName=region, productName=product, skuName=sku,
                meterName=meter, unitOfMeasure=unit, tierMinimumUnits=tier, effectiveStartDate="2026-01-01T00:00:00Z")


def fake_azure(url, timeout):
    q = unquote(url)
    region = re.search(r"armRegionName eq '([^']+)'", q).group(1)
    if "serviceName eq 'Storage'" in q:
        r = region
        items = [_item(r, "General Block Blob v2", "Hot LRS", "Hot LRS Data Stored", "1 GB/Month", STORAGE_PRICE[r], 0),
                 _item(r, "General Block Blob v2", "Hot LRS", "Hot LRS Data Stored", "1 GB/Month", STORAGE_PRICE[r] * 0.96, 50),
                 _item(r, "Hierarchical Namespace General Block Blob v2", "Hot LRS", "Hot LRS Data Stored", "1 GB/Month", 0.019, 0),
                 _item(r, "Premium Block Blob", "Premium LRS", "Hot LRS Data Stored", "1 GB/Month", 0.15, 0)]
    else:
        sku = re.search(r"armSkuName eq '([^']+)'", q).group(1)
        assert sku == "Standard_B2s"
        items = [_item(region, "Virtual Machines BS Series", "B2s", "B2s", "1 Hour", VM_PRICE[region]),
                 _item(region, "Virtual Machines BS Series Windows", "B2s", "B2s", "1 Hour", 0.0592),
                 _item(region, "Virtual Machines BS Series", "B2s Spot", "B2s Spot", "1 Hour", 0.0123)]
    return {"Items": items, "NextPageLink": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PRICING_CACHE_PATH"] = os.path.join(self.tmp.name, "cache.json")
        os.environ["PRICING_LIVE"] = "0"
        os.environ.pop("PRICING_TIMEOUT", None)
        ps._breaker.clear()
        self._orig_http = azure_pricing._http_get_json
        self.calls = []

    def tearDown(self):
        azure_pricing._http_get_json = self._orig_http
        self.tmp.cleanup()
        os.environ.pop("PRICING_CACHE_PATH", None)
        os.environ["PRICING_LIVE"] = "0"

    def go_live(self, handler=fake_azure):
        os.environ["PRICING_LIVE"] = "1"

        def wrapped(url, timeout):
            self.calls.append(url)
            return handler(url, timeout)
        azure_pricing._http_get_json = wrapped

    def run_engine(self, **kw):
        f = dict(FORM)
        f.update({k: str(v) for k, v in kw.items()})
        return engine.run_analysis(f)


class TestBaseline(Base):
    def test_C01_every_provider_has_every_component(self):
        b = ps.load_baseline(force=True)
        self.assertEqual(set(b["providers"]), set(engine.PROVIDERS))
        for name, comps in b["providers"].items():
            for c in ps.COMPONENTS:
                self.assertIn(c, comps, f"{name} missing {c}")
                self.assertGreater(ps._base_value(c, comps[c]), 0)
                self.assertTrue(comps[c].get("source_url"), f"{name}/{c} has no source_url")

    def test_C02_no_prices_hardcoded_in_engine_code(self):
        src = open(os.path.join(ROOT, "analysis_engine.py"), encoding="utf-8").read()
        self.assertIsNone(re.search(r"(storage_rate|hourly|egress_rate)\s*=\s*[0-9]", src))
        self.assertNotIn("0.004", src)       # the old invented per-user network constant


class TestOfflineAnalysis(Base):
    def test_C03_everything_labelled_estimated_never_live(self):
        d = self.run_engine()
        self.assertEqual(d["pricing"]["overall_status"], "estimated")
        self.assertEqual(d["meta"]["pricing_status"], "estimated")
        for comps in d["pricing"]["providers"].values():
            for q in comps.values():
                self.assertEqual(q["status"], "estimated")
                self.assertNotEqual(q["status"], "live")
        for row in d["cost"]["comparison"]:
            self.assertEqual(row["pricing_status"], "estimated")

    def test_C04_monthly_annual_and_parts_are_consistent(self):
        d = self.run_engine()
        for r in d["cost"]["comparison"]:
            self.assertAlmostEqual(r["monthly"], round(r["storage"] + r["compute"] + r["network"], 2), places=2)
            self.assertAlmostEqual(r["annual"], round(r["monthly"] * 12, 2), places=2)
        for key in ("providers",):
            for c in d["compute"][key]:
                self.assertAlmostEqual(c["annual"], round(c["monthly"] * 12, 2), places=2)
            for s in d["storage"][key]:
                self.assertAlmostEqual(s["annual"], round(s["monthly"] * 12, 2), places=2)
        p = d["cost"]["primary"]
        self.assertAlmostEqual(p["annual"], round(p["monthly"] * 12, 2), places=2)

    def test_C05_storage_formula_is_billed_gb_times_rate(self):
        d = self.run_engine(storage_gb=1000, data_sensitivity="Restricted")
        for s in d["storage"]["providers"]:
            self.assertEqual(s["billed_gb"], 1600.0)
            self.assertAlmostEqual(s["monthly"], round(1600.0 * s["per_gb"], 2), delta=0.011)

    def test_C06_sensitivity_changes_volume_not_unit_rate(self):
        a = self.run_engine(data_sensitivity="Internal")["storage"]["primary"]
        b = self.run_engine(data_sensitivity="Restricted")["storage"]["primary"]
        self.assertEqual(a["per_gb"], b["per_gb"])
        self.assertGreater(b["monthly"], a["monthly"])

    def test_C07_network_is_explicit_egress_formula(self):
        d = self.run_engine(expected_users=4000)
        for r in d["cost"]["comparison"]:
            self.assertEqual(r["egress_gb"], 4000 * engine.EGRESS_GB_PER_USER)
            self.assertAlmostEqual(r["network"], round(r["egress_gb"] * r["egress_rate"], 2), places=2)

    def test_C08_compute_formula_instances_times_hours_times_rate(self):
        d = self.run_engine(compute_req="Heavy", expected_users=6000)
        need = 8 + 3
        for c in d["compute"]["providers"]:
            vcpu = ps.get_prices(c["provider"], "us-east-1")["compute"]["vcpu"]
            n = -(-need // vcpu)
            self.assertEqual(c["instances"], n)
            self.assertAlmostEqual(c["monthly"], round(c["hourly"] * n * 730, 2), places=2)

    def test_C09_region_honesty_for_estimates(self):
        us = self.run_engine(region="us-east-1")["pricing"]["providers"]["AWS"]["storage"]
        eu = self.run_engine(region="eu-west-1")["pricing"]["providers"]["AWS"]["storage"]
        self.assertTrue(us["region_adjusted"])
        self.assertFalse(eu["region_adjusted"])
        self.assertIn("reference", eu["note"].lower())

    def test_C10_report_is_json_serializable_and_has_provenance_fields(self):
        d = self.run_engine()
        json.dumps(d)
        q = d["pricing"]["providers"]["Google Cloud"]["compute"]
        for k in ("value", "unit", "currency", "status", "source", "source_url", "region_used", "note"):
            self.assertIn(k, q)
        self.assertEqual(q["currency"], "USD")
        self.assertIn("generated_at", d["pricing"])

    def test_C11_assumptions_state_egress_and_no_free_tier(self):
        text = " ".join(self.run_engine()["assumptions"]).lower()
        self.assertIn("egress", text)
        self.assertIn("no free-tier", text)
        self.assertNotIn("pricing data unavailable", text)


class TestLiveCacheFallback(Base):
    def test_C12_live_azure_is_region_specific_and_labelled_live(self):
        self.go_live()
        eu = self.run_engine(region="eu-west-1")
        us = self.run_engine(region="us-east-1")
        qs = eu["pricing"]["providers"]["Microsoft Azure"]
        self.assertEqual(qs["storage"]["status"], "live")
        self.assertEqual(qs["storage"]["region_used"], "northeurope")
        self.assertAlmostEqual(qs["storage"]["value"], 0.0196)
        self.assertAlmostEqual(qs["compute"]["value"], 0.0457)           # Linux, not Windows (0.0592) or Spot
        self.assertNotEqual(eu["cost"]["by_provider"]["Microsoft Azure"], us["cost"]["by_provider"]["Microsoft Azure"])
        self.assertEqual(eu["pricing"]["providers"]["AWS"]["storage"]["status"], "estimated")
        self.assertEqual(qs["egress"]["status"], "estimated")             # egress is not fetched live
        self.assertEqual(eu["pricing"]["overall_status"], "mixed")
        row = next(r for r in eu["cost"]["comparison"] if r["provider"] == "Microsoft Azure")
        self.assertEqual(row["pricing_status"], "mixed")
        self.assertTrue(qs["storage"]["as_of"])

    def test_C13_first_tier_storage_price_chosen_not_decoys(self):
        self.go_live()
        q = ps.get_prices("Microsoft Azure", "us-east-1")["storage"]
        self.assertAlmostEqual(q["value"], 0.0184)                         # not 0.019 (HNS) / 0.15 (Premium) / 0.01766 (tier 50)

    def test_C14_second_request_is_served_from_cache(self):
        self.go_live()
        first = ps.get_prices("Microsoft Azure", "us-east-1")
        n = len(self.calls)
        self.assertEqual(first["storage"]["status"], "live")
        second = ps.get_prices("Microsoft Azure", "us-east-1")
        self.assertEqual(len(self.calls), n, "cache hit must not call the API again")
        self.assertEqual(second["storage"]["status"], "cached")
        self.assertEqual(second["compute"]["status"], "cached")
        self.assertEqual(second["storage"]["value"], first["storage"]["value"])

    def test_C15_api_failure_falls_back_to_estimate_without_crashing(self):
        def boom(url, timeout):
            raise urllib.error.URLError("simulated outage")
        self.go_live(boom)
        d = self.run_engine()
        q = d["pricing"]["providers"]["Microsoft Azure"]["storage"]
        self.assertEqual(q["status"], "estimated")
        self.assertIn("failed", q["note"].lower())
        self.assertEqual(d["pricing"]["overall_status"], "estimated")

    def test_C16_timeout_is_handled(self):
        def slow(url, timeout):
            raise TimeoutError("timed out")
        self.go_live(slow)
        q = ps.get_prices("Microsoft Azure", "us-east-1")["compute"]
        self.assertEqual(q["status"], "estimated")

    def test_C17_circuit_breaker_stops_repeated_slow_calls(self):
        def boom(url, timeout):
            raise urllib.error.URLError("down")
        self.go_live(boom)
        ps.get_prices("Microsoft Azure", "us-east-1")
        n = len(self.calls)
        ps.get_prices("Microsoft Azure", "us-east-1")
        self.assertEqual(len(self.calls), n, "after a failure live calls are paused")

    def _write_cache(self, age_seconds, value=0.0184):
        key = "Microsoft Azure|storage|us-east-1|"
        rec = {key: dict(value=value, region_used="eastus", exact=True, fetched_at=time.time() - age_seconds)}
        with open(os.environ["PRICING_CACHE_PATH"], "w") as fh:
            json.dump(rec, fh)

    def test_C18_stale_cache_used_when_refresh_fails_and_flagged(self):
        def boom(url, timeout):
            raise urllib.error.URLError("down")
        self.go_live(boom)
        self._write_cache(2 * 24 * 3600)
        q = ps.get_prices("Microsoft Azure", "us-east-1")["storage"]
        self.assertEqual(q["status"], "cached")
        self.assertTrue(q.get("stale"))
        self.assertIn("stale", q["note"].lower())

    def test_C19_cache_older_than_a_week_is_ignored(self):
        def boom(url, timeout):
            raise urllib.error.URLError("down")
        self.go_live(boom)
        self._write_cache(10 * 24 * 3600)
        self.assertEqual(ps.get_prices("Microsoft Azure", "us-east-1")["storage"]["status"], "estimated")

    def test_C20_expired_cache_is_refreshed_when_api_works(self):
        self.go_live()
        self._write_cache(2 * 24 * 3600, value=0.0199)
        q = ps.get_prices("Microsoft Azure", "us-east-1")["storage"]
        self.assertEqual(q["status"], "live")
        self.assertAlmostEqual(q["value"], 0.0184)

    def test_C21_implausible_live_value_is_rejected(self):
        def crazy(url, timeout):
            page = fake_azure(url, timeout)
            for i in page["Items"]:
                i["retailPrice"] = i["retailPrice"] * 100
            return page
        self.go_live(crazy)
        q = ps.get_prices("Microsoft Azure", "us-east-1")["storage"]
        self.assertEqual(q["status"], "estimated")
        self.assertIn("implausible", q["note"].lower())

    def test_C22_empty_wrong_currency_and_bad_shape_are_rejected(self):
        for bad in ({"Items": []}, {"Items": [_item("eastus", "General Block Blob v2", "Hot LRS", "Hot LRS Data Stored",
                                                    "1 GB/Month", 0.0184, cur="EUR")]}, ["not", "a", "dict"]):
            ps._breaker.clear()
            self.go_live(lambda u, t, b=bad: b)
            self.assertEqual(ps.get_prices("Microsoft Azure", "us-east-1")["storage"]["status"], "estimated")

    def test_C23_corrupt_cache_file_is_ignored(self):
        self.go_live()
        with open(os.environ["PRICING_CACHE_PATH"], "w") as fh:
            fh.write("{ this is not json")
        self.assertEqual(ps.get_prices("Microsoft Azure", "us-east-1")["storage"]["status"], "live")

    def test_C24_global_region_uses_default_and_is_not_claimed_exact(self):
        self.go_live()
        q = ps.get_prices("Microsoft Azure", "Global / multi-region")["storage"]
        self.assertEqual(q["status"], "live")
        self.assertFalse(q["region_adjusted"])

    def test_C25_live_switch_off_means_no_network_calls(self):
        self.go_live()
        os.environ["PRICING_LIVE"] = "0"
        ps.get_prices("Microsoft Azure", "us-east-1")
        self.assertEqual(self.calls, [])


class TestRendering(Base):
    @classmethod
    def setUpClass(cls):
        import jinja2
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(os.path.join(ROOT, "templates")))
        env.filters["money"] = lambda v: f"${float(v):,.2f}"
        cls.env = env

    def _render(self, name, d):
        return self.env.get_template(name).render(d=d, PCONFIG=engine.PROVIDERS)

    def test_C26_new_report_templates_render_with_sources_and_status(self):
        d = self.run_engine()
        html = self._render("partials/_cost.html", d)
        self.assertIn("Pricing sources and timestamps", html)
        self.assertIn("estimated", html)
        self.assertIn("not verified", html)
        for part in ("partials/_storage.html", "partials/_compute.html", "partials/_comparison.html",
                     "partials/_suitability.html"):
            self._render(part, d)

    def test_C27_old_saved_reports_still_render(self):
        with open(os.path.join(ROOT, "tests", "fixtures", "old_report.json")) as fh:
            old = json.load(fh)
        self.assertNotIn("pricing", old)
        html = self._render("partials/_cost.html", old)
        self.assertIn("older report", html.lower())
        for part in ("partials/_storage.html", "partials/_compute.html", "partials/_comparison.html"):
            self._render(part, old)

    def test_C28_pdf_builds_for_new_and_old_reports(self):
        from io import BytesIO
        from pypdf import PdfReader
        import pdf_generator
        with open(os.path.join(ROOT, "tests", "fixtures", "old_report.json")) as fh:
            old = json.load(fh)
        new = self.run_engine()
        new["suitability"]["explanation"] = "test"
        for rep in (new, old):
            rep = dict(rep)
            rep["providers_meta"] = engine.PROVIDERS
            pdf = pdf_generator.build_pdf(rep, None)
            self.assertTrue(pdf.startswith(b"%PDF"))
            text = " ".join(p.extract_text() for p in PdfReader(BytesIO(pdf)).pages)
            self.assertIn("Cost Analysis", text)
        text_new = " ".join(p.extract_text() for p in PdfReader(BytesIO(pdf_generator.build_pdf(dict(new, providers_meta=engine.PROVIDERS), None))).pages)
        self.assertIn("estimated", text_new)
        self.assertIn("Price point", text_new)


if __name__ == "__main__":
    unittest.main(verbosity=2)
