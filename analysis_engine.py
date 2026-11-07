"""
CloudInsight AI — analysis engine.

AUTOMATIC PROVIDER SELECTION: the engine evaluates ALL six supported providers
against the extracted requirements and recommends the most suitable one. No
provider is chosen by the user.

DATA ACCURACY: every pricing figure is a CONFIGURED ESTIMATE based on approximate
published list prices (pay-as-you-go, single-region baseline). Suitability scores
are deterministic rule-based assessments from configured capability baselines —
not live provider measurements.
"""
import math
from datetime import datetime

import pricing_service as pricing

HOURS = 730
EGRESS_GB_PER_USER = 0.25   # ASSUMPTION: outbound internet GB per expected user per month (shown in every report)
NOTE_ESTIMATE = "Estimates only, not billing amounts - verify with the provider's pricing calculator."

POSTURE_TOOLS = {
    "AWS": "AWS Security Hub", "Microsoft Azure": "Microsoft Defender for Cloud secure score",
    "Google Cloud": "Security Command Center", "IBM Cloud": "IBM Cloud Security and Compliance Center",
    "Oracle Cloud": "OCI Cloud Guard", "Tencent Cloud": "Tencent Cloud Advisor / CSP",
}

PROVIDERS = {
    "AWS": dict(
        storage_service="Amazon S3 (Standard)",
        compute_service="Amazon EC2 — Auto Scaling group behind an ALB",
        encryption="SSE-S3 / SSE-KMS (AES-256), customer-managed keys via AWS KMS",
        backup="S3 Versioning + Cross-Region Replication, AWS Backup plans",
        availability="99.99% SLA target (quoted, S3 Standard)",
        scalability="Horizontal autoscaling across ≥2 Availability Zones; serverless options scale to zero",
        security_notes=["S3 Block Public Access at account level",
                        "CloudTrail data events for object-level auditing",
                        "GuardDuty / Security Hub for posture management"],
        strengths=["Broadest managed-service catalogue", "Mature IAM and auditing tooling",
                   "Strong ecosystem for analytics workloads"],
        limitations=["Cost model is complex; egress fees add up", "Requires discipline to avoid misconfiguration"],
        compliance=["SOC 1/2/3", "ISO 27001", "PCI DSS", "HIPAA-eligible", "GDPR-ready"],
        cap=dict(security=88, scalability=92, availability=95),
    ),
    "Microsoft Azure": dict(
        storage_service="Azure Blob Storage (Hot, LRS)",
        compute_service="Azure Virtual Machine Scale Sets + Load Balancer",
        encryption="Storage Service Encryption (AES-256), CMK via Azure Key Vault",
        backup="Azure Backup + GRS / RA-GRS replication options",
        availability="99.99% SLA target (quoted, LRS hot blobs)",
        scalability="VM Scale Sets across availability zones; Azure Autoscale rules",
        security_notes=["Defender for Cloud secure score", "Storage firewall + private endpoints",
                        "Diagnostic logs to Log Analytics"],
        strengths=["Tight Microsoft / Active Directory integration", "Strong hybrid story (Arc, Stack)",
                   "Enterprise agreements often reduce effective cost"],
        limitations=["Resource-group scoping can confuse teams", "Pay-as-you-go VM pricing above reserved baselines"],
        compliance=["SOC 1/2/3", "ISO 27001", "PCI DSS", "HIPAA (BAA)", "GDPR-ready"],
        cap=dict(security=87, scalability=90, availability=95),
    ),
    "Google Cloud": dict(
        storage_service="Cloud Storage (Standard)",
        compute_service="Compute Engine managed instance groups + Cloud Load Balancing",
        encryption="Encryption at rest by default (AES-256), CMEK via Cloud KMS",
        backup="Object Versioning + Lifecycle Management, dual/multi-region buckets",
        availability="99.99% SLA target (quoted, Standard class)",
        scalability="Managed instance groups with autoscaler; global load balancing",
        security_notes=["Security Command Center for posture", "Uniform bucket-level access + IAM Conditions",
                        "VPC Service Controls for data perimeters"],
        strengths=["Sustained-use discounts applied automatically", "Strong data-analytics stack (BigQuery)",
                   "Clean IAM and billing granularity"],
        limitations=["Smaller enterprise ecosystem in some regions", "Support tiers priced separately"],
        compliance=["SOC 1/2/3", "ISO 27001", "PCI DSS", "HIPAA (BAA)", "GDPR-ready"],
        cap=dict(security=86, scalability=91, availability=95),
    ),
    "IBM Cloud": dict(
        storage_service="IBM Cloud Object Storage (Standard)",
        compute_service="IBM Cloud Virtual Servers (auto-scale groups)",
        encryption="Server-side encryption with IBM Key Protect (Keep Your Own Key)",
        backup="Cross-region bucket policies, IBM Cloud Backup",
        availability="99.95% SLA target (quoted)",
        scalability="Auto-scale groups across multi-zone regions",
        security_notes=["IAM access groups with context-based restrictions",
                        "Hyper Protect Crypto Services for key custody", "Activity Tracker event logging"],
        strengths=["KYOK / Hyper Protect crypto for regulated data", "Strong financial-services heritage",
                   "Bare-metal options"],
        limitations=["Smaller ecosystem and marketplace", "Fewer managed AI/analytics services than the big three"],
        compliance=["SOC 2 Type II", "ISO 27001", "PCI DSS", "HIPAA-ready", "GDPR-ready"],
        cap=dict(security=84, scalability=82, availability=90),
    ),
    "Oracle Cloud": dict(
        storage_service="Oracle Cloud Object Storage (Standard)",
        compute_service="OCI Compute — flexible shapes with autoscaling",
        encryption="Encryption at rest by default, customer-managed keys in OCI Vault",
        backup="Object Storage versioning + cross-region replication policies",
        availability="99.95% SLA target (quoted)",
        scalability="Flexible shape sizing; autoscaling across availability domains",
        security_notes=["OCI Cloud Guard and Security Zones", "Vault-managed master encryption keys",
                        "Audit service retention of control-plane logs"],
        strengths=["Generous always-free tier / lower egress pricing", "Strong fit for Oracle DB workloads",
                   "Flexible shape sizing"],
        limitations=["Smaller third-party ecosystem", "Regional availability narrower than the big three"],
        compliance=["SOC 1/2/3", "ISO 27001", "PCI DSS", "HIPAA (BAA)", "GDPR-ready"],
        cap=dict(security=83, scalability=80, availability=90),
    ),
    "Tencent Cloud": dict(
        storage_service="Tencent Cloud Object Storage (COS Standard)",
        compute_service="CVM Auto Scaling groups + CLB",
        encryption="Server-side encryption (SSE-COS / SSE-KMS via KMS)",
        backup="COS versioning + cross-region replication",
        availability="99.95% SLA target (quoted)",
        scalability="Auto Scaling groups across availability zones; strong APAC network",
        security_notes=["CAM (Cloud Access Management) policies", "Host security agents + posture checks",
                        "Audit logs bridged to CLS"],
        strengths=["Very competitive APAC pricing", "Strong China-mainland network options",
                   "Bundled security tooling"],
        limitations=["Limited presence outside APAC", "Compliance documentation thinner for EU/US frameworks"],
        compliance=["ISO 27001", "PCI DSS", "CSA STAR", "GDPR-ready"],
        cap=dict(security=80, scalability=81, availability=90),
    ),
}

