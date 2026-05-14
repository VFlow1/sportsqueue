#!/usr/bin/env python3
# server.py — Sports Queue Server
# Command Pattern: ทุก request route ผ่าน HANDLERS dict

import socket, threading, json, os, time, smtplib, ssl, requests, logging, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from email.mime.text      import MIMEText
from email.mime.multipart import MIMEMultipart
from requests.adapters import HTTPAdapter
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config      import HOST, PORT, EMAIL_SENDER, EMAIL_PASSWORD, API_TIMEOUT, WEATHER_CACHE_TTL, SOCKET_TIMEOUT, SESSION_TIMEOUT
from constants   import *
from auth        import hash_password, verify_password, migrate_if_needed
from email_templates import confirm_email, rain_alert_email
import telegram_bot as tg

# ── Logging setup ─────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(BASE_DIR, "server.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("server")


# ══════════════════════════════════════════════════
# STORAGE
# ══════════════════════════════════════════════════

USERS_FILE    = os.path.join(BASE_DIR, "data_users.json")
BOOKINGS_FILE = os.path.join(BASE_DIR, "data_bookings.json")
LOANS_FILE    = os.path.join(BASE_DIR, "data_loans.json")

users_db:    dict = {}
bookings_db: list = []
loans_db:    list = []
clients:     dict = {}   # username → socket (online only)
db_lock   = threading.RLock()
executor  = ThreadPoolExecutor(max_workers=500)
# --- ขยายขนาดท่อเชื่อมต่อสำหรับ Weather API ให้รองรับได้ 500 ท่อ ---
http = requests.Session()
adapter = HTTPAdapter(pool_connections=500, pool_maxsize=500)
http.mount('https://', adapter)
http.mount('http://', adapter)
http.headers.update({"User-Agent": "SportsQueue/1.0"})


# ══════════════════════════════════════════════════
# PERSISTENCE
# ══════════════════════════════════════════════════

def save_db():
    with db_lock:
        u, b, l = dict(users_db), list(bookings_db), list(loans_db)
    try:
        for path, data in [(USERS_FILE, u), (BOOKINGS_FILE, b), (LOANS_FILE, l)]:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("save_db ล้มเหลว: %s", e)

def load_db():
    for path, target in [(USERS_FILE, users_db), (BOOKINGS_FILE, bookings_db), (LOANS_FILE, loans_db)]:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            (target.update if isinstance(target, dict) else target.extend)(data)
            log.info("โหลด %s: %d รายการ", os.path.basename(path), len(data))
        except Exception as e:
            log.error("โหลด %s ล้มเหลว: %s", path, e)

def _autosave():
    while True:
        time.sleep(300)
        save_db()
        log.info("autosave สำเร็จ")


# ══════════════════════════════════════════════════
# UTILITY
# ══════════════════════════════════════════════════

def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def new_bid() -> str:
    """สร้าง Booking ID ที่ไม่ซ้ำกันด้วย UUID4"""
    return f"SQ{uuid.uuid4().hex[:8].upper()}"

def new_lid() -> str:
    """สร้าง Loan ID ที่ไม่ซ้ำกันด้วย UUID4"""
    return f"LN{uuid.uuid4().hex[:8].upper()}"


# ══════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════

def send_email(to: str, subject: str, html: str):
    if not to or "@" not in to:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = subject, EMAIL_SENDER, to
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as s:
            s.login(EMAIL_SENDER, EMAIL_PASSWORD)
            s.sendmail(EMAIL_SENDER, to, msg.as_string())
        log.info("Email ✓ → %s", to)
    except Exception as e:
        log.error("Email ✗ → %s : %s", to, e)

def email_bg(to: str, subject: str, html: str):
    executor.submit(send_email, to, subject, html)


# ══════════════════════════════════════════════════
# WEATHER
# ══════════════════════════════════════════════════

_wc  = {"cur": None, "hourly": [], "t_cur": 0.0, "t_hourly": 0.0}
TTL  = WEATHER_CACHE_TTL
LAT, LON = 14.8797, 102.0159

def _wmo(code: int) -> str:
    for threshold, text in [
        (0,  "ท้องฟ้าแจ่มใส ☀️"),
        (3,  "มีเมฆบางส่วน ⛅"),
        (48, "หมอก 🌫️"),
        (67, "ฝนตก 🌧️"),
        (82, "ฝนหนัก 🌨️"),
    ]:
        if code <= threshold:
            return text
    return "พายุ ⛈️"

