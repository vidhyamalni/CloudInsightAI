"""CloudInsight AI - Azure live pricing (official Azure Retail Prices API).

Endpoint: https://prices.azure.com/api/retail/prices  (public, no key or login needed)
Docs:     https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices

Only standard-library code is used (urllib), so no new dependency is required.
Every function raises PricingError when the API answer is missing or not trustworthy;
pricing_service.py catches that and falls back to the stored estimate.
"""
import json
import urllib.request
from urllib.parse import quote

API_URL = "https://prices.azure.com/api/retail/prices"
SOURCE_NAME = "Azure Retail Prices API"
SUPPORTED = ("storage", "compute")          # egress stays an estimate (zone-based meters)

# App region -> Azure region (closest equivalent; only ever taken from this table,
# never from free user text, so it can be safely placed in the API filter).
REGION_MAP = {
    "us-east-1": "eastus",
    "us-west-2": "westus2",
    "eu-west-1": "northeurope",          # Ireland
    "eu-central-1": "germanywestcentral",  # Frankfurt
    "ap-southeast-1": "southeastasia",   # Singapore
    "ap-northeast-1": "japaneast",       # Tokyo
}
DEFAULT_REGION = "eastus"                # used for 'Global / multi-region'


class PricingError(Exception):
    """The live price could not be obtained or was not trustworthy."""


def _http_get_json(url, timeout):
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "User-Agent": "CloudInsightAI/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:      # nosec - fixed https host
        return json.loads(resp.read().decode("utf-8"))


def _query(odata_filter, timeout, max_pages=3):
    """Run one filtered query and return the list of price items (follows paging)."""
    url = API_URL + "?$filter=" + quote(odata_filter, safe="'=")
    items = []
    for _ in range(max_pages):
        page = _http_get_json(url, timeout)
        if not isinstance(page, dict):
            raise PricingError("unexpected response shape")
        items.extend(page.get("Items") or [])
        url = page.get("NextPageLink")
        if not url:
            break
    return items


def resolve_region(app_region):
    """Return (azure_region, exact) - exact is False when a default had to be used."""
    if app_region in REGION_MAP:
        return REGION_MAP[app_region], True
    return DEFAULT_REGION, False


def _usable(item):
    try:
        return item.get("currencyCode") == "USD" and float(item.get("retailPrice", 0)) > 0
    except (TypeError, ValueError):
        return False


def fetch_storage(azure_region, timeout):
    """Hot LRS block-blob capacity price, USD per GB-month (first pricing tier)."""
    f = (f"serviceName eq 'Storage' and armRegionName eq '{azure_region}' "
         f"and meterName eq 'Hot LRS Data Stored' and priceType eq 'Consumption'")
    cands = [i for i in _query(f, timeout) if _usable(i)
             and "General Block Blob" in str(i.get("productName", ""))
             and "Hierarchical" not in str(i.get("productName", ""))
             and "Premium" not in str(i.get("productName", ""))
             and str(i.get("skuName", "")) == "Hot LRS"
             and str(i.get("unitOfMeasure", "")).strip().lower() == "1 gb/month"]
    if not cands:
        raise PricingError("no Hot LRS block-blob capacity meter found")
    first_tier = [i for i in cands if float(i.get("tierMinimumUnits", 0)) == 0] or cands
    best = min(first_tier, key=lambda i: float(i["retailPrice"]))
    return float(best["retailPrice"]), dict(unit="USD per GB-month", meter=best.get("meterName"),
                                           product=best.get("productName"), effective=best.get("effectiveStartDate"))


def fetch_compute(azure_region, sku, timeout):
    """Linux pay-as-you-go VM price, USD per hour."""
    f = (f"serviceName eq 'Virtual Machines' and armRegionName eq '{azure_region}' "
         f"and armSkuName eq '{sku}' and priceType eq 'Consumption'")
    cands = [i for i in _query(f, timeout) if _usable(i)
             and "Windows" not in str(i.get("productName", ""))
             and "Spot" not in str(i.get("skuName", "")) and "Low Priority" not in str(i.get("skuName", ""))
             and str(i.get("unitOfMeasure", "")).strip().lower() == "1 hour"]
    if not cands:
        raise PricingError("no Linux on-demand VM meter found for " + sku)
    best = min(cands, key=lambda i: float(i["retailPrice"]))
    return float(best["retailPrice"]), dict(unit="USD per hour", meter=best.get("meterName"),
                                           product=best.get("productName"), effective=best.get("effectiveStartDate"))


def fetch(component, app_region, base_entry, timeout):
    """Dispatcher used by pricing_service. Returns dict(value, region_used, exact, detail)."""
    region, exact = resolve_region(app_region)
    if component == "storage":
        value, detail = fetch_storage(region, timeout)
    elif component == "compute":
        sku = base_entry.get("azure_sku")
        if not sku:
            raise PricingError("no Azure SKU configured")
        value, detail = fetch_compute(region, sku, timeout)
    else:
        raise PricingError("component not supported live: " + component)
    return dict(value=value, region_used=region, exact=exact, detail=detail)
