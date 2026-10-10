"""
איסוף מחירים לרשת אחת: מוריד את קבצי הסניפים, המחירים והמבצעים של כל הסניפים,
ומפיק קובץ מרוכז chain-<NAME>.json.gz עם "המחיר הכי טוב" (רגיל או מבצע לכלל הלקוחות) לכל מוצר בכל סניף.

שימוש:
  python tools/collect.py CHAIN_NAME OUT_DIR            # הורדה + עיבוד (דורש il-supermarket-scraper)
  python tools/collect.py --parse-only DIR CHAIN OUT    # עיבוד בלבד של קבצים שכבר הורדו (לבדיקות)
"""
import glob, gzip, io, json, os, re, sys, time, zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

IL = timezone(timedelta(hours=3))
CHAIN_HE = {
    "SHUFERSAL": "שופרסל", "RAMI_LEVY": "רמי לוי", "YOHANANOF": "יוחננוף", "OSHER_AD": "אושר עד",
    "VICTORY_NEW_SOURCE": "ויקטורי", "VICTORY": "ויקטורי", "TIV_TAAM": "טיב טעם", "YAYNO_BITAN_AND_CARREFOUR": "קרפור",
    "HAZI_HINAM": "חצי חינם", "MAHSANI_ASHUK_NEW_SOURCE": "מחסני השוק", "MAHSANI_ASHUK": "מחסני השוק", "KESHET": "קשת טעמים",
    "SUPER_PHARM": "סופר-פארם", "GOOD_PHARM": "גוד פארם", "BAREKET": "ברקת", "CITY_MARKET_KIRYATGAT": "סיטי מרקט",
    "CITY_MARKET_SHOPS": "סיטי מרקט", "DOR_ALON": "דור אלון", "HET_COHEN_NEW_SOURCE": "ח. כהן", "HET_COHEN": "ח. כהן",
    "KING_STORE": "קינג סטור", "NETIV_HASED": "נתיב החסד", "POLIZER": "פוליצר", "SALACH_DABACH": "סאלח דבאח",
    "SHEFA_BARCART_ASHEM": "שפע ברכת השם", "SHUK_AHIR": "שוק העיר", "STOP_MARKET": "סטופ מרקט",
    "SUPER_YUDA": "סופר יודה", "SUPER_SAPIR": "סופר ספיר", "FRESH_MARKET_AND_SUPER_DOSH": "פרש מרקט",
    "YELLOW": "יילו", "ZOL_VEBEGADOL": "זול ובגדול", "WOLT": "וולט מרקט",
}
CHAIN_ID_HE = {"7290027600007": "שופרסל", "7290058140886": "רמי לוי", "7290803800003": "יוחננוף",
               "7290103152017": "אושר עד", "7290696200003": "ויקטורי", "7290873255550": "טיב טעם",
               "7290055700007": "קרפור", "7290725900003": "יינות ביתן"}
FALLBACKS = {"VICTORY_NEW_SOURCE": ["VICTORY"], "MAHSANI_ASHUK_NEW_SOURCE": ["MAHSANI_ASHUK"], "HET_COHEN_NEW_SOURCE": ["HET_COHEN"]}