def _om_cur() -> dict:
    """Fallback: ดึงอากาศปัจจุบันจาก Open-Meteo"""
    try:
        r = http.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
            "weather_code,wind_speed_10m,uv_index,precipitation_probability"
            "&wind_speed_unit=ms&timezone=Asia%2FBangkok",
            timeout=API_TIMEOUT,
        )
        c = r.json()["current"]
        return {
            "temperature": c["temperature_2m"],
            "humidity":    c["relative_humidity_2m"],
            "wind_speed":  c["wind_speed_10m"],
            "uv_index":    c["uv_index"],
            "rain_prob":   c["precipitation_probability"],
            "feels_like":  c["apparent_temperature"],
            "description": _wmo(c["weather_code"]),
            "source":      "Open-Meteo",
            "updated_at":  now_str(),
        }
    except Exception as e:
        log.warning("_om_cur ล้มเหลว: %s", e)
        return {
            "temperature": 30, "humidity": 70, "wind_speed": 5,
            "uv_index": 3, "rain_prob": 0, "feels_like": 32,
            "description": "ไม่มีข้อมูล", "source": "N/A", "updated_at": now_str(),
        }

def fetch_weather() -> dict:
    if _wc["cur"] and time.time() - _wc["t_cur"] < TTL:
        return _wc["cur"]
    try:
        r   = http.get("https://weather.sut.ac.th/api/current", timeout=API_TIMEOUT)
        raw = r.json() if r.ok else {}
        if raw.get("temperature") or raw.get("temp"):
            data = {
                "temperature": float(raw.get("temperature", raw.get("temp",        30))),
                "humidity":    float(raw.get("humidity",    raw.get("rh",          70))),
                "wind_speed":  float(raw.get("wind_speed",  raw.get("wind",         5))),
                "uv_index":    float(raw.get("uv_index",    raw.get("uv",           3))),
                "rain_prob":   float(raw.get("rain_probability", raw.get("rain_prob", 0))),
                "feels_like":  float(raw.get("feels_like",  raw.get("apparent_temp", 32))),
                "description": raw.get("description", "ท้องฟ้าแจ่มใส"),
                "source":      "สถานีอากาศ มทส",
                "updated_at":  now_str(),
            }
        else:
            data = _om_cur()
    except Exception:
        data = _om_cur()
    _wc["cur"], _wc["t_cur"] = data, time.time()
    return data

def fetch_hourly() -> list:
    if _wc["hourly"] and time.time() - _wc["t_hourly"] < TTL:
        return _wc["hourly"]
    try:
        r   = http.get(
            f"https://api.open-meteo.com/v1/forecast?latitude={LAT}&longitude={LON}"
            "&hourly=precipitation_probability,temperature_2m,weather_code"
            "&forecast_days=4&timezone=Asia%2FBangkok",
            timeout=API_TIMEOUT,
        )
        raw = r.json()
        if "hourly" not in raw:
            log.warning("fetch_hourly: ไม่พบคีย์ 'hourly' ใน response")
            return []
        hourly_data = raw["hourly"]
        data = [
            {"time": t, "rain_prob": p, "temperature": tmp, "weather_code": wc}
            for t, p, tmp, wc in zip(
                hourly_data["time"], hourly_data["precipitation_probability"],
                hourly_data["temperature_2m"], hourly_data["weather_code"],
            )
        ]
    except Exception as e:
        log.warning("fetch_hourly ล้มเหลว: %s", e)
        data = []
    _wc["hourly"], _wc["t_hourly"] = data, time.time()
    return data

def slot_weather(d: str, slot: str) -> dict:
    h  = int(slot[:2])
    ms = [x for x in fetch_hourly()
          if x["time"].startswith(d) and h <= int(x["time"][11:13]) < h + 2]
    if not ms:
        return fetch_weather()
    w = fetch_weather()
    return {
        **w,
        "temperature": round(sum(x["temperature"] for x in ms) / len(ms), 1),
        "rain_prob":   round(sum(x["rain_prob"]   for x in ms) / len(ms), 1),
        "description": _wmo(max(x["weather_code"] for x in ms)),
    }


# ══════════════════════════════════════════════════
# BOOKING
# ══════════════════════════════════════════════════

