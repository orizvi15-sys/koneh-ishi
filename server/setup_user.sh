#!/bin/bash
# התקנת השרת הישראלי בלי הרשאות מנהל (sudo) – הכל בתיקיית הבית:
#   curl -fsSL https://raw.githubusercontent.com/orizvi15-sys/koneh-ishi/main/server/setup_user.sh | bash
set -e
K="$HOME/koneh"
echo "== קונה אישי – התקנת שרת ישראלי (בלי sudo) =="
mkdir -p "$K"
# קוד הבוט
if command -v git >/dev/null; then
  if [ -d "$K/app/.git" ]; then git -C "$K/app" pull -q; else rm -rf "$K/app"; git clone -q https://github.com/orizvi15-sys/koneh-ishi "$K/app"; fi
else
  rm -rf "$K/app" && mkdir -p "$K/app" && curl -fsSL https://github.com/orizvi15-sys/koneh-ishi/archive/refs/heads/main.tar.gz | tar xz -C "$K/app" --strip-components=1
fi
# Python 3.12 + הספרייה (uv – בלי הרשאות מנהל)
command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
UV="$HOME/.local/bin/uv"; command -v uv >/dev/null && UV=$(command -v uv)
rm -rf "$K/venv"
"$UV" venv -q --python 3.12 "$K/venv"
VIRTUAL_ENV="$K/venv" "$UV" pip install -q pip "il-supermarket-scraper @ https://github.com/OpenIsraeliSupermarkets/israeli-supermarket-scarpers/archive/refs/heads/main.zip"
touch "$K/.env" && chmod 600 "$K/.env"
cat > "$K/run.sh" <<RUN
#!/bin/bash
cd "$K/app" && (git pull -q 2>/dev/null || true)
set -a; . "$K/.env"; set +a
exec "$K/venv/bin/python" "$K/app/server/il_collect.py" "\$@"
RUN
chmod +x "$K/run.sh"
( crontab -l 2>/dev/null | grep -v koneh ; echo "5 * * * * $K/run.sh >> $K/log.txt 2>&1" ) | crontab -
echo "== בדיקה: האם אתרי הרשתות נפתחים מכאן (כמה דקות) =="
"$K/run.sh" test || true
echo
echo "== סיום ההתקנה ✅ =="
