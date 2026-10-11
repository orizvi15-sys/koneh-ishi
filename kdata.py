"""
שכבת הנתונים של קונה אישי.
טוען את הקובץ הארצי koneh-data.bin (נבנה כל יום ב-GitHub Actions ומפורסם כ-Release),
ואם אין גישה – נופל חזרה לנתונים המקומיים (13 הסניפים שנאספו ידנית).
"""
import array, json, os, re, struct, threading, time, urllib.request, zlib
from collections import OrderedDict, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_URL = os.environ.get("DATA_URL", "https://github.com/orizvi15-sys/koneh-ishi/releases/download/data-latest/koneh-data.bin")
CACHE_FILE = os.environ.get("DATA_CACHE", "/tmp/koneh-data.bin")
REFRESH_HOURS = float(os.environ.get("DATA_REFRESH_HOURS", "0.5"))


def toks(s):
    s = re.sub(r"[׳'\"`.\-–_/()*%,+]", " ", s)
    s = re.sub(r"(\d)([א-ת])|([א-ת])(\d)", r"\1\3 \2\4", s)
    return [w for w in s.split() if len(w) > 1 or w.isdigit()]


SIZE_RX = re.compile(r'(\d+(?:\.\d+)?)\s*(ק"?ג|קילו|גרם|גר|ג\'?|מ"?ל|ליטר|ל\'?)(?![א-ת])')


def parse_size(name):
    if re.search(r"\d\s*[\*xX×]\s*\d", name): return None   # מארזים (6*1.5) – לא משווים
    m = SIZE_RX.search(name)
    if not m: return None
    v, u = float(m.group(1)), m.group(2)
    if u.startswith("ק"): v *= 1000
    elif u.startswith("ל"): v *= 1000
    return v if v > 0 else None


_SUFFIX = re.compile(r"(יות|ות|ים|ייה|יה|ה|י)$")
_stem_cache = {}
def stem(w):
    """גזירה גסה של רבים/נקבה בעברית: עגבניות→עגבני, ביצים→ביצ"""
    r = _stem_cache.get(w)
    if r is None:
        r = _SUFFIX.sub("", w) if len(w) > 3 and not w.isdigit() else w
        r = _stem_cache[w] = r.translate(FINALS)
    return r


FINALS = str.maketrans("ךםןףץ", "כמנפצ")


def edit1(a, b):
    """האם המילים שונות בשינוי אחד לכל היותר (החלפה/הוספה/מחיקה/החלפת שתי אותיות סמוכות)"""
    if a == b: return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1: return False
    if la == lb:
        d = [i for i in range(la) if a[i] != b[i]]
        if len(d) == 1: return True
        return len(d) == 2 and d[1] == d[0] + 1 and a[d[0]] == b[d[1]] and a[d[1]] == b[d[0]]
    if la > lb: a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]: i += 1
    return a[i:] == b[i + 1:]


FB_SKIP = ("מטוג", "קפוא", "מרוסק", "חתוכ", "כבוש", "מבושל", "מיובש", "צלוי", "אפוי", "ממולא", "מקולף", "טחון", "רסק",
           "פיקנטי", "חריף", "גמדי", "שאלוט", "ירוק", "שרי", "כסביה", "ארגני", "אורגני")
FB_NEUTRAL = {"פרימיום", "ישראלי", "ישראלית", "בקרטון", "ארוז", "ארוזה", "ארוזים", "טרי", "טריה", "טרייה", "טריים", "לבן", "לבנה",
              "רגיל", "רגילה", "מובחר", "מובחרת", "סוג", "א", "שקית", "במשקל", "משקל", "תפזורת", "יבש", "L", "M", "XL", "S",
              "לק\"ג", "לקג", "ק\"ג", "קג", "יח'", "יח", "יחידות"}
FB_NEUTRAL_S = set()
FB_VARIANT = ("בטעם", "טעם", "חמוץ", "חריף", "ממולא", "נוגט", "דיאט", "זירו", "לייט", "ללא", "נטול", "דל ", "מועשר", "אורגני",
              "מאנצ", "שוקולד", "וניל", "תות", "פיקנטי", "מעושן", "קפוא", "עיזים", "מקורמל", "סויה", "שקדים", "שיבולת",
              "פחי", "שויצרי", "גבינה")
