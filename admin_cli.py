#!/usr/bin/env python3
# admin_cli.py — Sports Queue Admin CLI
# ใช้ระบบเมนูตัวเลข ไม่ต้องพิมพ์คำสั่งเอง

import os, sys, socket, getpass, logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config    import HOST, PORT, SOCKET_TIMEOUT
from constants import *
from network   import make_client_socket, safe_send, safe_recv

# ── Logging ───────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "admin.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("admin_cli")


# ══════════════════════════════════════════════════
# TERMINAL COLORS
# ══════════════════════════════════════════════════

class T:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    RED   = "\033[91m"; GREEN = "\033[92m"; YELLOW = "\033[93m"
    BLUE  = "\033[94m"; CYAN  = "\033[96m"

def bold(s):   return f"{T.BOLD}{s}{T.RESET}"
def ok(s):     return f"{T.GREEN}✓  {s}{T.RESET}"
def err(s):    return f"{T.RED}✗  {s}{T.RESET}"
def warn(s):   return f"{T.YELLOW}⚠  {s}{T.RESET}"
def dim(s):    return f"{T.DIM}{s}{T.RESET}"
def hl(s):     return f"{T.CYAN}{s}{T.RESET}"


# ══════════════════════════════════════════════════
# TABLE PRINTER
# ══════════════════════════════════════════════════

def table(headers: list, rows: list):
    if not rows:
        print(dim("  (ไม่มีข้อมูล)\n"))
        return
    widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=0))
              for i, h in enumerate(headers)]
    sep  = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    head = "|" + "|".join(f" {T.BOLD}{h:<{widths[i]}}{T.RESET} "
                          for i, h in enumerate(headers)) + "|"
    print(sep)
    print(head)
    print(sep)
    for row in rows:
        print("|" + "|".join(f" {str(row[i]):<{widths[i]}} " for i in range(len(headers))) + "|")
    print(sep)
    print(dim(f"  {len(rows)} รายการ\n"))


# ══════════════════════════════════════════════════
# ADMIN CLIENT
# ══════════════════════════════════════════════════

class AdminSession:
    def __init__(self):
        self.sock: socket.socket = None

    def connect(self) -> bool:
        self.sock = make_client_socket()
        return self.sock is not None

    def send(self, data: dict) -> dict:
        try:
            safe_send(self.sock, data)
            res = safe_recv(self.sock, timeout=10)
            return res or {"ok": False, "msg": "ไม่ได้รับ response"}
        except Exception as e:
            log.error("send error: %s", e)
            return {"ok": False, "msg": str(e)}

    def close(self):
        if self.sock:
            try:
                safe_send(self.sock, {"cmd": CMD_LOGOUT})
                self.sock.close()
            except Exception:
                pass


# ══════════════════════════════════════════════════
# INPUT HELPERS
# ══════════════════════════════════════════════════

def ask(prompt: str, default: str = "") -> str:
    val = input(f"  {T.CYAN}{prompt}{' [' + default + ']' if default else ''}: {T.RESET}").strip()
    return val or default

def confirm(prompt: str) -> bool:
    return input(f"  {T.YELLOW}{prompt} (y/N): {T.RESET}").strip().lower() == "y"

def pick(options: list, title: str = "เลือก") -> int:
    """แสดงตัวเลือก คืน index ที่เลือก หรือ -1 ถ้ายกเลิก"""
    print(f"\n  {T.BOLD}{title}{T.RESET}")
    for i, opt in enumerate(options, 1):
        print(f"  {T.CYAN}{i}{T.RESET}. {opt}")
    print(f"  {T.CYAN}0{T.RESET}. ← ย้อนกลับ")
    try:
        choice = int(input(f"\n  เลือก: ").strip())
        if choice == 0:
            return -1
        if 1 <= choice <= len(options):
            return choice - 1
        print(warn("ตัวเลือกไม่ถูกต้อง"))
        return -1
    except (ValueError, EOFError):
        return -1


# ══════════════════════════════════════════════════
# SCREENS (แต่ละหน้าจอ)
# ══════════════════════════════════════════════════

def screen_header(title: str):
    print(f"\n{T.BOLD}{T.BLUE}{'═'*52}")
    print(f"  {title}")
    print(f"{'═'*52}{T.RESET}\n")


def screen_stats(s: AdminSession):
    screen_header("📊 สถิติระบบ")
    res = s.send({"cmd": CMD_ADMIN_STATS})
    if not res["ok"]:
        print(err(res["msg"])); return
    st = res["stats"]
    pairs = [
        ("ผู้ใช้ทั้งหมด",     st["total_users"]),
        ("Online ขณะนี้",     f"{st['online_users']} คน  →  {', '.join(st['online_list']) or '-'}"),
        ("จองทั้งหมด",        st["total_bookings"]),
        ("จองวันนี้",          st["confirmed_today"]),
        ("ยกเลิกสะสม",        st["cancelled_total"]),
        ("ยืมอุปกรณ์ active", st["active_loans"]),
        ("เวลาเซิร์ฟเวอร์",   st["server_time"]),
    ]
    for k, v in pairs:
        print(f"  {k:<26} {T.BOLD}{v}{T.RESET}")
    input(dim("\n  [Enter] เพื่อกลับ"))


