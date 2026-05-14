# network.py — Network helpers สำหรับทั้ง server และ client
# ครอบ try/except ทุก call, timeout ทุก socket

import socket
import logging
from typing import Optional

from constants import send_msg, recv_msg
from config    import HOST, PORT, SOCKET_TIMEOUT

log = logging.getLogger("network")


def make_client_socket() -> Optional[socket.socket]:
    """สร้าง TCP socket ที่พร้อมใช้งาน พร้อม timeout"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(SOCKET_TIMEOUT)
        s.connect((HOST, PORT))
        return s
    except ConnectionRefusedError:
        log.error("เชื่อมต่อ %s:%s ไม่ได้ — server ไม่ได้เปิดอยู่", HOST, PORT)
    except TimeoutError:
        log.error("เชื่อมต่อ %s:%s timeout", HOST, PORT)
    except Exception as e:
        log.error("เชื่อมต่อล้มเหลว: %s", e)
    return None


def safe_send(sock: socket.socket, data: dict) -> bool:
    """ส่ง message — คืน True ถ้าสำเร็จ"""
    try:
        send_msg(sock, data)
        return True
    except Exception as e:
        log.warning("safe_send ล้มเหลว: %s", e)
        return False


def safe_recv(sock: socket.socket, timeout: float = None) -> Optional[dict]:
    """รับ message — คืน None ถ้า error หรือ connection หลุด"""
    try:
        if timeout is not None:
            old = sock.gettimeout()
            sock.settimeout(timeout)
        msg = recv_msg(sock)
        if timeout is not None:
            sock.settimeout(old)
        return msg
    except TimeoutError:
        log.debug("safe_recv timeout")
        return None
    except Exception as e:
        log.warning("safe_recv ล้มเหลว: %s", e)
        return None


def request(sock: socket.socket, data: dict, timeout: float = None) -> Optional[dict]:
    """ส่ง request แล้วรอ response ใน call เดียว"""
    if not safe_send(sock, data):
        return None
    return safe_recv(sock, timeout)
