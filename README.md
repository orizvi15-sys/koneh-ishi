# קונה אישי 🛒

בוט ווטסאפ שמשווה מחירי סופרמרקטים לפי המיקום של הלקוח, לומד את הרגלי הקנייה שלו וממליץ איפה הכי משתלם לקנות, כולל עלות הדלק.

**נתונים כרגע:** 13 סניפים בנס ציונה, רחובות וראשון לציון, מקבצי שקיפות המחירים מ-9–10/10/2026.

## הפעלה ב-Render
1. New → Web Service → בוחרים את המאגר הזה.
2. Start command: `python app.py` (ייקבע אוטומטית מ-`render.yaml`).
3. Environment (מספיק אחד מהשניים – טלגרם או ווטסאפ):
   - `TELEGRAM_TOKEN` – ה-token מ-@BotFather. החיבור לטלגרם נעשה אוטומטית כשהשרת עולה.
   - `WHATSAPP_TOKEN` – ה-access token מ-Meta (WhatsApp → API Setup)
   - `PHONE_NUMBER_ID` – ה-Phone number ID מאותו מסך
   - `VERIFY_TOKEN` – מילה סודית שתבחר (למשל `koneh-ishi-2026`)
   - `ANTHROPIC_API_KEY` – לא חובה, לקריאת קבלות
4. אחרי שהשרת עולה, הכתובת `https://<שם>.onrender.com` צריכה להציג "קונה אישי פועל ✅".

## חיבור ב-Meta
WhatsApp → Configuration → Webhook:
- Callback URL: `https://<שם>.onrender.com/webhook`
- Verify token: אותה מילה כמו `VERIFY_TOKEN`
- Webhook fields: לסמן **messages**

## שימוש
שולחים "היי" למספר הבוט. כתיבת **הגדרות** מתחילה את השאלות מחדש.

## קבצים
- `app.py` – הבוט
- `data/prices.json` – מחיר מבצע/רגיל לכל מוצר בכל סניף
- `data/stores.json` – הסניפים ומיקומם
- `data/users.json` – העדפות והיסטוריה של המשתמשים (נוצר אוטומטית)
