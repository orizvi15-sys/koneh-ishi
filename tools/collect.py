"""
איסוף מחירים לרשת אחת: מוריד את קבצי הסניפים, המחירים והמבצעים של כל הסניפים,
ומפיק קובץ מרוכז chain-<NAME>.json.gz עם "המחיר הכי טוב" (רגיל או מבצע לכלל הלקוחות) לכל מוצר בכל סניף.

שימוש:
  python tools/collect.py CHAIN_NAME OUT_DIR            # הורדה + עיבוד (דורש il-supermarket-scraper)
  python tools/collect.py --parse-only DIR CHAIN OUT    # עיבוד בלבד של קבצים שכבר הורדו (לבדיקות)
"""
import glob, gzip, io, json, os, re, sys, time, zipfile
from collections import Counter
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

IL = timezone(timedelta(hours=3))
try:
    from zoneinfo import ZoneInfo
    IL_TZ = ZoneInfo("Asia/Jerusalem")
except Exception:
    IL_TZ = IL
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
# בשר, עוף ודגים טריים במשקל (מחיר לק"ג) – לכל רשת קוד פנימי משלה, מאחדים לפי שם. (שם, ביטוי, מחיר מינ'/מקס' לק"ג)
NO_MEAT = r"(?!.*(מעושן|ממולא|מתובל|מרינד|שיפוד|נקניק|קפוא|מוקפא|פריך|מצופה|ציפוי|טבעול|טבעוני|צמחי|כבד|מבושל|צלוי|לחתול|לכלב|ברוטב))"
MEAT = [
 ("חזה עוף", r"^(פילה |נתחי |נתח )?חזה (עוף|של עוף)(?!.*טחון)" + NO_MEAT, 18, 85),
 ("שניצל עוף טרי", r"^(שניצל|שניצלים)( עוף| חזה עוף| מחזה עוף)?( טרי| דק| פרוס)*$", 20, 85),
 ("פרגיות", r"^(פרגית|פרגיות|סטייק פרגית)" + NO_MEAT, 25, 95),
 ("כרעיים עוף", r"^(כרעיים|כרעי|כרע) (עוף|שלמים|שלם)" + NO_MEAT, 8, 45),
 ("שוקיים עוף", r"^(שוקיים|שוקי|שוק) (עוף|תחתון)" + NO_MEAT, 8, 50),
 ("כנפיים עוף", r"^(כנפיים|כנפי|כנף) עוף" + NO_MEAT, 6, 45),
 ("עוף שלם", r"^עוף (שלם|טרי שלם)" + NO_MEAT, 8, 45),
 ("בשר בקר טחון", r"^(בשר (בקר )?טחון|טחון (בקר|בשר)|בקר טחון)(?!.*(עוף|הודו|כבש))" + NO_MEAT, 30, 140),
 ("בשר בקר לבישול", r"^(בשר בקר(?! טחון)|כתף בקר|צלי כתף|בשר לגולאש|גולאש|בשר בקר לבישול)" + NO_MEAT, 40, 170),
 ("אנטריקוט", r"^(סטייק )?אנטריקוט" + NO_MEAT, 70, 320),
 ("פילה סלמון", r"^(פילה |נתח )?סלמון" + NO_MEAT, 45, 170),
 ("אמנון (מושט)", r"^(דג |פילה )?(אמנון|מושט)" + NO_MEAT, 15, 90),
 ("דניס", r"^(דג )?דניס" + NO_MEAT, 25, 140),
 ("לברק", r"^(דג )?לברק" + NO_MEAT, 25, 140),
 ("בורי", r"^(דג )?(בורי|קיפון)" + NO_MEAT, 15, 100),
 ("קרפיון", r"^(דג )?קרפיון" + NO_MEAT, 10, 70),
 ("פילה הייק/בקלה", r"^(פילה )?(הייק|בקלה|מרלוזה)" + NO_MEAT, 20, 120),
 # גבינות מהדלפק (במשקל)
 ("גבינה צהובה (במשקל)", r"^(גבינה |גבינת )?(צהובה|עמק|גאודה|גלבוע|אמנטל|טל העמק|גוש חלב|נעם)(?!.*(מגורר|פרוס דק|טבעונ))", 25, 160),
 ("גבינה בולגרית (במשקל)", r"^(גבינה |גבינת )?בולגרית", 25, 140),
 ("גבינה מלוחה / פטה (במשקל)", r"^(גבינה |גבינת )?(מלוחה|פטה|ברינזה)", 25, 160),
 ("גבינה צפתית (במשקל)", r"^(גבינה |גבינת )?צפתית", 25, 140),
]
MEAT_RX = [(f"M{i:02d}", n, re.compile(rx), lo, hi) for i, (n, rx, lo, hi) in enumerate(MEAT, 1)]
MEAT_WORDS = re.compile(r"עוף|בקר|טחון|הודו|כבש|סלמון|אמנון|מושט|דג |דניס|לברק|בורי|קרפיון|הייק|בקלה|פרגי|שניצל|אנטריקוט|כרעי|שוק|כנפ|גבינ|צהובה|בולגרית|פטה|מלוחה|צפתית")

