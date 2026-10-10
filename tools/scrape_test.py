"""
ניסוי: האם אפשר להוריד אוטומטית קבצי שקיפות מחירים מכל רשת?
לכל רשת מנסים להוריד קובץ סניפים אחד וקובץ PriceFull אחד, בתהליך נפרד עם הגבלת זמן.
התוצאה נכתבת ל-results/scrape_test.md ו-results/scrape_test.json.
"""
import glob, gzip, io, json, os, re, shutil, subprocess, sys, time, zipfile
from datetime import datetime, timezone, timedelta

CHAIN_HE = {
    "SHUFERSAL": "שופרסל", "RAMI_LEVY": "רמי לוי", "YOHANANOF": "יוחננוף", "OSHER_AD": "אושר עד",
    "VICTORY_NEW_SOURCE": "ויקטורי", "TIV_TAAM": "טיב טעם", "YAYNO_BITAN_AND_CARREFOUR": "קרפור/יינות ביתן",
    "HAZI_HINAM": "חצי חינם", "MAHSANI_ASHUK_NEW_SOURCE": "מחסני השוק", "KESHET": "קשת טעמים",
    "SUPER_PHARM": "סופר-פארם", "GOOD_PHARM": "גוד פארם", "BAREKET": "ברקת", "CITY_MARKET_KIRYATGAT": "סיטי מרקט ק\"ג",
    "CITY_MARKET_SHOPS": "סיטי מרקט", "DOR_ALON": "דור אלון", "HET_COHEN_NEW_SOURCE": "ח. כהן",
    "KING_STORE": "קינג סטור", "NETIV_HASED": "נתיב החסד", "POLIZER": "פוליצר", "SALACH_DABACH": "סאלח דבאח",
    "SHEFA_BARCART_ASHEM": "שפע ברכת השם", "SHUK_AHIR": "שוק העיר", "STOP_MARKET": "סטופ מרקט",
    "SUPER_YUDA": "סופר יודה", "SUPER_SAPIR": "סופר ספיר", "FRESH_MARKET_AND_SUPER_DOSH": "פרש מרקט",
    "YELLOW": "יילו", "ZOL_VEBEGADOL": "זול ובגדול", "WOLT": "וולט מרקט",
}
TIMEOUT = int(os.environ.get("CHAIN_TIMEOUT", "300"))

CHILD = r'''
import sys
from il_supermarket_scarper import ScarpingTask
chain, ftype, out = sys.argv[1], sys.argv[2], sys.argv[3]
t = ScarpingTask(enabled_scrapers=[chain], files_types=[ftype], multiprocessing=1,
                 output_configuration={"output_mode": "disk", "base_storage_path": out},
                 status_configuration={"database_type": "json", "base_path": out + "/status"},
                 timeout_in_seconds=%d)
t.start(limit=1); t.join()
''' % TIMEOUT


def read_any(path):
    data = open(path, "rb").read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data)); data = z.read(z.namelist()[0])
    return data


def run(chain, ftype):
    out = os.path.abspath(f"dumps_test/{chain}_{ftype}")
    shutil.rmtree(out, ignore_errors=True); os.makedirs(out, exist_ok=True)
    t0 = time.time(); err = ""
    try:
        p = subprocess.run([sys.executable, "-c", CHILD, chain, ftype, out], capture_output=True, text=True, timeout=TIMEOUT + 30)
        err = (p.stderr or "")[-400:]
    except subprocess.TimeoutExpired:
        err = "timeout"
    files = [f for f in glob.glob(out + "/**/*", recursive=True)
             if os.path.isfile(f) and "/status" not in f and not f.endswith(".json")]
    return files, round(time.time() - t0), err


def main():
    chains = list(CHAIN_HE)
    only = os.environ.get("ONLY")
    if only: chains = [c for c in chains if c in only.split(",")]
    res = []
    for c in chains:
        r = {"chain": c, "he": CHAIN_HE.get(c, c)}
        sf, r["stores_sec"], e1 = run(c, "STORE_FILE")
        if sf:
            try:
                x = read_any(sf[0]).decode("utf-8", "ignore")
                r["stores"] = len(re.findall(r"<store>|<STORE>|<Store>|<branch>", x)) or len(re.findall(r"(?i)<storeid>", x))
            except Exception as ex:
                r["stores"] = f"? ({ex})"
        pf, r["price_sec"], e2 = run(c, "PRICE_FULL_FILE")
        if pf:
            name = os.path.basename(pf[0]); r["price_file"] = name
            m = re.findall(r"(20\d{10})", name)
            r["price_time"] = m[-1] if m else ""
            try:
                x = read_any(pf[0]).decode("utf-8", "ignore")
                r["items"] = len(re.findall(r"(?i)<itemcode>", x))
            except Exception as ex:
                r["items"] = f"? ({ex})"
        r["ok_stores"] = bool(sf); r["ok_prices"] = bool(pf)
        if not (sf and pf): r["error"] = (e2 or e1).strip().splitlines()[-1:] if (e2 or e1) else ["no files found"]
        print(json.dumps(r, ensure_ascii=False), flush=True)
        res.append(r)
        shutil.rmtree("dumps_test", ignore_errors=True)

    os.makedirs("results", exist_ok=True)
    json.dump(res, open("results/scrape_test.json", "w"), ensure_ascii=False, indent=1)
    now = datetime.now(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M")
    ok = sum(r["ok_prices"] for r in res)
    lines = [f"# ניסוי הורדה אוטומטית – {now} (שעון ישראל)", "",
             f"**{ok} מתוך {len(res)} רשתות** הורדו בהצלחה מהשרת של GitHub (ארה\"ב).", "",
             "| רשת | סניפים | מחירים | מס' סניפים | מוצרים בקובץ | זמן הקובץ | הערה |", "|---|---|---|---|---|---|---|"]
    for r in res:
        t = r.get("price_time", "")
        t = f"{t[6:8]}/{t[4:6]} {t[8:10]}:{t[10:12]}" if len(t) == 12 else ""
        lines.append(f"| {r['he']} | {'✅' if r['ok_stores'] else '❌'} | {'✅' if r['ok_prices'] else '❌'} | "
                     f"{r.get('stores', '')} | {r.get('items', '')} | {t} | {' '.join(r.get('error', []))[:80].replace('|', '/')} |")
    open("results/scrape_test.md", "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
