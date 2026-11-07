"""CloudInsight AI — Gemini integration. The API key is read from the environment
or from gemini_key.txt (project root) and is NEVER exposed to the client.
Model names are tried from a candidate list, then auto-discovered — so the app
keeps working even when Google retires model names. All failures degrade gracefully."""
import os, json, re
import requirement_extractor as rx

OFFLINE_MSG = "AI analysis is temporarily unavailable. Your cloud analysis is still available."

MODEL_CANDIDATES = ["gemini-3.8-flash", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

_COMPUTE = {"Light", "Moderate", "Heavy", "Intensive"}
_SENS = {"Public", "Internal", "Confidential", "Restricted"}
_AVAIL = {"Basic", "Standard", "High", "Mission-critical"}
_COMP = {"None", "GDPR", "HIPAA", "PCI DSS", "SOC 2", "ISO 27001"}


def _api_key():
    k = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if k and k.strip():
        return k.strip()
    try:
        p = os.path.join(os.path.dirname(__file__), "gemini_key.txt")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read().strip()
    except Exception:
        pass
    return None


def gemini_ready():
    return bool(_api_key())


def _call_gemini(prompt):
    """Tries candidate models, then auto-discovers whatever is available.
    Returns response text or None. Never raises."""
    key = _api_key()
    if not key:
        return None
    try:
        from google import genai as ggenai
        client = ggenai.Client(api_key=key)
        for model in MODEL_CANDIDATES:
            try:
                resp = client.models.generate_content(model=model, contents=prompt)
                if resp and getattr(resp, "text", None):
                    return resp.text
            except Exception:
                continue
        # Auto-discovery: ask the API what models exist for this key
        try:
            for m in client.models.list():
                name = m.name.split("/")[-1]
                if "flash" in name.lower() and "embed" not in name.lower():
                    try:
                        resp = client.models.generate_content(model=name, contents=prompt)
                        if resp and getattr(resp, "text", None):
                            return resp.text
                    except Exception:
                        continue
        except Exception:
            pass
    except Exception:
        pass
    return None


def _extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)

# ---------------- report insights ----------------

def _compact(report):
    c, s, sec, i = report["cost"]["primary"], report["storage"], report["security"], report["inputs"]
    return json.dumps(dict(
        inputs=i, monthly_cost=c["monthly"], annual_cost=c["annual"],
        storage_cost=c["storage"], compute_cost=c["compute"], network_cost=c["network"],
        required_storage_gb=s["required_gb"], storage_service=s["primary"]["service"],
        storage_per_gb=s["primary"]["per_gb"], redundancy_multiplier=s["redundancy_multiplier"],
        compute_instance=report["compute"]["primary"]["instance"],
        instances=report["compute"]["primary"]["instances"],
        security_score=sec["score"], risk_level=sec["risk_level"],
        high_risks=sec["high"], medium_risks=sec["medium"], low_risks=sec["low"],
        domains=[d["domain"] + " (" + d["status"] + ")" for d in sec["domains"]],
        budget_utilization=report["cost"]["budget_utilization"],
        provider_monthly=report["cost"]["by_provider"]))


INSIGHTS_PROMPT = """You are the AI analyst inside CloudInsight AI, a cloud security, risk and cost analytics platform.
Analyze the following cloud analysis JSON for the user's environment and respond with a professional, grounded assessment.

Analysis data (all pricing figures are ESTIMATES from configured baselines):
{data}

Return ONLY valid JSON with exactly these keys:
"summary" (3-4 sentences), "cost_insights", "security_insights", "storage_insights", "risk_explanation",
"optimization" (array of 3-5 short actionable strings), "architecture" (array of 3-4 strings),
"recommendations" (array of 4-6 strings ordered by priority).

Rules: base everything strictly on the provided figures; do not invent live prices or scan results;
do not present estimates as measurements; be specific and concise."""


def generate_insights(report):
    if not gemini_ready():
        return False, None
    try:
        text = _call_gemini(INSIGHTS_PROMPT.format(data=_compact(report)))
        if not text:
            return False, None
        data = _extract_json(text)
        for k in ("summary", "cost_insights", "security_insights", "storage_insights", "risk_explanation"):
            data[k] = str(data.get(k, "")).strip()
        for k in ("optimization", "architecture", "recommendations"):
            v = data.get(k, [])
            data[k] = [str(x) for x in v][:6] if isinstance(v, list) else [str(v)]
        return True, data
    except Exception:
        return False, None


ASK_PROMPT = """You are the AI assistant of CloudInsight AI. Answer the user's question using ONLY the
analysis context below. All pricing figures are estimates. Be concise (max 180 words), professional and specific.

Analysis context: {data}
Recent conversation: {history}

User question: {q}"""