# כל שאר המוצרים שנמכרים במשקל – מפתח מנורמל לפי השם, כדי לאחד בין רשתות ("שקדים טבעיים במשקל" = "שקדים טבעיים")
W_DROP = re.compile(r'(?<![א-ת])(במשקל|משקל|לק"?ג|ק"?ג|קג|קילו|ל-?\s?ק"?ג|טרי(ים|ה|ות)?|מובחר(ים|ת|ות)?|ארוז(ים|ה)?|תפזורת|בתפזורת|'
                    r'מהדרין|בד"?ץ|כשר(ה)?|גלאט|איכות|סוג|א\'|פרימיום|שקיל|יח\'|יחידה|מבצע|חדש|ישראל|תוצרת|מארץ|ישראלי(ת|ים)?)(?![א-ת])')
def _wstem(w):   # "עגבניות"/"עגבניה" → אותו מפתח
    if len(w) > 3 and not w.isdigit(): w = re.sub(r"(יות|ות|ים|ייה|יה|ה|י)$", "", w)
    return w.translate(str.maketrans("ךםןףץ", "כמנפצ"))
def wname(n):   # שם לתצוגה – בלי "ארוז", "(ק)", "קג", "תפזורת"...
    n = re.sub(r'\(ק\)|(?<![א-ת])(במשקל|לק"?ג|ק"?ג|קג|תפזורת|בתפזורת|ארוז(ה|ים)?|מובחר(ת|ים)?|שקיל|מחיר ל)(?![א-ת])|[-–]\s*$', " ", n)
    return " ".join(n.split()).strip(" -–")
