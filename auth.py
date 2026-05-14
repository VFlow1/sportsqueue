# auth.py — Authentication helpers
# ไม่เก็บ plain-text password ที่ใดเลย ใช้ PBKDF2-HMAC-SHA256

from __future__ import annotations   # ← แก้ปัญหา Optional ใช้ก่อน import

import hashlib
import hmac
import os
from typing import Optional


def hash_password(password: str) -> str:
    """Hash password ด้วย PBKDF2-HMAC-SHA256 + random salt
    รูปแบบ: pbkdf2$<salt_hex>$<hash_hex>
    """
    salt = os.urandom(16)
    dk   = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """เปรียบเทียบ password กับค่าที่เก็บไว้
    รองรับทั้ง pbkdf2 (ใหม่) และ sha256 plain (เก่า) เพื่อ backward compat
    """
    if not stored:
        return False
    try:
        if stored.startswith("pbkdf2$"):
            _, salt_hex, hash_hex = stored.split("$")
            salt     = bytes.fromhex(salt_hex)
            expected = bytes.fromhex(hash_hex)
            dk       = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 260_000)
            return hmac.compare_digest(dk, expected)
        else:
            # legacy SHA-256 plain — backward compat
            legacy = hashlib.sha256(password.encode()).hexdigest()
            return hmac.compare_digest(legacy.encode(), stored.encode())
    except Exception:
        return False


def needs_upgrade(stored: str) -> bool:
    """คืน True ถ้า hash ยังเป็น legacy (ไม่ใช่ pbkdf2) และควร migrate"""
    return bool(stored) and not stored.startswith("pbkdf2$")


def migrate_if_needed(stored: str, password: str) -> Optional[str]:
    """ถ้ายังเป็น legacy sha256 และ password ถูกต้อง → rehash เป็น pbkdf2
    คืน hash ใหม่ถ้า migrate, คืน None ถ้าไม่ต้อง migrate
    """
    if needs_upgrade(stored) and verify_password(password, stored):
        return hash_password(password)
    return None