SENSITIVITY = {
    "Public":       {"mult": 1.0, "retention": "30 days"},
    "Internal":     {"mult": 1.0, "retention": "90 days"},
    "Confidential": {"mult": 1.3, "retention": "180 days"},
    "Restricted":   {"mult": 1.6, "retention": "365 days"},
}
COMPUTE_BASE = {"Light": 2, "Moderate": 4, "Heavy": 8, "Intensive": 16}
TIER = {"Light": 0, "Moderate": 1, "Heavy": 2, "Intensive": 3}
PERF_NOTES = {
    "Light": "Single-zone baseline is sufficient; burstable instances acceptable.",
    "Moderate": "Balanced vCPU/memory profile; enable autoscaling for daily peaks.",
    "Heavy": "CPU/memory-optimized shapes recommended; reserve capacity for baseline load.",
    "Intensive": "Multi-AZ deployment with load testing; dedicated performance tiers advised.",
}

CAPABILITY = {
    "AWS":             dict(compute=96, storage=96, database=95, networking=95, security=88, scalability=92, availability=95,
                            db_note="Amazon RDS, Aurora, DynamoDB, Redshift"),
    "Microsoft Azure": dict(compute=94, storage=94, database=93, networking=94, security=87, scalability=90, availability=95,
                            db_note="Azure SQL Database, Cosmos DB, PostgreSQL Flexible Server"),
    "Google Cloud":    dict(compute=93, storage=94, database=92, networking=93, security=86, scalability=91, availability=95,
                            db_note="Cloud SQL, AlloyDB, Spanner, Bigtable, Firestore"),
    "IBM Cloud":       dict(compute=80, storage=84, database=82, networking=80, security=84, scalability=82, availability=90,
                            db_note="IBM Db2, Cloud Databases for PostgreSQL/MySQL"),
    "Oracle Cloud":    dict(compute=83, storage=84, database=95, networking=82, security=83, scalability=80, availability=90,
                            db_note="Oracle Autonomous Database, Exadata Cloud"),
    "Tencent Cloud":   dict(compute=84, storage=85, database=84, networking=85, security=80, scalability=81, availability=90,
                            db_note="TencentDB for MySQL, PostgreSQL, TDSQL"),
}
DIM_LABELS = {"compute": "Compute", "storage": "Storage", "database": "Database", "networking": "Networking",
              "security": "Security", "scalability": "Scalability", "availability": "Availability & reliability",
              "cost": "Estimated cost", "compliance": "Compliance"}


