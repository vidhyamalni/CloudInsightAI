"""CloudInsight AI — AI diagnostic v2 (auto-discovers available models)."""
import os, sys, warnings
warnings.filterwarnings("ignore")

print("=" * 55)
print("CloudInsight AI — AI DIAGNOSTIC v2")
print("=" * 55)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_service
k = ai_service._api_key()
print(f"\nKey: {'OK (' + k[:8] + '...)' if k else 'MISSING'}")
if not k:
    print("Fix the key first (gemini_key.txt next to app.py).")
    sys.exit(1)

from google import genai as ggenai
client = ggenai.Client(api_key=k)

print("\nAsking Google which models your key can use...")
names = []
try:
    for m in client.models.list():
        names.append(m.name)
except Exception as e:
    print(f"Model list failed: {type(e).__name__}: {str(e)[:300]}")
    sys.exit(1)

flash = [n for n in names if "flash" in n.lower() and "embed" not in n.lower()]
print(f"Found {len(names)} models total. Flash models available:")
for n in flash[:12]:
    print("   ", n)

if not flash:
    print("\nNo flash models visible — paste this ENTIRE output to me.")
    sys.exit(1)

print("\nTesting each until one answers...")
chosen = None
for n in flash:
    short = n.split("/")[-1]
    try:
        resp = client.models.generate_content(model=short, contents="Say OK")
        if resp and getattr(resp, "text", None):
            print(f"   {short}: SUCCESS  (reply: {resp.text[:40].strip()})")
            chosen = short
            break
    except Exception as e:
        msg = str(e)[:100].replace("\n", " ")
        print(f"   {short}: failed ({msg})")

print("\n" + "=" * 55)
if chosen:
    print(f"WORKING MODEL: {chosen}")
    print(f'>>> ai_service.py must contain:  NEW_MODEL = "{chosen}"')
else:
    print("No working model found — paste this ENTIRE output to me.")
print("=" * 55)