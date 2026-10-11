"""
השרת הישראלי: מוריד את הרשתות שחוסמות גישה מחוץ לישראל ומעלה את הקבצים ל-Release של GitHub,
משם ה-workflow הרגיל בונה את koneh-data.bin. רץ כל שעה (cron): הורדה מלאה כל 4 שעות, ובשאר השעות – קבצי שינויים.
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone

REPO = "orizvi15-sys/koneh-ishi"; REL = "data-latest"
CHAINS = ["VICTORY_NEW_SOURCE", "HAZI_HINAM", "MAHSANI_ASHUK_NEW_SOURCE", "HET_COHEN_NEW_SOURCE", "NETIV_HASED"]
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def api(method, url, data=None, ctype="application/json"):
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json", "Content-Type": ctype, "User-Agent": "koneh-il"})
    with urllib.request.urlopen(req, timeout=300) as r:
        b = r.read(); return json.loads(b) if b else {}


def release():
    return api("GET", f"https://api.github.com/repos/{REPO}/releases/tags/{REL}")


def upload(path):
    rel = release(); name = os.path.basename(path)
    for a in rel.get("assets", []):
        if a["name"] == name: api("DELETE", a["url"])
    up = rel["upload_url"].split("{")[0] + f"?name={name}"
    api("POST", up, open(path, "rb").read(), "application/octet-stream")
    print("uploaded", name, os.path.getsize(path), flush=True)


def download_prev(chain, dest):
    url = f"https://github.com/{REPO}/releases/download/{REL}/chain-{chain}.json.gz"
    try:
        urllib.request.urlretrieve(url, dest); return True
    except Exception:
        return False


def run(chain, full):
    out = os.path.join(HERE, "out"); prev_dir = os.path.join(HERE, "prev")
    os.makedirs(out, exist_ok=True); os.makedirs(prev_dir, exist_ok=True)
    for f in os.listdir(out): os.remove(os.path.join(out, f))
    prev = os.path.join(prev_dir, f"chain-{chain}.json.gz")
    have_prev = download_prev(chain, prev)
    env = dict(os.environ, PREV_CHAIN_FILE=prev if have_prev else "")
    py = sys.executable; col = os.path.join(ROOT, "tools", "collect.py")
    if full or not have_prev:
        r = subprocess.run([py, col, chain, out], cwd=HERE, env=env)
        if r.returncode: subprocess.run([py, col, "--status-only", chain, out], cwd=HERE, env=env)
    else:
        subprocess.run([py, col, "--delta", chain, out], cwd=HERE, env=env)
    for f in sorted(os.listdir(out)):
        try: upload(os.path.join(out, f))
        except Exception as ex: print("upload failed", f, ex, flush=True)


def test():
    """בדיקה מהירה: האם אתרי הרשתות נפתחים מהשרת הזה"""
    from il_supermarket_scarper import ScarpingTask
    for c in CHAINS:
        d = os.path.join(HERE, "test", c)
        t0 = time.time()
        try:
            t = ScarpingTask(enabled_scrapers=[c], files_types=["STORE_FILE"], multiprocessing=1, timeout_in_seconds=300,
                             output_configuration={"output_mode": "disk", "base_storage_path": d},
                             status_configuration={"database_type": "json", "base_path": d + "/status"})
            t.start(limit=1); t.join()
            n = sum(len(fs) for p, _, fs in os.walk(d) if "status" not in p)
            print(f"{'✅' if n else '❌'} {c}: {n} files ({time.time() - t0:.0f}s)", flush=True)
        except Exception as ex:
            print(f"❌ {c}: {ex!r}"[:300], flush=True)


if __name__ == "__main__":
    if sys.argv[1:] == ["test"]: test(); sys.exit()
    if not TOKEN: sys.exit("GITHUB_TOKEN missing (/opt/koneh/.env)")
    full = datetime.now(timezone.utc).hour % 4 == 0 or "--full" in sys.argv
    print(datetime.now().isoformat(timespec="minutes"), "full" if full else "delta", flush=True)
    for c in CHAINS:
        try: run(c, full)
        except Exception as ex: print("chain failed", c, ex, flush=True)