def _price_fields(q):
    return dict(pricing_status=q["status"], pricing_source=q["source"], pricing_as_of=q["as_of"],
                region_used=q["region_used"], region_adjusted=q["region_adjusted"])


def storage_profiles(gb, sens, prices):
    """Billed GB = required GB x retained-copies factor (an assumption tied to data sensitivity).
    The per-GB rate is the provider's rate and is NOT multiplied."""
    mult = SENSITIVITY[sens]["mult"]
    billed_gb = round(gb * mult, 2)
    out = []
    for name, p in PROVIDERS.items():
        q = prices[name]["storage"]
        monthly = round(billed_gb * q["value"], 2)
        out.append(dict(provider=name, service=p["storage_service"], per_gb=round(q["value"], 4),
                        billed_gb=billed_gb, monthly=monthly, annual=round(monthly * 12, 2),
                        encryption=p["encryption"], backup=p["backup"], retention=SENSITIVITY[sens]["retention"],
                        security=p["security_notes"], **_price_fields(q)))
    return out


def compute_profiles(req, users, prices):
    need = COMPUTE_BASE[req] + math.ceil(max(users, 1) / 2000)
    out = []
    for name, p in PROVIDERS.items():
        q = prices[name]["compute"]
        inst = math.ceil(need / q["vcpu"])
        monthly = round(q["value"] * inst * HOURS, 2)
        out.append(dict(provider=name, service=p["compute_service"], instance=q["instance"],
                        instances=inst, vcpu_needed=need, hourly=q["value"], monthly=monthly,
                        annual=round(monthly * 12, 2), scalability=p["scalability"],
                        performance=PERF_NOTES[req], **_price_fields(q)))
    return out, need


def network_profiles(users, prices):
    """Network = outbound internet egress only: users x EGRESS_GB_PER_USER x provider egress rate."""
    egress_gb = round(max(users, 1) * EGRESS_GB_PER_USER, 2)
    out = []
    for name in PROVIDERS:
        q = prices[name]["egress"]
        monthly = round(egress_gb * q["value"], 2)
        out.append(dict(provider=name, egress_gb=egress_gb, rate=q["value"], monthly=monthly,
                        annual=round(monthly * 12, 2), **_price_fields(q)))
    return out


def cost_rollup(sprofs, cprofs, nprofs):
    comp = {c["provider"]: c for c in cprofs}
    net = {n["provider"]: n for n in nprofs}
    rows = []
    for s in sprofs:
        cm, nw = comp[s["provider"]], net[s["provider"]]
        monthly = round(s["monthly"] + cm["monthly"] + nw["monthly"], 2)
        parts = (s, cm, nw)
        rows.append(dict(provider=s["provider"], storage=s["monthly"], compute=cm["monthly"], network=nw["monthly"],
                         monthly=monthly, annual=round(monthly * 12, 2),
                         storage_rate=s["per_gb"], hourly=cm["hourly"], egress_rate=nw["rate"],
                         egress_gb=nw["egress_gb"], billed_gb=s["billed_gb"],
                         pricing_status=pricing.combine_status([x["pricing_status"] for x in parts]),
                         component_status=dict(storage=s["pricing_status"], compute=cm["pricing_status"],
                                               network=nw["pricing_status"]),
                         pricing_as_of=min([x["pricing_as_of"] for x in parts if x["pricing_as_of"]
                                            and x["pricing_status"] in ("live", "cached")], default=None)))
    return rows


