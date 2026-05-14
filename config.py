# config.py — โหลด .env และ expose ค่า config ทั้งหมด
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV     = os.path.join(BASE_DIR, ".env")

def _load_env() -> dict:
    """อ่าน .env แบบ minimal ไม่ต้องติดตั้ง python-dotenv"""
    env = {}
    if not os.path.exists(_ENV):
        return env
    with open(_ENV, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            env[key.strip()] = val.strip()
    return env

_cfg = _load_env()

def _get(key: str, default: str = "") -> str:
    """อ่านจาก .env ก่อน ถ้าไม่มีค่อยดู environment variable"""
    return _cfg.get(key) or os.environ.get(key, default)

# ── Network ───────────────────────────────────────
HOST = _get("HOST", "127.0.0.1")
PORT = int(_get("PORT", "12345"))

# ── Email ─────────────────────────────────────────
EMAIL_SENDER   = _get("EMAIL_SENDER")
EMAIL_PASSWORD = _get("EMAIL_PASSWORD")

# ── Timeouts ──────────────────────────────────────
SOCKET_TIMEOUT    = int(_get("SOCKET_TIMEOUT",    "5"))
API_TIMEOUT       = int(_get("API_TIMEOUT",        "3"))
WEATHER_CACHE_TTL = int(_get("WEATHER_CACHE_TTL",  "300"))
# SESSION_TIMEOUT: เวลา idle สูงสุดของ connection หนึ่ง session
# = SOCKET_TIMEOUT * 12 = 60 วิ (ผู้ใช้มี 1 นาทีก่อน server ตัด)
SESSION_TIMEOUT   = int(_get("SESSION_TIMEOUT",    str(SOCKET_TIMEOUT * 12)))

# ── Telegram ──────────────────────────────────────
TELEGRAM_TOKEN    = _get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = _get("TELEGRAM_CHAT_ID")
TELEGRAM_ALERTS   = _get("TELEGRAM_ALERTS", "true").lower() == "true"

# Bot แยกสำหรับผู้ใช้ทั่วไป (สร้างอีกตัวผ่าน @BotFather)
