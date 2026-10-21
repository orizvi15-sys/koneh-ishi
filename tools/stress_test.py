#!/usr/bin/env python3
"""חיפוש באגים מקיף בבוט "קונה אישי" – כמו לקוח רגיל.

רץ כל יום ראשון ב-12:00 (משימה מתוזמנת של Claude), ואפשר להריץ ידנית:
    DATA_CACHE=/path/koneh-data.bin python3 tools/stress_test.py [--quick]

מה נבדק:
  1. מוצרים: ~150 שמות בסיסיים (חלב, גבינות, בשרים, ירקות, מזווה, ניקיון) – נמצא? הותאם למוצר הנכון? נמכר ברוב הסניפים?
  2. מוצרים במשקל: מחיר לק"ג סביר בכל הארץ.
  3. מיקומים: עשרות יישובים + מיקומי קצה (ים, חו"ל, גבול) – זמן נסיעה, שם האזור, חסרים בסופר המומלץ.
  4. קבלות פיקטיביות מרשתות וערים שונות.
  5. שיחות מבלבלות ושיחות של כמה שלבים.

רשתות השרת הישראלי (ויקטורי, חצי חינם, מחסני השוק, נתיב החסד, ח. כהן) – המחירים שלהן יכולים להיות ישנים
כשהשרת לא רץ. לא נחשב באג אצלנו: מסומן בנפרד.

בלי מפתח Claude אמיתי: קריאת הקבלה והבנת ההודעה מזויפות (כמו בבדיקות), ובחירת המוצר = התוצאה הראשונה בחיפוש
(התרחיש הכי גרוע – בבוט האמיתי Claude בוחר מבין האפשרויות).
"""
import os, re, sys, time, random, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUICK = "--quick" in sys.argv
WIDE = "--wide" in sys.argv or not QUICK          # ברירת מחדל: גם קבלות בכל הערים
sys.argv = [sys.argv[0]]
_src = open(os.path.join(ROOT, "tests", "test_bot.py"), encoding="utf-8").read()
__file__ = os.path.join(ROOT, "tests", "test_bot.py")
exec(_src.split("# ================= הבדיקות")[0])          # אותה סביבה של הבדיקות (Claude מזויף, בלי טלגרם)
D = kdata.D
IL_CHAINS = {"ויקטורי", "חצי חינם", "מחסני השוק", "נתיב החסד", "ח. כהן"}
ISSUES = collections.OrderedDict()
def issue(kind, msg):
    ISSUES.setdefault(kind, [])
    if msg not in ISSUES[kind]: ISSUES[kind].append(msg)

def stems(t):
    return {kdata.stem(w).rstrip("י").translate(kdata.FINALS) for w in kdata.toks(t) if not re.fullmatch(r"[\d.%]+", w)}