def screen_users(s: AdminSession):
    screen_header("👥 รายชื่อผู้ใช้")
    search = ask("ค้นหา (ชื่อ/username) หรือ Enter เพื่อดูทั้งหมด")
    res = s.send({"cmd": CMD_ADMIN_USERS})
    if not res["ok"]:
        print(err(res["msg"])); return
    users = res["users"]
    if search:
        users = [u for u in users if search.lower() in u["username"].lower()
                 or search.lower() in u["name"].lower()]
    rows = []
    for u in users:
        status = "🟢 online" if u["online"] else ("⛔ banned" if u["is_banned"] else "⚪ offline")
        role   = "👑 Admin" if u["is_admin"] else "User"
        rows.append([u["username"], u["name"], u.get("email") or "-",
                     role, status, u["bookings"], u["loans"]])
    table(["Username", "ชื่อ", "Email", "Role", "สถานะ", "จอง", "ยืม"], rows)

    idx = pick(["ban ผู้ใช้", "unban ผู้ใช้"], "จัดการผู้ใช้")
    if idx == -1:
        return
    username = ask("Username ที่ต้องการ")
    if not username:
        return
    action = "ban" if idx == 0 else "unban"
    label  = "ระงับ" if action == "ban" else "ปลดระงับ"
    if not confirm(f"{label}บัญชี {bold(username)}?"):
        print(dim("  ยกเลิก")); return
    r = s.send({"cmd": CMD_ADMIN_BAN, "username": username, "action": action})
    print(ok(r["msg"]) if r["ok"] else err(r["msg"]))
    log.info("%s: %s '%s'", action, label, username)
    input(dim("  [Enter] เพื่อกลับ"))


def screen_bookings(s: AdminSession):
    screen_header("📅 ดูการจอง")
    idx = pick(["วันนี้", "เลือกวันที่เอง", "ทุกวัน (ทั้งหมด)"], "ช่วงเวลา")
    if idx == -1:
        return

    from datetime import date
    req = {"cmd": CMD_ADMIN_BOOKINGS, "status": "confirmed"}
    if idx == 0:
        req["date"] = date.today().strftime("%Y-%m-%d")
    elif idx == 1:
        req["date"] = ask("วันที่ (YYYY-MM-DD)")
    elif idx == 2:
        req["status"] = "all"

    idx2 = pick(["ทุกสนาม"] + COURT_TYPES, "กรองสนาม")
    if idx2 > 0:
        req["court_type"] = COURT_TYPES[idx2 - 1]

    res = s.send(req)
    if not res["ok"]:
        print(err(res["msg"])); return
    bks = res["bookings"]
    rows = [[b["booking_id"], b["username"], b["court_type"],
             b["date"], b["time_slot"], b["status"]] for b in bks]
    table(["ID", "ผู้จอง", "สนาม", "วันที่", "ช่วงเวลา", "สถานะ"], rows)

    if confirm("Force cancel การจองใดสักรายการ?"):
        bid = ask("Booking ID")
        if bid:
            if confirm(f"ยืนยัน force cancel {bold(bid)}?"):
                r = s.send({"cmd": CMD_ADMIN_CANCEL, "booking_id": bid})
                print(ok(r["msg"]) if r["ok"] else err(r["msg"]))
                log.info("admin cancel: %s", bid)
    input(dim("  [Enter] เพื่อกลับ"))


def screen_loans(s: AdminSession):
    screen_header("🏸 รายการยืมอุปกรณ์")
    idx = pick(["กำลังยืม (active)", "คืนแล้ว (returned)", "ทั้งหมด"], "แสดง")
    if idx == -1:
        return
    status_map = {0: "active", 1: "returned", 2: "all"}
    res = s.send({"cmd": CMD_ADMIN_LOANS, "status": status_map[idx]})
    if not res["ok"]:
        print(err(res["msg"])); return
    rows = [[ln["loan_id"], ln["username"], ln["court_type"],
             ", ".join(ln["item_names"]), ln["status"],
             ln["borrowed_at"], ln.get("returned_at") or "-"]
            for ln in res["loans"]]
    table(["Loan ID", "ผู้ยืม", "สนาม", "อุปกรณ์", "สถานะ", "ยืมเมื่อ", "คืนเมื่อ"], rows)

    if idx == 0 and confirm("Force return รายการใดสักรายการ?"):
        lid = ask("Loan ID")
        if lid:
            if confirm(f"ยืนยัน force return {bold(lid)}?"):
                r = s.send({"cmd": CMD_ADMIN_RETURN, "loan_id": lid})
                print(ok(r["msg"]) if r["ok"] else err(r["msg"]))
                log.info("admin return: %s", lid)
    input(dim("  [Enter] เพื่อกลับ"))