FB_SYN = {"סבון כלים": "נוזל כלים", "נוזל כלים": "סבון כלים", "מגבות נייר": "נייר מגבת", "נייר מגבת": "מגבות נייר",
          "נוזל כביסה": "ג'ל כביסה", "ג'ל כביסה": "נוזל כביסה", "קוקה קולה": "קוקה-קולה"}
UNIT_WORDS = {"ק\"ג", "קג", "קילו", "גרם", "גר", "ליטר", "מ\"ל", "מל", "יח", "יחידות", "ארוז", "ארוזה", "פרוס", "פרוסה"}

class Prices(dict):
    """מילון מחירים של סניף + מבצעי כמות"""
    def __init__(self, *a):
        super().__init__(*a); self.deals = {}


class Data:
    """items[i] = [code, name, avail, median_agorot]; stores[j] = dict(key, chain, n, a, p, blob, ts)"""

    def __init__(self, items, stores, version, built, blob_reader, source):
        self.items, self.stores, self.version, self.built, self.source = items, stores, version, built, source
        self._blob = blob_reader
        self.chains = {}; self.qa = {}
        self.code2i = {it[0]: i for i, it in enumerate(items)}
        self.name2i = {}
        for i, it in enumerate(items): self.name2i.setdefault(it[1], i)
        self.max_avail = max((it[2] for it in items), default=1) or 1
        self._size, self._groups = {}, {}
        self._cache = OrderedDict(); self._lock = threading.Lock()
        # אינדקס חיפוש
        self.itoks = [set(toks(it[1])) for it in items]
        inv, sinv = defaultdict(list), defaultdict(set)
        for i, t in enumerate(self.itoks):
            for w in t: inv[w].append(i); sinv[stem(w)].add(i)
        self.inv = dict(inv); self.sinv = dict(sinv); self.vocab = [w for w in self.inv if not w.isdigit()]
        # אינדקס תחיליות: "חל" -> [חלב, חלבון, ...] (במקום לסרוק את כל אוצר המילים בכל חיפוש)
        pre = defaultdict(list)
        for t in self.vocab:
            for k in range(2, len(t)): pre[t[:k]].append(t)
        self.pre = dict(pre); self.vset = set(self.vocab)
        self._bylen = defaultdict(list); self._fix = {}
        for t in self.vocab:
            if len(t) >= 3: self._bylen[len(t)].append(t)
        self._meta = {}

    def meta(self, i):
        """(המילה הראשונה אחרי גזירה, קבוצת המילים אחרי גזירה) – מחושב פעם אחת לכל מוצר"""
        m = self._meta.get(i)
        if m is None:
            t = toks(self.items[i][1])
            m = self._meta[i] = (stem(t[0]) if t else "", frozenset(stem(x) for x in t), len(t), [stem(x) for x in t])
        return m

    def warm(self):
        """מחשב מראש את נתוני החיפוש לכל המוצרים (ברקע), כדי שהחישוב הראשון למשתמש יהיה מהיר"""
        t0 = time.time()
        for i in range(len(self.items)): self.meta(i)
        print(f"warm-up: {time.time() - t0:.1f}s", flush=True)

    def prices(self, blob_no):
        """מילון: אינדקס מוצר -> מחיר בש"ח, לבלוק (סניף) נתון"""
        with self._lock:
            if blob_no in self._cache:
                self._cache.move_to_end(blob_no); return self._cache[blob_no]
        m = self._blob(blob_no)
        with self._lock:
            self._cache[blob_no] = m
            while len(self._cache) > 300: self._cache.popitem(last=False)
        return m

    def store_prices(self, store, clubs=()):
        """מחירי הסניף לרגע זה: מחיר רגיל + מבצעים שעדיין בתוקף (ולחבר מועדון של הרשת – גם מבצעי מועדון).
        מבצעי כמות ("3 ב-10") לא נכנסים למחיר היחידה – הם ב-P.deals = {אינדקס: (מחיר ליחידה, כמות מינימלית)}"""
        base = self.prices(store["blob"])
        layers, mlayers = [], []
        if store.get("pblob", -1) >= 0: layers.append(self.prices(store["pblob"]))
        if store.get("cblob", -1) >= 0 and store["chain"] in clubs: layers.append(self.prices(store["cblob"]))
        if store.get("mblob", -1) >= 0: mlayers.append(self.prices(store["mblob"]))
        if store.get("cmblob", -1) >= 0 and store["chain"] in clubs: mlayers.append(self.prices(store["cmblob"]))
        P = Prices(base); now = int(time.time() // 60)
        for L in layers:
            for i, v in L.items():
                p, end = (v[0], v[1]) if isinstance(v, tuple) else (v, 2 ** 32)
                if end >= now and p < P.get(i, 1e9): P[i] = p
        for L in mlayers:
            for i, v in L.items():
                p, end, q = v if len(v) == 3 else (v[0], v[1], 1)
                if end >= now and q > 1 and p < P.get(i, 1e9) - 0.009 and (i not in P.deals or p < P.deals[i][0]):
                    P.deals[i] = (p, q)
        return P

    def price(self, store, i):
        return self.prices(store["blob"]).get(i)

    def size(self, i):
        """גודל האריזה מתוך השם (בגרם/מ"ל), או None"""
        c = self._size.get(i, -1)
        if c != -1: return c
        c = parse_size(self.items[i][1]); self._size[i] = c; return c

    def group(self, i, query=None, n=30, tol=0.12):
        """מוצרים שקולים: אותו סוג ואותו גודל (±12%), לפי מילות החיפוש. משמש להשוואת 'הכי זול מאותו סוג'."""
        code = self.items[i][0]
        if not code[:1].isdigit(): return [i]          # ירקות/ביצים – כבר מאוחדים
        q = [stem(w) for w in toks(query or self.items[i][1])]
        if not q: return [i]
        key = (i, tuple(q), tol)
        if key in self._groups: return self._groups[key]
        s0 = self.size(i)
        out = [i]
        for j in self.search(query or self.items[i][1], 80):
            if j == i or not self.items[j][0][:1].isdigit(): continue
            mj = self.meta(j); t = mj[3]; ts = mj[1]
            if not all(w in ts or any(x.startswith(w) for x in ts) for w in q): continue   # כל מילות החיפוש (כולל מותג אם נכתב)
            if not t or t[0] != q[0] and self.meta(i)[0] != t[0]: continue  # אותו סוג מוצר (המילה הראשונה)
            s1 = self.size(j)
            if s0 is None or s1 is None:
                if s0 != s1: continue
            elif abs(s1 - s0) > tol * s0: continue
            out.append(j)
            if len(out) >= n: break
        self._groups[key] = out
        return out

    def fallback(self, i, query=None):
        """חלופות כשבסניף אין את המוצר עצמו: אותו מוצר בסיסי בשם/מותג/אריזה אחרים.
        ירקות/ביצים: פריט שקיל אחר עם אותן מילים בדיוק (+ מילים ניטרליות כמו "פרימיום", "ישראלי").
        ארוזים: אותו סוג מוצר (2 המילים הראשונות), גודל ±25%, בלי טעמים/גרסאות מיוחדות."""
        key = ("fb", i, query)
        if key in self._groups: return self._groups[key]
        code, name = self.items[i][0], self.items[i][1]
        if not FB_NEUTRAL_S: FB_NEUTRAL_S.update(stem(w) for w in FB_NEUTRAL)
        core = lambda t: {stem(w) for w in toks(t) if not w[:1].isdigit() and w not in FB_NEUTRAL and stem(w) not in FB_NEUTRAL_S}
        out = []
        if not code[:1].isdigit():
            mine = core(name)
            if mine:
                for j in self._unified():
                    if j == i or self.meta(j)[0] != self.meta(i)[0]: continue
                    if core(self.items[j][1]) == mine: out.append(j)
        else:
            words = [w for w in toks(query or name) if w not in UNIT_WORDS and not re.fullmatch(r"[\d.,*xX]+", w)][:2]
            if words:
                have = name + " " + (query or "")
                pct = set(re.findall(r"(\d+(?:\.\d+)?)\s*%", query or name))
                base = set(self.group(i, query))
                cands = self.group(i, " ".join(words), n=40, tol=0.25)
                alt = FB_SYN.get(" ".join(words))
                if alt: cands = cands + self.group(i, alt, n=40, tol=0.25)      # "סבון כלים" = "נוזל כלים"
                for j in dict.fromkeys(cands):
                    if j in base: continue
                    nm = self.items[j][1]
                    if any(f in nm and f not in have for f in FB_VARIANT): continue
                    if pct and not pct <= set(re.findall(r"(\d+(?:\.\d+)?)\s*%", nm)): continue   # חלב 3% ≠ 1%
                    out.append(j)
        self._groups[key] = out
        return out

    def _unified(self):
        u = getattr(self, "_uni", None)
        if u is None: u = self._uni = [j for j, it in enumerate(self.items) if not it[0][:1].isdigit()]
        return u

    def fix_word(self, w):
        """תיקון שגיאת כתיב קלה במילה שלא קיימת במאגר ("במבע"→"במבה", "חלן"→"חלב"): המילה הכי נפוצה שרחוקה באות אחת"""
        if w.isdigit() or len(w) < 3 or w in self.inv or stem(w) in self.sinv: return w
        c = self._fix.get(w)
        if c is not None: return c
        for t in (w.replace("ן", "ו"), w[:-1] + "ו" if w.endswith("ן") else w, w.replace("ו", "ן")[:-1] + w[-1]):
            if t != w and t in self.inv: self._fix[w] = t; return t   # ו/ן צמודות במקלדת – הטעות הנפוצה ביותר
        best = (0, w)
        for t in self._bylen.get(len(w), []) + self._bylen.get(len(w) - 1, []) + self._bylen.get(len(w) + 1, []):
            if edit1(w, t):
                f = len(self.inv[t])
                if f > best[0]: best = (f, t)
        self._fix[w] = best[1]
        return best[1]

    def search(self, name, n=6):
        q = [self.fix_word(w) for w in toks(name)]
        if not q: return []
        score = defaultdict(float)
        qs = [stem(w) for w in q]; q0 = qs[0]
        for w in q:
            hit = {}
            for i in self.inv.get(w, ()): hit[i] = 2
            for i in self.sinv.get(stem(w), ()):
                if i not in hit: hit[i] = 1.6
            if len(w) >= 2 and not w.isdigit():
                near = set(self.pre.get(w, ()))
                near.update(w[:k] for k in range(3, len(w)) if w[:k] in self.vset)
                near.discard(w)
                for t in near:
                    for i in self.inv[t]:
                        if i not in hit: hit[i] = 1
            for i, s in hit.items(): score[i] += s
        out = []
        for i, s in score.items():
            it = self.items[i]
            if not it[0][0].isdigit() and len(q) <= 3: s += 1
            f0, st, _, _ = self.meta(i)
            canon = False
            if f0 and f0 == q0:
                s += 0.6   # "אורז בסמטי" לפני "קובה אורז"
                # התאמה רק לפי גזירה ("חלבה"→"חלב") נחשבת "המוצר הבסיסי" רק למוצרים מאוחדים (ירקות/בשר במשקל)
                canon = all(w in self.itoks[i] or (sw in st and not it[0][0].isdigit()) for w, sw in zip(q, qs))
            sc = s / (len(q) + 0.3 * len(self.itoks[i])) + 0.35 * it[2] / self.max_avail
            if canon:   # בקשה כללית ("במבה") – המוצר הנפוץ ביותר מהסוג הזה
                sc += 0.5 + 3.0 * it[2] / self.max_avail
            if sc > 0.4: out.append((sc, i))
        return [i for _, i in sorted(out, reverse=True)[:n]]


def _display(chain, name, addr, city):
    n = name if name.startswith(chain) else f"{chain} {name}".strip()
    a = addr
    if city and city not in addr: a = f"{addr}, {city}" if addr else city
    return n, a or city


def _from_bin(path):
    f = open(path, "rb")
    if f.read(4) != b"KISH": raise ValueError("bad data file")
    hl = struct.unpack("<I", f.read(4))[0]
    h = json.loads(zlib.decompress(f.read(hl)))
    base = 8 + hl; blobs = h["blobs"]; lock = threading.Lock()

    def reader(b):
        off, ln = blobs[b]
        with lock:
            f.seek(base + off); raw = zlib.decompress(f.read(ln))
        n = struct.unpack("<I", raw[:4])[0]
        d = array.array("H"); d.frombytes(raw[4:4 + 2 * n])
        p = array.array("I"); p.frombytes(raw[4 + 2 * n:4 + 6 * n])
        ends = qs = None
        if len(raw) >= 4 + 10 * n and n:          # שכבת מבצעים – עם תוקף
            ends = array.array("I"); ends.frombytes(raw[4 + 6 * n:4 + 10 * n])
        if len(raw) >= 4 + 12 * n and n:          # מבצעי כמות – גם כמות מינימלית
            qs = array.array("H"); qs.frombytes(raw[4 + 10 * n:4 + 12 * n])
        out = {}; i = 0
        for k in range(n):
            i += d[k]
            if qs is not None: out[i] = (p[k] / 100, ends[k], qs[k])
            elif ends is not None: out[i] = (p[k] / 100, ends[k])
            else: out[i] = p[k] / 100
        return out

    stores = []
    STORE_FIX = {"7290803800003-7": [31.8605, 34.8225]}   # יוחננוף עקרון – הכתובת "המלך חסן 1" מוקמה בטעות בשדרות
    for row in h["stores"]:
        key, chain, name, addr, city, lat, lng, blob, ts, prec = row[:10]
        cblob = row[10] if len(row) > 10 else -1
        pblob = row[11] if len(row) > 11 else -1
        mblob = row[12] if len(row) > 12 else -1
        cmblob = row[13] if len(row) > 13 else -1
        if key in STORE_FIX: lat, lng = STORE_FIX[key]
        n, a = _display(chain, name, addr, city)
        stores.append({"key": key, "chain": chain, "n": n, "a": a, "p": [lat, lng], "blob": blob, "ts": ts, "prec": prec,
                       "cblob": cblob, "pblob": pblob, "mblob": mblob, "cmblob": cmblob})
    D_ = Data(h["items"], stores, h["v"], h.get("built", ""), reader, "remote")
    D_.chains = h.get("chains", {})
    D_.qa = h.get("qa", {})
    return D_


def _peek_version(path):
    with open(path, "rb") as f:
        if f.read(4) != b"KISH": return None
        hl = struct.unpack("<I", f.read(4))[0]
        return json.loads(zlib.decompress(f.read(hl))).get("v")


def _from_local():
    prods = json.load(open(os.path.join(HERE, "data", "prices.json"), encoding="utf-8"))
    sts = json.load(open(os.path.join(HERE, "data", "stores.json"), encoding="utf-8"))
    items, cols = [], [dict() for _ in sts]
    for i, (name, ps, prod) in enumerate(prods):
        v = sorted(x for x in ps if x is not None)
        items.append([name, name, len(v), int(round(v[len(v) // 2] * 100)) if v else 0])
        for j, x in enumerate(ps):
            if x is not None: cols[j][i] = x
    stores = [{"key": f"local-{j}", "chain": s["n"].split()[0], "n": s["n"], "a": s["a"], "p": s["p"], "blob": j, "ts": "", "prec": "manual"}
              for j, s in enumerate(sts)]
    return Data(items, stores, "local", "", lambda b: cols[b], "local")


D = None
_last_try = 0


def _download():
    tmp = CACHE_FILE + ".part"
    req = urllib.request.Request(DATA_URL, headers={"User-Agent": "koneh-ishi-bot"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    with open(tmp, "rb") as f:
        if f.read(4) != b"KISH": raise ValueError("downloaded file is not a data file")
    os.replace(tmp, CACHE_FILE)


def load(force_download=True):
    """טוען נתונים. מחזיר את אובייקט הנתונים הפעיל."""
    global D, _last_try
    _last_try = time.time()
    if force_download and DATA_URL:
        try:
            t0 = time.time(); _download()
            if D is not None and D.source == "remote" and _peek_version(CACHE_FILE) == D.version:
                return D                                   # אותה גרסה – לא בונים מחדש (חוסך זיכרון ו-CPU)
            nd = _from_bin(CACHE_FILE)
            if D is None or nd.version != D.version:
                threading.Thread(target=nd.warm, daemon=True).start()
                D = nd; print(f"נתונים: {len(D.stores)} סניפים, {len(D.items)} מוצרים, גרסה {D.version} ({time.time() - t0:.1f}s)", flush=True)
            return D
        except Exception as ex:
            print("טעינת נתונים מרחוק נכשלה:", ex, flush=True)
    if D is None and os.path.exists(CACHE_FILE):
        try: D = _from_bin(CACHE_FILE); return D
        except Exception as ex: print("cache load failed", ex)
    if D is None:
        D = _from_local(); print(f"נתונים מקומיים: {len(D.stores)} סניפים, {len(D.items)} מוצרים", flush=True)
    return D


def start_refresher():
    def loop():
        while True:
            # אם אנחנו על נתוני גיבוי – מנסים שוב כל 5 דקות, אחרת כל שעה
            time.sleep(300 if (D is None or D.source != "remote") else REFRESH_HOURS * 3600)
            try: load()
            except Exception as ex: print("refresh error", ex)
    threading.Thread(target=loop, daemon=True).start()
