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
import json, math, os, re, sys, threading, time, base64, urllib.request, urllib.error, collections
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

# ---- סנכרון בין שרתים ----
# בכל עדכון קוד Render מעלה שרת חדש בזמן שהישן עוד רץ. השרת החדש טוען את הזיכרון מ-GitHub –
# ובלי סנכרון הוא עובד עם גרסה ישנה (מיקום קודם, קניות שכבר נמחקו באיפוס) ודורס את מה שהישן שמר.
# הפתרון: לכל משתמש חותמת זמן "_t" של השינוי האחרון. לפני כל שמירה, וגם כל 15 שניות, ממזגים עם GitHub –
# לכל משתמש נשארת הגרסה החדשה יותר. משתמש שנמחק ("מחק אותי") נרשם ב-_meta.deleted כדי שלא יחזור.
SYNC_SEC = 15

def touch(to):
    """מסמן שהמשתמש השתנה עכשיו (הגרסה הזו מנצחת במיזוג)"""
    u = USERS.get(to)
    if isinstance(u, dict): u["_t"] = time.time()

def forget_user(to):
    USERS.pop(to, None)
    USERS.setdefault("_meta", {}).setdefault("deleted", {})[to] = time.time()

def merge_users(remote):
    """ממזג את הזיכרון מ-GitHub לזיכרון המקומי: לכל משתמש – הגרסה עם "_t" החדש יותר. מחזיר כמה משתמשים עודכנו מבחוץ"""
    if not isinstance(remote, dict): return 0
    n = 0
    with _lock:
        meta = USERS.setdefault("_meta", {}); rmeta = remote.get("_meta") or {}
        dele = meta.setdefault("deleted", {})
        for k, t in (rmeta.get("deleted") or {}).items(): dele[k] = max(t, dele.get(k, 0))
        for k, v in rmeta.items():
            if k != "deleted" and k not in meta: meta[k] = v
        for k, rv in remote.items():
            if k.startswith("_") or not isinstance(rv, dict): continue
            rt = rv.get("_t", 0); lv = USERS.get(k)
            if dele.get(k, -1) >= rt: continue                            # נמחק אחרי הגרסה הזו
            if not isinstance(lv, dict): USERS[k] = rv; n += 1
            elif rt > lv.get("_t", 0): lv.clear(); lv.update(rv); n += 1  # במקום – כדי שמי שמחזיק את האובייקט יראה את העדכון
        for k in [k for k, v in USERS.items() if not k.startswith("_") and isinstance(v, dict) and dele.get(k, -1) >= v.get("_t", 0)]:
            USERS.pop(k, None); n += 1
    return n

def pull_users():
    if not _remote["ok"]: return 0
    r = gh_users("GET"); _remote["sha"] = r["sha"]
    n = merge_users(json.loads(base64.b64decode(r["content"]).decode("utf-8") or "{}"))
    if n: print(f"סנכרון: {n} משתמשים עודכנו מהזיכרון ב-GitHub", flush=True)
    return n

def push_users():
    if not _remote["ok"]: return
    for attempt in range(3):
        try:
            try: pull_users()                                   # קודם ממזגים – לא דורסים שינוי חדש יותר משרת אחר
            except urllib.error.HTTPError as e:
                if e.code != 404: raise
                _remote["sha"] = None
            with _lock: content = json.dumps(USERS, ensure_ascii=False, separators=(",", ":"))
            body = {"message": "update users", "content": base64.b64encode(content.encode()).decode()}
            if _remote["sha"]: body["sha"] = _remote["sha"]
            _remote["sha"] = gh_users("PUT", body)["content"]["sha"]; return
        except urllib.error.HTTPError as e:
            if e.code not in (409, 422):  # 409/422 = sha לא עדכני – מנסים שוב (עם מיזוג)
                print("שמירת זיכרון נכשלה:", e.code, e.read()[:200], flush=True); time.sleep(5)
        except Exception as e:
            print("שמירת זיכרון נכשלה:", e, flush=True); time.sleep(5)
    _dirty.set()

def _users_writer():
    while True:
        if _dirty.wait(SYNC_SEC):
            time.sleep(3); _dirty.clear(); push_users()
        else:
            try: pull_users()                                   # קולטים שינויים משרת אחר (למשל הישן לפני שנכבה)
            except Exception as e: print("סנכרון נכשל:", e, flush=True)

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
CAM_URL = (PUBLIC_URL.rstrip("/") + "/cam") if PUBLIC_URL.startswith("https://") else ""
MENU = {"keyboard": [[{"text": "📍 שליחת המיקום שלי", "request_location": True}],
                     [{"text": M_NEW}, ({"text": M_CAM, "web_app": {"url": CAM_URL}} if CAM_URL else {"text": M_CAM})], [{"text": M_SET}]],
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
    if i == "cam" and CAM_URL: return {"text": t, "web_app": {"url": CAM_URL}}    # פותח את מסך הצילום (מצלמה / גלריה) – אורי 20/10
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

RAW_IMG = {}          # תמונות שהגיעו ממסך הצילום (Mini App) – נמחקות אחרי הקריאה

def download_media(media_id):
    if media_id.startswith("raw:"):
        data, mime = RAW_IMG.pop(media_id[4:], (b"", "unsupported"))
        return data, (sniff_mime(data) if data else mime)
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
                  'For a receipt also add "price" = the total paid for that line in shekels (after per-line discount if printed on the line), '
                  'or null if not readable / a shopping list. Also add "code" = the barcode / item code printed on that line (digits only, '
                  'usually 7–13 digits at the start or end of the line, e.g. "7290000208428"), or null if none is printed or readable. '
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
        row = {"name": str(x["name"]).strip(), "qty": q if 0 < q < 100 else 1}
        try:
            pr = float(x.get("price")) if x.get("price") not in (None, "") else None
            if pr and 0 < pr < 2000: row["price"] = pr
        except Exception: pass
        code = re.sub(r"\D", "", str(x.get("code") or ""))
        if 4 <= len(code) <= 14: row["code"] = code
        out.append(row)
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
STORE_FORMATS = {"דיל", "שלי", "יש", "אקספרס", "BE", "be", "גוד", "יוניברס", "סיטי", "היפר", "מרקט", "אקסטרה", "בכיף", "AM", "PM"}

def match_store(text, home):
    """מזהה את הסניף מתוך הכיתוב בקבלה (רשת + סניף/כתובת), מבין הסניפים באזור"""
    D = kdata.D
    if not text or not home: return None
    norm = lambda t: re.sub(r"קריית", "קרית", t)                          # "קריית אתא" בקבלה = "קרית אתא" בנתונים
    q = {kdata.stem(w) for w in kdata.toks(norm(text))}
    fmt_q = {f for f in STORE_FORMATS if f in q}
    best = None
    for s in D.stores:
        d = km(home, s["p"])
        if d > REG_MAX_KM: continue                          # סניף רחוק מהבית – לא נרשם כסופר הקבוע
        ch = {kdata.stem(w) for w in kdata.toks(s["chain"])}
        if not ch or not ch <= q: continue                   # הרשת חייבת להופיע בקבלה
        nm = {kdata.stem(w) for w in kdata.toks(norm(s["n"]))} - ch
        rest = nm | {kdata.stem(w) for w in kdata.toks(norm(s["a"]))} - ch
        sc = len(rest & q) - 0.5 * len(nm - q)                 # מילה בשם הסניף שלא מופיעה בקבלה (למשל BE) – פחות סביר
        fmt_s = {f for f in STORE_FORMATS if f in nm}
        if fmt_q and fmt_s: sc += 1.5 if fmt_q & fmt_s else -1.5   # "שופרסל דיל" בקבלה → סניף דיל, לא BE/אקספרס
        elif fmt_s & {"BE", "be"} and not fmt_q: sc -= 1           # בית מרקחת – רק אם כתוב בקבלה
        key = (sc, -d)
        if best is None or key > best[0]: best = (key, s)
    return best[1]["key"] if best else None

REG_MAX_KM = 15   # "סופר קבוע" רק אם הוא ליד המיקום הנוכחי (~25 דק' ברכב). משתמש שעבר מקום – הסופר הישן לא נחשב

def usual_store(u):
    """הסניף שהמשתמש קונה בו הכי הרבה (לפי הקבלות) – רק אם הוא קרוב למיקום הנוכחי"""
    c = u.get("stores") or {}
    home = (u.get("prefs") or {}).get("home")
    pos = {s["key"]: s["p"] for s in kdata.D.stores}
    c = {k: v for k, v in c.items() if k in pos and (not home or km(home, pos[k]) <= REG_MAX_KM)}
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
               "קישואים": "P18", "חציל": "P17", "חצילים": "P17", "כרובית": "P21", "תפוח עץ": "P30", "תפוחים": "P30", "תפוחי עץ": "P30",
               "בצל אדום": "P11", "בצל סגול": "P11", "בטטות": "P15", "אבוקדו": "P29",
               "חזה עוף": "M01", "פרגיות": "M03", "כרעיים": "M04", "כרעיים עוף": "M04", "שוקיים": "M05", "שוקיים עוף": "M05",
               "כנפיים": "M06", "כנפיים עוף": "M06", "עוף שלם": "M07", "בשר טחון": "M08", "בשר בקר טחון": "M08", "אנטריקוט": "M10",
               "סלמון": "M11", "פילה סלמון": "M11", "אמנון": "M12", "מושט": "M12", "דניס": "M13", "לברק": "M14",
               "תפוא": "P12", "תפו\"א": "P12", "תפוא לבן": "P12", "תפו\"א לבן": "P12", "תפוח אדמה לבן": "P12", "תפוחי אדמה לבנים": "P12",
               "תפוא אדום": "P13", "תפו\"א אדום": "P13", "חזה עוף טרי": "M01", "עוף שלם טרי": "M07",
               "צלי כתף": "M09", "כתף בקר": "M09", "בשר לגולאש": "M09", "גולאש": "M09", "בשר בקר לבישול": "M09", "בשר לבישול": "M09",
               "בשר לצלי": "M09", "שניצל עוף": "M02", "שניצל טרי": "M02", "בורי": "M15", "קרפיון": "M16", "בקלה": "M17", "פילה בקלה": "M17",
               "הייק": "M17", "פילה הייק": "M17", "פילה אמנון": "M22", "שניצל": "M02", "שניצלים": "M02"}
# גבינות (בולגרית/צפתית/פטה) – בכוונה בלי כינוי לפריט במשקל: רוב הקונים מתכוונים לגביע של 250 גרם, לא לק"ג מהדלפק
# שם אחר שבו המוצר מופיע ברשתות ("שקיות זבל" = "שקיות אשפה")
SEARCH_SYN = {"שקיות זבל": "שקיות אשפה", "נייר אלומיניום": "נייר כסף", "חלב סויה": "משקה סויה", "חלב שקדים": "משקה שקדים",
              "חלב שיבולת שועל": "משקה שיבולת שועל", "חלב אורז": "משקה אורז", "חלות": "חלה", "מלפפון חמוץ": "מלפפונים במלח",
              "מלפפונים חמוצים": "מלפפונים במלח", "חמוצים": "מלפפונים במלח", "תירס שימורים": "תירס גרעינים",
              "שימורי תירס": "תירס גרעינים", "תירס בקופסה": "תירס גרעינים", "ירקות קפואים": "ירקות מוקפאים", "תותים": "תות שדה",
              "ניקוי רצפות": "נוזל לניקוי רצפות", "ציפס קפוא": "צ'יפס קפוא", "וניל": "סוכר וניל", "פרורי לחם": "פירורי לחם",
              "מטהר אוויר": "מטהר אויר"}

WFB_SKIP = ("מתובל", "המבורגר", "קציצ", "קבב", "שניצל", "נקניק", "שיפוד", "צמחי", "טבעוני", "מעושן", "ממולא", "מבושל", "מוכן")

def weighed_fb(name):
    """מוצר ארוז שהוא בעצם מוצר בסיסי במשקל ("בשר בקר טחון קפוא 500 נתח") → (קוד הפריט השקיל, ק"ג באריזה).
    משמש כחלופה כשבסניף אין את המותג/האריזה: בשר טחון יש כמעט בכל סופר – במשקל."""
    if any(w in name for w in WFB_SKIP): return None
    t = [w for w in kdata.toks(re.sub(r"(\D)(\d)", r"\1 \2", name))]
    best = None
    for k, code in BASIC_ALIAS.items():
        if code[:1] not in ("M",): continue
        kt = kdata.toks(k)
        if t[:len(kt)] == kt and (best is None or len(kt) > len(kdata.toks(best[0]))): best = (k, code)
    if not best: return None
    g = kdata.parse_size(name)
    if g is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*ק(?![א-ת])", name)
        if m: g = float(m.group(1)) * 1000
        else:
            m = re.search(r"(?<!\d)(\d{3,4})(?!\d)", name)
            g = float(m.group(1)) if m and 100 <= float(m.group(1)) <= 3000 else None
    return (best[1], g / 1000) if g else None

def basic_alias(name):
    if re.search(r"ביצ", name) and re.search(r"(?<!\d)(1[8-9]|[2-9]\d)(?!\d)", name): return None   # "ביצים 30" = תבנית של 30, לא 12
    n = re.sub(r'(\d+(\.\d+)?|ק"ג|קג|קילו|ק\'|גרם|גר\'?|יחידות|יח\'?|תבנית|ארוז(ה|ים)?|טרי(ים|יה|ה)?|רגיל(ה|ים)?|[–\-:,()])', " ", name)
    return BASIC_ALIAS.get(" ".join(n.split()))

def generic_pick(name, cand):
    """לשם כללי (בלי מותג/גודל): האפשרות הראשונה שכל המילים שנכתבו מופיעות בה, והשם שלה מתחיל באותה מילה ("פסטה" → "פסטה ספגטי אסם")"""
    d = kdata.D; q = [kdata.stem(w) for w in kdata.toks(name) if not re.fullmatch(r"[\d.]+", w)]
    if not q or len(q) > 2: return None
    for k in cand:
        t = [kdata.stem(w) for w in kdata.toks(d.items[k][1])]
        if t and t[0] == q[0] and set(q) <= set(t): return k
    return None

def common_first(name, c):
    """שם כללי קצר ("תירס שימורים", "סבון ידיים"): אם המועמד הראשון נמכר במעט סניפים לעומת מועמד אחר מאותו סוג – הנפוץ קודם.
    (בלי זה נבחר לפעמים מוצר נדיר, והסל יוצא "חסר בסניף" כמעט בכל מקום.)"""
    d = kdata.D; t = kdata.toks(name)
    # גם מוצר בתפזורת (W) נדיר – "עמק" הותאם ל"עמק 28% חריץ (לק"ג)" שנמכר ב-1% מהסניפים (19/10). לא ירקות/בשר מאוחדים (P/M/E)
    if not c or len(t) > 3 or any(ch.isdigit() for ch in name) or not (d.items[c[0]][0][:1].isdigit() or d.items[c[0]][0][:2] == "W:"): return c
    q = {kdata.stem(w) for w in t}
    q |= {kdata.stem(w) for w in kdata.toks(SEARCH_SYN.get(" ".join(t), ""))}
    first = d.items[c[0]][0][:1]
    def kind(j): return {kdata.stem(w) for w in kdata.toks(d.items[j][1])}
    same = [j for j in c[:6] if (d.items[j][0][:1].isdigit() or d.items[j][0][:2] == "W:") and len(q & kind(j)) >= min(2, len(q & kind(c[0])))]
    if not same: return c
    top = max(same, key=lambda j: d.items[j][2])
    if d.items[c[0]][2] < 0.25 * d.items[top][2]:
        return [top] + [j for j in c if j != top]
    return c

MATCH_MEM_MAX = 6000

def barcode_item(d, code):
    """המוצר לפי הברקוד שמודפס בקבלה. רק ברקוד אמיתי (7+ ספרות) – קודים קצרים הם קוד פנימי של הרשת (ירקות, דלפק)."""
    if not code or len(code) < 7: return None
    for c in (code, code.lstrip("0")):
        i = d.code2i.get(c)
        if i is not None and i not in d.dlv_only(): return i
    return None

def mkey(name): return " ".join(kdata.toks(name)).strip()

