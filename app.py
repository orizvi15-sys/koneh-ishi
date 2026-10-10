"""
קונה אישי – בוט ווטסאפ/טלגרם להשוואת מחירי סופרמרקטים.
Python 3 בלבד, בלי ספריות חיצוניות.

משתני סביבה:
  WHATSAPP_TOKEN     – ה-access token מ-Meta
  PHONE_NUMBER_ID    – ה-Phone number ID מ-Meta
  VERIFY_TOKEN       – מילה סודית שתבחר, ותזין גם ב-Meta בהגדרת ה-Webhook
  TELEGRAM_TOKEN     – ה-token מ-@BotFather (לבוט בטלגרם)
  ANTHROPIC_API_KEY  – (לא חובה) לקריאת קבלות ולהתאמת מוצרים חכמה
  CLAUDE_MODEL       – (לא חובה) ברירת מחדל claude-sonnet-5-5
  PORT               – Render קובע אותו לבד
"""
import json, math, os, re, sys, threading, time, base64, urllib.request, urllib.error
try: sys.stdout.reconfigure(line_buffering=True); sys.stderr.reconfigure(line_buffering=True)
except Exception: pass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
PHONE_ID = os.environ.get("PHONE_NUMBER_ID", "")
VERIFY = os.environ.get("VERIFY_TOKEN", "koneh-ishi")
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
GRAPH = "https://graph.facebook.com/v21.0"
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG = f"https://api.telegram.org/bot{TG_TOKEN}"
PUBLIC_URL = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")
DRY_RUN = os.environ.get("DRY_RUN") == "1"  # לבדיקות: מדפיס במקום לשלוח

# ---------- נתונים ----------
import kdata  # הנתונים הארציים – kdata.D הוא תמיד הגרסה העדכנית
FUEL, KM_PER_L, ROAD, CAR_KMH, WALK_KMH, PARK_MIN = 7.77, 13, 1.3, 28, 4.5, 3
MAX_POOL = 25  # כמה סניפים קרובים לכל היותר נבדקים בכל חישוב
WEEKLY = [["חלב 3% בקרטון 2 ליטר", 2], ["גבינת קוטג 5% 250 גרם", 2], ["לחם אחיד פרוס 900 גר פיקוח אנג`ל", 2],
          ["גבינה צהובה נעם דק דק 22% 200 גר", 1], ["שמן עץ הזית קנולה 1 ליטר בריאות מהטבע", 1], ["אורז בסמטי קלאסי 1 קג סוגת", 1],
          ["פסטה ספגטי מס 8 אסם 500ג", 2], ["טחינה אמיתית 400 גרם צבר", 1], ["במבה חטיף בוטנים 200 גרם", 1], ["פפסי קולה 1.5 ליטר.", 2],
          ["עוף טוב שניצלונים 600 גרם", 1], ['עגבנייה (לק"ג)', 1.5], ['מלפפון (לק"ג)', 1.5], ['בצל יבש (לק"ג)', 1],
          ['תפוח אדמה לבן (לק"ג)', 2], ['פלפל אדום (לק"ג)', 0.5], ['גזר (לק"ג)', 1], ['בננה (לק"ג)', 1], ['תפוח עץ סמיט (לק"ג)', 1]]
HH = {"hh12": 1, "hh34": 1.6, "hh5": 2.2}

# ---------- משתמשים (קובץ JSON פשוט) ----------
# הזיכרון נשמר ב-repo פרטי ב-GitHub (USERS_REPO + USERS_TOKEN), כדי שלא יימחק בכל עדכון של השרת.
# בלי המשתנים האלה – נשמר רק בקובץ מקומי (נמחק בעדכון).
USERS_PATH = os.path.join(HERE, "data", "users.json")
USERS_REPO = os.environ.get("USERS_REPO", "").strip().strip("/")
USERS_TOKEN = os.environ.get("USERS_TOKEN", "").strip()
_lock = threading.Lock()
_remote = {"sha": None, "ok": False}
_dirty = threading.Event()

def gh_users(method, body=None):
    req = urllib.request.Request(os.environ.get("GITHUB_API", "https://api.github.com") + f"/repos/{USERS_REPO}/contents/users.json", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {USERS_TOKEN}", "Accept": "application/vnd.github+json",
                                          "User-Agent": "koneh-ishi-bot", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))