def _compliance_match(required, p):
    frameworks = p["compliance"]
    if not required or required == "None":
        return min(90, 50 + 8 * len(frameworks)), \
               "No specific framework required — scored on breadth of the provider's compliance portfolio."
    key = required.lower().split()[0]
    for f in frameworks:
        if key in f.lower():
            extra = 3 if len(frameworks) >= 5 else 0
            return min(97, 90 + extra), f"'{required}' is covered by the provider's published compliance portfolio."
    return 55, f"No '{required}' attestation found in the configured compliance profile — verify directly with the provider."


def suitability_analysis(inputs, costs):
    """Scores ALL six providers across nine dimensions with requirement-adaptive weights."""
    gb = max(inputs["storage_gb"], 1)
    users = max(inputs["expected_users"], 1)
    tier = TIER[inputs["compute_req"]]
    vcpu = COMPUTE_BASE[inputs["compute_req"]] + math.ceil(users / 2000)
    sens, avail, comp = inputs["data_sensitivity"], inputs["availability"], inputs["compliance"]
    region = str(inputs["region"])
    by = {c["provider"]: c for c in costs}
    cheapest_val = min(c["monthly"] for c in costs)
    cheapest_name = min(by, key=lambda n: by[n]["monthly"])

    w = dict(compute=0.10, storage=0.10, database=0.08, networking=0.07, security=0.13,
             scalability=0.10, availability=0.12, cost=0.16, compliance=0.14)
    if inputs["monthly_budget"] and cheapest_val > inputs["monthly_budget"]:
        w["cost"] += 0.06
    elif inputs["monthly_budget"]:
        w["cost"] += 0.03
    if sens in ("Confidential", "Restricted"):
        w["security"] += 0.05
    if sens == "Restricted":
        w["compliance"] += 0.03
    if comp != "None":
        w["compliance"] += 0.04
    if avail == "Mission-critical":
        w["availability"] += 0.05
    elif avail == "High":
        w["availability"] += 0.03
    if tier >= 2:
        w["scalability"] += 0.04
        w["compute"] += 0.03
    if gb > 5000:
        w["storage"] += 0.03
    tot = sum(w.values())
    weights = {k: v / tot for k, v in w.items()}

    advanced_db = sens == "Restricted" or users > 10000
    apac = region.startswith("ap-")
    results = {}
    for name, p in PROVIDERS.items():
        cap, c = CAPABILITY[name], by[name]
        sc, notes = {}, {}

        sc["compute"] = round(cap["compute"] - tier * (100 - cap["compute"]) / 10, 1)
        notes["compute"] = (f"'{inputs['compute_req']}' workload (~{vcpu} vCPU baseline) against a compute "
                            f"capability baseline of {cap['compute']}/100.")

        pen = (2 if gb > 5000 else 0) + (2 if gb > 20000 else 0)
        sc["storage"] = round(max(0, cap["storage"] - (pen if cap["storage"] < 90 else 0)), 1)
        notes["storage"] = (f"{gb} GB on {p['storage_service']} — estimated ${c['storage']:,.2f}/mo "
                            f"(${c['storage_rate']}/GB-mo × {SENSITIVITY[sens]['mult']} retained-copies factor, {c['pricing_status']} pricing).")

        sc["database"] = round(cap["database"] - (4 if advanced_db and cap["database"] < 90 else 0), 1)
        notes["database"] = cap["db_note"] + (" — an advanced, high-availability database posture is required."
                                              if advanced_db else ".")

        net = cap["networking"] + (4 if (name == "Tencent Cloud" and apac) else 0)
        sc["networking"] = round(min(100, net), 1)
        notes["networking"] = (f"Region '{region}' against a network capability baseline of {cap['networking']}/100"
                               + (" (+ APAC network strength bonus)." if name == "Tencent Cloud" and apac else "."))

        sc["security"] = round(cap["security"], 1)
        notes["security"] = (f"'{sens}' data sensitivity — security capability baseline {cap['security']}/100 "
                             f"({POSTURE_TOOLS[name]}).")

        sc["scalability"] = round(cap["scalability"] - tier * (100 - cap["scalability"]) / 12, 1)
        notes["scalability"] = p["scalability"]

        av_pen = 6 if (avail == "Mission-critical" and cap["availability"] < 93) else \
                 (2 if (avail == "High" and cap["availability"] < 93) else 0)
        sc["availability"] = round(cap["availability"] - av_pen, 1)
        notes["availability"] = f"'{avail}' availability requirement vs {p['availability']}."

        eff = round(cheapest_val / c["monthly"] * 100, 1)
        sc["cost"] = eff
        notes["cost"] = (f"Estimated ${c['monthly']:,.2f}/mo ({c['pricing_status']} pricing) — {eff}% of the "
                         f"cheapest estimate ({cheapest_name}).")

        csc, cnote = _compliance_match(comp, p)
        sc["compliance"], notes["compliance"] = csc, cnote

        overall = round(sum(sc[k] * weights[k] for k in weights), 1)
        results[name] = dict(dimensions=sc, notes=notes, overall=overall)

    ranked = sorted(results, key=lambda n: -results[n]["overall"])
    for i, name in enumerate(ranked):
        gap = results[ranked[0]]["overall"] - results[name]["overall"]
        results[name]["verdict"] = ("Recommended" if i == 0 else
                                    "Strong alternative" if gap <= 3 else
                                    "Viable option" if gap <= 8 else
                                    "Less suitable for these requirements")
    winner = ranked[0]
    avg = {k: round(sum(results[n]["dimensions"][k] for n in results) / len(results), 1) for k in weights}
    edges = sorted(((k, results[winner]["dimensions"][k] - avg[k]) for k in weights), key=lambda x: -x[1])
    factors = [f"{DIM_LABELS[k]}: {results[winner]['dimensions'][k]}/100 vs a {avg[k]} provider average "
               f"(dimension weight {weights[k] * 100:.0f}%)." for k, d in edges[:3] if d >= -2]
    factors.append(results[winner]["notes"]["cost"])
    if comp != "None":
        factors.append(results[winner]["notes"]["compliance"])
    factors.append(f"Closest alternative: {ranked[1]} at {results[ranked[1]]['overall']}/100 "
                   f"({round(results[winner]['overall'] - results[ranked[1]]['overall'], 1)} points behind).")

    return dict(winner=winner, scores=results,
                ranking=[dict(provider=n, overall=results[n]["overall"], verdict=results[n]["verdict"])
                         for n in ranked],
                weights={k: round(v, 3) for k, v in weights.items()},
                dim_labels=DIM_LABELS, factors=factors, average=avg,
                method_note=("Suitability is scored deterministically (0–100) from configured capability "
                             "assessments and estimated costs; dimension weights adapt to the requirements. "
                             "No live provider data is used — verify results with provider calculators."))


