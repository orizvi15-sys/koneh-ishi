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
    s = re.sub(r"[׳'\"`.\-–_/()*%,+\[\]{}]", " ", s)
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
              "לק\"ג", "לקג", "ק\"ג", "קג", "יח'", "יח", "יחידות", "פרימיום", "מגי"}
FB_NEUTRAL_S = set()

TYPE_HEAD = {stem(w) for w in ("גבינה", "גבינת", "צהובה", "חצי", "קשה")}   # סוג המוצר לפני המותג ("גבינה צהובה … עמק")

def _pcts(s):
    """אחוזים בשם: "28%" וגם "%28" (ככה כותבים חלק מהרשתות)"""
    return set(re.findall(r"(\d+(?:\.\d+)?)\s*%", s)) | set(re.findall(r"(?<![\d.])%(\d+(?:\.\d+)?)", s))
FB_VARIANT = ("בטעם", "טעם", "חמוץ", "חריף", "ממולא", "נוגט", "דיאט", "זירו", "לייט", "ללא", "נטול", "דל ", "מועשר", "אורגני",
              "מאנצ", "שוקולד", "וניל", "תות", "פיקנטי", "מעושן", "קפוא", "עיזים", "מקורמל", "סויה", "שקדים", "שיבולת",
              "פחי", "שויצרי", "גבינה", "לח ", "לילד", "תינוק", "בייבי", "מיני")
# פריט שקיל עם זנים – כשהמשתמש לא ציין זן, כל זן אחר הוא חלופה טובה
FB_FAMILY = {"P30": (["P31", "P32", "P33", "P34"], ["סמיט", "גאלה", "חרמון", "זהוב", "פינק"]),
             "P48": (["P49", "P50"], ["ירוק", "לבן", "אדום", "שחור"]),
             "P10": (["P11"], ["יבש", "אדום", "סגול"]), "P12": (["P13", "P14"], ["לבן", "אדום", "אפייה"]),
             "P37": (["P38"], ["קלמנטינ"]), "P04": (["P05", "P07"], ["אדום", "צהוב", "בהיר"])}
# לימוד עצמי של חלופות (learned_alt): מילים שמותר שיחסרו, ומילים נוספות שהופכות את המוצר למוצר אחר (אחרי גזירה ובלי אותיות סופיות)
LEARN_OPTIONAL = {"עוף"}
LEARN_BLOCK = {stem(w).rstrip("י").translate(FINALS) for w in (
    "שרי", "בייבי", "מיני", "ענק", "אשכולות", "קלוף", "קלופים", "חתוך", "קצוץ", "מיובש", "מבושל", "קפוא", "מוקפא", "מעושן", "ממולא", "מתובל",
    "מרינדה", "טחון", "שיפוד", "שיפודים", "כבוש", "במלח", "חמוץ", "מוחמץ", "צלוי", "מטוגן", "פרוס", "קוביות", "רצועות", "נתחים",
    "הודו", "עגל", "כבש", "טלה", "אווז", "ברווז", "דג", "דגים", "כבד", "לבבות", "קורקבן", "עצמות", "עור", "שניצל", "קבב", "נקניק",
    "עוף", "שלם", "קפ", "חזיר", "לבן", "על", "האש", "מוכן", "אפוי", "מנה", "מנות", "פנינה", "ליים", "סיני",
    "ירוק", "ירוקה", "ירוקות", "צהוב", "צהובה", "סגול", "סגולה", "שחור", "לבן", "אדום", "אדומה", "כתום", "ורוד",
    "סלט", "ממרח", "מחית", "רוטב", "מיץ", "ציפס", "חטיף", "לחתול", "לכלב", "צמחי", "טבעוני", "תחליף")}
FB_SYN = {"סבון כלים": "נוזל כלים", "נוזל כלים": "סבון כלים", "מגבות נייר": "נייר מגבת", "נייר מגבת": "מגבות נייר",
          "נוזל כביסה": "ג'ל כביסה", "ג'ל כביסה": "נוזל כביסה", "קוקה קולה": "קוקה-קולה", "נייר כסף": "נייר אלומיניום",
          "נייר אלומיניום": "נייר כסף", "משקה סויה": "חלב סויה", "מלפפונים במלח": "מלפפון במלח", "מלפפון במלח": "מלפפונים במלח"}
