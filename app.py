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

CAR_MAX_MIN = 35   # ברכב: סניפים עד ~35 דקות נסיעה (תמיד כולל הקרוב ביותר)

def car_min(d):
    """זמן נסיעה משוער ברכב: 5 הק"מ הראשונים בעיר (28 קמ"ש), עד 15 ק"מ כביש בינעירוני (55), מעבר לזה כביש מהיר (75)"""
    t = min(d, 5) / CAR_KMH + max(0, min(d, 15) - 5) / 55 + max(0, d - 15) / 75
    return t * 60 + PARK_MIN
MAX_POOL = 25
STAY_MIN = 35           # פחות מזה – לא שווה לעזוב את הסופר הקבוע
STAY_MIN_WEIGHED = 25   # כשמשווים רק מוצרים במשקל (קילו בשר, סלמון...) – סף נמוך יותר
MAX_MISSING = 4         # סניף שחסרים בו 4 מוצרים ומעלה – לא מוצג
SPLIT_MIN = 40          # מציעים לפצל בין שני סופרים רק אם חוסכים יותר מ-₪40
BUS_KMH, BUS_WAIT = 15, 10  # כמה סניפים קרובים לכל היותר נבדקים בכל חישוב
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

M_NEW, M_CAM, M_SET = "🛒 קנייה חדשה", "📷 צילום קבלה", "⚙️ הגדרות"
# מקלדת קבועה אחת לכל השיחה – כולל כפתור מיקום (שימושי גם לעדכון מיקום בכל רגע)
MENU = {"keyboard": [[{"text": "📍 שליחת המיקום שלי", "request_location": True}], [{"text": M_NEW}, {"text": M_CAM}], [{"text": M_SET}]],
        "resize_keyboard": True, "is_persistent": True}

def send_text(to, text, menu=False):
    if is_tg(to):
        body = {"chat_id": to[3:], "text": tg_html(text[:4000]), "parse_mode": "HTML", "disable_web_page_preview": True}
        if menu: body["reply_markup"] = MENU   # כפתורים קבועים בתחתית המסך
        return tg_call("sendMessage", body)
    graph_post(f"{PHONE_ID}/messages", {"messaging_product": "whatsapp", "to": to, "type": "text",
                                         "text": {"body": text[:4000], "preview_url": True}})

def typing(to):
    if is_tg(to): tg_call("sendChatAction", {"chat_id": to[3:], "action": "typing"})

def tg_btn(i, t):
    return {"text": t, "url": i} if i.startswith("http") else {"text": t, "callback_data": i}

def send_buttons(to, text, buttons):  # buttons: [(id, title<=20)] up to 3
    if is_tg(to):
        return tg_call("sendMessage", {"chat_id": to[3:], "text": tg_html(text), "parse_mode": "HTML",
                        "reply_markup": {"inline_keyboard": [[tg_btn(i, tt) for i, tt in buttons[k:k + 2]]
                                                             for k in range(0, len(buttons), 2)] if len(buttons) > 3 else
                                                            [[tg_btn(i, tt)] for i, tt in buttons]}})
    graph_post(f"{PHONE_ID}/messages", {"messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {"type": "button", "body": {"text": text[:1024]},
                        "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t[:20]}} for i, t in [b for b in buttons if not b[0].startswith("http")][:3]]}}})

def send_location_request(to, text):
    if is_tg(to):
        return tg_call("sendMessage", {"chat_id": to[3:], "text": tg_html(text), "parse_mode": "HTML",
                        "reply_markup": MENU})
    graph_post(f"{PHONE_ID}/messages", {"messaging_product": "whatsapp", "to": to, "type": "interactive",
        "interactive": {"type": "location_request_message", "body": {"text": text},
                        "action": {"name": "send_location"}}})

def sniff_mime(b):
    if b[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "image/webp"
    if b[:3] == b"GIF": return "image/gif"
    return "unsupported"

def download_media(media_id):
    if media_id.startswith("tg:"):
        fp = tg_call("getFile", {"file_id": media_id[3:]})["result"]["file_path"]
        data = urllib.request.urlopen(f"https://api.telegram.org/file/bot{TG_TOKEN}/{fp}", timeout=30).read()
        return data, sniff_mime(data)
    meta = json.load(urllib.request.urlopen(urllib.request.Request(f"{GRAPH}/{media_id}",
                     headers={"Authorization": f"Bearer {TOKEN}"}), timeout=20))
    data = urllib.request.urlopen(urllib.request.Request(meta["url"], headers={"Authorization": f"Bearer {TOKEN}"}), timeout=30).read()
    return data, meta.get("mime_type", "image/jpeg")

# ---------- Claude (לא חובה) ----------
NO_TEMP = False

def claude_json(prompt, image=None, mime="image/jpeg", keep_dict=False):
    global NO_TEMP
    if not CLAUDE_KEY:
        return None
    content = [{"type": "text", "text": prompt}]
    if image:
        content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(image).decode()}})
    body = {"model": CLAUDE_MODEL, "max_tokens": 8000, "messages": [{"role": "user", "content": content}]}
    if not NO_TEMP: body["temperature"] = 0           # אותה רשימה → אותה תשובה
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=90))
        txt = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
        if res.get("stop_reason") == "max_tokens": print("Claude: reply cut at max_tokens", flush=True)
    except urllib.error.HTTPError as e:
        err = e.read()[:500]; print("Claude error", e.code, err, flush=True)
        if e.code == 400 and b"temperature" in err:      # מודל שלא מקבל temperature – בלי
            NO_TEMP = True; return claude_json(prompt, image, mime, keep_dict)
        return None
    except Exception as e:
        print("Claude error", e, flush=True); return None
    m = re.search(r"[\[{].*[\]}]", txt, re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except Exception:
        out = None
    if isinstance(out, dict) and not keep_dict:  # לפעמים חוזר {"items": [...]}
        out = next((v for v in out.values() if isinstance(v, list)), out)
    if out is None: print("Claude: no JSON in reply:", txt[:300], flush=True)
    return out

RECEIPT_PROMPT = ('Extract the purchased products from this Israeli supermarket receipt or shopping list. Skip dates, totals (סה"כ), '
                  'discounts (הנחה), deposits (פיקדון), bags (שקית), payment, change and VAT lines, and prices. Reply with only a JSON object like '
                  '{"store":"רמי לוי – סניף מודיעין, עמק איילון 30 מודיעין","items":[{"name":"חלב 3% 1 ליטר","qty":2}]} — "store" = the chain, '
                  'branch name and address/city printed on the receipt (empty string if this is a list, not a receipt); "name" in Hebrew as written, '
                  '"qty" = number of PACKAGES bought (from ×2 / x2 / "2 חבילות" / "2 שקיות" / "3 בקבוקים" of one product), or kilograms for weighed produce if shown; default 1. '
                  'A size or count INSIDE one package is part of the name, NOT qty: "נייר טואלט – 12 גלילים" = {"name":"נייר טואלט 12 גלילים","qty":1}, '
                  '"ביצים – 12 יחידות" = {"name":"ביצים 12 יחידות","qty":1}, "מים מינרליים – שישייה" = qty 1, "קמח – 1 ק\"ג" = {"name":"קמח 1 ק\"ג","qty":1}. '
                  'Be consistent: the same input must always give the same output. '
                  'Photos are often blurry, dark, crooked or crumpled: extract every product line you can read with reasonable confidence, '
                  'skip lines you cannot read, never invent products. Also add "quality": "ok" (everything readable), "partial" (some lines '
                  'unreadable or the receipt is cut off) or "bad" (almost nothing readable), and "issue": one of "dark", "blurry", "cut", '
                  '"far", "glare", "not_receipt" or "" — the main problem, if any.')

def extract_items(image=None, mime="image/jpeg", text=None, with_store=False):
    res = claude_json(RECEIPT_PROMPT + ("\n\n" + text if text else ""), image, mime, keep_dict=True)
    store = ""; meta = {}
    if isinstance(res, dict):
        store = str(res.get("store") or "").strip()
        meta = {"quality": str(res.get("quality") or "ok"), "issue": str(res.get("issue") or "")}
        items = res.get("items") or next((v for v in res.values() if isinstance(v, list)), None)
    else: items = res
    if not isinstance(items, list): return (None, store, meta) if with_store else None
    out = []
    for x in items:
        if not isinstance(x, dict) or not str(x.get("name", "")).strip(): continue
        try: q = float(x.get("qty") or 1)
        except Exception: q = 1
        out.append({"name": str(x["name"]).strip(), "qty": q if 0 < q < 100 else 1})
    return (out, store, meta) if with_store else out


def enhance(img):
    """שיפור קל לתמונת קבלה: סיבוב לפי EXIF, ניגודיות, חדות, והקטנה לגודל שהמודל קורא (אם Pillow מותקן)"""
    try:
        from PIL import Image, ImageOps, ImageFilter
        import io
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(img))).convert("L")
        if max(im.size) > 2000: im.thumbnail((2000, 2000))
        im = ImageOps.autocontrast(im, cutoff=1).filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=3))
        out = io.BytesIO(); im.save(out, "JPEG", quality=90)
        return out.getvalue(), "image/jpeg"
    except Exception as e:
        print("enhance skipped:", e, flush=True)
        return None, None


ISSUE_TIP = {"dark": "התמונה חשוכה 💡 נסו לצלם ליד אור.", "blurry": "התמונה מטושטשת 📷 החזיקו את הטלפון יציב ותנו לו להתמקד.",
             "cut": "הקבלה חתוכה ✂️ ודאו שכולה בתוך התמונה (קבלה ארוכה – אפשר בכמה תמונות).",
             "far": "הקבלה רחוקה מדי 🔍 התקרבו כך שהיא ממלאת את המסך.", "glare": "יש השתקפות על הקבלה ✨ נסו מזווית קצת אחרת, בלי פלאש.",
             "not_receipt": "זה לא נראה כמו קבלה 🤔 שלחו צילום של קבלה מהסופר, או כתבו רשימה."}
MERGE_SEC = 150          # תמונה נוספת תוך 2.5 דקות = המשך של אותה קבלה
_img_locks = {}


# ---------- הסופר הקבוע ----------
def match_store(text, home):
    """מזהה את הסניף מתוך הכיתוב בקבלה (רשת + סניף/כתובת), מבין הסניפים באזור"""
    D = kdata.D
    if not text or not home: return None
    q = {kdata.stem(w) for w in kdata.toks(text)}
    best = None
    for s in D.stores:
        d = km(home, s["p"])
        if d > 60: continue
        ch = {kdata.stem(w) for w in kdata.toks(s["chain"])}
        if not ch or not ch <= q: continue                   # הרשת חייבת להופיע בקבלה
        nm = {kdata.stem(w) for w in kdata.toks(s["n"])} - ch
        rest = nm | {kdata.stem(w) for w in kdata.toks(s["a"])} - ch
        sc = len(rest & q) - 0.5 * len(nm - q)                 # מילה בשם הסניף שלא מופיעה בקבלה (למשל BE) – פחות סביר
        key = (sc, -d)
        if best is None or key > best[0]: best = (key, s)
    return best[1]["key"] if best else None

def usual_store(u):
    """הסניף שהמשתמש קונה בו הכי הרבה (לפי הקבלות)"""
    c = u.get("stores") or {}
    keys = {s["key"] for s in kdata.D.stores}
    c = {k: v for k, v in c.items() if k in keys}
    return max(c, key=c.get) if c else None

# ---------- התאמת מוצרים ----------
def item_of(d, i, qty, src=None):
    c, n = d.items[i][0], d.items[i][1]
    return {"src": src or n, "code": c, "name": n, "qty": qty}

# מוצרים בסיסיים בשם כללי ("בצל", "ביצים") → תמיד הפריט הרגיל והמאוחד (לא בצל אדום / מותג מסוים)
BASIC_ALIAS = {"בצל": "P10", "בצלים": "P10", "בצל יבש": "P10", "תפוח אדמה": "P12", "תפוחי אדמה": "P12", "עגבניה": "P01",
               "עגבנייה": "P01", "עגבניות": "P01", "מלפפון": "P03", "מלפפונים": "P03", "גזר": "P16", "בננה": "P28", "בננות": "P28",
               "ביצים": "E01", "ביצים L": "E01", "ביצים לארג'": "E01", "ביצים M": "E02", "ביצים מדיום": "E02", "לימון": "P39",
               "לימונים": "P39", "פלפל אדום": "P04", "תפוז": "P37", "תפוזים": "P37", "אבוקדו": "P29", "בטטה": "P15", "קישוא": "P18",
               "קישואים": "P18", "חציל": "P17", "חצילים": "P17", "כרובית": "P21", "תפוח עץ": "P30", "תפוחים": "P30", "תפוחי עץ": "P30"}