def security_posture(inputs):
    sens, comp = inputs["data_sensitivity"], inputs["compliance"]
    avail, req = inputs["availability"], inputs["compute_req"]
    P = PROVIDERS[inputs["provider"]]
    hi, top = sens in ("Confidential", "Restricted"), sens == "Restricted"
    strict, crit = comp != "None", avail in ("High", "Mission-critical")
    heavy = req in ("Heavy", "Intensive")
    domains = []
    add = lambda n, s, note: domains.append(dict(domain=n, status=s, note=note))

    add("Identity & Access Management", "review",
        "Least-privilege policies and access reviews could not be verified — validate roles, service accounts and cross-account access."
        if hi else "Verify least-privilege IAM policies and schedule periodic access reviews.")
    if strict or hi:
        add("MFA", "gap", "MFA enforcement could not be verified — enforce MFA for all human identities and break-glass accounts.")
    else:
        add("MFA", "review", "Enable MFA for all user accounts before production rollout.")
    add("Encryption at Rest", "ok", f"{P['encryption']} — verify key policy and rotation.")
    add("Encryption in Transit", "ok", "TLS 1.2+ is enforced on managed endpoints — confirm custom endpoints and client configuration.")
    add("Network Security", "review" if crit else "ok",
        "Confirm VPC segmentation, private endpoints for storage and no direct public paths."
        if crit else "Default VPC isolation is available — validate subnet design before launch.")
    add("Firewall", "review", "Enable provider firewall / security groups with deny-by-default rules; place a WAF in front of public endpoints.")
    if strict:
        add("Logging & Monitoring", "gap", f"Audit logging must satisfy {comp} — enable {POSTURE_TOOLS[inputs['provider']]} and export logs to an immutable store.")
    else:
        add("Logging & Monitoring", "ok", f"Enable {POSTURE_TOOLS[inputs['provider']]} and ship platform logs to an immutable store.")
    if hi or avail == "Mission-critical":
        add("Backup & Recovery", "gap", "Verified backup and restore testing required — define RPO/RTO and automate cross-region copies.")
    else:
        add("Backup & Recovery", "review", "Enable automated backups and test restores quarterly.")
    add("Vulnerability Management", "review" if (heavy or strict) else "ok",
        "Enable provider image/workload vulnerability scanning and patch SLAs for compute workloads.")
    if sens == "Public":
        add("Public Exposure", "ok", "Public delivery is intended — still keep Block Public Access defaults and use signed URLs where possible.")
    else:
        add("Public Exposure", "review", "Block public access at account level; serve data through private endpoints or signed access.")
    add("Misconfiguration Risk", "review", f"Enable posture management ({POSTURE_TOOLS[inputs['provider']]}) and remediate findings on a defined SLA.")
    if top:
        add("Data Protection", "gap", "Restricted data requires DLP classification, customer-managed keys and residency controls.")
    elif hi:
        add("Data Protection", "review", "Define a data classification scheme and key-management procedures.")
    else:
        add("Data Protection", "ok", "Provider-managed AES-256 is acceptable — document key ownership.")
    if strict:
        add("Compliance", "gap", f"Formal {comp} mapping required — collect evidence and schedule the audit cycle.")
    else:
        add("Compliance", "ok", "No specific framework targeted; baseline provider best practices apply.")

    high = sum(1 for d in domains if d["status"] == "gap")
    medium = sum(1 for d in domains if d["status"] == "review")
    low = sum(1 for d in domains if d["status"] == "ok")
    score = max(35, min(97, round(100 - 9 * high - 4.5 * medium - 1.2 * low)))
    level = "Low" if score >= 85 else "Medium" if score >= 70 else "High" if score >= 55 else "Critical"
    register = [dict(title=f"{d['domain']} — control gap", level="High", likelihood="Likely",
                     impact="Severe" if top else "High", mitigation=d["note"])
                for d in domains if d["status"] == "gap"]
    register += [dict(title=f"{d['domain']} — verification needed", level="Medium", likelihood="Possible",
                      impact="Moderate", mitigation=d["note"])
                 for d in domains if d["status"] == "review"]
    return dict(score=score, risk_level=level, high=high, medium=medium, low=low, domains=domains,
                register=register, tool=POSTURE_TOOLS[inputs["provider"]],
                disclaimer="Simulated configuration-based analysis — not a live cloud security scan. "
                           "Connect cloud credentials to enable real posture data.")