SEARCH_UNITS = {"גר", "גרם", "ג", "ק", "קג", "ק\"ג", "קילו", "ל", "ליטר", "מל", "מ\"ל", "יח", "יחידות", "יחי", "מ"}
UNIT_WORDS = {"ק\"ג", "קג", "קילו", "גרם", "גר", "ליטר", "מ\"ל", "מל", "יח", "יחידות", "ארוז", "ארוזה", "פרוס", "פרוסה"}

# סניפים שלא נכנסים להשוואה: שופרסל המפיץ סיטונאות (אשדוד, באר שבע) – חנות לעסקים, בלי חלב/לחם/ביצים (אורי 13/10)
EXCLUDE_STORES = re.compile(r"המפיץ|סיטונאות|Test Venue", re.I)
DELIVERY_STORES = re.compile(r"וולט|wolt", re.I)
PRICE_FILTER = True
PRICE_CACHE = 60      # סניפים במטמון (חישוב אחד משתמש בעד 25)   # tools/price_audit.py מכבה כדי לראות את הנתונים הגולמיים

class Prices(dict):
    """מילון מחירים של סניף + מבצעי כמות"""
    def __init__(self, *a):
        super().__init__(*a); self.deals = {}


class Data:
    """items[i] = [code, name, avail, median_agorot]; stores[j] = dict(key, chain, n, a, p, blob, ts)"""

    def __init__(self, items, stores, version, built, blob_reader, source):
        self.items, self.stores, self.version, self.built, self.source = items, stores, version, built, source
        self._blob = blob_reader
        self.chains = {}; self.qa = {}; self.delivery = []; self._dlv_only = None
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

    def prices(self, blob_no, cache=True):
        """מילון: אינדקס מוצר -> מחיר בש"ח, לבלוק (סניף) נתון.
        מטמון של PRICE_CACHE סניפים בלבד: 300 סניפים תפסו ~170MB, ובזמן עדכון הנתונים (שתי גרסאות בזיכרון) הבוט עבר
        את 512MB של Render ונהרג (13/10). cache=False – לסריקה של כל הסניפים (שומר מוצרים בסיסיים) בלי לנפח את המטמון."""
        with self._lock:
            if blob_no in self._cache:
                self._cache.move_to_end(blob_no); return self._cache[blob_no]
        m = self._blob(blob_no)
        if not cache: return m
        with self._lock:
            self._cache[blob_no] = m
            while len(self._cache) > PRICE_CACHE: self._cache.popitem(last=False)
        return m

    def free(self, deep=False):
        """משחרר מטמונים (לפני טעינת גרסה חדשה – כדי ששתי גרסאות לא יחזיקו מטמון מלא בבת אחת).
        deep: גם את אינדקס החיפוש (מחושב מחדש לבד אם צריך) – רגע לפני ההחלפה לגרסה החדשה"""
        with self._lock: self._cache.clear()
        self._groups.clear()
        if deep: self._meta.clear()
        trim_memory()

    def store_prices(self, store, clubs=(), cache=True):
        """מחירי הסניף לרגע זה: מחיר רגיל + מבצעים שעדיין בתוקף (ולחבר מועדון של הרשת – גם מבצעי מועדון).
        מבצעי כמות ("3 ב-10") לא נכנסים למחיר היחידה – הם ב-P.deals = {אינדקס: (מחיר ליחידה, כמות מינימלית)}"""
        pr = lambda b: self.prices(b, cache)
        base = pr(store["blob"])
        layers, mlayers = [], []
        if store.get("pblob", -1) >= 0: layers.append(pr(store["pblob"]))
        if store.get("cblob", -1) >= 0 and store["chain"] in clubs: layers.append(pr(store["cblob"]))
        if store.get("mblob", -1) >= 0: mlayers.append(pr(store["mblob"]))
        if store.get("cmblob", -1) >= 0 and store["chain"] in clubs: mlayers.append(pr(store["cmblob"]))
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
        # מסנן מחירים בלתי אפשריים (ביקורת מחירים 13/10): ₪0.01 למוצר אמיתי, מחיר ל-100 גרם שנרשם כמחיר לק"ג, טעות הקלדה.
        # מחיר זול מדי בטעות גורם לסניף "לנצח" – עדיף להתייחס אליו כ"אין נתון" ולהשתמש בחלופה / בהערכה האזורית.
        if PRICE_FILTER:
            items = self.items
            for i in [i for i, p in P.items() if p <= 0.10 or self._odd_price(items[i], p)]:
                P.pop(i, None); P.deals.pop(i, None)
        return P

    @staticmethod
    def _odd_price(it, p):
        med = it[3] / 100 if len(it) > 3 and it[3] else 0
        if med <= 1: return False
        if p < 0.25 * med: return True                              # רבע מהחציון הארצי ומטה
        return it[0][:1] in "PMW" and p > 4 * med                   # במשקל: פי 4 מהחציון = כנראה מחיר ליחידה/אריזה

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
        # stem(...).rstrip("י"): "עגבנייה"→"עגבנ" וגם "עגבניות"→"עגבני"→"עגבנ" (יחיד ורבים = אותו מוצר)
        core = lambda t: {stem(w).rstrip("י") for w in toks(t) if not w[:1].isdigit() and w not in FB_NEUTRAL and stem(w) not in FB_NEUTRAL_S}
        out = []
        if code in FB_FAMILY and not any(w in (query or "") for w in FB_FAMILY[code][1]):   # "תפוחים" כללי → כל זן תפוח עץ
            out += [self.code2i[c] for c in FB_FAMILY[code][0] if c in self.code2i]
        if code[:1] == "E":                                # ביצים: כל תבנית של 12 (M/L, "ביצי הכפר", "ביצי חופש") – גם בברקוד של רשת
            if not hasattr(self, "_eggs12"):
                self._eggs12 = [j for j, it in enumerate(self.items) if it[0][:1].isdigit() and re.match(r"(קרטון\s+)?ביצ(ים|י) ", it[1])
                                and re.search(r"(?<!\d)12(?!\d)", it[1]) and not re.search(r"הפתעה|קינדר|שוקולד|נודלס|אטריות|פסטה|מתוק", it[1])]
            out += [j for j in self._eggs12 if j != i]
        if not code[:1].isdigit():
            mine = core(name)
            if mine:
                for j in self._unified():
                    if j == i or self.meta(j)[0] != self.meta(i)[0]: continue
                    if core(self.items[j][1]) == mine or self.learned_alt(i, j, mine, core): out.append(j)
        else:
            words = [w for w in toks(query or name) if w not in UNIT_WORDS and not re.fullmatch(r"[\d.,*xX]+", w)][:2]
            if words:
                have = name + " " + (query or "")
                pct = _pcts(query or name)
                base = set(self.group(i, query))
                cands = self.group(i, " ".join(words), n=40, tol=0.25)
                alt = FB_SYN.get(" ".join(words))
                if alt: cands = cands + self.group(i, alt, n=40, tol=0.25)      # "סבון כלים" = "נוזל כלים"
                for j in dict.fromkeys(cands):
                    if j in base: continue
                    nm = self.items[j][1]
                    if any(f in nm and f not in have for f in FB_VARIANT): continue
                    if pct and not pct <= _pcts(nm): continue   # חלב 3% ≠ 1%
                    out.append(j)
                # אותו מוצר כשהמותג בסוף השם: "עמק פרוס 28% 200 גרם" = "גבינה צהובה פרוס בקופסא %28 200 ג' עמק" (סבב שבועי 18/10:
                # עמק פרוס יצא "חסר" ב-63% מהסניפים). לפני מילות המוצר מותר רק סוג המוצר ("גבינה צהובה") – לא "רוטב"/"סלט"/"ביסקוויט".
                # כל המילים, אותו גודל (±25%), מחיר רגיל דומה (פי 0.6–1.6).
                s0, p0 = self.size(i), self.items[i][3]
                qs = [stem(w) for w in toks(query or name) if w not in SEARCH_UNITS and not re.fullmatch(r"[\d.,*xX]+", w)]
                if len(qs) >= 2 and s0 and p0:
                    seen = base | set(out) | {i}
                    for j in self.search(query or name, 60):
                        if j in seen or not self.items[j][0][:1].isdigit(): continue
                        t = self.meta(j)[3]; ts = self.meta(j)[1]
                        if not all(w in ts or any(x.startswith(w) for x in ts) for w in qs): continue
                        k = next((n for n, x in enumerate(t) if any(x.startswith(w) for w in qs)), 0)
                        if not set(t[:k]) <= TYPE_HEAD: continue
                        s1, p1 = self.size(j), self.items[j][3]
                        if not s1 or abs(s1 - s0) > 0.25 * s0 or not p1 or not 0.6 * p0 <= p1 <= 1.6 * p0: continue
                        nm = self.items[j][1]; rest = " ".join(toks(nm)[k:])
                        if any(f in rest and f not in have for f in FB_VARIANT): continue
                        if pct and not pct <= _pcts(nm): continue
                        out.append(j); seen.add(j)
        self._groups[key] = out
        return out

    def learned_alt(self, i, j, mine, core):
        """לימוד עצמי: שם חדש של אותו מוצר בסיסי במשקל ("עגבניות טבע", "חזה טרי מחפוד") – בלי לחכות שמישהו יוסיף כלל.
        תנאים: כל המילים של המוצר המאוחד מופיעות (חוץ ממילים שמובנות מאליהן כמו "עוף"), המילים הנוספות לא משנות את המוצר
        (לא "שרי", "הודו", "קפוא", צבע אחר…), והמחיר הרגיל באותו טווח (פי 0.6–1.6) – שרי ב-₪19 לא ייחשב עגבנייה ב-₪7."""
        theirs = core(self.items[j][1])
        need = {w for w in mine if w not in LEARN_OPTIONAL}
        if not need or not need <= theirs: return False
        extra = {w.translate(FINALS) for w in theirs - mine}
        block = LEARN_BLOCK | self._varieties().get(self.meta(i)[0], set())
        if any(w.isdigit() or w in block or any(w.startswith(b) for b in block if len(b) >= 4) for w in extra): return False
        p0, p1 = self.items[i][3], self.items[j][3]
        return bool(p0 and p1 and 0.7 * p0 <= p1 <= 1.4 * p0)

    def _varieties(self):
        """זנים/סוגים שנלמדים מהנתונים: אם יש מוצר מאוחד "פלפל צהוב" – "צהוב" מבדיל בין סוגי פלפל, ולכן אינו חלופה לפלפל אדום"""
        v = getattr(self, "_var", None)
        if v is None:
            v = self._var = {}
            for j in self._unified():
                c = self.items[j][0]
                if c[:1] not in "PM": continue
                t = [stem(w).rstrip("י").translate(FINALS) for w in toks(self.items[j][1]) if not w[:1].isdigit()]
                if len(t) > 1: v.setdefault(self.meta(j)[0], set()).update(t[1:])
            for k in v: v[k] -= {"אדמ", "עצ"}            # חלק מהשם עצמו ("תפוח אדמה", "תפוח עץ"), לא זן
        return v

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
        allq = [self.fix_word(w) for w in toks(name)]
        pct = set(re.findall(r"(\d+(?:\.\d+)?)\s*%", name))                 # "3%" / "1%" – כן חשוב (סוג החלב/היוגורט)
        q = [w for w in allq if w in pct or not (re.fullmatch(r"[\d.,]+", w) or w in SEARCH_UNITS)] or allq   # מספרים/יחידות – רק רמז קטן
        nums = [w for w in allq if w not in q]
        if not q: return []
        score = defaultdict(float)
        qs = [stem(w) for w in q]; q0 = qs[0]
        for w in nums:                                  # "250" ב"גבינה צהובה 250 גר'" – עוזר לבחור גודל, לא מנצח את סוג המוצר
            for i in self.inv.get(w, ()): score[i] += 0.3
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
            if it[0][:1] in "PEM" and len(q) <= 3: s += 1     # ירקות/ביצים/בשר מאוחדים – "המוצר הבסיסי"; W (תפזורת) בלי בונוס
            f0, st, _, _ = self.meta(i)
            canon = False
            if f0 and f0 == q0:
                s += 0.6   # "אורז בסמטי" לפני "קובה אורז"
                # התאמה רק לפי גזירה ("חלבה"→"חלב") נחשבת "המוצר הבסיסי" רק למוצרים מאוחדים (ירקות/בשר במשקל)
                canon = all(w in self.itoks[i] or (sw in st and it[0][:1] in "PEM") for w, sw in zip(q, qs))
            full = len(q) <= 2 and not nums and f0 == q0 and all(w in self.itoks[i] or sw in st for w, sw in zip(q, qs))
            sc = s / (len(q) + 0.3 * len(self.itoks[i])) + (0.9 if full else 0.35) * it[2] / self.max_avail   # בקשה כללית קצרה שכל מילותיה במוצר → המוצר שנמכר בהכי הרבה סניפים
            if canon:   # בקשה כללית ("במבה") – המוצר הנפוץ ביותר מהסוג הזה
                sc += 0.5 + 3.0 * it[2] / self.max_avail
            if sc > 0.4: out.append((sc, i))
        dl = self.dlv_only()
        return [i for _, i in sorted(out, reverse=True) if i not in dl][:n]

    def dlv_only(self):
        """מוצרים שנמכרים רק במחסני משלוחים (וולט מרקט) ולא באף סופר – לא מציעים אותם בחיפוש (19/10: "מוצרלה טרייה" הותאם
        ל"מוצרלה טרייה | דליקטסן" של וולט, שלא נמכר באף סופר). avail כולל את מחסני המשלוחים, לכן: avail פחות מספר המחסנים ≤ 0."""
        if self._dlv_only is None:
            c = defaultdict(int)
            for st in self.delivery:
                for i in self.prices(st["blob"], cache=False): c[i] += 1
            self._dlv_only = {i for i, k in c.items() if self.items[i][2] - k <= 0}
        return self._dlv_only


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

    for it in h["items"]:                                     # סימני כיווון בשמות (וולט) – עד שהבנייה תנקה אותם
        if "\u202b" in it[1] or "\u202c" in it[1] or "\u200f" in it[1]: it[1] = re.sub(r"[\u200e\u200f\u202a-\u202e]", "", it[1]).strip()
    stores, delivery = [], []
    STORE_FIX = {"7290803800003-7": [31.8605, 34.8225],   # יוחננוף עקרון – הכתובת "המלך חסן 1" מוקמה בטעות בשדרות
                 "7290058249350-26": [32.2770, 34.8570]}  # וולט מרקט נתניה פולג – בקובץ העיר "נס ציונה" (19/10)
    for row in h["stores"]:
        key, chain, name, addr, city, lat, lng, blob, ts, prec = row[:10]
        if EXCLUDE_STORES.search(name or ""): continue          # סיטונאות לעסקים – לא בהמלצות (אורי 13/10)
        cblob = row[10] if len(row) > 10 else -1
        pblob = row[11] if len(row) > 11 else -1
        mblob = row[12] if len(row) > 12 else -1
        cmblob = row[13] if len(row) > 13 else -1
        if key in STORE_FIX: lat, lng = STORE_FIX[key]
        n, a = _display(chain, name, addr, city)
        st = {"key": key, "chain": chain, "n": n, "a": a, "p": [lat, lng], "blob": blob, "ts": ts, "prec": prec,
              "cblob": cblob, "pblob": pblob, "mblob": mblob, "cmblob": cmblob}
        # מחסן משלוחים (וולט מרקט) – לא נכנס להשוואה ולהמלצות, רק אופציה נוספת "במשלוח" (אורי 19/10)
        if DELIVERY_STORES.search(chain or ""): delivery.append(st)
        elif not DELIVERY_STORES.search(name or ""): stores.append(st)   # "קינג סטור וולט …" – סניף משלוחים של רשת אחרת: לא בכלל (19/10)
    D_ = Data(h["items"], stores, h["v"], h.get("built", ""), reader, "remote")
    D_.delivery = delivery
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