def mkuser(home, items=None):
    return {"step": "done", "prefs": {"home": list(home), "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
            "items": [dict(x) for x in (items or [])]}

# סניפי סופרמרקט לדגימה (בלי חנויות נוחות / פארם)
SUPER = [s for s in D.stores if s["chain"] not in ("יילו", "דור אלון", "סופר יודה") and not re.search(r"\bBE\b|Be\b", s["n"])]
random.seed(int(time.strftime("%U")))                          # דגימה אחרת כל שבוע
SAMPLE = random.sample(SUPER, min(len(SUPER), 60 if QUICK else 150))
SAMPLE_P = [(s, D.store_prices(s, ())) for s in SAMPLE]

# ================= 1. מוצרים =================
PRODUCTS = {
    "חלב וביצים": ["חלב", "חלב 1%", "חלב 3% 2 ליטר", "חלב סויה", "חלב שקדים", "שוקו", "ביצים", "ביצים M", "ביצים 30", "חמאה", "שמנת מתוקה",
                  "שמנת חמוצה", "שמנת לבישול", "יוגורט", "יוגורט יווני", "לבנה", "אשל", "מעדן", "דנונה", "מילקי", "פודינג"],
    "גבינות": ["קוטג'", "קוטג' 9%", "גבינה לבנה 5%", "גבינה לבנה 9%", "גבינת שמנת", "גבינה צהובה", "גבינה צהובה פרוסה", "גבינה צהובה מגורדת",
               "עמק", "גאודה", "צ'דר", "אמנטל", "מוצרלה", "מוצרלה טרייה", "פרמזן", "ריקוטה", "גבינה בולגרית", "פטה", "גבינה צפתית",
               "גבינת עיזים", "ברי", "קממבר", "רוקפור", "גבינה כחולה", "חלומי", "קשקבל", "גבינת מסקרפונה", "גבינה מותכת", "טל העמק", "פיראוס",
               "עמק פרוס", "גוש חלב", "נעם", "גבינת שמנת 5%"],
    "בשר עוף ודגים": ["חזה עוף", "שניצל", "פרגיות", "שוקיים", "כרעיים", "כנפיים", "עוף שלם", "עוף טחון", "הודו טחון", "חזה הודו",
                      "בשר טחון", "בשר בקר טחון", "אנטריקוט", "סינטה", "צלי כתף", "בשר לגולאש", "אסאדו", "צלעות טלה", "כבד עוף", "לבבות עוף",
                      "נקניקיות", "פסטרמה", "סלמי", "קבב", "המבורגר", "סלמון", "פילה אמנון", "דניס", "לברק", "פילה בקלה", "טונה טרייה",
                      "שניצל תירס", "קציצות דגים", "שווארמה"],
    "ירקות ופירות": ["עגבניה", "עגבניות שרי", "מלפפון", "בצל", "בצל אדום", "שום", "תפוחי אדמה", "בטטה", "גזר", "פלפל אדום", "פלפל ירוק",
                     "חסה", "כרוב", "כרובית", "ברוקולי", "קישוא", "חציל", "פטריות", "פטרוזיליה", "כוסברה", "שמיר", "נענע", "לימון",
                     "בננה", "תפוח", "תפוז", "קלמנטינה", "אבוקדו", "ענבים", "אבטיח", "מלון", "אגס", "מנגו", "תותים", "רימון"],
    "לחם ומזווה": ["לחם", "לחם אחיד", "לחם מלא", "פיתות", "חלה", "לחמניות", "טורטיה", "פסטה", "ספגטי", "פתיתים", "אורז", "אורז בסמטי",
                   "קוסקוס", "קמח", "סוכר", "מלח", "שמן", "שמן זית", "חומץ", "קטשופ", "מיונז", "חרדל", "טחינה", "חומוס", "טונה",
                   "תירס", "שעועית", "עדשים", "רסק עגבניות", "זיתים", "מלפפון חמוץ", "ריבה", "דבש", "נוטלה", "חמאת בוטנים",
                   "דגני בוקר", "קורנפלקס", "שיבולת שועל", "אבקת מרק", "פירורי לחם", "שמרים", "אבקת אפייה", "קפה", "קפה טורקי",
                   "נס קפה", "תה", "קקאו", "סוכרזית", "בורגול", "גריסים", "סילאן", "ממרח תמרים", "קמח מלא", "פול"],
    "חטיפים ומשקאות": ["במבה", "ביסלי", "תפוצ'יפס", "דוריטוס", "שוקולד", "שוקולד מריר", "עוגיות", "וופל", "קרמבו", "פופקורן", "גרעינים",
                       "קולה", "קולה זירו", "ספרייט", "מים מינרליים", "סודה", "מיץ תפוזים", "מיץ ענבים", "פטל", "בירה", "יין אדום", "יין לבן"],
    "ניקיון וטיפוח": ["נייר טואלט", "מגבות נייר", "טישו", "נוזל כלים", "אבקת כביסה", "ג'ל כביסה", "מרכך כביסה", "אקונומיקה", "שקיות אשפה",
                      "נייר אלומיניום", "ניילון נצמד", "ספוג", "טבליות למדיח", "שמפו", "מרכך שיער", "סבון ידיים", "סבון גוף", "משחת שיניים",
                      "מברשת שיניים", "דאודורנט", "חיתולים", "מגבונים", "תחבושות", "כפפות", "מטליות", "נוזל רצפות"],
}
PER_KG = {"חזה עוף": (20, 75), "פרגיות": (25, 95), "שוקיים": (8, 50), "כרעיים": (8, 45), "כנפיים": (6, 45), "עוף שלם": (8, 45),
          "בשר טחון": (30, 140), "בשר בקר טחון": (30, 140), "אנטריקוט": (70, 320), "סלמון": (45, 170), "פילה אמנון": (15, 90),
          "דניס": (25, 140), "לברק": (25, 140), "עגבניה": (2, 16), "מלפפון": (2, 16), "בצל": (1.5, 12), "תפוחי אדמה": (1.5, 12),
          "גזר": (1.5, 12), "בננה": (3, 16), "לימון": (3, 20), "אבוקדו": (5, 30)}

def product_round():
    names = [n for grp in PRODUCTS.values() for n in grp]
    found, missed = app.match_items([{"name": n, "qty": 1} for n in names])
    for n in missed: issue("מוצר לא נמצא במאגר", n)
    byname = {f["src"]: f for f in found}
    for grp, ns in PRODUCTS.items():
        for n in ns:
            f = byname.get(n)
            if not f: continue
            q, m = stems(n), stems(f["name"])
            if not q <= m and not (f["code"][:1] in "PMEW"):
                issue("התאמה חשודה (לא כל המילים במוצר)", f"{grp}: '{n}' → '{f['name']}'")
            i = D.code2i.get(f["code"])
            if i is None: continue
            g = D.group(i, n); fb = D.fallback(i, n)
            it = {"name": f["name"], "qty": 1, "i": i, "g": g, "fb": fb, "wfb": None}
            if f["code"][:1].isdigit():
                wf = app.weighed_fb(f["name"]); it["wfb"] = (D.code2i[wf[0]], wf[1]) if wf and wf[0] in D.code2i else None
            have = sum(1 for s, P in SAMPLE_P if app.lc(P, it) is not None)
            pct = have / len(SAMPLE_P)
            if pct < 0.35:
                issue("נמכר במעט סניפים (כנראה הותאם למוצר נדיר)", f"{grp}: '{n}' → '{f['name']}' – ב-{pct:.0%} מהסניפים")
    return found

# ================= 2. מוצרים במשקל =================
def weighed_round(cities):
    names = list(PER_KG)
    found, missed = app.match_items([{"name": n, "qty": 1} for n in names])
    for n in missed: issue("במשקל – לא נמצא", n)
    for city, home in cities:
        r = app.compute(mkuser(home, found))
        if not isinstance(r, dict): issue("במשקל – אין תוצאה", f"{city}: {r}"); continue
        for f in found:
            v = r["ip"].get(f["code"])
            if not v or v[1]: continue
            lo, hi = PER_KG[f["src"]]
            if not lo <= v[0] <= hi:
                issue("מחיר לק\"ג חשוד", f"{city} – {r['store']}: {f['src']} ₪{v[0]:.1f}/ק\"ג (הותאם ל'{f['name']}')")

# ================= 3. מיקומים =================
CITIES = {   # יישובים שלא נבדקו בסבב הראשון (12/10)
    "מעלות": (33.0167, 35.2667), "שלומי": (33.0750, 35.1450), "עכו": (32.9270, 35.0830), "קריית ים": (32.8490, 35.0690),
    "קריית אתא": (32.8090, 35.1070), "נשר": (32.7660, 35.0440), "טירת כרמל": (32.7600, 34.9710), "יקנעם": (32.6590, 35.1050),
    "מגדל העמק": (32.6760, 35.2400), "בית שאן": (32.4970, 35.4970), "קצרין": (32.9930, 35.6900), "סחנין": (32.8640, 35.2970),
    "זכרון יעקב": (32.5710, 34.9520), "אור עקיבא": (32.5070, 34.9190), "פרדס חנה": (32.4730, 34.9700), "רעננה": (32.1840, 34.8710),
    "הוד השרון": (32.1500, 34.8880), "ראש העין": (32.0950, 34.9570), "אלעד": (32.0520, 34.9510), "גבעתיים": (32.0700, 34.8120),
    "בת ים": (32.0170, 34.7500), "לוד": (31.9510, 34.8880), "רמלה": (31.9290, 34.8660), "נס ציונה": (31.9290, 34.7980),
    "יבנה": (31.8780, 34.7390), "גדרה": (31.8140, 34.7790), "קריית מלאכי": (31.7310, 34.7460), "נתיבות": (31.4220, 34.5890),
    "אופקים": (31.3150, 34.6210), "רהט": (31.3930, 34.7540), "ירוחם": (30.9870, 34.9290), "מצפה רמון": (30.6100, 34.8010),
    "מעלה אדומים": (31.7770, 35.2980), "ביתר עילית": (31.6960, 35.1160), "אפרת": (31.6530, 35.1500), "קריית ארבע": (31.5300, 35.1200),
    "טייבה": (32.2660, 35.0090), "כפר קאסם": (32.1140, 34.9760), "יהוד": (32.0330, 34.8900), "קיסריה": (32.5000, 34.9000),
    # נוספו 18/10
    "חריש": (32.4610, 35.0440), "נוף הגליל": (32.7020, 35.3200), "טמרה": (32.8530, 35.1980), "באקה אל-גרבייה": (32.4180, 35.0420),
    "מודיעין עילית": (31.9330, 35.0420), "גבעת שמואל": (32.0780, 34.8490), "אור יהודה": (32.0290, 34.8570), "כפר יונה": (32.3170, 34.9350),
    # נוספו 21/10 – יישובים שהקרוב אליהם הוא תחנת דלק / מכולת קיבוץ (מחנה רעים → "דור אלון כפר עזה")
    "מחנה רעים": (31.3880, 34.4600), "עין שמר": (32.4620, 35.0050), "גבעת עוז": (32.5550, 35.1950), "אורים": (31.3030, 34.5210),
    "דגניה א": (32.7070, 35.5750), "להבות חביבה": (32.3800, 35.0280),
}
EDGE = {   # מיקומי קצה – לא אמור לקרוס, ולא להמליץ על סניף בשעה וחצי נסיעה בלי אזהרה
    "בים מול תל אביב": (32.0900, 34.6500), "קפריסין": (34.9000, 33.6000), "ירדן – עמאן": (31.9500, 35.9300),
    "מצרים – סיני": (29.9000, 33.8000), "קו 0,0": (0.0, 0.0), "ים המלח – עין בוקק": (31.2000, 35.3600), "הר החרמון": (33.3100, 35.7700),
    "ערבה – יטבתה": (29.8930, 35.0600), "עזה": (31.5000, 34.4600), "רמת הגולן – מרום גולן": (33.1300, 35.7700),
}
NO_STORE_TOWNS = {"קצרין", "ירוחם", "מצפה רמון", "שלומי", "מעלות", "אפרת", "קריית ארבע"}   # עיירות קטנות – נסיעה ארוכה מוצדקת

def location_round(found):
    basket = [f for f in found if f["src"] in ("חלב", "לחם אחיד", "ביצים", "קוטג'", "פסטה", "אורז", "שמן", "עגבניה", "מלפפון",
                                                 "בננה", "חזה עוף", "במבה", "נייר טואלט")]
    rows = []
    for city, home in list(CITIES.items()) + list(EDGE.items()):
        edge = city in EDGE
        try:
            r = app.compute(mkuser(home, basket))
            pn = app.place_name(home)
        except Exception as ex:
            issue("מיקום – קריסה", f"{city}: {type(ex).__name__}: {ex}"); continue
        if not isinstance(r, dict):
            issue("מיקום – אין תוצאה", f"{city}: {str(r)[:120]}"); continue
        st = next(s for s in D.stores if s["n"] == r["store"])
        near = min(D.stores, key=lambda s: app.km(home, s["p"]))
        nmin = app.car_min(app.km(home, near["p"]))
        m = re.search(r"(\d+) דק' נסיעה ממך", r["main"]); mins = int(m.group(1)) if m else None
        warn = "עדיין אין לי סניפים באזור שלך" in r["main"]
        miss = ", ".join(r["miss"])                    # 18/10: בהודעה רק מספר החסרים; השמות בכפתור "מה חסר"
        rows.append(f"{city:22} → {r['store']} ({st['a']}) · {mins} דק' · {pn}" + (" · ⚠️ אזהרה" if warn else "") + (f" · חסר: {miss}" if miss else ""))
        if edge:
            if nmin > 45 and not warn: issue("מיקום קצה – בלי אזהרה", f"{city}: נשלח {mins} דק' ל{r['store']} בלי 'אין סניפים באזור'")
            if nmin > 45 and pn.startswith("אזור "): issue("מיקום קצה – שם אזור מטעה", f"{city}: '{pn}' (הסניף הקרוב {nmin:.0f} דק')")
            continue
        if app.small_store(st) and any(not app.small_store(s_) and app.car_min(app.km(home, s_["p"])) <= 45 for s_ in D.stores):
            issue("מיקום – תחנת דלק/מכולת במקום סופר", f"{city}: {r['store']}")
        if app.small_store(near):                      # הקרוב הוא תחנת דלק – מודדים "רחוק" מול הסופר הרגיל הקרוב
            near = min((s_ for s_ in D.stores if not app.small_store(s_)), key=lambda s_: app.km(home, s_["p"]))
            nmin = app.car_min(app.km(home, near["p"]))
        if mins is None: issue("מיקום – בלי זמן נסיעה", city)
        elif mins > nmin + 15 and mins > 20: issue("מיקום – נשלח רחוק מדי", f"{city}: {mins} דק' ל{r['store']}; הקרוב {nmin:.0f} דק' ({near['n']})")
        elif mins > 25 and city not in NO_STORE_TOWNS: issue("מיקום – נסיעה ארוכה", f"{city}: {mins} דק' ל{r['store']} ({st['a']})")
        if miss and st["chain"] not in IL_CHAINS: issue("חסר בסופר המומלץ", f"{city} – {r['store']}: {miss}")
        for nm, ml in r["miss_all"]:
            if len(ml) >= app.MAX_MISSING: issue("סניף עם 4+ חסרים מוצג", f"{city}: {nm}: {', '.join(ml)}")
        if "חסר בסניף:" in r["main"] + r["others"]: issue("רשימת חסרים בהודעה (צריך רק מספר)", city)
    return rows

# ================= 4. קבלות פיקטיביות =================
RECEIPTS = [
    ("שופרסל דיל – רעננה, אחוזה", "רעננה", [("גבינה בולגרית 5%", 1), ("גבינת שמנת 30%", 2), ("מוצרלה טרייה", 1), ("פרמזן מגורר", 1),
     ("עמק פרוס 28%", 1), ("לבנה גד", 1), ("יוגורט יווני", 4), ("חלב 3% 1ל", 3), ("ביצים L", 1), ("חסה", 1)]),
    ("רמי לוי – לוד", "לוד", [("אנטריקוט טרי", 0.75), ("צלי כתף", 1.2), ("בשר טחון", 1.0), ("כנפיים עוף", 1.5), ("כרעיים", 2.1),
     ("נקניקיות עוף זוגלובק", 2), ("פסטרמה הודו", 1), ("לחמניות המבורגר", 1), ("קטשופ היינץ", 1), ("פחם", 1)]),
    ("יוחננוף – ראש העין", "ראש העין", [("פילה סלמון", 0.65), ("פילה אמנון", 0.8), ("דניס טרי", 0.5), ("לימון", 0.3), ("שום", 0.2),
     ("שמן זית כתית", 1), ("אורז בסמטי", 1), ("כוסברה", 1), ("שמיר", 1)]),
    ("אושר עד – אלעד", "אלעד", [("חלב 3% 2ל", 4), ("לחם אחיד פרוס", 3), ("חלות", 2), ("ביצים 30", 2), ("קוטג 5%", 4), ("שמנת חמוצה", 2),
     ("גבינה לבנה 5%", 3), ("עוף שלם", 3.2), ("תפוא", 4), ("בצל", 2), ("שמן קנולה 1ל", 3), ("סוכר 1 קג", 2)]),
    ("קרפור – נשר", "נשר", [("קולה זירו 1.5ל", 4), ("מים מינרליים שישייה", 2), ("ביסלי גריל", 3), ("דוריטוס", 2), ("קרמבו", 1),
     ("שוקולד מריר עלית", 2), ("גרעינים", 1), ("בירה קרלסברג", 6)]),
    ("שופרסל שלי – בית שאן", "בית שאן", [("אבטיח", 4.5), ("מלון", 1.8), ("ענבים", 1.0), ("תפוז", 2), ("מנגו", 1.1), ("אגס", 0.9),
     ("רימון", 1.2), ("קלמנטינה", 1.5)]),
    ("טיב טעם – הוד השרון", "הוד השרון", [("ברי", 1), ("קממבר", 1), ("גבינה כחולה", 1), ("חלומי", 1), ("גבינת עיזים", 1), ("יין אדום", 2),
     ("לחם מחמצת", 1), ("זיתים", 1)]),
    ("רמי לוי – מעלה אדומים", "מעלה אדומים", [("אבקת כביסה אריאל 5 קג", 1), ("מרכך כביסה בדין", 1), ("אקונומיקה", 2), ("טבליות למדיח פיניש", 1),
     ("נייר טואלט 32 גלילים", 1), ("מגבות נייר", 1), ("שקיות אשפה", 2), ("ספוג", 1), ("סבון ידיים", 2)]),
    ("סופר ספיר – אופקים", "אופקים", [("קמח לבן 1 קג", 3), ("שמרים", 2), ("אבקת אפייה", 1), ("סוכר", 2), ("חמאה", 2), ("שמנת מתוקה", 1),
     ("שוקולד חלב", 3), ("קקאו", 1)]),
    ("שופרסל דיל – קריית אתא", "קריית אתא", [("שניצל עוף טרי", 1.2), ("חזה עוף", 1.5), ("פרגיות", 1.0), ("עוף טחון", 0.5), ("הודו טחון", 0.5),
     ("כבד עוף", 0.4), ("לבבות עוף", 0.4)]),
    ("קשת טעמים – גדרה", "גדרה", [("פסטה פנה", 2), ("רוטב עגבניות", 2), ("פרמזן", 1), ("שמן זית", 1), ("שום", 0.2), ("בזיליקום", 1)]),
    ("רמי לוי – טייבה", "טייבה", [("חלב", 2), ("לבנה", 2), ("גבינה בולגרית", 1), ("זעתר", 1), ("פיתות", 2), ("עגבניה", 1.5), ("מלפפון", 1.2),
     ("שמן זית", 1), ("חומוס", 2), ("טחינה", 1)]),
    # נוספו 18/10
    ("שופרסל דיל – חריש", "חריש", [("עמק פרוס 28%", 2), ("גבינה בולגרית 5%", 1), ("קוטג' 3%", 2), ("חלב 1% 1ל", 2), ("פיתות", 1),
     ("חומוס אחלה", 1), ("מלפפון", 1.1), ("עגבניה", 1.3), ("בצל", 0.8)]),
    ("רמי לוי – אור יהודה", "אור יהודה", [("כרעיים עוף", 1.6), ("שוקיים", 1.2), ("בשר טחון 500 גרם", 2), ("פרגיות", 0.9), ("אורז פרסי", 1),
     ("רוטב סויה", 1), ("קורנפלקס תלמה", 1), ("שוקו בשקית", 4)]),
    ("קרפור – כפר יונה", "כפר יונה", [("נוזל רצפות", 1), ("כפפות", 1), ("מטליות", 1), ("נייר טואלט 24", 1), ("אקונומיקה", 1), ("נוזל כלים", 2),
     ("טבליות למדיח", 1)]),
]

def receipts_round():
    out = []
    for store_txt, city, lines in RECEIPTS:
        home = CITIES.get(city)
        to = f"tg:rc{abs(hash(store_txt)) % 10**7}"; setup_user(to, *home)
        items = [{"name": n, "qty": q} for n, q in lines]
        def fake_rcpt(prompt, image=None, mime=None, keep_dict=False, _st=store_txt, _it=items):
            if prompt.startswith(app.RECEIPT_PROMPT[:40]) and image: return {"store": _st, "items": _it, "quality": "ok", "issue": ""}
            return fake_claude(prompt, image, mime, keep_dict)
        app.claude_json = fake_rcpt
        OUT.clear()
        try: photo(to)
        finally: app.claude_json = fake_claude
        u = app.USERS[to]
        if any("משהו השתבש" in t for t in texts()): issue("קבלה – קריסה", store_txt); continue
        if u.get("step") == "forgot": press(to, "noadd")
        main = next((t for t in texts() if "מחיר הסל" in t), None)
        reg = next((s for s in D.stores if s["key"] == app.usual_store(u)), None)
        chain = store_txt.split(" – ")[0].split()[0]
        if not reg: issue("קבלה – הסניף לא זוהה", store_txt)
        elif chain not in reg["n"]: issue("קבלה – רשת שגויה", f"{store_txt} → {reg['n']}")
        elif app.km(home, reg["p"]) > 12: issue("קבלה – סניף רחוק מהבית", f"{store_txt} → {reg['n']} ({reg['a']})")
        if not main: issue("קבלה – אין תוצאה", f"{store_txt}: {[t[:80] for t in texts()[-2:]]}"); continue
        if u.get("missed"): issue("קבלה – מוצרים לא נמצאו", f"{store_txt}: {u['missed']}")
        best = next((s for s in D.stores if s["n"] == (u.get("last") or {}).get("store")), None)
        OUT.clear(); press(to, "seen")
        seen = re.sub(r"<[^>]+>", "", texts()[-1]) if texts() else ""
        for ln in seen.split("\n"):
            if "חסר בסניף" in ln and not (best and best["chain"] in IL_CHAINS):
                issue("קבלה – חסר בסניף המומלץ", f"{store_txt} → {best['n'] if best else '?'}: {ln.strip()}")
        for x in u["items"]:
            q, m = stems(x["src"]), stems(x["name"])
            if not (q & m) and x["code"][:1] not in "PMEW": issue("קבלה – התאמה חשודה", f"{store_txt}: '{x['src']}' → '{x['name']}'")
        out.append(f"{store_txt} → זוהה: {reg['n'] if reg else '—'}\n   " + re.sub(r"<[^>]+>", "", main).replace("\n", "\n   "))
    return out

# ================= 5. שיחות =================
CHAOS = ["פסתה, במבע, חלן", "milk, bread, eggs", "🥛🍞🥚", "1234", "", " ", "a" * 3000, "חלב" * 200, "/start", "היי", "איפןס", "קנייה חדשה",
         "תודה", "סל שבועי", "הגדרות", "כמה חסכתי", "עגבניה,עגבניה,עגבניה,עגבניה", "גבינה, גבינה צהובה, גבינה לבנה",
         "<b>חלב</b>", "חלב\nלחם\nביצים", "חלב; לחם; ביצים", "קילו עגבניות", "מחק אותי", "היי"]
FLOWS = [   # שיחות של כמה שלבים: (תיאור, [("send"/"press"/"loc", ערך)])
    ("איפוס באמצע רשימה → מיקום חדש → רשימה", [("send", "חלב, לחם"), ("send", "איפוס"), ("loc", (31.8780, 34.7390)), ("press", "club:none"),
                                                ("send", "במבה, ביסלי")]),
    ("מועדון לקוחות", [("send", "הגדרות"), ("press", "set:club"), ("press", "club:שופרסל"), ("press", "clubdone"), ("send", "חלב, ביצים")]),
    ("החלפת מיקום מההגדרות", [("press", "set:loc"), ("loc", (32.7940, 34.9896)), ("send", "פסטה, אורז")]),
    ("סופר קבוע מההגדרות", [("send", "חלב"), ("press", "set:store"), ("press", "st:none"), ("send", "חלב, במבה")]),
    ("כפתורים ישנים אחרי איפוס", [("send", "איפוס"), ("press", "seen"), ("press", "oth"), ("press", "addf"), ("press", "mine")]),
    ("משתמש חדש לגמרי", [("send", "היי"), ("press", "keep"), ("send", "חלב")]),
]

def chat_round():
    to = "tg:chaos"; setup_user(to, 32.1840, 34.8710)
    for x in CHAOS:
        OUT.clear()
        try: send(to, x)
        except Exception as ex: issue("שיחה – קריסה", f"{x[:30]!r}: {ex}"); continue
        T = texts()
        if any("משהו השתבש" in t for t in T): issue("שיחה – 'משהו השתבש'", repr(x[:40]))
        if not T and x.strip() and x != "מחק אותי": issue("שיחה – אין תשובה", repr(x[:40]))
        if any(len(t) > 4096 for t in T): issue("שיחה – הודעה ארוכה מדי לטלגרם", repr(x[:40]))
        if any("₪0 " in t or "₪0\n" in t for t in T): issue("שיחה – 'מחיר ₪0'", repr(x[:40]))
        u = app.USERS.get(to)
        if not u or not u["prefs"].get("home"): setup_user(to, 32.1840, 34.8710)
        else: u["step"] = "done"
    for name, steps in FLOWS:
        to = f"tg:flow{abs(hash(name)) % 10**6}"
        if name != "משתמש חדש לגמרי": setup_user(to, 32.0700, 34.8120)
        else: app.USERS.pop(to, None)
        OUT.clear()
        for kind, val in steps:
            try:
                if kind == "send": send(to, val)
                elif kind == "press": press(to, val)
                else: loc(to, *val)
            except Exception as ex: issue("שיחה – קריסה", f"{name} / {kind} {val}: {ex}")
        if any("משהו השתבש" in t for t in texts()): issue("שיחה – 'משהו השתבש'", name)
        if name.startswith("איפוס באמצע"):
            main = [t for t in texts() if "מחיר הסל" in t]
            if not main or "יבנה" not in main[-1]: issue("שיחה – מיקום אחרי איפוס", f"{name}: {main[-1][:120] if main else 'אין תוצאה'}")
        if name.startswith("החלפת מיקום"):
            main = [t for t in texts() if "מחיר הסל" in t]
            st_ = next((s for s in D.stores if s["n"] == (app.USERS[to].get("last") or {}).get("store")), None)   # 20/10: בתוצאה כבר אין "(אזור …)"
            if not main or not st_ or app.km(st_["p"], (32.7940, 34.9896)) > 12: issue("שיחה – החלפת מיקום", f"{name}: {main[-1][:120] if main else 'אין תוצאה'}")

# ================= 6. קבלות בכל הערים (--wide) =================
# שורות כמו בקבלה אמיתית (קיצורים, משקלים). הקבלה "נלקחת" מסניף אמיתי בכל עיר שיש בה סופרמרקט.
RCPT_LINES = [("חלב תנובה 3% 1ל", 1, 3), ("חלב 3% 2 ליטר", 1, 2), ("חלב 1% טרה", 1, 2), ("קוטג' 5% 250ג", 1, 3), ("גבינה לבנה 5%", 1, 2),
    ("עמק פרוס 28%", 1, 1), ("גבינה בולגרית 5%", 1, 1), ("יוגורט דנונה", 1, 4), ("שמנת חמוצה 15%", 1, 1), ("חמאה 200ג", 1, 1),
    ("ביצים L 12", 1, 2), ("ביצים M 30", 1, 1), ("לחם אחיד פרוס", 1, 2), ("לחם מלא", 1, 1), ("פיתות 10 יח", 1, 1), ("חלה", 1, 2),
    ("עגבניה", 0.6, 2.2), ("מלפפון", 0.5, 1.8), ("בצל יבש", 0.4, 1.5), ('תפו"א לבן', 1.0, 3.5), ("גזר", 0.4, 1.2), ("בננה", 0.8, 2.0),
    ("תפוח עץ", 0.6, 1.6), ("לימון", 0.2, 0.6), ("אבוקדו", 0.3, 1.0), ("פלפל אדום", 0.3, 0.9), ("חסה", 1, 1), ("פטרוזיליה", 1, 1),
    ("חזה עוף טרי", 0.8, 1.8), ("שניצל עוף", 0.6, 1.4), ("פרגיות", 0.6, 1.3), ("שוקיים", 1.0, 2.5), ("עוף שלם", 1.8, 2.6),
    ("בשר טחון", 0.5, 1.2), ("כנפיים", 0.8, 1.6), ("פילה סלמון", 0.4, 0.8), ("נקניקיות עוף", 1, 2), ("פסטרמה הודו", 1, 1),
    ("פסטה ספגטי", 1, 3), ("אורז פרסי 1 ק", 1, 1), ("קמח לבן 1ק", 1, 1), ("סוכר לבן 1ק", 1, 1), ("שמן קנולה 1ל", 1, 2), ("שמן זית", 1, 1),
    ("טונה במים 4*160", 1, 1), ("רסק עגבניות", 1, 2), ("קטשופ", 1, 1), ("מיונז", 1, 1), ("טחינה", 1, 1), ("חומוס", 1, 2),
    ("תירס שימורים", 1, 2), ("קפה נמס", 1, 1), ("תה ויסוצקי", 1, 1), ("במבה 80ג", 1, 4), ("ביסלי גריל", 1, 3), ("שוקולד פרה", 1, 3),
    ("עוגיות", 1, 1), ("קולה 1.5 ל", 1, 6), ("מים מינרליים 1.5", 1, 6), ("מיץ תפוזים", 1, 2), ("נייר טואלט 32", 1, 1),
    ("נוזל כלים", 1, 1), ("אבקת כביסה", 1, 1), ("שקיות אשפה", 1, 1), ("סבון ידיים", 1, 1), ("שמפו", 1, 1), ("משחת שיניים", 1, 2),
    ("מגבונים", 1, 2), ("חיתולים", 1, 1), ("דגני בוקר", 1, 1), ("זיתים", 1, 1), ("מלפפון חמוץ", 1, 1), ("שוקו", 1, 2)]

def wide_round(n=200):
    rnd = random.Random(int(time.strftime("%U")) + 7)
    by_city = collections.defaultdict(list)
    for s in SUPER:
        c = s["a"].split(",")[-1].strip() if "," in s["a"] else ""
        if c and not re.search(r"אקספרס|AM|PM", s["n"]): by_city[c].append(s)
    cities = sorted(by_city)
    picks = [(c, rnd.choice(by_city[c])) for c in cities]
    big = [c for c in cities if len(by_city[c]) >= 8]
    while len(picks) < n and big: c = rnd.choice(big); picks.append((c, rnd.choice(by_city[c])))
    stats = collections.Counter(); out = []
    for k, (city, st) in enumerate(picks[:n]):
        home = [st["p"][0] + rnd.uniform(-0.012, 0.012), st["p"][1] + rnd.uniform(-0.012, 0.012)]   # עד ~1.5 ק"מ מהסניף
        br = st["n"][len(st["chain"]):].strip(" -–") if st["n"].startswith(st["chain"]) else st["n"]
        txt = rnd.choice([f"{st['chain']} – {br}", f"{st['n']} {city}", f"{st['chain']} סניף {br}, {st['a']}"])
        if rnd.random() < 0.3: txt = txt.replace("קרית", "קריית")
        lines = rnd.sample(RCPT_LINES, rnd.randint(6, 14))
        items = [{"name": nm, "qty": round(rnd.uniform(lo, hi), 2) if lo != int(lo) or hi != int(hi) else rnd.randint(int(lo), int(hi))}
                 for nm, lo, hi in lines]
        to = f"tg:w{k}"; app.USERS[to] = mkuser(home); app.USERS[to]["items"] = []
        def fake_rcpt(prompt, image=None, mime=None, keep_dict=False, _st=txt, _it=items):
            if prompt.startswith(app.RECEIPT_PROMPT[:40]) and image: return {"store": _st, "items": _it, "quality": "ok", "issue": ""}
            return fake_claude(prompt, image, mime, keep_dict)
        app.claude_json = fake_rcpt; OUT.clear()
        try: photo(to)
        except Exception as ex: issue("כל הערים – קריסה", f"{city}: {ex}"); continue
        finally: app.claude_json = fake_claude
        u = app.USERS[to]; stats["קבלות"] += 1
        if any("משהו השתבש" in t for t in texts()): issue("כל הערים – 'משהו השתבש'", f"{city} / {txt}"); continue
        if u.get("step") == "forgot": press(to, "noadd")
        main = next((t for t in texts() if "מחיר הסל" in t), None)
        rk = app.usual_store(u)
        if rk == st["key"]: stats["סניף זוהה נכון"] += 1
        else:
            r_ = next((s for s in D.stores if s["key"] == rk), None)
            if r_ and r_["chain"] == st["chain"] and app.km(st["p"], r_["p"]) < 1.5: stats["סניף שכן מאותה רשת"] += 1
            else: issue("כל הערים – זיהוי סניף מהקבלה", f"{city}: '{txt}' → {r_['n'] + ' (' + r_['a'] + ')' if r_ else 'לא זוהה'}; נכון: {st['n']} ({st['a']})")
        if not main: issue("כל הערים – אין תוצאה", f"{city}: {[re.sub(r'<[^>]+>', '', t)[:90] for t in texts()[-2:]]}"); continue
        clean = re.sub(r"<[^>]+>", "", main)
        m = re.search(r"(\d+) דק' נסיעה", clean); mins = int(m.group(1)) if m else None
        best = next((s for s in D.stores if s["n"] == (u.get("last") or {}).get("store")), None)
        if mins and mins > 25:
            near = min(SUPER, key=lambda s: app.km(home, s["p"]))
            if app.car_min(app.km(home, near["p"])) < 12: issue("כל הערים – נשלח רחוק", f"{city}: {mins} דק' ל{best['n'] if best else '?'}")
        if re.search(r"מחיר הסל: ₪0\b", clean): issue("כל הערים – מחיר ₪0", f"{city}: {clean[:120]}")
        if u.get("missed"): issue("כל הערים – שורה מהקבלה לא נמצאה", f"{city}: {u['missed']}")
        if best and best["chain"] not in IL_CHAINS:
            for x in (u.get("last") or {}).get("miss") or []: stats["חסר: " + x[:40]] += 1
        stats["המלצה: נשארים" if "משתלם" in clean else "המלצה: לעבור"] += 1
    return stats

# ================= 7. קבלות "אמיתיות" (20/10, אחרי הקבלה של אבא של אורי) =================
# מוצרים אמיתיים מסניף אמיתי, שם קטוע ל-20 תווים כמו בקבלה, ברקוד ב-70% מהשורות, מחיר ששולם. בודק שהבוט הוגן:
# הסניף של הקבלה תמיד בסיס ההשוואה, לא מוציאים חלק גדול מהסל, ואין "חיסכון" מופרך.
NOT_GROCERY = re.compile(r"כבל|USB|סוללות|מנורת|כרית|כסא|קומקום|מגבות מיקרו|עט |פצירה|טורמוס|מתקן לייבוש|שבועון|אביזרי|וינסטון|סיגר|מזון לכלב|מזון חתולים|ניקוטין")

def real_receipts_round(n=80):
    rnd = random.Random(int(time.strftime("%U")) + 31)
    sup = [s for s in SUPER if s["chain"] not in IL_CHAINS and not re.search(r"אקספרס|AM|PM", s["n"])]
    stats = collections.Counter()
    for st in rnd.sample(sup, min(n, len(sup))):
        P = D.store_prices(st, (), cache=False)
        pool = [i for i in P if D.items[i][2] >= 15 and not D.items[i][0].startswith("W:") and not NOT_GROCERY.search(D.items[i][1])]
        if len(pool) < 200: continue
        common = [i for i in pool if D.items[i][2] >= 400]; mid = [i for i in pool if D.items[i][2] < 400]
        k = rnd.randint(20, 35); sel = rnd.sample(common, min(len(common), int(k * .6))) + rnd.sample(mid, min(len(mid), k - int(k * .6)))
        lines = []
        for i in sel:
            code, nm = D.items[i][0], D.items[i][1]
            q = round(rnd.uniform(0.3, 2.0), 3) if code[:1] in "PM" else rnd.choice([1, 1, 1, 2, 3])
            row = {"name": nm[:20].strip(), "qty": q, "price": round(P[i] * q, 2)}
            if code[:1].isdigit() and rnd.random() < 0.7: row["code"] = code
            lines.append(row)
        paid = sum(r["price"] for r in lines)
        home = [st["p"][0] + rnd.uniform(-0.01, 0.01), st["p"][1] + rnd.uniform(-0.01, 0.01)]
        to = f"tg:rr{st['key']}"; app.USERS[to] = mkuser(home); app.USERS[to]["items"] = []
        br = st["n"][len(st["chain"]):].strip() if st["n"].startswith(st["chain"]) else st["n"]
        def fk(prompt, image=None, mime=None, keep_dict=False, _l=lines, _s=f"{st['chain']} – {br}"):
            if prompt.startswith(app.RECEIPT_PROMPT[:40]) and image: return {"store": _s, "items": _l, "quality": "ok", "issue": ""}
            return fake_claude(prompt, image, mime, keep_dict)
        app.claude_json = fk; OUT.clear()
        try: photo(to)
        except Exception as ex: issue("קבלה אמיתית – קריסה", f"{st['n']}: {ex}"); continue
        finally: app.claude_json = fake_claude
        u = app.USERS[to]
        if u.get("step") == "forgot": press(to, "noadd")
        main = next((re.sub(r"<[^>]+>", "", t) for t in texts() if "💰" in t), None)
        if not main: issue("קבלה אמיתית – אין תוצאה", st["n"]); continue
        stats["קבלות"] += 1; last = u.get("last") or {}
        if u.get("rstore") != st["key"]: issue("קבלה אמיתית – הסניף לא זוהה", f"{st['n']} → {u.get('rstore')}")
        stay = "שקנית בו" in main; stats["נשארים" if stay else "לעבור"] += 1
        m = re.search(r"חוסך ₪(\d+) לעומת מה ששילמת", main)
        if not stay and not m: issue("קבלה אמיתית – המלצה בלי השוואה למה ששולם", f"{st['n']}: {main[:150]}")
        nr = len(last.get("rare") or []); stats["שורות"] += len(lines); stats["הוצאו מההשוואה"] += nr
        mp = re.search(r"שילמת: ₪(\d+)", main) or re.search(r"ששילמת ב[^(\n]*\(₪(\d+)\)", main)                   # מוצרים מיוחדים נשארים בסכום (20/10) – הסל לא "מתכווץ"
        if mp and abs(int(mp.group(1)) - paid) <= max(3, 0.02 * paid): stats["סכום מלא"] += 1
        elif mp: issue("קבלה אמיתית – 'שילמת' לא תואם לקבלה", f"{st['n']}: ₪{mp.group(1)} מול ₪{paid:.0f}")
        if nr > len(lines) // 4: issue("קבלה אמיתית – רבע מהסל הוצא מההשוואה", f"{st['n']}: {nr}/{len(lines)}")
        if m and int(m.group(1)) > 0.3 * paid: issue("קבלה אמיתית – חיסכון מעל 30% (לבדוק התאמות)", f"{st['n']}: ₪{m.group(1)} מתוך ₪{paid:.0f}")
    return stats

# ================= הרצה =================
def main():
    t0 = time.time()
    print(f"נתונים: {len(D.stores)} סניפים, {len(D.items)} מוצרים, עודכן {D.built}")
    stale = sorted({s["chain"] for s in D.stores if s["ts"] and app._ts_age_h(s["ts"]) > 30 and s["chain"] in IL_CHAINS})
    other_stale = sorted({s["chain"] for s in D.stores if s["chain"] not in IL_CHAINS} -
                         {s["chain"] for s in D.stores if s["ts"] and app._ts_age_h(s["ts"]) <= 48})
    found = product_round(); print("✓ מוצרים")
    weighed_round([(c, CITIES[c]) for c in ("רעננה", "לוד", "בית שאן", "אופקים", "מעלה אדומים", "עכו")]); print("✓ במשקל")
    rows = location_round(found); print("✓ מיקומים")
    rc = receipts_round(); print("✓ קבלות")
    chat_round(); print("✓ שיחות")
    wide = wide_round(200 if "--wide" in sys.argv[1:] or WIDE else 0) if (WIDE or not QUICK) else collections.Counter()
    if wide: print("✓ קבלות בכל הערים")
    real = real_receipts_round(40 if QUICK else 100); print("✓ קבלות אמיתיות")
    print("\n=== מיקומים ===\n" + "\n".join(rows))
    print("\n=== קבלות ===\n" + "\n".join(rc))
    if wide:
        print(f"\n=== קבלות בכל הערים: {wide['קבלות']} ===")
        for k, v in wide.most_common():
            if not k.startswith("חסר: ") or v >= 3: print(f"  {k}: {v}")
    print(f"\n=== קבלות אמיתיות (ברקוד + מחיר ששולם): {real['קבלות']} – נשארים {real['נשארים']}, לעבור {real['לעבור']}, "
          f"הוצאו מההשוואה {real['הוצאו מההשוואה']}/{real['שורות']} שורות, סכום 'שילמת' מלא ב-{real['סכום מלא']} ===")
    n = sum(len(v) for v in ISSUES.values())
    print(f"\n========== {n} ממצאים ({time.time() - t0:.0f} שנ') ==========")
    if stale: print(f"ℹ️ רשתות השרת הישראלי עם מחירים ישנים (לא באג בבוט): {', '.join(stale)}")
    if other_stale: print(f"⚠️ רשתות אחרות שלא התעדכנו 48 שעות: {', '.join(other_stale)}")
    cov = app.basic_coverage()
    if cov:
        print(f"\n## שומר מוצרים בסיסיים – חסר ברוב הסניפים של רשת ({len(cov)}) – לבדוק אם שם חדש (תיקון) או באמת לא נמכר (לא באג)")
        for ch, nm, k, n_, sim in cov: print(f"  - {ch}: {nm} {k}/{n_}" + (f" | דומים: {', '.join(sim)}" if sim else ""))
    for k, v in ISSUES.items():
        print(f"\n## {k} ({len(v)})")
        for m in v[:30]: print("  - " + m)
    return n

if __name__ == "__main__":
    main()