def ask(question, report, history=""):
    if not gemini_ready():
        return False, None
    text = _call_gemini(ASK_PROMPT.format(data=_compact(report), history=history or "(none)", q=question))
    return (True, text.strip()) if text else (False, None)

# ---------------- requirement extraction ----------------

EXTRACT_PROMPT = """Extract cloud requirements from the company description below.
Return ONLY valid JSON with exactly these keys (use null for anything not stated):
"company_name", "industry", "region", "monthly_budget" (number, USD), "storage_gb" (number),
"expected_users" (number), "compute_req" (one of Light|Moderate|Heavy|Intensive),
"data_sensitivity" (one of Public|Internal|Confidential|Restricted),
"availability" (one of Basic|Standard|High|Mission-critical),
"compliance" (one of None|GDPR|HIPAA|PCI DSS|SOC 2|ISO 27001).

Company description:
{text}"""


def _clean_extracted(d):
    out = {}

    def put(key, allowed=None, num=False):
        v = d.get(key)
        if v in (None, "", "null", "None"):
            return
        if num:
            try:
                out[key] = max(0.0, float(str(v).replace(",", "").replace("$", "")))
            except (TypeError, ValueError):
                return
        else:
            v = str(v).strip()
            if allowed and v not in allowed:
                return
            out[key] = v

    put("company_name"); put("industry"); put("region")
    put("monthly_budget", num=True); put("storage_gb", num=True); put("expected_users", num=True)
    put("compute_req", _COMPUTE); put("data_sensitivity", _SENS)
    put("availability", _AVAIL); put("compliance", _COMP)
    for k in ("storage_gb", "expected_users"):
        if k in out:
            out[k] = int(out[k])
    return out


def extract_requirements(text):
    """Returns (fields_dict, source). Falls back to the rule-based parser."""
    text = (text or "").strip()
    if not text:
        return {}, "none"
    if gemini_ready():
        try:
            raw = _call_gemini(EXTRACT_PROMPT.format(text=text[:6000]))
            if raw:
                fields = _clean_extracted(_extract_json(raw))
                if fields:
                    return fields, "gemini"
        except Exception:
            pass
    return rx.rule_based(text), "rule-based"

# ---------------- suitability explanation ----------------

SUIT_PROMPT = """You are the cloud analyst of CloudInsight AI.
Company requirements: {req}
Automatic provider suitability results (best first): {suit}

Write 3-4 professional sentences explaining why {winner} is the most suitable provider for these
requirements, naming the decisive dimensions and the closest alternative. Treat all scores and costs
as configured estimates; do not invent data or mention providers outside the results."""


def suitability_explanation(inputs, suit):
    if not gemini_ready():
        return False, None
    try:
        brief = {k: inputs.get(k) for k in ("company_name", "industry", "region", "monthly_budget",
                                            "storage_gb", "expected_users", "compute_req",
                                            "data_sensitivity", "availability", "compliance")}
        top3 = [dict(provider=r["provider"], overall=r["overall"], verdict=r["verdict"])
                for r in suit["ranking"][:3]]
        text = _call_gemini(SUIT_PROMPT.format(req=json.dumps(brief), suit=json.dumps(top3), winner=suit["winner"]))
        return (True, text.strip()) if text else (False, None)
    except Exception:
        return False, None

# ---------------- offline fallbacks ----------------