def wkey(name):
    n = clean_produce(name).replace("״", '"').replace("׳", "'")
    if re.search(r"100\s*(ג|גר|גרם)(?![א-ת])", n): return None        # מחיר ל-100 גרם – לא משווים עם מחיר לק"ג
    n = W_DROP.sub(" ", n)
    n = re.sub(r"[^\w%\s]", " ", n)
    toks = [_wstem(t) for t in n.split() if not t.isdigit() and len(t) > 1]
    if not toks or not re.search(r"[א-ת]{2}", toks[0]): return None
    return " ".join(toks[:4])

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
    """מעדכן items[code]['best'] (מבצע לכלל הלקוחות) ו-items[code]['club'] (מבצע לחברי מועדון),
    בלי קופונים, בלי הטבות כרטיס אשראי ובלי מינימום קנייה."""
    n = 0
    for pr in root.iter():
        if pr.tag not in ("promotion", "sale"): continue
        s = pdate(deep(pr, "promotionstartdatetime", "promotionstartdate"), deep(pr, "promotionstarthour"))
        e = pdate(deep(pr, "promotionenddatetime", "promotionenddate"), deep(pr, "promotionendhour"))
        if not s or not e: continue
        if e.hour == 0 and e.minute == 0: e = e.replace(hour=23, minute=59)
        if not (s <= now <= e): continue
        end_min = int(e.replace(tzinfo=IL_TZ).timestamp() // 60)   # הבוט יבדוק בעצמו שהמבצע עדיין בתוקף
        if deep(pr, "additionaliscoupon") == "1": continue
        clubs = [x.text.strip() for x in pr.iter("clubid") if x.text and x.text.strip()]
        desc = re.sub(r'\s+', ' ', deep(pr, "promotiondescription"))
        if re.search(r'קופון|כרטיס אשראי|באשראי|ויזה|מסטרקארד|אמריקן|דיינרס', desc): continue
        is_club = bool(clubs and not any(c.startswith("0") for c in clubs)) or bool(re.search(r'תו זהב|מועדון|לחברי', desc))
        key = "club" if is_club else "best"
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
                    if u < it.get(key, reg): it[key] = round(u, 2); it[key + "_end"] = end_min; n += 1
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

    names, prices, promo, club = {}, {}, {}, {}
    meat_seen = set(); wdisp = {}
    for key, items in per_store.items():
        row, prow, crow = {}, {}, {}
        for code, it in items.items():
            if is_barcode(code, it):
                row[code] = it["price"]                                   # מחיר רגיל
                if it.get("best") is not None: prow[code] = [it["best"], it["best_end"]]   # מבצע + תוקף
                c = it.get("club")
                if c is not None and c < it.get("best", it["price"]) - 0.009: crow[code] = [c, it["club_end"]]
                if len(it["name"]) > len(names.get(code, "")): names[code] = it["name"]
        groups = [(pc, [it for it in items.values() if it["w"] and R.search(clean_produce(it["name"])) and not BAD.search(it["name"])])
                  for pc, pn, R in PRODUCE_RX]
        groups += [(sc, [it for it in items.values() if R.search(it["name"]) and not STAPLE_BAD.search(it["name"]) and 8 <= it["price"] <= 30])
                   for sc, sn, R in STAPLES]
        groups += [(mc, [it for it in items.values() if it["w"] and R.search(clean_produce(it["name"])) and lo <= it["price"] <= hi])
                   for mc, mn, R, lo, hi in MEAT_RX]
        for it in items.values():                               # דוגמאות לבדיקה: שמות בשר/עוף במשקל
            if it["w"] and MEAT_WORDS.search(it["name"]) and len(meat_seen) < 250: meat_seen.add(f'{clean_produce(it["name"])} | {it["price"]}')
        used = {id(it) for gc, its in groups for it in its}
        wg = {}
        for it in items.values():                               # שאר המוצרים במשקל – קבוצה לכל שם מנורמל
            if not it["w"] or id(it) in used or not (2 <= it["price"] <= 600): continue
            k = wkey(it["name"])
            if k: wg.setdefault("W:" + k, []).append(it); wdisp.setdefault("W:" + k, Counter())[clean_produce(it["name"])] += 1
        groups += list(wg.items())
        for gc, its in groups:
            if not its: continue
            row[gc] = min(it["price"] for it in its)
            pr_ = [(it["best"], it["best_end"]) for it in its if it.get("best") is not None and it["best"] < row[gc] - 0.009]
            if pr_: prow[gc] = list(min(pr_))
        prices[key] = row
        if prow: promo[key] = prow
        if crow: club[key] = crow
    for pc, pn, R in PRODUCE_RX: names[pc] = f'{pn} (לק"ג)'
    for sc, sn, R in STAPLES: names[sc] = sn
    for mc, mn, R, lo, hi in MEAT_RX: names[mc] = mn.replace(" (במשקל)", "") + ' (לק"ג)'
    for wc, cnt in wdisp.items(): names[wc] = wname(cnt.most_common(1)[0][0]) + ' (לק"ג)'
    for key in prices:
        stores.setdefault(key, {"name": "", "addr": "", "city": "", "sub": ""})
    chain_he = {}
    for key in stores:
        chain_he[key] = CHAIN_ID_HE.get(key.split("-")[0], CHAIN_HE.get(chain, chain))
    out = {"chain": chain, "fetched": datetime.now(IL).isoformat(timespec="minutes"),
           "stores": {k: dict(v, chain=chain_he[k], ts=latest.get(k, "")) for k, v in stores.items() if k in prices},
           "fmt": 2, "names": names, "prices": prices, "promo": promo, "club": club,
           "stats": {"store_files": len(kinds["stores"]), "price_files": len(kinds["price"]), "promo_files": len(kinds["promo"]),
                     "stores_with_prices": len(prices), "promo_hits": promo_n, "stores_listed": len(stores), "stores_with_club": len(club),
                     "meat_stores": sum(1 for r in prices.values() if any(k.startswith("M") for k in r))},
           "meat_samples": sorted(meat_seen)}
    return out


def load_cities(path="tools/cities.json"):
    try: return json.load(open(path, encoding="utf-8"))
    except Exception: return {}


def _scrape_once(chain, dump, when=None):
    from il_supermarket_scarper import ScarpingTask
    t = ScarpingTask(enabled_scrapers=[chain], files_types=["STORE_FILE", "PRICE_FULL_FILE", "PROMO_FULL_FILE"],
                     multiprocessing=1, timeout_in_seconds=55 * 60,
                     output_configuration={"output_mode": "disk", "base_storage_path": dump},
                     status_configuration={"database_type": "json", "base_path": dump + "/status"})
    t.start(when_date=when) if when else t.start(); t.join()


def _files(dump):
    return [f for f in glob.glob(dump + "/**/*", recursive=True)
            if os.path.isfile(f) and "/status" not in f and not f.endswith(".json")]


def _is_price(f):
    b = os.path.basename(f).lower()
    return "pricefull" in b or "price_full" in b


def scrape(chain, dump):
    _scrape_once(chain, dump)
    files = _files(dump)
    # בשעות הלילה חלק מהרשתות עוד לא פרסמו את קבצי היום – משלימים מאתמול
    n_price = len({re.sub(r"-\d{8,}.*", "", os.path.basename(f)) for f in files if _is_price(f)})
    n_stores = 0
    for f in files:
        if "store" in os.path.basename(f).lower() and not _is_price(f):
            try: n_stores = max(n_stores, len(parse_stores(parse_xml(read_any(f)), {})))
            except Exception: pass
    if n_price < max(5, 0.6 * n_stores):
        y = datetime.now(IL).replace(tzinfo=None, hour=12, minute=0) - timedelta(days=1)
        print(f"{chain}: only {n_price} price files (stores listed: {n_stores}) – adding yesterday's files", flush=True)
        try: _scrape_once(chain, dump + "_y", when=y)
        except Exception as ex: print("yesterday scrape error", ex)
        files += _files(dump + "_y")
    return files


class _Errors:
    """אוסף הודעות אזהרה/שגיאה מהספרייה כדי לדעת למה רשת נכשלה"""
    def __init__(self):
        import logging
        self.msgs = []
        h = logging.Handler(level=logging.WARNING)
        h.emit = lambda r: self.msgs.append(f"{r.levelname}: {r.getMessage()}"[:300])
        logging.getLogger("Logger").addHandler(h); logging.getLogger().addHandler(h)


def write_status(outdir, chain, **kw):
    os.makedirs(outdir, exist_ok=True)
    json.dump(dict(chain=chain, at=datetime.now(IL).isoformat(timespec="minutes"), **kw),
              open(os.path.join(outdir, f"status-{chain}.json"), "w", encoding="utf-8"), ensure_ascii=False)


def main():
    if sys.argv[1] == "--status-only":  # הסקריפט קרס – לפחות לרשום שנכשל
        write_status(sys.argv[3], sys.argv[2], ok=False, reason="הסקריפט קרס – ראו לוג ההרצה", files=0)
        return
    if sys.argv[1] == "--parse-only":
        d, chain, out = sys.argv[2], sys.argv[3], sys.argv[4]
        files = [f for f in glob.glob(d + "/**/*", recursive=True) if os.path.isfile(f)]
        outdir, errs, t0 = os.path.dirname(out) or ".", None, time.time()
    else:
        chain, outdir = sys.argv[1], sys.argv[2]
        out = os.path.join(outdir, f"chain-{chain}.json.gz")
        t0 = time.time(); files = []; errs = _Errors()
        for name in [chain] + FALLBACKS.get(chain, []):
            dump = os.path.abspath(f"dumps/{name}")
            try: files = scrape(name, dump)
            except Exception as ex:
                print("scrape error", name, ex); errs.msgs.append(f"EXCEPTION ({name}): {ex!r}"[:300])
            if any(_is_price(f) for f in files): break
        print(f"{chain}: {len(files)} files in {time.time() - t0:.0f}s", flush=True)
    data = build_chain(files, chain, load_cities())
    st = data["stats"]
    print(json.dumps(st, ensure_ascii=False), flush=True)
    reason = ""
    if not files: reason = "לא הורדו קבצים בכלל (אתר חסום מחוץ לישראל / לא זמין / שינה מבנה)"
    elif not st["price_files"]: reason = "הורדו קבצים אבל אין ביניהם קבצי מחירים מלאים"
    elif not data["prices"]: reason = "קבצי מחירים הורדו אבל לא נקראו (מבנה קובץ לא מוכר)"
    elif st["stores_listed"] and st["stores_with_prices"] < 0.5 * st["stores_listed"]:
        reason = f"רק {st['stores_with_prices']} מתוך {st['stores_listed']} סניפים קיבלו מחירים"
    write_status(outdir, chain, ok=bool(data["prices"]), reason=reason, files=len(files), seconds=round(time.time() - t0),
                 errors=(errs.msgs[-6:] if errs else []), **st)
    if not data["prices"]:
        print("no prices – not writing output"); sys.exit(0)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    print("wrote", out, os.path.getsize(out))


if __name__ == "__main__":
    main()
