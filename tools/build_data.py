"""
בונה את קובץ הנתונים הארצי koneh-data.bin מכל קבצי chain-*.json.gz.

  python tools/build_data.py IN_DIR OUT_FILE --geocache geocache.json --cities cities.json [--max-geocode 3000]

מבנה הקובץ (נקרא ע"י kdata.py בבוט):
  b"KISH" + uint32 אורך כותרת + zlib(JSON כותרת) + בלוקים דחוסים של מחירים לכל סניף.
  כל בלוק: zlib( uint32 n + n*uint16 הפרשי אינדקסים + n*uint32 מחירים באגורות ).
"""
import argparse, array, glob, gzip, hashlib, json, os, re, struct, sys, time, urllib.parse, urllib.request, zlib
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

IL = timezone(timedelta(hours=3))
UA = {"User-Agent": "koneh-ishi-price-bot/1.0 (+https://github.com/orizvi15-sys/koneh-ishi)"}
MAX_ITEMS = 65000
# מיקומים שאומתו ידנית
OVERRIDES = {
    "7290058140886-64": [32.5072, 34.9186],    # רמי לוי אור עקיבא – בקובץ רשום "חיפה"
    "7290803800003-7": [31.8605, 34.8225],     # יוחננוף עקרון – "המלך חסן 1" מוקם בטעות בשדרות
    "7290027600007-106": [31.921009, 34.7933387], "7290055700007-422": [31.9220671, 34.8099931],
    "7290803800003-25": [31.930427, 34.7973195], "7290696200003-82": [31.914131, 34.7889942],
    "7290027600007-276": [31.891911, 34.789574], "7290058140886-41": [31.8933542, 34.7865316],
    "7290803800003-31": [31.8942716, 34.7900746], "7290803800003-1": [31.8865401, 34.7802358],
    "7290873255550-18": [31.9094971, 34.8055737], "7290058140886-43": [31.9845909, 34.7695795],
    "7290103152017-25": [31.9913597, 34.7613372], "7290803800003-28": [31.9828662, 34.7923622],
}


def load_json(p, default):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return default


def fetch_cities():
    """טבלת סמלי יישובים של הלמ"ס מ-data.gov.il"""
    url = "https://data.gov.il/api/3/action/datastore_search?resource_id=5c78e9fa-c2e2-4771-93ff-7f400a12f7ba&limit=5000"
    try:
        recs = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60))["result"]["records"]
        out = {}
        for r in recs:
            code = str(r.get("סמל_ישוב", "")).strip(); name = str(r.get("שם_ישוב", "")).strip()
            if code.isdigit() and name: out[str(int(code))] = re.sub(r"\s+", " ", name)
        print("cities:", len(out)); return out
    except Exception as ex:
        print("cities fetch failed", ex); return {}


def nominatim(q):
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1, "countrycodes": "il"})
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        if r:
            lat, lng = float(r[0]["lat"]), float(r[0]["lon"])
            if 29.3 < lat < 33.5 and 34.2 < lng < 35.95: return [lat, lng]
    except Exception as ex:
        print("geocode error", q, ex); time.sleep(5)
    return None


def nominatim_struct(street, city):
    """חיפוש מובנה (רחוב + עיר) – מצליח לעתים קרובות כשחיפוש חופשי נכשל"""
    q = {"street": street, "city": city, "country": "ישראל", "format": "json", "limit": 1}
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(q)
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30))
        if r:
            lat, lng = float(r[0]["lat"]), float(r[0]["lon"])
            if 29.3 < lat < 33.5 and 34.2 < lng < 35.95: return [lat, lng]
    except Exception as ex:
        print("geocode error", street, city, ex); time.sleep(5)
    return None


GKEY = os.environ.get("GOOGLE_MAPS_KEY", "")
GOOD_TYPES = {"street_address", "premise", "subpremise", "establishment", "point_of_interest", "store", "supermarket",
              "grocery_or_supermarket", "shopping_mall", "route", "intersection"}


def google(q):
    """Google Geocoding – רק תוצאה מדויקת (כתובת/מקום), לא מרכז עיר"""
    url = "https://maps.googleapis.com/maps/api/geocode/json?" + urllib.parse.urlencode(
        {"address": q, "region": "il", "language": "he", "components": "country:IL", "key": GKEY})
    try:
        r = json.load(urllib.request.urlopen(url, timeout=30))
        if r.get("status") not in ("OK", "ZERO_RESULTS"): print("google:", r.get("status"), r.get("error_message", "")[:200])
        for x in r.get("results", [])[:1]:
            if not GOOD_TYPES & set(x.get("types", [])) or x.get("partial_match") and "route" in x.get("types", []): return None
            loc = x["geometry"]["location"]; lat, lng = loc["lat"], loc["lng"]
            if 29.3 < lat < 33.5 and 34.2 < lng < 35.95: return [lat, lng]
    except Exception as ex:
        print("google error", q, ex)
    return None


