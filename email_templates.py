"""
email_templates.py — โหลด CSS/HTML จากไฟล์ใน assets/
แก้หน้าตาอีเมลได้ที่ assets/email.css และ assets/*.html
"""

import os
import logging
from string import Template

log    = logging.getLogger(__name__)
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

# Fallback templates ใช้เมื่อไฟล์ assets หาย (เช่นรัน unit test หรือ deploy ไม่ครบ)
_FALLBACK_CSS             = ""
_FALLBACK_CONFIRM_HTML    = "<html><body>$booking_section</body></html>"
_FALLBACK_RAIN_ALERT_HTML = "<html><body>Rain alert $rain_prob% on $date $time_slot $booking_section</body></html>"

def _load(filename: str, fallback: str = "") -> str:
    path = os.path.join(ASSETS, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        log.warning("email_templates: ไม่พบไฟล์ %s — ใช้ fallback template", path)
        return fallback
    except OSError as exc:
        log.error("email_templates: อ่านไฟล์ %s ล้มเหลว: %s — ใช้ fallback template", path, exc)
        return fallback

# โหลดครั้งเดียวตอน import (ไม่ต้องอ่านไฟล์ซ้ำทุกครั้งที่ส่งอีเมล)
_CSS            = _load("email.css",        _FALLBACK_CSS)
_CONFIRM_TPL    = Template(_load("confirm.html",    _FALLBACK_CONFIRM_HTML))
_RAIN_ALERT_TPL = Template(_load("rain_alert.html", _FALLBACK_RAIN_ALERT_HTML))

# ── Weather helpers ───────────────────────────────────────────────────────────

_ADVICE = [
    (70, "#dc2626", "🌧️ ฝนตกหนัก — แนะนำยกเลิกหรือเลื่อนเวลา"),
    (50, "#ea580c", "🌦️ มีโอกาสฝน — เตรียมอุปกรณ์กันฝนไว้"),
    (30, "#d97706", "🌂 โอกาสฝนเล็กน้อย — ติดตามสภาพอากาศ"),
]
_COURT_COLOR = {"ฟุตบอล":"#16a34a","บาสเกตบอล":"#ea580c","แบดมินตัน":"#7c3aed","วอลเล่บอล":"#0284c7"}
_COURT_EMOJI = {"ฟุตบอล":"⚽","บาสเกตบอล":"🏀","แบดมินตัน":"🏸","วอลเล่บอล":"🏐"}

def _advice(rain, uv, temp):
    for threshold, color, text in _ADVICE:
        if rain >= threshold:
            return color, text
    if uv   >= 8:  return "#b45309", "☀️ UV สูงมาก — ทาครีมกันแดด"
    if temp >= 37: return "#b45309", "🌡️ ร้อนจัด — ดื่มน้ำมากๆ"
    return "#16a34a", "✅ สภาพอากาศดี เหมาะสำหรับกีฬากลางแจ้ง"

def _booking_section(bk, sw):
    """HTML block รายละเอียดการจอง + สภาพอากาศ (ใช้ร่วมกันทั้ง 2 template)"""
    color      = _COURT_COLOR.get(bk["court_type"], "#3b82f6")
    emoji      = _COURT_EMOJI.get(bk["court_type"], "🏟️")
    adv_c, adv = _advice(sw["rain_prob"], sw["uv_index"], sw["temperature"])
    rc         = "#ef4444" if sw["rain_prob"]>=50 else ("#f59e0b" if sw["rain_prob"]>=30 else "#22c55e")

    return f"""
<div class="card">
  <div class="lbl">รายละเอียดการจอง</div>
  <div style="display:flex;align-items:center;gap:10px;margin:12px 0">
    <div style="width:40px;height:40px;border-radius:8px;background:{color}22;
                display:flex;align-items:center;justify-content:center;font-size:22px">{emoji}</div>
    <div>
      <div style="font-size:18px;font-weight:700;color:#f1f5f9">{bk['court_type']}</div>
      <div style="font-size:12px;color:#64748b">{bk['date']}</div>
    </div>
  </div>
  <div class="row">
    <div class="col"><div class="k">⏰ ช่วงเวลา</div><div class="v">{bk['time_slot']}</div></div>
    <div class="col"><div class="k">👤 ผู้จอง</div><div class="v">{bk['user_name']}</div></div>
  </div>
  <div class="id">ID: {bk['booking_id']}</div>
</div>
<div class="card">
  <div class="lbl">สภาพอากาศช่วง {bk['time_slot']}</div>
  <div class="row">
    <div class="col"><div class="k">🌡️ อุณหภูมิ</div><div class="v">{sw['temperature']:.1f}°C</div></div>
    <div class="col"><div class="k">💧 ความชื้น</div><div class="v">{sw['humidity']:.0f}%</div></div>
    <div class="col"><div class="k">💨 ลม</div><div class="v">{sw['wind_speed']:.1f} m/s</div></div>
  </div>
  <div class="col" style="margin-top:4px">
    <div class="k">🌧️ โอกาสฝน</div>
    <div style="display:flex;align-items:center;gap:8px;margin-top:4px">
      <div style="flex:1;height:6px;background:#1e293b;border-radius:3px;overflow:hidden">
        <div style="width:{int(sw['rain_prob'])}%;height:100%;background:{rc}"></div>
      </div>
      <b style="color:{rc}">{sw['rain_prob']:.0f}%</b>
    </div>
  </div>
  <div class="advice" style="background:{adv_c}18;border-left:3px solid {adv_c}">{adv}</div>
</div>"""

# ── Public API ────────────────────────────────────────────────────────────────

def confirm_email(bk: dict, sw: dict) -> str:
    warn = (f'<div class="warn">⚠️ คาดว่ามีฝน {sw["rain_prob"]:.0f}% '
            f'ในช่วงนี้ — พิจารณาเลือกช่วงเวลาอื่น</div>') if sw["rain_prob"] >= 50 else ""
    return _CONFIRM_TPL.substitute(
        css=_CSS, warn=warn, booking_section=_booking_section(bk, sw)
    )

def rain_alert_email(bk: dict, rain_prob: float, sw: dict) -> str:
    return _RAIN_ALERT_TPL.substitute(
        css=_CSS, rain_prob=f"{rain_prob:.0f}",
        time_slot=bk["time_slot"], date=bk["date"],
        booking_section=_booking_section(bk, sw)
    )
