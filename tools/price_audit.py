#!/usr/bin/env python3
"""ביקורת מחירים בכל המאגר – מחפשת מחירים שגויים שיכולים להטעות את ההמלצה.

הרצה: DATA_CACHE=/path/koneh-data.bin python3 tools/price_audit.py

נבדק בכל הסניפים (כולל מבצעים בתוקף, בלי מועדון):
  1. מחיר ≤ ₪0.10 או מעל ₪3,000
  2. מחיר רחוק מאוד מהחציון של אותו מוצר בכל הארץ (פי 4 ומעלה / רבע ומטה)
  3. מבצע של 75%+ הנחה מהמחיר הרגיל של אותו סניף (כנראה מבצע "קנה X קבל Y" שנקרא לא נכון)
  4. מבצעי כמות לא הגיוניים (כמות מעל 20, או מחיר ליחידה במבצע יקר מהרגיל)
  5. סל בסיסי: סניף שהסל שלו זול ב-35%+ מהחציון באזור – סימן שמחירים שגויים יגרמו לו "לנצח"
"""
import os, re, sys, time, statistics, collections

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.update(DRY_RUN="1", TELEGRAM_TOKEN="test", DATA_URL="")
for k in ("ANTHROPIC_API_KEY", "USERS_REPO", "USERS_TOKEN"): os.environ.pop(k, None)
import kdata
kdata.load(False)
import app
D = kdata.D
IL = set(app.IL_SERVER.values())

def main():
    t0 = time.time()
    kdata.PRICE_FILTER = False                     # קודם: הנתונים הגולמיים כמו שהרשתות פרסמו
    raw_n = filt_n = 0
    stores = [s for s in D.stores if not re.search(r"\bBE\b|Be\b", s["n"])]
    by_item = collections.defaultdict(list)          # i → [(מחיר, סניף)]
    promo_bad = collections.Counter(); promo_ex = collections.defaultdict(list)
    deal_bad = collections.Counter(); deal_ex = collections.defaultdict(list)
    extreme = collections.Counter(); extreme_ex = collections.defaultdict(list)
    for s in stores:
        base = D.prices(s["blob"]); P = D.store_prices(s, ())
        raw_n += len(P); filt_n += sum(1 for i, p in P.items() if p <= 0.10 or D._odd_price(D.items[i], p))
        for i, p in P.items():
            by_item[i].append((p, s))
            if p <= 0.10 or p > 3000:
                extreme[s["chain"]] += 1
                if len(extreme_ex[s["chain"]]) < 3: extreme_ex[s["chain"]].append(f"{D.items[i][1][:40]} ₪{p}")
            b = base.get(i)
            if b and p < 0.25 * b and b > 4:
                promo_bad[s["chain"]] += 1
                if len(promo_ex[s["chain"]]) < 3: promo_ex[s["chain"]].append(f"{D.items[i][1][:40]}: רגיל ₪{b} → מבצע ₪{p}")
        for i, (u, q) in P.deals.items():
            reg = P.get(i)
            if q > 20 or (reg and u > reg + 0.01):
                deal_bad[s["chain"]] += 1
                if len(deal_ex[s["chain"]]) < 3: deal_ex[s["chain"]].append(f"{D.items[i][1][:40]}: {q} יח' ב-₪{u * q:.1f} (רגיל ₪{reg})")
    # 2. חריגים מול החציון הארצי
    out_lo = collections.Counter(); out_hi = collections.Counter(); out_ex = collections.defaultdict(list); items_bad = 0
    for i, lst in by_item.items():
        if len(lst) < 8: continue
        m = statistics.median(p for p, _ in lst)
        if m <= 0: continue
        bad = False
        for p, s in lst:
            if p < m / 4 or p > m * 4:
                bad = True
                (out_lo if p < m else out_hi)[s["chain"]] += 1
                if len(out_ex[s["chain"]]) < 4: out_ex[s["chain"]].append(f"{D.items[i][1][:40]}: ₪{p} (חציון ₪{m:.2f})")
        items_bad += bad
    kdata.PRICE_FILTER = True                      # הסל – כמו שהבוט רואה (אחרי המסנן)
    # 5. סל בסיסי לפי אזור
    names = ["חלב", "לחם אחיד", "ביצים", "קוטג'", "פסטה", "אורז", "שמן", "סוכר", "קמח", "עגבניה", "מלפפון", "בננה", "חזה עוף", "במבה", "נייר טואלט"]
    found, _ = app.match_items([{"name": n, "qty": 1} for n in names])
    cheap = []
    sample = [s for s in stores if s["chain"] not in ("יילו", "דור אלון", "סופר יודה")][::3]
    for s in sample:
        u = app.mkuser if hasattr(app, "mkuser") else None
        r = app.compute({"step": "done", "prefs": {"home": s["p"], "mode": "car", "maxMin": 15, "splitMin": -1, "clubs": []}, "hist": [],
                         "items": [dict(x) for x in found]})
        if not isinstance(r, dict): continue
        rows = re.findall(r"(?:🏆|▫️) \*([^*]+)\*[\s\S]*?💰 ₪(\d+)", r["others"])
        if len(rows) < 4: continue
        vals = [int(v) for _, v in rows]; med = statistics.median(vals)
        for nm, v in rows:
            if int(v) < 0.65 * med: cheap.append((nm.strip(), int(v), med))
    cheap = sorted(set(cheap), key=lambda x: x[1] / x[2])[:15]

    print(f"נבדקו {len(stores)} סניפים, {sum(len(v) for v in by_item.values()):,} מחירים, {len(by_item):,} מוצרים ({time.time() - t0:.0f} שנ')")
    print(f"מוצרים עם מחיר חריג מאוד (פי 4 מהחציון) לפחות בסניף אחד: {items_bad:,}")
    print(f"🧹 המסנן של הבוט מתעלם מ-{filt_n:,} מחירים בלתי אפשריים ({filt_n / max(raw_n, 1):.3%} מכל המחירים)\n")
    def show(title, cnt, ex):
        print(f"## {title} – {sum(cnt.values()):,}")
        for ch, n in cnt.most_common(12):
            print(f"  {ch}{' (שרת ישראלי)' if ch in IL else ''}: {n:,}  | " + " ; ".join(ex.get(ch, [])[:3]))
        print()
    show("מחיר ₪0.10 ומטה / מעל ₪3,000", extreme, extreme_ex)
    show("מבצע של 75%+ הנחה מהמחיר הרגיל", promo_bad, promo_ex)
    show("מבצע כמות לא הגיוני", deal_bad, deal_ex)
    allc = collections.Counter(); allc.update(out_lo); allc.update(out_hi)
    show(f"חריג מהחציון הארצי (נמוך {sum(out_lo.values()):,} / גבוה {sum(out_hi.values()):,})", allc, out_ex)
    print("## סניפים שהסל הבסיסי שלהם זול ב-35%+ מהחציון באזור (חשוד):")
    for nm, v, med in cheap: print(f"  {nm}: ₪{v} מול חציון ₪{med:.0f}")
    if not cheap: print("  אין ✅")

if __name__ == "__main__":
    main()
