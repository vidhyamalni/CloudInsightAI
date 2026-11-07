"""CloudInsight AI — application entry point.
Automatic multi-provider analysis: no provider selection — all six providers are
evaluated and the most suitable one is recommended from the company's details."""
import os, json, time
from io import BytesIO
from datetime import datetime, timedelta
from urllib.parse import urlparse
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, abort)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from sqlalchemy import func, text
from sqlalchemy.orm import defer
from models import db, User, Analysis, ChatMessage, ContactMessage
import analysis_engine as engine
import ai_service as ai
import requirement_extractor as extractor
import ui_filters
import security
from pdf_generator import build_pdf

app = Flask(__name__)
app.config["SECRET_KEY"] = security.load_secret_key(app.instance_path)     # env SECRET_KEY, else a random key stored in instance/
app.config["SQLALCHEMY_DATABASE_URI"] = security.normalize_db_url(os.environ.get("DATABASE_URL", "sqlite:///cloudinsight.db"))
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
db.init_app(app)
security.init_security(app)          # CSRF, hardened cookies, security headers

login = LoginManager(app)
login.login_view = "login_route"


@login.user_loader
def load_user(uid):
    return db.session.get(User, int(uid))


MAX_ATTEMPTS, LOCK_MINUTES = 5, 5


@app.context_processor
def inject_brand():
    return dict(brand="CloudInsight AI",
                tagline="AI-Powered Cloud Security, Risk & Cost Analytics Platform",
                year=datetime.utcnow().year, PCONFIG=engine.PROVIDERS)


@app.template_filter("money")
def money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


ui_filters.register(app)


def _chart_data(d):
    mult = d["storage"]["redundancy_multiplier"]
    out = dict(
        provider=d["inputs"]["provider"],
        cost=dict(labels=list(d["cost"]["by_provider"].keys()),
                  storage=[c["storage"] for c in d["cost"]["comparison"]],
                  compute=[c["compute"] for c in d["cost"]["comparison"]],
                  network=[c["network"] for c in d["cost"]["comparison"]],
                  monthly=[c["monthly"] for c in d["cost"]["comparison"]]),
        security=dict(score=d["security"]["score"], level=d["security"]["risk_level"],
                      high=d["security"]["high"], medium=d["security"]["medium"], low=d["security"]["low"]),
        storage=dict(raw=d["storage"]["required_gb"],
                     redundancy=round(d["storage"]["required_gb"] * (mult - 1), 1),
                     backup=round(d["storage"]["required_gb"] * 0.5, 1)),
        radar=d["comparison_scores"],
        domains=[dict(domain=x["domain"], status=x["status"]) for x in d["security"]["domains"]])
    suit = d.get("suitability")
    if suit:
        out["suit"] = dict(labels=[r["provider"] for r in suit["ranking"]],
                           overall=[r["overall"] for r in suit["ranking"]],
                           winner=suit["winner"])
    return out


def _owned_report(rid):
    r = db.session.get(Analysis, rid)
    if not r or r.user_id != current_user.id:
        abort(404)
    return r


def _latest_report():
    return current_user.analyses.order_by(Analysis.created_at.desc()).first()


# ---------------- Public site ----------------

@app.get("/")
def index():
    return render_template("index.html")


@app.post("/contact")
def contact():
    data = request.get_json(silent=True) or request.form
    if data.get("message") and data.get("email"):
        db.session.add(ContactMessage(name=str(data.get("name", ""))[:120], email=str(data["email"])[:255],
                                      message=str(data["message"])[:2000]))
        db.session.commit()
    if request.is_json:
        return jsonify(ok=True)
    flash("Message received — we'll get back to you soon.", "success")
    return redirect(url_for("index") + "#contact")


# ---------------- Auth ----------------

