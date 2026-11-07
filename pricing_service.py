"""CloudInsight AI - pricing service.

Every price that reaches a report is a *quote* carrying its own provenance:

  status   "live"      fetched from an official price API during this analysis
           "cached"    originally fetched live; served from the local cache (see 'stale')
           "estimated" stored estimate from data/pricing_baseline.json (never presented as live)
  source / source_url / as_of / currency / unit / region_used / region_adjusted / note

Live data is currently implemented for Azure only (public API, no credentials). The other
providers use stored estimates, labelled as such. To add a provider, write a module with the
same fetch() contract as azure_pricing.py and register it in LIVE_FETCHERS.

Failure handling: timeouts, HTTP errors, empty or implausible answers never raise to the caller;
the service falls back to a recent cache entry, then to the stored estimate, and says so in 'note'.
"""
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

import azure_pricing

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BASELINE_PATH = os.path.join(BASE_DIR, "data", "pricing_baseline.json")
COMPONENTS = ("storage", "compute", "egress")
LIVE_FETCHERS = {"Microsoft Azure": azure_pricing}

CACHE_TTL = 24 * 3600          # a cached live price is "cached" for 24 h, then refreshed
STALE_MAX = 7 * 24 * 3600      # after a failed refresh, a cache entry up to 7 days old may still be shown (flagged stale)
BREAKER_SECONDS = 300          # after a failure, skip live calls to that provider for 5 minutes
PLAUSIBLE_RATIO = (0.25, 4.0)  # live value must be within 0.25x-4x of the stored estimate, else rejected

_lock = threading.Lock()
_breaker = {}                  # provider -> monotonic time until which live calls are skipped
_baseline = None


class PricingError(Exception):
    pass


# ---------------------------------------------------------------- configuration (read at call time)
def live_enabled():
    return os.environ.get("PRICING_LIVE", "1").strip().lower() not in ("0", "false", "no", "off")


def _timeout():
    try:
        return float(os.environ.get("PRICING_TIMEOUT", "6"))
    except ValueError:
        return 6.0


def _cache_path():
    return os.environ.get("PRICING_CACHE_PATH") or os.path.join(BASE_DIR, "instance", "pricing_cache.json")


def _now():
    return datetime.now(timezone.utc)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------- baseline
def load_baseline(force=False):
    global _baseline
    if _baseline is None or force:
        try:
            with open(BASELINE_PATH, encoding="utf-8") as fh:
                _baseline = json.load(fh)
        except (OSError, ValueError) as exc:
            raise PricingError(f"pricing baseline file unreadable: {exc}")
    return _baseline


# ---------------------------------------------------------------- cache (atomic JSON file)
def _cache_read():
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _cache_write(data):
    path = _cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass                   # caching is best-effort; never break an analysis because of it


# ---------------------------------------------------------------- quote helpers
def _base_value(component, entry):
    return {"storage": entry.get("usd_per_gb_month"), "compute": entry.get("usd_per_hour"),
            "egress": entry.get("usd_per_gb")}[component]


def _unit(component):
    return {"storage": "USD per GB-month", "compute": "USD per hour", "egress": "USD per GB"}[component]


def _estimated_quote(provider, component, entry, region, baseline, attempt_note=None):
    ref = baseline.get("reference_app_region", "us-east-1")
    matches = (region == ref)
    reviewed = baseline.get("last_manual_review")
    note = []
    if attempt_note:
        note.append(attempt_note)
    note.append("Stored estimate" + (f", manually reviewed {reviewed}" if reviewed and entry.get("verified")
                                      else ", not yet verified against the provider price list"))
    note.append(f"{entry.get('provenance', 'baseline value')}")
    if not matches:
        note.append(f"US reference price used - '{region}' prices can differ")
    q = dict(value=float(_base_value(component, entry)), unit=_unit(component), currency=baseline.get("currency", "USD"),
             status="estimated", source="Stored estimate (data/pricing_baseline.json)",
             source_url=entry.get("source_url"), as_of=(reviewed if entry.get("verified") else None),
             region_requested=region, region_used=f"{ref} reference", region_adjusted=matches,
             verified=bool(entry.get("verified")), note=" · ".join(note))
    if component == "compute":
        q.update(instance=entry["instance"], vcpu=int(entry["vcpu"]), ram_gib=entry.get("ram_gib"))
    return q


def _live_quote(provider, component, entry, region, res, ts, status, note, mod):
    q = dict(value=float(res["value"]), unit=_unit(component), currency="USD", status=status,
             source=getattr(mod, "SOURCE_NAME", provider + " pricing API"),
             source_url=getattr(mod, "API_URL", entry.get("source_url")), as_of=_iso(ts),
             region_requested=region, region_used=res["region_used"], region_adjusted=bool(res.get("exact", True)),
             verified=True, note=note)
    if component == "compute":
        q.update(instance=entry["instance"], vcpu=int(entry["vcpu"]), ram_gib=entry.get("ram_gib"))
    return q