PRODUCE = [
 ('עגבנייה', r'^עגבני(ה|יה|ייה)$'), ('עגבניות שרי', r'^(עגבני(ה|ות|יה) )?שרי( אשכולות| שקיל| אשכולות -שקיל)?$'), ('מלפפון', r'^מלפפון$'),
 ('פלפל אדום', r'^פלפל אדום$'), ('פלפל צהוב', r'^פלפל צהוב$'), ('פלפל ירוק', r'^פלפל ירוק( כהה)?$'), ('פלפל בהיר', r'^פלפל (ירוק )?בהיר$'),
 ('פלפל חריף', r'^פלפל חריף$'), ('פלפל שושקה', r'^פלפל ש(ו|י)שקה'), ('בצל יבש', r'^בצל יבש$'), ('בצל אדום', r'^בצל (יבש )?(אדום|סגול)$'),
 ('תפוח אדמה לבן', r'^(תפו"א|תפוא|תפוח אדמה) לבן'), ('תפוח אדמה אדום', r'^(תפו"א|תפוא|תפוח אדמה) אדום( ארוז| תפזורת| בתפזורת)?$'),
 ('תפוח אדמה לאפייה', r'^(תפו"א|תפוח אדמה).*אפי'), ('בטטה', r'^בטטה'), ('גזר', r'^גזר'), ('חציל', r'^(חציל|חצילים)$'),
 ('קישוא', r'^(קישוא|קישואים|קישוא ירוק|קישוא זוקיני|זוקיני)( מובחר| \(זוקיני\))?$'), ('כרוב לבן', r'^כרוב לבן$'), ('כרוב אדום', r'^כרוב (אדום|סגול)$'),
 ('כרובית', r'^כרובית$'), ('סלק', r'^סלק'), ('קולורבי', r'^קולורבי$'), ('שומר', r'^שומר$'), ('דלורית', r'^דלורית$'), ('לוביה', r'^לוביה'),
 ('דלעת ערמונים', r'^דלעת ערמונים'), ('בננה', r'^(בננה|בננות)( מובחרת)?$'), ('אבוקדו', r'^אבוקדו( מובחר)?$'),
 ('תפוח עץ סמיט', r'^(תפוח עץ|תפו"ע|תפוח) סמיט'), ('תפוח עץ גאלה', r'^(תפוח עץ|תפו"ע|תפוח) (גאלה|גלה)( 70)?$'), ('תפוח עץ חרמון', r'^(תפוח עץ|תפו"ע|תפוח) חרמון'),
 ('תפוח עץ זהוב', r'^(תפוח עץ|תפו"ע|תפוח) (מוזהב|זהוב)'), ('תפוח עץ פינק', r'^(תפוח עץ|תפו"ע|תפוח) פינק'), ('אגס', r'^אגס( ירוק)?$'), ('אגס אדום', r'^אגס אדום'),
 ('תפוז', r'^(תפוז|תפוזים)$'), ('קלמנטינה', r'^קלמנטינה'), ('לימון', r'^לימון$'), ('פומלית', r'^פומלית'), ('אשכולית', r'^אשכולית'),
 ('מלון', r'^מלון( מובחר)?$'), ('אבטיח', r'^אבטיח( שלם)?$'), ('מנגו', r'^מנגו$'), ('רימון', r'^רימון'), ('אפרסמון', r'^אפרסמון( 65)?$'),
 ('אפרסמון קרמבו', r'^אפרסמון קרמבו'), ('ענבים ירוקים/לבנים', r'^ענב(ים)? (לבן|ירוקים)( מובחר)?$'), ('ענבים אדומים', r'^ענב(ים)? אדומ'),
 ('ענבים שחורים', r'^ענב(ים)? שחור'), ('נקטרינה', r'^נקטרינה'), ('אפרסק', r'^אפרסק'), ('שזיף אדום', r'^שזיף (אדום|שגיב)'),
]
PRODUCE_RX = [(f"P{i:02d}", n, re.compile(rx)) for i, (n, rx) in enumerate(PRODUCE, 1)]
# מוצרי יסוד שלכל רשת יש להם קוד פנימי משלה – מתאימים לפי שם (לא רק שקילים)
STAPLES = [("E01", "ביצים L (12 יח')", re.compile(r"^(12\s*)?ביצ(י|ים|ה)\b.*(\bL\b|גדול)")),
           ("E02", "ביצים M (12 יח')", re.compile(r"^(12\s*)?ביצ(י|ים|ה)\b.*(\bM\b|בינוני)"))]
STAPLE_BAD = re.compile(r"חופש|אומגה|אורגנ|שוקולד|הפתעה|וופל|ענק|30|18|24|\b6\b|\b10\b|חום|קשות|מבושל|XL")
BAD = re.compile(r'אורגנ|מיובש|ציפס|כשר|\(ק\)|מוקפא|קלוי')


def clean_produce(s):
    s = re.sub(r'[\s*.]+$', '', s).strip()
    return re.sub(r'\s+(תוצרת \S+|ישראל|ארצות הברית)(\s+תפזורת)?$', '', s).strip()


# ---------- קריאת XML גמישה ----------
def read_any(path):
    data = open(path, "rb").read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data)); data = z.read(z.namelist()[0])
    return data