def basic_alias(name):
    n = re.sub(r'(\d+(\.\d+)?|ק"ג|קג|קילו|גרם|יחידות|יח\'?|תבנית|ארוז(ה|ים)?|טרי(ים|יה|ה)?|רגיל(ה|ים)?|[–\-:,()])', " ", name)
    return BASIC_ALIAS.get(" ".join(n.split()))

def match_items(items):
    d = kdata.D
    cand = [d.search(it["name"]) for it in items]
    for k, it in enumerate(items):
        a = d.code2i.get(basic_alias(it["name"]) or "")
        if a is not None: cand[k] = [a]                 # אין מה לבחור – זה הפריט הרגיל
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
    found = [dict(item_of(d, picks[i], it["qty"], it["name"]), c=[d.items[k][0] for k in cand[i] if k != picks[i]][:4])
             for i, it in enumerate(items) if picks[i] is not None]
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

SKIP_LINE = re.compile(r'סה"?״?כ|סה״כ|לתשלום|מע"?מ|עודף|אשראי|מזומן|קבלה|חשבונית|הנחה|פיקדון|שקית|^\d{1,2}[/.]\d{1,2}[/.]\d{2,4}$')

def parse_list(text):
    out = []
    for s in re.split(r"[\n,،]+", text):
        s = s.strip()
        if not s or SKIP_LINE.search(s): continue
        s = re.sub(r"\s*₪?\s*\d+\.\d{2}\s*₪?$", "", s).strip()          # מחיר בסוף שורה
        mx = re.search(r"\s*[×xX\*]\s*(\d+(?:\.\d+)?)\s*$", s)            # ×2 בסוף
        if mx:
            out.append({"name": s[:mx.start()].strip(), "qty": float(mx.group(1))}); continue
        if not re.search(r"[א-תA-Za-z]", s): continue
        mu = re.match(r'^(חצי|רבע)?\s*(קילו|ק"ג|קג)\s+(.+)$', s)                 # "קילו חזה עוף", "חצי קילו טחון"
        if mu: out.append({"name": mu.group(3), "qty": {"חצי": .5, "רבע": .25}.get(mu.group(1), 1.0)}); continue
        mg = re.match(r'^(\d+)\s*(גרם|גר|ג\')\s+(.+)$', s)                      # "300 גרם פרגיות"
        if mg and int(mg.group(1)) >= 50: out.append({"name": mg.group(3), "qty": int(mg.group(1)) / 1000}); continue
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
                c = cnt.setdefault(i, [0, 0, None]); c[0] += 1; c[1] += it["qty"]; c[2] = it.get("src") or c[2]
    mn = 1 if len(h) == 1 else max(2, math.ceil(len(h) * 0.4))
    L = [dict(item_of(d, i, round(v[1] / v[0] * 2) / 2 or 1, v[2]), freq=v[0]) for i, v in cnt.items() if v[0] >= mn]
    return sorted(L, key=lambda x: -x["freq"])

# ---------- חישוב ----------
def km(a, b):
    r = math.radians; dla, dlo = r(b[0] - a[0]), r(b[1] - a[1])
    h = math.sin(dla / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(dlo / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h)) * ROAD

# רשתות שהסניפים שלהן כשרים (לפי ידיעה כללית על הרשת – לא אומת סניף-סניף). אפשר לערוך את הרשימה.
KOSHER_CHAINS = {"שופרסל", "רמי לוי", "יוחננוף", "אושר עד", "ויקטורי", "חצי חינם", "מחסני השוק", "קשת טעמים",
                 "סופר ספיר", "שפע ברכת השם", "נתיב החסד", "סופר יודה"}
def is_kosher(st):
    return st["chain"] in KOSHER_CHAINS and not re.search(r"\bBE\b|Be\b", st["n"])   # BE – פארם

def kosher_tag(st): return " · ✡️ סניף כשר" if is_kosher(st) else ""

def full_name(st):
    """"רמי לוי – סניף עזריאלי חיפה" """
    ch, n = st["chain"], st["n"]
    br = n[len(ch):].strip(" -–") if n.startswith(ch) else n
    return f"{ch} – סניף {br}" if br else ch

def gp(P, it):
    """המחיר הזול ביותר בסניף מבין המוצרים השקולים"""
    v = [P[g] for g in it["g"] if g in P]
    return min(v) if v else None

def money(x): return f"{x:.2f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 0.004 else f"{round(x)}"

