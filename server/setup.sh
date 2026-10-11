#!/bin/bash
# התקנת השרת הישראלי של קונה אישי – מריצים פעם אחת:
#   curl -fsSL https://raw.githubusercontent.com/orizvi15-sys/koneh-ishi/main/server/setup.sh | sudo bash
set -e
echo "== קונה אישי – התקנת שרת ישראלי =="
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3-venv python3-pip cron >/dev/null
fallocate -l 2G /swapfile 2>/dev/null && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile && echo '/swapfile none swap sw 0 0' >> /etc/fstab || true
if [ -d /opt/koneh/.git ]; then git -C /opt/koneh pull -q; else git clone -q https://github.com/orizvi15-sys/koneh-ishi /opt/koneh; fi
cd /opt/koneh
# הספרייה דורשת Python 3.12 (המערכת מגיעה עם גרסה חדשה מדי) – מתקינים 3.12 בנפרד עם uv
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null
export UV_PYTHON_INSTALL_DIR=/opt/koneh/python
rm -rf /opt/koneh/venv
uv venv -q --python 3.12 /opt/koneh/venv
VIRTUAL_ENV=/opt/koneh/venv uv pip install -q pip "git+https://github.com/OpenIsraeliSupermarkets/israeli-supermarket-scarpers.git"
/opt/koneh/venv/bin/python -m playwright install --with-deps chromium >/dev/null 2>&1 || true
touch /opt/koneh/.env && chmod 600 /opt/koneh/.env
cat > /opt/koneh/run.sh <<'RUN'
#!/bin/bash
cd /opt/koneh && git pull -q || true
set -a; . /opt/koneh/.env; set +a
exec /opt/koneh/venv/bin/python /opt/koneh/server/il_collect.py "$@"
RUN
chmod +x /opt/koneh/run.sh
( crontab -l 2>/dev/null | grep -v koneh ; echo "5 * * * * /opt/koneh/run.sh >> /opt/koneh/log.txt 2>&1" ) | crontab -
echo "== בדיקה: האם אתרי הרשתות נפתחים מכאן =="
/opt/koneh/run.sh test || true
echo
echo "== סיום ההתקנה ✅ =="
echo "השלב הבא: להכניס טוקן של GitHub עם הפקודה:  sudo nano /opt/koneh/.env"