GATE = {}            # עדכון נתונים שנחסם: {"reason", "version", "t"} – הבוט ממשיך עם הנתונים הקודמים
GATE_ACCEPT = set()  # גרסאות שהמנהל אישר ידנית ("אשר נתונים")

def data_gate(old, new):
    """שומר סף: עדכון שנראה פגום לא נכנס לבוט. מחזיר סיבה, או None אם תקין."""
    if old is None or old.source != "remote" or new.version in GATE_ACCEPT: return None
    if len(new.stores) < 0.85 * len(old.stores): return f"מספר הסניפים ירד מ-{len(old.stores)} ל-{len(new.stores)}"
    if len(new.items) < 0.8 * len(old.items): return f"מספר המוצרים ירד מ-{len(old.items):,} ל-{len(new.items):,}"
    oc, nc = {}, {}
    for st in old.stores: oc[st["chain"]] = oc.get(st["chain"], 0) + 1
    for st in new.stores: nc[st["chain"]] = nc.get(st["chain"], 0) + 1
    for ch, n in oc.items():
        if n >= 15 and nc.get(ch, 0) < 0.6 * n: return f"{ch}: ירדה מ-{n} ל-{nc.get(ch, 0)} סניפים"
    # מחירים: אותם מוצרים נפוצים באותם סניפים – אם הרבה קפצו פתאום, כנראה קובץ פגום
    try:
        common = [it[0] for it in sorted(old.items, key=lambda x: -x[2])[:200]]
        okey = {st["key"]: st for st in old.stores}; ratios = []
        for st in new.stores[::max(1, len(new.stores) // 60)]:
            so = okey.get(st["key"])
            if not so: continue
            po, pn = old.store_prices(so, set()), new.store_prices(st, set())
            for c in common:
                io, inn = old.code2i.get(c), new.code2i.get(c)
                if io in po and inn in pn and po[io] > 0: ratios.append(pn[inn] / po[io])
        if len(ratios) > 300:
            ratios.sort(); jumped = sum(1 for r in ratios if r > 1.5 or r < 0.67) / len(ratios)
            if jumped > 0.15: return f"{jumped:.0%} מהמחירים הנפוצים קפצו פי 1.5 ומעלה בבת אחת"
    except Exception as ex:
        print("gate price check error", ex, flush=True)
    return None


def _download(dest=None):
    dest = dest or CACHE_FILE
    tmp = dest + ".part"
    req = urllib.request.Request(DATA_URL, headers={"User-Agent": "koneh-ishi-bot"})
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b: break
            f.write(b)
    with open(tmp, "rb") as f:
        if f.read(4) != b"KISH": raise ValueError("downloaded file is not a data file")
    os.replace(tmp, dest)


def trim_memory():
    """gc + החזרת זיכרון פנוי למערכת (glibc malloc_trim) – אחרת RSS נשאר גבוה גם אחרי שחרור"""
    import gc; gc.collect()
    try:
        import ctypes; ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception: pass

def load(force_download=True):
    """טוען נתונים. מחזיר את אובייקט הנתונים הפעיל."""
    global D, _last_try
    _last_try = time.time()
    if force_download and DATA_URL:
        try:
            t0 = time.time(); NEW = CACHE_FILE + ".new"; _download(NEW)
            if D is not None and D.source == "remote" and _peek_version(NEW) == D.version:
                return D                                   # אותה גרסה – לא בונים מחדש (חוסך זיכרון ו-CPU)
            if GATE and _peek_version(NEW) == GATE.get("version"):
                return D                                   # אותו עדכון שכבר נחסם – לא בודקים שוב
            if D is not None: D.free()                           # לפני שבונים גרסה חדשה – משחררים את המטמון של הישנה
            nd = _from_bin(NEW)
            if D is None or nd.version != D.version:
                why = data_gate(D, nd)
                if why:
                    GATE.clear(); GATE.update(reason=why, version=nd.version, t=time.time())
                    print("🛑 עדכון נתונים נחסם:", why, flush=True); return D
                GATE.clear()
                os.replace(NEW, CACHE_FILE)
                old, D = D, nd
                if old is not None: old.free(deep=True); del old; trim_memory()
                threading.Thread(target=nd.warm, daemon=True).start()
                print(f"נתונים: {len(D.stores)} סניפים, {len(D.items)} מוצרים, גרסה {D.version} ({time.time() - t0:.1f}s)", flush=True)
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