ABBR = {'ת"א': "תל אביב - יפו", "תא": "תל אביב - יפו", 'ב"ש': "באר שבע", 'פ"ת': "פתח תקווה", 'ראשל"צ': "ראשון לציון",
        'ר"ג': "רמת גן", 'ק"ש': "קריית שמונה", 'ק. גת': "קריית גת", "ירושלים": "ירושלים",
        "מודיעין": "מודיעין-מכבים-רעות", "קדימה": "קדימה-צורן", "פרדס חנה": "פרדס חנה-כרכור", "דלית אל כרמל": "דאלית אל-כרמל",
        "חצור הגלילית": "חצור הגלילית", "מודיעין עלית": "מודיעין עילית", "עקרון": "קרית עקרון", "קרית עקרון": "קרית עקרון", "מודיעין עילית": "מודיעין עילית"}
CITY_FIX = {"אריאל": [32.1043, 35.1745], "מודיעין עילית": [31.9318, 35.0431]}   # מרכזי ערים שחיפוש השם מחזיר לא נכון
AMBIG = {"כרמל", "אזור", "רמות", "מגדל", "עין", "שורש", "לכיש", "גבעת", "נווה", "כפר", "מרכז", "צומת", "בית", "חצור", "רמת", "עזר", "שמיר", "ספיר", "סגולה"}   # מילים שהן גם שם יישוב קטן


def make_city_finder(cities):
    """מוצא שם עיר בתוך שם הסניף/הכתובת (כשהקובץ לא כולל עיר) – 'יוחננוף טבריה תחתית' → טבריה"""
    names = {}
    for n in set(cities.values()):
        base = re.sub(r"\s+[-–]\s+.*$|\s*\(.*$", "", n).strip()   # "תל אביב - יפו" → "תל אביב"
        for x in {n, base}:
            if len(x) >= 3 and x not in AMBIG: names[x] = n
    order = sorted(names, key=len, reverse=True)
    def find1(t, street):
        t = f" {t} "; hits = []               # כל ההתאמות – בוחרים את זו שמופיעה ראשונה (ואם שוויון – הארוכה)
        for a, full in list(ABBR.items()) + list(names.items()):
            if a not in t or (street and a in STREET_WORDS): continue        # בדיקה מהירה לפני regex
            for m in re.finditer(rf"(?<![א-ת]){re.escape(a)}(?![א-ת])", t):
                if street and re.search(r"(שדרות|שד'|רחוב|רח'|דרך|כביש|צומת|מחלף|כיכר)\s*$", t[:m.start()]): continue
                hits.append((m.start(), -len(a), full)); break
        return min(hits)[2] if hits else ""
    def find(name, addr=""):        # רק משם הסניף – בכתובת שם עיר הוא לרוב שם רחוב ("רחוב תל אביב 11" בחיפה)
        return find1(name or "", False)
    return find


STREET_WORDS = {"שדרות", "גבעת", "נווה", "כפר", "רמת", "קריית", "מצפה", "עין", "בית"}


def dist_km(a, b):
    import math
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))


def clean_addr(a):
    a = re.sub(r"\b(רח'?|רחוב|שד'?|שדרות|שד\.)\s+", "", a or "")
    a = re.sub(r"\s*(,|-)?\s*(מרכז מסחרי|קניון|מתחם|א\.?ת\.?|אזור התעשייה|אזור תעשייה|פינת).*$", "", a)
    return re.sub(r"\s+", " ", a).strip(" ,")


