# telegram_bot.py — Admin Bot สำหรับ Sports Queue  (Enhanced v3)
#
# ✅ ทุกอย่างที่ admin_cli ทำได้:
#   • สถิติระบบ (stats)
#   • จัดการผู้ใช้  — list, search, ban/unban
#   • ดูการจอง     — filter วัน + สนาม, force cancel
#   • ยืมอุปกรณ์   — filter status + สนาม, force return
#   • Broadcast ข้อความ
#   • สภาพอากาศ (weather)
#
# 🆕 มากกว่า admin_cli:
#   • /find <query>      ค้นหาผู้ใช้ (ชื่อ/username)
#   • /today             สรุปวันนี้ฉับไว (bookings + online + weather)
#   • /schedule          ตารางการจองวันนี้ จัดกลุ่มตามสนาม
#   • /kick <user>       Disconnect ผู้ใช้ที่ออนไลน์ทันที
#   • /setadmin <user>   Toggle สิทธิ์ Admin
#   • /uptime            เวลาที่ server ทำงานมา
#   • /clearlog          ล้าง log file
#   • /alert             Toggle push alerts พร้อมปุ่ม
#   • Court filter ในเมนูการจอง (กรองตามสนาม)
#   • Stats แยกรายสนาม
#   • Push alert: login ok/fail, booking, cancel, rain, loan, return, ban, error
#   • Auto daily-summary ส่งทุกวันเวลาที่ตั้งไว้
#   • Pagination ทุกรายการยาว
#
# ตั้งค่าใน .env:
#   TELEGRAM_TOKEN=<token จาก @BotFather>
#   TELEGRAM_CHAT_ID=<id จาก @userinfobot>
#   TELEGRAM_ALERTS=true
#   DAILY_SUMMARY_HOUR=8    (optional — ส่ง daily summary เวลาบ่าย default 8)

from __future__ import annotations
from requests.adapters import HTTPAdapter
import logging, os, threading, time
from datetime import date, datetime, timedelta
from typing   import Optional

import requests

from config    import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ALERTS, BASE_DIR
from constants import COURT_TYPES, COURT_EMOJIS, TIME_SLOTS, EQUIPMENT

log      = logging.getLogger("telegram_bot")
LOG_FILE = os.path.join(BASE_DIR, "server.log")

# ── หน้า pagination ───────────────────────────────
PAGE_SIZE = 8

# ── Daily summary hour (0-23) ────────────────────
try:
    from config import _get as _cfg_get
    DAILY_SUMMARY_HOUR = int(_cfg_get("DAILY_SUMMARY_HOUR", "8"))
except Exception:
    DAILY_SUMMARY_HOUR = 8


# ══════════════════════════════════════════════════
# TELEGRAM API WRAPPER
# ══════════════════════════════════════════════════


class TelegramAPI:
    def __init__(self, token: str):
        self._base    = f"https://api.telegram.org/bot{token}"
        self._session = requests.Session()
        
        # --- ขยายขนาดท่อเชื่อมต่อให้รองรับ 200 Threads ---
        pool_size = 500
        adapter = HTTPAdapter(pool_connections=pool_size, pool_maxsize=pool_size)
        self._session.mount('https://', adapter)
        self._session.mount('http://', adapter)

    def call(self, method: str, payload: dict = None, timeout: int = 10) -> Optional[dict]:
        try:
            r    = self._session.post(f"{self._base}/{method}", json=payload or {}, timeout=timeout)
            data = r.json()
            return data if data.get("ok") else None
        except requests.Timeout:
            return None
        except Exception as e:
            log.debug("TG %s: %s", method, e)
            return None

    def send(self, chat_id: str, text: str,
             markup: dict = None, parse_mode: str = "HTML") -> Optional[int]:
        pl = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if markup: pl["reply_markup"] = markup
        res = self.call("sendMessage", pl)
        return res["result"]["message_id"] if res else None

    def edit(self, chat_id: str, msg_id: int, text: str, markup: dict = None):
        pl = {"chat_id": chat_id, "message_id": msg_id,
              "text": text, "parse_mode": "HTML"}
        if markup: pl["reply_markup"] = markup
        self.call("editMessageText", pl)

    def answer(self, cb_id: str, text: str = "", alert: bool = False):
        self.call("answerCallbackQuery",
                  {"callback_query_id": cb_id, "text": text, "show_alert": alert})

    def delete(self, chat_id: str, msg_id: int):
        self.call("deleteMessage", {"chat_id": chat_id, "message_id": msg_id})

    def get_updates(self, offset: int) -> list:
        res = self.call("getUpdates",
                        {"offset": offset, "timeout": 25,
                         "allowed_updates": ["message", "callback_query"]},
                        timeout=30)
        return res.get("result", []) if res else []


# ── Keyboard helpers ──────────────────────────────

def kb(*rows) -> dict:
    return {"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in row]
        for row in rows
    ]}

def kb_grid(items: list, cols: int = 2) -> dict:
    rows = [items[i:i+cols] for i in range(0, len(items), cols)]
    return kb(*rows)

def btn_back(target: str) -> tuple:
    return ("◀️ กลับ", f"menu:{target}")

def btn_back_main() -> tuple:
    return ("🏠 เมนูหลัก", "menu:main")

def _make_kb(rows: list) -> dict:
    """แปลง rows ของ (text, data) tuples → Telegram inline keyboard dict ที่ถูกต้อง
    แก้บัค: Python tuple encode เป็น JSON array ซึ่ง Telegram ไม่รับ — ต้องเป็น dict
    """
    return {"inline_keyboard": [
        [{"text": t, "callback_data": d} for t, d in row]
        for row in rows
    ]}


# ── Text helpers ──────────────────────────────────

def now_s() -> str:
    return datetime.now().strftime("%d/%m %H:%M")

def now_full() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def sep() -> str:
    return "\n" + "─" * 28 + "\n"


# ══════════════════════════════════════════════════
# ALERT SYSTEM
# ══════════════════════════════════════════════════

_alerts_on: bool             = TELEGRAM_ALERTS
_api:  Optional[TelegramAPI] = None
_cid:  str                   = str(TELEGRAM_CHAT_ID or "")
_server_start_time: float    = time.time()


def _alert(text: str):
    if not _alerts_on or not _api or not _cid: return
    threading.Thread(target=_api.send, args=(_cid, text), daemon=True).start()

# ── Preset alerts ─────────────────────────────────

def alert_login_ok(username: str, name: str, addr: str):
    _alert(f"🟢 <b>Login</b>  {name} <code>{username}</code>\n🌐 {addr}  ·  {now_s()}")

def alert_login_fail(username: str, addr: str):
    _alert(f"🔴 <b>Login ล้มเหลว</b>  <code>{username}</code>\n🌐 {addr}  ·  {now_s()}")

def alert_server_start(host: str, port: int):
    _alert(f"🚀 <b>Server เริ่มแล้ว</b>  {host}:{port}  ·  {now_s()}")

def alert_server_error(error: str):
    _alert(f"🆘 <b>Server Error</b>\n<code>{str(error)[:300]}</code>\n{now_s()}")

def alert_booking(username: str, court: str, bdate: str, slot: str):
    _alert(f"📅 <b>จองใหม่</b>\n"
           f"👤 <code>{username}</code>  ·  {COURT_EMOJIS.get(court,'🏟')} {court}\n"
           f"📆 {bdate}  ⏰ {slot}  ·  {now_s()}")

