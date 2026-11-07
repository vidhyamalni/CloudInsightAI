"""CloudInsight AI — company-file parsing and rule-based requirement extraction."""
import io, json, csv, re

MAX_BYTES = 2 * 1024 * 1024  # 2 MB

KEYS = ["company_name", "industry", "region", "monthly_budget", "storage_gb",
        "expected_users", "compute_req", "data_sensitivity", "availability", "compliance"]

# ---------------- file reading ----------------

def read_file(fs):
    name = fs.filename or ""
    ext = name.rsplit(".", 1)[1].lower() if "." in name else ""
    data = fs.read()
    if len(data) > MAX_BYTES:
        raise ValueError("File exceeds the 2 MB limit.")
    if ext in ("txt", "md"):
        return data.decode("utf-8", "ignore")
    if ext == "json":
        return _flatten(json.loads(data.decode("utf-8", "ignore")))
    if ext == "csv":
        rows = list(csv.reader(io.StringIO(data.decode("utf-8", "ignore"))))
        return "\n".join(": ".join(c.strip() for c in r if c.strip()) for r in rows if r)
    if ext == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError("PDF support needs pypdf — run: pip install pypdf")
        try:
            reader = PdfReader(io.BytesIO(data))
            return "\n".join((pg.extract_text() or "") for pg in reader.pages[:20])
        except Exception:
            raise ValueError("The PDF could not be parsed.")
    raise ValueError("Unsupported file type. Use TXT, MD, JSON, CSV or PDF.")


def _flatten(obj, prefix=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.append(_flatten(v, f"{prefix}{k}: " if prefix else f"{k}: "))
    elif isinstance(obj, list):
        for v in obj:
            out.append(_flatten(v, prefix))
    else:
        out.append(f"{prefix}{obj}")
    return "\n".join(x for x in out if x)

# ---------------- rule-based extraction ----------------

_INDUSTRIES = [("health", "Healthcare"), ("hospital", "Healthcare"), ("pharma", "Healthcare"),
               ("bank", "Finance"), ("financ", "Finance"), ("fintech", "Finance"), ("insur", "Finance"),
               ("retail", "Retail"), ("e-commerce", "Retail"), ("ecommerce", "Retail"), ("shop", "Retail"),
               ("manufactur", "Manufacturing"), ("logistic", "Manufacturing"),
               ("school", "Education"), ("univers", "Education"), ("educat", "Education"), ("edtech", "Education"),
               ("media", "Media"), ("gaming", "Media"), ("stream", "Media"),
               ("software", "Technology"), ("saas", "Technology"), ("tech", "Technology"), ("startup", "Technology")]

def rule_based(text):
    t = (text or "").lower()
    f = {}

    m = re.search(r"company\s*(?:name)?\s*[:\-]\s*([A-Za-z0-9&.'\- ]{2,60})", text, re.I)
    if m: f["company_name"] = m.group(1).strip()
    for k in ("company_name", "company", "name"):
        m = re.search(rf'"{k}"\s*:\s*"([^"]+)"', text)
        if m: f["company_name"] = m.group(1).strip(); break

    for kw, ind in _INDUSTRIES:
        if kw in t: f["industry"] = ind; break

    if re.search(r"\beu\b|europe|germany|france|ireland|london|uk\b", t): f["region"] = "eu-west-1"
    elif re.search(r"singapore|apac|asia|mumbai|sydney|india", t): f["region"] = "ap-southeast-1"
    elif re.search(r"japan|tokyo|seoul", t): f["region"] = "ap-northeast-1"
    elif re.search(r"\bus\b|america|virginia|ohio|oregon|canada", t): f["region"] = "us-east-1"

    m = (re.search(r"\$\s*([\d,]+(?:\.\d+)?)\s*(?:k\b|,000)?(?:\s*(?:per\s*)?month|/mo|monthly)?", t)
         or re.search(r"(?:budget|spend)\D{0,15}([\d,]+)", t))
    if m:
        v = m.group(1).replace(",", "")
        try:
            val = float(v)
            if re.search(r"\d\s*k\b", t[max(0, m.start() - 2):m.end() + 2]): val *= 1000
            if val < 10000: f["monthly_budget"] = val
        except ValueError:
            pass

    m = re.search(r"(\d+(?:\.\d+)?)\s*(tb|terabyte)", t)
    if m: f["storage_gb"] = int(float(m.group(1)) * 1024)
    else:
        m = re.search(r"(\d[\d,]*)\s*(gb|gigabyte)", t)
        if m: f["storage_gb"] = int(m.group(1).replace(",", ""))

    m = re.search(r"(\d[\d,]*)\s*(?:\+)?\s*(?:expected\s*)?(?:users|concurrent|employees|customers|subscribers|students|patients)", t)
    if m: f["expected_users"] = int(m.group(1).replace(",", ""))

    if re.search(r"mission[- ]critical|99\.9\d|24/7|zero downtime|always available", t): f["availability"] = "Mission-critical"
    elif re.search(r"high availab|99\.9|minimal downtime", t): f["availability"] = "High"

    if re.search(r"\bphi\b|patient record|health record|cardholder|classified|trade secret|highly sensitive", t):
        f["data_sensitivity"] = "Restricted"
    elif re.search(r"confidential|\bpii\b|personal data|financial data|customer data", t):
        f["data_sensitivity"] = "Confidential"
    elif re.search(r"public website|publicly available|open data|marketing content", t):
        f["data_sensitivity"] = "Public"

    if re.search(r"\bhipaa\b", t): f["compliance"] = "HIPAA"
    elif re.search(r"\bgdpr\b", t): f["compliance"] = "GDPR"
    elif re.search(r"\bpci\b|cardholder|payment card", t): f["compliance"] = "PCI DSS"
    elif re.search(r"\bsoc\s*2\b", t): f["compliance"] = "SOC 2"
    elif re.search(r"\biso\s*27001\b", t): f["compliance"] = "ISO 27001"

    if re.search(r"machine learning|ml training|model training|rendering|simulation|video encoding|genomic|batch processing", t):
        f["compute_req"] = "Intensive"
    elif re.search(r"heavy|high traffic|analytics platform|data processing|compute[- ]intensive", t):
        f["compute_req"] = "Heavy"
    elif re.search(r"static website|light|internal tool|small blog|low traffic", t):
        f["compute_req"] = "Light"

    return f

# ---------------- merge ----------------

def merge(form, extracted):
    """Uploaded-file values override the form, except an explicitly typed company name."""
    merged = {k: form.get(k, "") for k in KEYS}
    from_file = []
    for k, v in (extracted or {}).items():
        if k == "company_name" and str(merged.get("company_name", "")).strip():
            continue
        merged[k] = v
        from_file.append(k)
    return merged, from_file