def _plausible(value, base):
    try:
        v, b = float(value), float(base)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > 0 and PLAUSIBLE_RATIO[0] * b <= v <= PLAUSIBLE_RATIO[1] * b


# ---------------------------------------------------------------- live resolution
def _resolve_live(provider, component, entry, region, baseline):
    """Return a live/cached quote, or an estimated quote that explains why live was not used."""
    mod = LIVE_FETCHERS.get(provider)
    if mod is None or component not in getattr(mod, "SUPPORTED", ()):
        return None            # not a live-capable component: caller uses the plain estimate
    if not live_enabled():
        return _estimated_quote(provider, component, entry, region, baseline,
                                "Live pricing is switched off (PRICING_LIVE=0)")
    key = f"{provider}|{component}|{region}|{entry.get('azure_sku', '')}"
    base_val = _base_value(component, entry)
    now = time.time()

    with _lock:
        cached = _cache_read().get(key)
    if cached and now - cached.get("fetched_at", 0) <= CACHE_TTL and _plausible(cached.get("value"), base_val):
        return _live_quote(provider, component, entry, region, cached, cached["fetched_at"], "cached",
                           "Served from local cache (originally fetched live)", mod)

    reason = None
    if _breaker.get(provider, 0) > time.monotonic():
        reason = "Live API skipped after a recent failure"
    else:
        try:
            res = mod.fetch(component, region, entry, _timeout())
            if not _plausible(res.get("value"), base_val):
                raise PricingError(f"live value {res.get('value')} is implausible versus the stored estimate")
            record = dict(value=res["value"], region_used=res["region_used"], exact=res["exact"], fetched_at=now)
            with _lock:
                data = _cache_read()
                data[key] = record
                _cache_write(data)
            return _live_quote(provider, component, entry, region, record, now, "live",
                               "Fetched live during this analysis", mod)
        except Exception as exc:   # timeouts, HTTP errors, bad JSON, empty results - all handled the same way
            _breaker[provider] = time.monotonic() + BREAKER_SECONDS
            reason = f"Live fetch failed ({type(exc).__name__}: {str(exc)[:80]})"

    if cached and now - cached.get("fetched_at", 0) <= STALE_MAX and _plausible(cached.get("value"), base_val):
        age_h = int((now - cached["fetched_at"]) // 3600)
        q = _live_quote(provider, component, entry, region, cached, cached["fetched_at"], "cached",
                        f"{reason}; showing a cached live price that is {age_h} h old (stale)", mod)
        q["stale"] = True
        return q
    return _estimated_quote(provider, component, entry, region, baseline, reason)


# ---------------------------------------------------------------- public API
def get_prices(provider, region):
    """Return {'storage': quote, 'compute': quote, 'egress': quote} for one provider."""
    baseline = load_baseline()
    try:
        entries = baseline["providers"][provider]
    except KeyError:
        raise PricingError(f"no pricing entry for provider '{provider}'")
    out = {}
    for comp in COMPONENTS:
        entry = entries[comp]
        out[comp] = _resolve_live(provider, comp, entry, region, baseline) or \
            _estimated_quote(provider, comp, entry, region, baseline)
    return out


def get_all_prices(providers, region):
    return {name: get_prices(name, region) for name in providers}


def combine_status(statuses):
    s = set(statuses)
    if s == {"estimated"}:
        return "estimated"
    if s <= {"live", "cached"}:
        return "cached" if "cached" in s else "live"
    return "mixed"


def oldest_as_of(quotes):
    stamps = [q["as_of"] for q in quotes if q.get("as_of") and q["status"] in ("live", "cached")]
    return min(stamps) if stamps else None


def summarize(prices, region):
    """Compact, JSON-safe block stored with each report so the UI/PDF can show provenance."""
    counts = {"live": 0, "cached": 0, "estimated": 0}
    for comps in prices.values():
        for q in comps.values():
            counts[q["status"]] += 1
    total = sum(counts.values())
    overall = ("estimated" if counts["estimated"] == total else
               "live" if counts["live"] == total else
               "cached" if counts["estimated"] == 0 else "mixed")
    baseline = load_baseline()
    return dict(currency=baseline.get("currency", "USD"), generated_at=_now().strftime("%Y-%m-%d %H:%M UTC"),
                region_requested=region, live_enabled=live_enabled(), overall_status=overall, counts=counts,
                last_manual_review=baseline.get("last_manual_review"), providers=prices)