def alert_cancel(username: str, booking_id: str):
    _alert(f"❌ <b>ยกเลิกการจอง</b>\n"
           f"👤 <code>{username}</code>  ·  <code>{booking_id}</code>  ·  {now_s()}")

def alert_rain(username: str, court: str, bdate: str, slot: str, prob: float):
    _alert(f"🌧 <b>แจ้งเตือนฝน {prob:.0f}%</b>\n"
           f"👤 <code>{username}</code>  ·  {court}\n"
           f"📆 {bdate}  ⏰ {slot}  ·  {now_s()}")

# 🆕 Alert เพิ่มเติม
def alert_loan(username: str, court: str, items: list):
    items_str = ", ".join(items[:3])
    _alert(f"📦 <b>ยืมอุปกรณ์</b>\n"
           f"👤 <code>{username}</code>  ·  {COURT_EMOJIS.get(court,'🏟')} {court}\n"
           f"🎒 {items_str}  ·  {now_s()}")

def alert_return(username: str, loan_id: str):
    _alert(f"↩️ <b>คืนอุปกรณ์</b>\n"
           f"👤 <code>{username}</code>  ·  <code>{loan_id}</code>  ·  {now_s()}")

def alert_ban(username: str, action: str):
    icon = "⛔" if action == "ban" else "✅"
    label = "ถูกระงับ" if action == "ban" else "ปลดระงับแล้ว"
    _alert(f"{icon} <b>บัญชี{label}</b>  <code>{username}</code>  ·  {now_s()}")

def alert_kick(username: str):
    _alert(f"🦵 <b>Kick</b>  <code>{username}</code> ออกจากระบบแล้ว  ·  {now_s()}")

def alerts_on():  global _alerts_on; _alerts_on = True
def alerts_off(): global _alerts_on; _alerts_on = False


# ══════════════════════════════════════════════════
# ADMIN BOT
# ══════════════════════════════════════════════════

