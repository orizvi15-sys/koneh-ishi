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
MAX_POOL = 25
STAY_MIN = 35           # פחות מזה – לא שווה לעזוב את הסופר הקבוע
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
def claude_json(prompt, image=None, mime="image/jpeg", keep_dict=False):
    if not CLAUDE_KEY:
        return None
    content = [{"type": "text", "text": prompt}]
    if image:
        content.insert(0, {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.b64encode(image).decode()}})
    body = {"model": CLAUDE_MODEL, "max_tokens": 8000, "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=90))
        txt = "".join(b.get("text", "") for b in res.get("content", []) if b.get("type") == "text")
        if res.get("stop_reason") == "max_tokens": print("Claude: reply cut at max_tokens", flush=True)
    except urllib.error.HTTPError as e:
        print("Claude error", e.code, e.read()[:500], flush=True); return None
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
                  '"qty" = number of units (from ×2 / x2 / 2 יח), or kilograms for weighed produce if shown; default 1. '
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

def full_name(st):
    """"רמי לוי – סניף עזריאלי חיפה" """
    ch, n = st["chain"], st["n"]
    br = n[len(ch):].strip(" -–") if n.startswith(ch) else n
    return f"{ch} – סניף {br}" if br else ch

def gp(P, it):
    """המחיר הזול ביותר בסניף מבין המוצרים השקולים"""
    v = [P[g] for g in it["g"] if g in P]
    return min(v) if v else None

def compute(u):
    D = kdata.D
    pr = u["prefs"]; home = pr["home"]; car = pr["mode"] == "car"; bus = pr["mode"] == "bus"
    split_min = -1 if pr.get("splitMin", SPLIT_MIN) < 0 else SPLIT_MIN
    clubs = set(pr.get("clubs") or [])
    cost = lambda d: d * FUEL / KM_PER_L if car else 0
    mins = lambda d: d / CAR_KMH * 60 + PARK_MIN if car else (d / BUS_KMH * 60 + BUS_WAIT if bus else d / WALK_KMH * 60)
    items, gone = [], []
    for it in u["items"]:
        i = resolve(D, it)
        if i is None: gone.append(it["name"])
        else:
            g = [i] if pr.get("brand") == "mine" else D.group(i, it.get("src") or it["name"])
            items.append(dict(it, i=i, g=g))
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
        tot = 0; miss = 0
        for it in items:
            p = gp(r["P"], it)
            if p is None: miss += 1; p = med[it["i"]]
            tot += p * it["qty"]
        r.update(sum=tot, fuel=cost(r["d"]), tot=tot + cost(r["d"]), miss=miss)
    nearest = rows[0]
    reg = next((r for r in rows if r["s"]["key"] == uk), None)
    if reg and reg["miss"] > 0.3 * len(items): reg = None     # כנראה זיהוי סניף שגוי (או חנות שלא מוכרת את רוב הסל)
    pool = [r for r in rows if r["min"] <= pr["maxMin"] or r is reg] or [nearest]
    # סניף שחסרים בו הרבה מהמוצרים לא באמת עונה על הסל
    inrange = sorted(pool, key=lambda r: r["tot"])
    full = [r for r in pool if r["miss"] <= max(1, 0.2 * len(items))]
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
                    pa, pb = gp(A["P"], it), gp(B["P"], it)
                    if pa is None and pb is None: ok = False; break
                    if pb is None or (pa is not None and pa <= pb): tot += pa * it["qty"]; inA.append(it["name"])
                    else: tot += pb * it["qty"]; inB.append(it["name"])
                if not ok or not inA or not inB: continue
                d = km(home, A["s"]["p"]) + km(A["s"]["p"], B["s"]["p"]) + km(B["s"]["p"], home)
                t = tot + cost(d)
                if not split or t < split["tot"]: split = {"A": A, "B": B, "tot": t, "inA": inA, "inB": inB}
    stay = reg is not None and reg["tot"] - best["tot"] < STAY_MIN
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
    far_km = km(home, nearest["s"]["p"])
    if far_km > 25:
        L.append(f"⚠️ _עדיין אין לי סניפים באזור שלך (הקרוב ביותר: {far_km:.0f} ק\"מ)._\n")
    inc = " (כולל עלות הנסיעה)" if car else ""
    if stay:
        L.append(f"👍 הסופר הקבוע שלך משתלם: *{s['n']}*")
        L.append(f"💰 מחיר הסל: *₪{best['tot']:.0f}*" + (" (במחירי מועדון)" if best.get("club") else ""))
        L.append(f"✅ זה הכי זול באזור 🏆" if gap < 1 else f"✅ במקום אחר היית חוסך רק ₪{gap:.0f}{inc} – לא שווה את הטרחה")
    else:
        L.append(f"🏆 הסופר המנצח: *{s['n']}*")
        L.append(f"💰 מחיר הסל: *₪{best['tot']:.0f}*" + (" (במחירי מועדון)" if best.get("club") else ""))
        if reg:
            L.append(f"✅ חוסך *₪{gap:.0f}* לעומת {reg['s']['n']}{inc}")
        elif avg and avg - best["tot"] >= 1:
            L.append(f"✅ חוסך *₪{avg - best['tot']:.0f}*{inc}")
    L.append(f"📍 {best['d'] / 2:.1f} ק\"מ ממך · {round(best['min'])} דק' {how}")
    split_txt = None
    if split and best["tot"] - split["tot"] > split_min:
        A, B = split["A"]["s"], split["B"]["s"]
        split_txt = (f"🔀 *קנייה בשני סופרים – ₪{split['tot']:.0f}*\n\n*{A['n']}* ({A['a']}):\n" + "\n".join("• " + x for x in split["inA"]) +
                     f"\n\n*{B['n']}* ({B['a']}):\n" + "\n".join("• " + x for x in split["inB"]))
    # פירוט מחירי הסל בסופר המנצח
    det = []
    for it in sorted(items, key=lambda it: -(gp(best["P"], it) or med[it["i"]]) * it["qty"]):
        p = gp(best["P"], it)
        q = f" ×{it['qty']:g}" if it["qty"] != 1 else ""
        det.append(f"• {it['name']}{q} – " + (f"₪{p * it['qty']:.2f}" if p is not None else f"~₪{med[it['i']] * it['qty']:.2f} _(חסר בסניף)_"))
    if gone: det.append("\n_לא נמצאו במאגר: " + ", ".join(gone) + "_")
    if car and best["fuel"] >= 1: det.append(f"\n⛽ דלק הלוך-חזור: ₪{best['fuel']:.0f}")
    fresh = f"\n\n_מחירים ומבצעים מעודכנים ל-{D.built[8:10]}/{D.built[5:7]} {D.built[11:16]}_" if len(D.built) >= 16 else ""
    saved = 0 if stay else (gap if reg else (avg - best["tot"] if avg else 0))
    return {"main": "\n".join(L), "store": s["n"], "saved": max(0, round(saved)),
            "nav": f"https://waze.com/ul?ll={s['p'][0]},{s['p'][1]}&navigate=yes",
            "details": f"📋 *הסל שלך ב{s['n']}:*\n" + "\n".join(det) + fresh,
            "others": (f"💡 אם מתחלקים בין *{split['A']['s']['n']}* ל*{split['B']['s']['n']}* חוסכים עוד *₪{best['tot'] - split['tot']:.0f}*\n\n" if split_txt else "")
                      + "🏪 *כל הסופרים בטווח:*\n\n" + "\n\n".join(
                f"{'🏆' if r['s']['key'] == s['key'] else '▫️'} *{full_name(r['s'])}*" + (" ⭐ _הקבוע שלך_" if r is reg else "") + "\n"
                f"      📍 {r['s']['a']}\n      💰 ₪{r['tot']:.0f} · {round(r['min'])} דק'" + (f" _· חסרים {r['miss']}_" if r["miss"] else "")
                for r in (inrange[:10] + ([reg] if reg and reg not in inrange[:10] else []))) + fresh,
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
    res = compute(u)
    if isinstance(res, list):
        for m in res: send_text(to, m)
    else:
        u["last"] = {k: res[k] for k in ("details", "others", "split", "store")}
        n, miss = len(u["items"]), len(u.get("missed") or [])
        main = res["main"] + f"\n🧾 זיהיתי {n} מוצרים" + (f" ({miss} לא נמצאו)" if miss else "")
        if u.get("note"): main += "\n\n" + u.pop("note")
        send_buttons(to, main, [("seen", "🧾 מה זיהיתי"), ("oth", "🏪 סופרים אחרים")])
    entry = {"d": int(time.time()), "items": [{"code": x.get("code", ""), "name": x["name"], "src": x.get("src", ""), "qty": x["qty"]} for x in u["items"]]}
    if redo and u["hist"]: u["hist"][-1] = entry          # תיקון – מחליפים את הקנייה האחרונה במקום להוסיף
    else: u["hist"] = (u["hist"] + [entry])[-12:]
    u["step"] = "done"; save_users()
    if redo: return
    if len(u["hist"]) == 1:
        send_text(to, "_שמרתי את הקנייה כדי ללמוד את ההרגלים שלך. מתי שרוצים – פשוט כותבים *היי* 🙂_")
    pr = u["prefs"]; n = len(u["hist"])
    if n >= 1 and "clubs" not in pr:                       # משתמשים ותיקים שלא נשאלו בפתיחה
        pr["clubs"] = []; return ask_clubs(to, u, first=True)


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
    L += ["", "🛡️ " + ("הכל תקין" if not iss else f"{len(iss)} התראות:")] + iss
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
        if seen.get(ch) and n < 0.5 * seen[ch]: out.append(f"⚠️ {he} ירדה מ-{seen[ch]} ל-{n} סניפים")
        elif n: seen[ch] = max(n, int(seen.get(ch, 0) * 0.9))
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

def _reminder_loop():
    while True:
        time.sleep(60)
        try: watchdog()
        except Exception as ex: print("watchdog error", ex, flush=True)
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
    if u.get("club_ctx") == "onb": return ask(to, "kosher", u)
    u["step"] = "done"; save_users()
    return send_text(to, "תודה, רשמתי 👍")

def understood_text(u, missed=()):
    L = ["🧾 *הבנתי כך:*"]
    for x in u["items"][:25]:
        q = f" ×{x['qty']:g}" if x["qty"] != 1 else ""
        L.append(f"• {x['src']}{q} ← {x['name']}" if x["src"] != x["name"] else f"• {x['name']}{q}")
    if len(u["items"]) > 25: L.append(f"…ועוד {len(u['items']) - 25}")
    if missed: L.append("\n❓ לא מצאתי: " + ", ".join(missed[:8]))
    return "\n".join(L)

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
items: product names in Hebrew as the user wrote them, WITHOUT quantity words; qty = units, but for meat/chicken/fish/produce
sold by weight qty is kilograms ("קילו"=1, "חצי קילו"=0.5, "300 גרם"=0.3, "2 קילו"=2). Empty list for chat.
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


def act_on(to, u, r):
    intent, its = r["intent"], r["items"]
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
    x = found[0]; D = kdata.D; i = resolve(D, x)
    pr = u["prefs"]; home = pr["home"]; clubs = set(pr.get("clubs") or [])
    g = D.group(i, x.get("src") or x["name"])
    rows = []
    for s in sorted(D.stores, key=lambda s: km(home, s["p"]))[:MAX_POOL * 2]:
        d1 = km(home, s["p"]); mn = d1 / CAR_KMH * 60 + PARK_MIN if pr["mode"] == "car" else (d1 / BUS_KMH * 60 + BUS_WAIT if pr["mode"] == "bus" else d1 / WALK_KMH * 60)
        if rows and mn > pr["maxMin"]: break
        p = gp(D.store_prices(s, clubs), dict(x, i=i, g=g))
        if p is not None: rows.append((p, d1, s))
        if len(rows) >= MAX_POOL: break
    if not rows: return send_text(to, f"לא מצאתי את *{D.items[i][1]}* בסופרים באזור שלך 🙁")
    rows.sort(key=lambda r: r[0]); kg = 'לק"ג' in D.items[i][1]
    unit = " לק\"ג" if kg else ""
    L = [f"💰 *{D.items[i][1]}* – באזור שלך:", ""]
    for k, (p, d1, s) in enumerate(rows[:5]):
        L.append(f"{'🏆' if k == 0 else '▫️'} {s['n']} – *₪{p:.2f}*{unit} · {d1:.1f} ק\"מ")
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

def handle(msg):
    to = msg["from"]; u = user(to); t = msg.get("type"); pr = u["prefs"]
    reply = None
    if t == "interactive":
        it = msg["interactive"]; reply = (it.get("button_reply") or it.get("list_reply") or {}).get("id")
    text = msg.get("text", {}).get("body", "").strip() if t == "text" else ""
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
    if text in ("אבחון מחדש", "אבחון", "איפוס", "איפוס מלא"):
        full = text == "איפוס מלא"
        u["prefs"] = {}; u.pop("remind_at", None); u.pop("items", None); u.pop("last", None)
        if full:
            u["hist"] = []
            for k in ("receipts", "last_receipt", "stores", "rbuf_t", "note", "cam_tip", "missed", "pending"): u.pop(k, None)
        save_users()
        return ask(to, "loc", u, intro=("🔄 " if not full else "🔄🧹 ") + welcome(u) + "\n\n")
    if text == "מחק אותי":
        USERS.pop(to, None); save_users()
        return send_text(to, "מחקתי את כל המידע שלך 🗑️ כדי להתחיל מחדש כתבו היי.")
    if text == M_CAM:
        reply, text = "cam", ""
    elif text == M_SET:
        text = "הגדרות"
    if text == M_NEW or (GREET.match(text) and len(text) <= 25):
        if all(k in pr for k in PROFILE_KEYS):
            send_text(to, f"היי{' ' + u['name'] if u.get('name') else ''}! 👋", menu=True)
            return ask(to, "basket", u)
        return ask(to, "loc", u, intro=welcome(u) + "\n\n")
    if text in ("הגדרות", "התחל", "מחדש", "/start", "/settings") or u["step"] == "new":
        if u["step"] == "new" and pr.get("home"):
            u["step"] = "keep"; save_users()
            return send_buttons(to, welcome(u) + "\n\nיש לי את ההעדפות שלך מהפעם הקודמת.", [("keep", "להמשיך איתן"), ("reset", "להתחיל מחדש")])
        return ask(to, "loc", u, intro=welcome(u) + "\n\n")
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
    if reply in ("car", "walk", "bus"): pr["mode"] = reply; return ask(to, "range", u)
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
        return send_buttons(to, understood_text(u, u.get("missed") or []), [("fix", "✏️ משהו לא נכון")])
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
    if reply in ("addf", "noadd"):   # לא תלוי ב-step – כדי שלחיצה לא "תיתקע" (למשל אם השרת עלה מחדש באמצע)
        pend = u.pop("pending", [])
        if reply == "addf": u.setdefault("items", []).extend({k: x.get(k, "") for k in ("src", "code", "name", "qty")} for x in pend)
        if not u.get("items"): return send_text(to, "אין כרגע סל – כתבו *היי* כדי להתחיל 🙂")
        typing(to)
        return finish(to, u)

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
                                                   {"command": "settings", "description": "לשנות מיקום והעדפות"}]})
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
