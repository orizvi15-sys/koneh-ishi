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
REFRESH_HOURS = float(os.environ.get("DATA_REFRESH_HOURS", "1"))


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


def stem(w):
    """גזירה גסה של רבים/נקבה בעברית: עגבניות→עגבני, ביצים→ביצ"""
    if len(w) > 3 and not w.isdigit():
        w = re.sub(r"(יות|ות|ים|ייה|יה|ה|י)$", "", w)
    return w.translate(FINALS)


FINALS = str.maketrans("ךםןףץ", "כמנפצ")


class Data:
    """items[i] = [code, name, avail, median_agorot]; stores[j] = dict(key, chain, n, a, p, blob, ts)"""

    def __init__(self, items, stores, version, built, blob_reader, source):
        self.items, self.stores, self.version, self.built, self.source = items, stores, version, built, source
        self._blob = blob_reader
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
        """מחירי הסניף; לחבר מועדון של הרשת – משולב עם מחירי המועדון"""
        P = self.prices(store["blob"])
        if store.get("cblob", -1) >= 0 and store["chain"] in clubs:
            C = self.prices(store["cblob"])
            P = dict(P); P.update({i: v for i, v in C.items() if v < P.get(i, 1e9)})
        return P

    def price(self, store, i):
        return self.prices(store["blob"]).get(i)

    def size(self, i):
        """גודל האריזה מתוך השם (בגרם/מ"ל), או None"""
        c = self._size.get(i, -1)
        if c != -1: return c
        c = parse_size(self.items[i][1]); self._size[i] = c; return c

    def group(self, i, query=None, n=30):
        """מוצרים שקולים: אותו סוג ואותו גודל (±12%), לפי מילות החיפוש. משמש להשוואת 'הכי זול מאותו סוג'."""
        code = self.items[i][0]
        if not code[:1].isdigit(): return [i]          # ירקות/ביצים – כבר מאוחדים
        q = [stem(w) for w in toks(query or self.items[i][1])]
        if not q: return [i]
        key = (i, tuple(q))
        if key in self._groups: return self._groups[key]
        s0 = self.size(i)
        out = [i]
        for j in self.search(query or self.items[i][1], 80):
            if j == i or not self.items[j][0][:1].isdigit(): continue
            t = [stem(w) for w in toks(self.items[j][1])]
            ts = set(t)
            if not all(w in ts or any(x.startswith(w) for x in ts) for w in q): continue   # כל מילות החיפוש (כולל מותג אם נכתב)
            if not t or t[0] != q[0] and stem(toks(self.items[i][1])[0]) != t[0]: continue  # אותו סוג מוצר (המילה הראשונה)
            s1 = self.size(j)
            if s0 is None or s1 is None:
                if s0 != s1: continue
            elif abs(s1 - s0) > 0.12 * s0: continue
            out.append(j)
            if len(out) >= n: break
        self._groups[key] = out
        return out

    def search(self, name, n=6):
        q = toks(name)
        if not q: return []
        score = defaultdict(float)
        for w in q:
            hit = {}
            for i in self.inv.get(w, ()): hit[i] = 2
            for i in self.sinv.get(stem(w), ()):
                if i not in hit: hit[i] = 1.6
            if len(w) >= 2 and not w.isdigit():
                for t in self.vocab:
                    if t != w and (t.startswith(w) or (len(t) >= 3 and w.startswith(t))):
                        for i in self.inv[t]:
                            if i not in hit: hit[i] = 1
            for i, s in hit.items(): score[i] += s
        out = []
        for i, s in score.items():
            it = self.items[i]
            if not it[0][0].isdigit() and len(q) <= 3: s += 1
            first = toks(it[1])[:1]
            if first and stem(first[0]) == stem(q[0]): s += 0.6   # "אורז בסמטי" לפני "קובה אורז"
            sc = s / (len(q) + 0.3 * len(self.itoks[i])) + 0.35 * it[2] / self.max_avail
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
        out = {}; i = 0
        for k in range(n):
            i += d[k]; out[i] = p[k] / 100
        return out

    stores = []
    for row in h["stores"]:
        key, chain, name, addr, city, lat, lng, blob, ts, prec = row[:10]
        cblob = row[10] if len(row) > 10 else -1
        n, a = _display(chain, name, addr, city)
        stores.append({"key": key, "chain": chain, "n": n, "a": a, "p": [lat, lng], "blob": blob, "ts": ts, "prec": prec, "cblob": cblob})
    return Data(h["items"], stores, h["v"], h.get("built", ""), reader, "remote")


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
            nd = _from_bin(CACHE_FILE)
            if D is None or nd.version != D.version:
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
            time.sleep(REFRESH_HOURS * 3600)
            try: load()
            except Exception as ex: print("refresh error", ex)
    threading.Thread(target=loop, daemon=True).start()