def screen_broadcast(s: AdminSession):
    screen_header("📢 ส่งข้อความหาทุกคน")
    msg = ask("ข้อความ")
    if not msg:
        print(warn("ไม่มีข้อความ")); return
    if not confirm(f"ส่ง: {bold(msg)}?"):
        print(dim("  ยกเลิก")); return
    r = s.send({"cmd": CMD_ADMIN_BROADCAST, "msg": msg})
    print(ok(r["msg"]) if r["ok"] else err(r["msg"]))
    log.info("broadcast: %s", msg)
    input(dim("  [Enter] เพื่อกลับ"))


def screen_weather(s: AdminSession):
    screen_header("🌤 สภาพอากาศปัจจุบัน (มทส)")
    res = s.send({"cmd": CMD_WEATHER})
    if not res["ok"]:
        print(err(res["msg"])); return
    w = res["weather"]
    for k, v in [
        ("สภาพ",         w["description"]),
        ("อุณหภูมิ",     f"{w['temperature']:.1f}°C"),
        ("ความชื้น",     f"{w['humidity']:.0f}%"),
        ("ลม",           f"{w['wind_speed']:.1f} m/s"),
        ("โอกาสฝน",     f"{w['rain_prob']:.0f}%"),
        ("UV Index",     f"{w['uv_index']:.1f}"),
        ("แหล่งข้อมูล", w["source"]),
        ("อัปเดต",       w["updated_at"]),
    ]:
        print(f"  {k:<16} {bold(str(v))}")
    input(dim("\n  [Enter] เพื่อกลับ"))


# ══════════════════════════════════════════════════
# MAIN MENU
# ══════════════════════════════════════════════════

MENU = [
    ("📊  สถิติระบบ",          screen_stats),
    ("👥  จัดการผู้ใช้",        screen_users),
    ("📅  ดูและจัดการการจอง",   screen_bookings),
    ("🏸  รายการยืมอุปกรณ์",    screen_loans),
    ("📢  ส่งข้อความ broadcast", screen_broadcast),
    ("🌤  สภาพอากาศ",           screen_weather),
]


def main_menu(s: AdminSession):
    while True:
        print(f"\n{T.BOLD}{T.BLUE}  ╔══════════════════════════════╗")
        print(f"  ║   Sports Queue Admin CLI    ║")
        print(f"  ╚══════════════════════════════╝{T.RESET}")
        for i, (label, _) in enumerate(MENU, 1):
            print(f"  {T.CYAN}{i}{T.RESET}. {label}")
        print(f"  {T.CYAN}0{T.RESET}. ออกจากระบบ\n")
        try:
            choice = int(input(f"  {T.BOLD}เลือก: {T.RESET}").strip())
        except (ValueError, EOFError):
            continue
        if choice == 0:
            break
        if 1 <= choice <= len(MENU):
            try:
                MENU[choice - 1][1](s)
            except Exception as e:
                print(err(f"เกิดข้อผิดพลาด: {e}"))
                log.error("menu error: %s", e)


# ══════════════════════════════════════════════════
# LOGIN & ENTRY
# ══════════════════════════════════════════════════

def login(s: AdminSession) -> bool:
    print(f"\n{T.BOLD}{T.BLUE}  Sports Queue — Admin Login{T.RESET}\n")
    username = ask("Username")
    password = getpass.getpass(f"  {T.CYAN}Password: {T.RESET}")
    res = s.send({"cmd": CMD_LOGIN, "username": username, "password": password})
    if not res or not res["ok"]:
        print(err(res["msg"] if res else "เชื่อมต่อล้มเหลว"))
        log.warning("Login ล้มเหลว: %s", username)
        return False
    if not res.get("is_admin"):
        print(err("บัญชีนี้ไม่มีสิทธิ์ Admin"))
        log.warning("Login ไม่ใช่ admin: %s", username)
        return False
    print(ok(f"Login สำเร็จ — {bold(res['name'])}"))
    log.info("Login สำเร็จ: %s", username)
    return True


def main():
    s = AdminSession()
    if not s.connect():
        print(err(f"เชื่อมต่อ {HOST}:{PORT} ไม่ได้"))
        sys.exit(1)
    if not login(s):
        s.close()
        sys.exit(1)
    try:
        main_menu(s)
    finally:
        print(f"\n{dim('  กำลังออกจากระบบ...')}")
        s.close()
        log.info("ออกจากระบบแล้ว")

if __name__ == "__main__":
    main()