def match_items(items, u=None):
    """לכל שם – המוצר במאגר. החלטה שכבר התקבלה (של Claude, או תיקון של המשתמש) נשמרת: אותו שם → תמיד אותו מוצר.
    (בלי זה Claude בחר לפעמים מוצר אחר לאותה רשימה, והמחיר "קפץ" בין ריצות – בדיקה יומית 13/10)"""
    d = kdata.D
    mem = USERS.setdefault("_meta", {}).setdefault("match_mem", {})
    mine_m = (u or {}).get("my_match") or {}
    def cands(name):
        alt = SEARCH_SYN.get(" ".join(kdata.toks(name)))
        c = d.search(alt) if alt else []
        c += [j for j in d.search(name) if j not in c]
        c = common_first(name, c)
        m = re.search(r"(?<!\d)(1[8-9]|[2-9]\d)(?!\d)", name) if re.search(r"ביצ", name) else None
        if m:                                            # "ביצים 30" – קודם תבניות של 30 (החיפוש הרגיל מעדיף את ה-12 הנפוצה)
            pk = sorted((j for j in d.search(name, 60) if m.group(1) in d.itoks[j] and d.items[j][1].startswith("ביצ")
                         and not re.search(r"אומגה|חופש|אורגני", d.items[j][1])), key=lambda j: -d.items[j][2])   # הרגילה והנפוצה קודם
            c = pk + [j for j in c if j not in pk]
        t = kdata.toks(name)
        if len(t) > 1:                                   # "חטיף ברנפלקס" – גם בלי המילה הכללית הראשונה
            c += [j for j in d.search(" ".join(t[1:]), 3) if j not in c]
        return c
    cand = [cands(it["name"]) for it in items]
    for k, it in enumerate(items):
        a = d.code2i.get(basic_alias(it["name"]) or "")
        if a is not None: cand[k] = [a]                 # אין מה לבחור – זה הפריט הרגיל
        bc = barcode_item(d, it.get("code"))
        if bc is not None: cand[k] = [bc]; it["_bc"] = True               # ברקוד מהקבלה = בדיוק אותו מוצר (אורי 20/10: "רוטב עגבניות 22%" הותאם לרוטב לפיצה)
    picks = [c[0] if c else None for c in cand]
    fixed = [False] * len(items)
    for k, it in enumerate(items):
        if len(cand[k]) == 1: fixed[k] = True; continue
        code = mine_m.get(mkey(it["name"])) or mem.get(mkey(it["name"]))
        j = d.code2i.get(code) if code else None
        if j is not None:
            picks[k] = j; fixed[k] = True
            cand[k] = [j] + [x for x in cand[k] if x != j]
    ask = [k for k in range(len(items)) if not fixed[k] and len(cand[k]) > 1]
    if CLAUDE_KEY and ask:
        sub = [{"i": n, "item": items[k]["name"], "options": [d.items[x][1] for x in cand[k]]} for n, k in enumerate(ask)]
        res = claude_json("Match each Israeli supermarket item to the option that is the SAME product (type, brand if given, similar size). "
                          "A generic name with no brand/size (e.g. 'פסטה', 'אורז', 'שמן') matches the most standard everyday option of that kind – never null for it. "
                          "Null only if no option is that kind of product. Reply only a JSON array like [{\"i\":0,\"pick\":2}].\n\n" +
                          json.dumps(sub, ensure_ascii=False))
        if isinstance(res, list):
            for r in res:
                try:
                    i = ask[int(r["i"])]; k = r.get("pick")
                    picks[i] = None if k is None or not cand[i] or int(k) >= len(cand[i]) else cand[i][int(k)]
                except Exception: pass
            for i in ask:                              # זוכרים את ההחלטה – בפעם הבאה אותו שם יקבל אותו מוצר
                if picks[i] is not None: mem[mkey(items[i]["name"])] = d.items[picks[i]][0]
            while len(mem) > MATCH_MEM_MAX: mem.pop(next(iter(mem)))
            save_users()
    for i, it in enumerate(items):                     # שם כללי ("פסטה") – מוצר בסיסי לא יוצא "לא מצאתי" גם אם Claude לא בחר
        if picks[i] is None and cand[i]: picks[i] = generic_pick(it["name"], cand[i])
    def pack_qty(k, q):
        """משקל בק"ג מקבלה (0.5) שהותאם למוצר ארוז (500 גרם) → מספר אריזות (1), ולא חצי אריזה.
        משקל של דלפק (0.18 ק"ג בולגרית) מול אריזה של 250 גרם → 0.72 אריזה – מחיר לפי משקל, לא אריזה שלמה (אורי 20/10)"""
        if q is None or abs(q - round(q)) < 0.01 or not d.items[k][0][:1].isdigit(): return q
        g = kdata.parse_size(d.items[k][1])
        if not g or g < 100 or re.search(r'מ"?ל|ליטר', d.items[k][1]): return q
        n = q * 1000 / g
        return max(1, round(n)) if abs(n - round(n)) < 0.15 else round(n, 2)
    found = [dict(item_of(d, picks[i], pack_qty(picks[i], it["qty"]), it["name"]), c=[d.items[k][0] for k in cand[i] if k != picks[i]][:4],
                  **({"paid": round(float(it["price"]), 2)} if it.get("price") else {}), **({"bc": True} if it.get("_bc") else {}),
                  **({"sure": True} if fixed[i] or len(cand[i]) == 1 or it.get("_bc") else {}))
             for i, it in enumerate(items) if picks[i] is not None]
    merged = {}                                          # אותה שורה פעמיים בקבלה (בטטה 1.555 + בטטה 0.435) – שורה אחת עם הכמות והמחיר יחד
    for x in found:
        key = x["code"] if "paid" in x else id(x)        # רק שורות מקבלה (עם מחיר)
        y = merged.get(key)
        if y is None: merged[key] = x; continue
        y["qty"] = round(y["qty"] + x["qty"], 3)
        if "paid" in y or "paid" in x: y["paid"] = round(y.get("paid", 0) + x.get("paid", 0), 2)
    found = list(merged.values())
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

# איפוס (וגם "להתחיל מחדש") מוחק גם את ההרגלים: קניות קודמות, הסופר הקבוע, קבלות – בקשה של אורי 12/10, כדי לבדוק את הבוט נקי.
# זמני: כנראה ישתנה בהמשך. False = "איפוס" מוחק רק העדפות, ורק "איפוס מלא" מוחק הרגלים.
RESET_CLEARS_HABITS = True

def forget_habits(u):
    u["hist"] = []
    for k in ("receipts", "last_receipt", "stores", "rbuf_t", "note", "cam_tip", "missed", "pending"): u.pop(k, None)

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
    w = it.get("wfb")                                  # אחרון: אותו מוצר בסיסי במשקל (בשר טחון ארוז → בשר טחון לק"ג)
    if w and w[0] in P and not any(g in P for g in it["g"]) and not any(g in P for g in it.get("fb") or []):
        g, kg = w
        nm = kdata.D.items[g][1].replace(' (לק"ג)', "")
        return (P[g] * kg * it["qty"], None, f'{nm} במשקל ({kg:g} ק"ג)')
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
    sc = it.get("sc")                                  # אותו מוצר בגודל אחר – לפי הגודל (סלמי 300 במקום 500 גרם, אורי 20/10)
    if sc:
        best = None
        for j, f in sc:
            if j not in P: continue
            c = P[j] * it["qty"] * f
            if best is None or c < best[0]: best = (c, None, kdata.D.items[j][1] + " – לפי הגודל")
        if best: return best
    sm = it.get("sim")                                 # מוצר מיוחד שאין בסניף – מוצר דומה (אותו סוג/מותג, טעם אחר), לפי הגודל (20/10)
    if sm:
        best = None
        for j, f in sm:
            if j not in P: continue
            c = P[j] * it["qty"] * f
            if best is None or c < best[0]: best = (c, None, kdata.D.items[j][1] + " – מוצר דומה")
        if best: return best
    pk = it.get("pk")                                  # ירק/פרי שבסניף נמכר רק ארוז → מספר מארזים לפי המשקל (אורי 20/10)
    if pk:
        best = None
        for j, kg in pk:
            if j not in P: continue
            n = 1 if kg is None else max(1, round(it["qty"] / kg)); c = P[j] * (it["qty"] if kg is None else n)
            if best is None or c < best[0]: best = (c, None, kdata.D.items[j][1] + (f" ×{n}" if n > 1 else ""))
        if best: return best
    fz = it.get("frz")                                 # אחרון-אחרון: אין טרי בכלל → אותו נתח קפוא (החלטה של אורי 12/10)
    if fz:
        best = None
        for j, kg in fz:
            if j not in P: continue
            c = P[j] * it["qty"] if kg is None else P[j] * max(1, round(it["qty"] / kg))   # במשקל: לק"ג; ארוז: מספר אריזות
            if best is None or c < best[0]: best = (c, None, kdata.D.items[j][1].replace(' (לק"ג)', ""))
        if best: return best
    return None

# ---------- קפוא כחלופה (אורי 12/10): בשר/עוף/דג טרי שאין בסניף בכלל → אותו נתח קפוא, עם "במקום: … קפוא" ----------
FROZEN_WORD = re.compile(r"קפוא|מוקפא|קפואה|קפואים|(?<![א-ת])קפ(?![א-ת])")
FROZEN_RX = {
    "M01": r"^(פילה |נתחי )?(חזה( עוף| של עוף)?|פילה עוף)(?!.*(טחון|הודו|בקר|עגל|אווז|מעושן|שניצל|ציפוי|מתובל|נקניק|פסטרמה|ברווז))",
    "M02": r"^שניצל(ים)?( עוף| חזה עוף| פרוס)?(?!.*(תירס|צמחי|טבעול|ציפוי|פריך|הצומח|ילדים|טבע|ג'ונגל|מצופה|תפו|עגל|בקר|הודו))",
    "M03": r"^(פרגית|פרגיות)(?!.*(שיפוד|מתובל|ממולא|מרינד))",
    "M04": r"^(כרעיים|כרעי)( עוף)?", "M05": r"^(שוקיים|שוקי)( עוף)?", "M06": r"^(כנפיים|כנפי)( עוף)?",
    "M07": r"^עוף( שלם| מס\W{0,2} ?\d| בשקית| טרי| מוכשר| גלאט| כשר)*(?!.*(טחון|שניצל|נקניק|פסטרמה|מחולק|כנפ|שוק|כרע|חזה|לחמין|טוב|קורקבן|כבד|לבבות|צוואר|גב))",
    "M08": r"^(בשר (בקר )?טחון|טחון בקר|בקר טחון)(?!.*(עוף|הודו|מתובל|קבב|המבורגר|עגל))",
    "M09": r"^(בשר בקר(?! טחון)|צלי כתף|כתף בקר|בשר מס\W{0,2} ?\d)(?!.*(טחון|עוף|הודו|ראש|לחמין))",
    "M10": r"^(סטייק )?אנטריקוט", "M11": r"^(פילה |נתח )?סלמון(?!.*(מעושן|גרבלקס|סושי|מתובל))",
    "M12": r"^(דג |פילה )?(אמנון|מושט)", "M17": r"^(פילה )?(הייק|בקלה|מרלוזה)",
}
_frz = {"v": None, "c": {}}

def frozen_alts(i):
    """[(אינדקס, ק"ג באריזה או None למוצר במשקל)] – גרסאות קפואות של נתח בשר/עוף/דג מאוחד, במחיר לק"ג סביר"""
    D = kdata.D; code = D.items[i][0]
    if code not in FROZEN_RX: return []
    if _frz["v"] != D.version: _frz.update(v=D.version, c={})
    if i in _frz["c"]: return _frz["c"][i]
    rx = re.compile(FROZEN_RX[code]); p0 = D.items[i][3] / 100 or None; out = []
    for j, it in enumerate(D.items):
        nm = it[1]
        if j == i or not FROZEN_WORD.search(nm) or not rx.search(nm.replace("`", "'")): continue
        if it[0].startswith("W:"): kg = None; per = it[3] / 100
        elif it[0][:1].isdigit():
            g = kdata.parse_size(nm) or ((lambda m: float(m.group(1)) * 1000)(re.search(r"(\d+(?:\.\d+)?)\s*ק(?![א-ת])", nm)) if re.search(r"(\d+(?:\.\d+)?)\s*ק(?![א-ת])", nm) else None)
            if not g or g < 300: continue
            kg = g / 1000; per = it[3] / 100 / kg
        else: continue
        if p0 and not 0.3 * p0 <= per <= 1.4 * p0: continue    # מחיר לק"ג לא הגיוני לאותו נתח – לא חלופה
        out.append((j, kg))
    _frz["c"][i] = out
    return out

# ---------- ירק/פרי במשקל שבסניף נמכר רק ארוז (עגבניות שרי במארז 460 גרם) – אורי 20/10: "חסר עגבניות שרי" בחצי משופרסל ----------
PACK_SKIP = re.compile(r"מיובש|קצוצ|מרוסק|שימורים|כבוש|ממרח|רוטב|ברוטב|בשמן|רסק|מחית|משקה|מיץ|צ.יפס|קפוא|פיצה|סלט|חטיף|מטוגן|אפוי|"
                       r"מבושל|ממולא|קלוי|בחומץ|במלח|יבש|אורגני|MUTTI|מוטי|נקטר|ריבה|שוקולד|מצופ|בטעם|מקולפ|עיסיס|איסיס|%|^ר\.|קצו[צץ]|מיקרו|מיונז|מוחמצ|מוקפא|חתו[כך]|שטוף|מגורר|פרוס|עלים|מנגולד|ו?ואקום|בייבי|ליטר|מ\"?ל|\d\s*ל(?![א-ת])")
_pk = {"v": None, "c": {}}

def _pack_g(name):
    """גודל המארז בגרמים: "460 גרם", "1 ק"ג", או מספר בסוף השם ("שרי מיקס 440")"""
    g = kdata.parse_size(name)
    if g: return g
    m = re.search(r"(\d+(?:\.\d+)?)\s*ק(?![א-ת])", name)
    if m and float(m.group(1)) <= 5: return float(m.group(1)) * 1000
    m = re.search(r"(?<![\d.])(\d{3,4})(?![\d.])", name)
    return float(m.group(1)) if m and 150 <= float(m.group(1)) <= 2500 else None