def get_schedule(target_date: str) -> dict:
    hourly, cw = fetch_hourly(), fetch_weather()
    schedule   = {}
    for court in COURT_TYPES:
        schedule[court] = {}
        for slot in TIME_SLOTS:
            booked = any(
                b["court_type"] == court and b["date"] == target_date
                and b["time_slot"] == slot and b["status"] == "confirmed"
                for b in bookings_db
            )
            h  = int(slot[:2])
            ms = [x for x in hourly
                  if x["time"].startswith(target_date) and h <= int(x["time"][11:13]) < h + 2]
            schedule[court][slot] = {
                "status":     "booked" if booked else "available",
                "rain_prob":  round(sum(x["rain_prob"]   for x in ms) / len(ms), 1) if ms else cw["rain_prob"],
                "temp":       round(sum(x["temperature"] for x in ms) / len(ms), 1) if ms else cw["temperature"],
                "weather_ok": bool(ms),
            }
    return schedule

def book_court(username: str, court_type: str, target_date: str, time_slot: str) -> dict:
    today = date.today()
    try:
        bd = datetime.strptime(target_date, "%Y-%m-%d").date()
    except ValueError:
        return {"ok": False, "msg": "รูปแบบวันที่ไม่ถูกต้อง"}

    # เช็คก่อนที่จะ parse slot_start เพื่อหลีกเลี่ยง ValueError
    if court_type.strip() not in COURT_TYPES:
        return {"ok": False, "msg": "ประเภทสนามไม่ถูกต้อง"}
    if time_slot.strip() not in TIME_SLOTS:
        return {"ok": False, "msg": "ช่วงเวลาไม่ถูกต้อง"}

    slot_start = datetime.strptime(f"{target_date} {time_slot.strip()[:5]}", "%Y-%m-%d %H:%M")

    for fail, msg in [
        (bd < today,                                            "ไม่สามารถจองวันที่ผ่านมาได้"),
        ((bd - today).days > MAX_ADVANCE_DAYS,                  f"จองล่วงหน้าได้สูงสุด {MAX_ADVANCE_DAYS} วัน"),
        (bd == today and datetime.now() >= slot_start,          "ช่วงเวลานี้ผ่านมาแล้ว ไม่สามารถจองย้อนหลังได้"),
    ]:
        if fail:
            return {"ok": False, "msg": msg}

    with db_lock:
        if any(b["username"] == username and b["date"] == target_date and b["status"] == "confirmed"
               for b in bookings_db):
            return {"ok": False, "msg": "คุณมีการจองในวันนี้แล้ว (จำกัด 1 รายการ/วัน)"}
        if any(b["court_type"] == court_type and b["date"] == target_date
               and b["time_slot"] == time_slot and b["status"] == "confirmed"
               for b in bookings_db):
            return {"ok": False, "msg": "ช่วงเวลานี้ถูกจองแล้ว"}
        bk = {
            "booking_id": new_bid(),
            "username":   username,
            "user_name":  users_db[username]["name"],
            "court_type": court_type,
            "date":       target_date,
            "time_slot":  time_slot,
            "status":     "confirmed",
            "booked_at":  now_str(),
        }
        bookings_db.append(bk)

    save_db()
    log.info("จอง: %s → %s %s %s", username, court_type, target_date, time_slot)
    tg.alert_booking(username, court_type, target_date, time_slot)
    sw = slot_weather(target_date, time_slot)
    if users_db[username].get("email"):
        email_bg(
            users_db[username]["email"],
            f"✅ ยืนยันการจอง {court_type} — {target_date} {time_slot}",
            confirm_email(bk, sw),
        )
    return {"ok": True, "msg": "จองสำเร็จ!", "booking_id": bk["booking_id"],
            "booking": bk, "slot_weather": sw}

def cancel_booking(username: str, booking_id: str) -> dict:
    with db_lock:
        for bk in bookings_db:
            if bk["booking_id"] != booking_id:
                continue
            if bk["username"] != username:
                return {"ok": False, "msg": "ไม่มีสิทธิ์ยกเลิก"}
            if bk["status"] != "confirmed":
                return {"ok": False, "msg": "ยกเลิกแล้ว"}
            bk["status"] = "cancelled"

            # auto-return: คืนอุปกรณ์ที่ยืมอยู่สำหรับการจองนี้
            returned = []
            for ln in loans_db:
                if ln["booking_id"] == booking_id and ln["status"] == "active":
                    ln["status"]      = "returned"
                    ln["returned_at"] = now_str()
                    returned.append(ln["loan_id"])
            if returned:
                log.info("auto-return loans %s เมื่อยกเลิก %s", returned, booking_id)

            save_db()
            log.info("ยกเลิก: %s → %s", username, booking_id)
            tg.alert_cancel(username, booking_id)
            return {"ok": True, "msg": "ยกเลิกเรียบร้อย",
                    "returned_loans": returned}
    return {"ok": False, "msg": "ไม่พบการจองนี้"}