class AdminBot:
    def __init__(self, api: TelegramAPI, ctx: dict):
        self._api  = api
        self._ctx  = ctx
        self._pending: dict[int, dict] = {}

    # ── Properties ────────────────────────────────

    @property
    def _db(self): return self._ctx["users_db"]
    @property
    def _bks(self): return self._ctx["bookings_db"]
    @property
    def _loans(self): return self._ctx["loans_db"]
    @property
    def _clients(self): return self._ctx["clients"]

    # ── Entry points ──────────────────────────────

    def on_message(self, text: str):
        parts = text.strip().split()
        cmd   = parts[0].lower().split("@")[0]
        args  = parts[1:]
        handler = {
            "/start":    self._main_menu,
            "/menu":     self._main_menu,
            "/help":     self._cmd_help,
            "/status":   lambda a: self._send_status(),
            "/today":    lambda a: self._send_today(),
            "/users":    self._cmd_users,
            "/find":     self._cmd_find,
            "/bookings": lambda a: self._send_bookings_menu(),
            "/schedule": lambda a: self._send_schedule(),
            "/loans":    lambda a: self._send_loans(),
            "/weather":  lambda a: self._send_weather(),
            "/stock":    lambda a: self._send_stock_menu(),
            "/logs":     self._cmd_logs,
            "/broadcast":self._cmd_broadcast,
            "/alert":    self._cmd_alert_toggle,
            "/kick":     self._cmd_kick,
            "/setadmin": self._cmd_setadmin,
            "/uptime":   self._cmd_uptime,
            "/clearlog": self._cmd_clearlog,
        }.get(cmd)
        if handler:
            handler(args)
        else:
            self._api.send(_cid, f"❓ ไม่รู้จัก <code>{cmd}</code>\nพิมพ์ /help ดูคำสั่งทั้งหมด")

    def on_callback(self, msg_id: int, cb_id: str, data: str):
        self._api.answer(cb_id)
        parts  = data.split(":", 2)
        action = parts[0]
        arg1   = parts[1] if len(parts) > 1 else ""
        arg2   = parts[2] if len(parts) > 2 else ""

        dispatch = {
            "menu":           lambda: self._handle_menu(arg1, msg_id),
            "status":         lambda: self._edit_status(msg_id),
            "today":          lambda: self._edit_today(msg_id),
            "users_page":     lambda: self._edit_users(msg_id, int(arg1)),
            "user_detail":    lambda: self._edit_user_detail(msg_id, arg1),
            "ban":            lambda: self._confirm_ban(msg_id, arg1, "ban"),
            "unban":          lambda: self._confirm_ban(msg_id, arg1, "unban"),
            "setadmin":       lambda: self._confirm_setadmin(msg_id, arg1),
            "kick":           lambda: self._confirm_kick(msg_id, arg1),
            "bks_date":       lambda: self._edit_bookings(msg_id, arg1, arg2, "all"),
            "bks_court":      lambda: self._edit_bookings_court(msg_id, arg1, arg2),
            "bks_page":       lambda: self._edit_bookings_page(msg_id, arg2, int(arg1)),
            "bk_detail":      lambda: self._edit_bk_detail(msg_id, arg1),
            "force_cancel":   lambda: self._confirm_action(msg_id, "force_cancel", {"bid": arg1}),
            "loans_page":     lambda: self._edit_loans(msg_id, arg1, int(arg2)),
            "loans_court":    lambda: self._edit_loans_court(msg_id, arg1),
            "loan_detail":    lambda: self._edit_loan_detail(msg_id, arg1),
            "force_return":   lambda: self._confirm_action(msg_id, "force_return", {"lid": arg1}),
            "stock_court":    lambda: self._edit_stock(msg_id, arg1),
            "confirm":        lambda: self._handle_confirm(msg_id, arg1),
            "alert_toggle":   lambda: self._toggle_alert(msg_id),
            "schedule":       lambda: self._edit_schedule(msg_id, arg1),
            "stats_court":    lambda: self._edit_stats_court(msg_id),
        }
        fn = dispatch.get(action)
        if fn:
            try: fn()
            except Exception as e:
                log.error("AdminBot callback %s: %s", data, e)
                self._api.send(_cid, f"❌ Error: {e}")

    # ══════════════════════════════════════════════
    # MAIN MENU
    # ══════════════════════════════════════════════

    def _main_menu(self, _=None):
        alert_icon = "🔔" if _alerts_on else "🔕"
        online_n   = len(self._clients)
        uptime_s   = int(time.time() - _server_start_time)
        h, m       = uptime_s // 3600, (uptime_s % 3600) // 60
        self._api.send(_cid,
            f"🤖 <b>Sports Queue Admin Bot</b>\n"
            f"Server: {self._ctx.get('host','?')}:{self._ctx.get('port','?')}  ⏱ {h}h {m}m\n"
            f"Alert: {alert_icon}  Online: <b>{online_n}</b> คน\n"
            f"─────────────────────────\n\n"
            f"<b>ข้อมูล:</b>\n"
            f"/status — สถิติระบบ\n"
            f"/today — สรุปวันนี้\n"
            f"/schedule — ตารางการจอง\n"
            f"/weather — สภาพอากาศ\n"
            f"/uptime — เวลา server ทำงาน\n\n"
            f"<b>จัดการผู้ใช้:</b>\n"
            f"/users — รายชื่อผู้ใช้\n"
            f"/find &lt;query&gt; — ค้นหาผู้ใช้\n"
            f"/kick &lt;user&gt; — Disconnect ผู้ใช้\n"
            f"/setadmin &lt;user&gt; — Toggle Admin\n\n"
            f"<b>การจอง &amp; อุปกรณ์:</b>\n"
            f"/bookings — ดูการจอง\n"
            f"/loans — รายการยืมอุปกรณ์\n"
            f"/stock — Stock อุปกรณ์\n\n"
            f"<b>อื่นๆ:</b>\n"
            f"/broadcast &lt;msg&gt; — ส่งข้อความ\n"
            f"/logs [n] — ดู log (default 20)\n"
            f"/clearlog — ล้าง log file\n"
            f"/alert — Toggle push alerts"
        )

    def _handle_menu(self, target: str, msg_id: int = None):
        handlers = {
            "main":      lambda: self._api.send(_cid, "พิมพ์ /menu หรือ /help ดูคำสั่งทั้งหมด"),
            "stats":     self._send_status,
            "users":     lambda: self._send_users(0),
            "bks":       self._send_bookings_menu,
            "loans":     lambda: self._send_loans(),
            "stock":     self._send_stock_menu,
            "weather":   self._send_weather,
            "logs":      lambda: self._send_logs(20),
            "broadcast": self._send_broadcast_prompt,
            "schedule":  self._send_schedule,
        }
        fn = handlers.get(target)
        if fn: fn()

    # ══════════════════════════════════════════════
    # STATS (สถิติ)
    # ══════════════════════════════════════════════

    def _stats_text(self) -> str:
        today = date.today().strftime("%Y-%m-%d")
        confirmed = sum(1 for b in self._bks if b["date"]==today and b["status"]=="confirmed")
        cancelled = sum(1 for b in self._bks if b["status"]=="cancelled")
        active_ln = sum(1 for l in self._loans if l["status"]=="active")
        online    = list(self._clients.keys())
        # court breakdown วันนี้
        court_lines = []
        for c in COURT_TYPES:
            n = sum(1 for b in self._bks if b["date"]==today
                    and b["status"]=="confirmed" and b["court_type"]==c)
            if n:
                court_lines.append(f"  {COURT_EMOJIS[c]} {c}: {n}")

        text = (
            f"📊 <b>สถิติ Server</b>{sep()}"
            f"👥 ผู้ใช้ทั้งหมด:   <b>{len(self._db)}</b>\n"
            f"🟢 Online:           <b>{len(online)}</b>"
            f"  {('→ ' + ', '.join(online[:5]) + ('...' if len(online)>5 else '')) if online else ''}\n"
            f"📅 จองวันนี้:         <b>{confirmed}</b>\n"
        )
        if court_lines:
            text += "\n".join(court_lines) + "\n"
        text += (
            f"❌ ยกเลิกสะสม:      <b>{cancelled}</b>\n"
            f"📦 ยืม active:       <b>{active_ln}</b>\n"
            f"─────────────────────────\n🕐 {now_full()}"
        )
        return text

    def _send_status(self):
        self._api.send(_cid, self._stats_text(),
                       markup=kb(
                           [("🔄 รีเฟรช", "status"), ("📊 แยกสนาม", "stats_court")],
                           [btn_back_main()],
                       ))

    def _edit_status(self, mid: int):
        self._api.edit(_cid, mid, self._stats_text(),
                       markup=kb(
                           [("🔄 รีเฟรช", "status"), ("📊 แยกสนาม", "stats_court")],
                           [btn_back_main()],
                       ))

    def _edit_stats_court(self, mid: int):
        """สถิติแยกรายสนาม"""
        today = date.today().strftime("%Y-%m-%d")
        lines = [f"📊 <b>สถิติแยกรายสนาม</b>  ({today}){sep()}"]
        for c in COURT_TYPES:
            bks_today   = sum(1 for b in self._bks if b["date"]==today and b["status"]=="confirmed" and b["court_type"]==c)
            bks_total   = sum(1 for b in self._bks if b["status"]=="confirmed" and b["court_type"]==c)
            loans_active = sum(1 for l in self._loans if l["status"]=="active" and l["court_type"]==c)
            lines.append(f"{COURT_EMOJIS[c]} <b>{c}</b>\n"
                         f"  📅 วันนี้: {bks_today}  |  รวม: {bks_total}  |  ยืม: {loans_active}")
        self._api.edit(_cid, mid, "\n".join(lines),
                       markup=kb([("🔄 รีเฟรช", "stats_court"), btn_back_main()]))

    # ══════════════════════════════════════════════
    # TODAY — สรุปวันนี้ฉับไว
    # ══════════════════════════════════════════════

    def _today_text(self) -> str:
        today = date.today().strftime("%Y-%m-%d")
        bks   = [b for b in self._bks if b["date"]==today and b["status"]=="confirmed"]
        bks.sort(key=lambda x: x["time_slot"])
        online = list(self._clients.keys())
        lines  = [
            f"🗓 <b>สรุปวันนี้  {today}</b>{sep()}",
            f"🟢 Online:  <b>{len(online)}</b>"
            + (f"  → {', '.join(online[:6])}" if online else ""),
            f"📅 จองทั้งหมด:  <b>{len(bks)}</b>\n",
        ]
        for c in COURT_TYPES:
            c_bks = [b for b in bks if b["court_type"]==c]
            if c_bks:
                lines.append(f"{COURT_EMOJIS[c]} <b>{c}</b>")
                for b in c_bks:
                    lines.append(f"  ⏰ {b['time_slot']}  👤 {b['user_name']}")
        lines.append(f"\n─────────────────────────\n🕐 {now_full()}")
        return "\n".join(lines)

    def _send_today(self):
        self._api.send(_cid, self._today_text(),
                       markup=kb([("🔄 รีเฟรช", "today"), btn_back_main()]))

    def _edit_today(self, mid: int):
        self._api.edit(_cid, mid, self._today_text(),
                       markup=kb([("🔄 รีเฟรช", "today"), btn_back_main()]))

    # ══════════════════════════════════════════════
    # SCHEDULE — ตารางจัดกลุ่มตามสนาม
    # ══════════════════════════════════════════════

    def _schedule_text(self, sel_date: str) -> str:
        bks = [b for b in self._bks if b["date"]==sel_date and b["status"]=="confirmed"]
        bks.sort(key=lambda x: x["time_slot"])
        lines = [f"📋 <b>ตาราง  {sel_date}</b>{sep()}"]
        for c in COURT_TYPES:
            c_bks = [b for b in bks if b["court_type"]==c]
            lines.append(f"{COURT_EMOJIS[c]} <b>{c}</b>")
            if c_bks:
                for b in c_bks:
                    lines.append(f"  ✅ {b['time_slot']}  👤 {b['user_name']}")
            else:
                lines.append("  ─ ว่างทุกช่วง")
        lines.append(f"\n🕐 {now_s()}")
        return "\n".join(lines)

    def _send_schedule(self):
        today = date.today()
        dates = [
            (f"{'วันนี้' if i==0 else 'พรุ่งนี้' if i==1 else (today+timedelta(i)).strftime('%d/%m')}",
             f"schedule:{(today+timedelta(i)).strftime('%Y-%m-%d')}")
            for i in range(MAX_ADVANCE_DAYS_PLUS)
        ]
        self._api.send(_cid, "📋 <b>ตารางการจอง</b>\nเลือกวันที่:",
                       markup=kb(*[[d] for d in dates], [btn_back_main()]))

    def _edit_schedule(self, mid: int, sel_date: str):
        self._api.edit(_cid, mid, self._schedule_text(sel_date),
                       markup=kb([("🔄 รีเฟรช", f"schedule:{sel_date}"),
                                  btn_back("schedule")]))

    # ══════════════════════════════════════════════
    # USERS
    # ══════════════════════════════════════════════

    def _users_page_data(self, page: int, search: str = ""):
        users = sorted(self._db.items(),
                       key=lambda x: (not (x[0] in self._clients), x[0]))
        if search:
            s = search.lower()
            users = [(k, v) for k, v in users
                     if s in k.lower() or s in v.get("name","").lower()
                     or s in (v.get("email") or "").lower()]
        return users

    def _build_users_payload(self, page: int, search: str = "") -> tuple[str, list]:
        """สร้าง text + keyboard สำหรับหน้า users (ใช้ร่วมกันระหว่าง send และ edit)"""
        users = self._users_page_data(page, search)
        total = len(users)
        start = page * PAGE_SIZE
        chunk = users[start: start + PAGE_SIZE]

        title = f"👥 <b>ผู้ใช้ ({total})</b>"
        if search: title += f"  🔍 <i>{search}</i>"
        title += f"  หน้า {page+1}/{max(1,-(-total//PAGE_SIZE))}\n"
        lines = [title]
        btns  = []
        for uname, u in chunk:
            s    = "🟢" if uname in self._clients else ("⛔" if u.get("is_banned") else "⚪")
            r    = "👑" if u.get("is_admin") else ""
            n_bk = sum(1 for b in self._bks if b["username"]==uname and b["status"]=="confirmed")
            lines.append(f"{s} <code>{uname}</code> {r} {u['name']}  📅{n_bk}")
            btns.append((f"{s} {uname}", f"user_detail:{uname}"))

        rows    = [btns[i:i+2] for i in range(0, len(btns), 2)]
        nav_row = []
        if page > 0:                nav_row.append(("◀️", f"users_page:{page-1}"))
        if start+PAGE_SIZE < total: nav_row.append(("▶️", f"users_page:{page+1}"))
        if nav_row: rows.append(nav_row)
        rows.append([btn_back_main()])
        return "\n".join(lines), _make_kb(rows)

    def _send_users(self, page: int = 0, search: str = ""):
        text, markup = self._build_users_payload(page, search)
        self._api.send(_cid, text, markup=markup)

    def _edit_users(self, mid: int, page: int, search: str = ""):
        text, markup = self._build_users_payload(page, search)
        self._api.edit(_cid, mid, text, markup=markup)

    def _edit_user_detail(self, mid: int, uname: str):
        u = self._db.get(uname)
        if not u: self._api.edit(_cid, mid, "❌ ไม่พบผู้ใช้"); return

        bks  = [b for b in self._bks  if b["username"]==uname]
        lns  = [l for l in self._loans if l["username"]==uname]
        conf = sum(1 for b in bks  if b["status"]=="confirmed")
        actv = sum(1 for l in lns  if l["status"]=="active")
        is_online = uname in self._clients
        status = "🟢 Online" if is_online else ("⛔ Banned" if u.get("is_banned") else "⚪ Offline")

        text = (
            f"👤 <b>รายละเอียดผู้ใช้</b>{sep()}"
            f"Username:  <code>{uname}</code>\n"
            f"ชื่อ:       {u['name']}\n"
            f"Email:     {u.get('email') or '-'}\n"
            f"Role:      {'👑 Admin' if u.get('is_admin') else 'User'}\n"
            f"สถานะ:     {status}\n"
            f"─────────────────────────\n"
            f"📅 จอง active:  {conf}  (ทั้งหมด {len(bks)})\n"
            f"📦 ยืม active:  {actv}  (ทั้งหมด {len(lns)})"
        )
        action_btns = []
        if u.get("is_banned"):
            action_btns.append(("✅ Unban", f"unban:{uname}"))
        else:
            action_btns.append(("⛔ Ban", f"ban:{uname}"))
        if is_online:
            action_btns.append(("🦵 Kick", f"kick:{uname}"))
        admin_label = "⬇️ ถอด Admin" if u.get("is_admin") else "👑 แต่งตั้ง Admin"
        self._api.edit(_cid, mid, text, markup=kb(
            action_btns,
            [(admin_label, f"setadmin:{uname}")],
            [btn_back("users")],
        ))

    def _confirm_ban(self, mid: int, uname: str, action: str):
        label = "ระงับ" if action == "ban" else "ปลดระงับ"
        self._pending[mid] = {"action": action, "payload": {"username": uname}}
        self._api.edit(_cid, mid,
            f"⚠️ ยืนยัน{label}บัญชี <code>{uname}</code>?",
            markup=kb(
                [(f"✅ ยืนยัน{label}", "confirm:ok")],
                [("❌ ยกเลิก",         "confirm:abort")],
            )
        )

    def _confirm_kick(self, mid: int, uname: str):
        self._pending[mid] = {"action": "kick", "payload": {"username": uname}}
        self._api.edit(_cid, mid,
            f"⚠️ Kick <code>{uname}</code> ออกจากระบบ?",
            markup=kb([("✅ ยืนยัน Kick", "confirm:ok")],
                      [("❌ ยกเลิก",      "confirm:abort")])
        )

    def _confirm_setadmin(self, mid: int, uname: str):
        u = self._db.get(uname)
        if not u: self._api.edit(_cid, mid, "❌ ไม่พบผู้ใช้"); return
        action = "removeadmin" if u.get("is_admin") else "makeadmin"
        label  = "ถอดสิทธิ์ Admin" if u.get("is_admin") else "แต่งตั้งเป็น Admin"
        self._pending[mid] = {"action": action, "payload": {"username": uname}}
        self._api.edit(_cid, mid,
            f"⚠️ {label}  <code>{uname}</code>?",
            markup=kb([(f"✅ ยืนยัน", "confirm:ok")],
                      [("❌ ยกเลิก", "confirm:abort")])
        )

    # ══════════════════════════════════════════════
    # BOOKINGS
    # ══════════════════════════════════════════════

    def _send_bookings_menu(self):
        today = date.today()
        dates = [
            (f"{'วันนี้' if i==0 else 'พรุ่งนี้' if i==1 else (today+timedelta(i)).strftime('%d/%m')}"
             f" ({(today+timedelta(i)).strftime('%a')})",
             f"bks_date:confirmed:{(today+timedelta(i)).strftime('%Y-%m-%d')}")
            for i in range(MAX_ADVANCE_DAYS_PLUS)
        ]
        court_btns = [(f"{COURT_EMOJIS[c]} {c}", f"bks_court:confirmed:{c}")
                      for c in COURT_TYPES]
        self._api.send(_cid, "📅 <b>ดูการจอง</b>",
            markup=kb(
                *[[d] for d in dates],
                [("📋 ทั้งหมด (all)",     "bks_date:all:all")],
                *[court_btns[i:i+2] for i in range(0, len(court_btns), 2)],
                [btn_back_main()],
            )
        )

    def _edit_bookings(self, mid: int, status_filter: str, date_filter: str,
                       court_filter: str = "all"):
        bks = [b for b in self._bks
               if (status_filter == "all" or b["status"] == status_filter)
               and (date_filter == "all"  or b["date"]   == date_filter)
               and (court_filter == "all" or b["court_type"] == court_filter)]
        bks.sort(key=lambda x: (x["date"], x["time_slot"]))
        fkey = f"{status_filter}:{date_filter}:{court_filter}"
        self._edit_bookings_page(mid, fkey, 0, bks)

    def _edit_bookings_court(self, mid: int, status_filter: str, court: str):
        self._edit_bookings(mid, status_filter, "all", court)

    def _edit_bookings_page(self, mid: int, filter_key: str,
                             page: int, bks: list = None):
        if bks is None:
            parts_ = filter_key.split(":", 2)
            sf = parts_[0]; df = parts_[1]; cf = parts_[2] if len(parts_) > 2 else "all"
            bks = [b for b in self._bks
                   if (sf=="all" or b["status"]==sf)
                   and (df=="all" or b["date"]==df)
                   and (cf=="all" or b["court_type"]==cf)]
            bks.sort(key=lambda x: (x["date"], x["time_slot"]))

        total  = len(bks)
        start  = page * PAGE_SIZE
        chunk  = bks[start: start + PAGE_SIZE]
        status_icons = {"confirmed":"✅","cancelled":"❌"}

        lines = [f"📅 <b>การจอง ({total} รายการ)</b>  หน้า {page+1}/{max(1,-(-total//PAGE_SIZE))}\n"]
        btns  = []
        for b in chunk:
            ic = status_icons.get(b["status"],"❓")
            lines.append(f"{ic} {b['court_type']}  {b['date']}  {b['time_slot']}\n"
                         f"   👤{b['user_name']}  <code>{b['booking_id']}</code>")
            btns.append((f"{ic} {b['booking_id']}", f"bk_detail:{b['booking_id']}"))

        rows    = [btns[i:i+1] for i in range(len(btns))]
        nav_row = []
        if page > 0:                nav_row.append(("◀️", f"bks_page:{page-1}:{filter_key}"))
        if start+PAGE_SIZE < total: nav_row.append(("▶️", f"bks_page:{page+1}:{filter_key}"))
        if nav_row: rows.append(nav_row)
        rows.append([btn_back("bks")])
        self._api.edit(_cid, mid, "\n".join(lines), markup=_make_kb(rows))

    def _edit_bk_detail(self, mid: int, bid: str):
        bk = next((b for b in self._bks if b["booking_id"]==bid), None)
        if not bk: self._api.edit(_cid, mid, "❌ ไม่พบการจอง"); return
        status_label = {"confirmed":"✅ confirmed","cancelled":"❌ cancelled"}.get(bk["status"], bk["status"])
        text = (
            f"📅 <b>รายละเอียดการจอง</b>{sep()}"
            f"🆔 <code>{bk['booking_id']}</code>\n"
            f"👤 {bk['user_name']}  <code>{bk['username']}</code>\n"
            f"{COURT_EMOJIS.get(bk['court_type'],'🏟')} {bk['court_type']}  ·  {bk['date']}  ·  {bk['time_slot']}\n"
            f"สถานะ:    {status_label}\n"
            f"จองเมื่อ:  {bk.get('booked_at','-')}"
        )
        rows = []
        if bk["status"] == "confirmed":
            rows.append([("🚫 Force Cancel", f"force_cancel:{bid}")])
        rows.append([btn_back("bks")])
        self._api.edit(_cid, mid, text, markup=_make_kb(rows))

    # ══════════════════════════════════════════════
    # LOANS
    # ══════════════════════════════════════════════

    def _send_loans(self, status: str = "active"):
        # court filter row
        court_btns = [(f"{COURT_EMOJIS[c]}", f"loans_court:{c}") for c in COURT_TYPES]
        loans = [l for l in self._loans if status == "all" or l["status"] == status]
        loans.sort(key=lambda x: x["borrowed_at"], reverse=True)
        total = len(loans)
        chunk = loans[:PAGE_SIZE]

        lines = [f"📦 <b>ยืมอุปกรณ์</b>  {status}  ({total} รายการ)\n"]
        btns  = []
        for l in chunk:
            ic = "🟡" if l["status"]=="active" else "✅"
            lines.append(f"{ic} {l['username']}  {', '.join(l['item_names'][:2])}\n"
                         f"   {l['date']}  <code>{l['loan_id']}</code>")
            btns.append((f"{ic} {l['loan_id']}", f"loan_detail:{l['loan_id']}"))

        rows    = [btns[i:i+1] for i in range(len(btns))]
        nav_row = []
        if total > PAGE_SIZE: nav_row.append(("▶️ ดูเพิ่ม", "loans_page:all:1"))
        if status == "active": nav_row.append(("📋 ดูทั้งหมด", "loans_page:all:0"))
        if nav_row: rows.append(nav_row)
        rows.append([court_btns[i] for i in range(len(court_btns))])
        rows.append([btn_back_main()])
        self._api.send(_cid, "\n".join(lines), markup=_make_kb(rows))

    def _edit_loans(self, mid: int, status: str, page: int):
        loans = [l for l in self._loans if status=="all" or l["status"]==status]
        loans.sort(key=lambda x: x["borrowed_at"], reverse=True)
        total = len(loans)
        start = page * PAGE_SIZE
        chunk = loans[start: start + PAGE_SIZE]

        lines = [f"📦 <b>ยืมอุปกรณ์ ({total})</b>  หน้า {page+1}\n"]
        btns  = []
        for l in chunk:
            ic = "🟡" if l["status"]=="active" else "✅"
            lines.append(f"{ic} {l['username']}  {', '.join(l['item_names'][:2])}\n"
                         f"   {l['date']}  <code>{l['loan_id']}</code>")
            btns.append((f"{ic} {l['loan_id']}", f"loan_detail:{l['loan_id']}"))

        rows    = [btns[i:i+1] for i in range(len(btns))]
        nav_row = []
        if page > 0:                nav_row.append(("◀️", f"loans_page:{status}:{page-1}"))
        if start+PAGE_SIZE < total: nav_row.append(("▶️", f"loans_page:{status}:{page+1}"))
        if nav_row: rows.append(nav_row)
        rows.append([btn_back_main()])
        self._api.edit(_cid, mid, "\n".join(lines), markup=_make_kb(rows))

    def _edit_loans_court(self, mid: int, court: str):
        """ยืมอุปกรณ์กรองตามสนาม"""
        loans = [l for l in self._loans if l["court_type"]==court]
        loans.sort(key=lambda x: x["borrowed_at"], reverse=True)
        total = len(loans)
        lines = [f"📦 <b>ยืม — {COURT_EMOJIS.get(court,'')} {court}</b>  ({total} รายการ)\n"]
        btns  = []
        for l in loans[:PAGE_SIZE]:
            ic = "🟡" if l["status"]=="active" else "✅"
            lines.append(f"{ic} {l['username']}  {', '.join(l['item_names'][:2])}\n"
                         f"   {l['date']}  <code>{l['loan_id']}</code>")
            btns.append((f"{ic} {l['loan_id']}", f"loan_detail:{l['loan_id']}"))
        rows = [btns[i:i+1] for i in range(len(btns))]
        rows.append([btn_back_main()])
        self._api.edit(_cid, mid, "\n".join(lines), markup=_make_kb(rows))

    def _edit_loan_detail(self, mid: int, lid: str):
        ln = next((l for l in self._loans if l["loan_id"]==lid), None)
        if not ln: self._api.edit(_cid, mid, "❌ ไม่พบรายการ"); return
        text = (
            f"📦 <b>รายละเอียดการยืม</b>{sep()}"
            f"🆔 <code>{ln['loan_id']}</code>\n"
            f"👤 {ln['username']}  ·  {COURT_EMOJIS.get(ln['court_type'],'')} {ln['court_type']}\n"
            f"📆 {ln['date']}  ⏰ {ln['time_slot']}\n"
            f"อุปกรณ์:  {', '.join(ln['item_names'])}\n"
            f"สถานะ:  {'🟡 active' if ln['status']=='active' else '✅ returned'}\n"
            f"ยืมเมื่อ:  {ln['borrowed_at']}\n"
            f"คืนเมื่อ:  {ln.get('returned_at') or '-'}"
        )
        rows = []
        if ln["status"] == "active":
            rows.append([("🔄 Force Return", f"force_return:{lid}")])
        rows.append([btn_back("loans")])
        self._api.edit(_cid, mid, text, markup=_make_kb(rows))

    # ══════════════════════════════════════════════
    # EQUIPMENT STOCK
    # ══════════════════════════════════════════════

    def _send_stock_menu(self):
        items = [(f"{COURT_EMOJIS[c]} {c}", f"stock_court:{c}") for c in COURT_TYPES]
        self._api.send(_cid, "📦 <b>Stock อุปกรณ์</b>\nเลือกสนาม:",
                       markup=kb_grid(items, 2))

    def _edit_stock(self, mid: int, court: str):
        stock_fn = self._ctx.get("get_stock")
        items    = stock_fn(court) if stock_fn else EQUIPMENT.get(court, [])
        lines    = [f"📦 <b>Stock  {COURT_EMOJIS.get(court,'')} {court}</b>{sep()}"]
        for eq in items:
            avail = eq.get("available", eq.get("stock", 0))
            total = eq.get("stock", 0)
            bar   = "🟩" * avail + "⬜" * (total - avail)
            pct   = int(avail/total*100) if total else 0
            lines.append(f"{eq['emoji']} {eq['name']}\n   {bar}  {avail}/{total}  ({pct}%)")
        self._api.edit(_cid, mid, "\n".join(lines),
                       markup=kb([btn_back("stock")]))

    # ══════════════════════════════════════════════
    # WEATHER
    # ══════════════════════════════════════════════

    def _send_weather(self):
        w = self._ctx["fetch_weather"]()
        self._api.send(_cid,
            f"🌤 <b>สภาพอากาศ มทส</b>{sep()}"
            f"{w['description']}\n\n"
            f"🌡 {w['temperature']:.1f}°C   🤔 รู้สึก {w.get('feels_like','-')}°C\n"
            f"💧 {w['humidity']:.0f}%   💨 {w['wind_speed']:.1f} m/s\n"
            f"🌧 ฝน {w['rain_prob']:.0f}%   ☀️ UV {w['uv_index']:.1f}\n"
            f"─────────────────────────\n"
            f"📡 {w['source']}  ·  {w['updated_at']}",
            markup=kb([("🔄 รีเฟรช", "menu:weather"), btn_back_main()])
        )

    # ══════════════════════════════════════════════
    # LOGS
    # ══════════════════════════════════════════════

    def _send_logs(self, n: int = 20):
        if not os.path.exists(LOG_FILE):
            self._api.send(_cid, "📄 ยังไม่มี log file"); return
        with open(LOG_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        tail = "".join(lines[-n:]).strip()
        size_kb = os.path.getsize(LOG_FILE) // 1024
        self._api.send(_cid,
            f"📄 <b>Log ล่าสุด {n} บรรทัด</b>  (ไฟล์ {size_kb} KB)\n\n"
            f"<pre>{tail[-3500:]}</pre>",
            markup=kb(
                [("🔄 รีเฟรช", "menu:logs"),  ("📄 50 บรรทัด", "menu:logs50")],
                [("🗑 ล้าง Log", "confirm:clearlog"), btn_back_main()],
            )
        )

    # ══════════════════════════════════════════════
    # BROADCAST
    # ══════════════════════════════════════════════

    def _send_broadcast_prompt(self):
        self._api.send(_cid,
            "📢 <b>Broadcast</b>\n\nพิมพ์คำสั่งในรูปแบบ:\n"
            "<code>/broadcast ข้อความที่ต้องการส่ง</code>\n\n"
            f"📡 จะส่งถึงผู้ใช้ที่ออนไลน์ <b>{len(self._clients)}</b> คน")

    # ══════════════════════════════════════════════
    # ALERT TOGGLE
    # ══════════════════════════════════════════════

    def _toggle_alert(self, mid: int):
        if _alerts_on:
            alerts_off()
            new_status = "🔕 Alert ปิดแล้ว"
        else:
            alerts_on()
            new_status = "🔔 Alert เปิดแล้ว"
        self._api.edit(_cid, mid, new_status,
                       markup=kb([btn_back_main()]))

    def _cmd_alert_toggle(self, _):
        self._api.send(_cid,
            f"Alert ขณะนี้: {'เปิด 🔔' if _alerts_on else 'ปิด 🔕'}",
            markup=kb([
                (("🔕 ปิด Alert" if _alerts_on else "🔔 เปิด Alert"), "alert_toggle"),
                btn_back_main(),
            ])
        )

    # ══════════════════════════════════════════════
    # CONFIRM FLOW
    # ══════════════════════════════════════════════

    def _confirm_action(self, mid: int, action: str, payload: dict):
        labels = {
            "force_cancel": f"🚫 Force Cancel การจอง <code>{payload.get('bid')}</code>",
            "force_return": f"🔄 Force Return อุปกรณ์ <code>{payload.get('lid')}</code>",
        }
        self._pending[mid] = {"action": action, "payload": payload}
        self._api.edit(_cid, mid,
            f"⚠️ ยืนยัน?\n{labels.get(action, action)}",
            markup=kb(
                [("✅ ยืนยัน", "confirm:ok")],
                [("❌ ยกเลิก", "confirm:abort")],
            )
        )

    def _handle_confirm(self, mid: int, result: str):
        # special: clearlog ไม่ต้อง pending
        if result == "clearlog":
            self._do_clearlog(mid); return

        if result == "abort" or mid not in self._pending:
            self._api.edit(_cid, mid, "↩️ ยกเลิกแล้ว")
            self._pending.pop(mid, None); return

        p      = self._pending.pop(mid)
        action = p["action"]
        pl     = p["payload"]

        if action == "force_cancel":
            bid = pl["bid"]
            with self._ctx["db_lock"]:
                for bk in self._bks:
                    if bk["booking_id"] == bid and bk["status"] == "confirmed":
                        bk["status"] = "cancelled"; self._ctx["save_db"]()
                        log.info("[BotAdmin] force cancel %s", bid)
                        self._ctx["broadcast"]({"cmd":"BROADCAST","type":"schedule_update",
                                                 "msg":f"🔔 [Admin] ยกเลิก {bid}"})
                        self._api.edit(_cid, mid, f"✅ Force cancel <code>{bid}</code> สำเร็จ")
                        return
            self._api.edit(_cid, mid, "❌ ไม่พบการจอง")

        elif action == "force_return":
            lid = pl["lid"]
            with self._ctx["db_lock"]:
                for ln in self._loans:
                    if ln["loan_id"] == lid and ln["status"] == "active":
                        ln["status"] = "returned"; ln["returned_at"] = now_full()
                        self._ctx["save_db"]()
                        log.info("[BotAdmin] force return %s", lid)
                        self._api.edit(_cid, mid, f"✅ Force return <code>{lid}</code> สำเร็จ")
                        return
            self._api.edit(_cid, mid, "❌ ไม่พบรายการยืม")

        elif action in ("ban", "unban"):
            uname = pl["username"]
            self._ctx["users_db"][uname]["is_banned"] = (action == "ban")
            self._ctx["save_db"]()
            log.info("[BotAdmin] %s %s", action, uname)
            label = "ระงับ" if action == "ban" else "ปลดระงับ"
            if action == "ban" and uname in self._ctx["clients"]:
                _try_send = self._ctx.get("try_send")
                if _try_send:
                    _try_send(self._ctx["clients"][uname],
                              {"cmd":"BROADCAST","type":"admin_msg","msg":"⛔ บัญชีถูกระงับ"})
                self._ctx["clients"].pop(uname, None)
            alert_ban(uname, action)
            self._api.edit(_cid, mid, f"✅ {label}บัญชี <code>{uname}</code> แล้ว")

        elif action == "kick":
            uname = pl["username"]
            clients = self._ctx["clients"]
            if uname in clients:
                sock = clients.pop(uname, None)
                if sock:
                    _try_send = self._ctx.get("try_send")
                    if _try_send:
                        _try_send(sock, {"cmd": "BROADCAST", "type": "force_logout",
                                         "msg": "🦵 ถูก Kick ออกจากระบบโดย Admin"})
                    try:
                        sock.shutdown(2)   # SHUT_RDWR — บังคับตัด TCP ทันที
                        sock.close()
                    except Exception:
                        pass
                log.info("[BotAdmin] kick %s", uname)
                alert_kick(uname)
                self._api.edit(_cid, mid, f"✅ Kick <code>{uname}</code> และปิด socket แล้ว")
            else:
                self._api.edit(_cid, mid, f"⚠️ <code>{uname}</code> ไม่ได้ Online")

        elif action in ("makeadmin", "removeadmin"):
            uname = pl["username"]
            self._ctx["users_db"][uname]["is_admin"] = (action == "makeadmin")
            self._ctx["save_db"]()
            label = "แต่งตั้งเป็น Admin" if action == "makeadmin" else "ถอดสิทธิ์ Admin"
            log.info("[BotAdmin] %s %s", action, uname)
            self._api.edit(_cid, mid, f"✅ {label}  <code>{uname}</code> สำเร็จ")

    # ══════════════════════════════════════════════
    # TEXT COMMANDS (พิมพ์ได้โดยตรง)
    # ══════════════════════════════════════════════

    def _cmd_users(self, _): self._send_users(0)

    def _cmd_find(self, args):
        """🆕 /find <query> — ค้นหาผู้ใช้ตามชื่อหรือ username"""
        query = " ".join(args).strip()
        if not query:
            self._api.send(_cid, "🔍 ใช้งาน: <code>/find ชื่อหรือusername</code>"); return
        self._send_users(0, search=query)

    def _cmd_logs(self, args):
        n = 20
        if args:
            try: n = min(int(args[0]), 100)
            except ValueError: pass
        self._send_logs(n)

    def _cmd_broadcast(self, args):
        msg = " ".join(args).strip()
        if not msg:
            self._send_broadcast_prompt(); return

        # ── 1. socket push ให้ผู้ใช้ที่ online ────────
        self._ctx["broadcast"]({"cmd": "BROADCAST", "type": "admin_msg",
                                 "msg": f"📢 [Admin] {msg}"})
        log.info("[BotAdmin] broadcast: %s", msg)

        # ── 2. email ถึงทุก user ที่มีอีเมลลงทะเบียนไว้ ──
        email_bg = self._ctx.get("email_bg")
        email_count = 0
        if email_bg:
            html = (
                f"<div style='font-family:sans-serif;padding:24px'>"
                f"<h2 style='color:#2563eb'>📢 ข้อความจากผู้ดูแลระบบ</h2>"
                f"<p style='font-size:16px;line-height:1.6'>{msg}</p>"
                f"<hr style='margin:20px 0'>"
                f"<p style='color:#888;font-size:12px'>Sports Queue · {now_full()}</p>"
                f"</div>"
            )
            for uname, u in self._db.items():
                to = (u.get("email") or "").strip()
                if to and "@" in to:
                    email_bg(to, "📢 ข้อความจากผู้ดูแลระบบ — Sports Queue", html)
                    email_count += 1

        summary = f"📡 Online {len(self._clients)} คน"
        if email_count:
            summary += f"\n📧 ส่งอีเมล {email_count} บัญชีแล้ว"
        else:
            summary += "\n⚠️ ไม่มีอีเมล config หรือไม่มี user ที่ลงทะเบียนอีเมล"
        self._api.send(_cid, f"✅ Broadcast สำเร็จ\n{summary}\n\n📢 <i>{msg}</i>")

    def _cmd_kick(self, args):
        """🆕 /kick <username> — Disconnect ผู้ใช้ออกทันที"""
        if not args:
            self._api.send(_cid, "🦵 ใช้งาน: <code>/kick username</code>"); return
        uname = args[0]
        mid   = self._api.send(_cid, f"⚠️ Kick <code>{uname}</code>?",
                               markup=kb([("✅ ยืนยัน Kick", "confirm:ok")],
                                         [("❌ ยกเลิก",      "confirm:abort")]))
        if mid:
            self._pending[mid] = {"action": "kick", "payload": {"username": uname}}

    def _cmd_setadmin(self, args):
        """🆕 /setadmin <username> — Toggle สิทธิ์ Admin"""
        if not args:
            self._api.send(_cid, "👑 ใช้งาน: <code>/setadmin username</code>"); return
        uname = args[0]
        u = self._db.get(uname)
        if not u:
            self._api.send(_cid, f"❌ ไม่พบผู้ใช้ <code>{uname}</code>"); return
        action = "removeadmin" if u.get("is_admin") else "makeadmin"
        label  = "ถอดสิทธิ์ Admin" if u.get("is_admin") else "แต่งตั้งเป็น Admin"
        mid    = self._api.send(_cid, f"⚠️ {label}  <code>{uname}</code>?",
                               markup=kb([("✅ ยืนยัน", "confirm:ok")],
                                         [("❌ ยกเลิก", "confirm:abort")]))
        if mid:
            self._pending[mid] = {"action": action, "payload": {"username": uname}}

    def _cmd_uptime(self, _):
        """🆕 /uptime — แสดงเวลา server ทำงานมา"""
        uptime_s = int(time.time() - _server_start_time)
        h, m, s  = uptime_s//3600, (uptime_s%3600)//60, uptime_s%60
        online_n = len(self._clients)
        total_bk = len(self._bks)
        self._api.send(_cid,
            f"⏱ <b>Server Uptime</b>\n"
            f"รันมาแล้ว:  <b>{h}h {m}m {s}s</b>\n"
            f"🟢 Online:   <b>{online_n}</b> คน\n"
            f"📅 จองทั้งหมด: <b>{total_bk}</b>\n"
            f"🕐 {now_full()}"
        )

    def _cmd_clearlog(self, _):
        """🆕 /clearlog — ล้าง log file"""
        mid = self._api.send(_cid, "⚠️ ล้าง log file ทั้งหมด?",
                            markup=kb([("✅ ยืนยัน ล้าง Log", "confirm:clearlog")],
                                      [("❌ ยกเลิก", "confirm:abort")]))

    def _do_clearlog(self, mid: int):
        try:
            open(LOG_FILE, "w").close()
            log.info("[BotAdmin] log file cleared")
            self._api.edit(_cid, mid, "✅ ล้าง log file เรียบร้อย")
        except Exception as e:
            self._api.edit(_cid, mid, f"❌ ล้าง log ล้มเหลว: {e}")

    def _cmd_help(self, _):
        self._api.send(_cid,
            "🤖 <b>Admin Bot — คำสั่งทั้งหมด</b>\n\n"
            "<b>เมนูหลัก:</b>\n"
            "/menu  /start  /help\n\n"
            "<b>ข้อมูล:</b>\n"
            "/status — สถิติระบบ\n"
            "/today — สรุปวันนี้\n"
            "/schedule — ตารางการจอง\n"
            "/weather — สภาพอากาศ\n"
            "/uptime — เวลา server ทำงาน\n\n"
            "<b>จัดการผู้ใช้:</b>\n"
            "/users — รายชื่อผู้ใช้\n"
            "/find &lt;query&gt; — ค้นหาผู้ใช้\n"
            "/kick &lt;user&gt; — Disconnect ผู้ใช้\n"
            "/setadmin &lt;user&gt; — Toggle Admin\n\n"
            "<b>การจอง & อุปกรณ์:</b>\n"
            "/bookings — ดูการจอง\n"
            "/loans — รายการยืมอุปกรณ์\n"
            "/stock — Stock อุปกรณ์\n\n"
            "<b>อื่นๆ:</b>\n"
            "/broadcast &lt;msg&gt; — ส่งข้อความ\n"
            "/logs [n] — ดู log (default 20)\n"
            "/clearlog — ล้าง log file\n"
            "/alert — Toggle push alerts\n\n"
            "💡 ทุก action มีปุ่ม inline ในเมนู")


# ══════════════════════════════════════════════════
# DAILY AUTO SUMMARY
# ══════════════════════════════════════════════════

def _schedule_daily_summary(api: TelegramAPI, bot: AdminBot):
    """ส่ง daily summary ทุกวันเวลา DAILY_SUMMARY_HOUR:00"""
    while True:
        now  = datetime.now()
        next = now.replace(hour=DAILY_SUMMARY_HOUR, minute=0, second=0, microsecond=0)
        if next <= now:
            next += timedelta(days=1)
        wait = (next - now).total_seconds()
        time.sleep(wait)
        try:
            today = date.today().strftime("%Y-%m-%d")
            bks   = [b for b in bot._bks if b["date"]==today and b["status"]=="confirmed"]
            online = len(bot._clients)
            active_ln = sum(1 for l in bot._loans if l["status"]=="active")
            lines = [
                f"🌅 <b>Daily Summary  {today}</b>\n",
                f"🟢 ขณะนี้ Online:  {online} คน",
                f"📅 จองวันนี้:       {len(bks)} รายการ",
                f"📦 ยืม active:      {active_ln}",
                "",
            ]
            for c in COURT_TYPES:
                c_bks = [b for b in bks if b["court_type"]==c]
                if c_bks:
                    lines.append(f"{COURT_EMOJIS[c]} {c}: {len(c_bks)} รายการ")
                    for b in c_bks:
                        lines.append(f"  ⏰ {b['time_slot']}  👤 {b['user_name']}")
            api.send(_cid, "\n".join(lines))
        except Exception as e:
            log.warning("daily summary error: %s", e)


# ══════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════

try:
    from constants import MAX_ADVANCE_DAYS
    MAX_ADVANCE_DAYS_PLUS = MAX_ADVANCE_DAYS + 1
except ImportError:
    MAX_ADVANCE_DAYS_PLUS = 4


# ══════════════════════════════════════════════════
# POLLING LOOP
# ══════════════════════════════════════════════════

def _run_bot(api: TelegramAPI, bot: AdminBot):
    global _server_start_time
    _server_start_time = time.time()
    offset = 0
    log.info("AdminBot polling เริ่มแล้ว (chat_id: %s)", _cid)

    threading.Timer(1.0, lambda: api.send(
        _cid,
        f"🤖 <b>Admin Bot พร้อมแล้ว</b>  ·  {now_s()}\n"
        f"พิมพ์ /menu หรือ /start เพื่อเริ่มใช้งาน\n"
        f"พิมพ์ /help ดูคำสั่งทั้งหมด"
    )).start()

    while True:
        try:
            for upd in api.get_updates(offset):
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        msg  = upd["message"]
                        cid  = str(msg["chat"]["id"])
                        text = (msg.get("text") or "").strip()
                        if not text: continue
                        if cid != _cid:
                            api.send(cid, "⛔ ไม่มีสิทธิ์ใช้งาน Bot นี้")
                            log.warning("Unauthorized chat_id: %s", cid)
                            continue
                        bot.on_message(text)

                    elif "callback_query" in upd:
                        cq  = upd["callback_query"]
                        cid = str(cq["message"]["chat"]["id"])
                        if cid != _cid: continue
                        bot.on_callback(
                            cq["message"]["message_id"],
                            cq["id"],
                            cq.get("data", ""),
                        )
                except Exception as e:
                    log.error("AdminBot update error: %s", e)

        except Exception as e:
            log.warning("AdminBot poll error: %s", e)
            time.sleep(3)


# ══════════════════════════════════════════════════
# PUBLIC API
# ══════════════════════════════════════════════════

def start_all_bots(server_ctx: dict):
    """เรียกจาก server.py ใน main()
    server_ctx ต้องมี:
        users_db, bookings_db, loans_db, clients,
        db_lock, save_db, broadcast, fetch_weather,
        book_court, cancel_booking, get_stock (optional),
        try_send (optional — ส่งข้อความหา client socket)
    """
    global _api

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("TELEGRAM_TOKEN/CHAT_ID ไม่ได้ตั้งค่า — ข้าม bot")
        return

    _api = TelegramAPI(TELEGRAM_TOKEN)

    from config import HOST, PORT
    server_ctx.setdefault("host", HOST)
    server_ctx.setdefault("port", PORT)

    bot = AdminBot(_api, server_ctx)

    # Polling thread
    threading.Thread(target=_run_bot, args=(_api, bot), daemon=True).start()

    # Daily summary thread
    threading.Thread(target=_schedule_daily_summary, args=(_api, bot),
                     daemon=True).start()


# backward compat
def start_polling(server_ctx: dict):
    start_all_bots(server_ctx)