def clean_name(n):
    return re.sub(r"\s+", " ", re.sub(r"^[\s*.\-]+|[\s*.\-]+$", "", n or "")).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("indir"); ap.add_argument("out")
    ap.add_argument("--geocache", default="geocache.json"); ap.add_argument("--cities", default="cities.json")
    ap.add_argument("--max-geocode", type=int, default=3000); ap.add_argument("--summary", default="")
    a = ap.parse_args()

    cities = load_json(a.cities, {})
    if len(cities) < 500:
        c2 = fetch_cities()
        if c2: cities = c2; json.dump(cities, open(a.cities, "w", encoding="utf-8"), ensure_ascii=False)
    geo = load_json(a.geocache, {})

    stores, prices, namecnt, chains, clubp, promop = {}, {}, defaultdict(Counter), {}, {}, {}
    for f in sorted(glob.glob(os.path.join(a.indir, "chain-*.json.gz"))):
        d = json.load(gzip.open(f, "rt", encoding="utf-8"))
        chains[d["chain"]] = {"fetched": d.get("fetched"), "stores": len(d["prices"]), "stats": d.get("stats")}
        for code, n in d["names"].items():
            n = clean_name(n)
            if n: namecnt[code][n] += 1
        old = (datetime.now(IL) - timedelta(days=10)).strftime("%Y%m%d")
        for k, v in d["prices"].items():
            ts = d["stores"][k].get("ts", "")
            if len(ts) >= 8 and ts[:2] == "20" and ts[:8] < old: continue  # קובץ ישן מדי (הרשת לא מעדכנת)
            if k in prices and stores[k].get("ts", "") > ts: continue
            prices[k] = v; stores[k] = d["stores"][k]
            NEVER = 2 ** 31 - 1
            ov = lambda m: {c: (tuple(v) if isinstance(v, list) else (v, NEVER)) for c, v in m.items()}
            if k in d.get("club", {}): clubp[k] = ov(d["club"][k])
            else: clubp.pop(k, None)
            if k in d.get("promo", {}): promop[k] = ov(d["promo"][k])
            else: promop.pop(k, None)
    print("chains", len(chains), "stores with prices", len(prices))

    # ---- שמות ערים ומיקומים ----
    city_geo = geo.setdefault("_city", {})
    q_left = a.max_geocode; t_last = 0

    def geocode(q):
        nonlocal q_left, t_last
        if q_left <= 0: return None
        q_left -= 1
        w = 1.1 - (time.time() - t_last)
        if w > 0: time.sleep(w)
        t_last = time.time()
        return nominatim(q)

    find_city = make_city_finder(cities) if cities else (lambda *a: "")
    for k, s in stores.items():
        c = s.get("city", "")
        if c.isdigit(): c = cities.get(str(int(c)), "")
        if not c: c = find_city(s["name"].replace(s["chain"], " "), s["addr"])          # אין עיר בקובץ – מנסים לזהות משם הסניף
        s["city"] = c
        if k in OVERRIDES: s["p"] = OVERRIDES[k]; s["prec"] = "manual"; continue
        ck = f"{s['chain']}|{s['name']}|{s['addr']}|{c}"
        if ck in geo and geo[ck]:
            p, prec = geo[ck]
            v2 = geo.setdefault("_v2", {})
            if prec in ("city", "name") and ck not in v2 and s["addr"] and c and q_left > 0:
                v2[ck] = 1; q_left -= 1
                w = 1.1 - (time.time() - t_last)
                if w > 0: time.sleep(w)
                t_last = time.time()
                p2 = nominatim_struct(clean_addr(s["addr"]), c)
                if p2: p, prec = p2, "address"; geo[ck] = [p, prec]
            s["p"], s["prec"] = p, prec; continue
        if ck in geo and geo[ck] is None and q_left < 500: continue
        p = prec = None
        addr = clean_addr(s["addr"])
        if addr and c: p = geocode(f"{addr}, {c}"); prec = "address"
        if not p and addr and not c: p = geocode(f"{addr}"); prec = "address"
        if not p and s["name"] and c: p = geocode(f"{s['chain']} {s['name']}, {c}"); prec = "name"
        if not p and c:
            if c not in city_geo: city_geo[c] = geocode(c)
            p = city_geo[c]; prec = "city"
        if not p and s["name"]: p = geocode(f"{s['name']}"); prec = "name"
        geo[ck] = [p, prec] if p else None
        if p: s["p"], s["prec"] = p, prec
    # ---- בדיקת שפיות: סניף שהמיקום שלו רחוק מהעיר שלו (למשל "יהודה הלוי 113" שנמצא בת"א במקום בטבריה) ----
    # מרכז העיר = חציון המיקומים של הסניפים בה (עמיד יותר מחיפוש שם העיר, שלפעמים מחזיר מקום אחר באותו שם)
    import statistics
    pts = {}
    for s in stores.values():
        if s.get("city") and s.get("p") and s.get("prec") in ("address", "google", "manual"): pts.setdefault(s["city"], []).append(s["p"])
    def center(c):
        if c in CITY_FIX: return CITY_FIX[c]
        L = pts.get(c, [])
        if len(L) >= 3: return [statistics.median(x[0] for x in L), statistics.median(x[1] for x in L)]
        if c not in city_geo and q_left > 0: city_geo[c] = geocode(c)
        return city_geo.get(c)
    def radius(c):                       # כמה רחוק מהמרכז סביר לסניף באותה עיר (לפי פיזור הסניפים בה)
        L = pts.get(c, []); cc = center(c)
        if len(L) < 3 or not cc: return 15
        return max(8, 2.5 * statistics.median(dist_km(x, cc) for x in L))
    bad = 0
    for k, s in stores.items():
        c = s.get("city"); p = s.get("p")
        if not c or not p or s.get("prec") in ("manual", "google"): continue
        cc = center(c)
        if not cc: continue
        if s.get("prec") == "city":      # מיקום לפי עיר – משתמשים במרכז העיר האמין (חיפוש שם עיר לפעמים מחזיר מקום אחר)
            s["p"] = cc; continue
        if dist_km(p, cc) > radius(c):
            bad += 1; print("far from its city:", s["chain"], s["name"], s["addr"], c, round(dist_km(p, cc)), "km")
            s["p"], s["prec"] = cc, "city"
    print("stores moved to their city center:", bad)

    # ---- Google: שיפור סניפים שמיקומם רק ברמת עיר/שם (פעם אחת לכל סניף, נשמר במטמון) ----
    if GKEY:
        gdone = geo.setdefault("_g", {}); used = 0
        for k, s in stores.items():
            if s.get("prec") in ("address", "manual", "google") and s.get("p") or used >= 1500: continue
            c = s["city"]; ck = f"{s['chain']}|{s['name']}|{s['addr']}|{c}"
            if ck in gdone: continue
            gdone[ck] = 1; addr = clean_addr(s["addr"]); p = None
            if addr and c: p = google(f"{addr}, {c}"); used += 1
            if not p and s["name"]: p = google(f"{s['chain']} {s['name']}" + (f", {c}" if c else "")); used += 1
            if p: s["p"], s["prec"] = p, "google"; geo[ck] = [p, "google"]
        print("google geocoding queries:", used)
    json.dump(geo, open(a.geocache, "w", encoding="utf-8"), ensure_ascii=False)
    located = {k: s for k, s in stores.items() if s.get("p")}
    print("located", len(located), "of", len(stores), "geocode queries used", a.max_geocode - q_left)

    # ---- קטלוג מוצרים ----
    avail = Counter()
    for k in located:
        avail.update(prices[k].keys())
    wch = defaultdict(set)                                   # מוצרי משקל שאוחדו לפי שם – באילו רשתות הם מופיעים
    for k in located:
        for c in prices[k]:
            if c.startswith("W:"): wch[c].add(stores[k].get("chain", ""))
    wok = sorted((c for c in wch if (len(wch[c]) >= 2 and avail[c] >= 4) or avail[c] >= 8), key=lambda c: -avail[c])[:6000]
    print("weighed items grouped by name:", len(wch), "kept:", len(wok), "in 2+ chains:", sum(1 for c in wok if len(wch[c]) >= 2))
    pcodes = sorted(c for c in avail if not c[0].isdigit() and not c.startswith("W:")) + wok
    codes = [c for c, n in avail.most_common() if c[0].isdigit() and n >= 3]
    codes = pcodes + codes[: MAX_ITEMS - len(pcodes)]
    codes = pcodes + sorted(codes[len(pcodes):], key=lambda c: -avail[c])
    idx = {c: i for i, c in enumerate(codes)}

    # ---- בלוקים לכל סניף ----
    blobs, blob_of, body, store_rows = [], {}, bytearray(), []
    vec_count = Counter(); vec_store = {}
    def pack(pmap):
        nonlocal body
        row = sorted((idx[c], int(round(p * 100))) for c, p in pmap.items() if c in idx and 0 < p < 50000)
        if not row: return None, None
        d = array.array("H"); pr = array.array("I"); last = 0
        for i, p in row:
            d.append(i - last); last = i; pr.append(p)
        raw = struct.pack("<I", len(row)) + d.tobytes() + pr.tobytes()
        h = hashlib.sha1(raw).hexdigest()
        if h not in blob_of:
            z = zlib.compress(raw, 9); blob_of[h] = len(blobs); blobs.append((len(body), len(z))); body += z
        return blob_of[h], (h, row)

    def pack_ov(omap):
        """שכבת מבצעים: אינדקס, מחיר באגורות, ותוקף (דקות מאז 1970)"""
        nonlocal body
        row = sorted((idx[c], int(round(p * 100)), int(e)) for c, (p, e) in omap.items() if c in idx and 0 < p < 50000)
        if not row: return None
        d = array.array("H"); pr = array.array("I"); en = array.array("I"); last = 0
        for i, p, e in row:
            d.append(i - last); last = i; pr.append(p); en.append(min(e, 2 ** 32 - 1))
        raw = struct.pack("<I", len(row)) + d.tobytes() + pr.tobytes() + en.tobytes()
        h = hashlib.sha1(raw).hexdigest()
        if h not in blob_of:
            z = zlib.compress(raw, 9); blob_of[h] = len(blobs); blobs.append((len(body), len(z))); body += z
        return blob_of[h]

    n_club = n_promo = 0
    for k in sorted(located):
        b, hr = pack(prices[k])
        if b is None: continue
        h, row = hr; vec_store.setdefault(h, row); vec_count[h] += 1
        cb = pack_ov(clubp[k]) if k in clubp else None
        pb = pack_ov(promop[k]) if k in promop else None
        n_club += cb is not None; n_promo += pb is not None
        s = located[k]
        store_rows.append([k, s["chain"], s["name"], s["addr"], s["city"], round(s["p"][0], 6), round(s["p"][1], 6),
                           b, s.get("ts", ""), s.get("prec", ""), -1 if cb is None else cb, -1 if pb is None else pb])
    print("stores with promos:", n_promo, "with club prices:", n_club)

    # חציון ארצי לכל מוצר (לפי וקטורים ייחודיים)
    per_item = defaultdict(list)
    for h, row in vec_store.items():
        for i, p in row: per_item[i].append(p)
    items = []
    for c in codes:
        i = idx[c]; v = sorted(per_item.get(i, [])); med = v[len(v) // 2] if v else 0
        # השם הארוך ביותר (יש רשתות שחותכות שמות ל-20 תווים)
        nm = max(namecnt[c], key=lambda n: (len(n), namecnt[c][n])) if namecnt[c] else c
        items.append([c, nm, avail[c], med])

    ver = datetime.now(IL).strftime("%Y%m%d%H%M")
    header = {"fmt": 2, "v": ver, "built": datetime.now(IL).isoformat(timespec="minutes"), "items": items, "stores": store_rows,
              "blobs": blobs, "chains": chains}
    hz = zlib.compress(json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode(), 9)
    with open(a.out, "wb") as f:
        f.write(b"KISH" + struct.pack("<I", len(hz)) + hz + bytes(body))
    print(f"wrote {a.out}: {os.path.getsize(a.out)/1e6:.1f}MB, items {len(items)}, stores {len(store_rows)}, unique blobs {len(blobs)}")

    if a.summary:
        by_chain = Counter(r[1] for r in store_rows)
        prec = Counter(r[9] for r in store_rows)
        L = [f"# נתוני קונה אישי – {header['built']}", "",
             f"**{len(store_rows)} סניפים** · **{len(items):,} מוצרים** · {len(chains)} רשתות", "",
             f"דיוק מיקום: {dict(prec)}", "", "| רשת | סניפים בקובץ |", "|---|---|"]
        L += [f"| {c} | {n} |" for c, n in by_chain.most_common()]
        L += ["", "| מקור (scraper) | עודכן | סניפים | קבצים |", "|---|---|---|---|"]
        L += [f"| {c} | {v['fetched']} | {v['stores']} | {(v.get('stats') or {}).get('price_files', '')} |" for c, v in sorted(chains.items())]
        sts = []
        for f in sorted(glob.glob(os.path.join(a.indir, "status-*.json"))):
            try: sts.append(json.load(open(f, encoding="utf-8")))
            except Exception: pass
        if sts:
            L += ["", "## מצב כל רשת בהרצה האחרונה", "", "| רשת | מצב | סניפים עם מחירים | קבצים | סיבה / שגיאות |", "|---|---|---|---|---|"]
            for x in sorted(sts, key=lambda x: (x.get("ok"), x["chain"])):
                why = x.get("reason") or ""
                if x.get("errors") and (not x.get("ok") or why): why += " — " + " / ".join(e.replace("|", "/") for e in x["errors"][-2:])
                L.append(f"| {x['chain']} | {'✅' if x.get('ok') and not x.get('reason') else ('⚠️' if x.get('ok') else '❌')} | "
                         f"{x.get('stores_with_prices', 0)}/{x.get('stores_listed', 0)} | {x.get('files', 0)} | {why[:400]} |")
        open(a.summary, "w", encoding="utf-8").write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