def my_bookings(username: str) -> list:
    return [b for b in bookings_db if b["username"] == username and b["status"] == "confirmed"]


# ══════════════════════════════════════════════════
# EQUIPMENT / LOANS
# ══════════════════════════════════════════════════

def _stock_snapshot(court_type: str) -> list:
    items = EQUIPMENT.get(court_type, [])
    result = []
    for eq in items:
        in_use = sum(
            1 for ln in loans_db
            if ln["status"] == "active"
            and ln["court_type"] == court_type
            and eq["id"] in ln["items"]
        )
        result.append({**eq, "available": max(0, eq["stock"] - in_use)})
    return result

def cmd_equipment_stock(ctx: dict, req: dict) -> dict:
    court = req.get("court_type", "").strip()
    if court not in EQUIPMENT:
        return {"ok": False, "msg": "ประเภทสนามไม่ถูกต้อง"}
    return {"ok": True, "items": _stock_snapshot(court)}

def cmd_borrow(ctx: dict, req: dict) -> dict:
    username   = ctx["me"]
    booking_id = req.get("booking_id", "").strip()
    item_ids   = req.get("items", [])
    if not item_ids:
        return {"ok": False, "msg": "กรุณาเลือกอุปกรณ์อย่างน้อย 1 รายการ"}
    with db_lock:
        bk = next((b for b in bookings_db
                   if b["booking_id"] == booking_id and b["username"] == username
                   and b["status"] == "confirmed"), None)
        if not bk:
            return {"ok": False, "msg": "ไม่พบการจองนี้"}
        if any(ln["booking_id"] == booking_id and ln["status"] == "active" for ln in loans_db):
            return {"ok": False, "msg": "การจองนี้ยืมอุปกรณ์ไปแล้ว"}
        court     = bk["court_type"]
        stock     = _stock_snapshot(court)
        stock_map = {s["id"]: s for s in stock}
        invalid   = [i for i in item_ids if i not in stock_map]
        if invalid:
            return {"ok": False, "msg": f"อุปกรณ์ไม่ถูกต้อง: {invalid}"}
        unavail = [stock_map[i]["name"] for i in item_ids if stock_map[i]["available"] < 1]
        if unavail:
            return {"ok": False, "msg": f"อุปกรณ์หมด: {', '.join(unavail)}"}
        ln = {
            "loan_id":    new_lid(),
            "username":   username,
            "booking_id": booking_id,
            "court_type": court,
            "date":       bk["date"],
            "time_slot":  bk["time_slot"],
            "items":      item_ids,
            "item_names": [stock_map[i]["name"] for i in item_ids],
            "status":     "active",
            "borrowed_at": now_str(),
            "returned_at": None,
        }
        loans_db.append(ln)
    save_db()
    log.info("ยืม: %s → %s", username, item_ids)
    return {"ok": True, "msg": "ยืมอุปกรณ์สำเร็จ!", "loan": ln}

def cmd_return_equipment(ctx: dict, req: dict) -> dict:
    username = ctx["me"]
    loan_id  = req.get("loan_id", "").strip()
    with db_lock:
        for ln in loans_db:
            if ln["loan_id"] != loan_id:
                continue
            if ln["username"] != username:
                return {"ok": False, "msg": "ไม่มีสิทธิ์คืนอุปกรณ์นี้"}
            if ln["status"] != "active":
                return {"ok": False, "msg": "คืนอุปกรณ์นี้แล้ว"}
            ln["status"]      = "returned"
            ln["returned_at"] = now_str()
            save_db()
            log.info("คืน: %s → %s", username, loan_id)
            return {"ok": True, "msg": "คืนอุปกรณ์เรียบร้อย"}
    return {"ok": False, "msg": "ไม่พบรายการยืมนี้"}

def cmd_my_loans(ctx: dict, req: dict) -> dict:
    loans = [ln for ln in loans_db
             if ln["username"] == ctx["me"] and ln["status"] == "active"]
    return {"ok": True, "loans": loans}


