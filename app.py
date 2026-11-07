"""CloudInsight AI — application entry point.
Automatic multi-provider analysis: no provider selection — all six providers are
evaluated and the most suitable one is recommended from the company's details."""
import os, json
from io import BytesIO
from datetime import datetime, timedelta
from urllib.parse import urlparse
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, send_file, abort)
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Analysis, ChatMessage, ContactMessage
import analysis_engine as engine
import ai_service as ai
import requirement_extractor as extractor
from pdf_generator import build_pdf

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-change-me-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///cloudinsight.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024 * 1024
db.init_app(app)

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
        db.session.add(ContactMessage(name=data.get("name", ""), email=data["email"], message=data["message"]))
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
                return redirect(request.args.get("next") or url_for("dashboard"))
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
                if nxt and urlparse(nxt).netloc == "":
                    return redirect(nxt)
                return redirect(url_for("dashboard"))
            else:
                if u:
                    u.failed_attempts += 1
                    if u.failed_attempts >= MAX_ATTEMPTS:
                        u.locked_until = datetime.utcnow() + timedelta(minutes=LOCK_MINUTES)
                        u.failed_attempts = 0
                    db.session.commit()
                flash("Invalid email or password.", "error")
    return render_template("login.html")


@app.get("/logout")
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

@app.route("/analysis", methods=["GET", "POST"])
@login_required
def analysis():
    if request.method == "POST":
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
        d = engine.run_analysis(form, extraction_meta=meta)
        if d.get("suitability"):
            ok, txt = ai.suitability_explanation(d["inputs"], d["suitability"])
            d["suitability"]["explanation"] = txt if (ok and txt) else " ".join(d["suitability"]["factors"])
            d["suitability"]["explanation_source"] = "gemini" if (ok and txt) else "rule-based"
        r = Analysis(user_id=current_user.id, company_name=d["inputs"]["company_name"],
                     provider=d["inputs"]["provider"], industry=d["inputs"]["industry"],
                     region=d["inputs"]["region"], monthly_budget=d["inputs"]["monthly_budget"],
                     storage_gb=d["inputs"]["storage_gb"], expected_users=d["inputs"]["expected_users"],
                     compute_req=d["inputs"]["compute_req"], data_sensitivity=d["inputs"]["data_sensitivity"],
                     availability_req=d["inputs"]["availability"], compliance_req=d["inputs"]["compliance"],
                     result_json=json.dumps(d))
        db.session.add(r); db.session.commit()
        return redirect(url_for("report", rid=r.id))
    return render_template("analysis.html")


# ---------------- Dashboard (history + trends) ----------------

@app.get("/dashboard")
@login_required
def dashboard():
    all_reports = current_user.analyses.order_by(Analysis.created_at.desc()).all()
    recent = all_reports[:5]

    selected = None
    try:
        rid = int(request.args.get("report") or 0)
        selected = next((x for x in all_reports if x.id == rid), None)
    except (TypeError, ValueError):
        pass
    if selected is None:
        selected = all_reports[0] if all_reports else None

    chart = _chart_data(selected.data()) if selected else None
    trend = dict(
        labels=[x.created_at.strftime("%b %d") for x in reversed(all_reports)],
        cost=[x.data().get("cost", {}).get("primary", {}).get("monthly", 0) for x in reversed(all_reports)],
        score=[x.data().get("security", {}).get("score", 0) for x in reversed(all_reports)])
    return render_template("dashboard.html", r=selected, d=selected.data() if selected else None,
                           chart=chart, recent=recent, reports_count=len(all_reports),
                           all_reports=all_reports, trend=trend)


@app.get("/reports")
@login_required
def reports():
    return render_template("reports.html",
                           rows=current_user.analyses.order_by(Analysis.created_at.desc()).all())


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


# ---------------- Focused analysis pages ----------------

def _focused(template_name):
    r = _latest_report()
    if not r:
        flash("Run a cloud analysis first to unlock this section.", "error")
        return redirect(url_for("analysis"))
    return render_template(template_name + ".html", r=r, d=r.data(), chart=_chart_data(r.data()))


@app.get("/security")
@login_required
def security_page():
    return _focused("security")


@app.get("/cost")
@login_required
def cost_page():
    return _focused("cost")


@app.get("/storage")
@login_required
def storage_page():
    return _focused("storage")


# ---------------- AI assistant ----------------

@app.get("/assistant")
@login_required
def assistant():
    r = _latest_report()
    msgs = (ChatMessage.query.filter_by(user_id=current_user.id)
            .order_by(ChatMessage.created_at).limit(60).all()) if r else []
    return render_template("assistant.html", r=r, msgs=msgs, gemini_ok=ai.gemini_ready(),
                           suggestions=["Why was this provider recommended?", "Why is the estimated cost high?",
                                        "How can I reduce storage cost?", "What are the main security risks?",
                                        "How should I secure my cloud storage?", "What should I improve first?"])


@app.post("/api/ai/ask")
@login_required
def api_ai_ask():
    payload = request.get_json(silent=True) or {}
    q = (payload.get("question") or "").strip()
    if not q:
        return jsonify(answer="Please type a question."), 400
    r = _latest_report()
    if not r:
        return jsonify(answer="Run a cloud analysis first so I can ground my answers in your report.")
    db.session.add(ChatMessage(user_id=current_user.id, analysis_id=r.id, role="user", content=q))
    recent = (ChatMessage.query.filter_by(user_id=current_user.id)
              .order_by(ChatMessage.created_at.desc()).limit(6).all())
    history = " | ".join(f"{m.role}: {m.content}" for m in reversed(recent))[:1200]
    ok, ans = ai.ask(q, r.data())
    if not ok:
        ans = ai.offline_answer(q, r.data()) or ai.OFFLINE_MSG
    db.session.add(ChatMessage(user_id=current_user.id, analysis_id=r.id, role="assistant", content=ans))
    db.session.commit()
    return jsonify(answer=ans, gemini=ok)


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
    return jsonify(available=False, message=ai.OFFLINE_MSG, fallback=ai.offline_insights(r.data()))


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)