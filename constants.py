# constants.py — Protocol & domain constants
import json
from typing import Optional

COURT_TYPES  = ["ฟุตบอล", "บาสเกตบอล", "แบดมินตัน", "วอลเล่บอล"]
COURT_EMOJIS = {"ฟุตบอล": "⚽", "บาสเกตบอล": "🏀", "แบดมินตัน": "🏸", "วอลเล่บอล": "🏐"}
COURT_COLORS = {"ฟุตบอล": "#16a34a", "บาสเกตบอล": "#ea580c", "แบดมินตัน": "#7c3aed", "วอลเล่บอล": "#0284c7"}
TIME_SLOTS = [
    "06:00-08:00", "08:00-10:00", "10:00-12:00", "12:00-14:00",
    "14:00-16:00", "16:00-18:00", "18:00-20:00", "20:00-22:00",
]
MAX_ADVANCE_DAYS = 3

# ── User commands ─────────────────────────────────
CMD_LOGIN        = "LOGIN"
CMD_REGISTER     = "REGISTER"
CMD_LOGOUT       = "LOGOUT"
CMD_WEATHER      = "WEATHER"
CMD_GET_SCHEDULE = "GET_SCHEDULE"
CMD_BOOK         = "BOOK"
CMD_CANCEL       = "CANCEL"
CMD_MY_BOOKINGS  = "MY_BOOKINGS"
CMD_BROADCAST    = "BROADCAST"
CMD_BORROW           = "BORROW"
CMD_RETURN_EQUIPMENT = "RETURN_EQUIPMENT"
CMD_MY_LOANS         = "MY_LOANS"
CMD_EQUIPMENT_STOCK  = "EQUIPMENT_STOCK"

# ── Admin commands (ต้อง is_admin=True) ──────────
CMD_ADMIN_STATS     = "ADMIN_STATS"
CMD_ADMIN_USERS     = "ADMIN_USERS"
CMD_ADMIN_BOOKINGS  = "ADMIN_BOOKINGS"
CMD_ADMIN_LOANS     = "ADMIN_LOANS"
CMD_ADMIN_CANCEL    = "ADMIN_CANCEL"
CMD_ADMIN_RETURN    = "ADMIN_RETURN"
CMD_ADMIN_BROADCAST = "ADMIN_BROADCAST"
CMD_ADMIN_BAN       = "ADMIN_BAN"

# ── Equipment stock per court ─────────────────────
EQUIPMENT = {
    "ฟุตบอล": [
        {"id": "football",   "name": "ลูกฟุตบอล",       "emoji": "⚽", "stock": 5},
        {"id": "pump",       "name": "ปั๊มลม",           "emoji": "💨", "stock": 3},
        {"id": "cone",       "name": "กรวยฝึกซ้อม",     "emoji": "🔺", "stock": 10},
    ],
    "บาสเกตบอล": [
        {"id": "basketball", "name": "ลูกบาสเกตบอล",    "emoji": "🏀", "stock": 4},
        {"id": "pump",       "name": "ปั๊มลม",           "emoji": "💨", "stock": 3},
    ],
    "แบดมินตัน": [
        {"id": "racket",     "name": "แร็กเก็ต (คู่)",   "emoji": "🏸", "stock": 6},
        {"id": "shuttle",    "name": "ลูกขนไก่ (กระป๋อง)", "emoji": "🪶", "stock": 10},
    ],
    "วอลเล่บอล": [
        {"id": "volleyball", "name": "ลูกวอลเล่บอล",    "emoji": "🏐", "stock": 4},
        {"id": "pump",       "name": "ปั๊มลม",           "emoji": "💨", "stock": 3},
        {"id": "knee_pad",   "name": "สนับเข่า (คู่)",   "emoji": "🦵", "stock": 8},
    ],
}

# ── Wire protocol ─────────────────────────────────
def send_msg(sock, data: dict):
    raw = json.dumps(data, ensure_ascii=False).encode()
    sock.sendall(len(raw).to_bytes(4, "big") + raw)

def recv_msg(sock) -> Optional[dict]:
    def read(n: int) -> Optional[bytes]:
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf
    hdr = read(4)
    if not hdr:
        return None
    raw = read(int.from_bytes(hdr, "big"))
    return json.loads(raw) if raw else None