# ══════════════════════════════════════════════════
# RAIN ALERT THREAD
# sent set เคลียร์ทุก 24 ชั่วโมง เพื่อไม่ให้โต
# ══════════════════════════════════════════════════

def _rain_alert_loop():
    sent       = set()
    last_clear = time.time()

    while True:
        time.sleep(60)

        # เคลียร์ sent set ทุก 24 ชั่วโมง
        if time.time() - last_clear > 86400:
            sent.clear()
            last_clear = time.time()

        active = [b for b in bookings_db
                  if b["status"] == "confirmed" and b["booking_id"] not in sent]
        for bk in active:
            try:
                start   = datetime.strptime(f"{bk['date']} {bk['time_slot'][:5]}", "%Y-%m-%d %H:%M")
                minutes = (start - datetime.now()).total_seconds() / 60
                if not (0 < minutes < 1440):
                    continue
                user = users_db.get(bk["username"])
                if not user or not user.get("email"):
                    continue
                sw = slot_weather(bk["date"], bk["time_slot"])
                if sw["rain_prob"] >= 50:
                    sent.add(bk["booking_id"])
                    log.info("แจ้งเตือนฝน → %s (%s)", bk["username"], bk["booking_id"])
                    tg.alert_rain(bk["username"], bk["court_type"], bk["date"], bk["time_slot"], sw["rain_prob"])
                    email_bg(
                        user["email"],
                        f"🌧️ แจ้งเตือนฝน — {bk['court_type']} {bk['date']}",
                        rain_alert_email(bk, sw["rain_prob"], sw),
                    )
            except Exception as e:
                log.warning("rain_alert_loop error: %s", e)


# ══════════════════════════════════════════════════
# ADMIN HANDLERS
# ══════════════════════════════════════════════════

def _require_admin(fn):
    """Decorator: ปฏิเสธถ้า user ไม่ใช่ admin"""
    def wrapper(ctx: dict, req: dict):
        if not users_db.get(ctx["me"], {}).get("is_admin"):
            log.warning("ปฏิเสธ admin cmd %s จาก %s", req.get("cmd"), ctx["me"])
            return {"ok": False, "msg": "⛔ คำสั่งนี้สำหรับ Admin เท่านั้น"}
        return fn(ctx, req)
    return wrapper

@_require_admin
def cmd_admin_stats(ctx: dict, req: dict) -> dict:
    today = date.today().strftime("%Y-%m-%d")
    return {"ok": True, "stats": {
        "total_users":     len(users_db),
        "online_users":    len(clients),
        "total_bookings":  len(bookings_db),
        "confirmed_today": sum(1 for b in bookings_db if b["date"] == today and b["status"] == "confirmed"),
        "cancelled_total": sum(1 for b in bookings_db if b["status"] == "cancelled"),
        "active_loans":    sum(1 for ln in loans_db if ln["status"] == "active"),
        "total_loans":     len(loans_db),
        "online_list":     list(clients.keys()),
        "server_time":     now_str(),
    }}

@_require_admin
def cmd_admin_users(ctx: dict, req: dict) -> dict:
    result = []
    for uname, u in users_db.items():
        result.append({
            "username":  uname,
            "name":      u["name"],
            "email":     u.get("email", ""),
            "is_admin":  u.get("is_admin", False),
            "is_banned": u.get("is_banned", False),
            "online":    uname in clients,
            "bookings":  sum(1 for b in bookings_db if b["username"] == uname and b["status"] == "confirmed"),
            "loans":     sum(1 for ln in loans_db  if ln["username"] == uname and ln["status"] == "active"),
        })
    result.sort(key=lambda x: (not x["online"], x["username"]))
    return {"ok": True, "users": result}

@_require_admin
def cmd_admin_bookings(ctx: dict, req: dict) -> dict:
    fdate, fcourt, fstatus = req.get("date"), req.get("court_type"), req.get("status")
    result = [
        b for b in bookings_db
        if (not fdate   or b["date"]       == fdate)
        and (not fcourt  or b["court_type"] == fcourt)
        and (fstatus in (None, "all") or b["status"] == fstatus)
    ]
    result.sort(key=lambda x: (x["date"], x["time_slot"]))
    return {"ok": True, "bookings": result, "count": len(result)}

@_require_admin
def cmd_admin_loans(ctx: dict, req: dict) -> dict:
    fstatus = req.get("status")
    result  = [ln for ln in loans_db if fstatus in (None, "all") or ln["status"] == fstatus]
    result.sort(key=lambda x: x["borrowed_at"], reverse=True)
    return {"ok": True, "loans": result, "count": len(result)}

