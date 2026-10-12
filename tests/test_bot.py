#!/usr/bin/env python3
"""שיחות מבחן אוטומטיות לבוט "קונה אישי".

רץ לפני כל עדכון (GitHub Actions). אם בדיקה נכשלת – העדכון לא עולה לבוט.
כל באג שנמצא בשטח → מוסיפים כאן בדיקה, כדי שלא יחזור.

הרצה מקומית:  DATA_CACHE=/path/koneh-data.bin python3 tests/test_bot.py
(בלי מפתחות אמיתיים: Claude מוחלף ב"Claude מזויף" שעונה תשובות קבועות, וכלום לא נשלח לטלגרם)
"""
import json, os, re, sys, tempfile, time, traceback

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

@test("משתמש חוזר: היי → אישור מיקום → סל שבועי")
def _():
    to = "tg:3002"; setup_user(to)
    send(to, "היי"); assert "loc_ok" in buttons(), buttons()
    press(to, "loc_ok"); assert "קונים" in last()
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
    want = {"פתיתים": ("פתיתים", "גבינה"), "חלב סויה": ("חלב", "חלבה"), "שקיות זבל": ("שקיות אשפה", None),
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
        for blk in [r["main"]] + r["others"].split("\n\n"):
            m = re.search(r"חסר בסניף: ([^_]*)_", blk)
            if m: assert len(m.group(1).split(", ")) < app.MAX_MISSING, blk[:300]
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

@test("מחיר הסל מציג גם הערכה לחסרים ('₪X + ~₪Y על N שחסרים') – בקשה 12/10")
def _():
    r = {"miss": 3, "sum": 1046.4, "have": 966.2}
    assert app.miss_est(r) == " + ~₪80 על 3 שחסרים", app.miss_est(r)
    assert app.miss_est({"miss": 1, "sum": 30, "have": 20}) == " + ~₪10 על מוצר אחד שחסר"
    assert app.miss_est({"miss": 0, "sum": 5, "have": 5}) == ""
    for lat, lng in ((31.8948, 34.8113), (32.0809, 34.7806)):
        u = {"step": "done", "prefs": {"home": [lat, lng], "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "hist": [],
             "items": [dict(x) for x in BBQ_ITEMS]}
        r = app.compute(u)
        for blk in [r["main"]] + r["others"].split("\n\n"):
            if "חסר בסניף" in blk: assert "שחסר" in blk, blk[:300]

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

# ---------- סיכום ----------
ok = sum(1 for r in RESULTS if r[0])
for passed, name, err in RESULTS:
    print(("✅ " if passed else "❌ ") + name)
    if not passed: print("   " + err.replace("\n", "\n   "))
print(f"\n{ok}/{len(RESULTS)} בדיקות עברו")
sys.exit(0 if ok == len(RESULTS) else 1)