def load_users():
    if USERS_REPO and USERS_TOKEN:
        for attempt in range(3):
            try:
                r = gh_users("GET"); _remote.update(sha=r["sha"], ok=True)
                data = json.loads(base64.b64decode(r["content"]).decode("utf-8") or "{}")
                print(f"זיכרון משתמשים נטען מ-GitHub: {len(data)} משתמשים", flush=True); return data
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    _remote.update(ok=True); print("אין עדיין users.json ב-repo – מתחילים ריק", flush=True); return {}
                print("טעינת זיכרון נכשלה:", e.code, e.read()[:200], flush=True)
                if e.code in (401, 403): break
            except Exception as e:
                print("טעינת זיכרון נכשלה:", e, flush=True)
            time.sleep(3)
        print("⚠️ אין גישה לזיכרון ב-GitHub – לא אכתוב לשם כדי לא למחוק מידע", flush=True)
    else:
        print("⚠️ USERS_REPO/USERS_TOKEN לא מוגדרים – הזיכרון יימחק בכל עדכון של השרת", flush=True)
    try: return json.load(open(USERS_PATH, encoding="utf-8"))
    except Exception: return {}

def push_users():
    if not _remote["ok"]: return
    with _lock: content = json.dumps(USERS, ensure_ascii=False, separators=(",", ":"))
    body = {"message": "update users", "content": base64.b64encode(content.encode()).decode()}
    for attempt in range(3):
        if _remote["sha"]: body["sha"] = _remote["sha"]
        try:
            _remote["sha"] = gh_users("PUT", body)["content"]["sha"]; return
        except urllib.error.HTTPError as e:
            if e.code in (409, 422):  # sha לא עדכני – מביאים את האחרון ומנסים שוב
                try: _remote["sha"] = gh_users("GET")["sha"]
                except Exception: pass
            else:
                print("שמירת זיכרון נכשלה:", e.code, e.read()[:200], flush=True); time.sleep(5)
        except Exception as e:
            print("שמירת זיכרון נכשלה:", e, flush=True); time.sleep(5)
    _dirty.set()

def _users_writer():
    while True:
        _dirty.wait(); time.sleep(10); _dirty.clear(); push_users()

USERS = load_users()
threading.Thread(target=_users_writer, daemon=True).start()

def save_users():
    with _lock:
        tmp = USERS_PATH + ".tmp"
        json.dump(USERS, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, USERS_PATH)
    _dirty.set()

def user(wa_id):
    return USERS.setdefault(wa_id, {"step": "new", "prefs": {}, "hist": []})

# ---------- טלגרם ----------
def tg_call(method, payload):
    if DRY_RUN:
        print("TG", method, json.dumps(payload, ensure_ascii=False)[:600]); return {}
    req = urllib.request.Request(f"{TG}/{method}", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        print("Telegram error", e.code, e.read()[:500]); return {}
    except Exception as e:
        print("Telegram error", e); return {}

def tg_html(text):  # *bold* / _italic_ בסגנון ווטסאפ → HTML של טלגרם
    s = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    s = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", s)
    return re.sub(r"(?<![\w])_([^_\n]+)_(?![\w])", r"<i>\1</i>", s)

def is_tg(to): return str(to).startswith("tg:")

# ---------- שליחת הודעות ----------
def graph_post(path, payload):
    if DRY_RUN:
        print("SEND", json.dumps(payload, ensure_ascii=False)[:600]); return {}
    req = urllib.request.Request(f"{GRAPH}/{path}", data=json.dumps(payload).encode(),
                                 headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=20))
    except urllib.error.HTTPError as e:
        print("Graph error", e.code, e.read()[:500]); return {}

def send_text(to, text):
    if is_tg(to):
        return tg_call("sendMessage", {"chat_id": to[3:], "text": tg_html(text[:4000]), "parse_mode": "HTML", "disable_web_page_preview": True})
    graph_post(f"{PHONE_ID}/messages", {"messaging_product": "whatsapp", "to": to, "type": "text",
                                         "text": {"body": text[:4000], "preview_url": True}})