@_require_admin
def cmd_admin_cancel(ctx: dict, req: dict) -> dict:
    bid = req.get("booking_id", "").strip()
    with db_lock:
        for bk in bookings_db:
            if bk["booking_id"] != bid:
                continue
            if bk["status"] != "confirmed":
                return {"ok": False, "msg": "ไม่ได้อยู่ในสถานะ confirmed"}
            bk["status"] = "cancelled"
            save_db()
            log.info("[Admin] force cancel %s โดย %s", bid, ctx["me"])
            _broadcast({"cmd": CMD_BROADCAST, "type": "schedule_update",
                        "msg": f"🔔 [Admin] ยกเลิกการจอง {bid}"})
            return {"ok": True, "msg": f"ยกเลิก {bid} สำเร็จ", "booking": bk}
    return {"ok": False, "msg": "ไม่พบการจองนี้"}

@_require_admin
def cmd_admin_return(ctx: dict, req: dict) -> dict:
    lid = req.get("loan_id", "").strip()
    with db_lock:
        for ln in loans_db:
            if ln["loan_id"] != lid:
                continue
            if ln["status"] != "active":
                return {"ok": False, "msg": "รายการนี้คืนแล้ว"}
            ln["status"]      = "returned"
            ln["returned_at"] = now_str()
            save_db()
            log.info("[Admin] force return %s โดย %s", lid, ctx["me"])
            return {"ok": True, "msg": f"บันทึกคืนอุปกรณ์ {lid} สำเร็จ"}
    return {"ok": False, "msg": "ไม่พบรายการยืมนี้"}

@_require_admin
def cmd_admin_broadcast(ctx: dict, req: dict) -> dict:
    msg = req.get("msg", "").strip()
    if not msg:
        return {"ok": False, "msg": "กรุณาระบุข้อความ"}
    log.info("[Admin] broadcast จาก %s: %s", ctx["me"], msg)

    # ── 1. ส่ง socket ให้ผู้ใช้ที่ online ────────────
    _broadcast({"cmd": CMD_BROADCAST, "type": "admin_msg", "msg": f"📢 [Admin] {msg}"})

    # ── 2. ส่ง email ให้ทุกบัญชีที่มีอีเมล ───────────
    email_count = 0
    if EMAIL_SENDER and EMAIL_PASSWORD:
        for uname, u in users_db.items():
            to = u.get("email", "").strip()
            if to and "@" in to:
                html = (
                    f"<div style='font-family:sans-serif;padding:20px'>"
                    f"<h2 style='color:#2563eb'>📢 ข้อความจากผู้ดูแลระบบ</h2>"
                    f"<p style='font-size:16px'>{msg}</p>"
                    f"<hr><p style='color:#888;font-size:12px'>"
                    f"Sports Queue — {now_str()}</p></div>"
                )
                email_bg(to, "📢 ข้อความจากผู้ดูแลระบบ — Sports Queue", html)
                email_count += 1
    else:
        log.warning("broadcast email ข้ามเพราะไม่ได้ตั้งค่า EMAIL_SENDER/EMAIL_PASSWORD")

    summary = f"ส่งถึง {len(clients)} คน (online)"
    if email_count:
        summary += f" + อีเมล {email_count} บัญชี"
    else:
        summary += " (ไม่ได้ส่งอีเมล — ไม่มี email config)"
    return {"ok": True, "msg": summary, "online": len(clients), "email_count": email_count}

@_require_admin
def cmd_admin_ban(ctx: dict, req: dict) -> dict:
    target = req.get("username", "").strip()
    action = req.get("action", "ban")
    if target not in users_db:
        return {"ok": False, "msg": "ไม่พบผู้ใช้นี้"}
    if target == ctx["me"]:
        return {"ok": False, "msg": "ไม่สามารถ ban ตัวเองได้"}
    users_db[target]["is_banned"] = (action == "ban")
    save_db()
    log.info("[Admin] %s '%s' โดย %s", action, target, ctx["me"])
    if action == "ban" and target in clients:
        _try_send(clients[target], {"cmd": CMD_BROADCAST, "type": "admin_msg",
                                     "msg": "⛔ บัญชีของคุณถูกระงับโดย Admin"})
        clients.pop(target, None)
    label = "ระงับ" if action == "ban" else "ปลดระงับ"
    return {"ok": True, "msg": f"{label}บัญชี {target} แล้ว"}