@app.route("/login", methods=["GET", "POST"])
def login_route():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        action = request.form.get("action", "login")
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if action == "register":
            name = request.form.get("name", "").strip()
            if not name or "@" not in email or len(password) < 8:
                flash("Please provide a name, a valid email and a password of at least 8 characters.", "error")
            elif User.query.filter_by(email=email).first():
                flash("An account with this email already exists.", "error")
            else:
                u = User(name=name, email=email)
                u.set_password(password)
                db.session.add(u); db.session.commit()
                login_user(u)
                nxt = request.args.get("next")
                return redirect(nxt if security.is_safe_next(nxt) else url_for("dashboard"))
        else:
            u = User.query.filter_by(email=email).first()
            if u and u.is_locked:
                mins = max(1, int((u.locked_until - datetime.utcnow()).total_seconds() // 60) + 1)
                flash(f"Account temporarily locked due to failed attempts. Try again in ~{mins} minute(s).", "error")
            elif u and u.check_password(password):
                u.failed_attempts, u.locked_until = 0, None
                db.session.commit()
                login_user(u)
                nxt = request.args.get("next")
                return redirect(nxt if security.is_safe_next(nxt) else url_for("dashboard"))
            else:
                if u:
                    u.failed_attempts += 1
                    if u.failed_attempts >= MAX_ATTEMPTS:
                        u.locked_until = datetime.utcnow() + timedelta(minutes=LOCK_MINUTES)
                        u.failed_attempts = 0
                    db.session.commit()
                flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))


# ---------------- Company-file extraction (AI) ----------------

@app.post("/api/extract")
@login_required
def api_extract():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(ok=False, error="No file provided."), 400
    try:
        text = extractor.read_file(f)
        fields, source = ai.extract_requirements(text)
        return jsonify(ok=True, fields=fields, source=source, chars=len(text))
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    except Exception:
        return jsonify(ok=False, error="Could not read the file. Try TXT, MD, JSON or CSV."), 400


# ---------------- Cloud analysis ----------------

# Security / Cost / Storage pages are analysis *interfaces*: they open the form (no old results),
# and after a new analysis they jump to the matching section of the newly saved report.
FOCUS_PAGES = {
    "security": dict(page_title="Security Analysis", page_sub="Describe your environment to assess its security posture (simulated configuration analysis)",
                     anchor="sec-7", button="Run Security Analysis",
                     busy="Running Security Analysis...", done="Security analysis completed successfully"),
    "cost": dict(page_title="Cost Analysis", page_sub="Describe your environment to estimate monthly and annual cloud cost",
                 anchor="sec-6", button="Estimate Cloud Cost",
                 busy="Estimating Cloud Cost...", done="Cost estimation completed successfully"),
    "storage": dict(page_title="Storage Analysis", page_sub="Describe your data to compare storage services and storage cost",
                    anchor="sec-4", button="Run Storage Analysis",
                    busy="Running Storage Analysis...", done="Storage analysis completed successfully"),
}