def offline_insights(report):
    c, s, sec, i = report["cost"]["primary"], report["storage"], report["security"], report["inputs"]
    comp_share = round(c["compute"] / c["monthly"] * 100)
    stor_share = round(c["storage"] / c["monthly"] * 100)
    suit = report.get("suitability") or {}
    reco = (f" Automatic provider evaluation recommended {suit.get('winner')} "
            f"({next(r['overall'] for r in suit['ranking'] if r['provider'] == suit.get('winner'))}/100 suitability)."
            if suit.get("winner") else "")
    return dict(
        offline=True,
        summary=(f"{i['company_name']} on {i['provider']} is estimated at ${c['monthly']:,.2f}/month (${c['annual']:,.2f}/year). "
                 f"Compute accounts for ~{comp_share}% of the estimate and storage ~{stor_share}%. "
                 f"Security posture scores {sec['score']}/100 ({sec['risk_level']} risk) with {sec['high']} high and {sec['medium']} medium findings "
                 f"across 13 domains (simulated configuration analysis).{reco} "
                 "All figures are estimates — verify with provider pricing calculators."),
        cost_insights=(f"Compute: {report['compute']['primary']['instances']}× {report['compute']['primary']['instance']} at "
                       f"${c['compute']:,.2f}/mo. Storage: {s['required_gb']} GB on {s['primary']['service']} at ${s['primary']['per_gb']}/GB-mo "
                       f"= ${c['storage']:,.2f}/mo. Network/other uplift: ${c['network']:,.2f}/mo (10% + per-user baseline)."),
        security_insights=(f"Score {sec['score']}/100 ({sec['risk_level']}). Top gaps: "
                           + "; ".join(d["domain"] for d in sec["domains"] if d["status"] == "gap")
                           + ". Verification recommended: " + "; ".join(d["domain"] for d in sec["domains"] if d["status"] == "review") + "."),
        storage_insights=(f"{s['required_gb']} GB with a ×{s['redundancy_multiplier']} redundancy factor for '{i['data_sensitivity']}' data. "
                          f"{s['primary']['service']} is estimated at ${s['primary']['monthly']:,.2f}/mo (${s['primary']['annual']:,.2f}/yr). "
                          f"Cheapest configured estimate: {min(report['cost']['by_provider'], key=report['cost']['by_provider'].get)}."),
        risk_explanation=(f"Risk level '{sec['risk_level']}' is driven by {sec['high']} control gaps and {sec['medium']} unverified controls, "
                          f"weighted by data sensitivity ('{i['data_sensitivity']}'), compliance target ('{i['compliance']}') "
                          f"and availability requirement ('{i['availability']}')."),
        optimization=["Move infrequently accessed objects to cold tiers via lifecycle policies.",
                      "Use committed-use / reserved capacity for the baseline compute footprint.",
                      f"Compare {i['provider']} against cheaper configured baselines in the Cost Analysis section.",
                      "Set billing alerts at 80% and 100% of the monthly budget."],
        architecture=[f"Deploy {report['compute']['primary']['service']} across multiple availability zones.",
                      f"Keep {s['primary']['service']} private with endpoints; no public object paths.",
                      "Centralize logs and posture findings in the provider's security tooling."],
        recommendations=report["recommendations"][:6])


def offline_answer(question, report):
    q = question.lower()
    c, s, sec = report["cost"]["primary"], report["storage"], report["security"]
    pre = "• Offline data-derived answer (Gemini not configured on this server):\n\n"
    if "provider" in q and ("recommend" in q or "suitab" in q or "best" in q or "why" in q):
        suit = report.get("suitability")
        if suit:
            return pre + (f"All six providers were scored automatically. {suit['winner']} ranked first with "
                          f"{next(r['overall'] for r in suit['ranking'] if r['provider'] == suit['winner'])}/100. "
                          + " ".join(suit["factors"][:3]))
    if "cost" in q and ("high" in q or "why" in q):
        share = round(c["compute"] / c["monthly"] * 100)
        return pre + (f"Your estimate is ${c['monthly']:,.2f}/mo. Compute is ~{share}% "
                      f"({report['compute']['primary']['instances']}× {report['compute']['primary']['instance']} at 730 h/mo on-demand rates), "
                      f"storage ~${c['storage']:,.2f}/mo for {s['required_gb']} GB (×{s['redundancy_multiplier']} redundancy for "
                      f"'{report['inputs']['data_sensitivity']}' data), plus ${c['network']:,.2f} network/other uplift. "
                      "These are configured list-price estimates — committed-use discounts would lower real costs.")
    if "storage" in q and "cost" in q or "reduce storage" in q:
        return pre + (f"Storage is estimated at ${s['primary']['per_gb']}/GB-mo (×{s['redundancy_multiplier']} redundancy) = "
                      f"${s['primary']['monthly']:,.2f}/mo for {s['required_gb']} GB. Reductions: lifecycle tiering to cold storage after 30 days, "
                      "trimming the redundancy factor for less critical data, and comparing providers — the configured baselines differ per provider.")
    if "security risk" in q or "main security" in q or "risks" in q:
        gaps = "; ".join(d["domain"] for d in sec["domains"] if d["status"] == "gap") or "none"
        reviews = "; ".join(d["domain"] for d in sec["domains"] if d["status"] == "review") or "none"
        return pre + f"Score {sec['score']}/100 ({sec['risk_level']}). High-priority gaps: {gaps}. Needs verification: {reviews}. This is simulated configuration analysis, not a live scan."
    if "secure my cloud storage" in q or ("storage" in q and "secur" in q):
        return pre + "\n".join("– " + r for r in report["storage"]["security_recs"])
    if "improve first" in q or "first" in q:
        return pre + "Start with the highest-priority gaps:\n" + "\n".join("– " + r for r in report["recommendations"][:3])
    if "storage security" in q:
        return pre + (f"Encryption: {report['storage']['requirements']['encryption']}. "
                      f"Backup: {report['storage']['requirements']['backup']}. "
                      f"Retention: {report['storage']['requirements']['retention']}.")
    return None