def comparison_scores(costs):
    by = {c["provider"]: c for c in costs}
    cheapest = min(c["monthly"] for c in costs)
    out = {}
    for name, p in PROVIDERS.items():
        out[name] = dict(
            security=p["cap"]["security"], scalability=p["cap"]["scalability"],
            availability=p["cap"]["availability"], compliance=min(95, 45 + 10 * len(p["compliance"])),
            cost_efficiency=round(cheapest / by[name]["monthly"] * 100))
    return out


def run_analysis(form, extraction_meta=None):
    inputs = dict(
        company_name=form.get("company_name", "Untitled").strip()[:160] or "Untitled",
        industry=form.get("industry", "Other"), region=form.get("region", "us-east-1"),
        monthly_budget=float(form.get("monthly_budget") or 0),
        storage_gb=int(form.get("storage_gb") or 0),
        expected_users=int(form.get("expected_users") or 0),
        compute_req=form.get("compute_req", "Moderate"),
        data_sensitivity=form.get("data_sensitivity", "Internal"),
        availability=form.get("availability", "Standard"),
        compliance=form.get("compliance", "None"))
    if inputs["compute_req"] not in COMPUTE_BASE:
        inputs["compute_req"] = "Moderate"
    if inputs["data_sensitivity"] not in SENSITIVITY:
        inputs["data_sensitivity"] = "Internal"
    if inputs["availability"] not in ("Basic", "Standard", "High", "Mission-critical"):
        inputs["availability"] = "Standard"
    inputs["extraction"] = extraction_meta or dict(source="form", file_name=None, fields_from_file=[])

    gb, users = max(inputs["storage_gb"], 1), max(inputs["expected_users"], 1)
    sens = inputs["data_sensitivity"]
    mult = SENSITIVITY[sens]["mult"]
    hi = sens in ("Confidential", "Restricted")

    region = inputs["region"]
    prices = pricing.get_all_prices(PROVIDERS.keys(), region)
    price_block = pricing.summarize(prices, region)
    sprofs = storage_profiles(gb, sens, prices)
    cprofs, vcpu_needed = compute_profiles(inputs["compute_req"], users, prices)
    nprofs = network_profiles(users, prices)
    costs = cost_rollup(sprofs, cprofs, nprofs)
    egress_gb = nprofs[0]["egress_gb"]
    billed_gb = sprofs[0]["billed_gb"]

    suit = suitability_analysis(inputs, costs)
    inputs["provider"] = suit["winner"]
    inputs["provider_selected_by"] = "Automatic suitability analysis — all six providers evaluated"

    sec = security_posture(inputs)
    primary_cost = next(c for c in costs if c["provider"] == inputs["provider"])
    budget_util = round(primary_cost["monthly"] / inputs["monthly_budget"] * 100, 1) if inputs["monthly_budget"] else None
    by_provider = {c["provider"]: c["monthly"] for c in costs}
    comp_share = round(primary_cost["compute"] / primary_cost["monthly"] * 100)

    recommendations = [d["note"] for d in sec["domains"] if d["status"] == "gap"]
    recommendations += [d["note"] for d in sec["domains"] if d["status"] == "review"]
    if budget_util and budget_util > 100:
        recommendations.append(f"Estimated monthly cost (${primary_cost['monthly']:,.2f}) exceeds the stated budget — consider committed-use pricing and storage lifecycle tiering.")
    recommendations += ["Enable object lifecycle policies to move infrequently accessed data to cold tiers after 30 days.",
                        "Tag all resources by environment and owner for cost allocation and anomaly detection.",
                        "Schedule quarterly restore tests and access reviews.",
                        f"Review egress patterns — ${primary_cost['network']:,.2f}/month of the estimate is outbound network transfer (assumed {egress_gb:,.0f} GB/month)."]
    if sec["register"]:
        sec["register"].append(dict(title="Budget overrun", level="Medium" if (budget_util or 0) <= 100 else "High",
                                    likelihood="Possible", impact="Moderate",
                                    mitigation="Set billing alerts at 80% and 100% of the monthly budget."))

    s_primary = next(s for s in sprofs if s["provider"] == inputs["provider"])
    c_primary = next(c for c in cprofs if c["provider"] == inputs["provider"])
    cheapest = min(by_provider, key=by_provider.get)
    dearest = max(by_provider, key=by_provider.get)
    counts = price_block["counts"]
    live_line = (f"Pricing basis for this report ({price_block['generated_at']}): {counts['live']} live, "
                 f"{counts['cached']} cached and {counts['estimated']} estimated price points across the six providers "
                 f"(overall: {price_block['overall_status']}). Live data is currently available for Azure only; "
                 "all other prices are stored estimates, labelled as such.")
    if not price_block["live_enabled"]:
        live_line += " Live pricing is switched off (PRICING_LIVE=0)."
    ref_note = (f"Region requested: '{region}'. "
                + ("Azure prices were fetched for the matching Azure region; " if counts["live"] + counts["cached"] else "")
                + ("Stored estimates are US reference prices, so prices in other regions can differ."
                   if region != "us-east-1" else "Stored estimates are US East reference prices."))
    assumptions = [
        f"All six providers were evaluated automatically; dimension weights adapted to the stated requirements "
        f"(cost {suit['weights']['cost']*100:.0f}%, security {suit['weights']['security']*100:.0f}%, "
        f"compliance {suit['weights']['compliance']*100:.0f}%, availability {suit['weights']['availability']*100:.0f}%, others in the report).",
        f"Storage is billed on {billed_gb:,.0f} GB = {gb:,} GB required × {mult} retained-copies factor for '{sens}' data "
        "(a modelling assumption, not a provider charge) at each provider's per-GB-month rate for standard single-region object storage.",
        f"Compute sized at {vcpu_needed} vCPU ('{inputs['compute_req']}' workload + 1 vCPU per 2,000 expected users), "
        f"deployed as {c_primary['instances']}× {c_primary['instance']} on {inputs['provider']}.",
        f"{HOURS} hours per month, on-demand Linux pricing; no reservations, spot, negotiated discounts, load balancers, disks, "
        "licences, support plans or managed databases are included.",
        f"Network = outbound internet egress only: {users:,} users × {EGRESS_GB_PER_USER} GB/user/month = {egress_gb:,.0f} GB/month "
        "at each provider's first-tier egress rate. No free-tier allowance is applied because allowances are not verified in this version.",
        live_line, ref_note,
        f"Currency: {price_block['currency']}. Annual cost = monthly cost × 12. " + NOTE_ESTIMATE,
    ]
    parts = {"Compute": primary_cost["compute"], "Storage": primary_cost["storage"], "Network egress": primary_cost["network"]}
    top_name = max(parts, key=parts.get)
    top_share = round(parts[top_name] / primary_cost["monthly"] * 100) if primary_cost["monthly"] else 0
    s_rates = {s["provider"]: s["per_gb"] for s in sprofs}
    n_rates = {n["provider"]: n["rate"] for n in nprofs}
    s_lo, s_hi = min(s_rates, key=s_rates.get), max(s_rates, key=s_rates.get)
    n_lo, n_hi = min(n_rates, key=n_rates.get), max(n_rates, key=n_rates.get)
    cost_drivers = [
        f"{top_name} is the largest driver ({top_share}% of the {inputs['provider']} estimate).",
        f"Compute: {c_primary['instances']}× {c_primary['instance']} at ${c_primary['hourly']}/hr × {HOURS} h "
        f"({c_primary['pricing_status']} price).",
        f"Storage rates range from ${s_rates[s_lo]}/GB-mo ({s_lo}) to ${s_rates[s_hi]}/GB-mo ({s_hi}); at {billed_gb:,.0f} billed GB "
        f"that moves the monthly estimate by ${round(billed_gb * (s_rates[s_hi] - s_rates[s_lo]), 2):,.2f}.",
        f"Egress rates range from ${n_rates[n_lo]}/GB ({n_lo}) to ${n_rates[n_hi]}/GB ({n_hi}); the assumed {egress_gb:,.0f} GB/month "
        "is a planning figure - real egress depends on your traffic.",
        f"Cheapest estimate: {cheapest} (${by_provider[cheapest]:,.2f}/mo); highest: {dearest} (${by_provider[dearest]:,.2f}/mo). "
        "Differences reflect on-demand list-price baselines, not committed-use discounts.",
    ]
    storage_requirements = dict(
        encryption="Customer-managed keys (CMK) required" if hi else "Provider-managed AES-256 acceptable",
        backup="Daily automated backups + cross-region copy" if (hi or inputs["availability"] == "Mission-critical") else "Daily automated backups, weekly archive",
        retention=SENSITIVITY[sens]["retention"])
    storage_security_recs = ["Block public access at the account/bucket level", "Enforce TLS 1.2+ on all endpoints",
                             "Enable access and audit logging", "Apply lifecycle policies instead of manual deletion"]
    if hi:
        storage_security_recs += ["Use customer-managed keys with rotation", "Enable object-level audit logging"]

    w_overall = suit["scores"][suit["winner"]]["overall"]
    return dict(
        inputs=inputs,
        exec_summary=[
            f"All six supported providers (AWS, Microsoft Azure, Google Cloud, IBM Cloud, Oracle Cloud, Tencent Cloud) were "
            f"evaluated automatically against the stated requirements; {suit['winner']} ranked first with an overall "
            f"suitability of {w_overall}/100 and was selected for the detailed analysis below.",
            f"{inputs['company_name']} is evaluated for a '{inputs['compute_req']}' compute workload in {inputs['region']}, "
            f"with {gb} GB of {sens}-class storage and {users} expected users.",
            f"The estimate on {inputs['provider']} ({primary_cost['pricing_status']} pricing) totals ${primary_cost['monthly']:,.2f}/month (${primary_cost['annual']:,.2f}/year)"
            + (f" — {budget_util}% of the stated monthly budget." if budget_util else "."),
            f"Security posture scores {sec['score']}/100 ({sec['risk_level']} risk) with {sec['high']} high, {sec['medium']} medium and {sec['low']} low findings across 13 domains — simulated configuration analysis, not a live scan.",
            "All figures are estimates. " + NOTE_ESTIMATE,
        ],
        storage=dict(required_gb=gb, storage_type="Object storage (standard tier, single region)",
                     redundancy_multiplier=mult, primary=s_primary, providers=sprofs,
                     requirements=storage_requirements, security_recs=storage_security_recs),
        compute=dict(requirement=inputs["compute_req"], vcpu_needed=vcpu_needed, primary=c_primary,
                     providers=cprofs, performance=PERF_NOTES[inputs["compute_req"]]),
        cost=dict(primary=primary_cost, comparison=costs, budget_utilization=budget_util,
                  cost_drivers=cost_drivers, by_provider=by_provider),
        security=sec,
        suitability=suit,
        comparison_scores=comparison_scores(costs),
        recommendations=recommendations[:8],
        assumptions=assumptions,
        pricing=price_block,
        meta=dict(generated_at=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
                  pricing_source=live_line, pricing_status=price_block["overall_status"],
                  notes=[NOTE_ESTIMATE]))