def parse_xml(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # קבצים עם הצהרת קידוד שגויה
        for enc in ("utf-8-sig", "utf-16", "cp1255"):
            try:
                txt = data.decode(enc)
                txt = re.sub(r'^\s*<\?xml[^>]*\?>', '', txt)
                root = ET.fromstring(txt); break
            except Exception:
                root = None
        if root is None: raise
    for el in root.iter():
        if isinstance(el.tag, str):
            el.tag = el.tag.split('}')[-1].lower()
    return root


def kid(el, *names):
    for n in names:
        x = el.find(n)
        if x is not None and x.text and x.text.strip():
            return x.text.strip()
    return ""


def deep(el, *names):
    for n in names:
        for x in el.iter(n):
            if x.text and x.text.strip():
                return x.text.strip()
    return ""


def num(s):
    try: return float(str(s).replace(',', ''))
    except Exception: return None


def norm_id(s):
    s = re.sub(r'\D', '', s or '')
    return str(int(s)) if s else ""


def file_ts(name):
    d = re.sub(r'\D', '', os.path.basename(name).split('.')[0])
    m = re.findall(r'(20\d{6}-?\d{4,6})', os.path.basename(name))
    return re.sub(r'\D', '', m[-1]) if m else d[-12:]


# ---------- סניפים ----------
def parse_stores(root, cities):
    chain_id = deep(root, "chainid")
    out = {}
    for st in root.iter():
        if st.tag not in ("store", "branch"): continue
        sid = norm_id(kid(st, "storeid", "storeidnumber"))
        if not sid: continue
        cid = kid(st, "chainid") or chain_id
        city = kid(st, "city", "cityname")
        if city.isdigit():  # סמל יישוב של הלמ"ס – יתורגם לשם בשלב הבנייה
            city = cities.get(str(int(city)), str(int(city)))
        addr = kid(st, "address", "storeaddress")
        if addr.lower() in ("unknown", "לא ידוע", "0"): addr = ""
        out[f"{cid}-{sid}"] = {"name": re.sub(r'\s+', ' ', kid(st, "storename")), "addr": re.sub(r'\s+', ' ', addr),
                               "city": city.strip(), "sub": kid(st, "subchainname")}
    return out


# ---------- מחירים ----------
def parse_prices(root, fname):
    cid = deep(root, "chainid")
    sid = norm_id(deep(root, "storeid"))
    if not sid:
        m = re.search(r'(\d{13})-(\d+)-(\d+)', os.path.basename(fname))
        if m: cid, sid = cid or m.group(1), norm_id(m.group(3))
    items = {}
    for it in root.iter():
        if it.tag not in ("item", "product", "line"): continue
        code = norm_id(kid(it, "itemcode"))
        p = num(kid(it, "itemprice"))
        if not code or not p or p <= 0: continue
        items[code] = {"name": re.sub(r'\s+', ' ', kid(it, "itemname", "itemnm")),
                       "price": p, "w": kid(it, "bisweighted") == "1"}
    return cid, sid, items


# ---------- מבצעים ----------
def pdate(d, h=""):
    d = (d or "").replace("T", " ")[:19]
    if h and len(d) <= 10: d = d[:10] + " " + h[:8]
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"):
        try: return datetime.strptime(d.strip(), f)
        except Exception: pass
    return None


def apply_promos(root, items, now):
    """מעדכן items[code]['best'] לפי מבצעים תקפים לכלל הלקוחות, בלי קופון ובלי מינימום קנייה."""
    n = 0
    for pr in root.iter():
        if pr.tag not in ("promotion", "sale"): continue
        s = pdate(deep(pr, "promotionstartdatetime", "promotionstartdate"), deep(pr, "promotionstarthour"))
        e = pdate(deep(pr, "promotionenddatetime", "promotionenddate"), deep(pr, "promotionendhour"))
        if not s or not e: continue
        if e.hour == 0 and e.minute == 0: e = e.replace(hour=23, minute=59)
        if not (s <= now <= e): continue
        if deep(pr, "additionaliscoupon") == "1": continue
        clubs = [x.text.strip() for x in pr.iter("clubid") if x.text and x.text.strip()]
        if clubs and not any(c.startswith("0") for c in clubs): continue
        desc = re.sub(r'\s+', ' ', deep(pr, "promotiondescription"))
        if re.search(r'תו זהב|מועדון|לחברי|קופון|כרטיס אשראי|באשראי', desc): continue
        if (num(deep(pr, "minpurchaseamount", "minpurchaseamnt")) or 0) > 0: continue
        m = re.search(r'(\d+)\s*ב\s*-?\s*(\d+(?:\.\d+)?)', desc)
        pis = [x for x in pr.iter() if x is not pr and x.find("itemcode") is not None]
        if not pis and pr.find("itemcode") is not None: pis = [pr]
        for pi in pis:
            code = norm_id(kid(pi, "itemcode"))
            it = items.get(code)
            if not it: continue
            reg = it["price"]
            dp = num(kid(pi, "discountedprice") or (deep(pr, "discountedprice") if pi is not pr else ""))
            mq = num(kid(pi, "minqty") or (deep(pr, "minqty") if pi is not pr else "")) or 1
            cands = []
            if m and not it["w"]:
                k = int(m.group(1)); P = float(m.group(2))
                if 1 < k <= 10: cands.append(P / k)
            if dp and dp > 0:
                if mq >= 1: cands.append(dp / mq)
                cands.append(dp)
            for u in cands:
                if 0.3 * reg <= u < reg - 0.009:
                    if u < it.get("best", reg): it["best"] = round(u, 2); n += 1
                    break
    return n


def is_barcode(code, it):
    return len(code) >= 8 and not it["w"] and not code.startswith("7290000000")


def build_chain(files, chain, cities, now=None):
    now = now or datetime.now(IL).replace(tzinfo=None)
    kinds = {"stores": [], "price": [], "promo": []}
    for f in files:
        b = os.path.basename(f).lower()
        if "promofull" in b or "promo_full" in b: kinds["promo"].append(f)
        elif "pricefull" in b or "price_full" in b: kinds["price"].append(f)
        elif "store" in b: kinds["stores"].append(f)
    stores, per_store, latest = {}, {}, {}
    for f in kinds["stores"]:
        try: stores.update(parse_stores(parse_xml(read_any(f)), cities))
        except Exception as ex: print("stores parse error", f, ex)
    for f in sorted(kinds["price"], key=file_ts):
        try:
            cid, sid, items = parse_prices(parse_xml(read_any(f)), f)
        except Exception as ex:
            print("price parse error", f, ex); continue
        if not sid or not items: continue
        key = f"{cid}-{sid}"
        per_store[key] = items; latest[key] = file_ts(f)
    promo_n = 0
    for f in sorted(kinds["promo"], key=file_ts):
        try:
            root = parse_xml(read_any(f))
            key = f"{deep(root, 'chainid')}-{norm_id(deep(root, 'storeid'))}"
            if key in per_store: promo_n += apply_promos(root, per_store[key], now)
        except Exception as ex:
            print("promo parse error", f, ex)

    names, prices = {}, {}
    for key, items in per_store.items():
        row = {}
        for code, it in items.items():
            if is_barcode(code, it):
                row[code] = it.get("best", it["price"])
                if len(it["name"]) > len(names.get(code, "")): names[code] = it["name"]
        for pc, pn, R in PRODUCE_RX:
            c = [it.get("best", it["price"]) for it in items.values()
                 if it["w"] and R.search(clean_produce(it["name"])) and not BAD.search(it["name"])]
            if c: row[pc] = min(c)
        for sc, sn, R in STAPLES:
            c = [it.get("best", it["price"]) for it in items.values()
                 if R.search(it["name"]) and not STAPLE_BAD.search(it["name"]) and 8 <= it["price"] <= 30]
            if c: row[sc] = min(c)
        prices[key] = row
    for pc, pn, R in PRODUCE_RX: names[pc] = f'{pn} (לק"ג)'
    for sc, sn, R in STAPLES: names[sc] = sn
    for key in prices:
        stores.setdefault(key, {"name": "", "addr": "", "city": "", "sub": ""})
    chain_he = {}
    for key in stores:
        chain_he[key] = CHAIN_ID_HE.get(key.split("-")[0], CHAIN_HE.get(chain, chain))
    out = {"chain": chain, "fetched": datetime.now(IL).isoformat(timespec="minutes"),
           "stores": {k: dict(v, chain=chain_he[k], ts=latest.get(k, "")) for k, v in stores.items() if k in prices},
           "names": names, "prices": prices,
           "stats": {"store_files": len(kinds["stores"]), "price_files": len(kinds["price"]), "promo_files": len(kinds["promo"]),
                     "stores_with_prices": len(prices), "promo_hits": promo_n, "stores_listed": len(stores)}}
    return out


def load_cities(path="tools/cities.json"):
    try: return json.load(open(path, encoding="utf-8"))
    except Exception: return {}


def scrape(chain, dump):
    from il_supermarket_scarper import ScarpingTask
    t = ScarpingTask(enabled_scrapers=[chain], files_types=["STORE_FILE", "PRICE_FULL_FILE", "PROMO_FULL_FILE"],
                     multiprocessing=1, timeout_in_seconds=110 * 60,
                     output_configuration={"output_mode": "disk", "base_storage_path": dump},
                     status_configuration={"database_type": "json", "base_path": dump + "/status"})
    t.start(); t.join()
    return [f for f in glob.glob(dump + "/**/*", recursive=True)
            if os.path.isfile(f) and "/status" not in f and not f.endswith(".json")]


def main():
    if sys.argv[1] == "--parse-only":
        d, chain, out = sys.argv[2], sys.argv[3], sys.argv[4]
        files = [f for f in glob.glob(d + "/**/*", recursive=True) if os.path.isfile(f)]
    else:
        chain, outdir = sys.argv[1], sys.argv[2]
        out = os.path.join(outdir, f"chain-{chain}.json.gz")
        t0 = time.time(); files = []
        for name in [chain] + FALLBACKS.get(chain, []):
            dump = os.path.abspath(f"dumps/{name}")
            try: files = scrape(name, dump)
            except Exception as ex: print("scrape error", name, ex)
            if any("pricefull" in os.path.basename(f).lower() or "price_full" in os.path.basename(f).lower() for f in files): break
        print(f"{chain}: {len(files)} files in {time.time() - t0:.0f}s", flush=True)
    data = build_chain(files, chain, load_cities())
    print(json.dumps(data["stats"], ensure_ascii=False), flush=True)
    if not data["prices"]:
        print("no prices – not writing output"); sys.exit(0)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote", out, os.path.getsize(out))


if __name__ == "__main__":
    main()