def packed_alts(i):
    """[(אינדקס, ק"ג במארז)] – אותו ירק/פרי ארוז, כשבסניף אין אותו במשקל. רק ירקות ופירות מאוחדים (P).
    מארז בלי גודל בשם – הגודל הנפוץ של אותו מוצר (לפי המארזים שכן כתוב בהם). מחיר לק"ג סביר (פי 0.4–2.5 מהמשקל)."""
    D = kdata.D; code = D.items[i][0]
    if code[:1] != "P": return []
    if _pk["v"] != D.version: _pk.clear(); _pk.update(v=D.version, c={})
    if i in _pk["c"]: return _pk["c"][i]
    neutral = {kdata.stem(w) for w in kdata.FB_NEUTRAL}
    core = [kdata.stem(w).rstrip("י") for w in kdata.toks(D.items[i][1].replace('(לק"ג)', "")) if kdata.stem(w) not in neutral and not w[:1].isdigit()]
    out = []
    if core:
        first = core[0]
        # זנים: מילה שהיא חלק מהשם של ירק/פרי מאוחד אחר מאותו סוג ("שרי" ל"עגבנייה") – אינה אותו מוצר
        if "fw" not in _pk:                              # לכל סוג (מילה ראשונה) – כל המילים בשמות של הירקות/הפירות המאוחדים שלו
            fw = collections.defaultdict(set)
            for j in D._unified():
                if D.items[j][0][:1] != "P": continue
                st_ = [kdata.stem(w).rstrip("י") for w in kdata.toks(D.items[j][1].replace('(לק"ג)', "")) if not w[:1].isdigit()]
                if st_: fw[st_[0]].update(st_)
            _pk["fw"] = fw
        other = _pk["fw"].get(first, set()) - set(core) - neutral
        cand = []
        for j in D.search(" ".join(kdata.toks(D.items[i][1].replace('(לק"ג)', ""))), 80):
            it = D.items[j]
            if not it[0][:1].isdigit() or it[2] < 5 or PACK_SKIP.search(it[1]): continue
            st = [kdata.stem(w).rstrip("י") for w in kdata.toks(it[1])]
            if not st or st[0] != first or not all(any(x.startswith(w) for x in st) for w in core): continue
            if set(st) & other: continue
            if re.search(r"\(ק\)|לק\"?ג|ק\"ג\)", it[1]) and not kdata.parse_size(it[1]):   # "(ק)" – מחיר לק"ג בברקוד של הרשת
                cand.append((j, -1)); continue
            cand.append((j, _pack_g(it[1])))
        known = sorted(g for _, g in cand if g and g > 0)
        dflt = known[len(known) // 2] if known else None
        p0 = D.items[i][3] / 100
        for j, g in cand:
            if g == -1:                                   # במשקל – מחיר לק"ג
                if p0 and 0.4 * p0 <= D.items[j][3] / 100 <= 2.5 * p0: out.append((j, None))
                continue
            g = g or dflt
            if not g or not p0: continue
            per = D.items[j][3] / 100 / (g / 1000)
            if 0.4 * p0 <= per <= 2.5 * p0: out.append((j, g / 1000))
    _pk["c"][i] = out
    return out

OTHERS_SHOW = 6          # "סופרים אחרים": עד 6 סניפים, שתי שורות לכל אחד (אורי 19/10: פחות מלל)

# ---------- וולט מרקט: אותה קנייה במשלוח (אורי 19/10) ----------
WOLT_KM = 6.0            # מחסן וולט מרקט עד ~6 ק"מ (דרך) מהמיקום – כנראה באותה עיר ומשלחים אליו
WOLT_URL = "https://wolt.com/he/isr"

def delivery_option(D, home, items, med, clubs=()):
    """אותו סל במחסן משלוחים קרוב (וולט מרקט) – אופציה נוספת בלבד, לא נכנס להשוואה/להמלצה. None אם אין או שחסר בו הרבה."""
    near = [st for st in getattr(D, "delivery", None) or [] if km(home, st["p"]) <= WOLT_KM]
    if not near or not items: return None
    st = min(near, key=lambda x: km(home, x["p"]))
    P = D.store_prices(st, clubs); have = tot = 0; mlist = []
    for it in items:
        c = lc(P, it)
        if c is None: mlist.append(it["name"].replace(' (לק"ג)', "")); tot += med[it["i"]] * it["qty"]
        else: have += c[0]; tot += c[0]
    if len(mlist) >= MAX_MISSING or len(mlist) * 4 > len(items): return None      # עד רבע מהסל חסר – אחרת זו לא "אותה קנייה"
    return {"s": st, "have": have, "sum": tot, "miss": len(mlist), "mlist": mlist}

def basket_price(r, short=False):
    """מחיר כל הסל בסניף – כולל המוצרים שחסרים בו (לפי המחיר באזור), כדי שההשוואה תהיה הוגנת: אותו סל בדיוק בכל סניף.
    (אורי 20/10: "סל שאבא קנה ב-670 עולה במקום אחר 450 כי חסר דברים" – המחיר בלי החסרים הטעה. ובלי הסימן ~)"""
    if not r.get("miss"): return f"₪{r['have']:.0f}"
    n = len(r.get("mlist") or []) or r["miss"]          # מוצרים שונים (שורה כפולה בקבלה = מוצר אחד, כמו ב"מה חסר")
    return f"₪{r['sum']:.0f}" + (f" (חסרים {n})" if short else f" _(כולל {'מוצר אחד שחסר' if n == 1 else f'{n} שחסרים'} שם – לפי המחיר באזור)_")

# כלל המעבר המשולב (אורי 20/10): לפחות ₪35 (₪25 לסל במשקל), וגם לפחות 4% מהסל, ועוד ₪1 לכל דקת נסיעה נוספת מעבר ל-5
STAY_PCT, STAY_FREE_MIN, STAY_PER_MIN = 0.04, 5, 1.0

def switch_need(stay_min, ref, best):
    """כמה צריך לחסוך כדי שיהיה שווה לעבור מהסופר הקבוע/הקרוב לסופר אחר"""
    extra = max(0.0, best["min"] - ref["min"] - STAY_FREE_MIN)
    return max(stay_min, STAY_PCT * ref["sum"]) + extra * STAY_PER_MIN

STALE_REC_H = 72         # קובץ מחירים של סניף ישן מ-3 ימים – לא נכנס להשוואה (חוץ מהסופר הקבוע/של הקבלה)

def stale_store(s):
    """ישן ביחס לשאר הנתונים (ולא לשעון): אם כל עדכון הנתונים נתקע ל-3 ימים, לא מוציאים את כל הסניפים מההשוואה"""
    a = _ts_age_h(s.get("ts")) if s.get("ts") else None
    if a is None: return False
    ref = _iso_age_h(getattr(kdata.D, "built", "") or "") or 0          # כמה זמן עבר מאז שהנתונים נבנו
    return a - max(0, ref) > STALE_REC_H

def miss_est(r):
    """(ישן – נשאר לתאימות) הערכה למוצרים שחסרים בסניף"""
    if not r.get("miss"): return ""
    est = r["sum"] - r["have"]
    return f" + ₪{est:.0f} על {'מוצר אחד שחסר' if r['miss'] == 1 else str(r['miss']) + ' שחסרים'}"

def compute(u):
    D = kdata.D
    pr = fill_prefs(dict(u["prefs"])); home = pr["home"]; car = pr["mode"] == "car"; bus = pr["mode"] == "bus"
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
            q = it.get("src") or it["name"]; q = SEARCH_SYN.get(" ".join(kdata.toks(q)), q)   # "חלות" → קבוצה וחלופות של "חלה"
            g = [i] if mine else D.group(i, q)
            wf = None if mine or not D.items[i][0][:1].isdigit() else weighed_fb(D.items[i][1])
            if wf: wf = (D.code2i[wf[0]], wf[1]) if wf[0] in D.code2i else None
            items.append(dict(it, i=i, g=g, fb=[] if mine else D.fallback(i, q), wfb=wf, frz=[] if mine else frozen_alts(i), pk=[] if mine else packed_alts(i), sc=[] if mine else D.scaled_alts(i, q),
                              sim=[] if mine else D.similar_alts(i)))
    if not items:
        return ["לא מצאתי אף אחד מהמוצרים במאגר המחירים העדכני 🙁 נסה לכתוב אותם שוב."]
    near = sorted(((km(home, s["p"]), s) for s in D.stores), key=lambda x: x[0])
    if not near: return ["אין לי עדיין נתוני סניפים 🙁"]
    rows = []
    for k, (d1, s) in enumerate(near[:MAX_POOL * 2]):
        if k > 0 and mins(d1) > pr["maxMin"] and len(rows) >= 1: break
        if stale_store(s): continue                    # מחירים ישנים (השרת הישראלי לא רץ) – לא ממליצים לפיהם (אורי 20/10: דיוק)
        rows.append({"s": s, "d": 2 * d1, "min": mins(d1), "P": D.store_prices(s, clubs), "club": s["chain"] in clubs and s.get("cblob", -1) >= 0})
        if len(rows) >= MAX_POOL: break
    uk = usual_store(u)
    rk = u.get("rstore") if any(it.get("paid") for it in items) else None
    if rk and any(st_["key"] == rk for st_ in D.stores) and km(home, next(st_["p"] for st_ in D.stores if st_["key"] == rk)) <= REG_MAX_KM:
        uk = rk                                        # קבלה: משווים מול הסניף שבו קנו בפועל, במחירים ששולמו (אורי 20/10)
    else: rk = None
    if uk and not any(r["s"]["key"] == uk for r in rows):      # הסופר הקבוע (קרוב לבית) תמיד בהשוואה
        s0 = next(s for s in D.stores if s["key"] == uk); d1 = km(home, s0["p"])
        rows.append({"s": s0, "d": 2 * d1, "min": mins(d1), "P": D.store_prices(s0, clubs), "club": s0["chain"] in clubs and s0.get("cblob", -1) >= 0})
    # חציון מקומי למוצרים שחסרים בסניף מסוים
    med = {}
    for it in items:
        v = sorted(x for x in (gp(r["P"], it) for r in rows) if x is not None)
        med[it["i"]] = v[min(len(v) - 1, 3 * len(v) // 4)] if v else D.items[it["i"]][3] / 100   # רבעון עליון: סניף עם חסרים לא "מנצח" בזכותם
    def lcr(r, it):                                    # בסניף של הקבלה – מה ששולם בפועל; בשאר – לפי המחירים
        if rk and it.get("paid"):
            if r["s"]["key"] == rk: return (it["paid"], None, None)
            c = lc(r["P"], it)
            # המחיר בקבלה הוא האמת: מחיר בסניף אחר שרחוק פי 2.5 ממנו = כנראה מוצר אחר / יחידה אחרת (גבינת דלפק לק"ג מול אריזה)
            if c and not 0.4 <= c[0] / it["paid"] <= 2.5: return None
            return c
        return lc(r["P"], it)
    special = []                                       # מוצרים מיוחדים שכמעט אין בסופרים אחרים – במחיר זהה בכל סניף
    def neutral(it):
        return it["paid"] if rk and it.get("paid") else med[it["i"]] * it["qty"]
    def stats():
        for r in rows:
            tot = 0; miss = 0; mlist = []; have = 0
            for it in special:                         # אותו מחיר בכל סניף: לא משפיע על ההשוואה, אבל הסל מלא (₪617 ולא ₪450 – 20/10)
                c = neutral(it); tot += c; have += c
            for it in items:
                c = lcr(r, it)
                if c is None: miss += 1; c = med[it["i"]] * it["qty"]; mlist.append(it["name"].replace(' (לק"ג)', "")) if it["name"].replace(' (לק"ג)', "") not in mlist else None
                else: c = c[0]; have += c              # מחיר מדויק – רק מה שיש בסניף (להצגה), כולל מבצעי כמות
                tot += c                               # להשוואה בין סניפים – חסר מוערך לפי האזור
            r.update(mlist=mlist, have=have, sum=tot, fuel=cost(r["d"]), tot=tot + cost(r["d"]), miss=miss)
    ok_miss = lambda r: r["miss"] < MAX_MISSING and r["miss"] * 2 < len(items)   # עד 3 חסרים (ופחות מחצי הסל)
    stats()
    # אף סניף בטווח לא עומד בכלל (4+ חסרים בכולם) – המוצרים הנדירים יוצאים מההשוואה, וסניף עם 4+ חסרים אף פעם לא מוצג
    rare = []
    inr = [r for r in rows if r["min"] <= pr["maxMin"]] or rows[:1]
    want, n0 = min(3, len(inr)), len(items)          # רוצים לפחות 3 סניפים להשוואה – אבל לא מוציאים יותר משליש מהסל בשביל זה
    while len(items) > 1 and ((n_ok := sum(1 for r in inr if ok_miss(r))) == 0 or (n_ok < want and len(rare) < n0 // 3)):
        it = max(items, key=lambda it: sum(1 for r in inr if lcr(r, it) is None))
        special += [x for x in items if x["name"] == it["name"]]
        items = [x for x in items if x["name"] != it["name"]]; rare.append(it["name"].replace(' (לק"ג)', "")); stats()   # כולל שורות כפולות של אותו מוצר
    if all(r["miss"] == len(items) for r in inr):        # אף סניף בטווח לא מוכר אף מוצר מהסל – לא ממליצים על "₪0"
        names = list(dict.fromkeys(it["name"].replace(' (לק"ג)', "") for it in items))
        return [f"🔎 לא מצאתי את {', '.join(names[:5])} בסופרים באזור שלך 🙁\nאפשר לנסות לכתוב את המוצר אחרת, או לשלוח רשימה עם עוד מוצרים."]
    nearest = rows[0]
    reg = next((r for r in rows if r["s"]["key"] == uk), None)
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
                    pa, pb = lcr(A, it), lcr(B, it)
                    if pa is None and pb is None: ok = False; break
                    if pb is None or (pa is not None and pa[0] <= pb[0]): tot += pa[0]; inA.append(it["name"])
                    else: tot += pb[0]; inB.append(it["name"])
                if not ok or not inA or not inB: continue
                d = km(home, A["s"]["p"]) + km(A["s"]["p"], B["s"]["p"]) + km(B["s"]["p"], home)
                t = tot + cost(d)
                if not split or t < split["tot"]: split = {"A": A, "B": B, "tot": t, "inA": inA, "inB": inB}
    weighed = all(D.items[it["i"]][0][:1] in ("M", "W", "P") for it in items)   # רק מוצרים במשקל (בשר, דגים, ירקות…)
    stay_min = STAY_MIN_WEIGHED if weighed else STAY_MIN
    # בלי סופר קבוע ידוע – משווים לסופר הקרוב (שיש בו את הסל): חיסכון קטן לא שווה נסיעה רחוקה
    near_ref = None if reg else min((r for r in pool if ok_miss(r)), key=lambda r: r["d"], default=None)
    if near_ref and near_ref["miss"]:               # הקרוב חסר מוצרים (AM:PM, אקספרס) – עדיף סניף מלא שקרוב כמעט באותה מידה
        full_near = min((r for r in pool if r["miss"] == 0 and r["min"] <= near_ref["min"] + 7), key=lambda r: r["d"], default=None)
        if full_near: near_ref = full_near
    ref = reg or near_ref
    def common_gap(a, b):                              # חיסכון רק על מוצרים שיש בשני הסניפים – בלי הערכות לחסרים
        g = 0.0
        for it in items:
            ca, cb = lcr(a, it), lcr(b, it)
            if ca is not None and cb is not None: g += ca[0] - cb[0]
        return g
    gap = ref["tot"] - best["tot"] if ref else 0
    if ref and ref is not best and best["miss"]:       # במנצח חסרים מוצרים: החיסכון חייב להתקיים גם על מה שיש בשניהם (אורי 20/10: חיסכון אמיתי)
        gap = min(gap, common_gap(ref, best))
    stay = ref is not None and gap < switch_need(stay_min, ref, best)
    if stay:                                   # לא שווה לעזוב את הסופר הקבוע / הקרוב
        alt = best; best = ref
        if ref in ranked: ranked.remove(ref)
        ranked.insert(0, ref)
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
        if rk and best is reg:                    # קבלה: מה ששילמו בפועל
            L.append(f"👍 הסופר שקנית בו משתלם: *{s['n']}*" + kosher_tag(s))
            L.append(f"💰 שילמת: *₪{best['sum']:.0f}*")
        else:
            L.append((f"👍 הסופר הקבוע שלך משתלם: *{s['n']}*" if reg else f"👍 הסופר הקרוב אליך משתלם: *{s['n']}*") + kosher_tag(s))
            L.append(f"💰 מחיר הסל: *{basket_price(best)}*" + (" (במחירי מועדון)" if best.get("club") else ""))
        L.append(f"✅ זה הכי זול באזור 🏆" if gap < 1 else f"✅ במקום אחר היית חוסך רק ₪{gap:.0f}{inc} – לא שווה את הטרחה")
    else:
        L.append(f"🏆 הסופר המנצח: *{s['n']}*" + kosher_tag(s))
        L.append(f"💰 מחיר הסל: *{basket_price(best)}*" + (" (במחירי מועדון)" if best.get("club") else "")
                 )
        if ref:
            L.append(f"✅ חוסך *₪{gap:.0f}* לעומת מה ששילמת ב{ref['s']['n']} (₪{ref['sum']:.0f})" if rk and ref is reg else
                     f"✅ חוסך *₪{gap:.0f}* לעומת {ref['s']['n']}" + ("" if reg else " (הקרוב אליך)") + inc)
        elif avg and avg - best["tot"] >= 1:
            L.append(f"✅ חוסך *₪{avg - best['tot']:.0f}*{inc}")
    L.append(f"{ride} {round(best['min'])} דק' {how} ממך")       # בלי "(אזור …)" – האזור מופיע ב"היי" (אורי 20/10: פחות מלל)
    dlv = delivery_option(D, home, items, med, clubs)
    if dlv: L.append(f"🛵 או במשלוח מ*{dlv['s']['chain']}*: {basket_price(dlv, short=True)} + דמי משלוח")
    if rare:                                   # רק כמה – את השמות רואים בכפתור "מה חסר" (אורי 18/10: רשימה ארוכה מבלבלת)
        nr = len(dict.fromkeys(rare))
        L.append("🔎 _" + ("מוצר מיוחד אחד" if nr == 1 else f"{nr} מוצרים מיוחדים") + " שכמעט אין בסופרים אחרים – חושבו בכל סופר לפי מה ששילמת_" if rk else
                 "🔎 _" + ("מוצר אחד כמעט לא נמכר" if nr == 1 else f"{nr} מוצרים כמעט לא נמכרים") + " באזור – חושבו בכל סופר לפי המחיר הרגיל_")
    split_txt = None
    if split and best["tot"] - split["tot"] > split_min:
        A, B = split["A"]["s"], split["B"]["s"]
        split_txt = (f"🔀 *קנייה בשני סופרים – ₪{split['tot']:.0f}*\n\n*{A['n']}* ({A['a']}):\n" + "\n".join("• " + x for x in split["inA"]) +
                     f"\n\n*{B['n']}* ({B['a']}):\n" + "\n".join("• " + x for x in split["inB"]))
    shown = inrange[:OTHERS_SHOW] + ([reg] if reg and reg not in inrange[:OTHERS_SHOW] and reg in full else [])
    if s["key"] not in {r["s"]["key"] for r in shown}: shown = [best] + shown[:OTHERS_SHOW - 1]
    if ref and ref["s"]["key"] not in {r["s"]["key"] for r in shown}: shown.append(ref)   # "חוסך לעומת X" – X תמיד ברשימה
    # פירוט מחירי הסל בסופר המנצח
    det = []
    for it in sorted(items, key=lambda it: -((lcr(best, it) or (med[it["i"]] * it["qty"],))[0])):
        c = lcr(best, it)
        q = f" ×{it['qty']:g}" if it["qty"] != 1 else ""
        det.append(f"• {it['name']}{q} – " + (f"₪{c[0]:.2f}" + (f" 🔥 _מבצע {c[1]}_" if c[1] else "")
                                               + (f" _(אין בסניף – במקום: {c[2]})_" if c[2] else "") if c is not None
                                               else f"₪{med[it['i']] * it['qty']:.2f} _(חסר בסניף – מחיר באזור)_"))
    for it in special:                                 # מוצרים מיוחדים – במחיר הזהה שחושב בכל סניף
        q = f" ×{it['qty']:g}" if it["qty"] != 1 else ""
        det.append(f"• {it['name']}{q} – ₪{neutral(it):.2f} _(מוצר מיוחד – {'מה ששילמת' if rk and it.get('paid') else 'מחיר באזור'})_")
    if gone: det.append("\n_לא נמצאו במאגר: " + ", ".join(gone) + "_")
    fresh = f"\n\n_מחירים ומבצעים מעודכנים ל-{D.built[8:10]}/{D.built[5:7]} {D.built[11:16]}_" if len(D.built) >= 16 else ""
    saved = 0 if stay else (gap if ref else (avg - best["tot"] if avg else 0))
    ip = {}
    for it in items:                                   # מחיר כל מוצר בסופר המומלץ (ל"מה זיהיתי")
        c = lcr(best, it)
        ip[it.get("code", "")] = [round(c[0] if c else med[it["i"]] * it["qty"], 2), c is None, c[1] if c else None]
    for it in special: ip[it.get("code", "")] = [round(neutral(it), 2), False, None]
    return {"main": "\n".join(L), "store": s["n"], "saved": max(0, round(saved)), "ip": ip,
            "nav": f"https://waze.com/ul?ll={s['p'][0]},{s['p'][1]}&navigate=yes",
            "details": f"📋 *הסל שלך ב{s['n']}:*\n" + "\n".join(det) + fresh,
            "others": ((f"💡 אם מתחלקים בין *{split['A']['s']['n']}* ל*{split['B']['s']['n']}* חוסכים עוד *₪{best['tot'] - split['tot']:.0f}*"
                        + (f" – כל הסל ב-₪{split['tot']:.0f}, לעומת ₪{best['tot']:.0f} כולל מה שחסר ב{s['n']}" if best["miss"] else "") + "\n\n")
                       if split_txt else "")
                      + "🏪 *סופרים באזור:*\n\n" + "\n\n".join(
                f"{'🏆' if r['s']['key'] == s['key'] else '▫️'} *{full_name(r['s'])}*" + (" ✡️" if is_kosher(r["s"]) else "")
                + (" ⭐ _הקבוע שלך_" if r is reg else "") +
                f"\n      💰 {basket_price(r, short=True)} · {ride} {round(r['min'])} דק'"
                for r in shown) + fresh,
            "split": split_txt, "miss": list(best["mlist"]), "rare": list(dict.fromkeys(rare)),
            "miss_all": [(full_name(r["s"]), list(r["mlist"])) for r in shown if r["miss"]],
            "stay": stay, "gap": round(gap), "ref": ref["s"]["n"] if ref else None, "store_key": s["key"], "ref_key": ref["s"]["key"] if ref else None, "ref_have": round(ref["have"]) if ref else None,
            "have": round(best["have"]), "sum": round(best["sum"]), "ref_sum": round(ref["sum"]) if ref else None, "mins": round(best["min"]), "kosher": is_kosher(s),
            "wolt": {"chain": dlv["s"]["chain"], "have": round(dlv["have"]), "miss": dlv["miss"]} if dlv else None}

# ---------- שיחה ----------
WELCOME = "היי{name}! 👋 אני אעזור לך לחסוך בקניות 🛒\nכמה שאלות קצרות ומתחילים 😊"
def welcome(u): return WELCOME.format(name=" " + u["name"] if u.get("name") else "")
PROFILE_KEYS = ("home", "mode", "maxMin", "splitMin")
GREET = re.compile(r"^\W*(הי+|שלום|אהלן|הלו|יו|מה קורה|מה נשמע|מה המצב|מה הולך|מה איתך|בוקר טוב|ערב טוב|צהריים טובים|hi+|hey|hello|start)\b[^,\n]*$", re.I)

def ask(to, step, u, intro="", loc_btn=False):
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
        L = learned(u); lb = [("loc_new", "📍 ממקום אחר")] if loc_btn else []
        if L:
            send_buttons(to, intro + "*מה קונים הפעם?*", [("mine", "🛒 הסל הקבוע שלי"), ("cam", "📷 צילום קבלה"), ("list", "✍️ אכתוב רשימה")] + lb)
        else:
            send_buttons(to, intro + "*מה קונים?*", [("cam", "📷 צילום קבלה"), ("list", "✍️ אכתוב רשימה"), ("weekly", "🛒 סל שבועי רגיל")] + lb)
    save_users()

LAST_KEYS = ("details", "others", "split", "store", "ip", "miss", "rare", "miss_all", "nav", "store_key", "ref_key", "stay", "gap", "wolt")

def result_buttons(res):
    """בהודעה הראשית רק שני כפתורים: "✅ נלך לשם" ו"📋 פרטים" (אורי 20/10: פשוט יותר). השאר בתוך "פרטים"."""
    return [("plan_ok", "✅ נלך לשם"), ("more", "📋 פרטים")] + ([(WOLT_URL, "🛵 להזמין בוולט")] if res.get("wolt") else [])

def details_buttons(last):
    """תפריט "פרטים": מה חסר (אם יש), מה זיהיתי, סופרים אחרים, תיקון"""
    n = len(last.get("miss") or []) + len(last.get("rare") or [])
    return ([("miss", f"❓ מה חסר ({n})")] if n else []) + [("seen", "🧾 מה זיהיתי"), ("oth", "🏪 סופרים אחרים"), ("fix", "✏️ משהו לא נכון")]

def missing_text(last, every=False):
    """מה חסר: בסופר המומלץ (+ מה שלא נמצא באזור). every – בכל סניף ברשימת "סופרים אחרים"."""
    L = []
    if every:
        for nm, ml in last.get("miss_all") or []: L.append(f"*{nm}*\n" + "\n".join("• " + x for x in ml))
        return "❌ *מה חסר בכל סניף:*\n\n" + "\n\n".join(L) if L else "בכל הסניפים ברשימה יש את כל הסל 👍"
    if last.get("miss"):
        L.append(f"❌ *חסר ב{last.get('store', 'סופר המומלץ')}:*\n" + "\n".join("• " + x for x in last["miss"])
                 + "\n_המחיר שלהם בסל הוא הערכה לפי הסופרים באזור._")
    if last.get("rare"):
        L.append("🔎 *מוצרים מיוחדים* (כמעט אין בסופרים אחרים – חושבו באותו מחיר בכל סופר):\n" + "\n".join("• " + x for x in last["rare"]))
    return "\n\n".join(L) or "לא חסר כלום בסופר המומלץ 👍"

def finish(to, u, redo=False):
    if not (u.get("prefs") or {}).get("home"):            # אין מיקום (זיכרון פגום / כפתור ישן) – מבקשים מיקום במקום לקרוס
        u["step"] = "loc"; save_users()
        return send_location_request(to, "📍 כדי להשוות מחירים צריך לדעת מאיפה יוצאים – לוחצים על *שליחת המיקום שלי* למטה")
    fill_prefs(u["prefs"])
    if not redo: u.pop("plan", None)                       # רשימה/קבלה חדשה – כבר לא הסל המשוער
    res = compute(u); saved = 0
    if isinstance(res, list):
        for m in res: send_text(to, m)
    else:
        u["last"] = {k: res.get(k) for k in LAST_KEYS}; saved = res.get("saved", 0)
        if not redo:
            wk_count("baskets")
            wk_count("missing", [f"{x['name']} @ {res['store']}" for x in u["items"] if (res["ip"].get(x.get("code", "")) or [0, False])[1]])
        n, miss = len(u["items"]), len(u.get("missed") or [])
        note = u.pop("note", "")
        nm = "מוצר אחד לא זוהה" if miss == 1 else f"{miss} מוצרים לא זוהו"
        if note.startswith("⚠️ חלק מהקבלה"):            # אזהרה אחת קצרה במקום שתיים (אורי 20/10)
            main = res["main"] + f"\n⚠️ _חלק מהקבלה לא היה ברור{f' ({nm})' if miss else ''} – אפשר לצלם שוב את החלק הזה או לכתוב מה חסר_"
        else:
            main = res["main"] + (f"\n⚠️ _{nm} – אפשר לכתוב אותם אחרת_" if miss else "") + (f"\n\n{note}" if note else "")
        send_buttons(to, main, result_buttons(res))
    if u.get("plan"):                                     # תיקון של הסל המשוער – תחזית, לא קנייה: לא נכנס להיסטוריה
        u["step"] = "done"; save_users(); return
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
    hist = repair_user(u)["hist"]
    def ym(b): t = _dt.fromtimestamp(b["d"], IL_TZ); return t.year, t.month
    month = [b for b in hist if b["d"] and ym(b) == (y, m)]
    tot_m = sum(b["saved"] for b in month); tot_all = sum(b["saved"] for b in hist)
    if not month: return None
    L = [f"💰 *סיכום חיסכון – {HEB_MONTHS[m - 1]}*", "", ("🛒 בדקת קנייה אחת" if len(month) == 1 else f"🛒 בדקת {len(month)} קניות")]
    if tot_m >= 1:
        L.append(f"✅ מצאתי לך חיסכון של *₪{tot_m:.0f}*" + (f" (בממוצע ₪{tot_m / len(month):.0f} לקנייה)" if len(month) > 1 else ""))
    else:
        L.append("👍 הסופר שלך כבר היה הכי משתלם – לא היה צריך להחליף")
    real = [r for r in (u.get("real") or []) if isinstance(r, dict) and r.get("d") and ym(r) == (y, m)]
    if real:                                          # נמדד מקבלות אחרי "נלך לשם" – לא על הנייר (אורי 20/10)
        L.append(f"💚 נמדד בקבלות: חסכת באמת *₪{sum(r['saved'] for r in real):.0f}* ב-{len(real)} קניות")
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
    us = {k: repair_user(v) for k, v in USERS.items() if not k.startswith("_") and isinstance(v, dict)}   # משתמש פגום לא מפיל את הלוח
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
    iss = watchdog_issues()                      # כולל גם "עדכון נתונים נחסם" (היה כאן out.append → קריסה כשהעדכון נחסם)
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

PLAN_SHOW = 8   # כמה מוצרים מהסל המשוער להציג בהודעה

def send_plan(to, u):
    """סעיף 1 מ-VISION (אורי 18/10): הבוט יוזם – "הנה הקנייה של השבוע": סל משוער מהקבלות + ההמלצה, והמשתמש רק מאשר או מתקן.
    זו תחזית, לא עובדה: לא נשמרת בהיסטוריה (לא "לומדים" מהסל שהבוט עצמו הציע). מחזיר False אם אין מספיק כדי להציע."""
    if not (u.get("prefs") or {}).get("home"): return False
    L = learned(u)
    if not L: return False
    u["items"] = [{k: x[k] for k in ("src", "code", "name", "qty")} for x in L]
    res = compute(u)
    if not isinstance(res, dict): return False
    u["plan"] = int(time.time()); u["step"] = "done"
    u["last"] = {k: res.get(k) for k in LAST_KEYS}
    name = u.get("name", ""); nh = len(u.get("hist") or [])
    lines = ", ".join(f"{x.get('src') or x['name']}" + (f" ×{x['qty']:g}" if x["qty"] != 1 else "") for x in L[:PLAN_SHOW])   # בשם שהמשתמש מכיר
    more = f" ועוד {len(L) - PLAN_SHOW}" if len(L) > PLAN_SHOW else ""
    txt = (f"היי{' ' + name if name else ''} 👋 הכנתי לך את הקנייה של השבוע 🛒\n"
           f"*הסל הרגיל שלך ({len(L)} מוצרים):* {lines}{more}\n\n"
           + res["main"] + deals_part(u) + "\n\n_אם משהו כבר יש בבית – ✏️ לתקן._")
    n = len(res.get("miss") or []) + len(res.get("rare") or [])
    send_buttons(to, txt, [("plan_ok", "✅ מאשר"), ("fix", "✏️ לתקן את הסל"), ("more", "📋 פרטים")]
                 + ([(WOLT_URL, "🛵 להזמין בוולט")] if res.get("wolt") else []))
    save_users()
    return True

# ---------- יום הקניות הקבוע: יום לפני – בדיקה אם הסל הקבוע זול יותר במקום אחר (אורי 19/10) ----------
DAYB_HOURS = (18, 21)     # ערב שלפני יום הקניות
HEB_DAYS = ["שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון"]   # לפי weekday() של פייתון (שני=0)

def shop_day(u, now=None):
    """היום בשבוע שהמשתמש קונה בו בדרך כלל – לפי תאריכי הקבלות בחודשיים האחרונים (כל יום נספר פעם אחת).
    רק כשזה ברור: לפחות פעמיים באותו יום, ולפחות חצי מימי הקנייה. אחרת None (לא מנחשים מקבלה אחת)."""
    now = now or time.time()
    sd = (u.get("prefs") or {}).get("shop_day")             # נבחר בהגדרות ("📅 יום הקניות") – גובר על הלמידה (אורי 20/10)
    if isinstance(sd, int) and 0 <= sd <= 6: return sd
    dates = {_dt.fromtimestamp(t, IL_TZ).date() for t in (u.get("receipts") or [])
             if isinstance(t, (int, float)) and 0 <= now - t < 63 * 86400}
    if len(dates) < 2: return None
    c = collections.Counter(d.weekday() for d in dates)
    day, n = c.most_common(1)[0]
    return day if n >= 2 and n * 2 >= len(dates) else None

def is_daybefore(u, il):
    sd = shop_day(u, il.timestamp())
    return sd is not None and (il.weekday() + 1) % 7 == sd

DEALS_IN_WEEKLY = 2

def deals_part(u, now=None):
    """סעיף 5 (אורי 20/10): המבצעים בתוך ההודעה השבועית – הודעה אחת בשבוע במקום שתיים. מסמן אותם כנשלחו, ולא שולח
    הודעת מבצעים נפרדת ב-3 הימים הקרובים."""
    now = now or time.time()
    pr = u.get("prefs") or {}
    u["deal_last"] = now
    if pr.get("deals_off"): return ""
    try:
        sent = {k: t for k, t in (u.get("deal_sent") or {}).items() if now - t < 7 * 86400}
        deals = [d for d in find_deals(u) if d[2] not in sent][:DEALS_IN_WEEKLY]
    except Exception as ex:
        print("deals in weekly error", ex, flush=True); return ""
    if not deals: return ""
    for d in deals: sent[d[2]] = now
    u["deal_sent"] = sent
    return "\n\n🔥 *וגם במבצע השבוע:*\n" + "\n".join(d[1] for d in deals)

def send_daybefore(to, u):
    """יום לפני יום הקניות: אם הסל הקבוע זול יותר במקום אחר – הודעה קצרה עם ההמלצה. אם לא – רק "כל הכבוד"."""
    pr = u.get("prefs") or {}
    L = learned(u); basis = "הסל הקבוע שלך" if len(u.get("hist") or []) > 1 else "הקנייה האחרונה שלך"
    if len(L) < 5 and u.get("hist"):                     # עוד מעט קבלות – הקנייה האחרונה היא ההערכה הכי טובה (שקוף: כתוב "לפי הקנייה האחרונה")
        L = [x for x in (u["hist"][-1].get("items") or []) if x.get("code")]; basis = "הקנייה האחרונה שלך"
    if not pr.get("home") or not L: return None
    items = [{k: x.get(k, "") for k in ("src", "code", "name", "qty")} for x in L]
    res = compute(dict(u, items=items))
    if not isinstance(res, dict): return None
    if res["stay"] or not res.get("ref"):
        how = "באמת הכי זול באזור" if res["gap"] < 1 else f"משתלם – במקום אחר היית חוסך רק ₪{res['gap']}, לא שווה את הנסיעה"
        send_text(to, f"✅ בדקתי את {basis} לקראת מחר – *{res['store']}* {how}.\nכל הכבוד, תמשיך ככה 👏" + deals_part(u))
        return "stay"
    u["items"] = items; u["plan"] = int(time.time()); u["step"] = "done"
    u["last"] = {k: res.get(k) for k in LAST_KEYS}
    n = len(res.get("miss") or []) + len(res.get("rare") or [])
    w = res.get("wolt")
    txt = (f"🛒 *מחר יום קניות?* בדקתי את {basis} ({len(items)} מוצרים):\n\n"
           f"💰 ב*{res['store']}*: ₪{res['sum']}" + (f" (חסרים {n})" if n else "") + f"\nבמקום ₪{res['ref_sum']} ב{res['ref']}\n"
           f"✅ *חוסך ₪{res['gap']}* · 🚗 {res['mins']} דק'" + (f"\n🛵 בוולט מרקט: ₪{w['have']} + משלוח" if w else "") + deals_part(u))
    send_buttons(to, txt, [("plan_ok", "✅ נלך לשם"), ("fix", "✏️ לתקן את הסל"), ("more", "📋 פרטים")])
    u["remind_miss"] = u.get("remind_miss", 0) + 1; save_users()
    return "switch"

def daybefore_tick(now=None):
    il = now or _dt.now(IL_TZ)
    if not DAYB_HOURS[0] <= il.hour < DAYB_HOURS[1]: return
    day = il.strftime("%Y-%m-%d")
    for to, u in list(USERS.items()):
        if to.startswith("_") or not isinstance(u, dict) or not is_tg(to): continue
        pr = u.get("prefs") or {}
        if pr.get("remind_off") or not pr.get("home") or u.get("remind_miss", 0) >= 3 or u.get("dayb") == day: continue
        try:
            if not is_daybefore(u, il): continue
            u["dayb"] = day; save_users()
            send_daybefore(to, u)
        except Exception as ex: print("day-before error", to, ex, flush=True)

def send_reminder(to, u):
    try:
        if send_plan(to, u): return
    except Exception as ex:
        print("plan error", to, ex, flush=True)
    name = u.get("name", "")
    u["step"] = "basket"
    btns = [("mine", "🛒 הסל הקבוע שלי"), ("cam", "📷 צילום קבלה"), ("remind_off", "בלי תזכורות")]
    send_buttons(to, f"היי{' ' + name if name else ''} 👋\nבדרך כלל אתה עושה קנייה בערך בזמן הזה.\n"
                     f"רוצה שאבדוק איפה הכי משתלם השבוע?", btns)

# ---------- התראת מבצעים: מוצר מהסל הקבוע במבצע ליד הבית (סעיף 3 מהשיפורים, אורי 13/10) ----------
DEAL_MIN_OFF = 0.20       # לפחות 20% הנחה מהמחיר הרגיל באותו סניף
DEAL_MIN_SAVE = 2.0       # ולפחות ₪2 חיסכון לכמות הרגילה של הלקוח
DEAL_EVERY_DAYS = 3       # לכל היותר הודעה אחת כל 3 ימים
DEAL_HOURS = (17, 21)     # שעות שליחה
_deals = {"day": ""}

def deal_stores(u):
    """הסניפים שבודקים בהם מבצעים: הסופר הקבוע + עד 5 סופרמרקטים קרובים (עד ~15 דק'), בלי חנויות נוחות/פארם"""
    D = kdata.D; home = (u.get("prefs") or {}).get("home")
    if not D or not home: return []
    near = sorted((s for s in D.stores if s["chain"] not in ("יילו", "דור אלון", "סופר יודה")
                   and not re.search(r"\bBE\b|Be\b|אקספרס|AM|PM", s["n"])
                   and not (s["chain"] in IL_SERVER.values() and il_muted())), key=lambda s: km(home, s["p"]))
    out = [s for s in near[:5] if car_min(km(home, s["p"])) <= 15]
    uk = usual_store(u)
    if uk and not any(s["key"] == uk for s in out):
        out = [s for s in D.stores if s["key"] == uk] + out
    return out

def find_deals(u, n=5):
    """[(חיסכון, טקסט)] – מבצעים בתוקף על מוצרים מהסל הקבוע, בסניפים הקרובים. מבצע = מחיר נמוך ב-20%+ מהמחיר הרגיל באותו סניף"""
    D = kdata.D; L = learned(u)
    if not L: return []
    sts = deal_stores(u)
    if not sts: return []
    best = {}
    for st in sts:
        base = D.prices(st["blob"]); P = D.store_prices(st, tuple((u.get("prefs") or {}).get("clubs") or ()))
        promo = D.prices(st["pblob"]) if st.get("pblob", -1) >= 0 else {}
        for x in L:
            i = resolve(D, x)
            if i is None or i not in base: continue
            reg, q = base[i], x["qty"] or 1
            cur, txt, end = P.get(i), None, None
            if cur is not None and cur < reg * (1 - DEAL_MIN_OFF):
                v = promo.get(i); end = v[1] if isinstance(v, tuple) else None
                txt = f"₪{money(cur)} במקום ₪{money(reg)}"
            elif i in P.deals and P.deals[i][0] < reg * (1 - DEAL_MIN_OFF) and P.deals[i][1] <= max(3, 2 * q):   # "9 ב-30" למי שקונה 1 – לא
                uu, k = P.deals[i]; cur = uu; q = max(q, k)
                bundle = uu * k; bundle = round(bundle) if abs(bundle - round(bundle)) < 0.06 else bundle      # 29.97 → 30
                txt = f"{k} ב־₪{money(bundle)} (במקום ₪{money(reg * k)})"
            if txt is None: continue
            save = (reg - cur) * q
            if save < DEAL_MIN_SAVE: continue
            nm = D.items[i][1].replace(' (לק"ג)', "")
            key = f"{x.get('code')}|{st['key']}|{money(cur)}"
            if key not in best or save > best[key][0]:
                till = ""
                if end and end < 2 ** 31:
                    t = _dt.fromtimestamp(end * 60, IL_TZ); till = f" (עד {t.day}/{t.month})"
                best[key] = (save, f"• *{nm}* – {txt} ב{st['n']}{till}", x.get("code"))
    by_item = {}                                                   # לכל מוצר – המבצע הכי משתלם בלבד
    for key, v in best.items():
        if v[2] not in by_item or v[0] > by_item[v[2]][0]: by_item[v[2]] = (v[0], v[1], key)
    return sorted(by_item.values(), reverse=True)[:n]

def send_deals(to, u, now=None):
    """שולח הודעת מבצעים אם יש מבצע חדש (שלא נשלח בשבוע האחרון) – מחזיר True אם נשלח"""
    now = now or time.time()
    pr = u.get("prefs") or {}
    if pr.get("deals_off") or not pr.get("home") or u.get("remind_miss", 0) >= 3: return False
    if now - u.get("deal_last", 0) < DEAL_EVERY_DAYS * 86400: return False
    if is_daybefore(u, _dt.fromtimestamp(now, IL_TZ)): return False   # הערב יש הודעת "מחר יום קניות" – לא שתי הודעות
    sent = {k: t for k, t in (u.get("deal_sent") or {}).items() if now - t < 7 * 86400}
    deals = [d for d in find_deals(u) if d[2] not in sent]
    if not deals: u["deal_sent"] = sent; return False
    for d in deals: sent[d[2]] = now
    u["deal_sent"] = sent; u["deal_last"] = now; save_users()
    tot = sum(d[0] for d in deals)
    send_buttons(to, f"🔥 *מבצעים על מוצרים שאתה קונה בדרך כלל* – חיסכון של עד ₪{tot:.0f}:\n\n" + "\n".join(d[1] for d in deals),
                 [("mine", "🛒 לחשב את הסל"), ("deals_off", "🔕 בלי התראות מבצעים")])
    return True

def deals_tick():
    """פעם ביום, בין 17:00 ל-21:00 – לכל משתמש פעיל עם סל קבוע"""
    il = _dt.now(IL_TZ); day = il.strftime("%Y-%m-%d")
    if not (DEAL_HOURS[0] <= il.hour < DEAL_HOURS[1]) or _deals["day"] == day: return
    _deals["day"] = day
    for to, u in list(USERS.items()):
        if to.startswith("_") or not isinstance(u, dict) or not is_tg(to): continue
        try: send_deals(to, u)
        except Exception as ex: print("deals error", to, ex, flush=True)

# ---------- שומר לילה: התראה למנהל כשמשהו בנתונים נשבר ----------
MAJOR = {"SHUFERSAL": "שופרסל", "RAMI_LEVY": "רמי לוי", "YOHANANOF": "יוחננוף", "OSHER_AD": "אושר עד",
         "TIV_TAAM": "טיב טעם", "YAYNO_BITAN_AND_CARREFOUR": "קרפור", "VICTORY_NEW_SOURCE": "ויקטורי", "HAZI_HINAM": "חצי חינם",
         "MAHSANI_ASHUK_NEW_SOURCE": "מחסני השוק", "NETIV_HASED": "נתיב החסד", "HET_COHEN_NEW_SOURCE": "ח. כהן"}
_watch = {"t": 0}
# רשתות שנאספות מהשרת בישראל (GCP). אורי 13/10: עד שהשרת מתוקן – השומר לא שולח עליהן התראות.
# ברגע שכל 5 הרשתות מתעדכנות שוב (פחות מ-6 שעות) – ההשתקה מתבטלת לבד, ונשלחת הודעה "השרת הישראלי חזר".
IL_SERVER = {"VICTORY_NEW_SOURCE": "ויקטורי", "HAZI_HINAM": "חצי חינם", "MAHSANI_ASHUK_NEW_SOURCE": "מחסני השוק",
             "NETIV_HASED": "נתיב החסד", "HET_COHEN_NEW_SOURCE": "ח. כהן"}

def il_server_back():
    """True כשכל רשתות השרת הישראלי עודכנו בשש השעות האחרונות"""
    D = kdata.D
    if not D or not getattr(D, "chains", None): return False
    ages = [_iso_age_h((D.chains.get(c) or {}).get("fetched", "")) for c in IL_SERVER]
    return all(a is not None and a < 6 for a in ages)

def il_muted():
    """ההתראות על רשתות השרת הישראלי מושתקות? (ברירת מחדל: כן, עד שהשרת חוזר)"""
    return USERS.setdefault("_meta", {}).get("il_mute", True)

# ---------- מונים לדוח השבועי ----------
def wk():
    """מונים של השבוע הנוכחי (נשמרים עם המשתמשים, מתאפסים אחרי שליחת הדוח)"""
    w = USERS.setdefault("_meta", {}).setdefault("wk", {})
    w.setdefault("since", int(time.time()))
    for k in ("baskets", "receipts", "r_partial", "r_bad", "errors"): w.setdefault(k, 0)
    for k in ("notfound", "missing"): w.setdefault(k, {})
    return w

def wk_count(key, names=None):
    try:
        w = wk()
        if names is None: w[key] = w.get(key, 0) + 1; return
        d = w[key]
        for n in names:
            n = str(n)[:60]
            if n: d[n] = d.get(n, 0) + 1
        if len(d) > 400:                                     # שומרים רק את השכיחים
            for k2 in sorted(d, key=d.get)[:len(d) - 300]: d.pop(k2)
    except Exception as ex:
        print("wk error", ex, flush=True)

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
    meta_ = USERS.setdefault("_meta", {})
    if meta_.get("il_mute", True) and il_server_back():          # השרת הישראלי חזר – מבטלים השתקה ומודיעים פעם אחת
        meta_["il_mute"] = False
        out.append("✅ *השרת הישראלי חזר לעבוד* – ויקטורי, חצי חינם, מחסני השוק, נתיב החסד וח. כהן מתעדכנים שוב. ההתראות עליהן חזרו.")
    for ch in dict.fromkeys(list(MAJOR) + list(D.chains) + [c for c in seen if c in CHAIN_HE]):   # כל הרשתות, לא רק הגדולות
        he = MAJOR.get(ch) or CHAIN_HE.get(ch, ch)
        if ch in IL_SERVER and il_muted(): continue               # מושתק עד שהשרת הישראלי מתוקן (אורי 13/10)
        info = D.chains.get(ch)
        if not info:                                              # רשת שלמה נעלמה (סופר ספיר 13/10 – העלאה שנכשלה)
            if seen.get(ch): out.append(f"⚠️ {he} נעלמה מהנתונים (היו {seen[ch]} סניפים)")
            continue
        a = _iso_age_h(info.get("fetched", ""))
        if a is not None and a > 12 and ch in MAJOR: out.append(f"⚠️ {he} לא התעדכנה כבר {a:.0f} שעות")
        n = info.get("stores", 0)
        if seen.get(ch) and n < 0.75 * seen[ch]: out.append(f"⚠️ {he} ירדה מ-{seen[ch]} ל-{n} סניפים")
        elif n: seen[ch] = max(n, int(seen.get(ch, 0) * 0.9))
    g = kdata.GATE
    if g: out.append(f"🛑 עדכון נתונים חדש נחסם: {g['reason']}.\nהבוט ממשיך עם הנתונים הקודמים והתקינים. "
                     "אם השינוי אמיתי (למשל רשת באמת סגרה סניפים) – כתבו לבוט: *אשר נתונים*")
    qa = getattr(D, "qa", {}) or {}
    for ch, n in (qa.get("by_chain") or {}).items():     # בדיקת תקינות: הרבה מחירים חשודים ברשת אחת = כנראה קובץ פגום
        if ch in IL_SERVER.values() and il_muted(): continue
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
  {"חזה עוף": 2, "בשר טחון": 0.5, "פילה סלמון": 0.3, "שניצל": 2, "נקניקיות": 1, "טונה": 1, "חומוס": 2,
   "טחינה": 1, "קפה": 1, "תה": 1, "קורנפלקס": 1, "בננ": 1, "תפוח": 3, "גזר": 1, "אבקת כביסה": 1,
   "מגבות נייר": 1, "משחת שיניים": 2}),
 ("""שמנת מתוקה – 2
חמאה – 1
לבן – 4 גביעים
גבינת שמנת – 1
קפה נמס – 1 צנצנת
פיתות – 2 חבילות
חלה – 1
אורז בסמטי – 1 ק"ג
עדשים – 1 חבילה
טונה – רביעייה
רסק עגבניות – 3
קטשופ – 1
מיונז – 1
פלפל אדום – 1 ק"ג
תפוחים – 2 ק"ג
לימון – חצי קילו
אבקת כביסה – 1
אקונומיקה – 1
שקיות אשפה – 1 גליל
חיתולים – 1 חבילה""",
  {"שמנת": 2, "חמאה": 1, "לבן": 4, "גבינת שמנת": 1, "קפה": 1, "פיתות": 2, "חלה": 1, "אורז": 1, "עדשים": 1, "טונה": 1,
   "רסק": 3, "קטשופ": 1, "מיונז": 1, "פלפל": 1, "תפוח": 2, "לימון": 0.5, "אבקת כביסה": 1, "אקונומיקה": 1, "שקיות": 1, "חיתולים": 1})]
SELFTEST_CITIES = {"נתיבות": [31.4215, 34.5925], "תל אביב": [32.0809, 34.7806], "ירושלים": [31.7857, 35.2007],
                   "חיפה": [32.7940, 34.9896], "באר שבע": [31.2518, 34.7913], "פתח תקווה": [32.0871, 34.8875],
                   "אשדוד": [31.8014, 34.6435], "ראשון לציון": [31.9640, 34.8040], "נתניה": [32.3215, 34.8532],
                   "רחובות": [31.8928, 34.8113], "בני ברק": [32.0840, 34.8338], "אשקלון": [31.6688, 34.5743],
                   "מודיעין": [31.8980, 35.0104], "בית שמש": [31.7470, 34.9881], "עפולה": [32.6078, 35.2897],
                   "טבריה": [32.7922, 35.5312], "כרמיאל": [32.9196, 35.2950], "קריית שמונה": [33.2075, 35.5703],
                   "אילת": [29.5577, 34.9519], "אופקים": [31.3155, 34.6210]}
SELFTEST_BASIC = ("חלב", "לחם", "ביצים", "עגבני", "מלפפו", "בצל", "תפוח", "גזר", "סוכר", "אורז", "פסטה", "שמן", "קמח", "בננ")
_selftest = {"day": ""}

BIG_CHAINS = ("רמי לוי", "שופרסל", "יוחננוף", "ויקטורי", "אושר עד", "קרפור", "מחסני השוק", "יילו")

def sells_kind(store_name, item_name):
    """מוצר בסיסי "חסר" ברשת גדולה = כנראה התאמה שגויה (רשת גדולה תמיד מוכרת בצל/חלב/סוכר); בחנות קטנה זה יכול להיות אמיתי"""
    st = next((x for x in kdata.D.stores if x["n"] == store_name), None)
    return bool(st) and st["chain"] in BIG_CHAINS

def selftest():
    """מחזיר (רשימת בעיות, שורת סיכום)"""
    D = kdata.D; probs = []; lines = []; miss_at = {}
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
                    if v and v[1] and any(norm(it.get("src", "")).startswith(b) for b in SELFTEST_BASIC) and sells_kind(r["store"], it["name"]):
                        miss_at.setdefault((li, it.get("src"), it["name"]), set()).add(r["store"])
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
    for (li, src, name), sts in miss_at.items():           # מסוכם: מוצר אחד שחסר בהרבה סניפים = שורה אחת
        if len(sts) < 2: continue                          # סניף בודד בלי המוצר בקובץ – כנראה חוסר אמיתי, לא טעות שלנו
        probs.append(f"רשימה {li}: \"{src}\" יצא חסר ב-{len(sts)} סניפים ({', '.join(sorted(sts)[:3])}{'…' if len(sts) > 3 else ''}) – הותאם ל\"{name}\"")
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

# ---------- דוח שבועי למנהל: מצב כל רשת + סניפים שלא עובדים + מה המשתמשים לא מצאו ----------
CHAIN_HE = {"SHUFERSAL": "שופרסל", "RAMI_LEVY": "רמי לוי", "YOHANANOF": "יוחננוף", "OSHER_AD": "אושר עד", "VICTORY_NEW_SOURCE": "ויקטורי",
            "TIV_TAAM": "טיב טעם", "YAYNO_BITAN_AND_CARREFOUR": "קרפור", "HAZI_HINAM": "חצי חינם", "MAHSANI_ASHUK_NEW_SOURCE": "מחסני השוק",
            "KESHET": "קשת טעמים", "BAREKET": "ברקת", "CITY_MARKET_KIRYATGAT": "סיטי מרקט", "CITY_MARKET_SHOPS": "סיטי מרקט",
            "DOR_ALON": "דור אלון", "HET_COHEN_NEW_SOURCE": "ח. כהן", "KING_STORE": "קינג סטור", "NETIV_HASED": "נתיב החסד",
            "POLIZER": "פוליצר", "SALACH_DABACH": "סאלח דבאח", "SHEFA_BARCART_ASHEM": "שפע ברכת השם", "SHUK_AHIR": "שוק העיר",
            "STOP_MARKET": "סטופ מרקט", "SUPER_YUDA": "סופר יודה", "SUPER_SAPIR": "סופר ספיר", "FRESH_MARKET_AND_SUPER_DOSH": "פרש מרקט",
            "YELLOW": "יילו", "ZOL_VEBEGADOL": "זול ובגדול"}
STALE_H = 48          # סניף שהמחירים שלו ישנים מיומיים = "לא מתעדכן"

def _ts_age_h(ts):
    try: return (time.time() - _dt.strptime(str(ts)[:12], "%Y%m%d%H%M").replace(tzinfo=IL_TZ).timestamp()) / 3600
    except Exception: return None

def track_stores():
    """זוכר אילו סניפים היו בנתונים, כדי לדעת אילו "נעלמו" (רשת הפסיקה לפרסם להם מחירים)"""
    D = kdata.D
    if not D or D.source != "remote": return
    seen = USERS.setdefault("_meta", {}).setdefault("stores_seen", {}); now = int(time.time())
    for st in D.stores: seen[st["key"]] = [st["chain"], st["n"], now]
    for k in [k for k, v in seen.items() if now - v[2] > 30 * 86400]: seen.pop(k)   # נעלם מעל חודש – מפסיקים לדווח

def weekly_report():
    D = kdata.D; w = wk(); meta = USERS.setdefault("_meta", {}); now = int(time.time())
    if not D: return "אין נתונים"
    cur = {st["key"] for st in D.stores}
    gone = {}
    for k, (ch, n, last) in (meta.get("stores_seen") or {}).items():
        if k not in cur and now - last > 86400: gone.setdefault(ch, []).append(n)
    by = {}
    for st in D.stores: by.setdefault(st["chain"], []).append(st)
    info = {}                                                  # מצב ההורדה של כל רשת (מהשרת/GitHub)
    for sc, i in (D.chains or {}).items():
        he = CHAIN_HE.get(sc, sc); x = info.setdefault(he, {"age": None, "nolist": 0})
        a = _iso_age_h(i.get("fetched", ""))
        if a is not None: x["age"] = a if x["age"] is None else max(x["age"], a)
        stt = i.get("stats") or {}
        x["nolist"] += max(0, stt.get("stores_listed", 0) - stt.get("stores_with_prices", 0))
    big = [c for c in BIG_CHAINS if c in by] + sorted((c for c in by if c not in BIG_CHAINS), key=lambda c: -len(by[c]))
    ok, bad = [], []
    for ch in big:
        stale = [st for st in by[ch] if (_ts_age_h(st.get("ts")) or 0) > STALE_H]
        g = gone.get(ch, []); i = info.get(ch, {}); probs = []
        if i.get("age") is not None and i["age"] > 12: probs.append(f"❌ הרשת לא התעדכנה כבר {i['age']:.0f} שעות")
        if stale: probs.append(f"⏳ {len(stale)} סניפים עם מחירים ישנים (מעל יומיים): " + ", ".join(st["n"].replace(ch, "").strip() for st in stale[:6]) + (" ועוד" if len(stale) > 6 else ""))
        if g: probs.append(f"🚫 {len(g)} סניפים נעלמו מהנתונים: " + ", ".join(n.replace(ch, "").strip() for n in g[:6]) + (" ועוד" if len(g) > 6 else ""))
        if probs: bad.append(f"⚠️ *{ch}* – {len(by[ch])} סניפים\n" + "\n".join("   " + p for p in probs))
        else: ok.append(f"{ch} ({len(by[ch])})")
    since = _dt.fromtimestamp(w.get("since", now), IL_TZ).strftime("%d/%m")
    L = [f"🗓️ *דוח שבועי – קונה אישי* ({since}–{_dt.now(IL_TZ).strftime('%d/%m')})", "",
         f"🏪 *מצב הרשתות* – {len(D.stores):,} סניפים, {len(by)} רשתות"]
    L += bad or ["כל הרשתות והסניפים מתעדכנים כרגיל 👌"]
    if ok: L.append("✅ תקין: " + ", ".join(ok))
    nl = sorted(((c, x["nolist"]) for c, x in info.items() if x["nolist"] >= 3), key=lambda x: -x[1])[:5]
    if nl: L.append("📭 סניפים ברשימת הרשת בלי קובץ מחירים (בדרך כלל סגורים/חדשים): " + ", ".join(f"{c} {n}" for c, n in nl))
    nb = w.get("receipts", 0)
    L += ["", "👥 *שימוש השבוע*", f"🛒 קניות שנבדקו: {w.get('baskets', 0)} · 🧾 קבלות: {nb}"
          + (f" (חלקיות: {w.get('r_partial', 0)}, לא נקראו: {w.get('r_bad', 0)})" if nb else ""),
          f"🚨 תקלות: {w.get('errors', 0)}"]
    acc = w.get("acc") or {}
    if acc:
        tot = sum(v[0] for v in acc.values()); good = sum(v[1] for v in acc.values())
        L += ["", f"🧾 *דיוק מול קבלות אמיתיות*: {good}/{tot} מחירים תואמים ({good / max(tot, 1):.0%})"]
        L += [f"• {c}: {v[1]}/{v[0]}" for c, v in sorted(acc.items(), key=lambda x: -x[1][0])[:6]]
        for b in (w.get("acc_bad") or [])[:6]:
            L.append(f"   ⚠️ {b[0]} – {b[1]}: בקבלה ₪{b[2]} · אצלנו ₪{b[3]}")
    top = lambda d, k=8: sorted(d.items(), key=lambda x: -x[1])[:k]
    if w.get("notfound"):
        L += ["", "🔎 *מוצרים שהבוט לא מצא* (כדאי ללמד אותו):"] + [f"• {n} ×{c}" for n, c in top(w["notfound"])]
    if w.get("missing"):
        L += ["", "❌ *יצאו \"חסר\" בסופר המומלץ הכי הרבה:*"] + [f"• {n} ×{c}" for n, c in top(w["missing"])]
    if il_muted():
        L += ["", "🔇 *השרת הישראלי* (ויקטורי, חצי חינם, מחסני השוק, נתיב החסד, ח. כהן): ההתראות מושתקות עד שהשרת יחזור לעבוד."]
    bl = meta.get("basics_last") or []
    if bl:
        L += ["", "🥬 *מוצרים בסיסיים שחסרים ברוב הסניפים של רשת* (שם אחר בקבצים / לא נמכר טרי):"]
        L += [f"• {b[0]}: {b[1]} – {b[2]}/{b[3]} סניפים" for b in sorted(bl, key=lambda b: -b[2] / max(b[3], 1))[:12]]
    return "\n".join(L)

_weekly_sent = {"wk": ""}
# ---------- שומר מוצרים בסיסיים: הבוט מזהה לבד שם חדש של מוצר בסיסי (לימוד עצמי, 12/10) ----------
# כל יום: לכל רשת – באיזה חלק מהסניפים כל מוצר בסיסי "חסר". אם פתאום חסר בהרבה סניפים של רשת, כנראה הרשת שינתה את השם
# ("עגבניות טבע" במקום "עגבנייה"). מתריעים לאורי עם השמות הדומים שנמצאו, כדי שאפשר יהיה להוסיף כלל (או שהלימוד העצמי כבר תפס).
BASIC_WATCH = ["P01", "P03", "P10", "P12", "P16", "P28", "P39", "P04", "P29", "E01", "M01", "M07", "M08", "M02"]
_basics = {"day": ""}

def basic_coverage(min_stores=5, bad_share=0.4):
    """[(רשת, מוצר, חסרים, סניפים, [שמות דומים בסניפים האלה])] – רק בעיות"""
    D = kdata.D
    if not D: return []
    its = []
    for c in BASIC_WATCH:
        i = D.code2i.get(c)
        if i is not None: its.append({"name": D.items[i][1], "qty": 1, "i": i, "g": [i], "fb": D.fallback(i), "wfb": None, "frz": frozen_alts(i), "pk": packed_alts(i)})
    by = {}
    for st in D.stores:
        if st["chain"] in ("יילו", "דור אלון", "סופר יודה") or re.search(r"\bBE\b|Be\b|אקספרס|AM|PM", st["n"]): continue
        by.setdefault(st["chain"], []).append(st)
    out = []
    for ch, sts in by.items():
        if len(sts) < min_stores: continue
        miss = {k: [] for k in range(len(its))}
        sizes = {}
        for st in sts:
            P = D.store_prices(st, (), cache=False)          # סריקה של כל הסניפים – בלי לנפח את המטמון (זיכרון)
            sizes[st["key"]] = len(P)
            if len(P) < 1500: continue                       # סניף קטן/קובץ חלקי – לא מעיד על הרשת
            for k, it in enumerate(its):
                if lc(P, it) is None: miss[k].append(P)
        n = sum(1 for st in sts if sizes.get(st["key"], 0) >= 1500)
        for k, Ps in miss.items():
            if n >= min_stores and len(Ps) >= bad_share * n:
                w0 = D.meta(its[k]["i"])[0]; sim = collections.Counter()
                for P in Ps[:15]:
                    for j in P:
                        if D.meta(j)[0] == w0 and j not in its[k]["fb"] and j != its[k]["i"]: sim[D.items[j][1]] += 1
                out.append((ch, its[k]["name"].replace(' (לק"ג)', ""), len(Ps), n, [x for x, _ in sim.most_common(3)]))
    return out

def basics_daily(now=None):
    """פעם ביום (אחרי 08:00): התראה לאורי רק על בעיה חדשה – בעיה שכבר דווחה לא נשלחת שוב. now – לבדיקות (שעה קבועה)"""
    il = now or _dt.now(IL_TZ); day = il.strftime("%Y-%m-%d")
    if il.hour < 8 or _basics["day"] == day: return
    _basics["day"] = day
    meta = USERS.setdefault("_meta", {}); first = "basics_known" not in meta; known = set(meta.get("basics_known") or [])
    probs = basic_coverage()
    if il_muted(): probs = [p for p in probs if p[0] not in IL_SERVER.values()]   # מחירים ישנים – לא מעיד על שם חדש
    keys = {f"{ch}|{nm}" for ch, nm, *_ in probs}
    new = [p for p in probs if f"{p[0]}|{p[1]}" not in known]
    meta["basics_known"] = sorted(keys); meta["basics_last"] = probs[:40]; save_users()
    if first or not new: return                              # הפעם הראשונה – רק שומרים מצב התחלתי (הרשימה המלאה בדוח השבועי)
    L = ["🥬 *שומר מוצרים בסיסיים* – מוצר בסיסי \"נעלם\" ברשת (כנראה שם חדש בקבצים):", ""]
    for ch, nm, k, n, sim in new[:12]:
        names = ", ".join(x.replace(' (לק"ג)', "") for x in sim)
        L.append(f"⚠️ {ch}: *{nm}* חסר ב-{k} מתוך {n} סניפים" + (f"\n      שמות דומים שם: {names}" if sim else ""))
    for a in [a for a in admins() if is_tg(a)]: send_text(a, "\n".join(L))

def weekly_report_tick():
    """כל יום ראשון ב-10:00 – שולחים את הדוח ומאפסים את המונים"""
    il = _dt.now(IL_TZ)
    track_stores() if il.minute == 0 else None
    tag = il.strftime("%G-%V")
    if il.weekday() != 6 or il.hour < 10 or _weekly_sent["wk"] == tag: return
    meta = USERS.setdefault("_meta", {})
    if meta.get("weekly_sent") == tag: _weekly_sent["wk"] = tag; return
    _weekly_sent["wk"] = tag; meta["weekly_sent"] = tag
    txt = weekly_report()
    for a in [a for a in admins() if is_tg(a)]: send_text(a, txt)
    meta.pop("wk", None); save_users()

def _reminder_loop():
    while True:
        time.sleep(60)
        try: watchdog()
        except Exception as ex: print("watchdog error", ex, flush=True)
        try: monthly_summaries()
        except Exception as ex: print("summary error", ex, flush=True)
        try: selftest_daily()
        except Exception as ex: print("selftest error", ex, flush=True)
        try: weekly_report_tick()
        except Exception as ex: print("weekly report error", ex, flush=True)
        try: basics_daily()
        except Exception as ex: print("basics watch error", ex, flush=True)
        try: daybefore_tick()
        except Exception as ex: print("day-before tick error", ex, flush=True)
        try: deals_tick()
        except Exception as ex: print("deals tick error", ex, flush=True)
        now = time.time()
        for to, u in list(USERS.items()):
            try:
                ra = u.get("remind_at")
                if not ra or ra > now or u.get("prefs", {}).get("remind_off") or not is_tg(to): continue
                if shop_day(u, now) is not None: continue      # יש יום קניות קבוע – מקבל את הודעת "מחר יום קניות" במקום
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

# ---------- רשימה לפי מחלקות בסופר (סעיף 5, בקשה 12/10) ----------
# הסדר = סדר הליכה טיפוסי בסופר. כל מחלקה: (כותרת, מילות מפתח). הבדיקה לפי הסדר של DEPT_CHECK – הראשונה שמתאימה מנצחת.
DEPTS = [("produce", "🥬 ירקות ופירות"), ("bread", "🍞 לחם ומאפים"), ("dairy", "🥛 מוצרי חלב וביצים"), ("meat", "🥩 בשר, עוף ודגים"),
         ("frozen", "🧊 קפואים"), ("pantry", "🥫 מזווה ובישול"), ("hot", "☕ קפה ותה"), ("snacks", "🍫 חטיפים ומתוקים"),
         ("drinks", "🥤 משקאות"), ("clean", "🧽 ניקיון ובית"), ("care", "🧴 טיפוח ותינוקות"), ("other", "📦 שונות")]
DEPT_NAME = dict(DEPTS)
# מילת מפתח = תחילת מילה ("גבינ" תופס גבינה/גבינת). מילה שמסתיימת ב-"|" = מילה שלמה בלבד ("תה|" לא תופס "תהילה").
DEPT_KW = {
    "clean": "ניקוי לניקוי נוזל|כלים סבון|כלים אבקת|כביסה ג'ל|כביסה מרכך|כביסה כביסה קפסולות|לכביסה אקונומיק מטהר נייר|טואלט נ.טואלט מגבות|נייר "
             "טישו ממחטות שקיות|אשפה שקיות|זבל נייר|אלומיניום נייר|כסף נייר|אפייה ניילון|נצמד ספוג סקוץ' מסיר|שומנים מדיח סנו פיירי "
             "מטליות מפיות צלחות כוסות|חד סכו\"ם מגב מטאטא דלי|ספונג'ר פחם כפפות תבנית מפת חול|לחתולים מזון|לחתולים מזון|חתולים "
             "מזון|לכלבים מזון|כלבים מצתים גפרורים נרות סוללות",
    "care": "שמפו מרכך|שיער סבון|גוף תחליב|רחצה תרחיץ סבון|ידיים סבון|נוזלי משחת|שיניים מברשת|שיניים דאודורנט דאו. קרם|ידיים "
            "קרם|גוף קרם|לחות קרם|יום קרם|לילה קרם|פנים חיתולים טיטולים האגיס פמפרס מגבונ תחבושות טמפונים סכיני|גילוח ג'ל|גילוח "
            "צמר|גפן מקלוני פדים מטרנה סימילאק נוטרילון בייבי|ביס מוצצ בושם א.ד.פ שפתון לק|",
    "frozen": "קפוא קפואה קפואים מוקפא גליד ארטיק שלגון בצק|עלים פיצה בורקס מלאווח ג'חנון לזניה נאגטס קרפלך",
    "meat": "עוף פרגית פרגיות חזה|עוף חזה שוקיים כרעיים ירכיים כנפיים בשר טחון|בקר אנטריקוט צלעות סטייק נקניק פסטרמה קבנוס סלמי "
            "קבב המבורגר שניצל הודו סלמון אמנון מושט דניס לברק בורי טונה|טרייה פילה|דג דג| דגים כבד|עוף לבבות שווארמה קורקבן "
            "כתף צלי אסאדו שייטל פילה|בקר כבש טלה",
    "dairy": "חלב| חלב|3% חלב|1% קוטג גבינ יוגורט יופלה דנונה מולר מעדן שמנת חמאה| לבן|3% לבנה אשל| גיל| ביצים ביצה| שוקו| מילקי "
             "פודינג בולגרית צפתית מוצרלה פרמזן ריקוטה קצפת גאודה צהובה קשקבל מימולט ברי| קממבר אמנטל צ'דר פרובולונה רוקפור "
             "עיזים טוב|טעם דנונה אקטיביה פרו| חלבון",
    "bread": "לחם לחמני פיתה פיתות חלה| חלות באגט בגט טורטיה טורטיות לאפה קרואסון מאפה פריכיות לחמית צנימים",
    "hot": "קפה תה| נס|קפה קפסולות אספרסו נספרסו קקאו סוכרזית ממתיק",
    "snacks": "במבה ביסלי חטיף שוקולד עוגיות עוגיה עוגת עוגה| וופל סוכריות מסטיק תפוצ'יפס צ'יפס דוריטוס אפרופו פופקורן "
              "בייגלה קליק פסק|זמן כיף|כף מקופלת קרמבו גרעינים בוטנים אגוז שקדים פיצוחים קשיו פיסטוק פקאן ליקריץ פרלין "
              "מרציפן קרקר קרקרים טוק| חטיפי פתי|בר ציפס קוקיס רוגלך",
    "drinks": "מים|מינרליים מים| מי|עדן סודה קולה פפסי ספרייט פאנטה שוופס מיץ משקה נקטר פריגת ספרינג תפוזינה בירה יין| וודקה "
              "ערק ויסקי וויסקי ג'ין| רום| אנרגיה רד|בול קינלי פטל| תרכיז סירופ ליקר",
    "pantry": "פסטה ספגטי אטריות פתיתים אורז קוסקוס בורגול קינואה עדשים שעועית חומוס טחינה קמח סוכר מלח| פלפל|שחור פפריקה "
              "כמון תבלין תבליני אבקת|מרק מרק שמן חומץ קטשופ מיונז חרדל רוטב רסק שימורים טונה סרדינים תירס|שימורים אפונה "
              "זיתים חמוצים ריבה קונפיטורה דבש ממרח נוטלה חמאת|בוטנים דגני|בוקר קורנפלקס שיבולת|שועל גרנולה פירורי פרורי "
              "שמרים אבקת|אפייה וניל סילאן פירה שקדי|מרק קרוטונים קורנפלור ג'לי חיטה צלפים תירס סולת זעתר קוקוס רביולי ניוקי טורטליני",
    "produce": "עגבני מלפפון בצל שום תפוח תפוחי|אדמה בטטה גזר פלפל חסה כרוב כרובית ברוקולי קישוא חציל לימון תפוז קלמנטינה "
               "אשכולית בננ אבוקדו ענבים אבטיח מלון| אגס שזיף אפרסק נקטרינה מנגו תות| תותים פטרוזיליה כוסברה שמיר נענע סלרי "
               "פטריות פטריה סלק צנון קולרבי דלעת רימון אפרסמון קיווי אננס נבטים ג'ינג'ר שרי| דובדבנים פומלה",
}
DEPT_OTHER = re.compile(r"(?<![א-ת])(סיגרי|טבק|מצית|מחברת|עט|צעצוע|כרית|תיק|מגבת|צבת)")
DEPT_PET = re.compile(r"(?<![א-ת])(מזון ל?חתול|מזון ל?כלב|פריסקיז|פדיגרי|ויסקס|חול לחתול|חטיף לכלב)")
DEPT_LIQUID = re.compile(r"(\d\s*(ליטר|ל'|ל\b|מ\"?ל|מל\b)|סיידר|פחית|מוגז|משקה)")
DEPT_PROCESSED = re.compile(r"(?<![א-ת])(מיובש|קצוצ|מרוסק|שימורים|במלח|כבוש|ממרח|ברוטב|מוחמץ|חמוץ|חמוצ|בשמן|רסק|קלוי|בחומץ)")
DEPT_CHECK = ("clean", "care", "frozen", "meat", "dairy", "bread", "hot", "snacks", "drinks", "pantry", "produce")

def _kw_compile():
    out = {}
    for d, words in DEPT_KW.items():
        pre, full = [], []
        for w in words.split():
            if w.endswith("|") or w.startswith("|"): full.append(w.strip("|").replace("|", " "))
            else: pre.append(w.replace("|", " "))
        parts = []
        FIN = {"ן": "[ןנ]", "ם": "[םמ]", "ך": "[ךכ]", "ף": "[ףפ]", "ץ": "[ץצ]"}   # "מלפפון" גם בתחילת "מלפפונים"
        pre_rx = {re.escape(x[:-1]) + FIN[x[-1]] if x[-1] in FIN else re.escape(x) for x in pre}
        if pre: parts.append("(?:" + "|".join(sorted(pre_rx, key=len, reverse=True)) + ")")
        if full: parts.append("(?:" + "|".join(sorted({re.escape(x) for x in full}, key=len, reverse=True)) + r")(?![א-ת])")
        out[d] = re.compile(r"(?<![א-תa-zA-Z])(?:" + "|".join(parts) + ")")
    return out
DEPT_RX = _kw_compile()

def dept_of(code, name):
    """לאיזו מחלקה בסופר שייך המוצר: לפי המילה שמופיעה הכי מוקדם בשם ("עוגיות ... שוקו" = חטיפים, "יוגורט ... שוקולד" = חלב)"""
    c = (code or "")[:1]
    if c == "P": return "produce"
    if c == "M": return "meat"
    if c == "E": return "dairy"
    n = name.replace('"', "").replace("׳", "'").replace("`", "'")
    if DEPT_OTHER.match(n): return "other"
    if DEPT_PET.search(n): return "clean"                               # מזון לחיות – מדף "בית"
    best = None
    for k, d in enumerate(DEPT_CHECK):
        m = DEPT_RX[d].search(n)
        if m and (best is None or (m.start(), k) < best[0]): best = ((m.start(), k), d)
    if not best: return "other"
    d = best[1]
    if d == "produce" and DEPT_PROCESSED.search(n): return "pantry"     # "עגבניות קצוצות", "מלפפונים במלח" – מדף שימורים
    if d == "produce" and DEPT_LIQUID.search(n): return "drinks"        # "ג'אמפ תפוחים 1.5 ליטר" – מיץ, לא פרי
    return d

SHOP_MAX = 60     # מגבלת כפתורים סבירה בהודעת טלגרם אחת

def start_shop(u, last):
    """רשימת קניות חיה: המוצרים לפי סדר המחלקות בסופר, כל מוצר כפתור שמסמנים ✅"""
    ip = last.get("ip") or {}
    order = {d: k for k, (d, _) in enumerate(DEPTS)}
    rows = []
    for x in u["items"]:
        kg = 'לק"ג' in x["name"]
        nm = x["name"].replace(' (לק"ג)', "")
        q = (f" ×{x['qty']:g}ק\"ג" if kg else f" ×{x['qty']:g}") if x["qty"] != 1 else ""
        pr = ip.get(x.get("code", ""))
        miss = " ⚠️" if pr and pr[1] else ""                    # לא נמצא בסניף הזה – שיידעו מראש
        rows.append((order[dept_of(x.get("code", ""), x["name"])], (nm[:28] + q + miss)))
    rows.sort(key=lambda r: r[0])                               # sort יציב – בתוך מחלקה נשמר הסדר
    u["shop"] = {"store": last.get("store", ""), "nav": last.get("nav"), "rows": [t for _, t in rows][:SHOP_MAX],
                 "done": [], "t": int(time.time())}

def shop_keyboard(u):
    sh = u["shop"]
    kb = [[{"text": ("✅ " if k in sh["done"] else "⬜ ") + t, "callback_data": f"chk:{k}"}] for k, t in enumerate(sh["rows"])]
    last = [tg_btn("shopdone", "✅ סיימתי")]
    if sh.get("nav"): last.insert(0, tg_btn(sh["nav"], "🧭 ניווט"))
    return kb + [last]

def send_shop(to, u):
    sh = u["shop"]
    txt = (f"סגור 👍 הקנייה ב*{sh['store']}*\n🛒 *רשימת הקניות* – לפי מחלקות. לוחצים על מוצר כשהוא בעגלה."
           + ("\n⚠️ = לא מצאתי בסניף הזה" if any(t.endswith("⚠️") for t in sh["rows"]) else ""))
    return tg_call("sendMessage", {"chat_id": to[3:], "text": tg_html(txt), "parse_mode": "HTML",
                                   "reply_markup": {"inline_keyboard": shop_keyboard(u)}})

def understood_text(u, missed=()):
    last = u.get("last") or {}; ip = last.get("ip") or {}
    by_dept = len(u["items"]) >= 5                     # סל קטן – רשימה רגילה; סל גדול – לפי מחלקות בסופר
    L = [f"🧾 *זיהיתי {len(u['items'])} מוצרים*" + (f" – מחירים ב{last['store']}" if ip and last.get("store") else "")
         + (" (לפי מחלקות בסופר)" if by_dept else "") + ":", ""]
    tot = 0
    def toks(t): return {kdata.stem(w) for w in kdata.toks(t)}
    rows = []
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
        rows.append((dept_of(x.get("code", ""), x["name"]), f"• {nm}{q}{price}{alt}"))
    if by_dept:
        order = {d: k for k, (d, _) in enumerate(DEPTS)}
        cur = None
        for d, line in sorted(rows, key=lambda r: order[r[0]]):   # sorted יציב – בתוך מחלקה נשמר הסדר המקורי
            if d != cur:
                if cur is not None: L.append("")
                L.append(f"*{DEPT_NAME[d]}*"); cur = d
            L.append(line)
    else:
        L += [line for _, line in rows]
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
        found, missed = match_items(its, u)
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
    found, _ = match_items([item], u)
    if not found: return send_text(to, f"לא מצאתי את *{item['name']}* במאגר 🙁 נסו לכתוב קצת אחרת.")
    x = found[0]; D = kdata.D
    pr = fill_prefs(u["prefs"]); home = pr["home"]; clubs = set(pr.get("clubs") or [])
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
    wk_count("receipts")
    if not items or meta.get("quality") == "bad": wk_count("r_bad")
    elif meta.get("quality") == "partial": wk_count("r_partial")
    if not items:
        tip = ISSUE_TIP.get(meta.get("issue"), "נסו לצלם שוב – על משטח ישר, באור טוב, כשהקבלה ממלאת את המסך.")
        return send_text(to, "לא הצלחתי לקרוא את הקבלה 🙁\n" + tip + "\nאפשר גם פשוט לכתוב מה קניתם ✍️")
    now = int(time.time())
    # תמונה נוספת זמן קצר אחרי הקודמת – כנראה עוד חלק מאותה קבלה: מחברים לאותו סל
    if u.get("rbuf_t", 0) > now - MERGE_SEC and u.get("items") and u.get("step") in ("done", "forgot"):
        found, missed = match_items(items, u)
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
    u["rstore"] = sk                                         # הסניף של הקבלה – בסיס ההשוואה, במחירים ששולמו
    if meta.get("quality") == "partial":
        u["note"] = "⚠️ חלק מהקבלה לא היה ברור – אפשר לשלוח עכשיו צילום של החלק החסר ואחבר אותו, או לכתוב מה חסר ✍️"
    res = got_items(to, u, items)
    try: verify_saving(to, u, sk)
    except Exception as ex: print("verify saving error", ex, flush=True)
    try: receipt_check(sk, items, u.get("items") or [])
    except Exception as ex: print("receipt check error", ex, flush=True)
    return res


def verify_saving(to, u, sk):
    """סעיף 3 (אורי 20/10): אחרי "✅ נלך לשם" – הקבלה מהסופר שהמלצנו עליו. כמה אותם מוצרים היו עולים בסופר שהשווינו אליו,
    מול מה ששולם בפועל. רק מוצרים שיש בשניהם, ורק אם יש לפחות 3 כאלה. חיסכון אמיתי, לא על הנייר."""
    w = u.get("went")
    if not w or time.time() - w.get("t", 0) > 10 * 86400: u.pop("went", None); return None
    if not sk or sk != w.get("store"): return None
    D = kdata.D; ref = next((s for s in D.stores if s["key"] == w.get("ref")), None)
    u.pop("went", None)
    if not ref: return None
    P = D.store_prices(ref, tuple((u.get("prefs") or {}).get("clubs") or ()))
    paid = refc = 0.0; n = 0
    for x in u.get("items") or []:
        if not x.get("paid"): continue
        i = resolve(D, x)
        if i is None: continue
        q = x.get("src") or x["name"]
        it = {"name": x["name"], "qty": x["qty"], "i": i, "g": D.group(i, q), "fb": D.fallback(i, q), "wfb": None,
              "frz": frozen_alts(i), "pk": packed_alts(i), "sc": D.scaled_alts(i, q)}
        c = lc(P, it)
        if c is None or not 0.4 <= c[0] / x["paid"] <= 2.5: continue
        paid += x["paid"]; refc += c[0]; n += 1
    if n < 3: save_users(); return None
    real = refc - paid
    u["real"] = (u.get("real") or []) + [{"d": int(time.time()), "saved": round(real, 2), "n": n}]
    u["real"] = u["real"][-24:]; save_users()
    if real >= 1:
        send_text(to, f"💚 *חסכת באמת ₪{real:.0f}* בקנייה הזאת – לפי הקבלה, לעומת {ref['n']} ({n} מוצרים).")
    else:
        send_text(to, f"🤔 הפעם לא נחסך: אותם {n} מוצרים היו עולים ב{ref['n']} ₪{refc:.0f}, ושילמת ₪{paid:.0f}.")
    return real

def receipt_check(store_key, rows, found):
    """בדיקת דיוק מהשטח: המחיר בקבלה מול המחיר שלנו לאותו מוצר באותו סניף (נצבר לדוח השבועי)"""
    D = kdata.D
    st = next((x for x in D.stores if x["key"] == store_key), None) if store_key else None
    if not st: return
    P = D.store_prices(st, set()); paid = {r["name"]: r for r in rows if r.get("price")}
    w = wk(); acc = w.setdefault("acc", {}); bad = w.setdefault("acc_bad", [])
    ch = acc.setdefault(st["chain"], [0, 0])               # [נבדקו, תואמים]
    deals = getattr(P, "deals", {})
    for f in found:
        # 20/10: רק שורות שזוהו לפי ברקוד (בדיוק אותו מוצר), עם המחיר והכמות של השורה עצמה
        r = {"price": f["paid"], "qty": f["qty"]} if f.get("bc") and f.get("paid") else None
        i = D.code2i.get(f.get("code", ""))
        if not r or i is None or i not in P: continue
        ours = lc(P, {"g": [i], "qty": r["qty"]})
        if not ours: continue
        ours = ours[0]; ok = abs(ours - r["price"]) <= max(0.5, 0.03 * r["price"])
        if not ok and i in deals and abs(P[i] * r["qty"] - r["price"]) <= max(0.5, 0.03 * r["price"]): ok = True   # מבצע כמות שלא הופעל בקופה
        ch[0] += 1; ch[1] += ok
        if not ok and len(bad) < 60:
            bad.append([st["n"], f["name"][:40], round(r["price"], 2), round(ours, 2)])

UNSURE_RATIO = 3.0      # שתי האפשרויות הראשונות שונות במחיר (לגרם) פי 3+ ובסוג, ושתיהן נפוצות באותה מידה – שואלים "התכוונת ל…?"
ASK_UNSURE = True

def unsure_item(x):
    """שם קצר וכללי מרשימה כתובה ("גבינה", "שמן") שהאפשרויות שלו שונות מאוד – עדיף לשאול פעם אחת מאשר לנחש.
    לא בקבלה (יש ברקוד/מחיר), לא מוצר בסיסי מאוחד, לא שם שכבר נבחר בעבר (זיכרון). מחזיר רשימת אפשרויות או None."""
    D = kdata.D
    if not ASK_UNSURE or x.get("sure") or x.get("paid") or not x.get("c"): return None
    t = kdata.toks(x.get("src") or "")
    if not t or len(t) > 2 or any(w.isdigit() for w in t): return None
    i = D.code2i.get(x["code"]); opts = [i] + [D.code2i[c] for c in x["c"] if c in D.code2i]
    if i is None or len(opts) < 2: return None
    sz = lambda j: D.size(j) or 0
    per = lambda j: D.items[j][3] / (sz(j) or 1000)                 # מחיר לגרם/מ"ל (או ליחידה)
    a, b = opts[0], opts[1]
    kind = lambda j: [kdata.stem(w) for w in kdata.toks(D.items[j][1])][:2]
    if kind(a) == kind(b) or not D.items[a][3] or not D.items[b][3]: return None
    if D.items[b][2] < 0.8 * D.items[a][2]: return None              # הנבחר נפוץ בהרבה – כנראה זה (לחם אחיד, לא לחם מחמצת)
    r = max(per(a), per(b)) / max(1e-9, min(per(a), per(b)))
    return opts[:3] if r >= UNSURE_RATIO else None

def got_items(to, u, items):
    found, missed = match_items(items, u)
    wk_count("notfound", missed)
    if not found:
        return send_text(to, "לא מצאתי את המוצרים האלה במאגר 🙁 נסה לכתוב אותם קצת אחרת." + (f"\n({', '.join(missed[:8])})" if missed else ""))
    u["items"] = found; u["missed"] = missed[:8]
    for k, x in enumerate(found):                       # סעיף 1 (אורי 20/10): מוצר לא ודאי – שאלה אחת, במקום לנחש
        opts = unsure_item(x)
        if opts:
            u["clarify"] = {"k": k, "opts": [kdata.D.items[j][0] for j in opts]}; u["step"] = "clarify"; save_users()
            return send_buttons(to, f"🤔 *{x['src']}* – למה התכוונת?",
                                [(f"cl:{j}", kdata.D.items[c][1][:40]) for j, c in enumerate(opts)] + [("cl:any", "🤷 לא משנה")])
    return after_match(to, u)

def after_match(to, u):
    found = u.get("items") or []
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

def in_israel(la, lo):
    """בערך: גבולות ישראל (כולל גולן ויו"ש). מזרח: גולן עד 35.95, משם הירדן/ים המלח ~35.6, ערבה עד אילת. דרום-מערב: גבול מצרים"""
    if not (29.45 <= la <= 33.45 and 34.2 <= lo <= 35.95): return False
    east = 35.95 if la >= 32.7 else 35.6 if la >= 31.0 else 35.45 - (31.0 - la) * 0.27
    west = 34.2 if la >= 31.28 else 34.25 + (31.28 - la) * 0.36
    return west <= lo <= east

def place_name(home):
    """שם המקום לפי הסניפים הקרובים ("נתיבות")"""
    D = kdata.D
    if not D or not home: return "המיקום ששמרתי"
    near = sorted(D.stores, key=lambda s: km(home, s["p"]))[:5]
    if km(home, near[0]["p"]) > 40: return "המיקום ששלחת"           # רחוק מכל הסניפים (ערבה, חו"ל) – לא "אזור אילת"
    city = lambda s: s["a"].split(",")[-1].strip() if "," in s["a"] else ""
    cities = [city(s) for s in near if city(s) and km(home, s["p"]) <= km(home, near[0]["p"]) + 6]   # רק סניפים באותו אזור
    close = [city(s) for s in near if city(s) and km(home, s["p"]) <= 4]
    if close: cities = close                       # יש סניף ממש ליד – העיר שלו (ירוחם, שלומי – לא "אזור דימונה/נהריה")
    c = max(set(cities), key=lambda x: (cities.count(x), -cities.index(x))) if cities else ""
    return f"אזור {c}" if c else "המיקום ששמרתי"

# 5 כפתורים בלבד (אורי 20/10: פשוט להורים). השאר בתוך "הודעות" ו"עזרה ועוד" – כל הכפתורים הישנים עדיין עובדים
SETTINGS = [("set:loc", "📍 מיקום"), ("set:club", "💳 מועדון לקוחות"), ("set:store", "⭐ הסופר הקבוע"),
            ("set:notif", "🔔 הודעות"), ("set:more", "❓ עזרה ועוד")]

def notif_buttons(pr):
    on = lambda off: "✅" if not off else "⬜"
    sd = pr.get("shop_day")
    return [("set:remind", f"{on(pr.get('remind_off'))} הודעה שבועית"), ("set:deals", f"{on(pr.get('deals_off'))} מבצעים"),
            ("set:day", f"📅 יום קניות: {HEB_DAYS[sd]}" if isinstance(sd, int) else "📅 יום הקניות שלי")]

def help_text():
    """קצר וברור – גם להורים (אורי 19/10: פחות מלל)"""
    L = ["❓ *איך משתמשים – בקצרה*", "",
         "1️⃣ שולחים מה קונים: רשימה (_חלב, לחם, 2 קוטג'_) או 📷 צילום של הקבלה",
         "2️⃣ אני בודק את כל הסופרים לידך ואומר איפה הכי זול",
         "3️⃣ אחרי הקנייה מצלמים את הקבלה – ככה אני לומד מה אתם קונים",
         "📅 בערב שלפני יום הקניות שלכם אבדוק את הסל, ואגיד אם יש איפה לחסוך", "",
         "💡 אפשר גם לכתוב: _כמה עולה קוטג'?_ · _תוסיף במבה_ · _כמה חסכתי_"]
    if GROQ_KEY or OPENAI_KEY: L.append("🎤 אפשר גם לשלוח הודעה קולית")
    L += ["⚙️ שינוי מיקום או מועדון: *הגדרות*"]
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
            return send_buttons(to, "✅ ההודעה השבועית *הופעלה* 👍", notif_buttons(pr))
        pr["remind_off"] = True; u.pop("remind_at", None); save_users()
        return send_buttons(to, "⬜ ההודעה השבועית *כובתה* 👍", notif_buttons(pr))
    if reply == "set:deals":
        if pr.get("deals_off"):
            pr.pop("deals_off", None); save_users()
            return send_buttons(to, "✅ התראות המבצעים *הופעלו* 👍", notif_buttons(pr))
        pr["deals_off"] = True; save_users()
        return send_buttons(to, "⬜ התראות המבצעים *כובו* 👍", notif_buttons(pr))
    if reply == "set:notif":
        return send_buttons(to, "🔔 *הודעות* – לוחצים כדי להדליק או לכבות:\n"
                                "• *הודעה שבועית* – לפני יום הקניות: איפה הסל הכי משתלם\n• *מבצעים* – מבצע על מוצר שאתה קונה", notif_buttons(pr))
    if reply == "set:day":
        return send_buttons(to, "📅 *באיזה יום בדרך כלל קונים?*\nבערב שלפני אבדוק את הסל ואגיד אם יש איפה לחסוך.",
                            [(f"day:{d}", HEB_DAYS[d]) for d in (6, 0, 1, 2, 3, 4, 5)] + [("day:auto", "🤖 שהבוט ילמד מהקבלות")])
    if reply.startswith("day:"):
        v = reply[4:]
        if v == "auto": pr.pop("shop_day", None); save_users(); return send_buttons(to, "👍 אלמד את יום הקניות מהקבלות", notif_buttons(pr))
        if not (v.isdigit() and 0 <= int(v) <= 6): return settings_action(to, u, "set:day")
        pr["shop_day"] = int(v); pr.pop("remind_off", None); save_users()
        return send_buttons(to, f"📅 רשמתי: יום *{HEB_DAYS[int(v)]}*. בערב שלפני אבדוק את הסל 👍", notif_buttons(pr))
    if reply == "set:more":
        return send_buttons(to, help_text(), [("set:faq", "💬 שאלות ותשובות"), ("set:sav", "💰 כמה חסכתי"), ("set:reset", "🔄 להתחיל מחדש")])
    if reply == "set:sav":
        return send_text(to, savings_text(u) or "עוד לא בדקת קניות החודש 🙂")
    if reply == "set:faq":
        return send_buttons(to, "💬 *שאלות ותשובות* – על מה תרצה לשאול?", [(f"set:faq{j}", q[:40]) for j, (q, _) in enumerate(faq_answer(None))])
    if reply.startswith("set:faq") and not (reply[7:].isdigit() and int(reply[7:]) < len(faq_answer(None))):
        reply = "set:faq"; return settings_action(to, u, reply)
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

def fill_prefs(pr):
    """ברירות מחדל להעדפות (ההרשמה הקצרה שואלת רק מיקום ומועדון) – כדי שאף פעם לא יחסר "mode" וכו'"""
    if pr.get("home"):
        pr.setdefault("mode", "car"); pr.setdefault("maxMin", 999); pr.setdefault("brand", "cheap"); pr.setdefault("splitMin", SPLIT_MIN)
        if pr["mode"] not in ("car", "walk", "bus"): pr["mode"] = "car"            # ערכים פגומים/ישנים בזיכרון → ברירת מחדל
        if not isinstance(pr["maxMin"], (int, float)): pr["maxMin"] = 999
        if not isinstance(pr["splitMin"], (int, float)): pr["splitMin"] = SPLIT_MIN
    return pr

def repair_user(u):
    """זיכרון משתמש פגום (גרסה ישנה, מיזוג, עריכה ידנית) → מתקנים במקום לקרוס בכל הודעה (נמצא בניסיון התקלה 13/10)"""
    if not isinstance(u.get("prefs"), dict): u["prefs"] = {}
    pr = u["prefs"]; h = pr.get("home")
    if h is not None and not (isinstance(h, (list, tuple)) and len(h) == 2 and all(isinstance(x, (int, float)) for x in h)):
        pr.pop("home", None)
    if not isinstance(u.get("step"), str): u["step"] = "done" if pr.get("home") else "new"
    def good_item(x): return isinstance(x, dict) and isinstance(x.get("name"), str) and x["name"]
    u["hist"] = [b for b in (u.get("hist") if isinstance(u.get("hist"), list) else [])
                 if isinstance(b, dict) and isinstance(b.get("items"), list)]
    for b in u["hist"]:
        b["items"] = [x for x in b["items"] if good_item(x)]
        if not isinstance(b.get("d"), (int, float)): b["d"] = 0                       # תאריך פגום → לא נספר בחודש
        if not isinstance(b.get("saved"), (int, float)) or not 0 <= b["saved"] < 10000: b["saved"] = 0
    if "items" in u and not isinstance(u["items"], list): u.pop("items")
    if isinstance(u.get("items"), list): u["items"] = [x for x in u["items"] if good_item(x)]
    for k in ("stores", "my_match"):
        if k in u and not isinstance(u[k], dict): u.pop(k)
    if isinstance(u.get("stores"), dict): u["stores"] = {k: v for k, v in u["stores"].items() if isinstance(v, (int, float))}
    for x in (u.get("items") or []) + [x for b in u["hist"] for x in b["items"]]:
        q = x.get("qty")
        if not isinstance(q, (int, float)) or q != q or q <= 0: x["qty"] = 1          # None / "שתיים" / שלילי / 0 → 1
        elif q > 200: x["qty"] = 200                                                   # מיליון חלב → 200 לכל היותר
    return u

def handle(msg):
    to = msg["from"]; u = repair_user(user(to)); t = msg.get("type"); pr = fill_prefs(u["prefs"])
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
        adm = set(filter(None, os.environ.get("ADMIN_CHAT", "").split(","))) | set(meta.get("admins", []))
        if not adm:                          # הראשון ששולח /stats נהיה המנהל
            meta["admins"] = [to]; save_users(); adm = {to}
            send_text(to, "רשמתי אותך כמנהל הבוט 🔐 רק אתה תוכל לראות את הנתונים.")
        if to not in adm: return send_text(to, "לא הבנתי 🙂")
        return send_text(to, stats_text())
    if text in ("אשר נתונים", "/acceptdata") and to in admins():
        g = dict(kdata.GATE)
        if not g: return send_text(to, "אין כרגע עדכון נתונים חסום 👍")
        kdata.GATE_ACCEPT.add(g["version"]); kdata.GATE.clear()
        threading.Thread(target=kdata.load, daemon=True).start()
        return send_text(to, f"✅ אישרתי את העדכון ({g['reason']}). טוען אותו עכשיו – כדקה.")
    if text in ("/report", "דוח שבועי", "דוח") and to in admins():
        track_stores(); return send_text(to, weekly_report())
    if text in ("/selftest", "בדיקה עצמית") and to in admins():
        typing(to); probs, summ = selftest()
        return send_text(to, ("✅ הבדיקה עברה – אותה רשימה, אותה תוצאה, כמויות תקינות" if not probs else
                              "🧪 *נמצאה בעיה*\n\n" + "\n".join("⚠️ " + p for p in probs[:12])) + ("\n\n" + summ if summ else ""))
    if text in ("אבחון מחדש", "אבחון", "איפוס", "איפוס מלא"):
        full = text == "איפוס מלא" or (text == "איפוס" and RESET_CLEARS_HABITS)
        u["prefs"] = {}; u.pop("remind_at", None); u.pop("items", None); u.pop("last", None)
        if full: forget_habits(u)
        save_users()
        return ask(to, "loc", u, intro=("🔄 " if not full else "🔄🧹 ") + welcome(u) + "\n\n")
    if re.fullmatch(r"(כמה חסכתי|חיסכון|סיכום|סיכום חיסכון|כמה חסכתי\?|/savings)", text):
        return send_text(to, savings_text(u) or "עוד לא בדקת קניות החודש 🙂 שלחו רשימה או קבלה ואתחיל לספור.")
    if text == "מחק אותי":
        forget_user(to); save_users()
        return send_text(to, "מחקתי את כל המידע שלך 🗑️ כדי להתחיל מחדש כתבו היי.")
    if text == M_CAM:
        reply, text = "cam", ""
    elif text == M_SET:
        text = "הגדרות"
    if text == M_NEW or (GREET.match(text) and len(text) <= 25) or (text == "/start" and all(k in pr for k in PROFILE_KEYS)):
        if all(k in pr for k in PROFILE_KEYS):         # משתמש חוזר – ישר "מה קונים"; המיקום רק בשורה קטנה + כפתור (אורי 20/10)
            return ask(to, "basket", u, intro=f"היי{' ' + u['name'] if u.get('name') else ''}! 👋 _📍 {place_name(pr['home'])}_\n\n", loc_btn=True)
        return ask(to, "loc", u, intro=welcome(u) + "\n\n")
    if text in ("הגדרות", "/settings"):
        return settings_menu(to, u)
    if reply == "loc_ok":
        return ask(to, "basket", u, intro="מעולה, סיימנו 🎉\n\n")
    if reply in ("loc_new", "set:loc"):
        u["step"] = "loccheck" if reply == "loc_new" else "setloc"; save_users()
        return send_location_request(to, "📍 לוחצים על *שליחת המיקום שלי* למטה (או 📎 ← מיקום כדי לבחור מקום אחר במפה)")
    if reply and (reply.startswith(("set:", "st:", "day:")) or reply in ("reset_yes", "reset_no")):
        return settings_action(to, u, reply)
    if text in ("התחל", "מחדש", "/start") or u["step"] == "new":
        if u["step"] == "new" and pr.get("home"):
            u["step"] = "keep"; save_users()
            return send_buttons(to, welcome(u) + "\n\nיש לי את ההעדפות שלך מהפעם הקודמת.", [("keep", "להמשיך איתן"), ("reset", "להתחיל מחדש")])
        return ask(to, "loc", u, intro=welcome(u) + "\n\n")
    if reply == "keep": return ask(to, "basket", u)
    if reply == "reset":
        u["prefs"] = {}
        if RESET_CLEARS_HABITS: forget_habits(u); save_users()
        return ask(to, "loc", u)

    if t == "location":
        la, lo = msg["location"]["latitude"], msg["location"]["longitude"]
        if not in_israel(la, lo):                                    # מחוץ לישראל (או 0,0 של GPS שלא נתפס) – לא שומרים
            return send_location_request(to, "🤔 המיקום שקיבלתי נראה מחוץ לישראל. שלחו שוב את המיקום (📎 ← מיקום) מהמקום שממנו יוצאים לקניות 📍")
        pr["home"] = [la, lo]; fill_prefs(pr)
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
    if reply and reply.startswith("cl:"):                # תשובה ל"התכוונת ל…?" – נזכר אצל המשתמש, שואלים פעם אחת בלבד
        c = u.pop("clarify", None); its = u.get("items") or []
        if not its: return send_text(to, "אין כרגע סל – כתבו *היי* כדי להתחיל 🙂")
        if c and c.get("k", 99) < len(its):
            x = its[c["k"]]; v = reply[3:]; D = kdata.D
            code = c["opts"][int(v)] if v.isdigit() and int(v) < len(c["opts"]) else x["code"]
            i = D.code2i.get(code)
            if i is not None:
                its[c["k"]] = dict(item_of(D, i, x["qty"], x["src"]), c=[z for z in [x["code"]] + x.get("c", []) if z != code][:4], sure=True)
                u.setdefault("my_match", {})[mkey(x["src"])] = code
        u["step"] = "done"; typing(to)
        return after_match(to, u)
    if reply == "fix" or (reply and reply.startswith(("fix:", "alt:", "del:"))):
        its = u.get("items") or []
        if not its: return send_text(to, "אין כרגע סל לתקן – כתבו *היי* כדי להתחיל 🙂")
        if reply == "fix":
            btns = [(f"fix:{k}", f"{k + 1}. {x['src'][:28]}") for k, x in enumerate(its[:20])] + [("fixdone", "✅ הכל נכון")]
            return send_buttons(to, "על איזה מוצר לתקן?", btns)
        try: k = int(reply.split(":")[1])
        except (ValueError, IndexError): return send_buttons(to, "על איזה מוצר לתקן?", [(f"fix:{n}", f"{n + 1}. {x['src'][:28]}") for n, x in enumerate(its[:20])])
        if not 0 <= k < len(its): return
        x = its[k]
        if reply.startswith("fix:"):
            d = kdata.D
            opts = [(f"alt:{k}:{j}", d.items[d.code2i[c]][1][:40]) for j, c in enumerate(x.get("c", [])) if c in d.code2i]
            if not opts:  # אין חלופות שמורות – מחפשים מחדש
                x["c"] = [d.items[i][0] for i in d.search(x["src"], 6) if d.items[i][0] != x.get("code")][:4]; save_users()
                opts = [(f"alt:{k}:{j}", d.items[d.code2i[c]][1][:40]) for j, c in enumerate(x["c"])]
            return send_buttons(to, f"*{x['src']}* – התכוונת ל:", opts + [(f"del:{k}", "🗑️ להוריד מהסל"), ("fix", "↩️ חזרה")])
        if reply.startswith("alt:"):
            try: j = int(reply.split(":")[2])
            except (ValueError, IndexError): return
            d = kdata.D
            c = x.get("c", [])[j] if j < len(x.get("c", [])) else None
            if c not in d.code2i: return
            old_code = x["code"]
            its[k] = dict(item_of(d, d.code2i[c], x["qty"], x["src"]), c=[old_code] + [z for z in x.get("c", []) if z != c][:3])
            u.setdefault("my_match", {})[mkey(x["src"])] = c          # התיקון של המשתמש נזכר: בפעם הבאה אותו שם → המוצר שבחר
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
    if reply == "more":
        last = u.get("last") or {}
        if not last.get("store"): return send_text(to, "אין לי עדיין חישוב להציג – כתבו *היי* כדי להתחיל קנייה 🙂")
        return send_buttons(to, "📋 *מה להראות?*", details_buttons(last))
    if reply == "plan_ok":
        last = u.get("last") or {}
        if not last.get("store"): return send_text(to, "אין כרגע תוכנית קנייה – כתבו *היי* כדי להתחיל 🙂")
        # סעיף 3 (אורי 20/10): זוכרים לאן הלכו ומול מה השווינו – בקבלה הבאה מראים כמה נחסך באמת
        if last.get("store_key") and last.get("ref_key") and not last.get("stay"):
            u["went"] = {"store": last["store_key"], "ref": last["ref_key"], "t": int(time.time()), "gap": last.get("gap")}
            save_users()
        if is_tg(to) and u.get("items"):              # רשימת קניות חיה (אורי 20/10): מסמנים ✅ תוך כדי הליכה בסופר
            start_shop(u, last); save_users()
            return send_shop(to, u)
        if last.get("details"): send_text(to, last["details"])
        nav = f"\n🧭 ניווט: {last['nav']}" if last.get("nav") else ""
        return send_text(to, f"סגור 👍 הקנייה ב*{last['store']}*.{nav}\n\n📷 _אחרי הקנייה – צלמו את הקבלה, ואבדוק כמה חסכתם באמת._")
    if reply and reply.startswith("chk:"):
        sh = u.get("shop")
        try: k = int(reply[4:])
        except ValueError: return
        if not sh or not 0 <= k < len(sh["rows"]): return send_text(to, "הרשימה הזו כבר לא פעילה 🙂")
        d = sh["done"]
        if k in d: d.remove(k)
        else: d.append(k)
        save_users()
        if is_tg(to) and msg.get("mid"):
            return tg_call("editMessageReplyMarkup", {"chat_id": to[3:], "message_id": msg["mid"],
                                                       "reply_markup": {"inline_keyboard": shop_keyboard(u)}})
        return send_shop(to, u)
    if reply == "shopdone":
        sh = u.pop("shop", None); save_users()
        left = len(sh["rows"]) - len(sh["done"]) if sh else 0
        note = f"\n_(נשארו {left} מוצרים לא מסומנים)_" if left else ""
        return send_buttons(to, f"כל הכבוד 🛒{note}\n\n📷 צלמו את הקבלה – ואבדוק כמה חסכתם באמת.", [("cam", "📷 צילום קבלה")])
    if reply in ("miss", "missall"):
        if not u.get("last"): return send_text(to, "אין לי עדיין חישוב להציג – כתבו *היי* כדי להתחיל קנייה 🙂")
        return send_text(to, missing_text(u["last"], every=reply == "missall"))
    if reply == "oth" and (u.get("last") or {}).get("miss_all"):
        return send_buttons(to, u["last"]["others"], [("missall", "❓ מה חסר בכל סניף")])
    if reply in ("det", "oth", "spl"):
        txt = (u.get("last") or {}).get({"det": "details", "oth": "others", "spl": "split"}[reply])
        return send_text(to, txt or "אין לי עדיין חישוב להציג – כתבו *היי* כדי להתחיל קנייה 🙂")
    if reply == "deals_off":
        pr["deals_off"] = True; save_users()
        return send_text(to, "בסדר, לא אשלח יותר התראות מבצעים 👍 (להפעלה מחדש: *הגדרות* ← 🔥 התראות מבצעים)")
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
        elif p == "/cam":
            body = CAM_PAGE.encode(); self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body); return
        elif p == "/privacy":
            body = PRIVACY.encode(); self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers(); self.wfile.write(body); return
        else:
            d = kdata.D
            info = f"\nנתונים: {len(d.stores)} סניפים, {len(d.items)} מוצרים, עודכן {d.built or d.version} ({d.source})" if d else ""
            body = ("קונה אישי פועל ✅" + info).encode(); self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        if urlparse(self.path).path == "/cam/upload":
            if n > 12_000_000: self.send_response(413); self.end_headers(); return
            code, txt = cam_upload(self.rfile.read(n))
            self.send_response(code); self.send_header("Content-Type", "text/plain; charset=utf-8"); self.end_headers(); self.wfile.write(txt.encode()); return
        raw = self.rfile.read(n)
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

# ---------- מסך צילום קבלה (Telegram Mini App) – אורי 20/10: לחיצה על "צילום קבלה" פותחת מצלמה, או גלריה ----------
CAM_PAGE = """<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<title>צילום קבלה</title>
<style>
 body{margin:0;font-family:system-ui,-apple-system,Arial,sans-serif;background:var(--tg-theme-bg-color,#fff);color:var(--tg-theme-text-color,#111);
      display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;gap:18px;padding:24px;box-sizing:border-box;text-align:center}
 h1{font-size:22px;margin:0 0 4px} p{margin:0;opacity:.75;font-size:15px}
 label{display:block;width:100%;max-width:340px;padding:22px 0;border-radius:18px;font-size:22px;font-weight:700;cursor:pointer;
       background:var(--tg-theme-button-color,#2F6BF0);color:var(--tg-theme-button-text-color,#fff)}
 label.sec{background:var(--tg-theme-secondary-bg-color,#eef2fb);color:var(--tg-theme-text-color,#111)}
 input{display:none} #st{font-size:18px;min-height:26px}
</style></head><body>
<h1>📷 צילום קבלה</h1><p>שימו את הקבלה על משטח, באור טוב. קבלה ארוכה – אפשר כמה תמונות.</p>
<label>📷 לצלם עכשיו<input id="cam" type="file" accept="image/*" capture="environment"></label>
<label class="sec">🖼️ מהגלריה<input id="gal" type="file" accept="image/*" multiple></label>
<div id="st"></div>
<script>
const tg=window.Telegram&&Telegram.WebApp; if(tg){tg.ready();tg.expand();}
const st=document.getElementById('st');
function shrink(file){return new Promise((ok,bad)=>{const im=new Image();im.onload=()=>{const m=2200,k=Math.min(1,m/Math.max(im.width,im.height));
 const c=document.createElement('canvas');c.width=Math.round(im.width*k);c.height=Math.round(im.height*k);c.getContext('2d').drawImage(im,0,0,c.width,c.height);
 ok(c.toDataURL('image/jpeg',0.85).split(',')[1]);};im.onerror=bad;im.src=URL.createObjectURL(file);});}
async function send(files){if(!files.length)return;st.textContent='⏳ שולח…';
 try{for(let i=0;i<files.length;i++){const b=await shrink(files[i]);
   const r=await fetch('/cam/upload',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({init:tg?tg.initData:'',img:b})});
   if(!r.ok)throw new Error(await r.text());}
  st.textContent='✅ התקבל! התוצאה תגיע בצ׳אט';setTimeout(()=>tg&&tg.close(),1200);}
 catch(e){st.textContent='🙁 לא הצלחתי לשלוח – אפשר לשלוח את התמונה ישר בצ׳אט (📎)';}}
for(const id of ['cam','gal'])document.getElementById(id).addEventListener('change',e=>send(e.target.files));
</script></body></html>"""

def tg_webapp_user(init):
    """בדיקת החתימה של טלגרם על המסך (initData) – רק משתמש אמיתי של הבוט יכול לשלוח קבלה. מחזיר מזהה משתמש או None."""
    import hmac, hashlib
    from urllib.parse import parse_qsl
    if not init or not TG_TOKEN: return None
    d = dict(parse_qsl(init, keep_blank_values=True)); h = d.pop("hash", "")
    dcs = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
    secret = hmac.new(b"WebAppData", TG_TOKEN.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest(), h): return None
    try:
        if time.time() - int(d.get("auth_date", 0)) > 86400: return None
        return json.loads(d.get("user") or "{}").get("id")
    except Exception: return None

def cam_upload(raw):
    """תמונה ממסך הצילום → כמו תמונה שנשלחה בצ'אט (אותה נעילה, אותו טיפול). מחזיר (קוד, טקסט)."""
    try: data = json.loads(raw)
    except Exception: return 400, "bad json"
    uid = tg_webapp_user(data.get("init", ""))
    if not uid: return 403, "not authorized"
    try: img = base64.b64decode(data.get("img") or "")
    except Exception: return 400, "bad image"
    if not img or len(img) > 8_000_000: return 400, "bad size"
    key = f"{uid}-{time.time_ns()}"; RAW_IMG[key] = (img, sniff_mime(img))
    m = {"from": f"tg:{uid}", "id": f"cam-{key}", "type": "image", "image": {"id": "raw:" + key}}
    threading.Thread(target=safe_handle, args=(m,), daemon=True).start()
    return 200, "ok"

_crash_sent = {}
def crash_alert(m, ex, tb):
    """תקלה אצל משתמש → הודעה מיידית למנהל: מה נשלח, איפה נשבר. אותה תקלה – פעם בחצי שעה לכל היותר."""
    wk_count("errors")
    fr = [f for f in tb if f.filename.endswith(("app.py", "kdata.py"))][-3:] or list(tb)[-2:]
    where = " ← ".join(f"{os.path.basename(f.filename)}:{f.lineno} {f.name}" for f in reversed(fr))
    sig = f"{type(ex).__name__}:{where}"
    if time.time() - _crash_sent.get(sig, 0) < 1800: return
    _crash_sent[sig] = time.time()
    t = m.get("type"); body = {"text": lambda: m["text"]["body"], "interactive": lambda: "כפתור " + m["interactive"]["button_reply"]["id"],
                               "location": lambda: "מיקום", "image": lambda: "תמונה (קבלה)", "voice": lambda: "הודעה קולית"}.get(t, lambda: t)()
    u = USERS.get(m["from"], {})
    msg = (f"🚨 *תקלה אצל משתמש*\n\n👤 ...{str(m['from'])[-4:]} (שלב: {u.get('step', '?')})\n💬 שלח: {str(body)[:200]}\n"
           f"❌ {type(ex).__name__}: {str(ex)[:200]}\n📍 {where}\n\n_המשתמש קיבל \"אופס, משהו השתבש\". אפשר להעביר לי את ההודעה הזו לתיקון._")
    for a in [a for a in admins() if is_tg(a)]: send_text(a, msg)

_user_locks = {}; _user_locks_guard = threading.Lock()

def safe_handle(m):
    """הודעות של אותו משתמש – אחת אחרי השנייה (נעילה למשתמש). בלי זה, "איפוס" ו"חלב" שנשלחו כמעט ביחד
    שינו את אותו סל במקביל ונפלו ("משהו השתבש", 13/10). משתמשים שונים ממשיכים לרוץ במקביל."""
    with _user_locks_guard: lk = _user_locks.setdefault(m.get("from"), threading.Lock())
    with lk: return _safe_handle(m)

def _safe_handle(m):
    done = threading.Event()
    def keep_typing():   # בטלגרם "מקליד..." נעלם אחרי 5 שניות – מחדשים כל 4 שניות כל עוד עובדים
        if done.wait(1.0): return
        while not done.is_set():
            typing(m["from"])
            if done.wait(4.0): return
    if is_tg(m["from"]): threading.Thread(target=keep_typing, daemon=True).start()
    try:
        handle(m); touch(m["from"]); save_users()
    except Exception as ex:
        import traceback; traceback.print_exc()
        try: send_text(m["from"], "אופס, משהו השתבש אצלי 🙈 נסה שוב בעוד רגע.")
        except Exception: pass
        try: crash_alert(m, ex, traceback.extract_tb(ex.__traceback__))
        except Exception as e2: print("crash alert failed", e2, flush=True)
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
