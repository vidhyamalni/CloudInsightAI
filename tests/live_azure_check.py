"""One-off REAL-network check of the Azure live price fetch. Run from the project root:

    python tests/live_azure_check.py

It makes real calls to https://prices.azure.com (needs internet) and prints what came back, so YOU can
confirm the parser matches the live API. If it prints FAIL, send me the output and I will adjust the parser.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PRICING_LIVE"] = "1"
os.environ["PRICING_CACHE_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_live_check_cache.json")
import pricing_service as ps

ok = True
for region in ("us-east-1", "eu-west-1", "ap-southeast-1"):
    ps._breaker.clear()          # show each call's own result, not 'skipped after a recent failure'
    q = ps.get_prices("Microsoft Azure", region)
    for comp in ("storage", "compute"):
        r = q[comp]
        good = r["status"] == "live"
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {region:15} {comp:8} {r['value']:.4f} {r['unit']:18} status={r['status']:9} "
              f"region_used={r['region_used']}\n      {r['note']}")
print("\nOVERALL:", "PASS - live Azure pricing works" if ok else "FAIL - see notes above (stored estimates were used instead)")
try:
    os.remove(os.environ["PRICING_CACHE_PATH"])
except OSError:
    pass
