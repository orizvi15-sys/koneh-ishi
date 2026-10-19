#!/usr/bin/env python3
"""שיחות מבחן אוטומטיות לבוט "קונה אישי".

רץ לפני כל עדכון (GitHub Actions). אם בדיקה נכשלת – העדכון לא עולה לבוט.
כל באג שנמצא בשטח → מוסיפים כאן בדיקה, כדי שלא יחזור.

הרצה מקומית:  DATA_CACHE=/path/koneh-data.bin python3 tests/test_bot.py
(בלי מפתחות אמיתיים: Claude מוחלף ב"Claude מזויף" שעונה תשובות קבועות, וכלום לא נשלח לטלגרם)
"""
import html, json, os, re, sys, tempfile, time, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.update(DRY_RUN="1", TELEGRAM_TOKEN="test", DATA_URL="")
os.environ.setdefault("DATA_CACHE", os.path.join(ROOT, "data", "koneh-data.bin"))
for k in ("ANTHROPIC_API_KEY", "USERS_REPO", "USERS_TOKEN", "GROQ_API_KEY", "OPENAI_API_KEY", "ADMIN_CHAT"):
    os.environ.pop(k, None)
sys.path.insert(0, ROOT)

import kdata
kdata.load(False)
if not kdata.D or kdata.D.source != "remote":
    sys.exit("❌ אין קובץ נתונים ארצי (DATA_CACHE) – אי אפשר לבדוק")
import app

# ---------- סביבת בדיקה ----------
app.USERS_PATH = os.path.join(tempfile.mkdtemp(), "users.json")
app.push_users = lambda *a, **k: None
OUT = []                                    # כל מה שהבוט "שלח"
def fake_tg(method, payload):
    if method in ("sendMessage", "editMessageText"): OUT.append(payload)
    return {}
app.tg_call = fake_tg
app.graph_post = lambda *a, **k: {}

ADMIN, USER = "tg:1000", "tg:2000"

# ---------- Claude מזויף (בלי מפתח, תשובות צפויות) ----------
RECEIPT_ITEMS = [{"name": "חלב 3% 1 ליטר", "qty": 2}, {"name": "לחם אחיד פרוס", "qty": 1}, {"name": "במבה", "qty": 3},
                 {"name": "קוטג' 5%", "qty": 2}, {"name": "עגבנייה", "qty": 1.2}]
def parse_line(line):
    """פענוח פשוט של שורה ברשימה: 'מוצר – 2 חבילות' / 'נייר טואלט – 32 גלילים' (=חבילה אחת)"""
    name, _, q = line.partition(" – "); q = q.strip()
    m = re.match(r"(\d+(?:\.\d+)?)", q)
    qty = float(m.group(1)) if m else 1
    if q.startswith("חצי"): qty = 0.5
    elif re.search(r"גרם", q) and m: qty = float(m.group(1)) / 1000
    elif re.search(r"גלילים|יחידות|שישייה|רביעייה|שקיות\)", q): qty = 1
    return {"name": name.strip(), "qty": qty}

def fake_claude(prompt, image=None, mime=None, keep_dict=False):
    if prompt.startswith("Match"): return None                      # בוחרים את התוצאה הראשונה בחיפוש
    if prompt.startswith(app.RECEIPT_PROMPT[:40]):
        if image: return {"store": "רמי לוי – סניף אופקים", "items": RECEIPT_ITEMS, "quality": "ok", "issue": ""}
        text = prompt[len(app.RECEIPT_PROMPT):].strip()
        return {"store": "", "items": [parse_line(l) for l in text.split("\n") if l.strip()], "quality": "ok", "issue": ""}
    if "parse messages" in prompt:                                  # UNDERSTAND
        msg = re.search(r"<<(.*)>>", prompt, re.S).group(1)
        if msg.startswith("כמה עולה"): return {"intent": "price", "items": [{"name": msg[8:].strip(" ?"), "qty": 1}], "reply": ""}
        if msg.startswith("תוסיף"): return {"intent": "add", "items": [{"name": msg[5:].strip(), "qty": 1}], "reply": ""}
        if msg.startswith("תוריד"): return {"intent": "remove", "items": [{"name": msg[5:].strip(), "qty": 1}], "reply": ""}
        if msg.startswith("קילו "): return {"intent": "list", "items": [{"name": msg[5:], "qty": 1}], "reply": ""}
        if msg in ("תודה", "תודה רבה"): return {"intent": "chat", "items": [], "reply": "בכיף! 🙂"}
        return {"intent": "list", "items": [{"name": x.strip(), "qty": 1} for x in msg.split(",")], "reply": ""}
    return None
app.claude_json = fake_claude
app.CLAUDE_KEY = "test"
app.download_media = lambda *a, **k: (b"\xff\xd8fakejpeg", "image/jpeg")
for name in ("tg_download", "get_media", "fetch_media"):
    if hasattr(app, name): setattr(app, name, lambda *a, **k: (b"\xff\xd8fakejpeg", "image/jpeg"))
app.enhance = lambda img: (None, None)

# ---------- כלים לשיחה ----------
_n = [0]
def _msg(to, **kw):
    _n[0] += 1
    return {"from": to, "id": f"t{_n[0]}", "name": "בדיקה", **kw}
def send(to, text):  app.safe_handle(_msg(to, type="text", text={"body": text}))
def press(to, bid):  app.safe_handle(_msg(to, type="interactive", interactive={"button_reply": {"id": bid}}))
def loc(to, lat, lng): app.safe_handle(_msg(to, type="location", location={"latitude": lat, "longitude": lng}))
def photo(to):       app.safe_handle(_msg(to, type="image", image={"id": "tg:fake"}))
def voice(to):       app.safe_handle(_msg(to, type="voice", voice={"id": "tg:v", "sec": 5}))

def texts(): return [o.get("text", "") for o in OUT]
def last(): return OUT[-1].get("text", "") if OUT else ""
def buttons():
    b = []
    for o in OUT:
        for row in ((o.get("reply_markup") or {}).get("inline_keyboard") or []):
            b += [x.get("callback_data") for x in row]
    return b

RESULTS = []
def test(name):
    def deco(fn):
        OUT.clear(); app._crash_sent.clear()
        try:
            fn()
            bad = [t for t in texts() if "משהו השתבש" in t or "Traceback" in t]
            assert not bad, f"הבוט ענה 'אופס, משהו השתבש': {bad[0][:120]}"
            RESULTS.append((True, name, ""))
        except Exception as ex:
            RESULTS.append((False, name, f"{type(ex).__name__}: {ex}\n" + "".join(traceback.format_tb(ex.__traceback__)[-2:])))
        return fn
    return deco