def lc(P, it):
    """עלות השורה בסל בסניף (כמות × מחיר), כולל מבצעי כמות כשהכמות מספיקה.
    מחזיר (עלות, תיאור מבצע, שם החלופה או None) או None. אם אין בסניף את המוצר – מחפש חלופה שקולה (it["fb"])."""
    deals = getattr(P, "deals", {})
    for lst, sub in ((it["g"], False), (it.get("fb") or [], True)):
        best = None
        for g in lst:
            if g not in P: continue
            reg = P[g]; q = it["qty"]; c = reg * q; txt = None
            if g in deals:
                u, k = deals[g]
                bundle = u * k
                if abs(bundle - round(bundle, 1)) < 0.025: bundle = round(bundle, 1)   # 3.33×3 → ₪10 (עיגול אגורות)
                if q >= k:
                    n = int(q // k); c2 = n * bundle + (q - n * k) * reg
                    if c2 < c - 0.009: c, txt = c2, f"{k} ב־₪{money(bundle)}"
            if best is None or c < best[0]: best = (c, txt, kdata.D.items[g][1] if sub else None)
        if best: return best
    return None

def compute(u):
    D = kdata.D
    pr = dict(u["prefs"]); home = pr["home"]; car = pr["mode"] == "car"; bus = pr["mode"] == "bus"
    if car and pr.get("maxMin", 999) > CAR_MAX_MIN: pr["maxMin"] = CAR_MAX_MIN   # בלי עלות דלק – לא שולחים רחוק מדי
    split_min = -1 if pr.get("splitMin", SPLIT_MIN) < 0 else SPLIT_MIN
    clubs = set(pr.get("clubs") or [])
    cost = lambda d: 0          # המחיר המוצג = מוצרים בלבד (בלי דלק)
    mins = lambda d: car_min(d) if car else (d / BUS_KMH * 60 + BUS_WAIT if bus else d / WALK_KMH * 60)
    items, gone = [], []
    for it in u["items"]:
        i = resolve(D, it)
        if i is None: gone.append(it["name"])
        else:
            mine = pr.get("brand") == "mine"
            g = [i] if mine else D.group(i, it.get("src") or it["name"])
            items.append(dict(it, i=i, g=g, fb=[] if mine else D.fallback(i, it.get("src") or it["name"])))
    if not items:
        return ["לא מצאתי אף אחד מהמוצרים במאגר המחירים העדכני 🙁 נסה לכתוב אותם שוב."]
    near = sorted(((km(home, s["p"]), s) for s in D.stores), key=lambda x: x[0])
    if not near: return ["אין לי עדיין נתוני סניפים 🙁"]
    rows = []
    for k, (d1, s) in enumerate(near[:MAX_POOL * 2]):
        if k > 0 and mins(d1) > pr["maxMin"] and len(rows) >= 1: break
        rows.append({"s": s, "d": 2 * d1, "min": mins(d1), "P": D.store_prices(s, clubs), "club": s["chain"] in clubs and s.get("cblob", -1) >= 0})
        if len(rows) >= MAX_POOL: break
    uk = usual_store(u)
    if uk and not any(r["s"]["key"] == uk for r in rows):      # הסופר הקבוע תמיד בהשוואה, גם אם מחוץ לטווח
        s0 = next(s for s in D.stores if s["key"] == uk); d1 = km(home, s0["p"])
        rows.append({"s": s0, "d": 2 * d1, "min": mins(d1), "P": D.store_prices(s0, clubs), "club": s0["chain"] in clubs and s0.get("cblob", -1) >= 0})
    # חציון מקומי למוצרים שחסרים בסניף מסוים
    med = {}
    for it in items:
        v = sorted(x for x in (gp(r["P"], it) for r in rows) if x is not None)
        med[it["i"]] = v[len(v) // 2] if v else D.items[it["i"]][3] / 100
    for r in rows:
        tot = 0; miss = 0; mlist = []; have = 0
        for it in items:
            c = lc(r["P"], it)
            if c is None: miss += 1; c = med[it["i"]] * it["qty"]; mlist.append(it["name"].replace(' (לק"ג)', ""))
            else: c = c[0]; have += c                  # מחיר מדויק – רק מה שיש בסניף (להצגה), כולל מבצעי כמות
            tot += c                                   # להשוואה בין סניפים – חסר מוערך לפי האזור
        r.update(mlist=mlist, have=have, sum=tot, fuel=cost(r["d"]), tot=tot + cost(r["d"]), miss=miss)
    nearest = rows[0]
    reg = next((r for r in rows if r["s"]["key"] == uk), None)
    ok_miss = lambda r: r["miss"] < MAX_MISSING and r["miss"] * 2 < len(items)   # עד 3 חסרים (ופחות מחצי הסל)
    if reg and not ok_miss(reg): reg = None     # כנראה זיהוי סניף שגוי (או חנות שלא מוכרת את רוב הסל)
    pool = [r for r in rows if r["min"] <= pr["maxMin"] or r is reg] or [nearest]
    # סניף שחסרים בו הרבה מהמוצרים לא באמת עונה על הסל
    full = [r for r in pool if ok_miss(r)]          # סניף שחסרים בו 4+ מוצרים (או חצי מהסל) – לא נחשב ולא מוצג
    inrange = sorted(full or pool, key=lambda r: r["tot"])
    if full: pool = full
    ranked = sorted(pool, key=lambda r: r["tot"]); best = ranked[0]
    # לא שולחים רחוק בשביל חיסכון זניח: אם סניף קרוב יותר כמעט באותו מחיר – ממליצים עליו
    close = [r for r in ranked if r["tot"] - best["tot"] < max(5, 0.03 * best["sum"]) and r["min"] <= best["min"] - 3]
    if close:
        best = min(close, key=lambda r: r["d"]); ranked.remove(best); ranked.insert(0, best)
    split = None
    if split_min >= 0:
        for a in range(len(pool)):
            for b in range(a + 1, len(pool)):
                A, B = pool[a], pool[b]; tot = 0; inA, inB = [], []; ok = True
                for it in items:
                    pa, pb = lc(A["P"], it), lc(B["P"], it)
                    if pa is None and pb is None: ok = False; break
                    if pb is None or (pa is not None and pa[0] <= pb[0]): tot += pa[0]; inA.append(it["name"])
                    else: tot += pb[0]; inB.append(it["name"])
                if not ok or not inA or not inB: continue
                d = km(home, A["s"]["p"]) + km(A["s"]["p"], B["s"]["p"]) + km(B["s"]["p"], home)
                t = tot + cost(d)
                if not split or t < split["tot"]: split = {"A": A, "B": B, "tot": t, "inA": inA, "inB": inB}
    weighed = all(D.items[it["i"]][0][:1] in ("M", "W", "P") for it in items)   # רק מוצרים במשקל (בשר, דגים, ירקות…)
    stay_min = STAY_MIN_WEIGHED if weighed else STAY_MIN
    stay = reg is not None and reg["tot"] - best["tot"] < stay_min
    gap = reg["tot"] - best["tot"] if reg else 0
    if stay:                                   # לא שווה לעזוב את הסופר הקבוע
        alt = best; best = reg
        if reg in ranked: ranked.remove(reg)
        ranked.insert(0, reg)
    s = best["s"]
    how = "נסיעה" if car else ("בתחבורה ציבורית" if bus else "הליכה")
    others = [r for r in ranked if r["s"]["key"] != s["key"]]
    avg = sum(r["tot"] for r in others) / len(others) if others else None
    L = []
    if nearest["min"] > 45:
        L.append(f"⚠️ _עדיין אין לי סניפים באזור שלך (הקרוב ביותר: {round(nearest['min'])} דק' נסיעה)._\n")
    inc = ""
    ride = "🚗" if car else ("🚌" if bus else "🚶")
    if stay:
        L.append(f"👍 הסופר הקבוע שלך משתלם: *{s['n']}*" + kosher_tag(s))
        L.append(f"💰 מחיר הסל: *₪{best['have']:.0f}*" + (" (במחירי מועדון)" if best.get("club") else "")
                 + (f"\n❌ _חסר בסניף: {', '.join(best['mlist'][:6])}_" if best["miss"] else ""))
        L.append(f"✅ זה הכי זול באזור 🏆" if gap < 1 else f"✅ במקום אחר היית חוסך רק ₪{gap:.0f}{inc} – לא שווה את הטרחה")
    else:
        L.append(f"🏆 הסופר המנצח: *{s['n']}*" + kosher_tag(s))
        L.append(f"💰 מחיר הסל: *₪{best['have']:.0f}*" + (" (במחירי מועדון)" if best.get("club") else "")
                 + (f"\n❌ _חסר בסניף: {', '.join(best['mlist'][:6])}_" if best["miss"] else ""))
        if reg:
            L.append(f"✅ חוסך *₪{gap:.0f}* לעומת {reg['s']['n']}{inc}")
        elif avg and avg - best["tot"] >= 1:
            L.append(f"✅ חוסך *₪{avg - best['tot']:.0f}*{inc}")
    L.append(f"{ride} {round(best['min'])} דק' {how} ממך")
    split_txt = None
    if split and best["tot"] - split["tot"] > split_min:
        A, B = split["A"]["s"], split["B"]["s"]
        split_txt = (f"🔀 *קנייה בשני סופרים – ₪{split['tot']:.0f}*\n\n*{A['n']}* ({A['a']}):\n" + "\n".join("• " + x for x in split["inA"]) +
                     f"\n\n*{B['n']}* ({B['a']}):\n" + "\n".join("• " + x for x in split["inB"]))
    # פירוט מחירי הסל בסופר המנצח
    det = []
    for it in sorted(items, key=lambda it: -((lc(best["P"], it) or (med[it["i"]] * it["qty"],))[0])):
        c = lc(best["P"], it)
        q = f" ×{it['qty']:g}" if it["qty"] != 1 else ""
        det.append(f"• {it['name']}{q} – " + (f"₪{c[0]:.2f}" + (f" 🔥 _מבצע {c[1]}_" if c[1] else "")
                                               + (f" _(אין בסניף – במקום: {c[2]})_" if c[2] else "") if c is not None
                                               else f"~₪{med[it['i']] * it['qty']:.2f} _(חסר בסניף)_"))
    if gone: det.append("\n_לא נמצאו במאגר: " + ", ".join(gone) + "_")
    fresh = f"\n\n_מחירים ומבצעים מעודכנים ל-{D.built[8:10]}/{D.built[5:7]} {D.built[11:16]}_" if len(D.built) >= 16 else ""
    saved = 0 if stay else (gap if reg else (avg - best["tot"] if avg else 0))
    ip = {}
    for it in items:                                   # מחיר כל מוצר בסופר המומלץ (ל"מה זיהיתי")
        c = lc(best["P"], it)
        ip[it.get("code", "")] = [round(c[0] if c else med[it["i"]] * it["qty"], 2), c is None, c[1] if c else None]
    return {"main": "\n".join(L), "store": s["n"], "saved": max(0, round(saved)), "ip": ip,
            "nav": f"https://waze.com/ul?ll={s['p'][0]},{s['p'][1]}&navigate=yes",
            "details": f"📋 *הסל שלך ב{s['n']}:*\n" + "\n".join(det) + fresh,
            "others": (f"💡 אם מתחלקים בין *{split['A']['s']['n']}* ל*{split['B']['s']['n']}* חוסכים עוד *₪{best['tot'] - split['tot']:.0f}*\n\n" if split_txt else "")
                      + "🏪 *כל הסופרים בטווח:*\n\n" + "\n\n".join(
                f"{'🏆' if r['s']['key'] == s['key'] else '▫️'} *{full_name(r['s'])}*" + (" ⭐ _הקבוע שלך_" if r is reg else "") + "\n"
                + ("      ✡️ סניף כשר\n" if is_kosher(r["s"]) else "") +
                f"      📍 {r['s']['a']}\n      💰 ₪{r['have']:.0f} · {ride} {round(r['min'])} דק'"
                + (f"\n      ❌ _חסר בסניף: {', '.join(r['mlist'][:6])}_" if r["miss"] else "")
                for r in (inrange[:10] + ([reg] if reg and reg not in inrange[:10] and reg in full else []))) + fresh,
            "split": split_txt}

# ---------- שיחה ----------
WELCOME = "היי{name}! 👋 אני אעזור לך לחסוך בקניות 🛒\nכמה שאלות קצרות ומתחילים 😊"
def welcome(u): return WELCOME.format(name=" " + u["name"] if u.get("name") else "")
PROFILE_KEYS = ("home", "mode", "maxMin", "splitMin")
GREET = re.compile(r"^\W*(הי+|שלום|אהלן|הלו|יו|מה קורה|מה נשמע|מה המצב|מה הולך|מה איתך|בוקר טוב|ערב טוב|צהריים טובים|hi+|hey|hello|start)\b[^,\n]*$", re.I)

def ask(to, step, u, intro=""):
    u["step"] = step
    if step == "loc":
        send_location_request(to, intro + "📍 *מאיפה יוצאים לקניות?*\nלוחצים על הכפתור למטה")
    elif step == "mode":
        send_buttons(to, "🚗 *איך מגיעים לסופר?*", [("car", "🚗 ברכב"), ("walk", "🚶 ברגל"), ("bus", "🚌 תחבורה ציבורית")])
    elif step == "range":
        send_buttons(to, "⏱️ *כמה רחוק שווה לך לנסוע?*", [("r5", "עד 5 דקות"), ("r15", "עד 15 דקות"), ("rall", "לא משנה")])
    elif step == "hh":
        send_buttons(to, "👨‍👩‍👧 *כמה אנשים בבית?*", [("hh12", "1–2"), ("hh34", "3–4"), ("hh5", "5+")])
    elif step == "clubq":
        u["club_ctx"] = "onb"
        send_buttons(to, "💳 *יש לך מועדון לקוחות?*", [("clubyes", "כן"), ("club:none", "לא")])
    elif step == "kosher":
        send_buttons(to, "✡️ *יש העדפת כשרות?*", [("kreg", "רגיל"), ("kmeh", "מהדרין בלבד")])
    elif step == "split":
        send_buttons(to, "*4️⃣ מוכן לקנות בשני סופרים אם חוסכים?*", [("s0", "כן"), ("s30", "רק אם מעל ₪30"), ("sno", "לא, סופר אחד")])
    elif step == "basket":
        L = learned(u)
        if L:
            send_buttons(to, intro + f"*ומה קונים הפעם?*\nלמדתי מה אתה קונה בדרך כלל 📈", [("mine", f"🛒 הסל הקבוע שלי"), ("cam", "📷 צילום קבלה"), ("list", "✍️ אכתוב רשימה")])
        else:
            send_buttons(to, intro + "*ומה קונים?*", [("cam", "📷 צילום קבלה"), ("list", "✍️ אכתוב רשימה"), ("weekly", "🛒 סל שבועי רגיל")])
    save_users()

def finish(to, u, redo=False):
    res = compute(u); saved = 0
    if isinstance(res, list):
        for m in res: send_text(to, m)
    else:
        u["last"] = {k: res[k] for k in ("details", "others", "split", "store", "ip")}; saved = res.get("saved", 0)
        n, miss = len(u["items"]), len(u.get("missed") or [])
        main = res["main"] + f"\n🧾 זיהיתי {n} מוצרים" + (f" ({miss} לא נמצאו)" if miss else "")
        if u.get("note"): main += "\n\n" + u.pop("note")
        send_buttons(to, main, [("seen", "🧾 מה זיהיתי"), ("oth", "🏪 סופרים אחרים")])
    entry = {"d": int(time.time()), "saved": saved, "items": [{"code": x.get("code", ""), "name": x["name"], "src": x.get("src", ""), "qty": x["qty"]} for x in u["items"]]}
    if redo and u["hist"]: u["hist"][-1] = entry          # תיקון – מחליפים את הקנייה האחרונה במקום להוסיף
    else: u["hist"] = (u["hist"] + [entry])[-12:]
    u["step"] = "done"; save_users()
    if redo: return
    if len(u["hist"]) == 1:
        send_text(to, "_שמרתי את הקנייה כדי ללמוד את ההרגלים שלך. מתי שרוצים – פשוט כותבים *היי* 🙂_")
    pr = u["prefs"]; n = len(u["hist"])
    if n >= 1 and "clubs" not in pr:                       # משתמשים ותיקים שלא נשאלו בפתיחה
        pr["clubs"] = []; return ask_clubs(to, u, first=True)


# ---------- סיכום חיסכון ----------
HEB_MONTHS = ["ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני", "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"]

def savings_text(u, y=None, m=None):
    """סיכום לחודש (ברירת מחדל: החודש הנוכחי) + מתחילת הדרך"""
    now = _dt.now(IL_TZ)
    y, m = y or now.year, m or now.month
    def ym(b): t = _dt.fromtimestamp(b["d"], IL_TZ); return t.year, t.month
    month = [b for b in u.get("hist", []) if ym(b) == (y, m)]
    tot_m = sum(b.get("saved", 0) for b in month); tot_all = sum(b.get("saved", 0) for b in u.get("hist", []))
    if not month: return None
    L = [f"💰 *סיכום חיסכון – {HEB_MONTHS[m - 1]}*", "", ("🛒 בדקת קנייה אחת" if len(month) == 1 else f"🛒 בדקת {len(month)} קניות")]
    if tot_m >= 1:
        L.append(f"✅ מצאתי לך חיסכון של *₪{tot_m:.0f}*" + (f" (בממוצע ₪{tot_m / len(month):.0f} לקנייה)" if len(month) > 1 else ""))
    else:
        L.append("👍 הסופר שלך כבר היה הכי משתלם – לא היה צריך להחליף")
    if tot_all > tot_m + 1: L.append(f"🏅 מאז שהתחלת: *₪{tot_all:.0f}*")
    return "\n".join(L)

def monthly_summaries():
    """ב-1 לחודש (מ-10:00) – סיכום החודש הקודם לכל מי שבדק בו קניות"""
    now = _dt.now(IL_TZ)
    if now.day != 1 or now.hour < 10: return
    py, pm = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    tag = f"{py}-{pm:02d}"
    for to, u in list(USERS.items()):
        if to.startswith("_") or not isinstance(u, dict) or not is_tg(to) or u.get("sum_sent") == tag: continue
        txt = savings_text(u, py, pm)
        u["sum_sent"] = tag
        if txt: send_text(to, txt)
    save_users()


# ---------- לוח בקרה למנהל ----------
def stats_text():
    now = time.time(); wk = now - 7 * 86400; day = now - 86400
    us = {k: v for k, v in USERS.items() if not k.startswith("_") and isinstance(v, dict)}
    done = [u for u in us.values() if all(k in u.get("prefs", {}) for k in PROFILE_KEYS)]
    buys = [b["d"] for u in us.values() for b in u.get("hist", [])]
    rec = [t for u in us.values() for t in u.get("receipts", [])]
    active = [u for u in us.values() if any(b["d"] > wk for b in u.get("hist", []))]
    tg = sum(1 for k in us if k.startswith("tg:"))
    clubs = {}
    for u in us.values():
        for c in u.get("prefs", {}).get("clubs") or []: clubs[c] = clubs.get(c, 0) + 1
    D = kdata.D
    L = ["📊 *קונה אישי – לוח בקרה*", "",
         f"👥 משתמשים: *{len(us)}* (טלגרם {tg}) · סיימו הרשמה: {len(done)}",
         f"🔥 פעילים השבוע: *{len(active)}*",
         f"🛒 קניות שנבדקו: היום {sum(d > day for d in buys)} · השבוע *{sum(d > wk for d in buys)}* · סה\"כ {len(buys)}",
         f"🧾 קבלות שנסרקו: היום {sum(t > day for t in rec)} · השבוע *{sum(t > wk for t in rec)}* · סה\"כ {len(rec)}",
         f"⏰ תזכורות פעילות: {sum(1 for u in us.values() if u.get('remind_at') and not u.get('prefs', {}).get('remind_off'))}"]
    if clubs: L.append("💳 מועדונים: " + ", ".join(f"{c} {n}" for c, n in sorted(clubs.items(), key=lambda x: -x[1])))
    iss = watchdog_issues()
    qa = getattr(D, "qa", {}) or {}
    L += ["", "🛡️ " + ("הכל תקין" if not iss else f"{len(iss)} התראות:")] + iss
    if qa.get("removed") is not None: L.append(f"🧪 בדיקת מחירים: הוסרו {qa['removed']:,} מחירים חשודים בעדכון האחרון")
    if D:
        L += ["", f"📦 נתונים: {len(D.stores):,} סניפים · {len(D.items):,} מוצרים",
              f"🕐 עודכנו: {D.built[8:10]}/{D.built[5:7]} {D.built[11:16]}" if len(D.built) >= 16 else f"🕐 גרסה: {D.version} ({D.source})"]
    return "\n".join(L)

# ---------- תזכורת שבועית ----------
# קבלה שנסרקה ביום ובשעה מסוימים => תזכורת שבוע אחרי, שעתיים לפני אותה שעה (לא בלילה).
try:
    from zoneinfo import ZoneInfo
    IL_TZ = ZoneInfo("Asia/Jerusalem")
except Exception:
    from datetime import timezone as _tz, timedelta as _td
    IL_TZ = _tz(_td(hours=3))
from datetime import datetime as _dt

def next_reminder(ts):
    t = _dt.fromtimestamp(ts + 7 * 86400 - 2 * 3600, IL_TZ)
    if t.hour < 8: t = t.replace(hour=8, minute=0, second=0)
    elif t.hour >= 21: t = t.replace(hour=20, minute=0, second=0)
    return int(t.timestamp())

def send_reminder(to, u):
    name = u.get("name", "")
    u["step"] = "basket"
    btns = [("mine", "🛒 הסל הקבוע שלי"), ("cam", "📷 צילום קבלה"), ("remind_off", "בלי תזכורות")]
    send_buttons(to, f"היי{' ' + name if name else ''} 👋\nבדרך כלל אתה עושה קנייה בערך בזמן הזה.\n"
                     f"רוצה שאבדוק איפה הכי משתלם השבוע?", btns)

# ---------- שומר לילה: התראה למנהל כשמשהו בנתונים נשבר ----------
MAJOR = {"SHUFERSAL": "שופרסל", "RAMI_LEVY": "רמי לוי", "YOHANANOF": "יוחננוף", "OSHER_AD": "אושר עד",
         "TIV_TAAM": "טיב טעם", "YAYNO_BITAN_AND_CARREFOUR": "קרפור", "VICTORY_NEW_SOURCE": "ויקטורי", "HAZI_HINAM": "חצי חינם"}
_watch = {"t": 0}

def _iso_age_h(iso):
    try: return (time.time() - _dt.fromisoformat(iso).timestamp()) / 3600
    except Exception: return None

def admins():
    meta = USERS.get("_meta", {})
    return set(filter(None, os.environ.get("ADMIN_CHAT", "").split(","))) | set(meta.get("admins", []))

def watchdog_issues():
    D = kdata.D; meta = USERS.setdefault("_meta", {}); seen = meta.setdefault("seen", {}); out = []
    if not D or D.source != "remote":
        return ["⚠️ הבוט עובד על נתוני גיבוי (13 סניפים) – הקובץ הארצי לא נטען"] if D else []
    age = _iso_age_h(D.built)
    if age is not None and age > 9: out.append(f"⚠️ הנתונים לא התעדכנו כבר {age:.0f} שעות (מאז {D.built[11:16]} {D.built[8:10]}/{D.built[5:7]})")
    for ch, he in MAJOR.items():
        info = D.chains.get(ch)
        if not info:
            if seen.get(ch): out.append(f"⚠️ {he} נעלמה מהנתונים (היו {seen[ch]} סניפים)")
            continue
        a = _iso_age_h(info.get("fetched", ""))
        if a is not None and a > 12: out.append(f"⚠️ {he} לא התעדכנה כבר {a:.0f} שעות")
        n = info.get("stores", 0)
        if seen.get(ch) and n < 0.75 * seen[ch]: out.append(f"⚠️ {he} ירדה מ-{seen[ch]} ל-{n} סניפים")
        elif n: seen[ch] = max(n, int(seen.get(ch, 0) * 0.9))
    qa = getattr(D, "qa", {}) or {}
    for ch, n in (qa.get("by_chain") or {}).items():     # בדיקת תקינות: הרבה מחירים חשודים ברשת אחת = כנראה קובץ פגום
        if n >= 1500: out.append(f"⚠️ {ch}: {n:,} מחירים חשודים הוסרו בעדכון האחרון (כנראה קובץ פגום אצל הרשת)")
    return out

def watchdog():
    if time.time() - _watch["t"] < 1800: return
    _watch["t"] = time.time()
    ad = [a for a in admins() if is_tg(a)]
    if not ad: return
    meta = USERS.setdefault("_meta", {}); sent = meta.setdefault("alerts", {})
    new = [i for i in watchdog_issues() if time.time() - sent.get(i, 0) > 12 * 3600]   # כל תקלה – פעם ב-12 שעות לכל היותר
    if not new: return
    for a in ad: send_text(a, "🛡️ *שומר לילה – קונה אישי*\n\n" + "\n".join(new))
    for i in new: sent[i] = time.time()
    for k in [k for k, t in sent.items() if time.time() - t > 3 * 86400]: sent.pop(k)
    save_users()

# ---------- בדיקה עצמית יומית: אותה רשימה פעמיים → אותה תוצאה, כמויות הגיוניות, בכמה ערים ----------
# כל רשימה: (טקסט, {תחילת שם: כמות צפויה}) – מוצרים ארוזים = מספר חבילות, שקילים = ק"ג
SELFTEST_LISTS = [("""חלב 3% – 4 קרטונים
לחם אחיד פרוס – 2
ביצים L – 12 יחידות
קוטג' 5% – 3
גבינה צהובה – 1 חבילה
עגבניות – 2 ק"ג
מלפפונים – 1.5 ק"ג
בצל – 1 ק"ג
אורז פרסי – 1 ק"ג
פסטה ספגטי – 3 חבילות
שמן קנולה – 1 בקבוק
סוכר לבן – 1 ק"ג
במבה – 2 שקיות
ביסלי – 2 שקיות
קוקה קולה 1.5 ליטר – 2 בקבוקים
מים מינרליים – שישייה
נייר טואלט – 32 גלילים
סבון כלים – 1 בקבוק""",
  {"חלב": 4, "לחם": 2, "ביצים": 1, "קוטג": 3, "גבינה צהובה": 1, "פסטה": 3, "שמן": 1, "סוכר": 1,
   "במבה": 2, "ביסלי": 2, "קוקה קולה": 2, "מים": 1, "נייר טואלט": 1, "סבון כלים": 1}),
 ("""חזה עוף – 2 קילו
בשר טחון – חצי קילו
פילה סלמון – 300 גרם
גבינה בולגרית – 250 גרם
שניצל תירס – 2 חבילות
נקניקיות – 1 חבילה
טונה בשמן – רביעייה
חומוס – 2 קופסאות
טחינה – 1
קפה טורקי – 1
תה – 1 קופסה (100 שקיות)
קורנפלקס – 1
שוקו – 6 יחידות
יוגורט – 8 גביעים
בננות – 1 ק"ג
תפוחי אדמה – 3 ק"ג
גזר – 1 ק"ג
אבקת כביסה – 1
מגבות נייר – 6 גלילים
משחת שיניים – 2""",
  {"חזה עוף": 2, "בשר טחון": 0.5, "סלמון": 0.3, "שניצל": 2, "נקניקיות": 1, "טונה": 1, "חומוס": 2,
   "טחינה": 1, "קפה": 1, "תה": 1, "קורנפלקס": 1, "בננ": 1, "תפוח": 3, "גזר": 1, "אבקת כביסה": 1,
   "מגבות נייר": 1, "משחת שיניים": 2})]
SELFTEST_CITIES = {"נתיבות": [31.4215, 34.5925], "תל אביב": [32.0809, 34.7806], "ירושלים": [31.7857, 35.2007],
                   "חיפה": [32.7940, 34.9896], "באר שבע": [31.2518, 34.7913], "פתח תקווה": [32.0871, 34.8875]}
SELFTEST_BASIC = ("חלב", "לחם", "ביצים", "עגבני", "מלפפו", "בצל", "תפוח", "גזר", "סוכר", "אורז", "פסטה", "שמן", "קמח", "בננ")
_selftest = {"day": ""}

def selftest():
    """מחזיר (רשימת בעיות, שורת סיכום)"""
    D = kdata.D; probs = []; lines = []
    if not D or not CLAUDE_KEY: return ["אין נתונים / אין מפתח Claude – לא בדקתי"], ""
    norm = lambda t: t.replace("'", "").replace("׳", "")
    for li, (text, want_q) in enumerate(SELFTEST_LISTS, 1):
        runs = []
        for k in (1, 2):
            tag = f"רשימה {li}, ריצה {k}"
            its = extract_items(text=text) or []
            if not its: probs.append(f"{tag}: לא הצלחתי לקרוא את הרשימה"); continue
            for key, want in want_q.items():
                x = next((x for x in its if norm(x["name"]).startswith(norm(key))), None)
                if x is None: probs.append(f"{tag}: לא זוהה \"{key}\"")
                elif abs(x["qty"] - want) > 0.01: probs.append(f"{tag}: \"{x['name']}\" נקרא ×{x['qty']:g} במקום ×{want:g}")
            found, missed = match_items(its)
            if missed: probs.append(f"{tag}: לא נמצאו במאגר: {', '.join(missed[:6])}")
            res = {}
            for city, home in SELFTEST_CITIES.items():
                fu = {"prefs": {"home": home, "mode": "car", "maxMin": 35, "splitMin": -1, "clubs": []}, "items": found, "hist": []}
                r = compute(fu)
                if not isinstance(r, dict): probs.append(f"{tag}: החישוב נכשל ב{city}"); continue
                res[city] = (r["store"], sum(v[0] for v in r["ip"].values()))
                for it in found:                       # מוצר בסיסי שיוצא "חסר" בסופר המומלץ = כנראה התאמה לא נכונה
                    v = r["ip"].get(it.get("code", ""))
                    if v and v[1] and any(norm(it.get("src", "")).startswith(b) for b in SELFTEST_BASIC):
                        probs.append(f"{tag}: \"{it.get('src')}\" יצא חסר ב{r['store']} ({city}) – הותאם ל\"{it['name']}\"")
                for it in found:
                    c = r["ip"].get(it.get("code", ""), [0])[0]
                    if c > 150 and not re.match(r"^[PMEW]", it.get("code", "")):
                        probs.append(f"{tag}: שורה יקרה חשודה ב{city} – {it['name']} ×{it['qty']:g} = ₪{c:.0f}")
            runs.append((sorted((x["name"], x["qty"]) for x in its), res, len(found)))
        if len(runs) == 2:
            (a, ra, n), (b, rb, _) = runs
            if a != b: probs.append(f"רשימה {li}: נקראה אחרת בשתי הריצות: " + ", ".join(f"{x} ×{q:g}" for x, q in sorted(set(a) ^ set(b)))[:400])
            for city in ra:
                if city in rb and abs(ra[city][1] - rb[city][1]) > 0.03 * max(ra[city][1], rb[city][1]):
                    probs.append(f"רשימה {li}, {city}: מחיר שונה בין הריצות ₪{ra[city][1]:.0f} מול ₪{rb[city][1]:.0f}")
            lines.append(f"רשימה {li} ({n} מוצרים): " + " · ".join(f"{c} ₪{v[1]:.0f}" for c, v in ra.items()))
    return list(dict.fromkeys(probs)), ("🧪 " + "\n".join(lines)) if lines else ""

def selftest_daily():
    il = time.gmtime(time.time() + 3 * 3600)
    day = time.strftime("%Y-%m-%d", il)
    if il.tm_hour < 9 or _selftest["day"] == day: return      # פעם ביום, אחרי 09:00
    meta = USERS.setdefault("_meta", {})
    if meta.get("selftest_day") == day: _selftest["day"] = day; return
    _selftest["day"] = day; meta["selftest_day"] = day; save_users()
    probs, summ = selftest()
    print("selftest", summ, probs, flush=True)
    if probs:
        for a in [a for a in admins() if is_tg(a)]:
            send_text(a, "🧪 *בדיקה יומית – נמצאה בעיה*\n\n" + "\n".join("⚠️ " + p for p in probs[:12]) + ("\n\n" + summ if summ else ""))

def _reminder_loop():
    while True:
        time.sleep(60)
        try: watchdog()
        except Exception as ex: print("watchdog error", ex, flush=True)
        try: monthly_summaries()
        except Exception as ex: print("summary error", ex, flush=True)
        try: selftest_daily()
        except Exception as ex: print("selftest error", ex, flush=True)
        now = time.time()
        for to, u in list(USERS.items()):
            try:
                ra = u.get("remind_at")
                if not ra or ra > now or u.get("prefs", {}).get("remind_off") or not is_tg(to): continue
                if now - ra > 6 * 3600:          # פספסנו (השרת היה כבוי) – לא שולחים באיחור גדול
                    u["remind_at"] = ra + 7 * 86400; continue
                send_reminder(to, u)
                u["remind_miss"] = u.get("remind_miss", 0) + 1
                u["remind_at"] = ra + 7 * 86400 if u["remind_miss"] < 3 else None   # 3 תזכורות בלי תגובה – מפסיקים
                save_users()
            except Exception as ex:
                print("reminder error", to, ex, flush=True)

CLUBS = ["שופרסל", "רמי לוי", "יוחננוף", "אושר עד", "ויקטורי", "טיב טעם"]
def club_chains():
    """רשתות שיש לנו בנתונים מחירי מועדון שלהן – לפי מספר סניפים"""
    D = kdata.D; cnt = {}
    for st in (D.stores if D else []):
        if st.get("cblob", -1) >= 0: cnt[st["chain"]] = cnt.get(st["chain"], 0) + 1
    return [c for c, n in sorted(cnt.items(), key=lambda x: -x[1])][:7] or CLUBS

MAX_CLUBS = 3

def ask_clubs(to, u, first=False):
    u["step"] = "clubq"; u["club_ctx"] = "later"; save_users()
    return send_buttons(to, "💳 *יש לך מועדון לקוחות?*", [("clubyes", "כן"), ("club:none", "לא")])

def club_buttons(u):
    have = u["prefs"].get("clubs", [])
    return [(f"club:{c}", ("✅ " if c in have else "") + c) for c in club_chains()] + [("clubdone", "סיימתי 👍")]

def clubs_done(to, u):
    if u.get("club_ctx") == "onb":       # סוף האבחון (בלי שאלת כשרות – מסמנים "סניף כשר" ליד הסניפים)
        pr = u["prefs"]; pr.setdefault("splitMin", SPLIT_MIN); pr.pop("remind_off", None)
        return ask(to, "basket", u, intro="מעולה, סיימנו 🎉\n\n")
    u["step"] = "done"; save_users()
    return send_text(to, "תודה, רשמתי 👍")

def understood_text(u, missed=()):
    last = u.get("last") or {}; ip = last.get("ip") or {}
    L = [f"🧾 *זיהיתי {len(u['items'])} מוצרים*" + (f" – מחירים ב{last['store']}:" if ip and last.get("store") else ":"), ""]
    tot = 0
    def toks(t): return {kdata.stem(w) for w in kdata.toks(t)}
    for x in u["items"]:
        kg = 'לק"ג' in x["name"]
        q = (f" ×{x['qty']:g} ק\"ג" if kg else f" ×{x['qty']:g}") if x["qty"] != 1 else ""
        nm = x["name"].replace(' (לק"ג)', "")
        src = x.get("src") or ""
        a, b = toks(src), toks(x["name"])
        alt = f" _(כתבת: {src})_" if src and a and len(a & b) < 0.5 * len(a) else ""   # הצגה רק כשהזיהוי שונה ממה שנכתב
        pr = ip.get(x.get("code", ""))
        price = ("" if not pr else " – ❌ _חסר בסניף_" if pr[1] else f" – ₪{pr[0]:.2f}" + (f" 🔥 _מבצע {pr[2]}_" if len(pr) > 2 and pr[2] else ""))
        if pr and not pr[1]: tot += pr[0]
        L.append(f"• {nm}{q}{price}{alt}")
    if tot: L.append(f"\n💰 *סה\"כ: ₪{tot:.2f}*")
    if missed: L.append("\n❓ לא מצאתי: " + ", ".join(missed[:8]))
    return "\n".join(L)

# ---------- הודעות קוליות (תמלול) ----------
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

def transcribe(audio, fname="voice.ogg"):
    """תמלול לעברית: Groq (חינמי) ואם אין – OpenAI. מחזיר טקסט או None"""
    if GROQ_KEY: url, key, model = "https://api.groq.com/openai/v1/audio/transcriptions", GROQ_KEY, "whisper-large-v3"
    elif OPENAI_KEY: url, key, model = "https://api.openai.com/v1/audio/transcriptions", OPENAI_KEY, "whisper-1"
    else: return None
    b = "----koneh" + base64.b16encode(os.urandom(8)).decode()
    parts = []
    for k, v in (("model", model), ("language", "he"), ("response_format", "json"),
                 ("prompt", "רשימת קניות לסופר: חלב, לחם, ביצים, קוטג', חזה עוף, במבה")):
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\nContent-Type: audio/ogg\r\n\r\n'.encode() + audio + b"\r\n")
    body = b"".join(parts) + f"--{b}--\r\n".encode()
    req = urllib.request.Request(url, data=body, headers={"Authorization": f"Bearer {key}", "Content-Type": f"multipart/form-data; boundary={b}",
                                                         "User-Agent": "koneh-ishi-bot"})
    try:
        return (json.load(urllib.request.urlopen(req, timeout=60)).get("text") or "").strip() or None
    except urllib.error.HTTPError as e:
        print("STT error", e.code, e.read()[:300], flush=True)
    except Exception as e:
        print("STT error", e, flush=True)
    return None


# ---------- הבנת טקסט חופשי ----------
UNDERSTAND = """You parse messages for "קונה אישי", an Israeli Telegram bot that compares supermarket basket prices near the user.
User message (Hebrew): <<{text}>>
Current basket: {basket}
Last recommended store: {store}
Reply with ONLY a JSON object: {{"intent": "...", "items": [{{"name": "...", "qty": 1}}], "reply": ""}}
intent is one of:
 "list"   – a shopping list / products to compare (a new basket). One product with a quantity ("קילו חזה עוף") is also a list.
 "add"    – add products to the CURRENT basket ("תוסיף במבה", "גם חלב").
 "remove" – remove products from the current basket ("תוריד את הקולה", "בלי ביצים").
 "price"  – asks the price of ONE product or where it is cheapest ("כמה עולה קוטג'?", "איפה הכי זול חלב").
 "chat"   – anything else: thanks, greetings, choosing a store ("נלך על רמי לוי"), questions about the bot.
items: product names in Hebrew, with obvious typos corrected ("במבע"→"במבה", "חלן"→"חלב", "קוטג"→"קוטג'"), WITHOUT quantity words; qty = units, but for meat/chicken/fish/produce
sold by weight qty is kilograms ("קילו"=1, "חצי קילו"=0.5, "300 גרם"=0.3, "2 קילו"=2). A count inside ONE package stays in the name, not qty
("נייר טואלט 12 גלילים" → qty 1, "ביצים 12" → qty 1). Empty list for chat.
reply: only for chat – a short warm Hebrew reply (max 2 short sentences), e.g. wish good shopping if they chose a store,
or explain they can send a list, a receipt photo or ask "כמה עולה X". Never invent prices."""


def understand(text, u):
    basket = ", ".join(x["name"] for x in (u.get("items") or [])[:40]) or "none"
    store = ((u.get("last") or {}).get("store")) or "none"
    r = claude_json(UNDERSTAND.format(text=text[:1500], basket=basket, store=store), keep_dict=True)
    if not isinstance(r, dict) or r.get("intent") not in ("list", "add", "remove", "price", "chat"): return None
    its = []
    for x in r.get("items") or []:
        if not isinstance(x, dict) or not str(x.get("name", "")).strip(): continue
        try: q = float(x.get("qty") or 1)
        except Exception: q = 1
        its.append({"name": str(x["name"]).strip(), "qty": q if 0 < q < 100 else 1})
    r["items"] = its
    print("understood:", r["intent"], [i["name"] for i in its], flush=True)
    return r


# מוצר כללי מדי ("בשר", "גבינה") – שואלים איזה סוג לפני שמשווים
VAGUE = {
    "בשר": [("בשר בקר טחון", "🥩 טחון"), ("בשר בקר לבישול", "🍲 בקר לבישול"), ("אנטריקוט", "🥩 אנטריקוט"), ("חזה עוף", "🍗 חזה עוף")],
    "עוף": [("חזה עוף", "חזה"), ("שניצל עוף", "שניצל"), ("פרגיות", "פרגיות"), ("כרעיים עוף", "כרעיים"), ("עוף שלם", "עוף שלם")],
    "דג": [("פילה סלמון", "🐟 סלמון"), ("אמנון", "🐟 אמנון"), ("דניס", "🐟 דניס"), ("לברק", "🐟 לברק")],
    "גבינה": [("גבינה צהובה", "🧀 צהובה"), ("קוטג' 5%", "קוטג'"), ("גבינה לבנה 5%", "גבינה לבנה"), ("גבינה בולגרית", "בולגרית")],
}
VAGUE_ALIAS = {"בשרים": "בשר", "בשר בקר": "בשר", "עופות": "עוף", "דגים": "דג", "גבינות": "גבינה"}
Q_VAGUE = {"בשר": "🥩 איזה בשר?", "עוף": "🍗 איזה עוף?", "דג": "🐟 איזה דג?", "גבינה": "🧀 איזו גבינה?"}

def vague_kind(name):
    n = re.sub(r"(?<![א-ת])(טרי|טריים|טרייה|טריות|טוב|טובה|איכותי|רגיל|רגילה|קצת|של)(?![א-ת])", " ", name)
    n = " ".join(n.split())
    n = VAGUE_ALIAS.get(n, n)
    return n if n in VAGUE else None

def ask_vague(to, u):
    pv = u["vague"]
    for k in range(pv["k"], len(pv["items"])):
        kind = vague_kind(pv["items"][k]["name"])
        if kind:
            pv["k"] = k; u["vague_kind"] = kind; save_users()
            return send_buttons(to, Q_VAGUE[kind], [(f"v:{j}", t) for j, (_, t) in enumerate(VAGUE[kind])])
    r = {"intent": pv["intent"], "items": pv["items"]}; u.pop("vague", None); u.pop("vague_kind", None)
    return act_on(to, u, r, asked=True)

def act_on(to, u, r, asked=False):
    intent, its = r["intent"], r["items"]
    if not asked and intent in ("list", "add", "price") and any(vague_kind(x["name"]) for x in its):
        u["vague"] = {"intent": intent, "items": its, "k": 0}
        return ask_vague(to, u)
    if intent in ("add", "remove") and not u.get("items"): intent = "list" if intent == "add" else "chat"
    if intent != "chat" and not its: intent = "chat"
    if intent == "chat":
        rep = str(r.get("reply") or "").strip()[:300] or "אפשר לשלוח רשימת קניות, צילום קבלה, או לשאול *כמה עולה* מוצר 🙂"
        return send_text(to, rep)
    if intent == "list":
        return got_items(to, u, its)
    if intent == "price":
        return price_answer(to, u, its[0])
    if intent == "add":
        found, missed = match_items(its)
        if not found: return send_text(to, "לא מצאתי את זה במאגר 🙁 נסו לכתוב קצת אחרת.")
        u["items"] += [{k: x[k] for k in ("src", "code", "name", "qty", "c")} for x in found]
        u["note"] = "➕ הוספתי: " + ", ".join(x["name"] for x in found) + (f"\n(לא מצאתי: {', '.join(missed)})" if missed else "")
        return finish(to, u, redo=True)
    # remove
    gone = []
    for x in its:
        w = {kdata.stem(t) for t in kdata.toks(x["name"])}
        best = max(u["items"], key=lambda it: len(w & {kdata.stem(t) for t in kdata.toks(it.get("src", "") + " " + it["name"])}), default=None)
        if best and w & {kdata.stem(t) for t in kdata.toks(best.get("src", "") + " " + best["name"])}:
            u["items"].remove(best); gone.append(best["name"])
    if not gone: return send_text(to, "לא מצאתי את זה בסל 🤔 אפשר ללחוץ *🧾 מה זיהיתי* ולתקן משם.")
    if not u["items"]: save_users(); return send_text(to, "הסל ריק עכשיו 🙂 אפשר לכתוב רשימה חדשה.")
    u["note"] = "➖ הורדתי: " + ", ".join(gone)
    return finish(to, u, redo=True)


def price_answer(to, u, item):
    """שאלה על מוצר אחד: איפה הכי זול באזור (לפי המיקום וטווח הנסיעה מהאבחון)"""
    found, _ = match_items([item])
    if not found: return send_text(to, f"לא מצאתי את *{item['name']}* במאגר 🙁 נסו לכתוב קצת אחרת.")
    x = found[0]; D = kdata.D
    pr = u["prefs"]; home = pr["home"]; clubs = set(pr.get("clubs") or [])
    near = sorted(D.stores, key=lambda s: km(home, s["p"]))[:MAX_POOL * 2]
    for code in [x["code"]] + list(x.get("c") or []):        # אם המוצר שנבחר לא נמכר באזור – מנסים את החלופה הבאה
        i = D.code2i.get(code)
        if i is None: continue
        g = D.group(i, x.get("src") or x["name"]); rows = []
        for s in near:
            d1 = km(home, s["p"]); mn = car_min(d1) if pr["mode"] == "car" else (d1 / BUS_KMH * 60 + BUS_WAIT if pr["mode"] == "bus" else d1 / WALK_KMH * 60)
            if rows and mn > (min(pr["maxMin"], CAR_MAX_MIN) if pr["mode"] == "car" else pr["maxMin"]): break
            SP = D.store_prices(s, clubs); p = gp(SP, dict(x, i=i, g=g))
            dl = min((SP.deals[j] for j in g if j in SP.deals), default=None)
            if p is not None: rows.append((p, mn, s, f" 🔥 _{dl[1]} ב־₪{money(round(dl[0] * dl[1], 1) if abs(dl[0] * dl[1] - round(dl[0] * dl[1], 1)) < 0.025 else dl[0] * dl[1])}_" if dl else ""))
            if len(rows) >= MAX_POOL: break
        if len(rows) >= 2: break
    if not rows: return send_text(to, f"לא מצאתי את *{D.items[i][1]}* בסופרים באזור שלך 🙁")
    rows.sort(key=lambda r: r[0]); kg = 'לק"ג' in D.items[i][1]
    unit = " לק\"ג" if kg else ""
    L = [f"💰 *{D.items[i][1]}* – באזור שלך:", ""]
    ride = "🚗" if pr["mode"] == "car" else ("🚌" if pr["mode"] == "bus" else "🚶")
    for k, (p, mn, s, dl) in enumerate(rows[:5]):
        L.append(f"{'🏆' if k == 0 else '▫️'} {s['n']} – *₪{p:.2f}*{unit}{dl} · {ride} {round(mn)} דק'")
    if len(rows) > 1 and rows[-1][0] - rows[0][0] >= 0.5:
        L.append(f"\n✅ הפרש של עד ₪{rows[-1][0] - rows[0][0]:.2f} בין הסופרים באזור")
    return send_text(to, "\n".join(L))


def got_receipt(to, u, img, mime):
    pr = u["prefs"]
    items, store_txt, meta = extract_items(img, mime, with_store=True)
    if not items or meta.get("quality") == "bad":
        e_img, e_mime = enhance(img)                       # ניסיון שני – אחרי שיפור התמונה
        if e_img:
            i2, s2, m2 = extract_items(e_img, e_mime, with_store=True)
            if i2 and len(i2) > len(items or []): items, store_txt, meta = i2, s2 or store_txt, m2
    print("receipt:", len(items or []), "items", meta, flush=True)
    if not items:
        tip = ISSUE_TIP.get(meta.get("issue"), "נסו לצלם שוב – על משטח ישר, באור טוב, כשהקבלה ממלאת את המסך.")
        return send_text(to, "לא הצלחתי לקרוא את הקבלה 🙁\n" + tip + "\nאפשר גם פשוט לכתוב מה קניתם ✍️")
    now = int(time.time())
    # תמונה נוספת זמן קצר אחרי הקודמת – כנראה עוד חלק מאותה קבלה: מחברים לאותו סל
    if u.get("rbuf_t", 0) > now - MERGE_SEC and u.get("items") and u.get("step") in ("done", "forgot"):
        found, missed = match_items(items)
        have = {(x["code"], x["qty"]) for x in u["items"]}
        new = [x for x in found if (x["code"], x["qty"]) not in have]   # שורות שצולמו פעמיים (חפיפה בין התמונות) – פעם אחת
        u["items"] += new; u["missed"] = ((u.get("missed") or []) + missed)[:8]; u["rbuf_t"] = now
        u["note"] = f"➕ חיברתי את התמונה הנוספת לאותה קבלה (+{len(new)} מוצרים)"
        if u["step"] == "forgot":                            # עדיין מחכים לתשובה על "בדרך כלל אתה קונה גם" – רק מעדכנים
            save_users(); return send_text(to, u.pop("note"))
        typing(to)
        return finish(to, u, redo=True)
    if not pr.get("home"): u["step"] = "loc"
    u["last_receipt"] = now; u["remind_at"] = next_reminder(now); u["remind_miss"] = 0
    u["receipts"] = (u.get("receipts", []) + [now])[-50:]
    u["rbuf_t"] = now
    sk = match_store(store_txt, pr.get("home"))
    print("receipt store:", store_txt, "->", sk, flush=True)
    if sk: st = u.setdefault("stores", {}); st[sk] = st.get(sk, 0) + 1
    if meta.get("quality") == "partial":
        u["note"] = "⚠️ חלק מהקבלה לא היה ברור – אפשר לשלוח עכשיו צילום של החלק החסר ואחבר אותו, או לכתוב מה חסר ✍️"
    return got_items(to, u, items)


def got_items(to, u, items):
    found, missed = match_items(items)
    if not found:
        return send_text(to, "לא מצאתי את המוצרים האלה במאגר 🙁 נסה לכתוב אותם קצת אחרת." + (f"\n({', '.join(missed[:8])})" if missed else ""))
    u["items"] = found; u["missed"] = missed[:8]
    have = {x["code"] for x in found}
    forgot = [x for x in learned(u) if x["code"] not in have and x["freq"] >= 2][:5]
    u["items"] = found
    if forgot:
        u["pending"] = forgot; u["step"] = "forgot"; save_users()
        send_buttons(to, "🤔 בדרך כלל אתה קונה גם:\n" + "\n".join("• " + x["name"] for x in forgot) + "\nלהוסיף לסל?", [("addf", "כן, להוסיף"), ("noadd", "לא, תודה")])
        return
    finish(to, u)

COMMANDS = ["אבחון מחדש", "אבחון", "איפוס", "איפוס מלא", "מחק אותי", "הגדרות", "כמה חסכתי", "סיכום חיסכון", "סטטיסטיקה"]

def fix_command(text):
    """פקודה עם שגיאת כתיב קלה (אות אחת, או אות סמוכה במקלדת כמו ו/ן) – מזהים אותה בכל זאת"""
    t = re.sub(r"[^\w\s]", "", text).strip()
    if not t or len(t) > 20 or t in COMMANDS: return text
    norm = lambda x: x.translate(kdata.FINALS)
    tn = norm(t)
    for c in COMMANDS:
        cn = norm(c)
        if len(cn) >= 4 and kdata.edit1(tn, cn): return c
        if len(cn) >= 8 and len(tn) == len(cn) and sum(a != b for a, b in zip(tn, cn)) <= 2: return c
    return text

def place_name(home):
    """שם המקום לפי הסניפים הקרובים ("נתיבות")"""
    D = kdata.D
    if not D or not home: return "המיקום ששמרתי"
    near = sorted(D.stores, key=lambda s: km(home, s["p"]))[:5]
    cities = [(s["a"].split(",")[-1].strip()) for s in near if "," in s["a"]]
    c = max(set(cities), key=cities.count) if cities else ""
    return f"אזור {c}" if c else "המיקום ששמרתי"

SETTINGS = [("set:loc", "📍 עדכון מיקום"), ("set:club", "💳 מועדוני לקוחות"), ("set:store", "⭐ הסופר הקבוע שלי"),
            ("set:remind", "⏰ תזכורת שבועית"), ("set:sav", "💰 כמה חסכתי"), ("set:help", "❓ איך משתמשים"),
            ("set:faq", "💬 שאלות ותשובות"),
            ("set:reset", "🔄 איפוס מלא")]

def help_text():
    L = ["❓ *איך משתמשים בקונה אישי*", "",
         "🛒 *מה הבוט עושה?*",
         "שולחים לי מה קונים – ואני משווה את המחיר של כל הסל בכל הסופרים הקרובים אליך, לפי מחירים ומבצעים "
         "שמתעדכנים כל שעה מכל הרשתות. אני ממליץ איפה הכי משתלם לקנות וכמה תחסוך.", "",
         "📍 *מיקום ומועדון – פעם אחת בלבד*",
         "בפעם הראשונה אני שואל מאיפה יוצאים לקניות ואם יש לך מועדון לקוחות, וזוכר את זה. "
         "בכל קנייה רק אוודא שהמיקום עדיין נכון.",
         "לשינוי: *⚙️ הגדרות* ← *📍 עדכון מיקום* או *💳 מועדוני לקוחות*.", "",
         "✍️ *איך שולחים סל?*",
         "• *רשימה:* כותבים מה קונים – _2 חלב, לחם, קילו חזה עוף_",
         "• *קבלה:* מצלמים קבלה מהסופר ושולחים (קבלה ארוכה – אפשר בכמה תמונות)",
         "• *שאלה:* _כמה עולה קוטג'?_ – ואראה איפה הכי זול לידך",
         "• *שינוי:* _תוסיף במבה_ / _תוריד את הקולה_"]
    if GROQ_KEY or OPENAI_KEY: L.append("• *הקלטה:* אפשר גם לשלוח הודעה קולית 🎤")
    L += ["", "🔘 *הכפתורים למטה*",
          "• *📍 שליחת המיקום שלי* – עדכון מהיר של המיקום",
          "• *🛒 קנייה חדשה* – מתחילים השוואה חדשה",
          "• *📷 צילום קבלה* – שולחים קבלה להשוואה",
          "• *⚙️ הגדרות* – מיקום, מועדונים, הסופר הקבוע, תזכורת, חיסכון ואיפוס", "",
          "📋 *אחרי ההשוואה*",
          "• *🧾 מה זיהיתי* – כל המוצרים והמחיר של כל אחד",
          "• *🏪 סופרים אחרים* – השוואה מלאה בין הסניפים באזור",
          "• *✏️ משהו לא נכון* – תיקון מוצר שזיהיתי לא נכון", "",
          "💰 בכל רגע אפשר לכתוב *כמה חסכתי*."]
    return "\n".join(L)

MISSING_CHAINS = ["ויקטורי", "חצי חינם", "מחסני השוק", "ח. כהן", "נתיב החסד"]   # נאספות בשרת בישראל – מוצגות כחסרות רק אם לא בנתונים

def faq_answer(k):
    D = kdata.D
    chains = sorted({s["chain"] for s in D.stores}) if D else []
    upd = f"{D.built[11:16]} {D.built[8:10]}/{D.built[5:7]}" if D and len(D.built) >= 16 else ""
    A = [
        ("🔎 מאיפה מגיעים המחירים?",
         "מהקבצים הרשמיים שכל רשת מחויבת לפרסם לפי *חוק שקיפות המחירים*: מחיר כל מוצר בכל סניף, המבצעים ומבצעי המועדון. "
         "אני מוריד אותם בעצמי מכל הרשתות – בלי פרסומות ובלי שאף רשת משלמת לי."),
        ("🏪 האם כל הרשתות מופיעות?",
         f"כרגע מופיעות *{len(chains)} רשתות* ({D and len(D.stores) or 0:,} סניפים): " + ", ".join(chains) + ".\n\n" +
         (f"עדיין חסרות: {', '.join(miss)} – הנתונים שלהן לא התעדכנו כרגע. אנחנו עובדים על זה." if (miss := [c for c in MISSING_CHAINS if c not in chains])
          else "אלה כל הרשתות הגדולות שמפרסמות מחירים 🙂")),
        ("⏱️ כמה מעודכנים המחירים?",
         f"המחירים מתעדכנים *כל שעה*, והורדה מלאה של הכל כל 4 שעות." + (f" העדכון האחרון: *{upd}*." if upd else "")),
        ("🤔 למה המחיר בסופר שונה ממה שכתוב?",
         "רוב המחירים תואמים, אבל לפעמים יש פער:\n• מבצע שתלוי רק על המדף ולא דווח\n• קופונים, הטבות אשראי או מבצעים עם מינימום קנייה (לא נכללים)\n"
         "• הרשת שינתה מחיר בשעה האחרונה\n• \"1+1\" ו\"השני ב-50%\" עדיין לא מחושבים\nאם מצאת פער – ספר לי ואבדוק 🙏"),
        ("🔥 האם מבצעים ומחירי מועדון נכללים?",
         "כן. מבצעים לכולם נכללים תמיד. מבצעי מועדון – רק לרשתות שסימנת ב*⚙️ הגדרות ← 💳 מועדוני לקוחות*. "
         "מבצעי כמות (\"3 ב-₪10\") נחשבים רק כשיש בסל מספיק יחידות."),
        ("🧭 איך הבוט בוחר סופר?",
         "אני מחשב כמה יעלה *כל הסל* בכל סניף קרוב (עד ~35 דקות נסיעה), וממליץ על הזול. "
         "אם אני מכיר את הסופר הקבוע שלך – אמליץ לעבור רק אם החיסכון לפחות ₪35 (או ₪25 כשקונים רק בשר/ירקות במשקל)."),
        ("🔒 מה קורה עם המידע שלי?",
         "אני שומר רק את מה שצריך כדי לעזור: המיקום, המועדונים והקניות הקודמות (כדי ללמוד מה אתה קונה). "
         "תמונות קבלות לא נשמרות. המידע לא נמכר ולא מועבר לאף אחד. למחיקה מלאה – כתבו *מחק אותי*."),
        ("💸 כמה זה עולה?", "כלום – הבוט חינמי לגמרי 🙂"),
    ]
    return A if k is None else A[k]

def settings_menu(to, u):
    u["step"] = "done"; save_users()
    return send_buttons(to, "⚙️ *מה תרצה לעשות?*", SETTINGS)

def settings_action(to, u, reply):
    pr = u["prefs"]
    if reply == "set:club":                     # מציגים את המועדונים הנוכחיים (✅) – לוחצים כדי להוסיף/להוריד
        u["step"] = "clubpick"; u["club_ctx"] = "later"; save_users()
        return send_buttons(to, f"💳 *מועדוני לקוחות* – לוחצים כדי לסמן/להוריד (עד {MAX_CLUBS})",
                            club_buttons(u) + [("club:none", "🚫 בלי מועדון")])
    if reply == "set:store":
        D = kdata.D; uk = usual_store(u); cur = next((s for s in D.stores if s["key"] == uk), None)
        near = sorted((s for s in D.stores if s["chain"] not in ("יילו",) and not re.search(r"\bBE\b|Be\b", s["n"])),
                      key=lambda s: km(pr["home"], s["p"]))[:6] if pr.get("home") else []
        btns = [(f"st:{s['key']}", s["n"][:30]) for s in near if not uk or s["key"] != uk] + [("st:none", "🚫 אין לי סופר קבוע")]
        return send_buttons(to, (f"⭐ הסופר הקבוע שלך: *{cur['n']}*\n\nלשנות?" if cur else "⭐ עוד אין לי סופר קבוע שלך (אני לומד אותו מהקבלות).\n\nאפשר לבחור:"), btns)
    if reply.startswith("st:"):
        k = reply[3:]
        if k == "none": u.pop("stores", None); save_users(); return send_text(to, "בסדר, השוואה תהיה מול ממוצע הסופרים באזור 👍")
        u["stores"] = {k: 1000}; save_users()
        s = next((s for s in kdata.D.stores if s["key"] == k), None)
        return send_text(to, f"⭐ רשמתי: *{s['n'] if s else 'הסופר'}* הוא הסופר הקבוע שלך 👍")
    if reply == "set:remind":
        if pr.get("remind_off"):
            pr.pop("remind_off", None); save_users()
            return send_text(to, "⏰ התזכורת השבועית *הופעלה* – אזכיר לך שבוע אחרי הקנייה 👍")
        pr["remind_off"] = True; u.pop("remind_at", None); save_users()
        return send_text(to, "⏰ התזכורת השבועית *כובתה* 👍")
    if reply == "set:sav":
        return send_text(to, savings_text(u) or "עוד לא בדקת קניות החודש 🙂")
    if reply == "set:faq":
        return send_buttons(to, "💬 *שאלות ותשובות* – על מה תרצה לשאול?", [(f"set:faq{j}", q[:40]) for j, (q, _) in enumerate(faq_answer(None))])
    if reply.startswith("set:faq"):
        q, a = faq_answer(int(reply[7:]))
        return send_buttons(to, f"*{q}*\n\n{a}", [("set:faq", "↩️ עוד שאלות")])
    if reply == "set:help":
        return send_text(to, help_text())
    if reply == "set:reset":
        return send_buttons(to, "🔄 *איפוס מלא* ימחק את כל מה שלמדתי עליך (מיקום, מועדונים, קניות קודמות).\nבטוח?",
                            [("reset_yes", "כן, לאפס"), ("reset_no", "לא, ביטול")])
    if reply == "reset_no":
        return send_text(to, "בוטל 👍")
    if reply == "reset_yes":
        return handle({"from": to, "type": "text", "text": {"body": "איפוס מלא"}})

def handle(msg):
    to = msg["from"]; u = user(to); t = msg.get("type"); pr = u["prefs"]
    reply = None
    if t == "interactive":
        it = msg["interactive"]; reply = (it.get("button_reply") or it.get("list_reply") or {}).get("id")
    text = msg.get("text", {}).get("body", "").strip() if t == "text" else ""
    if text: text = fix_command(text)                     # "איפןס מלא" → "איפוס מלא"
    print("handle", t, "step", u["step"])
    if msg.get("name"): u["name"] = msg["name"]
    u["remind_miss"] = 0

    if text in ("/stats", "סטטיסטיקה"):
        meta = USERS.setdefault("_meta", {})
        admins = set(filter(None, os.environ.get("ADMIN_CHAT", "").split(","))) | set(meta.get("admins", []))
        if not admins:                       # הראשון ששולח /stats נהיה המנהל
            meta["admins"] = [to]; save_users(); admins = {to}
            send_text(to, "רשמתי אותך כמנהל הבוט 🔐 רק אתה תוכל לראות את הנתונים.")
        if to not in admins: return send_text(to, "לא הבנתי 🙂")
        return send_text(to, stats_text())
    if text in ("/selftest", "בדיקה עצמית") and to in admins():
        typing(to); probs, summ = selftest()
        return send_text(to, ("✅ הבדיקה עברה – אותה רשימה, אותה תוצאה, כמויות תקינות" if not probs else
                              "🧪 *נמצאה בעיה*\n\n" + "\n".join("⚠️ " + p for p in probs[:12])) + ("\n\n" + summ if summ else ""))
    if text in ("אבחון מחדש", "אבחון", "איפוס", "איפוס מלא"):
        full = text == "איפוס מלא"
        u["prefs"] = {}; u.pop("remind_at", None); u.pop("items", None); u.pop("last", None)
        if full:
            u["hist"] = []
            for k in ("receipts", "last_receipt", "stores", "rbuf_t", "note", "cam_tip", "missed", "pending"): u.pop(k, None)
        save_users()
        return ask(to, "loc", u, intro=("🔄 " if not full else "🔄🧹 ") + welcome(u) + "\n\n")
    if re.fullmatch(r"(כמה חסכתי|חיסכון|סיכום|סיכום חיסכון|כמה חסכתי\?|/savings)", text):
        return send_text(to, savings_text(u) or "עוד לא בדקת קניות החודש 🙂 שלחו רשימה או קבלה ואתחיל לספור.")
    if text == "מחק אותי":
        USERS.pop(to, None); save_users()
        return send_text(to, "מחקתי את כל המידע שלך 🗑️ כדי להתחיל מחדש כתבו היי.")
    if text == M_CAM:
        reply, text = "cam", ""
    elif text == M_SET:
        text = "הגדרות"
    if text == M_NEW or (GREET.match(text) and len(text) <= 25) or (text == "/start" and all(k in pr for k in PROFILE_KEYS)):
        if all(k in pr for k in PROFILE_KEYS):         # משתמש חוזר – רק מוודאים שהמיקום עדיין נכון
            u["step"] = "loccheck"; save_users()
            return send_buttons(to, f"היי{' ' + u['name'] if u.get('name') else ''}! 👋\n📍 יוצאים לקניות מ{place_name(pr['home'])}?",
                                [("loc_ok", "✅ כן"), ("loc_new", "📍 ממקום אחר")])
        return ask(to, "loc", u, intro=welcome(u) + "\n\n")
    if text in ("הגדרות", "/settings"):
        return settings_menu(to, u)
    if reply == "loc_ok":
        return ask(to, "basket", u, intro="מעולה, סיימנו 🎉\n\n")
    if reply in ("loc_new", "set:loc"):
        u["step"] = "loccheck" if reply == "loc_new" else "setloc"; save_users()
        return send_location_request(to, "📍 לוחצים על *שליחת המיקום שלי* למטה (או 📎 ← מיקום כדי לבחור מקום אחר במפה)")
    if reply and (reply.startswith("set:") or reply.startswith("st:") or reply in ("reset_yes", "reset_no")):
        return settings_action(to, u, reply)
    if text in ("התחל", "מחדש", "/start") or u["step"] == "new":
        if u["step"] == "new" and pr.get("home"):
            u["step"] = "keep"; save_users()
            return send_buttons(to, welcome(u) + "\n\nיש לי את ההעדפות שלך מהפעם הקודמת.", [("keep", "להמשיך איתן"), ("reset", "להתחיל מחדש")])
        return ask(to, "loc", u, intro=welcome(u) + "\n\n")
    if reply == "keep": return ask(to, "basket", u)
    if reply == "reset": u["prefs"] = {}; return ask(to, "loc", u)

    if t == "location":
        pr["home"] = [msg["location"]["latitude"], msg["location"]["longitude"]]
        if u["step"] == "loc":           # בלי שאלת "איך מגיעים": ברכב, כל הסניפים הקרובים (עלות הדלק בחישוב)
            pr.setdefault("mode", "car"); pr.setdefault("maxMin", 999); pr.setdefault("brand", "cheap")
            return ask(to, "clubq", u)
        if u["step"] == "loccheck":      # משתמש חוזר שעדכן מיקום – ישר לקנייה
            return ask(to, "basket", u, intro=f"עדכנתי: {place_name(pr['home'])} 👍\nמעולה, סיימנו 🎉\n\n")
        u["step"] = "done"; send_text(to, f"עדכנתי את המיקום: {place_name(pr['home'])} 👍"); save_users()
        if u.get("items"): return finish(to, u)
        return
    if u["step"] == "loc":
        return send_location_request(to, "כדי לדעת מאיפה אתה יוצא, צריך מיקום 📍 לוחצים על הכפתור:")
    if reply in ("car", "walk", "bus"):   # בלי שאלת טווח: ברכב – כל הסניפים הקרובים (עלות הנסיעה כבר בחישוב), ברגל/בתחבורה ציבורית – טווח סביר
        pr["mode"] = reply; pr["maxMin"] = {"car": 999, "walk": 20, "bus": 45}[reply]
        pr.setdefault("brand", "cheap"); return ask(to, "clubq", u)   # בלי שאלת "כמה אנשים" – נשאל רק אם יבקשו "סל שבועי"
    if reply in ("r5", "r15", "rall"): pr["maxMin"] = {"r5": 6, "r15": 16, "rall": 999}[reply]; return ask(to, "hh", u)
    if reply in HH and u["step"] == "hh": pr["hh"] = reply; pr.setdefault("brand", "cheap"); return ask(to, "clubq", u)
    if reply in ("bmine", "bcheap", "bdep"):
        pr["brand"] = reply[1:]; return ask(to, "kosher", u)
    if reply in ("kreg", "kmeh") and u["step"] == "kosher":
        pr["kosher"] = reply[1:]; pr.setdefault("splitMin", SPLIT_MIN); pr.pop("remind_off", None)
        return ask(to, "basket", u, intro="מעולה, סיימנו 🎉\n\n")
    if reply in ("s0", "s30", "sno"): pr["splitMin"] = {"s0": SPLIT_MIN, "s30": SPLIT_MIN, "sno": -1}[reply]; return ask(to, "basket", u)
    if reply == "clubyes":
        pr["clubs"] = []; u["step"] = "clubpick"; save_users()
        return send_buttons(to, f"באילו רשתות? (אפשר לבחור עד {MAX_CLUBS})", club_buttons(u))
    if reply == "clubdone": return clubs_done(to, u)
    if reply and reply.startswith("club:"):
        c = reply[5:]
        if c == "none":
            pr["clubs"] = []; return clubs_done(to, u)
        cl = pr.setdefault("clubs", [])
        if c in cl: cl.remove(c)
        else: cl.append(c)
        save_users()
        if len(cl) >= MAX_CLUBS: return clubs_done(to, u)
        if is_tg(to) and msg.get("mid"):   # מסמנים ✅ על אותה הודעה, בלי הודעה חדשה
            return tg_call("editMessageReplyMarkup", {"chat_id": to[3:], "message_id": msg["mid"],
                           "reply_markup": {"inline_keyboard": [[tg_btn(i, t) for i, t in club_buttons(u)[k:k + 2]]
                                                                for k in range(0, len(club_buttons(u)), 2)]}})
        return send_buttons(to, "עוד רשת? כשמסיימים – *סיימתי*", club_buttons(u))
    if reply in ("kreg", "kmeh"):
        pr["kosher"] = reply[1:]; save_users(); return send_text(to, "רשמתי 👍")
    if reply == "fix" or (reply and reply.startswith(("fix:", "alt:", "del:"))):
        its = u.get("items") or []
        if not its: return send_text(to, "אין כרגע סל לתקן – כתבו *היי* כדי להתחיל 🙂")
        if reply == "fix":
            btns = [(f"fix:{k}", f"{k + 1}. {x['src'][:28]}") for k, x in enumerate(its[:20])] + [("fixdone", "✅ הכל נכון")]
            return send_buttons(to, "על איזה מוצר לתקן?", btns)
        k = int(reply.split(":")[1])
        if k >= len(its): return
        x = its[k]
        if reply.startswith("fix:"):
            d = kdata.D
            opts = [(f"alt:{k}:{j}", d.items[d.code2i[c]][1][:40]) for j, c in enumerate(x.get("c", [])) if c in d.code2i]
            if not opts:  # אין חלופות שמורות – מחפשים מחדש
                x["c"] = [d.items[i][0] for i in d.search(x["src"], 6) if d.items[i][0] != x.get("code")][:4]; save_users()
                opts = [(f"alt:{k}:{j}", d.items[d.code2i[c]][1][:40]) for j, c in enumerate(x["c"])]
            return send_buttons(to, f"*{x['src']}* – התכוונת ל:", opts + [(f"del:{k}", "🗑️ להוריד מהסל"), ("fix", "↩️ חזרה")])
        if reply.startswith("alt:"):
            j = int(reply.split(":")[2]); d = kdata.D
            c = x.get("c", [])[j] if j < len(x.get("c", [])) else None
            if c not in d.code2i: return
            old_code = x["code"]
            its[k] = dict(item_of(d, d.code2i[c], x["qty"], x["src"]), c=[old_code] + [z for z in x.get("c", []) if z != c][:3])
        else:
            its.pop(k)
            if not its: u["items"] = []; save_users(); return send_text(to, "הסל ריק עכשיו 🙂 אפשר לכתוב רשימה חדשה.")
        typing(to)
        return finish(to, u, redo=True)
    if reply == "seen":
        if not u.get("items"): return send_text(to, "אין כרגע סל – כתבו *היי* כדי להתחיל 🙂")
        txt = understood_text(u, u.get("missed") or [])
        parts = [""]                                   # סל גדול – מפצלים להודעות של עד ~3500 תווים (מגבלת טלגרם)
        for line in txt.split("\n"):
            if len(parts[-1]) + len(line) > 3500: parts.append("")
            parts[-1] += line + "\n"
        for ptxt in parts[:-1]: send_text(to, ptxt.rstrip())
        return send_buttons(to, parts[-1].rstrip(), [("fix", "✏️ משהו לא נכון")])
    if reply == "fixdone":
        return send_text(to, "מעולה 👍")
    if reply in ("det", "oth", "spl"):
        txt = (u.get("last") or {}).get({"det": "details", "oth": "others", "spl": "split"}[reply])
        return send_text(to, txt or "אין לי עדיין חישוב להציג – כתבו *היי* כדי להתחיל קנייה 🙂")
    if reply == "remind_off":
        pr["remind_off"] = True; u.pop("remind_at", None); save_users()
        return send_text(to, "בסדר, לא אשלח יותר תזכורות 👍 (כדי להפעיל שוב – כתבו *הגדרות*)")
    if reply == "mine":
        L = learned(u); u["items"] = [{k: x[k] for k in ("src", "code", "name", "qty")} for x in L]
        send_text(to, "*הסל הקבוע שלך:*\n" + "\n".join(f"• {x['name']}" + (f" ×{x['qty']:g}" if x["qty"] != 1 else "") for x in L[:15]))
        return finish(to, u)
    if reply == "cam":
        u["step"] = "basket"
        tip = "" if u.get("cam_tip") else "\n\n💡 _שימו את הקבלה על משטח, באור טוב. קבלה ארוכה – אפשר לצלם בכמה תמונות ואחבר אותן._"
        u["cam_tip"] = 1; save_users()
        return send_text(to, "צלם את הקבלה (או רשימה כתובה) ושלח כאן 📷" + tip)
    if reply == "list":
        u["step"] = "basket"; save_users(); return send_text(to, "כתוב מה קונים, למשל:\n_2 חלב, לחם, קוטג', 1.5 קג עגבניות_")
    if reply == "weekly" and pr.get("hh"): reply = pr["hh"]
    elif reply == "weekly":
        u["step"] = "hhw"; save_users(); return send_buttons(to, "*לכמה אנשים?*", [("hh12", "1–2"), ("hh34", "3–4"), ("hh5", "5+")])
    if reply in HH:
        pr.setdefault("hh", reply); f = HH[reply]
        u["items"] = weekly_items(f)
        send_text(to, f"הכנתי סל שבועי רגיל: {len(u['items'])} מוצרים (חלב, לחם, גבינות, ירקות, פירות ועוד)."); return finish(to, u)
    if reply and reply.startswith("v:") and u.get("vague") and u.get("vague_kind") in VAGUE:
        opts = VAGUE[u["vague_kind"]]; j = int(reply[2:])
        if j < len(opts):
            pv = u["vague"]; pv["items"][pv["k"]]["name"] = opts[j][0]; pv["k"] += 1
        return ask_vague(to, u)
    if reply in ("addf", "noadd"):   # לא תלוי ב-step – כדי שלחיצה לא "תיתקע" (למשל אם השרת עלה מחדש באמצע)
        pend = u.pop("pending", [])
        if reply == "addf": u.setdefault("items", []).extend({k: x.get(k, "") for k in ("src", "code", "name", "qty")} for x in pend)
        if not u.get("items"): return send_text(to, "אין כרגע סל – כתבו *היי* כדי להתחיל 🙂")
        typing(to)
        return finish(to, u)

    if t == "voice":
        if not (GROQ_KEY or OPENAI_KEY):
            return send_text(to, "הודעות קוליות עוד לא מופעלות 🎤 בינתיים אפשר לכתוב את הרשימה ✍️")
        if msg["voice"].get("sec", 0) > 180:
            return send_text(to, "ההקלטה ארוכה מדי 🙂 עד 3 דקות בבקשה.")
        try: audio, _ = download_media(msg["voice"]["id"])
        except Exception as e:
            print("voice download error", e, flush=True); return send_text(to, "לא הצלחתי להוריד את ההקלטה 🙁 נסו שוב.")
        heard = transcribe(audio)
        if not heard: return send_text(to, "לא הצלחתי להבין את ההקלטה 🙁 נסו שוב בשקט יותר, או כתבו ✍️")
        send_text(to, f"🎤 _שמעתי:_ {heard}")
        return handle({**msg, "type": "text", "text": {"body": heard}})
    if t == "image":
        if not CLAUDE_KEY:
            return send_text(to, "קריאת קבלות עוד לא מופעלת. אפשר לכתוב את הרשימה בהודעה ✍️")
        typing(to) if is_tg(to) else send_text(to, "קורא את הקבלה… ⏳")
        try:
            img, mime = download_media(msg["image"]["id"])
        except Exception as e:
            print("media download error", e, flush=True)
            return send_text(to, "לא הצלחתי להוריד את התמונה 🙁 נסה לשלוח שוב.")
        print("image bytes", len(img), mime, flush=True)
        if mime == "unsupported":
            return send_text(to, "את סוג הקובץ הזה אני לא יודע לקרוא 🙁 שלחו את הקבלה כ*תמונה* רגילה (לא כקובץ).")
        with _img_locks.setdefault(to, threading.Lock()):     # כמה תמונות ביחד (אלבום) – אחת אחרי השנייה
            return got_receipt(to, u, img, mime)
    if text:
        if not all(k in pr for k in PROFILE_KEYS):
            return send_text(to, "בוא נסיים קודם את השאלות 🙂 אפשר ללחוץ על הכפתורים למעלה.")
        if CLAUDE_KEY and not (text.count("\n") >= 3 or re.search(r"\d+\.\d{2}", text)):
            r = understand(text, u)
            if r: return act_on(to, u, r)
        items = None
        if CLAUDE_KEY and (text.count("\n") >= 3 or re.search(r"\d+\.\d{2}", text)):
            items = extract_items(text=text)  # רשימה ארוכה / קבלה מועתקת – Claude מסדר אותה
        return got_items(to, u, items or parse_list(text))
    if reply: print("unhandled button", reply, "step", u.get("step"), flush=True)
    send_text(to, "לא הבנתי 🙂 אפשר לשלוח רשימה, קבלה, או לכתוב *הגדרות*.")


def tg_to_msg(up):
    """ממיר עדכון מטלגרם להודעה בפורמט של ווטסאפ, כדי שאותה לוגיקה תעבוד לשניהם"""
    if "callback_query" in up:
        cq = up["callback_query"]; tg_call("answerCallbackQuery", {"callback_query_id": cq["id"]})
        return {"from": f"tg:{cq['message']['chat']['id']}", "id": "tgc" + cq["id"], "type": "interactive",
                "name": (cq.get("from") or {}).get("first_name", ""), "mid": cq["message"].get("message_id"),
                "interactive": {"button_reply": {"id": cq.get("data", "")}}}
    m = up.get("message") or {}
    if not m: return None
    base = {"from": f"tg:{m['chat']['id']}", "id": f"tg{m['chat']['id']}_{m['message_id']}", "name": (m.get("from") or {}).get("first_name", "")}
    if "location" in m:
        return {**base, "type": "location", "location": {"latitude": m["location"]["latitude"], "longitude": m["location"]["longitude"]}}
    if "photo" in m:
        return {**base, "type": "image", "image": {"id": "tg:" + m["photo"][-1]["file_id"]}}
    doc = m.get("document") or {}
    if str(doc.get("mime_type", "")).startswith("image/"):  # תמונה שנשלחה כ"קובץ"
        return {**base, "type": "image", "image": {"id": "tg:" + doc["file_id"], "mime": doc["mime_type"]}}
    if "text" in m:
        return {**base, "type": "text", "text": {"body": m["text"]}}
    v = m.get("voice") or m.get("audio")
    if v:
        return {**base, "type": "voice", "voice": {"id": "tg:" + v["file_id"], "sec": v.get("duration", 0)}}
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
    done = threading.Event()
    def keep_typing():   # בטלגרם "מקליד..." נעלם אחרי 5 שניות – מחדשים כל 4 שניות כל עוד עובדים
        if done.wait(1.0): return
        while not done.is_set():
            typing(m["from"])
            if done.wait(4.0): return
    if is_tg(m["from"]): threading.Thread(target=keep_typing, daemon=True).start()
    try: handle(m)
    except Exception as ex:
        import traceback; traceback.print_exc()
        try: send_text(m["from"], "אופס, משהו השתבש אצלי 🙈 נסה שוב בעוד רגע.")
        except Exception: pass
    finally:
        done.set()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    kdata.load(force_download=os.environ.get("DATA_URL", "x") != ""); kdata.start_refresher()
    threading.Thread(target=_reminder_loop, daemon=True).start()
    import signal
    def _bye(*_):  # Render שולח SIGTERM לפני עדכון – שומרים את הזיכרון לפני שיוצאים
        if _dirty.is_set(): push_users()
        os._exit(0)
    signal.signal(signal.SIGTERM, _bye)
    print("קונה אישי מאזין בפורט", port)
    if TG_TOKEN and PUBLIC_URL:
        try:
            print("חיבור טלגרם:", tg_call("setWebhook", {"url": PUBLIC_URL.rstrip("/") + "/telegram", "drop_pending_updates": True}))
            tg_call("setMyDescription", {"description": "היי! אני קונה אישי 🛒 אני משווה מחירים בין הסופרים שלידך ואומר לך איפה הכי משתלם לקנות – כולל מבצעים ודלק. פשוט כותבים לי היי 👋"})
            tg_call("setMyShortDescription", {"short_description": "משווה מחירי סופרים לידך ואומר איפה הכי משתלם 🛒"})
            tg_call("setMyCommands", {"commands": [{"command": "start", "description": "להתחיל / קנייה חדשה"},
                                                   {"command": "settings", "description": "לשנות מיקום והעדפות"},
                                                   {"command": "savings", "description": "כמה חסכתי החודש"}]})
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