def send_buttons(to, text, buttons):  # buttons: [(id, title<=20)] up to 3
    if is_tg(to):
        return tg_call("sendMessage", {"chat_id": to[3:], "text": tg_html(text), "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": [[{"text": tt, "callback_data": i}] for i, tt in buttons]}})
    graph_post(f"{PHONE_ID}/messages", {"messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {"type": "button", "body": {"text": text[:1024]},
                        "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in buttons]}}})

def send_location_request(to, text):
    if is_tg(to):
        return tg_call("sendMessage", {"chat_id": to[3:], "text": tg_html(text), "parse_mode": "HTML",
                        "reply_markup": {"keyboard": [[{"text": "📍 שליחת המיקום שלי", "request_location": True}]],
                                         "resize_keyboard": True, "one_time_keyboard": True}})
    graph_post(f"{PHONE_ID}/messages", {"messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {"type": "location_request_message", "body": {"text": text},
                        "action": {"name": "send_location"}}})

def download_media(media_id):
    if media_id.startswith("tg:"):
        fp = tg_call("getFile", {"file_id": media_id[3:]})["result"]["file_path"]
        return urllib.request.urlopen(f"https://api.telegram.org/file/bot{TG_TOKEN}/{fp}", timeout=30).read(), "image/jpeg"
    meta = json.load(urllib.request.urlopen(urllib.request.Request(f"{GRAPH}/{media_id}",
                     headers={"Authorization": f"Bearer {TOKEN}"}), timeout=20))
    data = urllib.request.urlopen(urllib.request.Request(meta["url"], headers={"Authorization": f"Bearer {TOKEN}"}), timeout=30).read()
    return data, meta.get("mime_type", "image/jpeg")

# ---------- Claude (לא חובה) ----------
def claude_json(prompt, image=None, mime="image/jpeg"):
    if not CLAUDE_KEY:
        return None
    content = [{"type": "text", "text": prompt}]
    if image:
        content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(image).decode()}})
    body = {"model": CLAUDE_MODEL, "max_tokens": 2000, "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        txt = json.load(urllib.request.urlopen(req, timeout=90))["content"][0]["text"]
        m = re.search(r"[\[{].*[\]}]", txt, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception as e:
        print("Claude error", e); return None

# ---------- התאמת מוצרים ----------
def item_of(d, i, qty, src=None):
    c, n = d.items[i][0], d.items[i][1]
    return {"src": src or n, "code": c, "name": n, "qty": qty}

def match_items(items):
    d = kdata.D
    cand = [d.search(it["name"]) for it in items]
    picks = [c[0] if c else None for c in cand]
    if CLAUDE_KEY and any(len(c) > 1 for c in cand):
        res = claude_json("Match each Israeli supermarket item to the option that is the SAME product (type, brand if given, similar size). "
                          "If none fits, null. Reply only a JSON array like [{\"i\":0,\"pick\":2}].\n\n" +
                          json.dumps([{"i": i, "item": it["name"], "options": [d.items[k][1] for k in cand[i]]} for i, it in enumerate(items)], ensure_ascii=False))
        if isinstance(res, list):
            for r in res:
                try:
                    i = int(r["i"]); k = r.get("pick")
                    picks[i] = None if k is None or not cand[i] or int(k) >= len(cand[i]) else cand[i][int(k)]
                except Exception: pass
    found = [item_of(d, picks[i], it["qty"], it["name"]) for i, it in enumerate(items) if picks[i] is not None]
    missed = [it["name"] for i, it in enumerate(items) if picks[i] is None]
    return found, missed

def resolve(d, it):
    """אינדקס המוצר בגרסת הנתונים הנוכחית (לפי ברקוד, ואם אין – לפי שם)"""
    i = d.code2i.get(it.get("code", ""))
    if i is None: i = d.name2i.get(it.get("name", ""))
    return i

_weekly = {}
def weekly_items(f):
    d = kdata.D
    if _weekly.get("v") != d.version:
        L = []
        for q, n in WEEKLY:
            i = d.name2i.get(q)
            if i is None:
                c = d.search(q, 1); i = c[0] if c else None
            if i is not None: L.append((i, n))
        _weekly.clear(); _weekly.update(v=d.version, L=L)
    return [item_of(d, i, max(.5, round(q * f * 2) / 2) if d.items[i][0].startswith("P") or "לק\"ג" in d.items[i][1] else max(1, round(q * f)))
            for i, q in _weekly["L"]]

def parse_list(text):
    out = []
    for s in re.split(r"[\n,،]+", text):
        s = s.strip()
        if not s: continue
        m = re.match(r'^(\d+(?:\.\d+)?)\s*(ק"ג|קג|קילו)?\s+(.+)$', s) or re.match(r'^(.+?)\s+(\d+(?:\.\d+)?)\s*(ק"ג|קג|קילו)?$', s)
        if not m: out.append({"name": s, "qty": 1})
        elif m.group(1)[0].isdigit(): out.append({"name": m.group(3), "qty": float(m.group(1))})
        else: out.append({"name": m.group(1), "qty": float(m.group(2))})
    return out

# ---------- הרגלים ----------
def learned(u):
    h = u["hist"]
    if not h: return []
    d = kdata.D; cnt = {}
    for b in h:
        for it in b["items"]:
            i = resolve(d, it)
            if i is not None:
                c = cnt.setdefault(i, [0, 0]); c[0] += 1; c[1] += it["qty"]
    mn = 1 if len(h) == 1 else max(2, math.ceil(len(h) * 0.4))
    L = [dict(item_of(d, i, round(v[1] / v[0] * 2) / 2 or 1), freq=v[0]) for i, v in cnt.items() if v[0] >= mn]
    return sorted(L, key=lambda x: -x["freq"])

# ---------- חישוב ----------
def km(a, b):
    r = math.radians; dla, dlo = r(b[0] - a[0]), r(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(dlo / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h)) * ROAD

def compute(u):
    D = kdata.D
    pr = u["prefs"]; home = pr["home"]; car = pr["mode"] == "car"
    cost = lambda d: d * FUEL / KM_PER_L if car else 0
    mins = lambda d: d / CAR_KMH * 60 + PARK_MIN if car else d / WALK_KMH * 60
    items, gone = [], []
    for it in u["items"]:
        i = resolve(D, it)
        if i is None: gone.append(it["name"])
        else: items.append(dict(it, i=i))
    if not items:
        return ["לא מצאתי אף אחד מהמוצרים במאגר המחירים העדכני 🙁 נסה לכתוב אותם שוב."]
    near = sorted(((km(home, s["p"]), s) for s in D.stores), key=lambda x: x[0])
    if not near: return ["אין לי עדיין נתוני סניפים 🙁"]
    rows = []
    for k, (d1, s) in enumerate(near[:MAX_POOL * 2]):
        if k > 0 and mins(d1) > pr["maxMin"] and len(rows) >= 1: break
        rows.append({"s": s, "d": 2 * d1, "min": mins(d1), "P": D.prices(s["blob"])})
        if len(rows) >= MAX_POOL: break
    # חציון מקומי למוצרים שחסרים בסניף מסוים
    med = {}
    for it in items:
        v = sorted(r["P"][it["i"]] for r in rows if it["i"] in r["P"])
        med[it["i"]] = v[len(v) // 2] if v else D.items[it["i"]][3] / 100
    for r in rows:
        tot = 0; miss = 0
        for it in items:
            p = r["P"].get(it["i"])
            if p is None: miss += 1; p = med[it["i"]]
            tot += p * it["qty"]
        r.update(sum=tot, fuel=cost(r["d"]), tot=tot + cost(r["d"]), miss=miss)
    nearest = rows[0]
    pool = [r for r in rows if r["min"] <= pr["maxMin"]] or [nearest]
    # סניף שחסרים בו הרבה מהמוצרים לא באמת עונה על הסל
    full = [r for r in pool if r["miss"] <= max(1, 0.2 * len(items))]
    if full: pool = full
    ranked = sorted(pool, key=lambda r: r["tot"]); best = ranked[0]
    # לא שולחים רחוק בשביל חיסכון זניח: אם סניף קרוב יותר כמעט באותו מחיר – ממליצים עליו
    close = [r for r in ranked if r["tot"] - best["tot"] < max(5, 0.03 * best["sum"]) and r["min"] <= best["min"] - 3]
    if close:
        best = min(close, key=lambda r: r["d"]); ranked.remove(best); ranked.insert(0, best)
    split = None
    if pr["splitMin"] >= 0:
        for a in range(len(pool)):
            for b in range(a + 1, len(pool)):
                A, B = pool[a], pool[b]; tot = 0; inA, inB = [], []; ok = True
                for it in items:
                    pa, pb = A["P"].get(it["i"]), B["P"].get(it["i"])
                    if pa is None and pb is None: ok = False; break
                    if pb is None or (pa is not None and pa <= pb): tot += pa * it["qty"]; inA.append(it["name"])
                    else: tot += pb * it["qty"]; inB.append(it["name"])
                if not ok or not inA or not inB: continue
                d = km(home, A["s"]["p"]) + km(A["s"]["p"], B["s"]["p"]) + km(B["s"]["p"], home)
                t = tot + cost(d)
                if not split or t < split["tot"]: split = {"A": A, "B": B, "tot": t, "inA": inA, "inB": inB}
    s = best["s"]; lines = [f"🏆 *הכי משתלם בשבילך:*\n*{s['n']}, {s['a']}*", f"הסל יעלה *₪{best['tot']:.0f}*" + (f" (כולל ₪{best['fuel']:.0f} דלק)" if car and best["fuel"] >= 1 else "")]
    if best["miss"]: lines.append(f"_{best['miss']} מוצרים לא נמצאו בסניף, וחושבו לפי מחיר ממוצע_")
    if gone: lines.append(f"_לא נמצאו היום במאגר: {', '.join(gone[:5])}_")
    if nearest["s"]["key"] != s["key"] and nearest["tot"] - best["tot"] > 1:
        lines.append(f"חוסך *₪{nearest['tot'] - best['tot']:.0f}* לעומת הסופר הכי קרוב אליך ({nearest['s']['n']})")
    lines.append(f"📍 {round(best['min'])} דקות {'נסיעה' if car else 'הליכה'}")
    lines.append(f"🗺️ ניווט: https://waze.com/ul?ll={s['p'][0]},{s['p'][1]}&navigate=yes")
    msgs = ["\n".join(lines)]
    if split and best["tot"] - split["tot"] > max(pr["splitMin"], 5):
        A, B = split["A"]["s"], split["B"]["s"]
        msgs.append(f"💡 אם מתחלקים בין *{A['n']}* ({A['a']}) ל*{B['n']}* ({B['a']}) חוסכים עוד *₪{best['tot'] - split['tot']:.0f}*.\n\n"
                    f"*ב{A['n']}:* {', '.join(split['inA'][:15])}\n\n*ב{B['n']}:* {', '.join(split['inB'][:15])}")
    msgs.append("כל הסופרים בטווח:\n" + "\n".join(f"{'✅' if k == 0 else '▫️'} {r['s']['n']} · {r['s']['a']} – ₪{r['tot']:.0f} ({round(r['min'])} דק')" for k, r in enumerate(ranked[:8]))
                + (f"\n\n_מחירים ומבצעים מעודכנים ל-{D.built[8:10]}/{D.built[5:7]} {D.built[11:16]}_" if len(D.built) >= 16 else ""))
    return msgs

# ---------- שיחה ----------
WELCOME = "*קונה אישי 🛒*\nהיי! אני אעזור לך לחסוך בקניות בסופר.\n4 שאלות קצרות ומתחילים 👇"

def ask(to, step, u):
    u["step"] = step
    if step == "loc":
        send_location_request(to, "*1️⃣ מאיפה יוצאים לקניות?*\nלוחצים על הכפתור ושולחים מיקום 📍")
    elif step == "mode":
        send_buttons(to, "*2️⃣ איך מגיעים לסופר?*", [("car", "🚗 ברכב"), ("walk", "🚶 ברגל / אוטובוס")])
    elif step == "range":
        send_buttons(to, "*3️⃣ כמה רחוק שווה לך לנסוע כדי לחסוך?*", [("r5", "עד 5 דקות"), ("r15", "עד 15 דקות"), ("rall", "לא משנה")])
    elif step == "split":
        send_buttons(to, "*4️⃣ מוכן לקנות בשני סופרים אם חוסכים?*", [("s0", "כן"), ("s30", "רק אם מעל ₪30"), ("sno", "לא, סופר אחד")])
    elif step == "basket":
        L = learned(u)
        if L:
            send_buttons(to, f"*ומה קונים הפעם?*\nלמדתי מה אתה קונה בדרך כלל 📈", [("mine", f"🛒 הסל הקבוע שלי"), ("cam", "📷 צילום קבלה"), ("list", "✍️ אכתוב רשימה")])
        else:
            send_buttons(to, "*ומה קונים?*", [("cam", "📷 צילום קבלה"), ("list", "✍️ אכתוב רשימה"), ("weekly", "🛒 סל שבועי רגיל")])
    save_users()

def finish(to, u):
    for m in compute(u): send_text(to, m)
    u["hist"] = (u["hist"] + [{"d": int(time.time()), "items": [{"code": x.get("code", ""), "name": x["name"], "qty": x["qty"]} for x in u["items"]]}])[-12:]
    u["step"] = "done"; save_users()
    send_text(to, "שמרתי את הקנייה כדי ללמוד את ההרגלים שלך 🙂\nמתי שרוצים – שולחים קבלה או רשימה ואבדוק שוב. לשינוי הגדרות כתוב *הגדרות*.")

def got_items(to, u, items):
    found, missed = match_items(items)
    msg = f"מצאתי מחירים ל-*{len(found)}* מתוך {len(items)} מוצרים."
    if missed: msg += "\nלא נמצאו: " + ", ".join(missed[:8])
    send_text(to, msg)
    if not found: return
    have = {x["code"] for x in found}
    forgot = [x for x in learned(u) if x["code"] not in have and x["freq"] >= 2][:5]
    u["items"] = found
    if forgot:
        u["pending"] = forgot; u["step"] = "forgot"; save_users()
        send_buttons(to, "🤔 בדרך כלל אתה קונה גם:\n" + "\n".join("• " + x["name"] for x in forgot) + "\nלהוסיף לסל?", [("addf", "כן, להוסיף"), ("noadd", "לא, תודה")])
        return
    finish(to, u)

def handle(msg):
    to = msg["from"]; u = user(to); t = msg.get("type"); pr = u["prefs"]
    reply = None
    if t == "interactive":
        it = msg["interactive"]; reply = (it.get("button_reply") or it.get("list_reply") or {}).get("id")
    text = msg.get("text", {}).get("body", "").strip() if t == "text" else ""
    print("handle", t, "step", u["step"])

    if text == "מחק אותי":
        USERS.pop(to, None); save_users()
        return send_text(to, "מחקתי את כל המידע שלך 🗑️ כדי להתחיל מחדש כתבו שלום.")
    if text in ("הגדרות", "התחל", "מחדש", "/start") or u["step"] == "new":
        if u["step"] == "new" and pr.get("home"):
            u["step"] = "keep"; save_users()
            return send_buttons(to, WELCOME + "\n\nיש לי את ההעדפות שלך מהפעם הקודמת.", [("keep", "להמשיך איתן"), ("reset", "להתחיל מחדש")])
        send_text(to, WELCOME); return ask(to, "loc", u)
    if reply == "keep": return ask(to, "basket", u)
    if reply == "reset": u["prefs"] = {}; return ask(to, "loc", u)

    if t == "location":
        pr["home"] = [msg["location"]["latitude"], msg["location"]["longitude"]]
        if u["step"] == "loc": return ask(to, "mode", u)
        send_text(to, "עדכנתי את המיקום 👍"); save_users()
        if u.get("items"): return finish(to, u)
        return
    if u["step"] == "loc":
        return send_location_request(to, "כדי לדעת מאיפה אתה יוצא, צריך מיקום 📍 לוחצים על הכפתור:")
    if reply in ("car", "walk"): pr["mode"] = reply; return ask(to, "range", u)
    if reply in ("r5", "r15", "rall"): pr["maxMin"] = {"r5": 6, "r15": 16, "rall": 999}[reply]; return ask(to, "split", u)
    if reply in ("s0", "s30", "sno"): pr["splitMin"] = {"s0": 0, "s30": 30, "sno": -1}[reply]; return ask(to, "basket", u)
    if reply == "mine":
        L = learned(u); u["items"] = [{k: x[k] for k in ("src", "code", "name", "qty")} for x in L]
        send_text(to, "*הסל הקבוע שלך:*\n" + "\n".join(f"• {x['name']}" + (f" ×{x['qty']:g}" if x["qty"] != 1 else "") for x in L[:15]))
        return finish(to, u)
    if reply == "cam":
        u["step"] = "basket"; save_users(); return send_text(to, "צלם את הקבלה (או רשימה כתובה) ושלח כאן 📷")
    if reply == "list":
        u["step"] = "basket"; save_users(); return send_text(to, "כתוב מה קונים, למשל:\n_2 חלב, לחם, קוטג', 1.5 קג עגבניות_")
    if reply == "weekly":
        u["step"] = "hh"; save_users(); return send_buttons(to, "*לכמה אנשים?*", [("hh12", "1–2"), ("hh34", "3–4"), ("hh5", "5+")])
    if reply in HH:
        f = HH[reply]
        u["items"] = weekly_items(f)
        send_text(to, f"הכנתי סל שבועי רגיל: {len(u['items'])} מוצרים (חלב, לחם, גבינות, ירקות, פירות ועוד)."); return finish(to, u)
    if reply in ("addf", "noadd") and u["step"] == "forgot":
        if reply == "addf": u["items"] += [{k: x.get(k, "") for k in ("src", "code", "name", "qty")} for x in u.pop("pending", [])]
        return finish(to, u)

    if t == "image":
        if not CLAUDE_KEY:
            return send_text(to, "קריאת קבלות עוד לא מופעלת. אפשר לכתוב את הרשימה בהודעה ✍️")
        send_text(to, "קורא את הקבלה… ⏳")
        img, mime = download_media(msg["image"]["id"])
        items = claude_json('This is a photo of an Israeli supermarket receipt (or a shopping list). Extract purchased products only; skip totals, discounts (הנחה), '
                            'deposits (פיקדון), bags (שקית), payment and VAT lines. Reply with only a JSON array like [{"name":"חלב 3% 1 ליטר","qty":2}] — '
                            '"name" in Hebrew as printed, "qty" = units, or kilograms for weighed produce.', img, mime)
        if not isinstance(items, list) or not items:
            return send_text(to, "לא הצלחתי לקרוא את הקבלה. נסה תמונה חדה יותר, או כתוב רשימה.")
        if not pr.get("home"): u["step"] = "loc"
        return got_items(to, u, [{"name": str(x.get("name", "")), "qty": float(x.get("qty") or 1)} for x in items if x.get("name")])
    if text:
        if not all(k in pr for k in ("home", "mode", "maxMin", "splitMin")):
            return send_text(to, "בוא נסיים קודם את השאלות 🙂 אפשר ללחוץ על הכפתורים למעלה.")
        return got_items(to, u, parse_list(text))
    send_text(to, "לא הבנתי 🙂 אפשר לשלוח רשימה, קבלה, או לכתוב *הגדרות*.")


def tg_to_msg(up):
    """ממיר עדכון מטלגרם להודעה בפורמט של ווטסאפ, כדי שאותה לוגיקה תעבוד לשניהם"""
    if "callback_query" in up:
        cq = up["callback_query"]; tg_call("answerCallbackQuery", {"callback_query_id": cq["id"]})
        return {"from": f"tg:{cq['message']['chat']['id']}", "id": "tgc" + cq["id"], "type": "interactive",
                "interactive": {"button_reply": {"id": cq.get("data", "")}}}
    m = up.get("message") or {}
    if not m: return None
    base = {"from": f"tg:{m['chat']['id']}", "id": f"tg{m['chat']['id']}_{m['message_id']}"}
    if "location" in m:
        return {**base, "type": "location", "location": {"latitude": m["location"]["latitude"], "longitude": m["location"]["longitude"]}}
    if "photo" in m:
        return {**base, "type": "image", "image": {"id": "tg:" + m["photo"][-1]["file_id"]}}
    if "text" in m:
        return {**base, "type": "text", "text": {"body": m["text"]}}
    return {**base, "type": "unknown"}

# ---------- שרת ----------
PRIVACY = """<!doctype html><html dir="rtl" lang="he"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>קונה אישי – מדיניות פרטיות</title><body style="font-family:sans-serif;max-width:640px;margin:2em auto;padding:0 16px;line-height:1.6">
<h1>קונה אישי – מדיניות פרטיות</h1>
<p>קונה אישי הוא בוט שמשווה מחירי סופרמרקטים. כדי לעשות זאת הוא שומר: מספר הטלפון/מזהה הצ'אט, המיקום ששלחת, ההעדפות שבחרת ורשימות הקניות שלך – רק לצורך מתן ההמלצה ולמידת הרגלי הקנייה שלך.</p>
<p>תמונות קבלות נשלחות לעיבוד אוטומטי (Anthropic Claude API) לצורך זיהוי המוצרים בלבד, ואינן נשמרות.</p>
<p>המידע לא נמכר ולא מועבר לצד שלישי לשום מטרה אחרת. למחיקת כל המידע שלך: כתבו "מחק אותי" לבוט או פנו אלינו בבוט.</p>
</body></html>"""
SEEN = set()
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        p = urlparse(self.path).path
        if p == "/webhook" and q.get("hub.mode") == ["subscribe"] and q.get("hub.verify_token") == [VERIFY]:
            print("WA webhook verified"); body = q["hub.challenge"][0].encode(); self.send_response(200)
        elif p == "/privacy":
            body = PRIVACY.encode(); self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body); return
        else:
            d = kdata.D
            info = f"\nנתונים: {len(d.stores)} סניפים, {len(d.items)} מוצרים, עודכן {d.built or d.version} ({d.source})" if d else ""
            body = ("קונה אישי פועל ✅" + info).encode(); self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); raw = self.rfile.read(n)
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        if urlparse(self.path).path == "/telegram":
            try:
                m = tg_to_msg(json.loads(raw))
                if m and m["id"] not in SEEN:
                    SEEN.add(m["id"]); threading.Thread(target=safe_handle, args=(m,), daemon=True).start()
            except Exception as ex:
                print("bad telegram payload", ex)
            return
        try:
            data = json.loads(raw)
            for e in data.get("entry", []):
                for c in e.get("changes", []):
                    v = c.get("value", {})
                    print("WA in:", c.get("field"), "msgs:", len(v.get("messages", [])),
                          "statuses:", [(s.get("status"), s.get("errors")) for s in v.get("statuses", [])])
                    for m in c.get("value", {}).get("messages", []):
                        if m.get("id") in SEEN: continue
                        SEEN.add(m.get("id"))
                        threading.Thread(target=safe_handle, args=(m,), daemon=True).start()
        except Exception as ex:
            print("bad payload", ex)
    def log_message(self, *a): pass

def safe_handle(m):
    try: handle(m)
    except Exception as ex:
        import traceback; traceback.print_exc()
        try: send_text(m["from"], "אופס, משהו השתבש אצלי 🙈 נסה שוב בעוד רגע.")
        except Exception: pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    kdata.load(force_download=os.environ.get("DATA_URL", "x") != ""); kdata.start_refresher()
    import signal
    def _bye(*_):  # Render שולח SIGTERM לפני עדכון – שומרים את הזיכרון לפני שיוצאים
        if _dirty.is_set(): push_users()
        os._exit(0)
    signal.signal(signal.SIGTERM, _bye)
    print("קונה אישי מאזין בפורט", port)
    if TG_TOKEN and PUBLIC_URL:
        try:
            print("חיבור טלגרם:", tg_call("setWebhook", {"url": PUBLIC_URL.rstrip("/") + "/telegram", "drop_pending_updates": True}))
        except Exception as ex:
            print("חיבור טלגרם נכשל:", ex)
    elif not TG_TOKEN:
        print("אזהרה: TELEGRAM_TOKEN לא מוגדר")
    WABA_ID = os.environ.get("WABA_ID", "1510874674181761")
    if TOKEN and WABA_ID:
        for method, path in (("POST", f"/{WABA_ID}/subscribed_apps"), ("GET", f"/{WABA_ID}/subscribed_apps"),
                             ("GET", f"/{PHONE_ID}?fields=display_phone_number,status,code_verification_status,platform_type")):
            try:
                rq = urllib.request.Request(GRAPH + path, data=b"" if method == "POST" else None, method=method,
                                            headers={"Authorization": "Bearer " + TOKEN})
                print("WA setup", method, path.split("?")[0], urllib.request.urlopen(rq, timeout=20).read()[:400])
            except urllib.error.HTTPError as e:
                print("WA setup error", method, path.split("?")[0], e.code, e.read()[:400])
            except Exception as e:
                print("WA setup error", method, path, e)
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