def setup_user(to, lat=31.3155, lng=34.6210, clubs=None):
    app.USERS[to] = {"step": "done", "prefs": {"home": [lat, lng], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": clubs or []}, "hist": []}

app.USERS.clear(); app.USERS["_meta"] = {"admins": [ADMIN]}

# ================= הבדיקות =================
@test("משתמש חדש: היי → מיקום → מועדון → מוכן לקנות")
def _():
    to = "tg:3001"
    send(to, "היי"); assert "מאיפה יוצאים" in last(), last()
    loc(to, 32.0809, 34.7806); assert "מועדון" in last(), last()
    press(to, "clubyes"); press(to, "club:רמי לוי"); press(to, "clubdone")
    assert "מה קונים" in last(), last()
    assert app.USERS[to]["prefs"].get("clubs") == ["רמי לוי"]

@test("משתמש חוזר: היי → ישר 'מה קונים' (בלי שאלת מיקום, אורי 20/10) → סל שבועי")
def _():
    to = "tg:3002"; setup_user(to)
    OUT.clear(); send(to, "היי"); assert "קונים" in last() and "loc_new" in buttons() and "loc_ok" not in buttons(), (last(), buttons())
    assert "אזור" in last() or "המיקום" in last(), last()                       # האזור בשורה קטנה – לראות שהמיקום נכון
    press(to, "loc_ok"); assert "קונים" in last()                                 # כפתור ישן – עדיין עובד
    press(to, "weekly"); press(to, "hh34")
    assert any("מחיר הסל" in t for t in texts()), texts()[-2:]

@test("רשימה בשורה אחת → המלצה על סופר")
def _():
    to = "tg:3003"; setup_user(to)
    send(to, "חלב, לחם, במבה, קוטג'")
    assert any("מחיר הסל" in t for t in texts()), texts()

@test("רשימה ארוכה: '32 גלילים' / '12 יחידות' = חבילה אחת (באג 11/10)")
def _():
    to = "tg:3004"; setup_user(to)
    send(to, "חלב 3% – 2 קרטונים\nלחם אחיד – 1\nביצים L – 12 יחידות\nנייר טואלט – 32 גלילים\nבצל – 1 ק\"ג")
    q = {x["src"].split()[0]: x["qty"] for x in app.USERS[to]["items"]}
    assert q.get("נייר") == 1, q; assert q.get("ביצים") == 1, q; assert q.get("חלב") == 2, q

@test("אותה רשימה פעמיים = אותו מחיר (באג 11/10)")
def _():
    to = "tg:3005"; setup_user(to); lst = "חלב 3% – 2 קרטונים\nלחם אחיד – 1\nנייר טואלט – 32 גלילים\nבמבה – 2 שקיות"
    send(to, lst); a = app.USERS[to]["last"]["details"]
    send(to, lst); b = app.USERS[to]["last"]["details"]
    strip = lambda t: re.sub(r"_מחירים ומבצעים.*", "", t)
    assert strip(a) == strip(b), (a, b)

@test("מוצר בסיסי לא יוצא 'חסר' ברמי לוי אופקים (באג 11/10)")
def _():
    to = "tg:3006"; s0 = next(s for s in kdata.D.stores if s["n"] == "רמי לוי אופקים")
    setup_user(to, *s0["p"]); app.USERS[to]["stores"] = {s0["key"]: 5}
    send(to, "בצל – 1 ק\"ג\nתפוחי אדמה – 2 ק\"ג\nביצים M – 12 יחידות\nסוכר לבן – 1 ק\"ג\nאורז פרסי – 1 ק\"ג")
    det = app.USERS[to]["last"]["details"]
    for w in ("בצל", "תפוח אדמה", "ביצים"):
        line = next((l for l in det.split("\n") if w in l), "")
        assert line and "חסר בסניף" not in line, (w, det)

@test("שמות מקבלה עם קיצורים (ק', גר', ל') נמצאים במאגר (באג 12/10: 8 מוצרים 'לא נמצאו')")
def _():
    names = ["עגבניות 1 ק'", "גבינה צהובה 250 גר'", "פילה סלמון קפוא 500 גר'", "רוטב עגבניות 700 גר'", "נוזל כביסה 1.5 ל'", "מלפפונים 1 ק'"]
    found, missed = app.match_items([{"name": n, "qty": 1} for n in names])
    assert not missed, missed
    got = {f["src"]: f["name"] for f in found}
    assert got["עגבניות 1 ק'"].startswith("עגבנייה"), got
    assert "צהובה" in got["גבינה צהובה 250 גר'"], got
    assert got["מלפפונים 1 ק'"].startswith("מלפפון ("), got

@test("סריקת לילה 12/10: מוצרים כלליים מותאמים לסוג הנכון")
def _():
    want = {"פתיתים": ("פתיתים", "גבינה"), "חלב סויה": ("משקה סויה", "חלבה"),   # בחנויות: "משקה סויה" (12/10) "שקיות זבל": ("שקיות אשפה", None),
            "בצל סגול": ("בצל אדום", None), "סלמון": ("פילה סלמון", "מעושן"), "ריבה": ("ריבה", "ריב "),
            "פירה": ("פירה", "פירות"), "נייר כסף": ("נייר", "דימונד"), "גבינה בולגרית": ("גבינה בולגרית", None)}
    found, missed = app.match_items([{"name": q, "qty": 1} for q in want])
    assert not missed, missed
    for f in found:
        good, bad = want[f["src"]]
        assert good in f["name"], (f["src"], f["name"])
        if bad: assert bad not in f["name"], (f["src"], f["name"])

@test("'תפוחים' כללי לא יוצא חסר ברמי לוי (כל זן תפוח עץ הוא חלופה) – באג 12/10")
def _():
    s0 = next(s for s in kdata.D.stores if s["n"] == "רמי לוי נתיבות")
    found, _m = app.match_items([{"name": "תפוחים", "qty": 2}])
    it = dict(found[0]); i = kdata.D.code2i[it["code"]]
    it.update(i=i, g=kdata.D.group(i, "תפוחים"), fb=kdata.D.fallback(i, "תפוחים"))
    assert app.lc(kdata.D.store_prices(s0, set()), it), "תפוחים חסר ברמי לוי נתיבות"

@test("זיהוי בסיסי: 'בצל' → בצל יבש, 'ביצים M' → ביצים M")
def _():
    assert app.basic_alias("בצל") == "P10"; assert app.basic_alias("ביצים M 12 יחידות") == "E02"
    assert app.basic_alias("עגבניות שרי") is None

@test("שגיאת כתיב בחיפוש: 'חלן' → חלב, 'במבע' → במבה")
def _():
    assert "חלב" in kdata.D.items[kdata.D.search("חלן 3%")[0]][1]
    assert "במבה" in kdata.D.items[kdata.D.search("במבע")[0]][1]

@test("טקסט חופשי: 'קילו חזה עוף', 'כמה עולה במבה?', תוסיף/תוריד, תודה")
def _():
    to = "tg:3007"; setup_user(to)
    send(to, "קילו חזה עוף"); assert any("מחיר הסל" in t for t in texts())
    send(to, "כמה עולה במבה?"); assert "₪" in last(), last()
    send(to, "תוסיף חלב"); assert any(x["name"].startswith("חלב") or "חלב" in x["name"] for x in app.USERS[to]["items"])
    send(to, "תוריד חלב"); assert not any("חלב" in x["name"] for x in app.USERS[to]["items"])
    send(to, "תודה"); assert "בכיף" in last()

@test("מוצר כללי ('בשר') → שואל איזה סוג")
def _():
    to = "tg:3008"; setup_user(to)
    send(to, "בשר, חלב"); assert "איזה בשר" in last(), last()
    press(to, "v:0"); assert any("מחיר הסל" in t for t in texts())

@test("קבלה מצולמת → המלצה, 'מה זיהיתי', 'סופרים אחרים', תיקון")
def _():
    to = "tg:3009"; setup_user(to)
    photo(to); assert any("מחיר הסל" in t for t in texts()), texts()
    press(to, "seen"); assert "₪" in last()
    press(to, "oth"); assert "סופרים" in last()
    press(to, "fix"); assert "לתקן" in last()

@test("קבלה ממשתמש שיש לו רק מיקום (בלי mode) – לא קורס (באג 12/10 KeyError 'mode')")
def _():
    to = "tg:3020"; app.USERS[to] = {"step": "basket", "prefs": {"home": [31.9115, 34.7888]}, "hist": []}
    photo(to); assert any("מחיר הסל" in t for t in texts()), texts()
    to = "tg:3021"; app.USERS[to] = {"step": "basket", "prefs": {"home": [31.9115, 34.7888]}, "hist": []}
    send(to, "חלב, לחם"); assert any("מחיר הסל" in t for t in texts()), texts()

@test("הודעה קולית בלי מפתח → הסבר ידידותי")
def _():
    to = "tg:3010"; setup_user(to); voice(to); assert "קוליות" in last() or "הקלטה" in last(), last()

@test("תפריט הגדרות: כל כפתור עונה")
def _():
    to = "tg:3011"; setup_user(to); send(to, "חלב, לחם")
    send(to, "הגדרות"); assert "מה תרצה לעשות" in last()
    for b in ("set:loc", "set:club", "clubdone", "set:store", "st:none", "set:remind", "set:remind", "set:sav", "set:help", "set:faq"):
        n = len(OUT); press(to, b); assert len(OUT) > n, f"אין תשובה ל-{b}"
    for j in range(len(app.faq_answer(None))):
        n = len(OUT); press(to, f"set:faq{j}"); assert len(OUT) > n and "₪" not in "", f"אין תשובה לשאלה {j}"
    press(to, "set:reset"); assert "בטוח" in last(); press(to, "reset_no"); assert "בוטל" in last()

@test("שאלה 'האם כל הרשתות מופיעות' מונה רשתות וסניפים")
def _():
    q, a = app.faq_answer(1); assert "רשתות" in a and "סניפים" in a, a

@test("פקודות: כמה חסכתי, איפוס מלא, פקודה עם שגיאת כתיב, מחק אותי")
def _():
    to = "tg:3012"; setup_user(to); send(to, "חלב, לחם")
    send(to, "כמה חסכתי"); assert "חיסכון" in last() or "חסכ" in last(), last()
    send(to, "איפןס מלא"); assert "מאיפה יוצאים" in last(), last()
    assert app.USERS[to]["hist"] == []
    send(to, "מחק אותי"); assert to not in app.USERS

@test("מנהל: /stats, בדיקה עצמית, דוח שבועי (באג 11/10: בדיקה עצמית קרסה)")
def _():
    setup_user(ADMIN)
    send(ADMIN, "/stats"); assert "לוח בקרה" in last(), last()
    send(ADMIN, "בדיקה עצמית"); assert "🧪" in last(), last()
    send(ADMIN, "דוח שבועי"); assert "דוח שבועי" in last() and "מצב הרשתות" in last(), last()

@test("משתמש רגיל לא מקבל פקודות מנהל")
def _():
    to = "tg:3013"; setup_user(to)
    send(to, "דוח שבועי"); assert "מצב הרשתות" not in last()

@test("תקלה אצל משתמש → המנהל מקבל התראה עם הפרטים (פעם אחת)")
def _():
    to = "tg:3014"; setup_user(to)
    orig = app.handle
    def boom(m): raise ValueError("בדיקה")
    app.handle = boom
    try:
        app.safe_handle(_msg(to, type="text", text={"body": "חלב"}))
        app.safe_handle(_msg(to, type="text", text={"body": "חלב"}))
    finally:
        app.handle = orig
    alerts = [o for o in OUT if o["chat_id"] == ADMIN[3:] and "תקלה אצל משתמש" in o["text"]]
    assert len(alerts) == 1, len(alerts)
    assert "חלב" in alerts[0]["text"] and "ValueError" in alerts[0]["text"]
    OUT.clear()                                          # ה"אופס" כאן מכוון – לא נכשלים עליו

@test("בדיקה יומית: רשימה תקינה עוברת, ×32 נתפס")
def _():
    probs, summ = app.selftest()
    assert not [p for p in probs if "נקרא" in p or "שונה" in p], probs
    orig = app.claude_json; cnt = [0]
    def bad(prompt, image=None, mime=None, keep_dict=False):
        r = orig(prompt, image, mime, keep_dict)
        if isinstance(r, dict) and "items" in r and prompt.startswith(app.RECEIPT_PROMPT[:40]):
            cnt[0] += 1
            if cnt[0] == 2: r["items"] = [dict(x, qty=32) if x["name"].startswith("נייר") else x for x in r["items"]]
        return r
    app.claude_json = bad
    try: probs, _s = app.selftest()
    finally: app.claude_json = orig
    assert any("×32" in p for p in probs), probs

@test("כל ההודעות קצרות מספיק לטלגרם (עד 4096 תווים)")
def _():
    to = "tg:3015"; setup_user(to)
    send(to, "\n".join(f"{x} – 1" for x in ["חלב", "לחם", "ביצים", "קוטג'", "גבינה צהובה", "יוגורט", "חמאה", "עגבנייה", "מלפפון", "בצל",
                                            "תפוח אדמה", "גזר", "בננה", "אורז", "פסטה", "שמן", "סוכר", "קמח", "במבה", "ביסלי", "קולה",
                                            "מים", "נייר טואלט", "סבון כלים", "חזה עוף", "טונה", "חומוס", "טחינה", "קפה", "תה"]))
    press(to, "seen"); press(to, "oth")
    long = [len(t) for t in texts() if len(t) > 4096]
    assert not long, long

@test("הפצצה בקלט מוזר: טקסטים וכפתורים לא צפויים, במצבי משתמש שונים ופגומים – בלי קריסה")
def _():
    import copy
    texts_ = ["", " ", "😀😀😀", "hello", "milk, bread", "123", "0", "-5 חלב", "חלב x0", "חלב ×1000", "א" * 5000, "חלב\n" * 300,
              "<b>חלב</b>", "*_~`", "/start", "/unknown", "\u200f\u200e", "קילו", "חצי", "300 גרם", "תוסיף", "תוריד", "כמה עולה",
              "בשר, עוף, דג, גבינה", "ביצים 0 יחידות", "חלב 3% – ", " – 2"]
    btns = ["", "v:9", "v:x", "fix:99", "alt:0:9", "del:5", "set:faq99", "set:faqx", "club:לא קיים", "st:nokey", "hh99",
            "loc_ok", "reset_yes", "seen", "oth", "spl", "det", "addf", "noadd", "mine", "weekly", "cam", "list", "x" * 300]
    states = [{"step": "new", "prefs": {}, "hist": []}, {"step": "done", "prefs": {"home": [32.08, 34.78]}, "hist": []},
              {"step": "loc", "prefs": {}, "hist": []},
              {"step": "zzz", "prefs": {"home": [32.08, 34.78], "mode": "bike", "maxMin": "x"}, "hist": [{"d": 0, "items": [{"code": "nope", "name": "?", "qty": 1}]}]}]
    to = "tg:3999"
    for st in states:
        for x in texts_:
            app.USERS[to] = copy.deepcopy(st); OUT.clear(); send(to, x)
            assert not any("משהו השתבש" in o.get("text", "") for o in OUT), ("טקסט", x[:30], st["step"])
            assert not any(len(o.get("text", "")) > 4096 for o in OUT), ("ארוך מדי", x[:30])
        for b in btns:
            app.USERS[to] = copy.deepcopy(st); OUT.clear(); press(to, b)
            assert not any("משהו השתבש" in o.get("text", "") for o in OUT), ("כפתור", b[:30], st["step"])
    OUT.clear()

@test("שומר סף לנתונים: עדכון עם חצי מהסניפים / מחירים שקפצו – נחסם; עדכון רגיל – עובר")
def _():
    import copy
    D = kdata.D
    assert kdata.data_gate(D, D) is None
    half = copy.copy(D); half.version = "test-half"; half.stores = D.stores[: len(D.stores) // 2]
    assert kdata.data_gate(D, half), "חצי סניפים לא נחסם"
    jump = copy.copy(D); jump.version = "test-jump"
    orig_sp = D.store_prices
    def inflated(store, clubs=()):
        P = orig_sp(store, clubs); Q = type(P)({k: v * 2 for k, v in P.items()})
        Q.deals = getattr(P, "deals", {}); return Q
    jump.store_prices = inflated
    assert "קפצו" in (kdata.data_gate(D, jump) or ""), kdata.data_gate(D, jump)
    kdata.GATE.update(reason="בדיקה", version="test-half", t=0); _ld = kdata.load
    try:
        assert any("נחסם" in x for x in app.watchdog_issues())
        setup_user(ADMIN); kdata.load = lambda *a, **k: kdata.D
        send(ADMIN, "אשר נתונים"); assert "אישרתי" in last(), last()
        assert "test-half" in kdata.GATE_ACCEPT and not kdata.GATE
    finally:
        kdata.GATE.clear(); kdata.GATE_ACCEPT.discard("test-half"); kdata.load = _ld

@test("דיוק מול קבלות: מחיר בקבלה נבדק מול המחיר שלנו באותו סניף ונכנס לדוח השבועי")
def _():
    D = kdata.D; s0 = next(s for s in D.stores if s["n"] == "רמי לוי אופקים"); P = D.store_prices(s0, set())
    found, _m = app.match_items([{"name": "במבה", "qty": 1}, {"name": "קוטג' 5%", "qty": 1}])
    i1, i2 = D.code2i[found[0]["code"]], D.code2i[found[1]["code"]]
    assert i1 in P and i2 in P
    rows = [{"name": "במבה", "qty": 1, "price": P[i1]}, {"name": "קוטג' 5%", "qty": 1, "price": P[i2] + 5}]
    app.USERS.setdefault("_meta", {}).pop("wk", None)
    app.receipt_check(s0["key"], rows, found)
    acc = app.wk()["acc"]["רמי לוי"]
    assert acc == [2, 1], acc
    assert app.wk()["acc_bad"][0][1].startswith(found[1]["name"][:10])
    assert "דיוק מול קבלות" in app.weekly_report()
    app.USERS["_meta"].pop("wk", None)

@test("בלי סופר קבוע: חיסכון קטן מ-₪35 → ממליץ על הסופר הקרוב ('הסופר הקרוב אליך משתלם') – בקשה 12/10")
def _():
    to = "tg:3030"; setup_user(to, 31.911968, 34.788889)       # נס ציונה
    send(to, "במבה, חלב")
    main = next(t for t in texts() if "מחיר הסל" in t)
    assert "הסופר הקרוב אליך משתלם" in main or "חוסך" in main, main
    if "חוסך" in main:
        n = int(re.search(r"חוסך <b>₪(\d+)", main).group(1)); assert n >= 35, main

BBQ_ITEMS = [{"name": "דיאמונד רדיד אלומיניום נון סטיק 30*5 מטר", "code": "10900678148", "qty": 1.0, "src": "דיאמונד דדיד אלומי"}, {"name": "בירה וולפאס אנגלמן \"אוסטרליה פייל אייל\" 568 מ\"ל", "code": "4770301232023", "qty": 2.0, "src": "אוסטרליה פייל אייל"}, {"name": "בירה וולפאס אנגלמן \"אינדיה פייל אייל\" 568 מ\"ל", "code": "4770301230821", "qty": 2.0, "src": "בירה אינדיה פייל אייל"}, {"name": "פאולנר מינכן הל פחית 500 מ\"ל 24 יח'", "code": "4066600211815", "qty": 4.0, "src": "פאולנר מינכן הל פחי"}, {"name": "יין פסגות רוזה 750 מ\"ל", "code": "87752019191", "qty": 1.0, "src": "יין פסגות רוזה 750"}, {"name": "CAPE HERBS לימון עשבי תיבול 100ג\"ר(פח)", "code": "6006507007454", "qty": 1.0, "src": "CAPE HERBS לימון"}, {"name": "ביירן - נקניקיות ילדים 500 גרם", "code": "7290119951581", "qty": 2.0, "src": "נקניקיות קנקרס ביירן"}, {"name": "פחם דרום אמריקאי 2 ק`ג", "code": "835811005322", "qty": 1.0, "src": "פחם דרום אמריקאי 2"}, {"name": "מלפפון 7-9 במלח 560 ג' פריניר", "code": "7290013284136", "qty": 1.0, "src": "טלפפון במקח 7-9 ב"}, {"name": "רוטב ברביקיו מעושן מקורמיק 500 גר", "code": "8859287400018", "qty": 1.0, "src": "רוטב ברביקיו מעושן"}, {"name": "רוטב טבסקו צ'יפוטלי", "code": "11210007703", "qty": 1.0, "src": "רוטב טבסקו ציפוטלי"}, {"name": "שיפודי פרגיות קפוא (לק\"ג)", "code": "W:שיפוד פרג קפוא", "qty": 0.8, "src": "שיפודי פרגיות קפואים"}, {"name": "שיפודי פרגיות קפוא (לק\"ג)", "code": "W:שיפוד פרג קפוא", "qty": 0.818, "src": "שיפודי פרגיות קפואים"}]

@test("סל עם הרבה מוצרים נדירים: אף סניף עם 4+ חסרים לא מוצג (גם לא כמנצח) – באג 12/10, פעם שנייה")
def _():
    for lat, lng in ((31.8948, 34.8113), (31.911968, 34.788889), (32.0809, 34.7806)):   # רחובות, נס ציונה, תל אביב
        u = {"step": "done", "prefs": {"home": [lat, lng], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
             "items": [dict(x) for x in BBQ_ITEMS]}
        r = app.compute(u)
        assert isinstance(r, dict), r
        for nm, ml in r["miss_all"] + [("המומלץ", r["miss"])]:
            assert len(ml) < app.MAX_MISSING, (nm, ml)
        assert r["main"].count("שיפודי פרגיות") <= 1, r["main"]          # שורה כפולה מהקבלה = מוצר אחד

@test("קשת טעמים בילו ממוקם בגבעת ברנר, לא ליד נס ציונה (זמן נסיעה שגוי) – באג 12/10, פעם שנייה")
def _():
    s = next(s for s in kdata.D.stores if s["key"] == "7290785400000-17")
    d = app.km([31.929, 34.798], s["p"])                                  # מרכז נס ציונה
    assert d > 5, f"קשת טעמים בילו רק {d:.1f} ק\"מ ממרכז נס ציונה – מיקום שגוי"
    assert app.car_min(d) >= 14, app.car_min(d)

@test("סופר קבוע ברחובות + מיקום בפתח תקווה: לא ממליץ על רחובות, משווה לסופרים בפתח תקווה – באג 12/10")
def _():
    D = kdata.D
    reh = next(s for s in D.stores if s["key"] == "7290873255550-18")      # טיב טעם רחובות (מקבלה ישנה)
    pt = [32.0880, 34.8860]                                                 # מרכז פתח תקווה
    items = [dict(x) for x in BBQ_ITEMS[:1]] + [{"name": n, "qty": 1} for n in ("במבה", "חלב 3% 1 ליטר", "לחם אחיד פרוס")]
    u = {"step": "done", "prefs": {"home": pt, "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
         "stores": {reh["key"]: 3}, "items": app.match_items(items)[0]}
    assert app.usual_store(u) is None, "סופר ברחובות נחשב 'קבוע' למי שגר בפתח תקווה"
    r = app.compute(u)
    assert isinstance(r, dict), r
    assert "רחובות" not in r["main"] and "הסופר הקבוע" not in r["main"], r["main"]
    m = re.search(r"(\d+) דק'", r["main"]); assert m and int(m.group(1)) <= 20, r["main"]
    assert app.match_store("טיב טעם רחובות", pt) != reh["key"]               # קבלה מרחובות לא נרשמת כקבוע של פתח תקווה
    u["prefs"]["home"] = [31.8948, 34.8113]                                # כשהמיקום ברחובות – כן נחשב קבוע
    assert app.usual_store(u) == reh["key"]

@test("איפוס מוחק גם את ההרגלים (קניות קודמות, סופר קבוע, קבלות) – בקשה 12/10")
def _():
    for how in ("text", "button"):
        to = "tg:3131"; setup_user(to, 32.0880, 34.8860)
        u = app.USERS[to]
        u["stores"] = {"7290873255550-87": 3}; u["receipts"] = [1]; u["last_receipt"] = 1
        u["hist"] = [{"d": 1, "items": [{"name": "במבה", "qty": 1}], "store": "x"}]
        if how == "text": send(to, "איפוס")
        else: press(to, "reset")
        u = app.USERS[to]
        assert not u.get("hist") and "stores" not in u and "receipts" not in u, (how, u)
        assert app.usual_store(u) is None and app.learned(u) == []

ASHKELON, PT = (31.6688, 34.5743), (32.0880, 34.8860)

@test("איפוס → מיקום באשקלון → רשימה: סופר באשקלון, בלי 'בדרך כלל אתה קונה גם' – באג 12/10")
def _():
    to = "tg:3232"; setup_user(to, *PT)
    u = app.USERS[to]; u["stores"] = {"7290873255550-87": 5}
    u["hist"] = [{"d": 1, "saved": 0, "items": [{"code": "", "name": "פחם", "src": "פחם", "qty": 1}]}] * 3
    send(to, "איפןס"); loc(to, *ASHKELON); press(to, "club:none"); OUT.clear()
    send(to, "במבה, חלב, לחם")
    T = texts()
    assert not any("בדרך כלל" in t for t in T), T
    main = next(t for t in T if "מחיר הסל" in t)
    assert "אשקלון" in main and "פתח תקווה" not in main, main
    m = re.search(r"(\d+) דק'", main); assert m and int(m.group(1)) <= 20, main
    assert not app.USERS[to].get("stores") and len(app.USERS[to]["hist"]) == 1

@test("שרת ישן בזמן עדכון: זיכרון ישן מ-GitHub לא מחזיר מיקום ישן / קניות שנמחקו – באג 12/10")
def _():
    import copy
    to = "tg:3333"; setup_user(to, *PT)
    app.USERS[to]["hist"] = [{"d": 1, "items": []}] * 3; app.touch(to)
    old = copy.deepcopy({to: app.USERS[to]}); time.sleep(0.01)          # מה שהשרת הישן שמר
    send(to, "איפוס"); loc(to, *ASHKELON)                                  # בשרת החדש
    app.merge_users(old)
    u = app.USERS[to]
    assert app.km(u["prefs"]["home"], ASHKELON) < 1 and not u["hist"], u
    newer = copy.deepcopy({to: u}); newer[to]["prefs"]["home"] = list(PT); time.sleep(0.01); newer[to]["_t"] = time.time(); time.sleep(0.01)
    app.merge_users(newer); assert app.km(app.USERS[to]["prefs"]["home"], PT) < 1     # שינוי חדש יותר משרת אחר – נקלט
    send(to, "מחק אותי"); assert to not in app.USERS
    app.merge_users(newer); assert to not in app.USERS, "משתמש שנמחק חזר מהזיכרון הישן"
    send(to, "היי"); assert to in app.USERS                                # אפשר להתחיל מחדש

@test("מוצר בסיסי בשם כללי ('פסטה') נמצא תמיד, גם כש-Claude לא בוחר – באג 12/10 (אשדוד)")
def _():
    orig = app.claude_json
    def null_match(prompt, *a, **k):
        if prompt.startswith("Match"):
            return [{"i": i, "pick": None} for i in range(prompt.count('"item"'))]
        return orig(prompt, *a, **k)
    app.claude_json = null_match
    try:
        names = ["פסטה", "אורז", "שמן", "קמח", "סוכר", "קפה", "טונה", "במבה", "חמאה", "קוטג"]
        found, missed = app.match_items([{"name": n, "qty": 1} for n in names])
    finally: app.claude_json = orig
    assert not missed, missed
    got = {f["src"]: f["name"] for f in found}
    assert got["פסטה"].startswith("פסטה") and got["סוכר"].startswith("סוכר ") and "סוכריות" not in got["סוכר"], got
    found, missed = app.match_items([{"name": "פסטה", "qty": 1}, {"name": "חזה עוף", "qty": 1}])
    assert not missed, missed

@test("בשר טחון ארוז (נתח 500 גרם) לא 'חסר בסניף' כשיש בשר טחון במשקל – באג 12/10 (יוחננוף בן צבי)")
def _():
    D = kdata.D
    assert app.weighed_fb("בשר בקר טחון קפוא500 נתח") == ("M08", 0.5)
    assert app.weighed_fb("טיבון ויל המבורגר בשר בקר טחון מתובל קפוא 400 גרם") is None     # המבורגר ≠ בשר טחון
    s = next(s for s in D.stores if s["key"] == "7290803800003-32")
    u = {"step": "done", "prefs": {"home": s["p"], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
         "items": [{"name": "בשר בקר טחון קפוא500 נתח", "code": "7296073747574", "qty": 1, "src": "בשר בקר טחון נתח"},
                   {"name": "במבה חטיף בוטנים אסם 80 גרם", "code": D.items[D.search("במבה")[1]][0], "qty": 1}]}
    r = app.compute(u)
    blk = next(b for b in r["others"].split("\n\n") if "בן צבי" in b)
    assert "בשר בקר טחון" not in blk, blk

@test("מחיר הסל = כל הסל, כולל החסרים לפי המחיר באזור ('₪1046 (כולל 3 שחסרים שם)'), בלי ~ – 12/10, אורי 20/10")
def _():
    r = {"miss": 3, "sum": 1046.4, "have": 966.2}
    assert app.basket_price(r).startswith("₪1046") and "3 שחסרים" in app.basket_price(r), app.basket_price(r)
    assert app.basket_price(r, short=True) == "₪1046 (חסרים 3)"
    assert app.basket_price({"miss": 0, "sum": 5, "have": 5}) == "₪5"
    for lat, lng in ((31.8948, 34.8113), (32.0809, 34.7806)):
        u = {"step": "done", "prefs": {"home": [lat, lng], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
             "items": [dict(x) for x in BBQ_ITEMS]}
        r = app.compute(u)
        if r["miss"]: assert "שחסר" in r["main"], r["main"][:300]
        for nm, ml in r["miss_all"]:
            blk = next(b for b in r["others"].split("\n\n") if nm in b); assert f"(חסרים {len(ml)})" in blk, blk[:300]
        assert "~" not in r["main"] + r["others"] + r["details"], "סימן ~ ליד מחיר"

@test("'מה זיהיתי' מסודר לפי מחלקות בסופר (ירקות, חלב, ניקיון...) – סעיף 5, בקשה 12/10")
def _():
    D = kdata.D
    want = {"עגבנייה (לק\"ג)": "produce", "חזה עוף (לק\"ג)": "meat", "ביצים L (12 יח')": "dairy",
            "קוטג' תנובה 5% שומן 250 ג' בד\"צ": "dairy", "פסטה ספגטי מספר 8 אסם 500 גרם": "pantry", "במבה חטיף בוטנים אסם 200 גרם": "snacks",
            "נוזל לניקוי כלים שקוף קלאסי 750 מ\"ל פלמוליב": "clean", "מלפפונים במלח 9-7 קבוצת יבנה 320 גרם": "pantry",
            "עוגיות קרמוגית במילוי קרם שוקולד אסם 200 גרם": "snacks", "יוגורט ביו מצופה שוקולד 170 מ\"ל": "dairy",
            "גלידה בן ג'ריס 500 גרם": "frozen", "קפה נמס עלית 200 גרם": "hot", "פיתות 10 יחידות אנג'ל": "bread",
            "מזון חתול יבש עוף 2 קילו": "clean", "ג'אמפ טעם תפוחים 1.5 ליטר": "drinks", "סלמי תה מעושן (לק\"ג)": "meat"}
    for n, d in want.items():
        i = D.name2i.get(n); code = D.items[i][0] if i is not None else ("P01" if "עגבנייה" in n else "W:x" if "לק" in n else "729")
        assert app.dept_of(code, n) == d, (n, app.dept_of(code, n), d)
    to = "tg:3434"; setup_user(to, 32.0809, 34.7806)
    send(to, "עגבניה, נוזל כלים, חלב, במבה, פסטה, חזה עוף, ביצים"); OUT.clear(); press(to, "seen")
    t = texts()[-1]
    assert "לפי מחלקות" in t, t
    heads = [h for h in ("ירקות ופירות", "מוצרי חלב", "בשר, עוף", "מזווה", "חטיפים", "ניקיון") if h in t]
    assert len(heads) >= 5 and [t.index(h) for h in heads] == sorted(t.index(h) for h in heads), t     # סדר הליכה בסופר
    to2 = "tg:3435"; setup_user(to2, 32.0809, 34.7806); send(to2, "במבה, חלב"); OUT.clear(); press(to2, "seen")
    assert "לפי מחלקות" not in texts()[-1]                                  # סל קטן – רשימה רגילה

@test("עגבנייה לא 'חסר בסניף' כשיש 'עגבניות מגי' / 'עגבניה 05 פרימיום' (רבים, פרימיום) – באג 12/10 (חצי חינם רחובות, 23% מהסניפים)")
def _():
    D = kdata.D; app_rx = re.compile(r'^(עגבני(ות|יה|ה|ייה) (מגי|0?5 פרימיום|פרימיום)|מלפפון פרימיום|לימון פרימיום|בננה 0?5 פרימיום) \(לק"ג\)$')
    alt = {j for j, it in enumerate(D.items) if it[0].startswith("W:") and app_rx.match(it[1])}
    fb = {c: set(D.fallback(D.code2i[c])) for c in ("P01", "P03", "P39", "P28")}
    for j in alt:
        assert any(j in v for v in fb.values()), ("לא חלופה", D.items[j][1])
    sts = [s for s in D.stores if s["chain"] in ("חצי חינם", "ויקטורי", "מחסני השוק")][:40]
    for st in sts:
        P = D.store_prices(st, ())
        if any(j in P for j in alt if "עגבני" in D.items[j][1]):
            i = D.code2i["P01"]
            it = {"name": "עגבנייה", "qty": 1, "i": i, "g": [i], "fb": D.fallback(i), "wfb": None}
            assert app.lc(P, it) is not None, st["n"]
    # הכללים באיסוף הנתונים (מהעדכון הבא)
    import importlib.util
    src = open(os.path.join(ROOT, "tools", "collect.py"), encoding="utf-8").read()
    ns = {"re": re}; exec(src[src.index("PRODUCE = ["):src.index("PRODUCE_RX =")], ns)
    exec(src[src.index("def clean_produce"):src.index("# ---------- קריאת XML")], ns)
    R = dict((n, re.compile(rx)) for n, rx in ns["PRODUCE"])
    for name, want in (("עגבניות מגי", "עגבנייה"), ("עגבניה 05 פרימיום", "עגבנייה"), ("עגבניות", "עגבנייה"), ("בצל", "בצל יבש"),
                       ("מלפפון פרימיום", "מלפפון"), ("עגבניות שרי תמר", None), ("עגבניות ירוקות", None), ("מלפפון במלח", None)):
        got = [n for n, r in R.items() if r.search(ns["clean_produce"](name))]
        assert got == ([want] if want else []), (name, got)

@test("קבלות: תפו\"א לבן = תפוח אדמה רגיל, 'ביצים 30' = תבנית של 30, מוצר שאין בשום סניף – לא 'מחיר הסל ₪0' – באגים 12/10")
def _():
    D = kdata.D
    f, m = app.match_items([{"name": 'תפו"א לבן', "qty": 2.3}, {"name": "תפוא", "qty": 1}, {"name": "ביצים 30", "qty": 1}, {"name": "ביצים", "qty": 1}])
    assert not m, m
    assert f[0]["code"] == "P12" and f[1]["code"] == "P12", [x["name"] for x in f]
    assert "30" in f[2]["name"] and "12" not in f[2]["name"], f[2]["name"]
    assert f[3]["code"] == "E01", f[3]["name"]
    i = min((j for j, it in enumerate(D.items) if it[0][:1].isdigit()), key=lambda j: D.items[j][2])   # המוצר הכי נדיר במאגר
    have = [s["p"] for s in D.stores if i in D.store_prices(s, ())]
    far = next(s for s in D.stores if all(app.km(p, s["p"]) > 80 for p in have))
    u = {"step": "done", "prefs": {"home": far["p"], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
         "items": [app.item_of(D, i, 1)]}
    r = app.compute(u)
    if isinstance(r, dict): assert "₪0 " not in r["main"] and "₪0\n" not in r["main"], r["main"]
    else: assert "לא מצאתי" in r[0], r
    # כללי איסוף: "חזה טרי מחפוד" (רמי לוי) ו"עוף מוכשר שלם" (ברקת) נכנסים לקבוצה, "חזה הודו"/"חזה בקר" לא
    src = open(os.path.join(ROOT, "tools", "collect.py"), encoding="utf-8").read()
    ns = {"re": re}; exec(src[src.index("NO_MEAT ="):src.index("MEAT = [")], ns)
    exec(src[src.index("MEAT = ["):src.index("]\n", src.index("MEAT = [")) + 2], ns)
    R = [(n, re.compile(rx)) for n, rx, lo, hi in ns["MEAT"]]
    for name, want in (("חזה טרי מחפוד", "חזה עוף"), ("חזה עוף", "חזה עוף"), ("עוף מוכשר שלם", "עוף שלם"), ("עוף שלם", "עוף שלם"),
                       ("חזה הודו טרי", None), ("חזה בקר", None), ("חזה עוף קפוא כש", None)):
        got = [n for n, r in R if r.search(name)]
        assert got == ([want] if want else []), (name, got)

@test("חיפוש באגים 12/10 (סבב 2): ביצים בכל תבנית 12, חלב סויה, שניצל, מיקום מחוץ לישראל, אזור רחוק, הסופר הקרוב עם סל מלא")
def _():
    D = kdata.D
    # ביצים: "ביצים תבנית מידה M ביצי הכפר 12 יח'" / "ביצי חופש 12" הם חלופה ל"ביצים L (12)"
    fb = {D.items[j][1] for j in D.fallback(D.code2i["E01"])}
    assert any("ביצי הכפר" in n or "ביצי חופש" in n for n in fb) and not any("הפתעה" in n or "קינדר" in n for n in fb), sorted(fb)[:8]
    f, m = app.match_items([{"name": n, "qty": 1} for n in ("חלב סויה", "חלב שקדים", "שניצל", "צלי כתף", "חלות", "נייר אלומיניום")])
    got = {x["src"]: x["name"] for x in f}
    assert not m, m
    assert got["חלב סויה"].startswith("משקה סויה") and got["חלב שקדים"].startswith("משקה שקדים"), got
    assert got["שניצל"].startswith("שניצל עוף") and "תירס" not in got["שניצל"], got["שניצל"]
    assert got["צלי כתף"] == 'בשר בקר לבישול (לק"ג)' and got["חלות"].startswith("חלה"), got
    # מיקום מחוץ לישראל (או 0,0) – לא נשמר, מבקשים שוב
    to = "tg:3535"; setup_user(to, 32.0809, 34.7806)
    for la, lo in ((0.0, 0.0), (34.9, 33.6), (31.95, 35.93)):
        OUT.clear(); loc(to, la, lo)
        assert "מחוץ לישראל" in last(), last()
        assert app.km(app.USERS[to]["prefs"]["home"], [32.0809, 34.7806]) < 1
    assert app.place_name([29.893, 35.06]) == "המיקום ששלחת"              # יטבתה – לא "אזור אילת"
    assert app.place_name([32.0809, 34.7806]).startswith("אזור ")
    # בלי סופר קבוע: הסניף הקרוב חסר מוצרים (AM:PM) → ההשוואה מול סניף מלא שקרוב כמעט באותה מידה
    names = ["חלב", "לחם אחיד", "ביצים", "קוטג'", "פסטה", "אורז", "שמן", "עגבניה", "מלפפון", "בננה", "חזה עוף", "במבה", "נייר טואלט"]
    f, m = app.match_items([{"name": n, "qty": 1} for n in names])
    for home in ((32.7600, 34.9710), (31.8140, 34.7790)):                   # טירת כרמל, גדרה (12/10: AM:PM / אקספרס בלי חזה עוף)
        r = app.compute({"step": "done", "prefs": {"home": list(home), "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [], "items": [dict(x) for x in f]})
        if "הסופר הקרוב אליך משתלם" in r["main"]:
            assert not r["miss"], (r["miss"], r["main"])

@test("קבלה: 'שופרסל דיל קריית אתא' → סניף דיל (לא BE), 'חלות' בסניף בלי המותג → חלה אחרת, שם אזור לפי הסניף הצמוד – באגים 12/10")
def _():
    D = kdata.D; ata = (32.8090, 35.1070)
    name = lambda k: next((s["n"] for s in D.stores if s["key"] == k), "")
    assert "דיל" in name(app.match_store("שופרסל דיל – קריית אתא", ata)), name(app.match_store("שופרסל דיל – קריית אתא", ata))
    assert "BE" not in name(app.match_store("שופרסל – קריית אתא", ata))
    assert "BE" in name(app.match_store("שופרסל BE קריית אתא", ata))
    assert "שלי" in name(app.match_store("שופרסל שלי קריית אתא איינשטיין", ata))
    st = next(s for s in D.stores if s["chain"] == "אושר עד" and "אונו" in s["n"])
    f, m = app.match_items([{"name": "חלות", "qty": 2}])
    u = {"step": "done", "prefs": {"home": st["p"], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [], "items": f}
    r = app.compute(u)
    assert not any(st["n"] in nm or "אונו" in nm and st["chain"] in nm for nm, ml in r["miss_all"]), r["miss_all"]
    for town, p in (("ירוחם", (30.987, 34.929)), ("שלומי", (33.075, 35.145))):
        assert app.place_name(p) == f"אזור {town}", (town, app.place_name(p))

@test("קבלה: 0.5 ק\"ג שהותאם למוצר ארוז של 500 גרם = אריזה אחת (לא חצי) – באג 12/10")
def _():
    f, m = app.match_items([{"name": "הודו טחון", "qty": 0.5}, {"name": "עגבניה", "qty": 1.35}, {"name": "במבה", "qty": 3}])
    q = {x["src"]: x["qty"] for x in f}
    assert q["הודו טחון"] == 1 and q["עגבניה"] == 1.35 and q["במבה"] == 3, q

@test("לימוד עצמי: שם חדש של מוצר בסיסי נלמד לבד (בלי שרי/הודו/קפוא/פלפל אחר), ושומר מוצרים בסיסיים מתריע רק על חדש – 12/10")
def _():
    D = kdata.D
    fb = lambda c: {D.items[j][1] for j in D.fallback(D.code2i[c])}
    tom, pep, beef, chick = fb("P01"), fb("P04"), fb("M08"), fb("M01")
    assert not any("שרי" in n for n in tom), [n for n in tom if "שרי" in n]
    assert not any(re.search(r"חריף|שושקה|צ.ילי|רמירו", n) for n in pep), sorted(pep)   # צהוב/בהיר – כלל ישן ומכוון (FB_FAMILY)
    assert not any(re.search(r"עוף|לבן|הודו|עגל", n) for n in beef), sorted(beef)
    assert not any(re.search(r"הודו|קפוא|בקר|טחון", n) for n in chick), sorted(chick)
    # התנהגות: פריט "חדש" עם אותו שם בסיסי ומחיר דומה – נכנס; אותו שם במחיר כפול – לא
    i = D.code2i["P01"]; core = lambda t: {kdata.stem(w).rstrip("י") for w in kdata.toks(t) if w not in kdata.FB_NEUTRAL}
    p0 = D.items[i][3]
    D.items.append(["W:test1", 'עגבניות טבע חדש (לק"ג)', 5, p0]); D.items.append(["W:test2", 'עגבניות טבע יקר (לק"ג)', 5, p0 * 3])
    try:
        j1, j2 = len(D.items) - 2, len(D.items) - 1
        assert D.learned_alt(i, j1, core(D.items[i][1]), core) and not D.learned_alt(i, j2, core(D.items[i][1]), core)
    finally:
        del D.items[-2:]
    # שומר: פעם ראשונה – שקט; בעיה ידועה – שקט; בעיה חדשה – התראה למנהל
    meta = app.USERS.setdefault("_meta", {}); meta.pop("basics_known", None); app._basics["day"] = ""
    AT10 = app._dt.now(app.IL_TZ).replace(hour=10)          # השומר רץ רק אחרי 08:00 – בלי זה הבדיקה נכשלה בלילה (18/10)
    orig = app.basic_coverage
    try:
        app.basic_coverage = lambda: [("רשת א", "עגבנייה", 9, 10, ["עגבניות טבע (לק\"ג)"])]
        OUT.clear(); app.basics_daily(now=AT10); assert not any("שומר מוצרים" in t for t in texts()), "שלח בהפעלה ראשונה"
        app._basics["day"] = ""; OUT.clear(); app.basics_daily(now=AT10); assert not any("שומר מוצרים" in t for t in texts()), "שלח שוב בעיה ידועה"
        app.basic_coverage = lambda: [("רשת א", "עגבנייה", 9, 10, []), ("רשת ב", "בננה", 7, 8, ["בננה טבע (לק\"ג)"])]
        app._basics["day"] = ""; OUT.clear(); app.basics_daily(now=AT10)
        t = "\n".join(texts()); assert "רשת ב" in t and "בננה טבע" in t and "רשת א" not in t, t
        assert "מוצרים בסיסיים שחסרים" in app.weekly_report()
    finally:
        app.basic_coverage = orig; meta.pop("basics_known", None); meta.pop("basics_last", None)

@test("קפוא כחלופה (אורי 12/10): אין חזה עוף/עוף שלם/בשר טחון טרי בסניף → הקפוא, עם 'במקום'; יש טרי → הטרי")
def _():
    D = kdata.D
    for c, bad in (("M01", r"הודו|בקר|שניצל|מעושן"), ("M02", r"עגל|תירס|טבעול"), ("M07", r"טחון|שניצל|חזה|כנפ"), ("M08", r"עוף|הודו|קבב")):
        fz = app.frozen_alts(D.code2i[c])
        assert fz, c
        names = [D.items[j][1] for j, _ in fz]
        assert all(app.FROZEN_WORD.search(n) for n in names) and not any(re.search(bad, n) for n in names), (c, names[:6])
    # סניף ברשת חרדית בלי חזה עוף טרי – יוצא קפוא (במקום), לא "חסר"
    i = D.code2i["M01"]; fz = app.frozen_alts(i)
    it = {"name": "חזה עוף", "qty": 1.5, "i": i, "g": [i], "fb": D.fallback(i), "wfb": None, "frz": fz}
    hit = None
    for st in D.stores:
        if st["chain"] not in ("שפע ברכת השם", "נתיב החסד", "זול ובגדול", "ויקטורי"): continue
        P = D.store_prices(st, ())
        if i not in P and not any(j in P for j in it["fb"]) and any(j in P for j, _ in fz): hit = (st, P); break
    assert hit, "לא נמצא סניף לבדיקה"
    c = app.lc(hit[1], it)
    assert c and c[2] and app.FROZEN_WORD.search(c[2]), c
    for st in D.stores:                                                     # יש טרי – לא לוקחים קפוא
        P = D.store_prices(st, ())
        if i in P:
            c = app.lc(P, it); assert c and c[2] is None, c; break
    # מחיר: קפוא במשקל = מחיר לק"ג × כמות; קפוא ארוז = מספר אריזות
    j, kg = next((j, kg) for j, kg in app.frozen_alts(D.code2i["M08"]) if kg)
    P = kdata.Prices({j: 40.0}); it8 = {"name": "בשר טחון", "qty": 1.0, "i": D.code2i["M08"], "g": [D.code2i["M08"]], "fb": [], "wfb": None, "frz": [(j, kg)]}
    assert abs(app.lc(P, it8)[0] - 40.0 * max(1, round(1.0 / kg))) < 0.01

@test("אותה רשימה → אותו מחיר גם כש-Claude בוחר כל פעם מוצר אחר (בדיקה יומית 13/10: ₪240 מול ₪217) + תיקון של משתמש נזכר")
def _():
    meta = app.USERS.setdefault("_meta", {}); meta.pop("match_mem", None)
    orig = app.claude_json; n = [0]
    def flaky(prompt, *a, **k):                      # כל קריאה בוחרת אפשרות אחרת – כמו Claude לא עקבי
        if prompt.startswith("Match"):
            n[0] += 1; arr = json.loads(prompt.rsplit("\n\n", 1)[1])
            return [{"i": x["i"], "pick": (n[0] + x["i"]) % len(x["options"])} for x in arr]
        return orig(prompt, *a, **k)
    app.claude_json = flaky
    try:
        names = [{"name": x, "qty": 1} for x in ("גבינה צהובה", "אורז פרסי", "שמן קנולה", "קפה נמס", "במבה")]
        a, _ = app.match_items([dict(x) for x in names]); b, _ = app.match_items([dict(x) for x in names])
        assert [x["code"] for x in a] == [x["code"] for x in b], ([x["name"] for x in a], [x["name"] for x in b])
        assert n[0] == 1, f"Claude נשאל שוב על שמות שכבר הוחלטו ({n[0]})"
        u = {"my_match": {app.mkey("במבה"): b[-1]["c"][0]}}                 # המשתמש תיקן ל"במבה" אחרת
        c, _ = app.match_items([dict(x) for x in names], u)
        assert c[-1]["code"] == b[-1]["c"][0] and [x["code"] for x in c[:-1]] == [x["code"] for x in b[:-1]]
    finally:
        app.claude_json = orig; meta.pop("match_mem", None)

@test("השרת הישראלי מושתק (אורי 13/10): אין התראות על 5 הרשתות שלו; כשהן חוזרות להתעדכן – הודעת 'חזר' וההשתקה מתבטלת")
def _():
    D = kdata.D; meta = app.USERS.setdefault("_meta", {}); meta.pop("il_mute", None)
    old = {c: dict(D.chains.get(c) or {}) for c in list(app.IL_SERVER) + ["SHUFERSAL"]}
    try:
        for c in app.IL_SERVER: D.chains[c] = dict(old[c], fetched="2026-01-01T00:00+02:00", stores=old[c].get("stores", 50))
        D.chains["SHUFERSAL"] = dict(old["SHUFERSAL"], fetched="2026-01-01T00:00+02:00")
        iss = "\n".join(app.watchdog_issues())
        assert not any(he in iss for he in app.IL_SERVER.values()), iss
        assert "שופרסל לא התעדכנה" in iss, iss                              # רשת רגילה – כן מתריעים
        now = app._dt.now(app.IL_TZ).isoformat(timespec="minutes")
        for c in app.IL_SERVER: D.chains[c]["fetched"] = now
        iss = "\n".join(app.watchdog_issues())
        assert "השרת הישראלי חזר" in iss and meta.get("il_mute") is False, iss
        assert "השרת הישראלי חזר" not in "\n".join(app.watchdog_issues())     # פעם אחת בלבד
    finally:
        for c, v in old.items():
            if v: D.chains[c] = v
            else: D.chains.pop(c, None)
        meta.pop("il_mute", None)

@test("רשת קטנה שנעלמה כולה מהנתונים (סופר ספיר 13/10) – השומר מתריע, לא רק על הרשתות הגדולות")
def _():
    D = kdata.D; meta = app.USERS.setdefault("_meta", {}); seen = meta.setdefault("seen", {})
    seen["SUPER_SAPIR"] = 63; saved = D.chains.pop("SUPER_SAPIR", None)
    try:
        iss = "\n".join(app.watchdog_issues())
        assert "סופר ספיר נעלמה מהנתונים (היו 63 סניפים)" in iss, iss
    finally:
        if saved: D.chains["SUPER_SAPIR"] = saved
        seen.pop("SUPER_SAPIR", None)

@test("שופרסל המפיץ סיטונאות לא בהשוואה (אורי 13/10)")
def _():
    assert not any("המפיץ" in s["n"] or "סיטונאות" in s["n"] for s in kdata.D.stores)
    r = app.compute({"step": "done", "prefs": {"home": [31.2518, 34.7913], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
                     "items": app.match_items([{"name": n, "qty": 1} for n in ("חלב", "במבה", "פסטה", "שמן")])[0]})
    assert "המפיץ" not in r["main"] + r["others"]

@test("זיכרון (Render 13/10 – עבר 512MB ונהרג): מטמון מחירים קטן, סריקת כל הסניפים לא מנפחת אותו, free משחרר")
def _():
    D = kdata.D
    assert kdata.PRICE_CACHE <= 80, kdata.PRICE_CACHE
    for st in D.stores[:200]: D.prices(st["blob"])
    assert len(D._cache) <= kdata.PRICE_CACHE, len(D._cache)
    before = len(D._cache); app.basic_coverage()
    assert len(D._cache) <= max(before, kdata.PRICE_CACHE), "השומר ניפח את המטמון"
    D.free(); assert len(D._cache) == 0 and not D._groups
    r = app.compute({"step": "done", "prefs": {"home": [32.0809, 34.7806], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
                     "items": app.match_items([{"name": "חלב", "qty": 1}, {"name": "במבה", "qty": 1}])[0]})
    assert isinstance(r, dict)                                            # עובד גם אחרי שחרור

@test("לימוד הרגלים: 4 קבלות → סל קבוע + כמויות + סופר קבוע; קבלה בלי חלב/לחם → 'בדרך כלל אתה קונה גם'; 'כן, להוסיף' מוסיף")
def _():
    to = "tg:3636"; setup_user(to, 32.0880, 34.8860)
    week = [("חלב תנובה 3% 1ל", 3), ("לחם אחיד פרוס", 2), ("קוטג' 5%", 2), ("ביצים L 12", 1), ("עגבניה", 1.4), ("במבה 80ג", 3), ("פסטה ספגטי", 2)]
    def rcpt(items):
        def fk(prompt, image=None, mime=None, keep_dict=False):
            if prompt.startswith(app.RECEIPT_PROMPT[:40]) and image:
                return {"store": "רמי לוי – שטמפפר פתח תקווה", "items": [{"name": n, "qty": q} for n, q in items], "quality": "ok", "issue": ""}
            return fake_claude(prompt, image, mime, keep_dict)
        orig = app.claude_json; app.claude_json = fk
        try: OUT.clear(); photo(to)
        finally: app.claude_json = orig
        app.USERS[to]["rbuf_t"] = 0
        if app.USERS[to].get("step") == "forgot": press(to, "noadd")
    for w in range(4): rcpt(week + ([("קפה נמס", 1)] if w == 0 else []))
    u = app.USERS[to]; L = app.learned(u); q = {x["src"]: x["qty"] for x in L}
    assert len(L) >= 6 and not any("קפה" in x["name"] for x in L), [x["name"] for x in L]
    assert abs(q["חלב תנובה 3% 1ל"] - 3) < 0.6, q
    assert "רמי לוי" in next(s["n"] for s in kdata.D.stores if s["key"] == app.usual_store(u))
    def fk2(prompt, image=None, mime=None, keep_dict=False):
        if prompt.startswith(app.RECEIPT_PROMPT[:40]) and image:
            return {"store": "רמי לוי – שטמפפר פתח תקווה", "items": [{"name": n, "qty": q} for n, q in week[2:]], "quality": "ok", "issue": ""}
        return fake_claude(prompt, image, mime, keep_dict)
    orig = app.claude_json; app.claude_json = fk2
    try: OUT.clear(); photo(to)
    finally: app.claude_json = orig
    t = "\n".join(texts()); assert "בדרך כלל" in t and "חלב" in t and "לחם" in t, t[:300]
    n0 = len(app.USERS[to]["items"]); press(to, "addf"); assert len(app.USERS[to]["items"]) >= n0 + 2

@test("ניסיון התקלה 13/10: 10 הודעות במקביל מאותו משתמש, זיכרון משתמש פגום, כפתורים פגומים, כמויות קיצוניות – בלי קריסה")
def _():
    import threading, copy
    to = "tg:3737"; setup_user(to, 32.0809, 34.7806); send(to, "חלב, במבה")
    OUT.clear(); errs = []
    def w(m):
        try: app.safe_handle(_msg(to, type="text", text={"body": m}))
        except Exception as ex: errs.append(ex)
    th = [threading.Thread(target=w, args=(m,)) for m in ("חלב", "איפוס", "היי", "פסטה, אורז", "הגדרות", "איפוס מלא", "שמן", "במבה", "תודה", "חלב, ביצים")]
    [x.start() for x in th]; [x.join(60) for x in th]
    assert not errs and not any("משהו השתבש" in t for t in texts()), (errs, [t[:60] for t in texts() if "השתבש" in t])
    for bad in ({"step": "done", "hist": []}, {"step": "done", "prefs": None, "hist": []}, {"step": "done", "prefs": {"home": "32,34"}, "hist": None},
                {"step": 5, "prefs": {"home": [32.08, 34.78]}, "hist": [{"d": 1}, "x"], "items": "x", "stores": {"k": "v"}}):
        for act in (lambda: send(to, "חלב, במבה"), lambda: send(to, "היי"), lambda: press(to, "mine"), lambda: loc(to, 31.25, 34.79)):
            app.USERS[to] = copy.deepcopy(bad); OUT.clear(); act()
            assert not any("משהו השתבש" in t for t in texts()), (bad, [t[:80] for t in texts()])
    setup_user(to, 32.0809, 34.7806); send(to, "חלב, במבה")
    for b in ("alt:x:y", "fix:abc", "fix:-1", "alt:0:99", "del:50", "st:garbage", "club:XYZ"):
        OUT.clear(); press(to, b); assert not any("משהו השתבש" in t for t in texts()), b
    u = app.repair_user({"prefs": {"home": [32.0, 34.8]}, "items": [{"name": "חלב", "qty": 1000000}, {"name": "במבה", "qty": -2}, {"name": "x", "qty": None}, {"qty": 1}]})
    assert [x["qty"] for x in u["items"]] == [200, 1, 1], u["items"]

@test("התראת מבצעים (סעיף 3, 13/10): מבצע 20%+ על מוצר מהסל הקבוע ליד הבית; לא שולח שוב תוך 3 ימים; כיבוי בהגדרות; בלי סל קבוע – כלום")
def _():
    to = "tg:3838"; setup_user(to, 32.0809, 34.7806)
    names = ["חלב", "לחם אחיד", "קוטג'", "פסטה", "אורז", "שמן", "קפה", "במבה", "ביסלי", "שוקולד", "קולה", "נייר טואלט", "נוזל כלים", "טונה"]
    f, _ = app.match_items([{"name": n, "qty": 1} for n in names])
    u = app.USERS[to]; u["hist"] = [{"d": 1, "saved": 0, "items": [{k: x[k] for k in ("code", "name", "src", "qty")} for x in f]}] * 3
    assert app.send_deals(to, {"step": "done", "prefs": dict(u["prefs"]), "hist": []}) is False        # בלי סל קבוע
    deals = app.find_deals(u)
    assert deals, "לא נמצא אף מבצע בתל אביב"
    for save, line, code in deals:
        assert save >= app.DEAL_MIN_SAVE and "במקום" in line, line
        m = re.search(r"^• \*[^*]+\* – (\d+) ב", line)
        if m: assert int(m.group(1)) <= 3, line                              # מבצע כמות – לא "9 ב-30" למי שקונה 1
    OUT.clear(); assert app.send_deals(to, u) is True
    t = texts()[-1]; assert "מבצעים על מוצרים שאתה קונה" in t and "deals_off" in buttons(), t[:200]
    OUT.clear(); assert app.send_deals(to, u) is False and not texts()                                 # לא שוב תוך 3 ימים
    press(to, "deals_off"); assert app.USERS[to]["prefs"].get("deals_off")
    assert app.send_deals(to, app.USERS[to], now=time.time() + 10 * 86400) is False                     # כובה
    press(to, "set:deals"); assert not app.USERS[to]["prefs"].get("deals_off")                          # הופעל מחדש

@test("סבב התקלה 3 (13/10): סטטיסטיקה כשעדכון נחסם, חיסכון עם היסטוריה פגומה, פיצול – כל הסל ובחשבון נכון")
def _():
    kdata.GATE.clear(); kdata.GATE.update(reason="בדיקה", version="x", t=time.time())
    app.USERS["tg:brk"] = {"step": "done"}; app.USERS["tg:brk2"] = {"prefs": None, "hist": [{"d": "x", "saved": None}]}
    try:
        t = app.stats_text(); assert "נחסם" in t, t[-300:]
    finally:
        kdata.GATE.clear(); app.USERS.pop("tg:brk", None); app.USERS.pop("tg:brk2", None)
    for h in ([{"d": time.time(), "saved": None}], [{"d": "x", "saved": 5}], [{"d": time.time(), "saved": -50}], [{"d": time.time(), "saved": 1e9}]):
        app.savings_text({"prefs": {}, "hist": h})
    names = ["חלב", "לחם אחיד", "ביצים", "קוטג'", "גבינה צהובה", "פסטה", "אורז", "שמן", "קפה", "במבה", "ביסלי", "שוקולד", "קולה", "נייר טואלט",
             "נוזל כלים", "אבקת כביסה", "עגבניה", "מלפפון", "חזה עוף", "בשר טחון", "טונה", "טחינה", "סוכר", "קמח", "שמפו", "חיתולים", "ריבה", "חמאה"]
    f, _ = app.match_items([{"name": n, "qty": 2} for n in names])
    found = False
    for home in ([31.2518, 34.7913], [31.7857, 35.2007], [32.794, 34.9896]):
        r = app.compute({"step": "done", "prefs": {"home": home, "mode": "car", "maxMin": 35, "splitMin": app.SPLIT_MIN, "clubs": []}, "hist": [], "items": [dict(x) for x in f]})
        if not r.get("split"): continue
        found = True
        assert r["split"].count("• ") == len(f), (r["split"].count("• "), len(f))       # כל מוצר באחד משני הסופרים
        save = int(re.search(r"חוסכים עוד \*₪(\d+)", r["others"]).group(1)); assert save > app.SPLIT_MIN, save
    assert found, "פיצול לא הופעל באף עיר בסל גדול"

@test("חיפוש באגים שבועי 18/10: מוצר שהמותג בסוף השם ('גבינה צהובה פרוס בקופסא %28 200 ג' עמק') = חלופה ל'עמק פרוס 28%'; בלי רוטב/סלט/משקה")
def _():
    assert kdata._pcts("גבינה צהובה פרוס %28 200 ג'") == {"28"} and kdata._pcts("חלב 3% 2 ליטר") == {"3"}
    D = kdata.D
    a = next((i for i, it in enumerate(D.items) if it[1].startswith("עמק פרוס 28%") and D.size(i) == 200), None)
    b = next((i for i, it in enumerate(D.items) if it[1].startswith("גבינה צהובה פרוס בקופסא %28 200")), None)
    if a is not None and b is not None:
        assert b in D.fallback(a, "עמק פרוס 28%"), [D.items[j][1] for j in D.fallback(a, "עמק פרוס 28%")]
    for q in ("סוכר לבן 1ק", "שמנת חמוצה", "תות שדה", "גבינה בולגרית", "שוקולד פרה", "יוגורט דנונה", "עוף טחון"):
        f, _ = app.match_items([{"name": q, "qty": 1}])
        for x in f:
            i = D.code2i[x["code"]]
            bad = [D.items[j][1] for j in D.fallback(i, q) if re.match(r"(משקה|רוטב|סלט|קונפיטורת|מלית|סירופ|ביסקוויט|מעדן|המבורגר|ערגליות)", D.items[j][1])]
            assert not bad, (q, bad)

@test("חסרים (אורי 18/10): בהודעה רק כמה חסרים, לא רשימת שמות; כפתור 'מה חסר (N)' מציג אותם; גם בסופרים אחרים")
def _():
    to = "tg:miss18"; setup_user(to, 31.8948, 34.8113)                         # רחובות
    app.USERS[to]["items"] = [dict(x) for x in BBQ_ITEMS]
    OUT.clear(); app.finish(to, app.USERS[to], redo=True)
    last = app.USERS[to]["last"]; main = next(t for t in texts() if "מחיר הסל" in t)
    assert "חסר בסניף:" not in main and "חסר בסניף:" not in last["others"], (main, last["others"][:400])
    n = len(last["miss"]) + len(last["rare"])
    assert n, "בסל הזה תמיד חסר משהו"
    assert f"מה חסר ({n})" in str(OUT[-1].get("reply_markup")), OUT[-1].get("reply_markup")
    OUT.clear(); press(to, "miss"); t = html.unescape(texts()[-1])
    for x in last["miss"] + last["rare"]: assert x in t, (x, t)
    if last["miss_all"]:
        OUT.clear(); press(to, "oth"); assert "missall" in str(OUT[-1]), OUT[-1]
        OUT.clear(); press(to, "missall"); t = html.unescape(texts()[-1])
        for nm, ml in last["miss_all"]: assert nm in t and all(x in t for x in ml), t
    for nm, ml in last["miss_all"]: assert len(ml) < app.MAX_MISSING                # עד 3 חסרים בסניף
    to2 = "tg:miss18b"; setup_user(to2, 31.8948, 34.8113)
    f, _ = app.match_items([{"name": "חלב", "qty": 1}]); app.USERS[to2]["items"] = f
    OUT.clear(); app.finish(to2, app.USERS[to2], redo=True); assert "מה חסר" not in str(OUT[-1].get("reply_markup")), OUT[-1]

@test("סעיף 1 מ-VISION (אורי 18/10): הבוט יוזם 'הקנייה של השבוע' – סל משוער + המלצה לאישור; תחזית לא נשמרת כקנייה; תיקון ואישור")
def _():
    to = "tg:plan18"; setup_user(to, 32.0880, 34.8860)
    f, _ = app.match_items([{"name": n, "qty": q} for n, q in (("חלב", 2), ("לחם אחיד", 1), ("ביצים", 1), ("קוטג'", 2), ("עגבניה", 1.5), ("במבה", 3))])
    hist = [{"d": int(time.time()) - k * 7 * 86400, "saved": 0, "items": [{"code": x["code"], "name": x["name"], "src": x["src"], "qty": x["qty"]} for x in f]} for k in (3, 2, 1)]
    app.USERS[to]["hist"] = [dict(h) for h in hist]
    OUT.clear(); app.send_reminder(to, app.USERS[to])
    t = texts()[-1]; u = app.USERS[to]
    assert "הקנייה של השבוע" in t and "הסל הרגיל שלך (6 מוצרים)" in t and "מחיר הסל" in t and "לתקן" in t, t
    assert "plan_ok" in str(OUT[-1].get("reply_markup")) and "fix" in str(OUT[-1].get("reply_markup")), OUT[-1]
    assert len(u["hist"]) == 3 and u.get("plan"), "הסל המשוער לא נשמר כקנייה"
    OUT.clear(); press(to, "fix"); press(to, "del:0")                            # מורידים מוצר
    assert len(u["items"]) == 5 and len(u["hist"]) == 3 and u["hist"][-1]["items"][0]["code"] == f[0]["code"], "תיקון התחזית דרס קנייה אמיתית"
    assert "plan_ok" in str(OUT[-1].get("reply_markup")), OUT[-1]
    OUT.clear(); press(to, "plan_ok"); t = "\n".join(texts())
    assert "סגור" in t and "waze.com" in t and "הסל שלך" in t, t[:300]
    OUT.clear(); send(to, "חלב, פסטה")                                          # רשימה חדשה = קנייה רגילה
    if u.get("step") == "forgot": press(to, "noadd")                            # "בדרך כלל אתה קונה גם…"
    assert not u.get("plan") and len(u["hist"]) == 4, (u.get("plan"), len(u["hist"]))
    to3 = "tg:plan18c"; setup_user(to3, 32.0880, 34.8860)                        # בלי היסטוריה – התזכורת הרגילה
    OUT.clear(); app.send_reminder(to3, app.USERS[to3]); assert "בדרך כלל אתה עושה קנייה" in texts()[-1]

@test("וולט מרקט (אורי 19/10): אותו סל במשלוח – שורה אחת + כפתור להזמנה, רק כשיש מחסן קרוב; לא נכנס להשוואה")
def _():
    D = kdata.D; home = [32.0880, 34.8860]
    src = min((st for st in D.stores if st["chain"] == "שופרסל" and "דיל" in st["n"]), key=lambda st: app.km(home, st["p"]))
    fake = dict(src, key="wolt-test", chain="וולט מרקט", n="וולט מרקט פתח תקווה", p=[home[0] + 0.01, home[1]])
    f, _ = app.match_items([{"name": n, "qty": 1} for n in ("חלב", "לחם אחיד", "ביצים", "קוטג'", "במבה")])
    old = D.delivery
    try:
        D.delivery = [fake]
        to = "tg:wolt19"; setup_user(to, *home); app.USERS[to]["items"] = [dict(x) for x in f]
        OUT.clear(); app.finish(to, app.USERS[to], redo=True)
        t = texts()[-1]; kb = str(OUT[-1].get("reply_markup"))
        assert "במשלוח מ" in t and "וולט מרקט" in t and "דמי משלוח" in t, t
        assert app.WOLT_URL in kb, kb
        assert "וולט" not in app.USERS[to]["last"]["store"] and "וולט" not in app.USERS[to]["last"]["others"]   # לא בהשוואה
        D.delivery = [dict(fake, p=[home[0] + 0.2, home[1]])]                                                  # ~25 ק"מ – לא
        OUT.clear(); app.finish(to, app.USERS[to], redo=True)
        assert "במשלוח" not in texts()[-1] and app.WOLT_URL not in str(OUT[-1].get("reply_markup"))
    finally:
        D.delivery = old
    assert not any(kdata.DELIVERY_STORES.search(st["chain"] + " " + st["n"]) for st in D.stores)          # בנתונים: לא בין הסופרים

@test("יום קניות קבוע (אורי 19/10): נלמד מהקבלות; ערב לפני – 'חוסך ₪X' אם יש זול יותר, אחרת 'כל הכבוד'; לא מנחשים מקבלה אחת")
def _():
    from datetime import timedelta
    tue = app._dt(2026, 10, 13, 18, 30, tzinfo=app.IL_TZ)                                   # יום שלישי
    now = (tue + timedelta(days=7)).timestamp()
    u = {"receipts": [int((tue - timedelta(days=7 * k)).timestamp()) for k in range(3)]}
    assert app.shop_day(u, now) == 1, app.shop_day(u, now)                                # שלישי (weekday=1)
    assert app.shop_day({"receipts": u["receipts"][:1]}, now) is None                      # קבלה אחת – לא מנחשים
    mixed = {"receipts": [int((tue - timedelta(days=k)).timestamp()) for k in (0, 8, 16, 24)]}   # ימים שונים
    assert app.shop_day(mixed, now) is None, app.shop_day(mixed, now)
    mon = app._dt(2026, 10, 19, 19, 0, tzinfo=app.IL_TZ); sun = mon - timedelta(days=1)
    assert app.is_daybefore(u, mon) and not app.is_daybefore(u, sun)
    to = "tg:dayb19"; setup_user(to, 32.0880, 34.8860)
    f, _ = app.match_items([{"name": n, "qty": 1} for n in ("חלב", "לחם אחיד", "ביצים", "קוטג'")])
    U = app.USERS[to]; U["receipts"] = list(u["receipts"])
    U["hist"] = [{"d": t, "saved": 0, "items": [{"code": x["code"], "name": x["name"], "src": x["src"], "qty": x["qty"]} for x in f]} for t in U["receipts"]]
    orig = app.compute
    try:
        base = {"main": "", "store": "רמי לוי שטמפפר", "saved": 0, "ip": {}, "nav": "https://waze.com/x", "details": "📋 הסל שלך", "others": "",
                "split": None, "miss": [], "rare": [], "miss_all": [], "have": 310, "sum": 310, "mins": 9, "kosher": True, "wolt": None}
        app.compute = lambda uu: dict(base, stay=False, gap=42, ref="שופרסל יש פ\"ת", ref_have=352, ref_sum=352)
        OUT.clear(); assert app.send_daybefore(to, U) == "switch"
        t = texts()[-1]; assert "מחר יום קניות" in t and "חוסך ₪42" in t and "₪352" in t and "plan_ok" in str(OUT[-1].get("reply_markup")), t
        assert len(t) < 260, len(t)                                                         # הודעה קצרה
        app.compute = lambda uu: dict(base, stay=True, gap=4, ref="רמי לוי שטמפפר", ref_have=314, ref_sum=314)
        OUT.clear(); assert app.send_daybefore(to, U) == "stay"
        t = texts()[-1]; assert "משתלם" in t and "רק ₪4" in t and "כל הכבוד" in t, t
    finally:
        app.compute = orig
    OUT.clear(); U.pop("dayb", None); U["remind_miss"] = 0
    app.daybefore_tick(now=mon); assert texts() and U.get("dayb") == mon.strftime("%Y-%m-%d")   # חישוב אמיתי – בלי קריסה
    n = len(OUT); app.daybefore_tick(now=mon); assert len(OUT) == n                                # פעם אחת בערב
    U.pop("dayb", None); OUT.clear(); app.daybefore_tick(now=sun); assert not texts()               # לא ערב לפני
    assert app.send_deals(to, U, now=mon.timestamp()) is False                                       # לא שתי הודעות באותו ערב

@test("מוצר שנמכר רק בוולט מרקט לא מוצע בחיפוש ('מוצרלה טרייה | דליקטסן' – 19/10); 'קינג סטור וולט' לא בסופרים; נתניה פולג בנתניה")
def _():
    D = kdata.D
    i = D.search("מוצרלה טרייה", 1)[0]
    old = D._dlv_only
    try:
        D._dlv_only = {i}; assert i not in D.search("מוצרלה טרייה", 6)
    finally:
        D._dlv_only = old
    for q in ("מוצרלה טרייה", "עמק", "גרעינים", "חלב", "במבה"):
        assert not set(D.search(q, 6)) & D.dlv_only(), q
    assert not any("וולט" in st["n"] for st in D.stores), [st["n"] for st in D.stores if "וולט" in st["n"]][:3]
    f, _ = app.match_items([{"name": "עמק", "qty": 1}])                       # לא "עמק 28% חריץ (לק"ג)" שנמכר ב-1% מהסניפים
    assert D.items[D.code2i[f[0]["code"]]][2] > 100, f[0]["name"]
    pol = next((st for st in D.delivery if st["key"] == "7290058249350-26"), None)
    if pol: assert pol["p"][0] > 32.2, pol["p"]

@test("פחות מלל (אורי 20/10): בתוצאה בלי 'זיהיתי N' ובלי '(אזור …)'; הגדרות – 5 כפתורים; הודעות מדליקים/מכבים מתוך 'הודעות'")
def _():
    to = "tg:short20"; setup_user(to, 32.0880, 34.8860)
    OUT.clear(); send(to, "חלב, לחם, ביצים")
    if app.USERS[to].get("step") == "forgot": press(to, "noadd")
    t = next(x for x in texts() if "מחיר הסל" in x)
    assert "זיהיתי" not in t and "(אזור" not in t and "דק' נסיעה ממך" in t, t
    assert len(t.split("\n")) <= 5, t
    OUT.clear(); send(to, "הגדרות"); kb = OUT[-1]["reply_markup"]["inline_keyboard"]
    assert sum(len(r) for r in kb) == 5, kb
    OUT.clear(); press(to, "set:notif"); assert "set:remind" in buttons() and "set:deals" in buttons()
    OUT.clear(); press(to, "set:remind"); assert app.USERS[to]["prefs"].get("remind_off") and "⬜ הודעה שבועית" in str(OUT[-1]["reply_markup"])
    press(to, "set:remind"); assert not app.USERS[to]["prefs"].get("remind_off")
    OUT.clear(); press(to, "set:more"); assert "איך משתמשים" in last() and "set:faq" in buttons() and "set:reset" in buttons()

@test("דיוק (אורי 20/10): עגבניות שרי בסניף שמוכר רק מארזים → המארז (לא 'חסר'); ירק/פרי מעובד או זן אחר – לא חלופה")
def _():
    D = kdata.D; i = D.code2i["P02"]; pk = app.packed_alts(i)
    assert pk, "אין חלופת מארז לעגבניות שרי"
    names = [D.items[j][1] for j, _ in pk]
    assert not any(re.search(r"כבוש|מיובש|רוטב|MUTTI|פיצה", n) for n in names), names
    j, kg = next((j, kg) for j, kg in pk if kg)
    P = kdata.Prices({j: 11.9})
    it = {"name": D.items[i][1], "qty": 0.9, "i": i, "g": [i], "fb": [], "wfb": None, "frz": [], "pk": pk}
    c = app.lc(P, it); assert c and c[2] and abs(c[0] - 11.9 * max(1, round(0.9 / kg))) < 0.01, c
    tom = [D.items[j][1] for j, _ in app.packed_alts(D.code2i["P01"])]
    assert not any("שרי" in n or "מקולפ" in n or "%" in n for n in tom), tom                # עגבנייה ≠ שרי / שימורים / רסק
    sh = [st for st in D.stores if st["chain"] == "שופרסל" and "דיל" in st["n"]][:40]
    f, _ = app.match_items([{"name": "עגבניות שרי", "qty": 1}]); x = f[0]; k = D.code2i[x["code"]]
    it = {"name": x["name"], "qty": 1, "i": k, "g": D.group(k, x["src"]), "fb": D.fallback(k, x["src"]), "wfb": None, "frz": [], "pk": app.packed_alts(k)}
    miss = sum(1 for st in sh if app.lc(D.store_prices(st, (), cache=False), it) is None)
    assert miss <= len(sh) * 0.25, f"עגבניות שרי חסר ב-{miss}/{len(sh)} סניפי שופרסל דיל"

@test("סעיף 5 (אורי 20/10): המבצעים בתוך ההודעה השבועית – הודעה אחת; אחריה אין הודעת מבצעים נפרדת")
def _():
    to = "tg:deal5"; setup_user(to, 32.0880, 34.8860); U = app.USERS[to]
    f, _ = app.match_items([{"name": n, "qty": 1} for n in ("חלב", "לחם אחיד", "ביצים")])
    U["hist"] = [{"d": int(time.time()) - k * 604800, "saved": 0, "items": [{"code": x["code"], "name": x["name"], "src": x["src"], "qty": 1} for x in f]} for k in (2, 1)]
    orig = app.find_deals
    try:
        app.find_deals = lambda u, n=5: [(5.0, "• *חלב* – ₪5 במקום ₪7 ברמי לוי", f[0]["code"])]
        OUT.clear(); app.send_reminder(to, U)
        t = texts()[-1]; assert "וגם במבצע השבוע" in t and "₪5 במקום ₪7" in t, t
        assert app.send_deals(to, U) is False                                       # לא הודעה נוספת
        U["prefs"]["deals_off"] = True; U.pop("deal_sent", None)
        OUT.clear(); app.send_reminder(to, U); assert "במבצע" not in texts()[-1]     # כיבה מבצעים – בלי
    finally:
        app.find_deals = orig

@test("דיוק התאמה (אורי 20/10): הגודל שנכתב קובע ('חלב 3% 2 ליטר', 'במבה 80ג'), מארז 4*160 = רביעייה, ק''ג = ק\"ג")
def _():
    D = kdata.D
    assert kdata.parse_size("אבקת כביסה קולור 4.5 ק''ג אריאל") == 4500 and kdata.parse_size("קמח 1 ק\"ג") == 1000
    assert kdata.toks("טונה (160x4 גרם)")[1:3] == ["160", "4"]
    for q, want in (("חלב 3% 2 ליטר", 2000), ("במבה 80ג", 80)):
        i = D.search(q, 1)[0]; assert D.size(i) == want, (q, D.items[i][1])
    i = D.search("טונה במים 4*160", 1)[0]
    assert re.search(r"4\s*[*xX]\s*160|160\s*[*xX]\s*4|רביעי", D.items[i][1]), D.items[i][1]

@test("הקבלה של אבא (אורי 20/10, יוחננוף נס ציונה ₪617): ברקוד = אותו מוצר, משווים מול מה ששולם, הסניף של הקבלה תמיד בהשוואה")
def _():
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
    import receipt_yohananof_nesziona as R
    D = kdata.D
    st = next((s for s in D.stores if s["chain"] == "יוחננוף" and "נס ציונה" in s["n"] + s["a"]), None)
    if not st: return
    if app.barcode_item(D, "7290000207162") is None: return                     # קובץ נתונים ישן בלי המוצר
    assert "100" in D.items[app.barcode_item(D, "7290000207162")][1]          # רסק 100 גרם, לא רוטב לפיצה
    assert app.barcode_item(D, "622") is None                                   # קוד פנימי קצר – לא ברקוד
    to = "tg:father20"; setup_user(to, st["p"][0] + 0.004, st["p"][1] + 0.003)
    items = [{"name": n, "qty": q, "price": p, "code": c} for n, q, p, c in R.ITEMS]
    def fk(prompt, image=None, mime=None, keep_dict=False):
        if prompt.startswith(app.RECEIPT_PROMPT[:40]) and image: return {"store": R.STORE, "items": items, "quality": "ok", "issue": ""}
        return fake_claude(prompt, image, mime, keep_dict)
    orig = app.claude_json; app.claude_json = fk
    try: OUT.clear(); photo(to)
    finally: app.claude_json = orig
    u = app.USERS[to]
    if u.get("step") == "forgot": press(to, "noadd")
    assert u.get("rstore") == st["key"], u.get("rstore")
    main = next(t for t in texts() if "💰" in t)
    assert "שילמת" in main, main                                                # מול מה ששולם בפועל – לא מול סופר אחר
    assert len(u["last"]["rare"]) <= 4, u["last"]["rare"]                       # לא "חסרים 8"
    bt = [x for x in u["items"] if x["name"].startswith("בטטה")]
    assert len(bt) == 1 and abs(bt[0]["qty"] - 1.99) < 0.01 and abs(bt[0]["paid"] - 15.72) < 0.01, bt   # שתי שורות בטטה = אחת
    rs = next(x for x in u["items"] if x.get("code") == D.items[app.barcode_item(D, "7290000207162")][0])
    assert rs["qty"] == 8, rs
    bul = next(x for x in u["items"] if "בולגרית" in x["src"])
    assert bul["qty"] < 1, bul                                                  # 0.18 ק"ג מדלפק ≠ אריזה שלמה של 250 גרם
    assert app.basket_price({"miss": 0, "have": 553.4, "sum": 553.4}) == "₪553"

# ---------- סיכום ----------
ok = sum(1 for r in RESULTS if r[0])
for passed, name, err in RESULTS:
    print(("✅ " if passed else "❌ ") + name)
    if not passed: print("   " + err.replace("\n", "\n   "))
print(f"\n{ok}/{len(RESULTS)} בדיקות עברו")
sys.exit(0 if ok == len(RESULTS) else 1)