# ══════════════════════════════════════════════════
# COMMAND REGISTRY (Command Pattern)
# ══════════════════════════════════════════════════

def cmd_register(ctx: dict, req: dict) -> dict:
    u = req.get("username", "").strip()
    if not u:         return {"ok": False, "msg": "กรุณาระบุชื่อผู้ใช้"}
    if u in users_db: return {"ok": False, "msg": "ชื่อผู้ใช้นี้มีแล้ว"}
    with db_lock:
        users_db[u] = {
            "password_hash": hash_password(req["password"]),
            "email":         req.get("email", "").strip(),
            "name":          req.get("name", u).strip(),
        }
    save_db()
    log.info("สมัคร: %s", u)
    return {"ok": True, "msg": "สมัครสมาชิกสำเร็จ"}

def cmd_login(ctx: dict, req: dict) -> dict:
    u    = req.get("username", "").strip()
    user = users_db.get(u)
    if not user or not verify_password(req.get("password", ""), user["password_hash"]):
        log.warning("Login ล้มเหลว: %s", u)
        tg.alert_login_fail(u, str(ctx["conn"].getpeername()))
        return {"ok": False, "msg": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"}
    if user.get("is_banned"):
        log.warning("Login ถูก ban: %s", u)
        return {"ok": False, "msg": "⛔ บัญชีนี้ถูกระงับ กรุณาติดต่อผู้ดูแลระบบ"}
    # migrate legacy hash ถ้าจำเป็น
    new_hash = migrate_if_needed(user["password_hash"], req.get("password", ""))
    if new_hash:
        user["password_hash"] = new_hash
        save_db()
        log.info("migrate hash: %s", u)
    ctx["me"] = u
    clients[u] = ctx["conn"]
    log.info("Login สำเร็จ: %s", u)
    tg.alert_login_ok(u, user["name"], str(ctx["conn"].getpeername()))
    return {"ok": True, "msg": f"ยินดีต้อนรับ {user['name']}!",
            "name": user["name"], "is_admin": user.get("is_admin", False)}

def cmd_logout(ctx: dict, req: dict) -> dict:
    if ctx["me"]:
        clients.pop(ctx["me"], None)
        log.info("Logout: %s", ctx["me"])
        ctx["me"] = None
    return {"ok": True, "msg": "ออกจากระบบแล้ว"}

def cmd_weather(ctx: dict, req: dict) -> dict:
    return {"ok": True, "weather": fetch_weather(), "hourly": fetch_hourly()}

def cmd_get_schedule(ctx: dict, req: dict) -> dict:
    return {"ok": True, "schedule": get_schedule(
        req.get("date", date.today().strftime("%Y-%m-%d")))}

def cmd_book(ctx: dict, req: dict) -> dict:
    res = book_court(ctx["me"], req["court_type"], req["date"], req["time_slot"])
    if res["ok"]:
        _broadcast(
            {"cmd": CMD_BROADCAST, "type": "schedule_update",
             "msg": f"🔔 {users_db[ctx['me']]['name']} จองสนาม{req['court_type']} {req['date']} {req['time_slot']}"},
            exclude=ctx["me"],
        )
    return res

def cmd_cancel(ctx: dict, req: dict) -> dict:
    res = cancel_booking(ctx["me"], req["booking_id"])
    if res["ok"]:
        _broadcast({"cmd": CMD_BROADCAST, "type": "schedule_update",
                    "msg": f"🔔 ยกเลิกการจอง {req['booking_id']}"}, exclude=ctx["me"])
    return res

def cmd_my_bookings(ctx: dict, req: dict) -> dict:
    return {"ok": True, "bookings": my_bookings(ctx["me"])}

def cmd_unknown(ctx: dict, req: dict) -> dict:
    log.warning("คำสั่งไม่รู้จัก: %s จาก %s", req.get("cmd"), ctx.get("me"))
    return {"ok": False, "msg": f"ไม่รู้จักคำสั่ง: {req.get('cmd')}"}

# ── Public: ไม่ต้อง login ──────────────────────────
PUBLIC_HANDLERS = {
    CMD_REGISTER: cmd_register,
    CMD_LOGIN:    cmd_login,
    CMD_LOGOUT:   cmd_logout,
}

# ── Auth: ต้อง login ──────────────────────────────
AUTH_HANDLERS = {
    CMD_WEATHER:          cmd_weather,
    CMD_GET_SCHEDULE:     cmd_get_schedule,
    CMD_BOOK:             cmd_book,
    CMD_CANCEL:           cmd_cancel,
    CMD_MY_BOOKINGS:      cmd_my_bookings,
    CMD_EQUIPMENT_STOCK:  cmd_equipment_stock,
    CMD_BORROW:           cmd_borrow,
    CMD_RETURN_EQUIPMENT: cmd_return_equipment,
    CMD_MY_LOANS:         cmd_my_loans,
    CMD_ADMIN_STATS:      cmd_admin_stats,
    CMD_ADMIN_USERS:      cmd_admin_users,
    CMD_ADMIN_BOOKINGS:   cmd_admin_bookings,
    CMD_ADMIN_LOANS:      cmd_admin_loans,
    CMD_ADMIN_CANCEL:     cmd_admin_cancel,
    CMD_ADMIN_RETURN:     cmd_admin_return,
    CMD_ADMIN_BROADCAST:  cmd_admin_broadcast,
    CMD_ADMIN_BAN:        cmd_admin_ban,
}


# ══════════════════════════════════════════════════
# BROADCAST
# ══════════════════════════════════════════════════

def _try_send(sock, data: dict) -> bool:
    try:
        send_msg(sock, data)
        return True
    except Exception:
        return False

def _broadcast(msg: dict, exclude: str = None):
    dead = [u for u, s in list(clients.items())
            if u != exclude and not _try_send(s, msg)]
    for u in dead:
        clients.pop(u, None)


# ══════════════════════════════════════════════════
# CLIENT HANDLER
# ══════════════════════════════════════════════════

def handle_client(conn: socket.socket, addr):
    log.info("เชื่อมต่อ: %s", addr)
    ctx = {"conn": conn, "me": None}
    try:
        conn.settimeout(SESSION_TIMEOUT)  # idle timeout สำหรับ session
        while True:
            req = recv_msg(conn)
            if not req:
                break
            cmd     = req.get("cmd", "").strip()
            handler = PUBLIC_HANDLERS.get(cmd) or AUTH_HANDLERS.get(cmd) or cmd_unknown
            if handler in AUTH_HANDLERS.values() and not ctx["me"]:
                result = {"ok": False, "msg": "กรุณาเข้าสู่ระบบก่อน"}
            else:
                result = handler(ctx, req)
            send_msg(conn, result)
    except Exception as e:
        log.error("handle_client %s: %s", addr, e)
    finally:
        if ctx["me"]:
            clients.pop(ctx["me"], None)
        conn.close()
        log.info("ตัดการเชื่อมต่อ: %s", addr)


# ══════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════

def main():
    load_db()

    # สร้าง default accounts ถ้ายังไม่มี
    if "demo" not in users_db:
        users_db["demo"] = {
            "password_hash": hash_password("demo123"),
            "email": "", "name": "ผู้ใช้ทดสอบ",
        }
    if "admin" not in users_db:
        from config import _get
        users_db["admin"] = {
            "password_hash": hash_password(_get("ADMIN_PASSWORD", "admin1234")),
            "email": "", "name": "ผู้ดูแลระบบ", "is_admin": True,
        }

    for fn in [_rain_alert_loop, _autosave]:
        threading.Thread(target=fn, daemon=True).start()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(500)
    log.info("Server พร้อมใช้งาน %s:%s", HOST, PORT)
    print(f"╔══════════════════════════════╗\n║  Server :{PORT}               ║\n╚══════════════════════════════╝")
    tg.alert_server_start(HOST, PORT)
    tg.start_all_bots({
        "users_db":       users_db,
        "bookings_db":    bookings_db,
        "loans_db":       loans_db,
        "clients":        clients,
        "db_lock":        db_lock,
        "save_db":        save_db,
        "broadcast":      _broadcast,
        "try_send":       _try_send,
        "fetch_weather":  fetch_weather,
        "book_court":     book_court,
        "cancel_booking": cancel_booking,
        "get_stock":      _stock_snapshot,
        "email_bg":       email_bg,          # ← bot ใช้ส่ง email broadcast
    })

    while True:
        try:
            conn, addr = srv.accept()
            executor.submit(handle_client, conn, addr)
        except Exception as e:
            log.error("accept error: %s", e)
            tg.alert_server_error(str(e))

if __name__ == "__main__":
    main()
