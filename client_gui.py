#!/usr/bin/env python3
"""
client_gui.py — Sports Queue Client (Data-Driven UI)
หน้าต่างและ form ทุกส่วนสร้างจาก data definition ไม่ใช่ hard-code ทีละ widget
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading, socket, queue, os, sys, time, json
import urllib.request, urllib.error
from datetime import date, timedelta

try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

EQUIP_IMG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "equipment")
_img_cache: dict = {}   # eq_id → ImageTk.PhotoImage (ป้องกัน GC เก็บ reference)

def load_equip_image(eq_id: str, size: int = 64):
    """โหลดรูป assets/equipment/<eq_id>.png (หรือ .jpg/.jpeg/.gif/.webp)
    คืน ImageTk.PhotoImage หรือ None ถ้าไม่มีรูป / PIL ไม่ได้ติดตั้ง"""
    key = f"{eq_id}_{size}"
    if key in _img_cache:
        return _img_cache[key]
    if not _PIL_OK:
        return None
    for ext in ("png","jpg","jpeg","gif","webp"):
        path = os.path.join(EQUIP_IMG_DIR, f"{eq_id}.{ext}")
        if os.path.exists(path):
            try:
                img = Image.open(path).convert("RGBA")
                img.thumbnail((size, size), Image.LANCZOS)
                # วาง thumbnail บน background สีเดียวกับ card
                bg_hex = C.get("card","#131d2e").lstrip("#")
                bg_rgb = tuple(int(bg_hex[i:i+2],16) for i in (0,2,4))
                canvas = Image.new("RGBA", (size, size), (*bg_rgb, 255))
                offset = ((size - img.width)//2, (size - img.height)//2)
                canvas.paste(img, offset, img)
                photo = ImageTk.PhotoImage(canvas)
                _img_cache[key] = photo
                return photo
            except Exception:
                break
    return None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from constants import *
from config    import HOST, PORT, SOCKET_TIMEOUT


# ══════════════════════════════════════════════════
# THEME
# ══════════════════════════════════════════════════

C = {
    "bg":"#080d14","surface":"#0e1623","card":"#131d2e","elevated":"#182233",
    "border":"#1e2d42","accent":"#3b82f6","success":"#22c55e","warning":"#f59e0b",
    "danger":"#ef4444","text":"#e2e8f0","sub":"#64748b","dim":"#334155",
    **COURT_COLORS,
}

def mix(fg, bg="#080d14", a=0.2):
    try:
        r = lambda h,i: int(h[i:i+2],16)
        return "#{:02x}{:02x}{:02x}".format(
            int(r(fg,1)*a + r(bg,1)*(1-a)),
            int(r(fg,3)*a + r(bg,3)*(1-a)),
            int(r(fg,5)*a + r(bg,5)*(1-a)))
    except: return bg

def lighten(h):
    try: return "#{:02x}{:02x}{:02x}".format(*[min(255,int(h[i:i+2],16)+25) for i in (1,3,5)])
    except: return h

def rain_color(p): return C["danger"] if p>=70 else ("#f97316" if p>=50 else (C["warning"] if p>=30 else C["success"]))
def rain_label(p): return f"{'🌧' if p>=70 else '🌦' if p>=50 else '🌤' if p>=30 else '☀'} {p:.0f}%"

CA = {k: mix(C[k], a=a) for k,a in [("accent",.15),("success",.15),("warning",.15),
                                      ("danger",.12),("accent",.25),("warning",.20),("danger",.20)]}
CA = {
    "accent_dim":  mix(C["accent"],  a=0.15), "success_dim": mix(C["success"], a=0.15),
    "warning_dim": mix(C["warning"], a=0.15), "danger_dim":  mix(C["danger"],  a=0.12),
    "accent_mid":  mix(C["accent"],  a=0.25),
}

F = {"sm":("Tahoma",9),"md":("Tahoma",11),"lg":("Tahoma",14,"bold"),"xl":("Tahoma",18,"bold")}


# ── Widget helpers ────────────────────────────────

def frm(p, bg=None, **kw):   return tk.Frame(p, bg=bg or C["bg"], **kw)
def lbl(p, text="", fg=None, font="md", bg=None, **kw):
    return tk.Label(p, text=text, fg=fg or C["text"], font=F[font], bg=bg or C["bg"], **kw)
def sep(p, bg=None):          tk.Frame(p, bg=bg or C["border"], height=1).pack(fill="x")
def inp(p, show=None, w=22):
    return tk.Entry(p, bg=C["elevated"], fg=C["text"], insertbackground=C["text"],
                    font=F["md"], bd=0, show=show, width=w,
                    highlightbackground=C["border"], highlightcolor=C["accent"],
                    highlightthickness=1, relief="flat")

def btn(p, text, cmd, color=None, fg="white", padx=18, pady=6):
    color = color or C["accent"]
    b = tk.Button(p, text=text, command=cmd, bg=color, fg=fg, font=F["md"],
                  bd=0, padx=padx, pady=pady, cursor="hand2", relief="flat",
                  activebackground=color, activeforeground=fg, highlightthickness=0)
    b.bind("<Enter>", lambda e: b.config(bg=lighten(color)))
    b.bind("<Leave>", lambda e: b.config(bg=color))
    return b


# ══════════════════════════════════════════════════
# SCROLL FRAME
# ══════════════════════════════════════════════════

class ScrollFrame(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C["bg"])
        cv  = tk.Canvas(self, bg=C["bg"], highlightthickness=0, bd=0)
        vsb = tk.Scrollbar(self, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y"); cv.pack(side="left", fill="both", expand=True)
        self.inner = frm(cv)
        win = cv.create_window((0,0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<Configure>",         lambda e: cv.itemconfig(win, width=e.width))
        cv.bind_all("<MouseWheel>",    lambda e: cv.yview_scroll(-1*(e.delta//120),"units"))


# ══════════════════════════════════════════════════
# DATA DEFINITIONS — แก้ที่นี่เพื่อเปลี่ยน UI
# ══════════════════════════════════════════════════

# หน้าต่างๆ ใน sidebar
NAV_PAGES = [
    {"key":"tab_book",    "label":"📅  จองสนาม",       "on_enter":"_load_schedule"},
    {"key":"tab_mybk",    "label":"📋  การจองของฉัน",   "on_enter":"_load_my_bookings"},
    {"key":"tab_loans",   "label":"🎽  อุปกรณ์ของฉัน",  "on_enter":"_load_my_loans"},
    {"key":"tab_weather", "label":"🌤  สภาพอากาศ",      "on_enter":"_load_weather"},
    {"key":"tab_ai",      "label":"🤖  AI ผู้ช่วย",     "on_enter":"_on_enter_ai"},
]

# ฟอร์ม Login
LOGIN_FIELDS = [
    {"key":"username", "label":"ชื่อผู้ใช้",  "show":None, "default":"demo"},
    {"key":"password", "label":"รหัสผ่าน",    "show":"•",  "default":"demo123"},
]

# ฟอร์ม Register
REGISTER_FIELDS = [
    {"key":"name",     "label":"ชื่อ-นามสกุล",             "show":None},
    {"key":"username", "label":"ชื่อผู้ใช้",                "show":None},
    {"key":"email",    "label":"อีเมล (รับการแจ้งเตือน)",   "show":None},
    {"key":"password", "label":"รหัสผ่าน",                  "show":"•"},
]

# Grid ข้อมูลอากาศ
WEATHER_STATS = [
    {"key":"temp",     "label":"🌡 อุณหภูมิ",    "fmt": lambda w: f"{w['temperature']:.1f}°C"},
    {"key":"feels",    "label":"🤔 รู้สึกเหมือน", "fmt": lambda w: f"{w['feels_like']:.1f}°C"},
    {"key":"humidity", "label":"💧 ความชื้น",     "fmt": lambda w: f"{w['humidity']:.0f}%"},
    {"key":"wind",     "label":"💨 ลม",           "fmt": lambda w: f"{w['wind_speed']:.1f} m/s"},
    {"key":"uv",       "label":"☀ UV Index",     "fmt": lambda w: f"{w['uv_index']:.1f}",
     "color": lambda w: C["danger"] if w["uv_index"]>=8 else (C["warning"] if w["uv_index"]>=6 else C["success"])},
    {"key":"rain",     "label":"🌧 โอกาสฝน",     "fmt": lambda w: f"{w['rain_prob']:.0f}%",
     "color": lambda w: rain_color(w["rain_prob"])},
]

# คอลัมน์ตาราง My Bookings
BOOKING_COLS = [
    {"key":"booking_id", "label":"หมายเลข",  "width":120},
    {"key":"court_type", "label":"สนาม",      "width":120},
    {"key":"date",       "label":"วันที่",    "width":110},
    {"key":"time_slot",  "label":"ช่วงเวลา",  "width":130},
    {"key":"booked_at",  "label":"จองเมื่อ",  "width":160},
]

# คอลัมน์ตาราง My Loans
LOAN_COLS = [
    {"key":"loan_id",    "label":"หมายเลขยืม", "width":130},
    {"key":"court_type", "label":"สนาม",        "width":110},
    {"key":"date",       "label":"วันที่",      "width":110},
    {"key":"time_slot",  "label":"ช่วงเวลา",    "width":130},
    {"key":"items_str",  "label":"อุปกรณ์",     "width":220},
    {"key":"borrowed_at","label":"ยืมเมื่อ",    "width":160},
]


# ══════════════════════════════════════════════════
# APP
# ══════════════════════════════════════════════════

class App(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Sports Queue"); self.configure(bg=C["bg"])
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.sock=None; self.username=None; self.user_name=None
        self._my_bk_cache=[]; self._running=True
        self._weather_cache=None; self._weather_ts=0
        self._sched_ts=0
        self._jobs=queue.Queue()
        self._bcast=queue.Queue()
        # AI chatbot state
        self._ai_history = []          # [{"role":"user"|"assistant","content":"..."}]
        self._groq_api_key = self._load_groq_key()
        self._setup_style(); self._show_login()
        threading.Thread(target=self._worker_loop, daemon=True).start()
        threading.Thread(target=self._bcast_loop,  daemon=True).start()

    # ── TTK Style ─────────────────────────────────

    def _setup_style(self):
        s = ttk.Style(self); s.theme_use("clam")
        s.configure(".", background=C["bg"], foreground=C["text"], font=F["md"])
        s.configure("TNotebook", background=C["surface"], borderwidth=0)
        s.configure("TNotebook.Tab", background=C["surface"], foreground=C["sub"], padding=[20,10])
        s.map("TNotebook.Tab", background=[("selected",C["card"])], foreground=[("selected",C["text"])])
        s.configure("TCombobox", fieldbackground=C["elevated"], background=C["elevated"],
                    foreground=C["text"], selectbackground=C["elevated"], arrowcolor=C["sub"])
        s.map("TCombobox", fieldbackground=[("readonly",C["elevated"])])
        s.configure("Treeview", background=C["card"], foreground=C["text"],
                    fieldbackground=C["card"], rowheight=32)
        s.configure("Treeview.Heading", background=C["elevated"], foreground=C["sub"],
                    font=("Tahoma",9,"bold"))
        s.map("Treeview", background=[("selected",CA["accent_mid"])])

    # ── Network (Worker Thread Pattern) ──────────
    # UI โพสต์ job(data, callback) เข้า _jobs queue
    # _worker_loop รัน sequential — ไม่มี lock แย่งกัน
    # _bcast_loop รับ broadcast แยกต่างหาก

    def _net_connect(self):
        """เชื่อมต่อ socket ใหม่ — เรียกจาก worker thread"""
        try:
            if self.sock:
                try: self.sock.close()
                except: pass
            s = socket.socket()
            s.settimeout(SOCKET_TIMEOUT)
            s.connect((HOST, PORT))
            self.sock = s
            return True
        except Exception as e:
            self.after(0, lambda: messagebox.showerror("เชื่อมต่อไม่ได้", str(e)))
            return False

    # ประเภท message ที่ server push มาเองโดยไม่รอ request
    _PUSH_TYPES = {"schedule_update", "admin_msg", "force_logout", "rain_alert"}

    def _is_push(self, msg) -> bool:
        """True ถ้า msg เป็น server-push (broadcast) ไม่ใช่ response ของ request"""
        return isinstance(msg, dict) and msg.get("type") in self._PUSH_TYPES

    def _recv_response(self) -> dict | None:
        """
        อ่าน message จาก socket จนได้ response จริง (มี key "ok")
        ถ้าระหว่างรออ่านได้ broadcast ให้โยนเข้า _bcast queue แล้วอ่านต่อ
        ป้องกัน: broadcast แทรก response → callback ได้ผิด type → KeyError
        """
        while True:
            msg = recv_msg(self.sock)
            if msg is None:
                return None                          # connection หลุด
            if self._is_push(msg):
                self._bcast.put(msg)                 # ส่งไป bcast loop จัดการ
                continue                             # อ่านต่อจนได้ response จริง
            return msg

    def _worker_loop(self):
        """Worker thread เดียวที่คุยกับ server — ทำงาน sequential ไม่มีชน"""
        while self._running:
            try:
                job = self._jobs.get(timeout=1)
                if job is None: break          # sentinel ให้หยุด
                data, callback = job
                try:
                    send_msg(self.sock, data)
                    res = self._recv_response()  # กรอง broadcast ออกก่อน
                except Exception:
                    res = None
                # ── ตรวจ connection ขาด (kick / ban / server ปิด) ──
                if res is None and self._running:
                    self.after(0, self._handle_disconnect)
                    return
                # ── ตรวจ force_logout ที่แทรกมาเป็น response ──
                if isinstance(res, dict) and res.get("type") == "force_logout":
                    self.after(0, lambda m=res.get("msg","ถูก Kick ออกจากระบบ"):
                               self._handle_force_logout(m))
                    return
                if callback:
                    self.after(0, lambda r=res, cb=callback: cb(r))
            except queue.Empty:
                continue
            except Exception:
                continue

    def _bcast_loop(self):
        """อ่าน broadcast จาก server แยกจาก worker (ใช้ socket แยก)"""
        # bcast ใช้ sock เดียวกัน แต่ worker จัดการ response แล้ว
        # loop นี้ไม่ได้ใช้งานตอนนี้ — broadcast ถูกจัดการผ่าน worker แล้ว
        while self._running:
            try:
                msg = self._bcast.get(timeout=1)
                if not msg: continue
                t, mt = msg.get("msg",""), msg.get("type","")
                self._notif(t, C["danger"] if mt=="admin_msg" else C["accent"])
                if mt == "schedule_update": self._load_schedule()
            except queue.Empty:
                continue

    def _post(self, data: dict, callback):
        """โพสต์ job เข้า worker queue — เรียกจาก main thread ได้เลย"""
        self._jobs.put((data, callback))

    def _connect(self):
        """เชื่อมต่อแบบ synchronous สำหรับ Login/Register (ก่อน worker พร้อม)"""
        try:
            if self.sock:
                try: self.sock.close()
                except: pass
            s = socket.socket(); s.settimeout(SOCKET_TIMEOUT)
            s.connect((HOST, PORT))
            self.sock = s
            return True
        except Exception as e:
            messagebox.showerror("เชื่อมต่อไม่ได้", str(e)); return False

    def _send_sync(self, data):
        """ส่งแบบ synchronous — ใช้เฉพาะ Login/Register ก่อน worker พร้อม"""
        try:
            send_msg(self.sock, data)
            return recv_msg(self.sock)
        except Exception:
            return None

    # ══════════════════════════════════════════════
    # LOGIN SCREEN — built from LOGIN_FIELDS data
    # ══════════════════════════════════════════════

    def _show_login(self):
        for w in self.winfo_children(): w.destroy()
        self.geometry("440x600"); self.resizable(False,False)

        root = frm(self, C["bg"]); root.pack(fill="both", expand=True)
        tk.Frame(root, bg=C["accent"], height=3).pack(fill="x")

        # Header
        hdr = frm(root, C["surface"]); hdr.pack(fill="x")
        ih  = frm(hdr,  C["surface"]); ih.pack(padx=40, pady=28)
        lbl(ih,"SPORTS QUEUE", fg=C["accent"], bg=C["surface"], font="sm").pack()
        tk.Label(ih, text="🏟️", font=("Tahoma",40), bg=C["surface"], fg=C["text"]).pack(pady=(4,0))
        lbl(ih,"ระบบจองสนามกีฬา มทส", font="lg", bg=C["surface"]).pack()
        lbl(ih,"Suranaree University of Technology", fg=C["sub"], bg=C["surface"], font="sm").pack(pady=(2,0))
        sep(root)

        # Tab buttons
        tab_row = frm(root, C["surface"]); tab_row.pack(fill="x")
        self._tab_btns = []
        for i, text in enumerate(["เข้าสู่ระบบ","สมัครสมาชิก"]):
            b = tk.Button(tab_row, text=text, bd=0, pady=10, relief="flat",
                          cursor="hand2", width=20,
                          command=lambda i=i: self._switch_auth(i))
            b.pack(side="left", fill="x", expand=True)
            self._tab_btns.append(b)
        sep(root)

        body = frm(root); body.pack(fill="both", expand=True, padx=36, pady=20)

        # สร้าง form จาก data definition
        self._login_entries  = self._build_form(body, LOGIN_FIELDS)
        self._register_entries = self._build_form(body, REGISTER_FIELDS, visible=False)

        self._login_form_frame    = self._login_entries["_frame"]
        self._register_form_frame = self._register_entries["_frame"]

        self._add_login_buttons(self._login_form_frame)
        self._add_register_buttons(self._register_form_frame)
        self._switch_auth(0)

    def _build_form(self, parent, fields: list, visible=True) -> dict:
        """สร้าง form จาก list of field definitions — คืน dict ของ Entry widgets"""
        f = frm(parent); entries = {"_frame": f}
        for field in fields:
            lbl(f, field["label"], fg=C["sub"], font="sm").pack(anchor="w", pady=(10,2))
            e = inp(f, show=field.get("show"), w=32)
            e.pack(fill="x", ipady=7)
            if field.get("default"): e.insert(0, field["default"])
            entries[field["key"]] = e
        if visible: f.pack(fill="x")
        return entries

    def _add_login_buttons(self, frame):
        def do_login():
            if not self._connect(): return
            res = self._send_sync({"cmd":CMD_LOGIN,
                                   "username":self._login_entries["username"].get(),
                                   "password":self._login_entries["password"].get()})
            if res and res.get("ok"):
                self.username=self._login_entries["username"].get()
                self.user_name=res.get("name",self.username)
                self._show_main()
            else:
                messagebox.showerror("ไม่สำเร็จ", res["msg"] if res else "เชื่อมต่อล้มเหลว")
        btn(frame,"เข้าสู่ระบบ  →",do_login,padx=0,pady=10).pack(fill="x",pady=(18,0))


    def _add_register_buttons(self, frame):
        def do_register():
            e = self._register_entries
            if not all(e[k].get() for k in ["name","username","password"]):
                messagebox.showwarning("แจ้งเตือน","กรุณากรอกข้อมูลให้ครบ"); return
            if not self._connect(): return
            res = self._send_sync({"cmd":CMD_REGISTER,"username":e["username"].get(),
                                   "password":e["password"].get(),"name":e["name"].get(),
                                   "email":e["email"].get()})
            if res and res.get("ok"):
                messagebox.showinfo("สำเร็จ ✅",res["msg"]); self._switch_auth(0)
            else:
                messagebox.showerror("ผิดพลาด", res["msg"] if res else "เชื่อมต่อล้มเหลว")
        btn(frame,"สมัครสมาชิก  →",do_register,C["success"],padx=0,pady=10).pack(fill="x",pady=(18,0))

    def _switch_auth(self, idx):
        for i, b in enumerate(self._tab_btns):
            active = i == idx
            b.config(bg=C["card"] if active else C["surface"],
                     fg=C["text"] if active else C["sub"],
                     font=("Tahoma",10,"bold") if active else ("Tahoma",10))
        (self._register_form_frame if idx==0 else self._login_form_frame).pack_forget()
        (self._login_form_frame if idx==0 else self._register_form_frame).pack(fill="x")

    # ══════════════════════════════════════════════
    # MAIN WINDOW — built from NAV_PAGES data
    # ══════════════════════════════════════════════

    def _show_main(self):
        for w in self.winfo_children(): w.destroy()
        self.geometry("1100x760"); self.resizable(True,True)

        # Sidebar
        side = frm(self, C["surface"]); side.pack(side="left", fill="y")
        side.pack_propagate(False); side.configure(width=210)
        tk.Frame(side, bg=C["accent"], height=3).pack(fill="x")
        ib = frm(side, C["surface"]); ib.pack(padx=18, pady=18)
        lbl(ib,"SPORTS QUEUE", fg=C["accent"], bg=C["surface"], font="sm").pack(anchor="w")
        lbl(ib,f"👤 {self.user_name}", font="lg", bg=C["surface"]).pack(anchor="w", pady=(4,0))
        lbl(ib,"มทส · ออนไลน์", fg=C["success"], bg=C["surface"], font="sm").pack(anchor="w")
        sep(side, C["border"])

        # Nav buttons — built from NAV_PAGES
        self._pages={}; self._nav_btns={}
        nav_f = frm(side, C["surface"]); nav_f.pack(fill="x", pady=8)
        for page in NAV_PAGES:
            b = tk.Button(nav_f, text=page["label"], bg=C["surface"], fg=C["sub"],
                          font=("Tahoma",11), bd=0, pady=11, padx=18,
                          cursor="hand2", anchor="w", relief="flat",
                          command=lambda k=page["key"]: self._nav(k))
            b.pack(fill="x"); self._nav_btns[page["key"]] = b

        bot = frm(side, C["surface"]); bot.pack(side="bottom", fill="x", pady=8)
        sep(bot)
        btn(bot,"  ออกจากระบบ",self._logout,C["surface"],fg=C["sub"],padx=18,pady=10).pack(fill="x")

        self._content = frm(self); self._content.pack(side="left", fill="both", expand=True)
        self._notif_var = tk.StringVar()
        self._notif_bar = tk.Label(self._content, textvariable=self._notif_var,
                                   bg=mix(C["warning"],a=0.18), fg=C["warning"],
                                   font=F["sm"], anchor="w", padx=16, pady=6)

        self._build_page_book()
        self._build_page_mybk()
        self._build_page_loans()
        self._build_page_weather()
        self._build_page_ai()
        self._nav("tab_book"); self._load_schedule()

    def _nav(self, key):
        if key not in self._pages: return
        for k, b in self._nav_btns.items():
            active = k == key
            b.config(bg=C["card"] if active else C["surface"],
                     fg=C["text"] if active else C["sub"],
                     font=("Tahoma",11,"bold") if active else ("Tahoma",11))
            if active: tk.Frame(b, bg=C["accent"], width=3).place(x=0,y=0,relheight=1)
        cur = getattr(self,"_current_page",None)
        if cur and cur != self._pages[key]:
            try: cur.pack_forget()
            except: pass
        self._pages[key].pack(fill="both", expand=True)
        self._current_page = self._pages[key]
        # on_enter callback — ข้ามถ้าข้อมูลยังสด (schedule < 30s, weather handled ใน _load_weather)
        page_def = next((p for p in NAV_PAGES if p["key"]==key), None)
        if page_def and page_def.get("on_enter"):
            method = page_def["on_enter"]
            if method == "_load_schedule" and (time.time() - self._sched_ts) < 30:
                pass  # ข้อมูลยังสด ไม่ต้องดึงใหม่
            else:
                getattr(self, method)()

    def _notif(self, msg, color=None):
        self._notif_var.set(f"  {msg}")
        self._notif_bar.config(bg=mix(color or C["accent"],a=0.18), fg=color or C["accent"])
        try: self._notif_bar.pack(fill="x", before=list(self._content.winfo_children())[0])
        except: self._notif_bar.pack(fill="x")
        self.after(5000, lambda: self._notif_bar.pack_forget())

    def _set_loading(self, is_loading: bool, buttons: list = None, msg: str = "กำลังโหลด…"):
        """เปิด/ปิด loading state — disable ปุ่มชั่วคราวป้องกัน double action"""
        if buttons:
            state = "disabled" if is_loading else "normal"
            for b in buttons:
                try: b.config(state=state)
                except Exception: pass
        if hasattr(self, "_wrow_lbl") and is_loading:
            self._wrow_lbl.config(text=f"⏳ {msg}", fg=C["sub"])

    # ══════════════════════════════════════════════
    # BOOKING PAGE
    # ══════════════════════════════════════════════

    def _build_page_book(self):
        p = frm(self._content); self._pages["tab_book"] = p
        hdr = frm(p, C["surface"]); hdr.pack(fill="x")
        ih  = frm(hdr, C["surface"]); ih.pack(fill="x", padx=20, pady=14)
        lbl(ih,"จองสนามกีฬา",font="lg",bg=C["surface"]).pack(side="left")
        ctrl = frm(ih, C["surface"]); ctrl.pack(side="right")
        lbl(ctrl,"วันที่:",fg=C["sub"],bg=C["surface"],font="sm").pack(side="left",padx=(0,4))
        today     = date.today()
        date_opts = [(today+timedelta(i)).strftime("%Y-%m-%d") for i in range(MAX_ADVANCE_DAYS+1)]
        self._date_var = tk.StringVar(value=date_opts[0])
        dcb = ttk.Combobox(ctrl,textvariable=self._date_var,values=date_opts,state="readonly",width=13)
        dcb.pack(side="left",padx=(0,8)); dcb.bind("<<ComboboxSelected>>",lambda _:self._load_schedule())
        btn(ctrl,"🔄",self._load_schedule,C["elevated"],fg=C["sub"],padx=8,pady=4).pack(side="left")
        sep(p)

        # Court tabs — built from COURT_TYPES
        self._court_tab_btns={}; self._court_var=tk.StringVar(value=COURT_TYPES[0])
        ct_row = frm(p, C["surface"]); ct_row.pack(fill="x")
        for ct in COURT_TYPES:
            b = tk.Button(ct_row,text=f"{COURT_EMOJIS[ct]} {ct}",bg=C["surface"],fg=C["sub"],
                          font=("Tahoma",10),bd=0,padx=16,pady=10,cursor="hand2",relief="flat",
                          command=lambda c=ct: self._select_court(c))
            b.pack(side="left"); self._court_tab_btns[ct] = b
        self._select_court(COURT_TYPES[0], update=False)
        sep(p)

        # Weather row
        wr = frm(p, C["card"]); wr.pack(fill="x")
        wi = frm(wr, C["card"]); wi.pack(padx=16, pady=8)
        self._wrow_lbl = lbl(wi,"กำลังโหลด…",fg=C["sub"],bg=C["card"],font="sm")
        self._wrow_lbl.pack(side="left")

        sf = ScrollFrame(p); sf.pack(fill="both",expand=True)
        self._slots_frame = sf.inner
        self._sched_status = lbl(p,"",fg=C["dim"],bg=C["surface"],font="sm",anchor="e")
        self._sched_status.pack(fill="x",padx=12,pady=4,side="bottom"); sep(p)

    def _select_court(self, ct, update=True):
        self._court_var.set(ct)
        for name, b in self._court_tab_btns.items():
            sel = name==ct
            b.config(bg=mix(C[name],a=0.25) if sel else C["surface"],
                     fg=C[name] if sel else C["sub"],
                     font=("Tahoma",10,"bold") if sel else ("Tahoma",10))
        if update: self._render_slots()

    def _load_schedule(self):
        self._set_loading(True)
        target_date = self._date_var.get()

        def on_schedule(res):
            if res and res.get("ok"):
                self._schedule_data = res["schedule"]
                self._sched_ts = time.time()   # บันทึกเวลาที่ดึงล่าสุด
                self._render_slots()
            # ใช้ weather cache ถ้าข้อมูลยังสด (< 5 นาที)
            if self._weather_cache and (time.time() - self._weather_ts) < 300:
                on_weather(self._weather_cache)
            else:
                self._post({"cmd":CMD_WEATHER}, on_weather)

        def on_weather(res):
            if not res or not res.get("ok"): return
            self._weather_cache = res          # เก็บ cache
            self._weather_ts = time.time()
            w = res["weather"]
            self._wrow_lbl.config(
                text=f"🌡 {w['temperature']:.0f}°C  💧{w['humidity']:.0f}%  "
                     f"💨{w['wind_speed']:.1f}m/s  ☀UV{w['uv_index']:.0f}  "
                     f"{rain_label(w['rain_prob'])}  · {w['description']}",
                fg=rain_color(w["rain_prob"]) if w["rain_prob"]>=30 else C["sub"])

        self._post({"cmd":CMD_GET_SCHEDULE,"date":target_date}, on_schedule)

    def _render_slots(self):
        if not hasattr(self,"_schedule_data"): return
        ct = self._court_var.get(); slots = self._schedule_data.get(ct,{})
        for w in self._slots_frame.winfo_children(): w.destroy()

        hdr = frm(self._slots_frame, C["card"]); hdr.pack(fill="x",padx=12,pady=(12,4))
        lbl(frm(hdr,C["card"]),f"{COURT_EMOJIS[ct]}  {ct}  —  {self._date_var.get()}",
            font="lg",bg=C["card"],fg=C[ct]).pack(padx=16,pady=8,side="left")

        leg = frm(self._slots_frame); leg.pack(fill="x",padx=12,pady=(0,4))
        for color, text in [(C["success"],"✔ ว่าง"),(C["danger"],"✗ จองแล้ว"),
                            (C["accent"],"★ ของฉัน"),(C["warning"],"⚠ ฝน")]:
            tk.Frame(leg,bg=color,width=10,height=10).pack(side="left",padx=(0,3))
            lbl(leg,text,fg=C["sub"],font="sm").pack(side="left",padx=(0,14))

        my = {(b["court_type"],b["date"],b["time_slot"]) for b in self._my_bk_cache}

        for slot in TIME_SLOTS:
            info    = slots.get(slot,{"status":"available","rain_prob":0,"temp":30,"weather_ok":False})
            is_mine = (ct,self._date_var.get(),slot) in my
            booked  = info["status"]=="booked" and not is_mine
            rainy   = info["weather_ok"] and info["rain_prob"]>=30

            if is_mine: sbg,sfg,stxt = CA["accent_dim"],  C["accent"],  "[ ของฉัน ]"
            elif booked: sbg,sfg,stxt = CA["danger_dim"],  C["danger"],  "[ จองแล้ว ]"
            else:        sbg,sfg,stxt = (CA["warning_dim"] if rainy else C["card"],
                                         C["warning"] if rainy else C["success"],
                                         "[ ว่าง/ฝน ]" if rainy else "[ ว่าง ]")

            row = tk.Frame(self._slots_frame,bg=sbg,highlightbackground=C["border"],highlightthickness=1)
            row.pack(fill="x",padx=12,pady=3)
            inner = tk.Frame(row,bg=sbg); inner.pack(fill="x",padx=16,pady=12)
            tk.Label(inner,text=slot,font=("Tahoma",13,"bold"),fg=C["text"],bg=sbg,width=13,anchor="w").pack(side="left")

            wf = tk.Frame(inner,bg=sbg); wf.pack(side="left",padx=16)
            if info["weather_ok"]:
                tk.Label(wf,text=f"{info['temp']:.0f}°C",font=("Tahoma",10),fg=C["sub"],bg=sbg).pack(side="left",padx=(0,8))
                tk.Label(wf,text=rain_label(info["rain_prob"]),font=("Tahoma",10,"bold"),
                         fg=rain_color(info["rain_prob"]),bg=sbg).pack(side="left")
            else:
                tk.Label(wf,text="ไม่มีข้อมูลอากาศ",font=("Tahoma",9),fg=C["dim"],bg=sbg).pack(side="left")

            tk.Label(inner,text=stxt,bg=mix(sfg,a=0.2),fg=sfg,
                     font=("Tahoma",9,"bold"),padx=10,pady=4).pack(side="right",padx=(0,8))

            if not is_mine and not booked:
                bb = tk.Button(inner,text="จอง",bg=C[ct],fg="white",font=("Tahoma",9,"bold"),
                               bd=0,padx=16,pady=4,cursor="hand2",relief="flat",
                               command=lambda s=slot,c=ct,r=info["rain_prob"],t=info["temp"],
                                              wok=info["weather_ok"]:
                               self._confirm_book(c,s,r if wok else -1,t if wok else -1))
                bb.pack(side="right",padx=(0,4))
                bb.bind("<Enter>",lambda e,b=bb,c=ct: b.config(bg=lighten(C[c])))
                bb.bind("<Leave>",lambda e,b=bb,c=ct: b.config(bg=C[c]))

        self._sched_status.config(text=f"อัปเดต {date.today().strftime('%H:%M')}  ·  กดจองเพื่อเลือก")

    def _confirm_book(self, ct, slot, rain_p, temp):
        if rain_p>=50:  note=f"\n\nโอกาสฝน {rain_p:.0f}% — พิจารณาเลือกช่วงอื่น"
        elif rain_p>=30:note=f"\n\nโอกาสฝน {rain_p:.0f}% — ติดตามสภาพอากาศ"
        elif temp>0:    note=f"\n\nอากาศ {temp:.0f}°C  ฝน {rain_p:.0f}%"
        else:           note=""
        if not messagebox.askyesno("ยืนยันการจอง",
               f"สนาม: {ct}\nวันที่: {self._date_var.get()}\nเวลา: {slot}{note}\n\nยืนยัน?"): return
        def cb(res):
            if not res: return
            if res.get("ok"):
                bid = res.get("booking_id","")
                self._notif(f"✅ จองสนาม{ct} {slot}", C["success"])
                self._load_my_bookings_cache(); self._load_schedule()
                # ── ถามเรื่องยืมอุปกรณ์ ──
                self._show_borrow_dialog(bid, ct, self._date_var.get(), slot)
            else: messagebox.showerror("จองไม่ได้", res["msg"])
        self._post({"cmd":CMD_BOOK,"court_type":ct,"date":self._date_var.get(),"time_slot":slot}, cb)

    # ── Equipment borrow dialog ───────────────────

    def _show_borrow_dialog(self, booking_id, court_type, date_str, slot):
        """Dialog popup เพื่อยืมอุปกรณ์หลังจองสำเร็จ — แสดงรูปจาก assets/equipment/"""
        def on_stock(res):
            if not res or not res.get("ok"): return
            items = res.get("items", [])
            available = [it for it in items if it["available"] > 0]
            if not available:
                messagebox.showinfo("จองสำเร็จ ✅",
                    f"หมายเลข: {booking_id}\n📧 ส่งอีเมลยืนยันแล้ว\n\n(อุปกรณ์ไม่ว่างขณะนี้)")
                return

            dlg = tk.Toplevel(self)
            dlg.title("ยืมอุปกรณ์กีฬา 🎽")
            dlg.configure(bg=C["bg"])
            dlg.resizable(False, False)
            dlg.grab_set()

            # ── Header ─────────────────────────────────
            outer = frm(dlg, C["bg"]); outer.pack(padx=24, pady=(20,0), fill="x")
            lbl(outer, f"จองสำเร็จ! หมายเลข: {booking_id}", fg=C["success"], font="md").pack(anchor="w")
            lbl(outer, f"สนาม {court_type} • {date_str} {slot}", fg=C["sub"], font="sm").pack(anchor="w", pady=(2,4))

            # hint path รูป
            hint_bg = C["elevated"]
            hint = frm(outer, hint_bg); hint.pack(fill="x", pady=(0,12))


            lbl(outer, "ต้องการยืมอุปกรณ์กีฬาด้วยไหม?", font="md").pack(anchor="w")


            # ── Equipment cards ─────────────────────────
            vars_ = {}
            IMG_SIZE = 64

            for it in available:
                v = tk.BooleanVar()
                vars_[it["id"]] = v

                card = tk.Frame(outer, bg=C["card"],
                                highlightbackground=C["border"], highlightthickness=1)
                card.pack(fill="x", pady=4)

                # รูปภาพ (Pillow) หรือ emoji fallback
                photo = load_equip_image(it["id"], IMG_SIZE)
                img_frame = frm(card, C["card"]); img_frame.pack(side="left", padx=(10,8), pady=8)
                if photo:
                    il = tk.Label(img_frame, image=photo, bg=C["card"], cursor="hand2")
                    il.image = photo
                    il.pack()
                    il.bind("<Button-1>", lambda e, var=v: var.set(not var.get()))
                else:
                    eb = frm(img_frame, C["elevated"]); eb.pack()
                    tk.Label(eb, text=it["emoji"], font=("Segoe UI Emoji", 26),
                             bg=C["elevated"], width=3, height=1).pack(padx=6, pady=6)

                # ชื่อ + stock
                info_col = frm(card, C["card"]); info_col.pack(side="left", fill="both", expand=True, pady=8)
                lbl(info_col, it["name"], font="md", bg=C["card"]).pack(anchor="w")
                avail_color = C["success"] if it["available"] >= 3 else (C["warning"] if it["available"] >= 1 else C["danger"])
                lbl(info_col, f"คงเหลือ {it['available']} ชิ้น", fg=avail_color, font="sm", bg=C["card"]).pack(anchor="w", pady=(2,0))
                if not _PIL_OK:
                    lbl(info_col, "(pip install Pillow เพื่อแสดงรูป)", fg=C["dim"], font="sm", bg=C["card"]).pack(anchor="w")

                # checkbox ทางขวา
                cb_frame = frm(card, C["card"]); cb_frame.pack(side="right", padx=14)
                tk.Checkbutton(cb_frame, variable=v,
                               bg=C["card"], activebackground=C["card"],
                               selectcolor=C["accent"],
                               width=2, height=2).pack()

                # คลิก card ทั้งใบ = toggle
                for w in (card, info_col):
                    w.bind("<Button-1>", lambda e, var=v: var.set(not var.get()))

            # ── Buttons ─────────────────────────────────
            btnrow = frm(outer); btnrow.pack(fill="x", pady=(16,20))

            def do_borrow():
                selected = [eid for eid, var in vars_.items() if var.get()]
                if not selected:
                    dlg.destroy()
                    messagebox.showinfo("จองสำเร็จ ✅",
                        f"หมายเลข: {booking_id}\n📧 ส่งอีเมลยืนยันแล้ว")
                    return
                def borrow_cb(res2):
                    dlg.destroy()
                    if res2 and res2["ok"]:
                        names = ", ".join(res2["loan"].get("item_names", []))
                        messagebox.showinfo("ยืมสำเร็จ ✅",
                            f"หมายเลขการจอง: {booking_id}\n"
                            f"อุปกรณ์ที่ยืม: {names}\n\n📧 ส่งอีเมลยืนยันแล้ว")
                        self._notif(f"🎽 ยืม: {names}", C["accent"])
                    else:
                        messagebox.showerror("ยืมไม่ได้", res2.get("msg","") if res2 else "ไม่มีการตอบสนอง")
                self._post({"cmd":CMD_BORROW, "booking_id":booking_id, "items":selected}, borrow_cb)

            def skip():
                dlg.destroy()
                messagebox.showinfo("จองสำเร็จ ✅",
                    f"หมายเลข: {booking_id}\n📧 ส่งอีเมลยืนยันแล้ว")

            btn(btnrow, "🎽  ยืมอุปกรณ์", do_borrow, C["accent"],   padx=14, pady=9).pack(side="left",  expand=True, fill="x", padx=(0,6))
            btn(btnrow, "ข้าม",            skip,       C["elevated"], fg=C["sub"], padx=14, pady=9).pack(side="right", expand=True, fill="x", padx=(6,0))

        self._post({"cmd":CMD_EQUIPMENT_STOCK, "court_type":court_type}, on_stock)




    # ══════════════════════════════════════════════
    # MY BOOKINGS PAGE — built from BOOKING_COLS data
    # ══════════════════════════════════════════════

    def _build_page_mybk(self):
        p = frm(self._content); self._pages["tab_mybk"] = p
        hdr = frm(p, C["surface"]); hdr.pack(fill="x")
        ih  = frm(hdr, C["surface"]); ih.pack(fill="x",padx=20,pady=14)
        lbl(ih,"การจองของฉัน",font="lg",bg=C["surface"]).pack(side="left")
        btn(ih,"🔄 รีเฟรช",self._load_my_bookings,C["elevated"],fg=C["sub"],padx=10,pady=5).pack(side="right")
        sep(p)

        # สร้าง Treeview จาก BOOKING_COLS
        cols = tuple(c["key"] for c in BOOKING_COLS)
        self._bk_tree = ttk.Treeview(p, columns=cols, show="headings", height=14)
        for col_def in BOOKING_COLS:
            self._bk_tree.heading(col_def["key"], text=col_def["label"])
            self._bk_tree.column(col_def["key"],  width=col_def["width"], anchor="center")

        vsb = ttk.Scrollbar(p, orient="vertical", command=self._bk_tree.yview)
        self._bk_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right",fill="y",padx=(0,8))
        self._bk_tree.pack(fill="both",expand=True,padx=16,pady=12)

        bot = frm(p, C["surface"]); bot.pack(fill="x",side="bottom"); sep(bot)
        bb = frm(bot, C["surface"]); bb.pack(fill="x", padx=16, pady=12)
        btn(bb,"🎽  ยืมอุปกรณ์",self._borrow_from_mybk,C["accent"],padx=16,pady=9).pack(side="left")
        btn(bb,"❌  ยกเลิกการจอง",self._cancel_selected,C["danger"],padx=16,pady=9).pack(side="right")

    def _load_my_bookings_cache(self):
        def cb(res):
            if res and res.get("ok"): self._my_bk_cache = res.get("bookings",[])
        self._post({"cmd":CMD_MY_BOOKINGS}, cb)

    def _load_my_bookings(self):
        def cb(res):
            if not res or not res.get("ok"): return
            self._my_bk_cache = res.get("bookings",[])
            for r in self._bk_tree.get_children(): self._bk_tree.delete(r)
            for bk in self._my_bk_cache:
                self._bk_tree.insert("","end",values=(
                    bk["booking_id"],
                    f"{COURT_EMOJIS.get(bk['court_type'],'')} {bk['court_type']}",
                    bk["date"],bk["time_slot"],bk.get("booked_at","")))
            if not self._my_bk_cache:
                self._bk_tree.insert("","end",values=("—","ไม่มีการจอง","","",""))
        self._post({"cmd":CMD_MY_BOOKINGS}, cb)

    def _cancel_selected(self):
        sel = self._bk_tree.selection()
        if not sel: messagebox.showwarning("แจ้งเตือน","กรุณาเลือกรายการก่อน"); return
        bid = self._bk_tree.item(sel[0])["values"][0]
        if bid=="—": return
        if not messagebox.askyesno("ยืนยัน",f"ยกเลิกการจอง {bid}?"): return
        def cb(res):
            if not res: return
            if res.get("ok"):
                self._notif(f"ยกเลิก {bid} แล้ว", C["warning"])
                # ── FIX: chain ให้ cache อัปเดตก่อน แล้วค่อย render slots ──
                def reload_cb(res2):
                    if res2 and res2["ok"]:
                        self._my_bk_cache = res2.get("bookings",[])
                        for r in self._bk_tree.get_children(): self._bk_tree.delete(r)
                        for bk in self._my_bk_cache:
                            self._bk_tree.insert("","end",values=(
                                bk["booking_id"],
                                f"{COURT_EMOJIS.get(bk['court_type'],'')} {bk['court_type']}",
                                bk["date"],bk["time_slot"],bk.get("booked_at","")))
                        if not self._my_bk_cache:
                            self._bk_tree.insert("","end",values=("—","ไม่มีการจอง","","",""))
                    self._sched_ts = 0   # บังคับ reload schedule ครั้งถัดไป
                    self._load_schedule()
                self._post({"cmd":CMD_MY_BOOKINGS}, reload_cb)
            else: messagebox.showerror("ผิดพลาด", res["msg"])
        self._post({"cmd":CMD_CANCEL,"booking_id":bid}, cb)

    def _borrow_from_mybk(self):
        """ยืมอุปกรณ์จากการจองที่เลือกใน My Bookings"""
        sel = self._bk_tree.selection()
        if not sel: messagebox.showwarning("แจ้งเตือน","กรุณาเลือกรายการก่อน"); return
        vals = self._bk_tree.item(sel[0])["values"]
        bid  = vals[0]
        if bid == "—": return
        # หาข้อมูล booking จาก cache
        bk = next((b for b in getattr(self,"_my_bk_cache",[]) if b["booking_id"]==bid), None)
        if not bk: messagebox.showwarning("แจ้งเตือน","ไม่พบข้อมูลการจอง"); return
        self._show_borrow_dialog(bk["booking_id"], bk["court_type"], bk["date"], bk["time_slot"])

    # ══════════════════════════════════════════════
    # EQUIPMENT LOANS PAGE
    # ══════════════════════════════════════════════

    def _build_page_loans(self):
        p = frm(self._content); self._pages["tab_loans"] = p
        hdr = frm(p, C["surface"]); hdr.pack(fill="x")
        ih  = frm(hdr, C["surface"]); ih.pack(fill="x",padx=20,pady=14)
        lbl(ih,"อุปกรณ์กีฬาที่ยืมอยู่",font="lg",bg=C["surface"]).pack(side="left")
        btn(ih,"🔄 รีเฟรช",self._load_my_loans,C["elevated"],fg=C["sub"],padx=10,pady=5).pack(side="right")
        sep(p)

        cols = tuple(c["key"] for c in LOAN_COLS)
        self._ln_tree = ttk.Treeview(p, columns=cols, show="headings", height=14)
        for col_def in LOAN_COLS:
            self._ln_tree.heading(col_def["key"], text=col_def["label"])
            self._ln_tree.column(col_def["key"],  width=col_def["width"], anchor="center")

        vsb = ttk.Scrollbar(p, orient="vertical", command=self._ln_tree.yview)
        self._ln_tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right",fill="y",padx=(0,8))
        self._ln_tree.pack(fill="both",expand=True,padx=16,pady=12)

        info = frm(p, C["card"]); info.pack(fill="x",padx=16,pady=(0,8))
        lbl(info,"💡  กรุณาคืนอุปกรณ์ก่อนหมดเวลาจองนะครับ",
            fg=C["warning"],font="sm",bg=C["card"]).pack(padx=12,pady=8,side="left")

        bot = frm(p, C["surface"]); bot.pack(fill="x",side="bottom"); sep(bot)
        btn(bot,"✅  คืนอุปกรณ์ที่เลือก",self._return_selected_loan,
            C["success"],padx=20,pady=10).pack(pady=12)

    def _load_my_loans(self):
        def cb(res):
            if not res or not res.get("ok"): return
            loans = res.get("loans",[])
            for r in self._ln_tree.get_children(): self._ln_tree.delete(r)
            for ln in loans:
                names = ", ".join(ln.get("item_names", ln.get("items",[])))
                self._ln_tree.insert("","end", values=(
                    ln["loan_id"],
                    f"{COURT_EMOJIS.get(ln['court_type'],'')} {ln['court_type']}",
                    ln["date"], ln["time_slot"],
                    names,
                    ln.get("borrowed_at",""),
                ))
            if not loans:
                self._ln_tree.insert("","end",values=("—","ไม่มีการยืม","","","",""))
        self._post({"cmd":CMD_MY_LOANS}, cb)

    def _return_selected_loan(self):
        sel = self._ln_tree.selection()
        if not sel: messagebox.showwarning("แจ้งเตือน","กรุณาเลือกรายการก่อน"); return
        lid = self._ln_tree.item(sel[0])["values"][0]
        if lid == "—": return
        if not messagebox.askyesno("ยืนยัน",f"คืนอุปกรณ์ {lid}?"): return
        def cb(res):
            if not res: return
            if res.get("ok"):
                self._notif("✅ คืนอุปกรณ์เรียบร้อย", C["success"])
                self._load_my_loans()
            else: messagebox.showerror("ผิดพลาด", res["msg"])
        self._post({"cmd":CMD_RETURN_EQUIPMENT,"loan_id":lid}, cb)

    def _build_page_weather(self):
        p = frm(self._content); self._pages["tab_weather"] = p
        hdr = frm(p, C["surface"]); hdr.pack(fill="x")
        ih  = frm(hdr, C["surface"]); ih.pack(fill="x",padx=20,pady=14)
        lbl(ih,"สภาพอากาศ — มทส",font="lg",bg=C["surface"]).pack(side="left")
        btn(ih,"🔄",self._load_weather,C["elevated"],fg=C["sub"],padx=8,pady=5).pack(side="right")
        sep(p)

        sf = ScrollFrame(p); sf.pack(fill="both",expand=True)
        body = sf.inner

        cur = frm(body, C["card"]); cur.pack(fill="x",padx=16,pady=12)
        self._w_desc = lbl(cur,"—",font="xl",bg=C["card"]); self._w_desc.pack(padx=20,pady=(14,8))

        # Grid สร้างจาก WEATHER_STATS
        grid = frm(cur, C["card"]); grid.pack(padx=20,pady=(0,14),fill="x")
        self._wl = {}
        for i, stat in enumerate(WEATHER_STATS):
            r, c = divmod(i, 3)
            box = frm(grid, C["elevated"]); box.grid(row=r,column=c,padx=5,pady=5,sticky="nsew")
            grid.columnconfigure(c, weight=1)
            lbl(box,stat["label"],fg=C["sub"],bg=C["elevated"],font="sm").pack(padx=14,pady=(10,2))
            v = lbl(box,"—",font="lg",bg=C["elevated"]); v.pack(padx=14,pady=(0,10))
            self._wl[stat["key"]] = (v, stat)

        self._w_src = lbl(body,"",fg=C["dim"],font="sm"); self._w_src.pack(anchor="w",padx=18)
        lbl(body,"📊 พยากรณ์รายชั่วโมง (48 ชั่วโมง)",fg=C["sub"],font="sm").pack(anchor="w",padx=18,pady=(14,6))

        h_cv = tk.Canvas(body,bg=C["bg"],height=120,highlightthickness=0)
        h_sb = tk.Scrollbar(body,orient="horizontal",command=h_cv.xview)
        h_cv.configure(xscrollcommand=h_sb.set)
        h_sb.pack(fill="x",padx=16); h_cv.pack(fill="x",padx=16,pady=(0,16))
        self._hourly_frame = frm(h_cv)
        h_cv.create_window((0,0),window=self._hourly_frame,anchor="nw")
        self._hourly_frame.bind("<Configure>",lambda e: h_cv.configure(scrollregion=h_cv.bbox("all")))

    def _load_weather(self):
        # ใช้ cache ถ้าข้อมูลยังสด (< 5 นาที) — ไม่ต้องดึงซ้ำบ่อย
        if self._weather_cache and (time.time() - self._weather_ts) < 300:
            self._update_weather_ui(self._weather_cache)
            return
        def cb(res):
            if res and res.get("ok"):
                self._weather_cache = res
                self._weather_ts = time.time()
                self._update_weather_ui(res)
        self._post({"cmd":CMD_WEATHER}, cb)

    def _update_weather_ui(self, res):
        w = res["weather"]
        self._w_desc.config(text=w["description"])

        # อัปเดตทุก stat จาก WEATHER_STATS definition
        for widget, stat in self._wl.values():
            text  = stat["fmt"](w)
            color = stat["color"](w) if "color" in stat else C["text"]
            widget.config(text=text, fg=color)

        self._w_src.config(text=f"แหล่งข้อมูล: {w['source']}  ·  {w['updated_at']}")

        for widget in self._hourly_frame.winfo_children(): widget.destroy()
        for h in res.get("hourly",[])[:48]:
            rc  = rain_color(h["rain_prob"])
            bbg = CA["danger_dim"] if h["rain_prob"]>=50 else (CA["warning_dim"] if h["rain_prob"]>=30 else C["card"])
            box = frm(self._hourly_frame,bbg); box.pack(side="left",padx=2)
            lbl(box,h["time"][11:16],fg=C["sub"],bg=bbg,font="sm").pack(padx=8,pady=(8,2))
            lbl(box,f"{h['temperature']:.0f}°",fg=C["text"],bg=bbg,font="lg").pack(padx=8)
            lbl(box,rain_label(h["rain_prob"]),fg=rc,bg=bbg,font="sm").pack(padx=8,pady=(2,8))

    # ── Logout / Close ────────────────────────────

    def _handle_disconnect(self):
        """เรียกเมื่อ socket ตายกะทันหัน (kick / server ปิด / network error)"""
        messagebox.showwarning(
            "หลุดการเชื่อมต่อ",
            "การเชื่อมต่อถูกตัด\nอาจถูก Kick หรือ Server ปิด\nกรุณา Login ใหม่"
        )
        self._do_logout()

    def _handle_force_logout(self, msg: str):
        """เรียกเมื่อได้รับ force_logout message (kick/ban)"""
        messagebox.showwarning("ถูกออกจากระบบ", msg + "\nกรุณา Login ใหม่")
        self._do_logout()

    def _logout(self):
        if not messagebox.askyesno("ออกจากระบบ","ต้องการออกจากระบบ?"): return
        self._post({"cmd":CMD_LOGOUT}, None)
        # flush jobs queue แล้วรอ worker ส่ง logout เสร็จ
        self.after(300, self._do_logout)

    def _do_logout(self):
        if self.sock:
            try: self.sock.close()
            except: pass
        self.sock=None; self.username=None
        # clear jobs queue
        while not self._jobs.empty():
            try: self._jobs.get_nowait()
            except: break
        self._show_login()

    # ══════════════════════════════════════════════
    # AI CHATBOT PAGE (Groq API + Tool Calling)
    # ══════════════════════════════════════════════

    _GROQ_TOOLS = [
        {
            "type": "function",
            "function": {
                "name": "book_court",
                "description": "จองสนามกีฬา ใช้เมื่อผู้ใช้ต้องการจองสนาม",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "court_type": {
                            "type": "string",
                            "enum": ["ฟุตบอล", "บาสเกตบอล", "แบดมินตัน", "วอลเล่บอล"],
                            "description": "ประเภทสนาม"
                        },
                        "date": {
                            "type": "string",
                            "description": "วันที่จองรูปแบบ YYYY-MM-DD"
                        },
                        "time_slot": {
                            "type": "string",
                            "enum": ["06:00-08:00","08:00-10:00","10:00-12:00","12:00-14:00",
                                     "14:00-16:00","16:00-18:00","18:00-20:00","20:00-22:00"],
                            "description": "ช่วงเวลาจอง"
                        }
                    },
                    "required": ["court_type", "date", "time_slot"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "cancel_booking",
                "description": "ยกเลิกการจองสนาม",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "booking_id": {
                            "type": "string",
                            "description": "หมายเลขการจองที่ต้องการยกเลิก"
                        }
                    },
                    "required": ["booking_id"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_my_bookings",
                "description": "ดูรายการจองทั้งหมดของผู้ใช้",
                "parameters": {"type": "object", "properties": {}}
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_schedule",
                "description": "ดูตารางว่างของสนาม",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "court_type": {
                            "type": "string",
                            "enum": ["ฟุตบอล", "บาสเกตบอล", "แบดมินตัน", "วอลเล่บอล"]
                        },
                        "date": {
                            "type": "string",
                            "description": "วันที่รูปแบบ YYYY-MM-DD (ถ้าไม่ระบุใช้วันนี้)"
                        }
                    },
                    "required": ["court_type"]
                }
            }
        }
    ]

    def _load_groq_key(self) -> str:
        """อ่าน GROQ_API_KEY จาก .env ในโฟลเดอร์เดียวกับสคริปต์"""
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("GROQ_API_KEY"):
                        _, _, val = line.partition("=")
                        return val.strip().strip('"').strip("'")
        except FileNotFoundError:
            pass
        except Exception:
            pass
        # fallback: อ่านจาก environment variable ถ้ามี
        return os.environ.get("GROQ_API_KEY", "")

    def _save_groq_key(self, key: str):
        pass  # ไม่ใช้แล้ว — จัดการผ่าน .env

    def _build_page_ai(self):
        p = frm(self._content); self._pages["tab_ai"] = p

        # ── Header ───────────────────────────────
        hdr = frm(p, C["surface"]); hdr.pack(fill="x")
        ih = frm(hdr, C["surface"]); ih.pack(fill="x", padx=20, pady=14)
        lbl(ih, "🤖  AI ผู้ช่วย", font="lg", bg=C["surface"]).pack(side="left")
        btn(ih, "🗑 ล้างแชท", self._ai_clear_chat,
            C["elevated"], fg=C["sub"], padx=8, pady=5).pack(side="right")
        sep(p)

        # ── Chat area ─────────────────────────────
        chat_outer = frm(p); chat_outer.pack(fill="both", expand=True, padx=16, pady=10)

        self._ai_canvas = tk.Canvas(chat_outer, bg=C["bg"], highlightthickness=0)
        vsb = tk.Scrollbar(chat_outer, orient="vertical", command=self._ai_canvas.yview)
        self._ai_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._ai_canvas.pack(side="left", fill="both", expand=True)

        self._ai_msg_frame = frm(self._ai_canvas, C["bg"])
        self._ai_canvas_win = self._ai_canvas.create_window((0, 0), window=self._ai_msg_frame, anchor="nw")

        def _on_frame_configure(e):
            self._ai_canvas.configure(scrollregion=self._ai_canvas.bbox("all"))
        def _on_canvas_configure(e):
            self._ai_canvas.itemconfig(self._ai_canvas_win, width=e.width)
        self._ai_msg_frame.bind("<Configure>", _on_frame_configure)
        self._ai_canvas.bind("<Configure>", _on_canvas_configure)

        # Mouse wheel scroll
        def _on_wheel(e):
            self._ai_canvas.yview_scroll(int(-1*(e.delta/120)), "units")
        self._ai_canvas.bind("<MouseWheel>", _on_wheel)

        # ── Input bar ─────────────────────────────
        sep(p)
        inp_bar = frm(p, C["surface"]); inp_bar.pack(fill="x", padx=16, pady=10)
        self._ai_input = tk.Text(inp_bar, bg=C["elevated"], fg=C["text"],
                                 insertbackground=C["text"], font=F["md"],
                                 bd=0, height=2, wrap="word", width=1)
        self._ai_input.pack(side="left", fill="x", expand=True, padx=(0, 10))
        self._ai_input.bind("<Return>", self._ai_on_enter)
        self._ai_input.bind("<Shift-Return>", lambda e: None)  # allow newline with shift

        self._ai_send_btn = btn(inp_bar, "ส่ง ➤", self._ai_send,
                                C["accent"], fg="#fff", padx=14, pady=8)
        self._ai_send_btn.pack(side="right")

        # ── Welcome message ──────────────────────
        self._ai_add_bubble(
            "สวัสดี! 👋 ฉันคือ AI ผู้ช่วยของ Sports Queue\n\n"
            "คุณสามารถ:\n"
            "• ถามเรื่องสนามที่ว่าง เช่น \"สนามแบดมินตันวันนี้ว่างไหม\"\n"
            "• สั่งจองสนาม เช่น \"จองสนามฟุตบอล พรุ่งนี้ 08:00-10:00\"\n"
            "• ดูการจองของฉัน เช่น \"ฉันจองอะไรไว้บ้าง\"\n"
            "• ยกเลิกการจอง เช่น \"ยกเลิกการจองหมายเลข BK001\"",
            is_ai=True
        )

    def _on_enter_ai(self):
        pass  # ไม่ต้อง reload เมื่อเข้า tab นี้

    def _ai_clear_chat(self):
        self._ai_history.clear()
        for w in self._ai_msg_frame.winfo_children():
            w.destroy()
        self._ai_add_bubble("ล้างแชทแล้ว 🗑 เริ่มการสนทนาใหม่ได้เลย!", is_ai=True)

    def _ai_on_enter(self, event):
        if not (event.state & 0x1):  # Shift not held
            self._ai_send()
            return "break"

    def _ai_add_bubble(self, text: str, is_ai: bool):
        """วาด message bubble ใน chat frame"""
        row = frm(self._ai_msg_frame, C["bg"])
        row.pack(fill="x", padx=10, pady=3)

        if is_ai:
            # AI bubble — ซ้าย
            outer = frm(row, C["bg"]); outer.pack(anchor="w", fill="x")
            icon = lbl(outer, "🤖", bg=C["bg"], font="sm"); icon.pack(side="left", anchor="n", padx=(0,6), pady=4)
            bubble = frm(outer, C["card"]); bubble.pack(side="left", anchor="w")
            tk.Label(bubble, text=text, fg=C["text"], bg=C["card"], font=F["sm"],
                     wraplength=480, justify="left", anchor="w",
                     padx=14, pady=10).pack()
        else:
            # User bubble — ขวา
            outer = frm(row, C["bg"]); outer.pack(anchor="e")
            bubble = frm(outer, mix(C["accent"], a=0.35)); bubble.pack(side="right")
            tk.Label(bubble, text=text, fg=C["text"], bg=mix(C["accent"], a=0.35),
                     font=F["sm"], wraplength=420, justify="right",
                     anchor="e", padx=14, pady=10).pack()

        self.after(50, lambda: self._ai_canvas.yview_moveto(1.0))

    def _ai_add_typing(self):
        """แสดง ... (กำลังพิมพ์) placeholder"""
        row = frm(self._ai_msg_frame, C["bg"])
        row.pack(fill="x", padx=10, pady=3)
        outer = frm(row, C["bg"]); outer.pack(anchor="w", fill="x")
        icon = lbl(outer, "🤖", bg=C["bg"], font="sm"); icon.pack(side="left", anchor="n", padx=(0,6), pady=4)
        bubble = frm(outer, C["card"]); bubble.pack(side="left", anchor="w")
        self._ai_typing_lbl = tk.Label(bubble, text="⏳ กำลังคิด...",
                                       fg=C["sub"], bg=C["card"], font=F["sm"],
                                       padx=14, pady=10)
        self._ai_typing_lbl.pack()
        self._ai_typing_row = row
        self.after(50, lambda: self._ai_canvas.yview_moveto(1.0))

    def _ai_remove_typing(self):
        try:
            self._ai_typing_row.destroy()
        except Exception:
            pass

    def _ai_send(self):
        text = self._ai_input.get("1.0", "end").strip()
        if not text:
            return
        if not self._groq_api_key:
            self._ai_add_bubble("⚠️ ไม่พบ GROQ_API_KEY ใน .env\nกรุณาเพิ่ม GROQ_API_KEY=gsk_xxx ในไฟล์ .env แล้วรีสตาร์ท", is_ai=True)
            return
        # Clear input
        self._ai_input.delete("1.0", "end")
        # Show user bubble
        self._ai_add_bubble(text, is_ai=False)
        # Add to history
        self._ai_history.append({"role": "user", "content": text})
        # Disable send button
        self._ai_send_btn.config(state="disabled")
        self._ai_add_typing()
        # Call Groq in background thread
        threading.Thread(target=self._ai_call_groq, daemon=True).start()

    def _ai_call_groq(self):
        """เรียก Groq API ใน background thread (ไม่บล็อก UI)"""
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        day_after = (date.today() + timedelta(days=2)).isoformat()

        system_prompt = (
            f"คุณคือ AI ผู้ช่วยสำหรับระบบจองสนามกีฬา Sports Queue ของมหาวิทยาลัยเทคโนโลยีสุรนารี (มทส)\n"
            f"ผู้ใช้ปัจจุบัน: {self.user_name} (username: {self.username})\n"
            f"วันนี้: {today} | พรุ่งนี้: {tomorrow} | มะรืน: {day_after}\n\n"
            f"สนามที่มีให้จอง: ฟุตบอล, บาสเกตบอล, แบดมินตัน, วอลเล่บอล\n"
            f"ช่วงเวลา: 06:00-08:00, 08:00-10:00, 10:00-12:00, 12:00-14:00, "
            f"14:00-16:00, 16:00-18:00, 18:00-20:00, 20:00-22:00\n"
            f"จองล่วงหน้าได้สูงสุด 3 วัน (วันนี้ถึง {day_after})\n\n"
            f"ตอบเป็นภาษาไทย สุภาพ กระชับ\n"
            f"ถ้าต้องการข้อมูลเพิ่มเพื่อจอง (เช่น ยังไม่ระบุวันที่หรือเวลา) ให้ถามผู้ใช้ก่อน\n"
            f"ก่อนจองจริงๆ ให้บอกรายละเอียดและถามยืนยันจากผู้ใช้ก่อนเสมอ"
        )

        messages = [{"role": "system", "content": system_prompt}] + self._ai_history

        payload = json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "tools": self._GROQ_TOOLS,
            "tool_choice": "auto",
            "max_tokens": 1024,
            "temperature": 0.7
        }).encode("utf-8")

        result = self._groq_post(payload)
        if isinstance(result, str):
            self.after(0, lambda m=result: self._ai_finish(None, error=m))
        else:
            self.after(0, lambda r=result: self._ai_finish(r))

    def _ai_finish(self, result, error=None):
        """เรียกจาก main thread หลัง Groq ตอบกลับ"""
        self._ai_remove_typing()
        self._ai_send_btn.config(state="normal")

        if error:
            self._ai_add_bubble(error, is_ai=True)
            return

        choice = result.get("choices", [{}])[0]
        msg = choice.get("message", {})

        # ── Tool calls ────────────────────────────
        tool_calls = msg.get("tool_calls", [])
        if tool_calls:
            # เก็บ assistant message (with tool_calls) เข้า history
            self._ai_history.append(msg)
            for tc in tool_calls:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except Exception:
                    fn_args = {}
                tool_call_id = tc["id"]
                self._ai_execute_tool(fn_name, fn_args, tool_call_id)
            return

        # ── Normal text response ──────────────────
        text = msg.get("content", "").strip()
        if text:
            self._ai_history.append({"role": "assistant", "content": text})
            self._ai_add_bubble(text, is_ai=True)

    def _ai_execute_tool(self, fn_name: str, args: dict, tool_call_id: str):
        """Execute tool และส่งผลกลับให้ Groq เพื่อสร้าง final reply"""
        if fn_name == "book_court":
            court_type = args.get("court_type", "")
            date_str   = args.get("date", "")
            time_slot  = args.get("time_slot", "")

            def cb(res):
                if res and res.get("ok"):
                    bid = res.get("booking_id", "")
                    tool_result = f"จองสำเร็จ ✅ หมายเลขการจอง: {bid} | สนาม: {court_type} | วันที่: {date_str} | เวลา: {time_slot}"
                    self._load_my_bookings_cache()
                    self._load_schedule()
                else:
                    msg_txt = res.get("msg", "ไม่ทราบสาเหตุ") if res else "ไม่ได้รับการตอบกลับ"
                    tool_result = f"จองไม่สำเร็จ ❌: {msg_txt}"
                self._ai_tool_reply(tool_call_id, fn_name, tool_result)

            self._notif(f"🤖 AI กำลังจองสนาม{court_type} {time_slot}...", C["accent"])
            self._post({"cmd": CMD_BOOK, "court_type": court_type,
                        "date": date_str, "time_slot": time_slot}, cb)

        elif fn_name == "cancel_booking":
            booking_id = args.get("booking_id", "")

            def cb(res):
                if res and res.get("ok"):
                    tool_result = f"ยกเลิกการจองหมายเลข {booking_id} สำเร็จ ✅"
                    self._load_my_bookings_cache()
                    self._load_schedule()
                else:
                    msg_txt = res.get("msg", "ไม่ทราบสาเหตุ") if res else "ไม่ได้รับการตอบกลับ"
                    tool_result = f"ยกเลิกไม่สำเร็จ ❌: {msg_txt}"
                self._ai_tool_reply(tool_call_id, fn_name, tool_result)

            self._post({"cmd": CMD_CANCEL, "booking_id": booking_id}, cb)

        elif fn_name == "get_my_bookings":
            def cb(res):
                if res and res.get("ok"):
                    bks = res.get("bookings", [])
                    if not bks:
                        tool_result = "ไม่มีการจองในขณะนี้"
                    else:
                        lines = [f"• {b['booking_id']} | {b['court_type']} | {b['date']} {b['time_slot']}"
                                 for b in bks]
                        tool_result = "รายการจองของคุณ:\n" + "\n".join(lines)
                else:
                    tool_result = "ไม่สามารถดึงข้อมูลการจองได้"
                self._ai_tool_reply(tool_call_id, fn_name, tool_result)

            self._post({"cmd": CMD_MY_BOOKINGS}, cb)

        elif fn_name == "get_schedule":
            court_type = args.get("court_type", "")
            date_str   = args.get("date", date.today().isoformat())

            def cb(res):
                if res and res.get("ok"):
                    sched = res.get("schedule", {}).get(court_type, {})
                    free  = [s for s, info in sched.items() if not info.get("booked")]
                    booked = [s for s, info in sched.items() if info.get("booked")]
                    tool_result = (
                        f"ตาราง {court_type} วันที่ {date_str}:\n"
                        f"ว่าง: {', '.join(free) if free else 'ไม่มี'}\n"
                        f"จองแล้ว: {', '.join(booked) if booked else 'ไม่มี'}"
                    )
                else:
                    tool_result = f"ดึงตารางสนาม{court_type}ไม่ได้"
                self._ai_tool_reply(tool_call_id, fn_name, tool_result)

            self._post({"cmd": CMD_GET_SCHEDULE, "date": date_str}, cb)

        else:
            self._ai_tool_reply(tool_call_id, fn_name, f"ไม่รู้จัก tool: {fn_name}")

    def _ai_tool_reply(self, tool_call_id: str, fn_name: str, result_text: str):
        """ส่งผลลัพธ์ของ tool กลับให้ Groq เพื่อสร้าง final response"""
        # เพิ่ม tool result เข้า history
        self._ai_history.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": fn_name,
            "content": result_text
        })
        # แสดง typing และเรียก Groq อีกรอบ
        self._ai_send_btn.config(state="disabled")
        self._ai_add_typing()
        threading.Thread(target=self._ai_call_groq_followup, daemon=True).start()

    def _ai_call_groq_followup(self):
        """เรียก Groq อีกรอบหลังได้ผลลัพธ์จาก tool"""
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        day_after = (date.today() + timedelta(days=2)).isoformat()

        system_prompt = (
            f"คุณคือ AI ผู้ช่วยสำหรับระบบจองสนามกีฬา Sports Queue ของมทส\n"
            f"ผู้ใช้ปัจจุบัน: {self.user_name} (username: {self.username})\n"
            f"วันนี้: {today} | พรุ่งนี้: {tomorrow} | มะรืน: {day_after}\n"
            f"ตอบเป็นภาษาไทย สุภาพ กระชับ"
        )
        messages = [{"role": "system", "content": system_prompt}] + self._ai_history

        payload = json.dumps({
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "max_tokens": 1024,
            "temperature": 0.7
        }).encode("utf-8")

        result = self._groq_post(payload)
        if isinstance(result, str):
            self.after(0, lambda m=result: self._ai_finish(None, error=m))
        else:
            self.after(0, lambda r=result: self._ai_finish(r))

    def _groq_post(self, payload: bytes):
        """POST ไปยัง Groq API — คืน dict ถ้าสำเร็จ หรือ error string ถ้าล้มเหลว
        ใช้ requests ถ้ามี ไม่งั้น fallback ไป urllib พร้อม headers ครบ"""
        url     = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {self._groq_api_key}",
            "User-Agent":    "Mozilla/5.0 (compatible; SportsQueue/1.0)",
            "Accept":        "application/json",
        }
        # ── ลอง requests ก่อน (ถ้าติดตั้งไว้) ─────────────────
        try:
            import requests as _req
            resp = _req.post(url, data=payload, headers=headers, timeout=30)
            if resp.ok:
                return resp.json()
            try:
                msg = resp.json().get("error", {}).get("message", resp.text[:200])
            except Exception:
                msg = resp.text[:200]
            return f"❌ Groq Error {resp.status_code}: {msg}"
        except ImportError:
            pass          # ไม่มี requests — ใช้ urllib แทน
        except Exception as e:
            return f"❌ {e}"

        # ── fallback: urllib ────────────────────────────────────
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                msg = json.loads(body).get("error", {}).get("message", body[:200])
            except Exception:
                msg = body[:200]
            return f"❌ Groq Error {e.code}: {msg}"
        except Exception as e:
            return f"❌ {e}"

    # ══════════════════════════════════════════════
    # END AI CHATBOT
    # ══════════════════════════════════════════════

    def _on_close(self):
        self._running=False
        if self.sock:
            try: self.sock.close()
            except: pass
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