def _company_profiles():
    """Previously used company details (latest per company) so a form can be pre-filled.
    Reads input columns only - never the stored analysis results."""
    seen, out = set(), []
    rows = (current_user.analyses.options(defer(Analysis.result_json), defer(Analysis.ai_insights))
            .order_by(Analysis.created_at.desc(), Analysis.id.desc()).all())
    for a in rows:
        key = (a.company_name or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(dict(id=a.id, company_name=a.company_name, industry=a.industry, region=a.region,
                        monthly_budget=a.monthly_budget, storage_gb=a.storage_gb, expected_users=a.expected_users,
                        compute_req=a.compute_req, data_sensitivity=a.data_sensitivity,
                        availability=a.availability_req, compliance=a.compliance_req))
    return out


def _analysis_form_page(focus=None, values=None):
    meta = FOCUS_PAGES.get(focus, {})
    return render_template("analysis.html", focus=focus if focus in FOCUS_PAGES else None,
                           profiles=_company_profiles(), form_values=values or {}, **meta)


@app.route("/analysis", methods=["GET", "POST"])
@login_required
def analysis():
    if request.method == "POST":
        t0 = time.perf_counter()
        focus = request.form.get("focus")
        form = dict(request.form)
        meta = dict(source="form", file_name=None, fields_from_file=[])
        file = request.files.get("company_file")
        if file and file.filename:
            try:
                text = extractor.read_file(file)
                fields, src = ai.extract_requirements(text)
                form, from_file = extractor.merge(request.form, fields)
                meta = dict(source=src, file_name=file.filename, fields_from_file=from_file)
            except ValueError as e:
                flash(f"Uploaded file skipped: {e}", "error")
            except Exception:
                flash("Uploaded file could not be read — using the form values only.", "error")
        clean, err = security.validate_analysis_form(form)
        if err:
            flash(err, "error")
            return _analysis_form_page(focus, {k: v for k, v in request.form.items() if k != "csrf_token"})
        try:
            d = engine.run_analysis(clean, extraction_meta=meta)
        except (ValueError, TypeError):
            flash("Please enter valid numbers for budget, storage and users.", "error")
            return _analysis_form_page(focus, {k: v for k, v in request.form.items() if k != "csrf_token"})
        if d.get("suitability"):
            ok, txt = ai.suitability_explanation(d["inputs"], d["suitability"])
            d["suitability"]["explanation"] = txt if (ok and txt) else " ".join(d["suitability"]["factors"])
            d["suitability"]["explanation_source"] = "gemini" if (ok and txt) else "rule-based"
        d.setdefault("meta", {})["duration_sec"] = round(time.perf_counter() - t0, 1)     # measured, not assumed
        r = Analysis(user_id=current_user.id, company_name=d["inputs"]["company_name"],
                     provider=d["inputs"]["provider"], industry=d["inputs"]["industry"],
                     region=d["inputs"]["region"], monthly_budget=d["inputs"]["monthly_budget"],
                     storage_gb=d["inputs"]["storage_gb"], expected_users=d["inputs"]["expected_users"],
                     compute_req=d["inputs"]["compute_req"], data_sensitivity=d["inputs"]["data_sensitivity"],
                     availability_req=d["inputs"]["availability"], compliance_req=d["inputs"]["compliance"],
                     result_json=json.dumps(d))
        db.session.add(r); db.session.commit()
        flash(f"{FOCUS_PAGES[focus]['done'] if focus in FOCUS_PAGES else 'Cloud analysis completed successfully'}! "
              f"Time taken: {d['meta']['duration_sec']} seconds.", "success")
        anchor = FOCUS_PAGES[focus]["anchor"] if focus in FOCUS_PAGES else None
        return redirect(url_for("report", rid=r.id, _anchor=anchor))
    return _analysis_form_page()


# ---------------- Dashboard (overview only) ----------------

@app.get("/dashboard")
@login_required
def dashboard():
    """Summary overview. Detailed storage / cost / compute / security results are NOT shown here;
    they live in each saved report (open it from Analysis History)."""
    mine = current_user.analyses
    recent = (mine.options(defer(Analysis.result_json), defer(Analysis.ai_insights))
              .order_by(Analysis.created_at.desc(), Analysis.id.desc()).limit(5).all())
    total = mine.count()
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    top = (db.session.query(Analysis.provider, func.count(Analysis.id))
           .filter(Analysis.user_id == current_user.id).group_by(Analysis.provider)
           .order_by(func.count(Analysis.id).desc(), Analysis.provider).first())
    stats = dict(
        total=total,
        reports=mine.filter(Analysis.result_json.isnot(None), Analysis.result_json != "").count(),
        this_month=mine.filter(Analysis.created_at >= month_start).count(),
        ai_insights=mine.filter(Analysis.ai_insights.isnot(None)).count(),
        ai_questions=ChatMessage.query.filter_by(user_id=current_user.id, role="user").count(),
        top_provider=top[0] if top else None,
        last=recent[0].created_at if recent else None)
    return render_template("dashboard.html", stats=stats, recent=recent)


# ---------------- Analysis history ----------------

@app.get("/history")
@login_required
def history():
    rows = (current_user.analyses.options(defer(Analysis.ai_insights))
            .order_by(Analysis.created_at.desc(), Analysis.id.desc()).all())
    return render_template("history.html", rows=rows)


@app.get("/reports")
@login_required
def reports():
    """Kept so old bookmarks keep working; the list now lives at /history."""
    return redirect(url_for("history"))


@app.get("/profile")
@login_required
def profile():
    return render_template("profile.html", reports_count=current_user.analyses.count())


# ---------------- Report & PDF ----------------

@app.get("/report/<int:rid>")
@login_required
def report(rid):
    r = _owned_report(rid)
    return render_template("report.html", r=r, d=r.data(), chart=_chart_data(r.data()),
                           insights=json.loads(r.ai_insights) if r.ai_insights else None)


@app.get("/report/<int:rid>/pdf")
@login_required
def report_pdf(rid):
    r = _owned_report(rid)
    insights = json.loads(r.ai_insights) if r.ai_insights else None
    payload = dict(r.data())
    payload["providers_meta"] = engine.PROVIDERS
    pdf = build_pdf(payload, insights)
    return send_file(BytesIO(pdf), mimetype="application/pdf", as_attachment=True,
                     download_name=f"cloudinsight-report-{r.id}.pdf")


# ---------------- Security / Cost / Storage analysis interfaces ----------------

@app.get("/security")
@login_required
def security_page():
    return _analysis_form_page("security")


@app.get("/cost")
@login_required
def cost_page():
    return _analysis_form_page("cost")


@app.get("/storage")
@login_required
def storage_page():
    return _analysis_form_page("storage")


# ---------------- AI assistant ----------------

CHAT_CONTEXT_TURNS = 8          # last N messages sent to the model as conversation context
MAX_QUESTION_CHARS = 2000


def _chat_scope(raw):
    """Resolve the report a conversation is about. None = general cloud questions.
    A report id must belong to the logged-in user (404 otherwise)."""
    if raw is None:
        return _latest_report()
    raw = str(raw).strip().lower()
    if raw in ("", "0", "general", "none", "null"):
        return None
    try:
        return _owned_report(int(raw))
    except ValueError:
        abort(404)


def _chat_query(report):
    return ChatMessage.query.filter_by(user_id=current_user.id, analysis_id=report.id if report else None)


@app.get("/assistant")
@login_required
def assistant():
    r = _chat_scope(request.args.get("report"))
    msgs = list(reversed(_chat_query(r).order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(60).all()))
    options = current_user.analyses.options(defer(Analysis.result_json), defer(Analysis.ai_insights)) \
        .order_by(Analysis.created_at.desc(), Analysis.id.desc()).all()
    if r:
        suggestions = ["Why was this provider recommended?", "Why is the estimated cost what it is?",
                       "What are the main security risks?", "What should I improve first?"]
    else:
        suggestions = ["What is the difference between object and block storage?", "How does cloud egress pricing work?",
                       "Explain IAM in simple terms", "AWS vs Azure vs Google Cloud \u2014 how do I choose?"]
    return render_template("assistant.html", r=r, msgs=msgs, options=options, gemini_ok=ai.gemini_ready(),
                           suggestions=suggestions)


@app.post("/api/ai/ask")
@login_required
def api_ai_ask():
    payload = request.get_json(silent=True) or {}
    q = (payload.get("question") or "").strip()
    if not q:
        return jsonify(answer="Please type a question."), 400
    q = q[:MAX_QUESTION_CHARS]
    rid = payload.get("report_id")
    r = _chat_scope(rid if rid is not None else "general")   # no id sent = general questions, never a guess
    prior = list(reversed(_chat_query(r).order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                          .limit(CHAT_CONTEXT_TURNS).all()))
    history_turns = [dict(role=m.role, content=m.content) for m in prior]
    ok, ans = ai.ask(q, r.data() if r else None, history_turns)
    gemini_error = None
    if not ok:
        gemini_error = ai.last_error()
        ans = ai.offline_answer(q, r.data()) if r else None
        if not ans:
            # Nothing trustworthy to say: report the failure, and do not store it as an "answer".
            msg = ai.OFFLINE_MSG + (" Select a saved report to get data-derived answers while the AI is offline." if not r else "")
            return jsonify(answer=msg, gemini=False, error=gemini_error)
    aid = r.id if r else None
    db.session.add(ChatMessage(user_id=current_user.id, analysis_id=aid, role="user", content=q))
    db.session.add(ChatMessage(user_id=current_user.id, analysis_id=aid, role="assistant", content=ans))
    db.session.commit()
    return jsonify(answer=ans, gemini=ok, error=gemini_error)


@app.get("/api/ai/insights/<int:rid>")
@login_required
def api_ai_insights(rid):
    r = _owned_report(rid)
    if r.ai_insights:
        return jsonify(available=True, insights=json.loads(r.ai_insights), cached=True)
    ok, data = ai.generate_insights(r.data())
    if ok and data:
        r.ai_insights = json.dumps(data)
        db.session.commit()
        return jsonify(available=True, insights=data)
    reason = ai.last_error()
    try:
        fallback = ai.offline_insights(r.data())
    except Exception:
        fallback = None
    return jsonify(available=False, message=ai.OFFLINE_MSG, fallback=fallback, error=reason)


@app.get("/healthz")
def healthz():
    """Hosting health check: confirms the app is up and the database answers."""
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify(status="ok")
    except Exception:
        return jsonify(status="error"), 503


with app.app_context():
    db.create_all()          # creates missing tables only; never alters or deletes existing data


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "").strip() == "1")     # debug is OFF unless FLASK_DEBUG=1