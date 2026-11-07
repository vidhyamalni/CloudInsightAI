"""CloudInsight AI - application-security helpers (no database or login dependency, so they are unit-testable).

* CSRF protection for every state-changing request (session token; hidden form field or X-CSRF-Token header)
* secret key from the environment, else a random key generated once and stored in instance/.secret_key
* hardened session cookies and basic security response headers
* server-side validation of the analysis form (types, ranges, allowed values)
* safe "next" redirect check and DATABASE_URL normalisation
"""
import math
import os
import secrets
from urllib.parse import urlparse

from flask import jsonify, request, session

REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1", "ap-northeast-1",
           "Global / multi-region"]
INDUSTRIES = ["Technology", "Finance", "Healthcare", "Retail", "Manufacturing", "Education", "Media", "Other"]
COMPLIANCE = ["None", "GDPR", "HIPAA", "PCI DSS", "SOC 2", "ISO 27001"]
LIMITS = dict(monthly_budget=(0, 10_000_000), storage_gb=(1, 5_000_000), expected_users=(1, 50_000_000))
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# ---------------------------------------------------------------- configuration helpers
def load_secret_key(instance_dir):
    key = os.environ.get("SECRET_KEY", "").strip()
    if key:
        return key
    path = os.path.join(instance_dir, ".secret_key")
    try:
        with open(path, encoding="utf-8") as fh:
            key = fh.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        os.makedirs(instance_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError:
        pass                      # unwritable disk: a per-process key still keeps the app safe (sessions reset on restart)
    return key


def normalize_db_url(url):
    """Hosts such as Render/Heroku hand out postgres://, which SQLAlchemy 2 no longer accepts."""
    if url and url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def is_safe_next(target):
    """Only same-site relative paths may be used as a post-login redirect."""
    if not target or not isinstance(target, str):
        return False
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return False
    p = urlparse(target)
    return not p.netloc and not p.scheme


# ---------------------------------------------------------------- CSRF
def csrf_token():
    tok = session.get("_csrf")
    if not tok:
        tok = session["_csrf"] = secrets.token_urlsafe(32)
    return tok


def _csrf_guard():
    from flask import current_app
    if request.method not in UNSAFE_METHODS or not current_app.config.get("CSRF_ENABLED", True):
        return None
    sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
    expected = session.get("_csrf", "")
    if expected and sent and secrets.compare_digest(str(sent), str(expected)):
        return None
    msg = "Your session security token is missing or expired. Please refresh the page and try again."
    if request.path.startswith("/api/") or request.is_json or request.path == "/contact":
        return jsonify(ok=False, error=msg, answer=msg), 400
    return (f"<h1>Request blocked</h1><p>{msg}</p><p><a href='/'>Back to home</a></p>", 400)


def _headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return resp


def init_security(app):
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    if not app.config.get("SESSION_COOKIE_SAMESITE"):        # Flask's own default is None, so setdefault would not apply
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    if os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes", "on"):
        app.config["SESSION_COOKIE_SECURE"] = True       # set on HTTPS hosting
    app.before_request(_csrf_guard)
    app.after_request(_headers)
    app.context_processor(lambda: dict(csrf_token=csrf_token))


# ---------------------------------------------------------------- analysis-form validation
def _number(raw, label, lo, hi, integer):
    text = str(raw if raw is not None else "").strip().replace(",", "")
    if text == "":
        return None, f"{label} is required."
    try:
        v = float(text)
    except ValueError:
        return None, f"{label} must be a number."
    if not math.isfinite(v):
        return None, f"{label} must be a valid number."
    if v < lo or v > hi:
        return None, f"{label} must be between {lo:,} and {hi:,}."
    return (int(round(v)) if integer else round(v, 2)), None


def validate_analysis_form(form):
    """Returns (clean_form_dict, error_message). On error the dict is None. Values are strings for the engine."""
    name = str(form.get("company_name", "") or "").strip()
    if not name:
        return None, "Company name is required."
    if len(name) > 160:
        return None, "Company name must be 160 characters or fewer."
    out = dict(company_name=name)
    for key, label, integer, required in (("monthly_budget", "Monthly budget", False, False),
                                          ("storage_gb", "Required storage (GB)", True, True),
                                          ("expected_users", "Expected users", True, True)):
        lo, hi = LIMITS[key]
        raw = form.get(key)
        if not required and str(raw if raw is not None else "").strip() == "":
            out[key] = "0"
            continue
        val, err = _number(raw, label, lo, hi, integer)
        if err:
            return None, err
        out[key] = str(val)
    ind = str(form.get("industry", "Other") or "Other").strip()
    out["industry"] = ind if ind in INDUSTRIES else "Other"
    reg = str(form.get("region", "") or "").strip()
    out["region"] = reg if reg in REGIONS else "Global / multi-region"      # unknown region: no exact price claim
    comp = str(form.get("compliance", "None") or "None").strip()
    out["compliance"] = comp if comp in COMPLIANCE else "None"
    for key in ("compute_req", "data_sensitivity", "availability"):       # the engine whitelists these
        out[key] = str(form.get(key, "") or "").strip()
    return out, None
