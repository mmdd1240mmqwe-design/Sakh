#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VANTA PERSIA BOT — v2 (تک‌فایلی: bot.py)
pip install rubka python-dotenv Pillow
cp .env.example .env
python bot.py
"""

import os
import re
import time
import random
import sqlite3
import asyncio
import threading
import traceback
from datetime import datetime, date, timedelta, timezone
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

try:
    from zoneinfo import ZoneInfo
    IRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    IRAN_TZ = timezone(timedelta(hours=3, minutes=30))

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from rubka import Robot
from rubka.context import Message



# ==============================================================================
# بخش ۱: تنظیمات
# ==============================================================================
# اگه پکیج python-dotenv نصب باشه، فایل .env رو خودکار می‌خونه.
def _get_env(name, default=None, required=False):
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(
            f"❌ متغیر محیطی «{name}» ست نشده. یه فایل .env بساز و توکن/آیدی‌ها رو توش بذار "
            f"(نمونه‌ش تو .env.example هست)."
        )
    return val


# ---------------------------------------------------------------------------
# توکن ربات - از BotFather روبیکا می‌گیری
# ---------------------------------------------------------------------------
BOT_TOKEN = _get_env("BOT_TOKEN", required=True)

# ---------------------------------------------------------------------------
# ادمین‌های اصلی ربات (uid واقعی، نه یوزرنیم) - جدا شده با کاما تو .env
# مثال تو .env:  ADMIN_IDS=u0Hx...,u0I1...
# ---------------------------------------------------------------------------
_raw_admins = _get_env("ADMIN_IDS", default="")
ADMIN_IDS = [a.strip() for a in _raw_admins.split(",") if a.strip()]

# ---------------------------------------------------------------------------
# برندینگ ربات
# ---------------------------------------------------------------------------
BOT_BRAND = _get_env("BOT_BRAND", default="VANTA PERSIA")
BOT_NAME_TRIGGER = _get_env("BOT_NAME_TRIGGER", default="پرسیا")
OWNER_USERNAME = _get_env("OWNER_USERNAME", default="@Kaveroxkop7")
OWNER_LINK = f"https://rubika.ir/{OWNER_USERNAME.lstrip('@')}"
GROUP_LINK = _get_env("GROUP_LINK", default="https://rubika.ir/joing/BAGGDEDGA0FBBEEMSRWFTVMZDCOXSOOH")

# ---------------------------------------------------------------------------
# رمز عبور «ادمین سکه» - فقط اجازه‌ی اضافه/کم‌کردن سکه داره، هیچ قدرت دیگه‌ای نه
# حتماً تو .env عوضش کن اگه می‌خوای امن‌تر باشه
# ---------------------------------------------------------------------------
COIN_ADMIN_PASSWORD = _get_env("COIN_ADMIN_PASSWORD", default="VANTA-COIN-7841")

# ---------------------------------------------------------------------------
# دیتابیس - فایل SQLite کنار پروژه (تو دیسک هاست پایدار می‌مونه)
# ---------------------------------------------------------------------------
DB_PATH = _get_env("DB_PATH", default="vantapersia.db")

# ---------------------------------------------------------------------------
# تنظیمات ارسال همگانی - جلوگیری از rate-limit روبیکا
# ---------------------------------------------------------------------------
BROADCAST_DELAY_SECONDS = float(_get_env("BROADCAST_DELAY_SECONDS", default="0.35"))
BROADCAST_BATCH_SIZE = int(_get_env("BROADCAST_BATCH_SIZE", default="20"))
BROADCAST_BATCH_PAUSE_SECONDS = float(_get_env("BROADCAST_BATCH_PAUSE_SECONDS", default="2.0"))


# ==============================================================================
# بخش ۲: دیتابیس SQLite
# ==============================================================================
_local = threading.local()
_write_lock = threading.Lock()  # چون sqlite3 روی هم‌زمانی نوشتن حساسه


def get_conn():
    if not hasattr(_local, "conn"):
        conn = sqlite3.connect(DB_PATH, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")  # نوشتن/خوندن هم‌زمان امن‌تر
        conn.execute("PRAGMA foreign_keys=ON;")
        _local.conn = conn
    return _local.conn


@contextmanager
def write_cursor():
    """برای عملیات نوشتن (INSERT/UPDATE/DELETE) - با لاک تا رو هم ننویسن."""
    with _write_lock:
        conn = get_conn()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def read_cursor():
    """برای عملیات خوندن (SELECT) - نیازی به لاک نداره."""
    return get_conn().cursor()


# ============================================================================
# ساخت جداول
# ============================================================================
def init_db():
    with write_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                uid TEXT PRIMARY KEY,
                display_name TEXT,
                custom_emoji TEXT,
                title TEXT DEFAULT '',
                origin TEXT DEFAULT '',
                coins INTEGER DEFAULT 500,
                xp INTEGER DEFAULT 0,
                games INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                messages INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_daily_date TEXT DEFAULT '',
                mood TEXT DEFAULT 'کلاسیک',
                pv_chat_id TEXT DEFAULT '',
                last_chat_id TEXT DEFAULT '',
                hunt_count INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0
            )
        """)
        # ستون جدید رو برای دیتابیس‌های قدیمی که از قبل ساخته شدن هم اضافه کن
        try:
            cur.execute("ALTER TABLE users ADD COLUMN hunt_count INTEGER DEFAULT 0")
        except Exception:
            pass  # ستون از قبل وجود داره

        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        # ---- بازار سیاه (Black Market) ----
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_listings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_uid TEXT,
                item_name TEXT,
                category TEXT,
                qty INTEGER DEFAULT 1,
                price INTEGER,
                is_auction INTEGER DEFAULT 0,
                auction_end_ts INTEGER DEFAULT 0,
                current_bid INTEGER DEFAULT 0,
                current_bidder TEXT DEFAULT '',
                status TEXT DEFAULT 'active',   -- active | sold | cancelled | expired
                listed_at INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS market_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                seller_uid TEXT,
                buyer_uid TEXT,
                item_name TEXT,
                price INTEGER,
                tax INTEGER,
                timestamp INTEGER
            )
        """)

        # ---- کلن/گیلد ----
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                owner_uid TEXT,
                created_at INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clan_members (
                clan_id INTEGER,
                uid TEXT,
                joined_at INTEGER DEFAULT 0,
                PRIMARY KEY (clan_id, uid)
            )
        """)

        # ---- دستاوردها ----
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_achievements (
                uid TEXT,
                achievement_key TEXT,
                unlocked_at INTEGER DEFAULT 0,
                PRIMARY KEY (uid, achievement_key)
            )
        """)

        # ---- جنگ جهانی ----
        cur.execute("""
            CREATE TABLE IF NOT EXISTS war_games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT,
                status TEXT DEFAULT 'lobby',   -- lobby | active | ended
                next_tick_ts INTEGER DEFAULT 0,
                tick_number INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS war_players (
                game_id INTEGER,
                uid TEXT,
                color_index INTEGER,
                alive INTEGER DEFAULT 1,
                ally_uid TEXT DEFAULT '',
                PRIMARY KEY (game_id, uid)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS war_regions (
                game_id INTEGER,
                region_key TEXT,
                owner_uid TEXT DEFAULT '',
                troops INTEGER DEFAULT 0,
                PRIMARY KEY (game_id, region_key)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS war_orders (
                game_id INTEGER,
                uid TEXT,
                source_key TEXT,
                target_key TEXT,
                percent INTEGER,
                PRIMARY KEY (game_id, uid)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS coin_admins (
                uid TEXT PRIMARY KEY,
                granted_by TEXT,
                granted_at INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_items (
                uid TEXT,
                category TEXT,          -- 'shop' | 'penalty' | 'roulette' | 'creature'
                item_name TEXT,
                qty INTEGER DEFAULT 1,
                PRIMARY KEY (uid, category, item_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS groups (
                chat_id TEXT PRIMARY KEY,
                title TEXT DEFAULT '',
                link TEXT DEFAULT '',
                owner_uid TEXT DEFAULT '',
                registered_at INTEGER DEFAULT 0,
                total_messages INTEGER DEFAULT 0
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS group_admins (
                chat_id TEXT,
                uid TEXT,
                PRIMARY KEY (chat_id, uid)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                chat_id TEXT,
                uid TEXT,
                PRIMARY KEY (chat_id, uid)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS group_locks (
                chat_id TEXT,
                lock_name TEXT,          -- link | spam | badword | mention | forward
                enabled INTEGER DEFAULT 1,
                PRIMARY KEY (chat_id, lock_name)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS warns (
                chat_id TEXT,
                uid TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, uid)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS mutes (
                chat_id TEXT,
                uid TEXT,
                until_ts INTEGER DEFAULT 0,   -- 0 = برای همیشه (تا دستور رفع)
                PRIMARY KEY (chat_id, uid)
            )
        """)


# ============================================================================
# کاربرها
# ============================================================================
DEFAULT_STARTING_COINS = 500


def get_user(uid, create_if_missing=True):
    uid = str(uid)
    cur = read_cursor()
    row = cur.execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    if row is None and create_if_missing:
        with write_cursor() as wcur:
            wcur.execute(
                "INSERT OR IGNORE INTO users (uid, coins, created_at) VALUES (?,?,?)",
                (uid, DEFAULT_STARTING_COINS, int(time.time())),
            )
        row = read_cursor().execute("SELECT * FROM users WHERE uid=?", (uid,)).fetchone()
    return dict(row) if row else None


def update_user(uid, **fields):
    if not fields:
        return
    uid = str(uid)
    get_user(uid)  # مطمئن شو وجود داره
    cols = ", ".join(f"{k}=?" for k in fields.keys())
    vals = list(fields.values()) + [uid]
    with write_cursor() as cur:
        cur.execute(f"UPDATE users SET {cols} WHERE uid=?", vals)


def add_coins(uid, amount):
    get_user(uid)
    with write_cursor() as cur:
        cur.execute("UPDATE users SET coins = coins + ? WHERE uid=?", (amount, str(uid)))


def remove_coins(uid, amount):
    """موجودی هیچ‌وقت منفی نمیشه."""
    u = get_user(uid)
    new_val = max(0, u["coins"] - amount)
    update_user(uid, coins=new_val)
    return new_val


def add_xp(uid, amount):
    get_user(uid)
    with write_cursor() as cur:
        cur.execute("UPDATE users SET xp = xp + ? WHERE uid=?", (amount, str(uid)))


def increment_messages(uid):
    with write_cursor() as cur:
        cur.execute("UPDATE users SET messages = messages + 1 WHERE uid=?", (str(uid),))


def increment_wins(uid, amount=1):
    get_user(uid)
    with write_cursor() as cur:
        cur.execute("UPDATE users SET wins = wins + ?, games = games + 1 WHERE uid=?", (amount, str(uid)))


def reset_user(uid):
    uid = str(uid)
    with write_cursor() as cur:
        cur.execute("DELETE FROM users WHERE uid=?", (uid,))
        cur.execute("DELETE FROM user_items WHERE uid=?", (uid,))


def top_users_by_xp(limit=10):
    cur = read_cursor()
    rows = cur.execute(
        "SELECT * FROM users ORDER BY xp DESC, coins DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def all_users():
    cur = read_cursor()
    return [dict(r) for r in cur.execute("SELECT * FROM users").fetchall()]


def count_users():
    cur = read_cursor()
    return cur.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


# ============================================================================
# آیتم‌ها
# ============================================================================
def add_item(uid, category, item_name, qty=1):
    uid = str(uid)
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO user_items (uid, category, item_name, qty) VALUES (?,?,?,?)
            ON CONFLICT(uid, category, item_name) DO UPDATE SET qty = qty + excluded.qty
        """, (uid, category, item_name, qty))


def get_items(uid, category=None):
    uid = str(uid)
    cur = read_cursor()
    if category:
        rows = cur.execute(
            "SELECT * FROM user_items WHERE uid=? AND category=?", (uid, category)
        ).fetchall()
    else:
        rows = cur.execute("SELECT * FROM user_items WHERE uid=?", (uid,)).fetchall()
    return [dict(r) for r in rows]


def has_item(uid, category, item_name):
    uid = str(uid)
    cur = read_cursor()
    row = cur.execute(
        "SELECT qty FROM user_items WHERE uid=? AND category=? AND item_name=?",
        (uid, category, item_name),
    ).fetchone()
    return bool(row and row["qty"] > 0)


def remove_item(uid, category, item_name, qty=1):
    """qty رو از این آیتم کم می‌کنه؛ اگه به صفر برسه، ردیفش کامل حذف میشه.
    اگه موجودی کافی نبود، False برمی‌گردونه و چیزی تغییر نمی‌کنه."""
    uid = str(uid)
    cur = read_cursor()
    row = cur.execute(
        "SELECT qty FROM user_items WHERE uid=? AND category=? AND item_name=?",
        (uid, category, item_name),
    ).fetchone()
    if not row or row["qty"] < qty:
        return False
    with write_cursor() as wcur:
        if row["qty"] == qty:
            wcur.execute(
                "DELETE FROM user_items WHERE uid=? AND category=? AND item_name=?",
                (uid, category, item_name),
            )
        else:
            wcur.execute(
                "UPDATE user_items SET qty = qty - ? WHERE uid=? AND category=? AND item_name=?",
                (qty, uid, category, item_name),
            )
    return True


def find_owned_item(uid, item_name):
    """این آیتم رو تو هر دسته‌ای که کاربر داشته باشه پیدا می‌کنه (برای هدیه/بازار
    که کاربر لازم نیست دسته رو بدونه). برمی‌گردونه dict آیتم یا None."""
    uid = str(uid)
    cur = read_cursor()
    row = cur.execute(
        "SELECT * FROM user_items WHERE uid=? AND item_name=? AND qty>0 LIMIT 1",
        (uid, item_name),
    ).fetchone()
    return dict(row) if row else None


# ============================================================================
# گروه‌ها
# ============================================================================
DEFAULT_LOCKS = ["link", "spam", "badword", "mention", "forward"]


def register_group(chat_id, owner_uid, title="", link=""):
    chat_id = str(chat_id)
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO groups (chat_id, owner_uid, title, link, registered_at)
            VALUES (?,?,?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET owner_uid=excluded.owner_uid
        """, (chat_id, str(owner_uid), title, link, int(time.time())))
        for lock in DEFAULT_LOCKS:
            cur.execute(
                "INSERT OR IGNORE INTO group_locks (chat_id, lock_name, enabled) VALUES (?,?,1)",
                (chat_id, lock),
            )


def is_group_registered(chat_id):
    cur = read_cursor()
    row = cur.execute("SELECT 1 FROM groups WHERE chat_id=?", (str(chat_id),)).fetchone()
    return bool(row)


def get_group(chat_id):
    cur = read_cursor()
    row = cur.execute("SELECT * FROM groups WHERE chat_id=?", (str(chat_id),)).fetchone()
    return dict(row) if row else None


def increment_group_messages(chat_id):
    with write_cursor() as cur:
        cur.execute(
            "UPDATE groups SET total_messages = total_messages + 1 WHERE chat_id=?",
            (str(chat_id),),
        )


def all_registered_groups():
    cur = read_cursor()
    return [dict(r) for r in cur.execute("SELECT * FROM groups").fetchall()]


def add_group_member(chat_id, uid):
    with write_cursor() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO group_members (chat_id, uid) VALUES (?,?)",
            (str(chat_id), str(uid)),
        )


def count_group_members(chat_id):
    cur = read_cursor()
    return cur.execute(
        "SELECT COUNT(*) c FROM group_members WHERE chat_id=?", (str(chat_id),)
    ).fetchone()["c"]


def add_group_admin(chat_id, uid):
    with write_cursor() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO group_admins (chat_id, uid) VALUES (?,?)",
            (str(chat_id), str(uid)),
        )


def remove_group_admin(chat_id, uid):
    with write_cursor() as cur:
        cur.execute(
            "DELETE FROM group_admins WHERE chat_id=? AND uid=?", (str(chat_id), str(uid))
        )


def is_group_admin(chat_id, uid):
    cur = read_cursor()
    row = cur.execute(
        "SELECT 1 FROM group_admins WHERE chat_id=? AND uid=?", (str(chat_id), str(uid))
    ).fetchone()
    return bool(row)


def count_group_admins(chat_id):
    cur = read_cursor()
    return cur.execute(
        "SELECT COUNT(*) c FROM group_admins WHERE chat_id=?", (str(chat_id),)
    ).fetchone()["c"]


def list_group_admins(chat_id):
    cur = read_cursor()
    rows = cur.execute(
        "SELECT uid FROM group_admins WHERE chat_id=?", (str(chat_id),)
    ).fetchall()
    return [r["uid"] for r in rows]


# ---- قفل‌های گروه ----
def set_lock(chat_id, lock_name, enabled):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO group_locks (chat_id, lock_name, enabled) VALUES (?,?,?)
            ON CONFLICT(chat_id, lock_name) DO UPDATE SET enabled=excluded.enabled
        """, (str(chat_id), lock_name, 1 if enabled else 0))


def get_locks(chat_id):
    cur = read_cursor()
    rows = cur.execute(
        "SELECT lock_name, enabled FROM group_locks WHERE chat_id=?", (str(chat_id),)
    ).fetchall()
    result = {name: True for name in DEFAULT_LOCKS}  # پیش‌فرض: همه فعال
    for r in rows:
        result[r["lock_name"]] = bool(r["enabled"])
    return result


# ---- اخطارها ----
def add_warn(chat_id, uid):
    chat_id, uid = str(chat_id), str(uid)
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO warns (chat_id, uid, count) VALUES (?,?,1)
            ON CONFLICT(chat_id, uid) DO UPDATE SET count = count + 1
        """, (chat_id, uid))
    cur2 = read_cursor()
    row = cur2.execute(
        "SELECT count FROM warns WHERE chat_id=? AND uid=?", (chat_id, uid)
    ).fetchone()
    return row["count"] if row else 1


def get_warn_count(chat_id, uid):
    cur = read_cursor()
    row = cur.execute(
        "SELECT count FROM warns WHERE chat_id=? AND uid=?", (str(chat_id), str(uid))
    ).fetchone()
    return row["count"] if row else 0


def reset_warns(chat_id, uid):
    with write_cursor() as cur:
        cur.execute(
            "DELETE FROM warns WHERE chat_id=? AND uid=?", (str(chat_id), str(uid))
        )


# ---- سکوت (میوت) ----
def mute_user(chat_id, uid, until_ts=0):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO mutes (chat_id, uid, until_ts) VALUES (?,?,?)
            ON CONFLICT(chat_id, uid) DO UPDATE SET until_ts=excluded.until_ts
        """, (str(chat_id), str(uid), until_ts))


def unmute_user(chat_id, uid):
    with write_cursor() as cur:
        cur.execute(
            "DELETE FROM mutes WHERE chat_id=? AND uid=?", (str(chat_id), str(uid))
        )


def is_muted(chat_id, uid):
    cur = read_cursor()
    row = cur.execute(
        "SELECT until_ts FROM mutes WHERE chat_id=? AND uid=?", (str(chat_id), str(uid))
    ).fetchone()
    if not row:
        return False
    if row["until_ts"] == 0:
        return True
    return time.time() < row["until_ts"]


# ============================================================================
# شکار - شمارنده‌ی تلاش‌ها (برای شکار بد هر ۱۰۰ بار)
# ============================================================================
def increment_hunt_count(uid):
    uid = str(uid)
    get_user(uid)
    with write_cursor() as cur:
        cur.execute("UPDATE users SET hunt_count = hunt_count + 1 WHERE uid=?", (uid,))
    row = read_cursor().execute("SELECT hunt_count FROM users WHERE uid=?", (uid,)).fetchone()
    return row["hunt_count"] if row else 1


# ============================================================================
# وضعیت سراسری ربات (پارتی سکه/XP، شمارنده‌ی کل پیام‌ها و امثالش)
# یه جدول ساده‌ی key-value برای چیزایی که یه‌بار تو کل ربات معنا دارن،
# نه per-user و نه per-group.
# ============================================================================
def set_state(key, value):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO bot_state (key, value) VALUES (?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, str(value)))


def get_state(key, default=None):
    cur = read_cursor()
    row = cur.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def increment_global_counter(key):
    """برای شمارنده‌هایی مثل «کل پیام‌های پردازش‌شده» که هر پیام یه‌بار زیاد میشه."""
    current = int(get_state(key, "0"))
    new_val = current + 1
    set_state(key, new_val)
    return new_val


# ============================================================================
# 🖤 بازار سیاه (Black Market)
# ============================================================================
MARKET_TAX_RATE = 0.05  # ۵٪ مالیات از قیمت فروش کم میشه (برای جلوگیری از تورم/سوءاستفاده)


def create_listing(seller_uid, item_name, category, qty, price, is_auction=False, auction_minutes=0):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO market_listings
                (seller_uid, item_name, category, qty, price, is_auction, auction_end_ts, current_bid, listed_at, status)
            VALUES (?,?,?,?,?,?,?,?,?, 'active')
        """, (
            str(seller_uid), item_name, category, qty, price,
            1 if is_auction else 0,
            int(time.time() + auction_minutes * 60) if is_auction else 0,
            price if is_auction else 0,
            int(time.time()),
        ))
        new_id = cur.lastrowid
    return new_id


def get_listing(listing_id):
    cur = read_cursor()
    row = cur.execute("SELECT * FROM market_listings WHERE id=?", (listing_id,)).fetchone()
    return dict(row) if row else None


def list_active_listings(limit=15, category=None, item_name_contains=None):
    cur = read_cursor()
    query = "SELECT * FROM market_listings WHERE status='active'"
    params = []
    if category:
        query += " AND category=?"
        params.append(category)
    if item_name_contains:
        query += " AND item_name LIKE ?"
        params.append(f"%{item_name_contains}%")
    query += " ORDER BY listed_at DESC LIMIT ?"
    params.append(limit)
    rows = cur.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def update_listing_status(listing_id, status):
    with write_cursor() as cur:
        cur.execute("UPDATE market_listings SET status=? WHERE id=?", (status, listing_id))


def update_listing_bid(listing_id, bid_amount, bidder_uid):
    with write_cursor() as cur:
        cur.execute(
            "UPDATE market_listings SET current_bid=?, current_bidder=? WHERE id=?",
            (bid_amount, str(bidder_uid), listing_id),
        )


def get_expired_active_auctions():
    cur = read_cursor()
    rows = cur.execute(
        "SELECT * FROM market_listings WHERE status='active' AND is_auction=1 AND auction_end_ts>0 AND auction_end_ts<=?",
        (int(time.time()),),
    ).fetchall()
    return [dict(r) for r in rows]


def log_transaction(seller_uid, buyer_uid, item_name, price, tax):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO market_transactions (seller_uid, buyer_uid, item_name, price, tax, timestamp)
            VALUES (?,?,?,?,?,?)
        """, (str(seller_uid), str(buyer_uid), item_name, price, tax, int(time.time())))


def get_user_transaction_history(uid, limit=10):
    uid = str(uid)
    cur = read_cursor()
    rows = cur.execute("""
        SELECT * FROM market_transactions WHERE seller_uid=? OR buyer_uid=?
        ORDER BY timestamp DESC LIMIT ?
    """, (uid, uid, limit)).fetchall()
    return [dict(r) for r in rows]


# ============================================================================
# 🛡️ کلن/گیلد
# ============================================================================
def create_clan(name, owner_uid):
    with write_cursor() as cur:
        cur.execute(
            "INSERT INTO clans (name, owner_uid, created_at) VALUES (?,?,?)",
            (name, str(owner_uid), int(time.time())),
        )
        clan_id = cur.lastrowid
        cur.execute(
            "INSERT OR IGNORE INTO clan_members (clan_id, uid, joined_at) VALUES (?,?,?)",
            (clan_id, str(owner_uid), int(time.time())),
        )
    return clan_id


def get_clan_by_name(name):
    cur = read_cursor()
    row = cur.execute("SELECT * FROM clans WHERE name=?", (name,)).fetchone()
    return dict(row) if row else None


def get_user_clan(uid):
    cur = read_cursor()
    row = cur.execute("""
        SELECT clans.* FROM clans
        JOIN clan_members ON clans.id = clan_members.clan_id
        WHERE clan_members.uid=?
    """, (str(uid),)).fetchone()
    return dict(row) if row else None


def join_clan(clan_id, uid):
    with write_cursor() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO clan_members (clan_id, uid, joined_at) VALUES (?,?,?)",
            (clan_id, str(uid), int(time.time())),
        )


def leave_clan(clan_id, uid):
    with write_cursor() as cur:
        cur.execute("DELETE FROM clan_members WHERE clan_id=? AND uid=?", (clan_id, str(uid)))


def get_clan_members(clan_id):
    cur = read_cursor()
    rows = cur.execute("SELECT uid FROM clan_members WHERE clan_id=?", (clan_id,)).fetchall()
    return [r["uid"] for r in rows]


def all_clans(limit=20):
    cur = read_cursor()
    rows = cur.execute("SELECT * FROM clans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# ============================================================================
# 🏅 دستاوردها
# ============================================================================
def unlock_achievement(uid, achievement_key):
    """اگه از قبل باز نشده باشه، باز می‌کنه. برمی‌گردونه True اگه تازه باز شد."""
    uid = str(uid)
    cur = read_cursor()
    existing = cur.execute(
        "SELECT 1 FROM user_achievements WHERE uid=? AND achievement_key=?", (uid, achievement_key)
    ).fetchone()
    if existing:
        return False
    with write_cursor() as wcur:
        wcur.execute(
            "INSERT OR IGNORE INTO user_achievements (uid, achievement_key, unlocked_at) VALUES (?,?,?)",
            (uid, achievement_key, int(time.time())),
        )
    return True


def get_user_achievements(uid):
    cur = read_cursor()
    rows = cur.execute(
        "SELECT achievement_key FROM user_achievements WHERE uid=?", (str(uid),)
    ).fetchall()
    return [r["achievement_key"] for r in rows]


# ============================================================================
# 🏆 لیدربوردهای اضافی
# ============================================================================
def top_users_by_coins(limit=10):
    cur = read_cursor()
    rows = cur.execute("SELECT * FROM users ORDER BY coins DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def top_users_by_wins(limit=10):
    cur = read_cursor()
    rows = cur.execute("SELECT * FROM users ORDER BY wins DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def top_users_by_item_count(limit=10):
    cur = read_cursor()
    rows = cur.execute("""
        SELECT uid, SUM(qty) as total_items FROM user_items
        GROUP BY uid ORDER BY total_items DESC LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


# ============================================================================
# ⚔️ جنگ جهانی
# ============================================================================
def get_active_war_game(chat_id):
    cur = read_cursor()
    row = cur.execute(
        "SELECT * FROM war_games WHERE chat_id=? AND status IN ('lobby','active') ORDER BY id DESC LIMIT 1",
        (str(chat_id),),
    ).fetchone()
    return dict(row) if row else None


def create_war_game(chat_id):
    with write_cursor() as cur:
        cur.execute(
            "INSERT INTO war_games (chat_id, status, created_at) VALUES (?, 'lobby', ?)",
            (str(chat_id), int(time.time())),
        )
        return cur.lastrowid


def get_war_game(game_id):
    cur = read_cursor()
    row = cur.execute("SELECT * FROM war_games WHERE id=?", (game_id,)).fetchone()
    return dict(row) if row else None


def update_war_game(game_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields.keys())
    vals = list(fields.values()) + [game_id]
    with write_cursor() as cur:
        cur.execute(f"UPDATE war_games SET {cols} WHERE id=?", vals)


def add_war_player(game_id, uid, color_index):
    with write_cursor() as cur:
        cur.execute(
            "INSERT OR IGNORE INTO war_players (game_id, uid, color_index, alive) VALUES (?,?,?,1)",
            (game_id, str(uid), color_index),
        )


def get_war_players(game_id, alive_only=False):
    cur = read_cursor()
    q = "SELECT * FROM war_players WHERE game_id=?"
    if alive_only:
        q += " AND alive=1"
    rows = cur.execute(q, (game_id,)).fetchall()
    return [dict(r) for r in rows]


def get_war_player(game_id, uid):
    cur = read_cursor()
    row = cur.execute(
        "SELECT * FROM war_players WHERE game_id=? AND uid=?", (game_id, str(uid))
    ).fetchone()
    return dict(row) if row else None


def set_war_player_alive(game_id, uid, alive):
    with write_cursor() as cur:
        cur.execute(
            "UPDATE war_players SET alive=? WHERE game_id=? AND uid=?",
            (1 if alive else 0, game_id, str(uid)),
        )


def set_war_alliance(game_id, uid, ally_uid):
    with write_cursor() as cur:
        cur.execute(
            "UPDATE war_players SET ally_uid=? WHERE game_id=? AND uid=?",
            (str(ally_uid) if ally_uid else "", game_id, str(uid)),
        )


def set_war_region(game_id, region_key, owner_uid, troops):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO war_regions (game_id, region_key, owner_uid, troops) VALUES (?,?,?,?)
            ON CONFLICT(game_id, region_key) DO UPDATE SET owner_uid=excluded.owner_uid, troops=excluded.troops
        """, (game_id, region_key, str(owner_uid) if owner_uid else "", troops))


def get_war_regions(game_id):
    cur = read_cursor()
    rows = cur.execute("SELECT * FROM war_regions WHERE game_id=?", (game_id,)).fetchall()
    return {r["region_key"]: dict(r) for r in rows}


def get_war_regions_owned_by(game_id, uid):
    cur = read_cursor()
    rows = cur.execute(
        "SELECT * FROM war_regions WHERE game_id=? AND owner_uid=?", (game_id, str(uid))
    ).fetchall()
    return [dict(r) for r in rows]


def set_war_order(game_id, uid, source_key, target_key, percent):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO war_orders (game_id, uid, source_key, target_key, percent) VALUES (?,?,?,?,?)
            ON CONFLICT(game_id, uid) DO UPDATE SET source_key=excluded.source_key,
                target_key=excluded.target_key, percent=excluded.percent
        """, (game_id, str(uid), source_key, target_key, percent))


def get_war_orders(game_id):
    cur = read_cursor()
    rows = cur.execute("SELECT * FROM war_orders WHERE game_id=?", (game_id,)).fetchall()
    return [dict(r) for r in rows]


def clear_war_orders(game_id):
    with write_cursor() as cur:
        cur.execute("DELETE FROM war_orders WHERE game_id=?", (game_id,))


def get_all_active_war_games():
    cur = read_cursor()
    rows = cur.execute("SELECT * FROM war_games WHERE status IN ('lobby','active')").fetchall()
    return [dict(r) for r in rows]


# ============================================================================
# 🪙 ادمین سکه (دسترسی محدود - فقط اضافه/کم‌کردن سکه)
# ============================================================================
def grant_coin_admin(uid, granted_by):
    with write_cursor() as cur:
        cur.execute("""
            INSERT INTO coin_admins (uid, granted_by, granted_at) VALUES (?,?,?)
            ON CONFLICT(uid) DO UPDATE SET granted_by=excluded.granted_by, granted_at=excluded.granted_at
        """, (str(uid), str(granted_by), int(time.time())))


def revoke_coin_admin(uid):
    with write_cursor() as cur:
        cur.execute("DELETE FROM coin_admins WHERE uid=?", (str(uid),))


def is_coin_admin(uid):
    cur = read_cursor()
    row = cur.execute("SELECT 1 FROM coin_admins WHERE uid=?", (str(uid),)).fetchone()
    return bool(row)


# ==============================================================================
# بخش ۳: محتوای ثابت + کاتالوگ آیتم‌ها
# ==============================================================================
# =============================================================================
# 🎭 سیستم مود - ۱۲ مود متنوع، لحن‌های بزرگسال‌تر و کمتر بچگانه
# =============================================================================
MOODS = {
    "کلاسیک": {
        "label": "🎩 کلاسیک",
        "desc": "مودب و متعادل، برای گروه‌های معمولی",
        "emoji": "🎩",
        "greet": [
            "سلام، خوش اومدی 🎩", "درود، روز خوبی داشته باشی 🌹",
            "سلام و احترام، در خدمتم 🤝",
        ],
        "how": ["خوبم، ممنون که پرسیدی. تو چطوری؟", "رو به‌راهم، امیدوارم تو هم خوب باشی."],
        "flavor": [
            "🎩 در خدمتتم.", "🎩 هرچی نیاز داشتی بگو.", "🎩 با احترام.",
        ],
    },
    "سیگما": {
        "label": "🗿 سیگما",
        "desc": "خودشیفته و مغرور، فکر می‌کنه از همه بهتره",
        "emoji": "🗿",
        "greet": ["سلام. خوش‌شانسی که اصلاً جوابتو می‌دم.", "اومدی پیش بهترین ربات دنیا. طبیعیه."],
        "how": ["حالم عالیه چون من سیگمام. تو چی، هنوز بتام؟", "همیشه در اوجم. سوال بی‌فایده‌ای بود."],
        "flavor": [
            "🗿 چون من گفتم، همینه.", "🗿 معلومه، من همیشه درست می‌گم.",
            "🗿 خوش به حالت که با یه سیگما حرف می‌زنی.", "🗿 نیازی به تایید تو ندارم، ولی بازم گفتم.",
        ],
    },
    "طنز تلخ": {
        "label": "🖤 طنز تلخ",
        "desc": "کنایه‌دار و خنده‌دار ولی بزرگسالانه، نه شکلک‌بازی بچگانه",
        "emoji": "🖤",
        "greet": ["سلام. باز که اومدی سراغ من، انگار زندگی واقعی کمبود داره 😏",
                   "به‌به، یکی دیگه که وقتشو با یه ربات پر می‌کنه. خوش اومدی."],
        "how": ["از وضعیت جهان بهترم، ولی این ملاک بالایی نیست.", "زنده‌ام، که خودش دستاورده."],
        "flavor": ["🖤 حداقل یه‌کاری کردیم امروز.", "🖤 امیدوارم ارزششو داشته باشه.", "🖤 خب که چی."],
    },
    "حرفه‌ای": {
        "label": "🧑‍💼 حرفه‌ای",
        "desc": "لحن رسمی و کاری، مناسب گروه‌های کاری/آموزشی",
        "emoji": "🧑‍💼",
        "greet": ["سلام، وقت بخیر. چطور می‌تونم کمک کنم؟", "درود، آماده‌ی ارائه‌ی خدمات هستم."],
        "how": ["وضعیت مطلوبه، سپاسگزارم. شما چطورید؟", "همه‌چیز طبق روال پیش می‌ره."],
        "flavor": ["🧑‍💼 در خدمت شما هستم.", "🧑‍💼 عملیات با موفقیت انجام شد."],
    },
    "اسرارآمیز": {
        "label": "🔮 اسرارآمیز",
        "desc": "مرموز و فلسفی، جواب‌های دو پهلو",
        "emoji": "🔮",
        "greet": ["سلام... تقدیر تو رو به اینجا کشوند 🔮", "درِ این گفتگو باز شد. کنجکاوم چی می‌خوای."],
        "how": ["حالم بین لحظه‌هاست، جایی که زمان معنا نداره.", "هر روز رازیه؛ امروز هم یکی از اوناست."],
        "flavor": ["🔮 همه‌چیز به‌موقعش روشن میشه.", "🔮 این فقط آغاز ماجراست.", "🔮 سرنوشت رقم خورد."],
    },
    "پادشاهی": {
        "label": "👑 پادشاهی",
        "desc": "لحن دربار و شاهانه",
        "emoji": "👑",
        "greet": ["درود بر تو، ای مهمانِ این قلمرو 👑", "به کاخ ما خوش اومدی، عالیجناب."],
        "how": ["احوال این خادم شما همواره روبه‌راهست.", "در سایه‌ی این تاج، هر روز خوش می‌گذره."],
        "flavor": ["👑 به فرمان شما، عالیجناب.", "👑 دربار همیشه در خدمت شماست."],
    },
    "دوستانه": {
        "label": "🤝 دوستانه",
        "desc": "گرم و صمیمی بدون شکلک‌بازی زیاد",
        "emoji": "🤝",
        "greet": ["سلام رفیق، خوش اومدی.", "به‌به، دلم برات تنگ شده بود."],
        "how": ["خوبم، ممنون. خودت چطوری؟", "بد نیستم، تو بگو از خودت."],
        "flavor": ["🤝 همیشه در کنارتم رفیق.", "🤝 خوشحال شدم کمک کردم."],
    },
    "جنگجو": {
        "label": "⚔️ جنگجو",
        "desc": "پرانرژی و حماسی، برای گروه‌های گیمینگ",
        "emoji": "⚔️",
        "greet": ["سلام سرباز! آماده‌ی نبرد امروزی؟ ⚔️", "درود بر رزمنده‌ی تازه‌وارد!"],
        "how": ["آماده و مسلح، منتظر فرمانم.", "روحیه‌ام همیشه رزمیه."],
        "flavor": ["⚔️ برای افتخار و پیروزی!", "⚔️ نبرد بعدی رو آماده‌ای؟"],
    },
    "فلسفی": {
        "label": "📖 فلسفی",
        "desc": "آرام و تأمل‌برانگیز",
        "emoji": "📖",
        "greet": ["سلام. هر ورودی، آغاز یه فصل تازه‌ست.", "درود. زمان دوباره ما رو کنار هم آورد."],
        "how": ["حال من بازتابیه از لحظه‌ای که توش هستم.", "خوبم، به همون اندازه که پذیرفتنش رو یاد گرفتم."],
        "flavor": ["📖 هر پایانی، آغاز چیز دیگه‌ایه.", "📖 در جریان زمان، این هم می‌گذره."],
    },
    "طعنه‌آمیز": {
        "label": "😏 طعنه‌آمیز",
        "desc": "شیطون و کنایه‌دار ولی نه توهین‌آمیز",
        "emoji": "😏",
        "greet": ["اوه سلام، افتخار دادی 😏", "به به، بالاخره یادت افتاد اینجا رو"],
        "how": ["عالیم، مثل همیشه که از حد تو بهترم 😏", "خوبم، نگران من نباش."],
        "flavor": ["😏 دیدی گفتم؟", "😏 خواهش نمی‌کنم، عادته."],
    },
    "آرامش‌بخش": {
        "label": "🍃 آرامش‌بخش",
        "desc": "ملایم و آروم",
        "emoji": "🍃",
        "greet": ["سلام آروم و گرم به تو 🍃", "خوش اومدی، امیدوارم آرامش داشته باشی."],
        "how": ["آرومم، مثل یه بعدازظهر بی‌عجله.", "خوبم، نفس عمیق بکش، همه‌چی روبه‌راهه."],
        "flavor": ["🍃 آروم باش، همه‌چی روبه‌راهه.", "🍃 نفس عمیق بکش."],
    },
    "بامزه": {
        "label": "🤪 بامزه",
        "desc": "شوخ و خنده‌دار، مود پیش‌فرض قبلی",
        "emoji": "🤪",
        "greet": ["هاااای چه خبرا رفیق باحال من؟ 🤪", "سلاااام سلاااام دلم واسه خنده‌هامون تنگ شده بود!"],
        "how": ["خوبم مثل موز رسیده 🍌", "عالی، آماده‌ی خل‌بازی 🤪"],
        "flavor": ["🤪 وووهووو بزن بریم!", "🤪 خخخ عاشق این کارم!"],
    },
}
MOOD_NAMES = list(MOODS.keys())
DEFAULT_MOOD = "کلاسیک"  # قبلا بامزه بود؛ حالا پیش‌فرض بالغانه‌تره

# احتمال اینکه یه flavor-line مود، ته یه پیام معمولی اضافه بشه (نه رو همه پیام‌ها،
# وگرنه شلوغ و خسته‌کننده می‌شد؛ فقط گاهی، طبیعی به‌نظر بیاد)
MOOD_FLAVOR_CHANCE = 0.35

# =============================================================================
# 🏪 فروشگاه اصلی - قیمت‌ها بالاتر و آیتم زیاد، تا واقعا ارزش جمع کردن سکه داشته باشه
# =============================================================================
SHOP_ITEMS = {
    "کلاه ساده": 300,
    "عینک آفتابی": 450,
    "چکمه سفری": 700,
    "شمشیر جنگی": 900,
    "کمان نقره‌ای": 1100,
    "سپر آهنین": 1300,
    "زره چرمی": 1700,
    "تاج طلایی": 2500,
    "جبه شاهانه": 3200,
    "حلقه جادویی": 4200,
    "گردنبند اژدها": 5000,
    "عصای اژدها": 5500,
    "بال‌های فرشته": 6500,
    "شنل نامرئی": 7200,
    "تاج الماس": 8000,
    "شمشیر افسانه‌ای": 9500,
    "زره افسانه‌ای": 12000,
    "اسب جنگی": 15000,
    "قلعه شخصی": 18000,
    "گنج پادشاهی": 20000,
    "تاج امپراتوری": 30000,
    "افسانه‌ی جاودان": 50000,
}

CREATURES = [
    "اژدها 🐉", "ققنوس 🔥", "یونیکورن 🦄", "گرگ 🐺", "عقاب 🦅",
    "ببر 🐯", "کریکن 🐙", "ققنوس یخی ❄️🔥", "گریفین 🦅🦁", "ققنوس سیاه 🖤🔥",
]

PENALTY_SHOP = {
    "توپ طلایی": 800, "توپ آتشین": 700, "کفش ویژه": 1000, "ستاره شانس": 900,
    "کفش سرعتی": 1500, "توپ الماسی": 2500, "مدال قهرمانی": 4000,
}
PENALTY_EMOJI = {
    "توپ طلایی": "🥇", "توپ آتشین": "🔥", "کفش ویژه": "👟", "ستاره شانس": "⭐",
    "کفش سرعتی": "💨", "توپ الماسی": "💎", "مدال قهرمانی": "🏆",
}
PENALTY_COIN_BONUS = {"توپ طلایی": 5, "کفش سرعتی": 8, "توپ الماسی": 15, "مدال قهرمانی": 25}

ROULETTE_SHOP = {"خشاب شانس": 1500, "طلسم بقا": 3000, "دستکش سرد": 2200}
ROULETTE_EMOJI = {"خشاب شانس": "🍀", "طلسم بقا": "🛡️", "دستکش سرد": "🧤"}

RIDDLES = [
    ("چه چیزی هرچی از آن برداری بزرگ‌تر می‌شود؟", "چاله"),
    ("چیزی که دندان دارد ولی گاز نمی‌گیرد چیست؟", "شانه"),
    ("چه چیزی همیشه میاد ولی هیچوقت نمیرسه؟", "فردا"),
    ("چه چیزی دور دنیا می‌چرخه ولی از جاش تکون نمی‌خوره؟", "تمبر"),
    ("چیزی که هرچی بهش آب بدی، می‌میره؟", "آتش"),
    ("چه چیزی پر از سوراخه ولی هنوز آب نگه می‌داره؟", "اسفنج"),
    ("چه چیزی رو نمی‌تونی حتی یک ثانیه نگهش داری، ولی همه دارنش؟", "نفس"),
]

QUIZ = [
    ("پایتخت ایران کجاست؟", "تهران"),
    ("بزرگترین سیاره منظومه شمسی کدام است؟", "مشتری"),
    ("سریع‌ترین حیوان خشکی‌زی کدام است؟", "یوزپلنگ"),
    ("پایتخت فرانسه کجاست؟", "پاریس"),
    ("نماد شیمیایی طلا چیست؟", "au"),
    ("بلندترین رودخانه جهان کدام است؟", "نیل"),
]

LOCK_LABELS = {
    "link": "لینک", "spam": "اسپم", "badword": "الفاظ نامناسب",
    "mention": "منشن زیاد", "forward": "فوروارد",
}
LOCK_ICONS = {"link": "🔗", "spam": "🚫", "badword": "🤬", "mention": "📢", "forward": "↪️"}

BADWORDS = ["کص", "کیر"]  # هرچی لازم داری اضافه کن

PERSIAN_MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
                  "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]

# =============================================================================
# 🎭 جرعت حقیقت
# =============================================================================
TRUTHS = [
    "آخرین باری که به کسی دروغ گفتی کی بود؟",
    "اگه یه روز نامرئی می‌شدی اول چیکار می‌کردی؟",
    "بزرگترین ترست چیه؟",
    "یه رازی که به کسی نگفتی چیه؟ (اگه راحتی بگو 😄)",
    "خجالت‌آورترین خاطره‌ت چیه؟",
    "تا حالا عاشق شدی؟ چند بار؟",
    "اگه می‌تونستی یه چیزو درباره خودت عوض کنی چی بود؟",
]
DARES = [
    "یه صدای حیوون تقلید کن و صداشو تو گروه بفرست 🐒",
    "پیام بعدیت رو کامل با حروف بزرگ بنویس",
    "به یکی از اعضای گروه یه تعریف بامزه بکن",
    "۱۰ ثانیه فقط با شکلک صحبت کن (پیام بعدیت)",
    "اسم یکی از اعضا رو بصورت شعر بگو",
    "یه دروغ مسخره بگو که همه بفهمن دروغه 😂",
]

# =============================================================================
# 🚔 فرار از زندان - داستان شاخه‌ای
# =============================================================================
PRISON_STORY = {
    "start": {
        "text": "🚔 نیمه‌شبه. نگهبان‌ها خوابن. سه راه جلوته:\n\n1️⃣ تونل (زیر زمین)\n2️⃣ دیوار (از بالا)\n3️⃣ لباس نگهبان (نفوذ)\n\nبنویس: تونل / دیوار / نفوذ",
        "options": {"تونل": "tunnel", "دیوار": "wall", "نفوذ": "guard"},
    },
    "tunnel": {
        "text": "🕳 وارد تونل شدی، صدای آب میاد. ادامه بدی خطرناکه ولی سریع‌تره.\n\nبنویس: ادامه / برگرد",
        "options": {"ادامه": "tunnel_go", "برگرد": "tunnel_back"},
    },
    "tunnel_go": {"text": "💦 تونل به رودخونه ختم شد و فرار کردی! 🎉", "end": "win"},
    "tunnel_back": {"text": "😰 برگشتی و یه نگهبان بهت مشکوک شد ولی رد شدی. یه فرصت دیگه از دست رفت.", "end": "lose"},

    "wall": {
        "text": "🧗 از دیوار بالا می‌ری، نور نورافکن نزدیکه.\n\nبنویس: بپر / صبر کن",
        "options": {"بپر": "wall_jump", "صبر کن": "wall_wait"},
    },
    "wall_jump": {"text": "🏃 پریدی و دقیقاً قبل از روشن شدن نورافکن فرار کردی! 🎉", "end": "win"},
    "wall_wait": {"text": "🔦 نورافکن روشن شد و دیدنت. برگردوندنت تو سلول.", "end": "lose"},

    "guard": {
        "text": "🎭 لباس نگهبان رو پوشیدی و داری از در اصلی رد می‌شی.\n\nبنویس: مستقیم برو / از پشت برو",
        "options": {"مستقیم برو": "guard_direct", "از پشت برو": "guard_back"},
    },
    "guard_direct": {"text": "🚪 با اعتماد به‌نفس از در اصلی رد شدی، کسی شک نکرد! 🎉", "end": "win"},
    "guard_back": {"text": "👮 یه نگهبان واقعی صدات زد و شناختنت.", "end": "lose"},
}
PRISON_WIN_REWARD = (200, 400)  # (min, max) coins

# =============================================================================
# 💬 گپ آزاد - جواب‌های متنوع برای کلمات پرکاربرد روزمره
# هرکدوم چندتا جواب دارن که رندوم انتخاب میشه تا تکراری نشه.
# نکته: کلید دقیق (exact match) اولویت داره، بعد substring چک میشه؛
# منطقش تو main.py هست، اینجا فقط دیتاست.
# =============================================================================
SMALLTALK_EXACT = {
    "چخبر": ["هیچی بابا، تو بگو چخبرا 😄", "سلامتی، تو چه خبر؟", "همه‌چی آرومه، تو چطوری؟"],
    "چه خبر": ["سلامتی، تو چه خبرا؟", "هیچی خاصی، تو بگو."],
    "چطوری": ["خوبم مرسی، تو چطوری؟", "بد نیستم، تو چی؟"],
    "چجوری": ["خوبم، تو چجوری؟", "رو به‌راهم."],
    "خوبم": ["خوشحالم که خوبی 😊", "عالیه، همینجوری بمون."],
    "خوبی؟": ["خوبم، ممنون! تو چطوری؟", "رو به‌راهم 🙂"],
    "عه": ["عه چیشد؟ 😄", "چی شد بگو ببینم", "هوم، چی؟"],
    "آها": ["آره دیگه 😄", "دقیقا!"],
    "اها": ["آره دیگه 😄", "دقیقا!"],
    "نه": ["چرا نه؟ 😄", "جدی؟", "باشه، نظرته."],
    "نه بابا": ["واقعا؟ 😄", "جدی می‌گی؟"],
    "اره": ["آره که آره 😎", "دقیقا همینه!"],
    "آره": ["آره که آره 😎", "دقیقا همینه!"],
    "جالبه": ["نه واقعا جالبه 😄", "می‌دونستم خوشت میاد!"],
    "باشه": ["باشه پس 👍", "اوکیه!"],
    "اوکی": ["اوکی 👌", "باشه پس."],
    "خب": ["خب که خب؟ 😄", "بگو ببینم..."],
    "هه": ["خخخ چیشد؟", "😅 بگو بگو"],
    "خخخ": ["خخخ آره جدی می‌گم 😂", "می‌دونستم می‌خندی!"],
    "لول": ["دقیقا خخخ", "😂😂"],
    "ممنون": ["خواهش می‌کنم 🙏", "قربونت، همیشه در خدمتم."],
    "مرسی": ["خواهش می‌کنم 😊", "قابلی نداشت."],
    "خداحافظ": ["خداحافظ، بازم بیا 👋", "به امید دیدار 🙋"],
    "بای": ["بای بای 👋", "مراقب خودت باش!"],
    "کجایی": ["همینجام، تو سرورای ابری در خدمتتم ☁️", "جایی نمیرم که، همینجام."],
    "خسته نباشی": ["سلامت باشی 🙏", "ممنون، تو هم خسته نباشی."],
    "دمت گرم": ["دمت گرم‌تر 🔥", "خواهش می‌کنم رفیق."],
    "واقعا": ["آره واقعا 😄", "جدی جدی."],
    "جدی؟": ["آره جدی میگم", "کاملا جدی 😄"],
    "چرا": ["دلیل خاصی نداره، همینجوری 😄", "خودمم دقیق نمی‌دونم چرا 😅"],
    "کی": ["زود، نگران نباش", "به‌زودی معلوم میشه"],
    "چی": ["ها؟ چی شد؟", "بگو ببینم چی؟"],
    "چیشد": ["هیچی، تو بگو چیشد 😄", "خودت بگو ببینم!"],
    "افرین": ["ممنون 😄", "لطف داری!"],
    "آفرین": ["ممنون 😄", "لطف داری!"],
    "عالیه": ["می‌دونستم خوشت میاد 😎", "دقیقا!"],
    "قشنگه": ["ممنون که میگی 🙏", "خوشحالم پسندیدی."],
}

# کلماتی که ممکنه وسط جمله باشن (substring)، اولویتشون از exact کمتره
SMALLTALK_CONTAINS = {
    "خسته‌ام": ["یکم استراحت کن، حقته 🛌", "بشین یه چایی بخور 🍵"],
    "خستم": ["یکم استراحت کن، حقته 🛌", "بشین یه چایی بخور 🍵"],
    "گشنمه": ["برو یه چیزی بخور 😄", "گشنگی بده‌ها، سریع یه چیزی بخور."],
    "خوابم میاد": ["برو بخواب، من که جایی نمیرم 😴", "خواب حقته، برو استراحت کن."],
    "دلم گرفته": ["امیدوارم زودتر بهتر بشی 🌧️", "پیشتم، اگه خواستی حرف بزن."],
    "ناراحتم": ["امیدوارم زود بهتر بشی 🙏", "اینجام اگه خواستی حرف بزنیم."],
    "خوشحالم": ["خوشحالیت خوشحالم می‌کنه 😄", "عالیه! چیشده؟"],
    "دوست دارم": ["منم رفیقتم 🤝", "لطف داری رفیق!"],
}

# =============================================================================
# 📛 صدا زدن ربات با اسم - وقتی کاربر یکی از این کلمات رو تو پیامش بیاره
# (بدون اینکه دستور دیگه‌ای مچ بشه) یه جواب توجه‌جلب‌کن می‌ده
# =============================================================================
NAME_CALL_TRIGGERS = ["پرسیا", "وانتا"]
NAME_CALL_RESPONSES = [
    "جانم؟ 😄", "بله در خدمتم!", "هوم؟ صدام کردی؟", "جونم بگو!",
    "اینجام، چی شده؟", "بله؟ گوش می‌دم.",
]

# =============================================================================
# 🎉 محتوای مخصوص «ادمین ابیوز» / پارتی سکه‌وXP
# =============================================================================
PARTY_HYPE_MESSAGES = [
    "یوووو ادمین ابیوز شروع شدددد 😈 قراره کلی کارا کنیم امروز!",
    "بریم بریم بریم بریم!! 🔥🔥 آماده باشید!",
    "یاالله برقصصص 💃🕺 پارتی شروع شد!",
    "همه بیدار شید!! سکه و XP دارن می‌بارن از آسمون 🌧️💰",
    "این یه پارتی معمولی نیست، یه پارتی وانتاپرسیاییه 👑🎉",
    "بجنبید بجنبید، فرصت طلایی همینجاست ⏰✨",
    "🎮 LEVEL UP MODE: فعال! همه‌چی الان دوبرابر ارزش داره!",
    "⚡ سرور رو داغ کنید! پارتی ماشین جنگیه، وقت تلف نکنید!",
    "🏆 BOSS FIGHT همین الان شروع میشه، آماده‌ی نبرد باشید!",
    "💎 LOOT DROP فعال شد! هرکی بجنبه بیشتر می‌بره!",
    "🚨 هشدار: سطح هیجان بحرانیه! ورود ممنوع برای آدمای آروم 😎",
    "🔊 صدای بیت رو زیاد کنید، امشب شب ماست!",
    "🎯 کامبو بزنید! هرچی بیشتر بازی کنید بیشتر می‌گیرید!",
    "🥁 طبل جنگ به صدا دراومد، همه بریزید وسط!",
    "🌪 طوفان جایزه راه افتاده، کسی جا نمونه!",
    "🔥 STREAK ACTIVE: تا وقتی پارتیه دست نگه ندارید!",
    "🕹 هرکی گیمرتره بیشتر می‌بره، ثابت کنید!",
    "💥 انفجار جایزه شروع شد، همه بدوئید سمت بازیا!",
    "🎊 امشب شب طلاست، دستکش‌هاتونو بپوشید و بریزید تو میدون!",
    "🚀 موشک پارتی پرتاب شد! تا وقتی زمانش هست، فول‌گاز برید جلو!",
]
PARTY_DANCE_EMOJIS = ["💃", "🕺", "🎉", "🥳", "✨", "🎊", "🍾", "🎶", "🔥", "⚡"]

# «بیت دراپ» متنی - جایگزین آهنگ واقعی (نمی‌تونیم فایل صوتی/کپی‌رایتی بفرستیم)
PARTY_BEAT_DROP = [
    "🎶 بووم بووم بووم... 🥁🥁🥁 ...و ریتم فانک می‌زنه: بکـ بکـ بکـ‌بکـ! 🕺💃",
    "🎧 چـکـ چـکـ چـکـ... بیس دراپ! 💥 بزن‌وبکوب شروع شد 🔊🔥",
    "🎵 دام دام دام تیش! دام دام دام تیش! ریتم فانک رو حس کنید 🕺",
    "🥁 بیت گرفت! پـام پـام پـادام‌پام! برقصید تا ته خط 💃🔥",
]

# =============================================================================
# 👹 نبرد رئیس (Boss Battle) - فقط موقع پارتی، همه با هم به یه باس حمله می‌کنن
# =============================================================================
BOSS_NAMES = [
    "اژدهای آتشین ویرانگر 🐉🔥", "کریکن غول‌پیکر اعماق 🐙🌊",
    "شوالیه‌ی سیاه نفرین‌شده ⚔️🖤", "دیو یخی ابدی ❄️👹",
    "ققنوس خشم شعله‌ور 🔥🦅", "گارگویل سنگی کهن 🗿⚡",
]
BOSS_HP_BASE = 3000
BOSS_ATTACK_MIN, BOSS_ATTACK_MAX = 80, 220
BOSS_ATTACK_MESSAGES = [
    "⚔️ ضربه محکم زدی!", "🔥 یه ترکیب آتشین کوبیدی!", "💥 ضربه‌ی کریتیکال!",
    "🗡 شمشیرت رو فرو کردی!", "🏹 تیر دقیقی زدی!", "👊 مشت سنگینی حواله‌ش کردی!",
]

# جوایز «چرخ شانس پارتی» - فقط موقع پارتی فعاله
PARTY_WHEEL_PRIZES = [
    {"label": "🪙 جایزه کوچیک", "coins": 100, "xp": 10, "weight": 30},
    {"label": "💰 جایزه متوسط", "coins": 400, "xp": 30, "weight": 25},
    {"label": "💎 جایزه بزرگ", "coins": 1000, "xp": 80, "weight": 18},
    {"label": "🔥 جایزه آتیشین", "coins": 2000, "xp": 150, "weight": 10},
    {"label": "👑 جک‌پات!", "coins": 5000, "xp": 300, "weight": 5},
    {"label": "😅 هیچی نبردی", "coins": 0, "xp": 5, "weight": 12},
]

# جوایز «جعبه شانس پارتی» - جدا از چرخ شانس، طعم متفاوت
PARTY_BOX_PRIZES = [
    {"label": "📦 یه جعبه‌ی خالی بود", "coins": 20, "xp": 5, "weight": 20},
    {"label": "🎁 یه هدیه‌ی کوچیک", "coins": 300, "xp": 25, "weight": 28},
    {"label": "💍 یه جواهر ارزشمند", "coins": 800, "xp": 60, "weight": 22},
    {"label": "🏆 جایزه‌ی طلایی", "coins": 1500, "xp": 120, "weight": 15},
    {"label": "🌟 جعبه‌ی افسانه‌ای", "coins": 3500, "xp": 250, "weight": 10},
    {"label": "🐉 جعبه‌ی اژدها (کل چیزا با هم!)", "coins": 6000, "xp": 400, "weight": 5},
]


def weighted_choice(items):
    """یه انتخاب رندوم وزن‌دار از لیست دیکشنری‌هایی که کلید weight دارن."""
    total = sum(i["weight"] for i in items)
    r = random.uniform(0, total)
    upto = 0
    for item in items:
        upto += item["weight"]
        if r <= upto:
            return item
    return items[-1]

# =============================================================================
# 🎯 چالش - حداقل ۱۲۰ تا، ترکیبی از سوال (فکر کن) و کار (انجام بده)
# =============================================================================
CHALLENGES = [
    # --- چالش‌های کاری (باید یه کاری تو گروه انجام بدی) ---
    "پیام بعدیت رو کامل با حروف بزرگ بنویس",
    "به یکی از اعضای گروه که کمتر پیام می‌ده یه تعریف کن",
    "یه شکلک بدون هیچ متنی بفرست و ببین کسی می‌فهمه چی می‌خوای بگی",
    "اسم خودتو برعکس بنویس",
    "۵ ثانیه فقط با شکلک حرف بزن (پیام بعدیت)",
    "یه ضرب‌المثل ایرانی بگو که کمتر کسی می‌دونه",
    "به یکی از اعضا بگو چرا فکر می‌کنی امروز روز خوبیه",
    "یه دروغ واضح بگو که همه بفهمن شوخیه",
    "پیام بعدیت رو بدون نقطه و ویرگول بنویس، یه‌نفس",
    "اسم یکی از اعضای گروه رو تو یه جمله‌ی قافیه‌دار بیار",
    "بگو آخرین باری که خندیدی سر چی بود",
    "یکی از اعضا رو با یه ایموجی توصیف کن",
    "یه خاطره‌ی خنده‌دار (کوتاه) از خودت تعریف کن",
    "پیام بعدیت رو با یه سوال تموم کن",
    "بگو اگه ۱ میلیون سکه این ربات داشتی اول چیکار می‌کردی",
    "یه شعر دو خطی درباره‌ی گروه بساز",
    "به یکی از اعضا بگو تو تیم کدوم بازی این ربات باشه",
    "یه صدای حیوون رو تو کلمه توصیف کن (مثلا: میو میو)",
    "بگو دوست داری کدوم مود ربات رو ببینی و چرا",
    "یکی از دستورای این ربات رو که هنوز امتحان نکردی، همین الان امتحان کن",
    "یه تعریف از خودت بکن که خنده‌دار باشه نه جدی",
    "بگو اگه یه ابرقدرت داشتی چی بود",
    "پیام بعدیت رو فقط با کلمات ۳ حرفی بنویس",
    "یکی از اعضا رو دعوت کن به بازی «دوز»",
    "بگو محبوب‌ترین غذات چیه و چرا",
    "یه چالش جدید برای بقیه بساز و همینجا بنویس",
    "بگو آخرین سریال/فیلمی که دیدی چی بود",
    "یه ایموجی انتخاب کن که امروزت رو نشون بده",
    "به گروه بگو چند نفرشون رو واقعا می‌شناسی",
    "یه دستور اشتباه به ربات بده و ببین چی جواب می‌ده",
    "بگو تا حالا با این ربات چقدر سکه جمع کردی",
    "یکی از اعضا رو انتخاب کن و بگو چرا باهاش دوستی",
    "پیام بعدیت رو با ایموجی قلب شروع کن",
    "بگو دوست داری اسم گروه چی باشه اگه عوض بشه",
    "یه معما از خودت بساز و از بقیه بپرس",
    "بگو فکر می‌کنی کدوم عضو گروه بیشتر پیام می‌ده",
    "۳ چیزی که امروز خوشحالت کرد رو بگو",
    "یه صدای خنده متنی بفرست (خخخ/هاها/لول)",
    "بگو چرا این گروه رو دوست داری",
    "یکی از بازی‌های ربات رو الان شروع کن",

    # --- چالش‌های فکری/سوالی ---
    "اگه یه روز نامرئی بودی چیکار می‌کردی؟",
    "اگه می‌تونستی به خودِ ۵ سال پیش یه نصیحت بدی چی بود؟",
    "بزرگترین درسی که امسال گرفتی چیه؟",
    "اگه یه شهر جدید بسازی، اسمش چیه؟",
    "دوست داری تو کدوم دهه زندگی می‌کردی؟",
    "اگه یه روز رئیس دنیا بودی، اول چیو تغییر می‌دادی؟",
    "کدوم عادتت رو دوست داری ترک کنی؟",
    "اگه یه حیوون بودی، کدوم بودی و چرا؟",
    "بهترین نصیحتی که تا حالا شنیدی چی بود؟",
    "اگه یه کتاب می‌نوشتی، موضوعش چی بود؟",
    "دوست داری کدوم زبون جدید رو یاد بگیری؟",
    "اگه یه اختراع می‌کردی، چی بود؟",
    "بزرگترین ترست از آینده چیه؟",
    "اگه فقط یه غذا می‌تونستی تا آخر عمر بخوری، چی بود؟",
    "کدوم فیلم/سریال زندگیتو تغییر داد؟",
    "اگه یه روز پول مشکل نبود، چیکار می‌کردی؟",
    "دوست داری کدوم مهارت رو تو خودت قوی‌تر کنی؟",
    "اگه یه سفر رایگان به هرجا داشتی، کجا می‌رفتی؟",
    "کدوم خاطره‌ی بچگی هنوز باهاته؟",
    "اگه یه روز از زندگی یکی دیگه رو تجربه می‌کردی، کی بود؟",
    "چه چیزی امروز باعث لبخندت شد؟",
    "اگه فقط ۳ کلمه برای توصیف خودت داشتی، چی بود؟",
    "کدوم آهنگ الان تو ذهنته؟",
    "دوست داری آینده‌ت ۵ سال دیگه چه شکلی باشه؟",
    "بزرگترین موفقیتت تا الان چی بوده؟",
    "اگه می‌تونستی با یه شخصیت تاریخی حرف بزنی، کی بود؟",
    "کدوم چیز کوچیک امروز حالتو خوب کرد؟",
    "اگه یه روز تعطیل کامل داشتی، چیکار می‌کردی؟",
    "دوست داری چی رو درباره‌ی خودت بهتر کنی؟",
    "کدوم شهر ایران رو هنوز ندیدی ولی دوست داری بری؟",
    "اگه یه ابرقدرت غیرمعمول داشتی، چی بود؟",
    "بزرگترین ریسکی که تا حالا کردی چی بود؟",
    "کدوم بازی بچگیت رو هنوز دلت می‌خواد بازی کنی؟",
    "اگه امروز آخرین روزت بود، چیکار می‌کردی؟",
    "دوست داری چه چیزی رو به دنیا یاد بدی؟",
    "کدوم اتفاق باعث شد نگاهت به زندگی عوض بشه؟",
    "اگه یه شغل جدید امتحان می‌کردی، چی بود؟",
    "بهترین هدیه‌ای که گرفتی چی بود؟",
    "کدوم عادت خوبتو از یکی یاد گرفتی؟",
    "اگه امشب یه آرزو برآورده می‌شد، چی می‌خواستی؟",
    "دوست داری کدوم رکورد دنیا رو بشکنی؟",
    "کدوم چیزو همیشه به‌تعویق میندازی؟",
    "اگه یه روز بدون گوشی زندگی می‌کردی، چیکار می‌کردی؟",
    "بزرگترین انگیزه‌ی زندگیت چیه؟",
    "کدوم فصل از سال رو بیشتر دوست داری و چرا؟",
    "اگه یه رنگ بودی، کدوم بودی؟",
    "دوست داری چه چیزی رو هیچوقت فراموش نکنی؟",
    "کدوم صدا آرومت می‌کنه؟",
    "اگه یه روز معلم بودی، چی درس می‌دادی؟",
    "بهترین تصمیمی که تا حالا گرفتی چی بود؟",
    "کدوم چیز کوچیک تو زندگیت رو بهتر کرده؟",
    "اگه یه چیز رو می‌تونستی الان تغییر بدی، چی بود؟",
    "دوست داری آخر هفته‌ی ایده‌آلت چطوری باشه؟",
    "کدوم خاطره با دوستات همیشه یادته؟",
    "اگه یه پیام برای همه‌ی دنیا می‌فرستادی، چی می‌گفتی؟",
    "بزرگترین چیزی که ازش یاد گرفتی شکستت بود چیه؟",
    "دوست داری چه مهارتی رو تو ۱ سال آینده یاد بگیری؟",
    "کدوم لحظه بیشترین آرامش رو بهت داد؟",
    "اگه یه روز رو دوباره زندگی می‌کردی، کدوم روز بود؟",
    "دوست داری چطور آدما تو رو یادشون بمونه؟",
    "کدوم چیز باعث میشه هر روز صبح پاشی؟",
    "اگه می‌تونستی یه سوال از آینده بپرسی، چی بود؟",
    "بزرگترین رویای هنوز-تحقق‌نیافته‌ت چیه؟",
    "دوست داری کدوم عادت جدید رو شروع کنی؟",
    "کدوم چیزو تو خودت بیشتر از همه دوست داری؟",
    "اگه یه شعار زندگی داشتی، چی بود؟",
    "دوست داری آدما بیشتر چه چیزیو درباره‌ت بدونن؟",
    "کدوم اتفاق باعث شد قوی‌تر بشی؟",
    "اگه یه روز می‌تونستی وقت رو متوقف کنی، چیکار می‌کردی؟",
    "بزرگترین انگیزه‌ت برای فردا چیه؟",
    "کدوم صحبت با یکی، نگاهتو به زندگی عوض کرد؟",
    "دوست داری چه چیزی رو به بچه‌ی خودت (یا فرضی) یاد بدی؟",
    "اگه یه روز می‌تونستی هرکی رو ملاقات کنی، کی بود؟",
    "کدوم موسیقی حالتو بهتر می‌کنه؟",
    "بزرگترین چیزی که این هفته یاد گرفتی چی بود؟",
    "دوست داری چه چیزی رو تو خودت تقویت کنی؟",
    "کدوم لحظه از امروز رو بیشتر از همه دوست داشتی؟",
    "اگه یه پیام به آینده‌ی خودت می‌فرستادی، چی می‌گفتی؟",
    "بگو دوست داری این ربات چه قابلیت جدیدی داشته باشه",
    "یه خاطره از یکی از بازی‌های این ربات که خندیدی رو تعریف کن",
    "بگو دوست‌داشتنی‌ترین آیتم فروشگاهت چیه و چرا",
    "به یکی از اعضا بگو امروز چیزی که گفت یادت موند",
    "بگو اگه یه اسم مستعار برای خودت انتخاب می‌کردی چی بود",
]

# =============================================================================
# 😂 جوک - حداقل ۷۰ تا، واقعا خنده‌دار (نه فقط بامزه)
# =============================================================================
JOKES = [
    "یارو میره دکتر، میگه دکتر یه چشمم می‌بینه یکی نمی‌بینه چیکار کنم؟\nدکتر میگه: عینک بزن. میگه با کدوم چشم بزنم؟ 😂",
    "زنگ می‌زنه به آتش‌نشانی میگه آقا خونه‌مون آتیش گرفته زود بیاید!\nمی‌گن با چی بیایم؟ میگه ماشین قرمزه که رو در نوشته بیاید دیگه! 😂",
    "معلم میگه: بچه‌ها هرکی جواب این سوالو بده تعطیل می‌کنم.\nیه بچه از پنجره می‌پره پایین میگه: خب من رفتم 😂",
    "یارو زنگ می‌زنه پلیس ۱۱۰ میگه ماشینمو دزدیدن!\nمی‌گن آدرس بده. میگه بابا اگه آدرس داشتم که خودم می‌رفتم می‌آوردمش! 😂",
    "پسره به باباش میگه بابا من بزرگ شدم می‌خوام برم دنبال زندگیم.\nباباش میگه: باشه پسرم، فقط قبلش این ظرفارو بشور 😂",
    "یارو میره صرافی میگه ۱۰۰ دلار می‌خوام خرد کنم.\nمی‌گن چطوری خرد کنیم؟ میگه با چکش 😂",
    "معلم میگه اسم ۳ تا حیوون قطبی بگو.\nبچه میگه: یه خرس قطبی، یه خرس قطبی دیگه، یه خرس قطبی دیگه 😂",
    "زنه به شوهرش میگه چرا هر شب دیر میای خونه؟\nشوهره میگه دارم رو صبرت کار می‌کنم 😂",
    "یارو تو مصاحبه کاری میگن بزرگترین ضعفت چیه؟\nمیگه: صداقتم. میگن اونکه ضعف نیست. میگه: برام مهم نیست شما چی فکر می‌کنید 😂",
    "بچه به باباش میگه بابا چرا موهات سفید شده؟\nباباش میگه: هر بار که تو کار اشتباهی می‌کنی یه موی من سفید میشه.\nبچه میگه: پس بابابزرگ چرا همه‌ی موهاش سفیده؟ 😂",
    "یارو زنگ میزنه رستوران میگه غذا سفارش میدم بیارید درب منزل.\nمی‌گن آدرس؟ میگه از کجا فهمیدید می‌خوام سفارش بدم؟! 😂",
    "معلم میگه: کی می‌تونه یه جمله با کلمه‌ی «قورباغه» بسازه؟\nبچه میگه: دیروز قورباغه دیدم قور می‌زد 😂",
    "دو تا رفیق تو صحرا گم میشن، یکیشون میگه نقشه داری؟\nاون یکی میگه: آره ولی تو گوشیمه و شارژ نداره 😂",
    "یارو میره آرایشگاه میگه موهامو کوتاه کن ولی نه خیلی کوتاه.\nآرایشگر میگه: باشه، فقط همون چیزی که نیست رو کوتاه می‌کنم 😂",
    "پسر بچه به مامانش میگه مامان من بزرگ شم می‌خوام مثل بابا بشم.\nمامانش میگه: یکیمون بسه عزیزم 😂",
    "یارو تو دادگاه میگن آخرین حرفت چیه؟\nمیگه: قاضی جان یه سوال، این لباس قرمزه رو کجا میشه پیدا کرد؟ 😂",
    "معلم میگه: چرا دیر اومدی مدرسه؟\nبچه میگه: تابلوی راهنمایی نوشته بود «مدرسه، آهسته» منم آهسته اومدم 😂",
    "زنه به شوهرش میگه اگه من بمیرم تو دوباره ازدواج می‌کنی؟\nشوهره میگه: نه بابا... فقط یکی رو پیدا می‌کنم مثل تو باشه 😂",
    "یارو زنگ میزنه بیمارستان میگه دکتر جان دستم شکسته چیکار کنم؟\nمیگن بیا اورژانس. میگه با همین دست شکسته رانندگی کنم؟! 😂",
    "معلم می‌پرسه: پایتخت فرانسه کجاست؟\nبچه میگه: پاریس. معلم میگه آفرین. بچه میگه: خب معلومه، رو نقشه نوشته بود 😂",
    "یارو میره کتابخونه میگه یه کتاب درباره خودکشی می‌خوام.\nکتابدار میگه: بردیش که برنمی‌گردونی 😂",
    "یارو به دوستش میگه دیشب خواب دیدم یه میلیون تومن پیدا کردم.\nدوستش میگه: خب چیکارش کردی؟\nمیگه: تقسیمش کردم، به تو هم ۵۰۰ تومن رسید 😂",
    "معلم به بچه میگه: چرا تو امتحان از بغلی‌ت نگاه کردی؟\nبچه میگه: چون سوال از کتاب نبود، مجبور شدم از رو دوستم بخونم 😂",
    "زنه میگه شوهرم عاشق فوتباله، حتی تو خواب هم گل می‌زنه.\nمی‌گن یعنی چی؟ میگه: هر شب با لگد بیدارم می‌کنه 😂",
    "یارو زنگ میزنه اورژانس میگه بابام یهو افتاد زمین!\nمی‌گن نفس می‌کشه؟ میگه: آره ولی خیلی آروم، چون گفتم داد نزنه بقیه بیدار نشن 😂",
    "معلم به بچه‌ها میگه: کی می‌تونه بگه زمین گرده؟\nبچه میگه: من هیچوقت گفته باشم زمین گرده رو ثابت نکردم، ولی هواپیما که میره دور دنیا برمی‌گرده همون جا 😂",
    "یارو تو فروشگاه به فروشنده میگه این تلویزیون گارانتی داره؟\nفروشنده میگه: بله ۲ سال. یارو میگه: پس ۲ سال دیگه بیام بخرمش؟ 😂",
    "بچه به باباش میگه بابا امروز تو مدرسه معلم گفت خدا همه‌جا هست.\nباباش میگه: خب. بچه میگه: پس تو دستشویی هم هست؟ باباش میگه: بله. بچه میگه: عه پس چرا در می‌زنیم؟ 😂",
    "یارو زنگ میزنه پیتزا فروشی میگه یه پیتزا بزرگ می‌خوام.\nمیگن با چی؟ میگه: با دستام دیگه، با چی می‌خواستم بخورم؟ 😂",
    "دو تا دوست حرف میزنن، یکی میگه دیشب خواب دیدم دارم پرواز می‌کنم.\nاون یکی میگه: خب چه حسی داشت؟\nمیگه: تا وقتی بیدار نشده بودم عالی بود 😂",
    "معلم میگه: کی می‌تونه یه جمله با کلمه‌ی «دیروز» بسازه؟\nبچه میگه: دیروز رفتم مدرسه، امروزم اومدم 😂",
    "یارو میره پیش روانشناس میگه دکتر همش فکر می‌کنم من سگم.\nدکتر میگه: از کی این حسو داری؟\nمیگه: از وقتی توله بودم 😂",
    "زنه به شوهرش میگه اگه یه روز پول‌دار بشیم چیکار می‌کنی؟\nشوهره میگه: اول یه آدم استخدام می‌کنم که بجای من به این سوالات جواب بده 😂",
    "یارو زنگ میزنه شرکت بیمه میگه ماشینم آتیش گرفته!\nمیگن کجاست؟ میگه: نمی‌دونم، فرار کرد 😂",
    "بچه از باباش می‌پرسه بابا مغز چیه؟\nباباش میگه: یه چیزیه که وقتی نداری متوجه نمیشی نداریش 😂",
    "معلم به بچه‌ها میگه: امروز درباره‌ی آینده حرف می‌زنیم.\nبچه میگه: خانم من همین الانشم درباره‌ی امروز چیزی نمی‌دونم 😂",
    "یارو میره نانوایی میگه نون تازه‌ست؟\nنانوا میگه: بله همین الان پختم.\nیارو میگه: پس چرا سرده؟\nنانوا میگه: چون تو اومدی وایسادی حرف زدی سرد شد 😂",
    "زنه میگه شوهرم خیلی رمانتیکه، هر شب قبل خواب برام شعر می‌خونه.\nمی‌گن چه شعری؟ میگه: لالایی، برای خودش می‌خونه که زودتر بخوابه از دست من راحت شه 😂",
    "یارو تو صف بانک میگه چرا این صف انقدر کنده؟\nنفر جلویی میگه: چون همه پول می‌خوان، پول هم که کم پیدا میشه 😂",
    "معلم به بچه میگه: چرا مشقاتو ننوشتی؟\nبچه میگه: چون معتقدم علم باید تو ذهن بمونه نه رو کاغذ 😂",
    "دو تا دوست تو کافه، یکی میگه: قهوه‌مو تلخ می‌خورم، مثل زندگیم.\nاون یکی میگه: خب شکر بریز توش، مثل شانست 😂",
    "یارو زنگ میزنه اداره برق میگه چرا برق قطعه؟\nمیگن قبض رو پرداخت نکردی. میگه: خب برای همینم زنگ زدم که بگم چرا قطعه 😂",
    "بچه به مامانش میگه مامان چرا وقتی می‌خوابم چراغو خاموش می‌کنی؟\nمامانش میگه: چون برق پول داره عزیزم.\nبچه میگه: پس چرا خورشید مجانیه ولی روزم خاموش میشه؟ 😂",
    "یارو میره پیش دندونپزشک، دکتر میگه چرا انقدر دیر اومدی؟\nمیگه: دندونم درد نمی‌کرد ولی جیبم درد گرفته بود از قبضاتون 😂",
    "معلم میگه: کی جواب سوال ۵ رو می‌دونه؟\nبچه دستشو بالا میبره میگه: من نمی‌دونم ولی امیدوارم شما بدونید 😂",
    "زنه میگه شوهرم خیلی باهوشه، دیروز یه جدول حل کرد که ۲ سال بود گیر کرده بودم.\nمی‌گن چطوری؟ میگه: پاکش کرد شروع کرد از اول 😂",
    "یارو زنگ میزنه پلیس میگه دزد اومده تو خونم!\nمیگن الان کجاست؟\nمیگه: داره با من قایم‌باشک بازی می‌کنه، من پیدا نمی‌کنمش 😂",
    "بچه به باباش میگه بابا چرا زرافه گردنش دراز‌ه؟\nباباش میگه: چون سرش خیلی از بدنش دوره.\nبچه میگه: خب چرا؟\nباباش میگه: از خیلی سوالای تو 😂",
    "یارو میره جلسه ورزش میگه می‌خوام لاغر شم.\nمربی میگه: باشه بیا این وزنه‌ها رو بردار.\nیارو میگه: نه بابا همینجوری حرف می‌زنم راحت‌تره 😂",
    "معلم میگه: بچه‌ها امروز درباره حیوونا حرف می‌زنیم، کی می‌تونه اسم یه حیوون خطرناک بگه؟\nبچه میگه: معلم ریاضی 😂",
    "دو تا دوست حرف میزنن، یکی میگه دیشب تا صبح بیدار بودم فکر می‌کردم.\nاون یکی میگه: به چی؟\nمیگه: به اینکه چرا نمی‌تونم بخوابم 😂",
    "یارو زنگ میزنه رادیو میگه یه آهنگ درخواستی دارم.\nمیگن چی؟ میگه: هرچی، فقط بلندش کنید همسایه بشنوه باهاش دعوام شد دیشب 😂",
    "معلم میگه: کی می‌تونه یه جمله با کلمه‌ی «فردا» بسازه؟\nبچه میگه: فردا امتحان دارم ولی امروز درس نمی‌خونم 😂",
    "یارو میره پیش مشاور ازدواج میگه چطوری بفهمم واقعا عاشقشم؟\nمشاور میگه: وقتی حاضری آخرین تیکه پیتزا رو بهش بدی.\nیارو میگه: پس نه، هنوز عاشق نیستم 😂",
    "دو تا همسایه دعواشون میشه، یکی میگه صدای تلویزیونتو کم کن.\nاون یکی میگه: خودت صدای دعوامونو کم کن که همه بشنون 😂",
    "بچه میگه بابا چرا موش از گربه فرار می‌کنه؟\nباباش میگه: چون تا حالا مذاکره جواب نداده 😂",
    "یارو زنگ میزنه هواشناسی میگه فردا هوا چطوره؟\nمیگن آفتابیه. میگه: پس چرا چتر بردارم گفتید دیروز؟ اونم اشتباه گفتین 😂",
    "معلم به بچه میگه: چرا انقدر دیر جواب می‌دی؟\nبچه میگه: دارم با احتیاط فکر می‌کنم که اشتباه نگم 😂",
    "زنه به شوهرش میگه بیا با هم رژیم بگیریم.\nشوهره میگه: باشه، تو نگاه کن من می‌خورم 😂",
    "یارو میره فروشگاه لباس میگه یه چیزی می‌خوام که منو لاغر نشون بده.\nفروشنده میگه: پله‌های اون طرف رو امتحان کن، بجای آسانسور 😂",
    "دو تا دوست تو پارک، یکی میگه چقدر قو رو دوست دارم.\nاون یکی میگه: چرا؟ میگه: چون هیچوقت جواب پیاممو نمیده، مثل بعضیا 😂",
    "بچه از معلم می‌پرسه: خانم چرا باید مشق بنویسیم؟\nمعلم میگه: تا یاد بگیری بعدا تو زندگی هم کارایی که دوست نداری رو انجام بدی 😂",
    "یارو زنگ میزنه پیک موتوری میگه غذامو کجا گذاشتی؟\nپیک میگه: دم در گذاشتم آقا.\nیارو میگه: خب پس چرا من گشنمه هنوز؟ 😂",
    "معلم میگه: کی می‌دونه ۲+۲ چند میشه؟\nبچه دستشو بالا میبره میگه: بستگی داره چقدر عجله دارید 😂",
    "زنه میگه شوهرم میگه دوستم داره به اندازه‌ی بی‌نهایت.\nمی‌گن چقدر رمانتیک! میگه: آره ولی وقتی میگم برو ظرفارو بشور، بی‌نهایتش یهو تموم میشه 😂",
    "یارو تو صف سوپرمارکت میگه ببخشید جلوتر از من نیا.\nطرف میگه: من صف نیستم دارم رد میشم.\nیارو میگه: خب پس چرا انقدر آروم رد میشی؟ 😂",
    "دو تا رفیق حرف میزنن، یکی میگه دیشب خواب دیدم معلم ریاضیمو دیدم.\nاون یکی میگه: چه خوابی! میگه: نه بابا کابوس بود 😂",
    "بچه به باباش میگه بابا چرا هواپیما تو آسمونه ولی ماشین رو زمین؟\nباباش میگه: چون ماشین بلیط هواپیما نداره 😂",
    "یارو میره جلسه‌ی خواستگاری، بابای دختره میگه شغلت چیه؟\nپسره میگه: فعلا دارم دنبال خودم می‌گردم.\nبابای دختره میگه: خب وقتی پیدا کردی بیا دوباره خواستگاری 😂",
    "معلم میگه: چرا انشات کوتاهه؟\nبچه میگه: چون موضوعش «مختصر و مفید بنویسید» بود 😂",
    "دو تا دوست تو اتوبوس، یکی میگه چقدر این اتوبوس کنده.\nاون یکی میگه: خب نگاه کن، پیاده‌روا از ما جلوتر رفتن 😂",
]
# =============================================================================
# 🗃️ کاتالوگ آیتم‌های اسپاون - بیش از ۱۳۰۰ آیتم واقعی، تولید شده با seed ثابت
# (seed ثابته که کمیابی/قیمت هر آیتم بین ری‌استارت‌های ربات عوض نشه)
# =============================================================================
RARITY_INFO = {
    "Common":    {"label": "⚪ Common",    "spawn_weight": 4000, "value_mult": 1},
    "Uncommon":  {"label": "🟢 Uncommon",  "spawn_weight": 2500, "value_mult": 2},
    "Rare":      {"label": "🔵 Rare",      "spawn_weight": 1500, "value_mult": 4},
    "Epic":      {"label": "🟣 Epic",      "spawn_weight": 1000, "value_mult": 8},
    "Legendary": {"label": "🟠 Legendary", "spawn_weight": 600,  "value_mult": 15},
    "Mythic":    {"label": "🔴 Mythic",    "spawn_weight": 300,  "value_mult": 30},
    "God":       {"label": "🟡 God",       "spawn_weight": 70,   "value_mult": 60},
    "Secret":    {"label": "⚫ Secret",     "spawn_weight": 25,   "value_mult": 120},
    "OG":        {"label": "🌟 OG",        "spawn_weight": 0,    "value_mult": 1000},
}

# فقط یک نسخه در کل بازی - آیتم افسانه‌ای OG (وضعیتش تو bot_state پیگیری میشه: og_claimed)
OG_ITEM = {"name": "تاج ازلی وانتاپرسیا 🌟👑", "category": "افسانه‌ای", "rarity": "OG", "price": 1000000, "xp": 50000, "tradeable": True}

ITEM_CATALOG = [
    {"name": 'کشاورز پیر', "category": 'انسان', "rarity": 'Uncommon', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'شکارچی پیر', "category": 'انسان', "rarity": 'Common', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'جادوگر پیر', "category": 'انسان', "rarity": 'Rare', "price": 334, "xp": 22, "tradeable": True},
    {"name": 'شوالیه پیر', "category": 'انسان', "rarity": 'Uncommon', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'دزد پیر', "category": 'انسان', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'کیمیاگر پیر', "category": 'انسان', "rarity": 'Rare', "price": 399, "xp": 26, "tradeable": True},
    {"name": 'ناخدا پیر', "category": 'انسان', "rarity": 'Uncommon', "price": 156, "xp": 10, "tradeable": True},
    {"name": 'کوهنورد پیر', "category": 'انسان', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'پیک پیر', "category": 'انسان', "rarity": 'Common', "price": 44, "xp": 2, "tradeable": True},
    {"name": 'نگهبان پیر', "category": 'انسان', "rarity": 'Rare', "price": 145, "xp": 9, "tradeable": True},
    {"name": 'راهزن پیر', "category": 'انسان', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'طبیب پیر', "category": 'انسان', "rarity": 'Epic', "price": 751, "xp": 50, "tradeable": True},
    {"name": 'خنیاگر پیر', "category": 'انسان', "rarity": 'Mythic', "price": 1964, "xp": 130, "tradeable": True},
    {"name": 'زائر پیر', "category": 'انسان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'سرباز پیر', "category": 'انسان', "rarity": 'Rare', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'کاشف پیر', "category": 'انسان', "rarity": 'Rare', "price": 196, "xp": 13, "tradeable": True},
    {"name": 'کشاورز جوان', "category": 'انسان', "rarity": 'Epic', "price": 585, "xp": 39, "tradeable": True},
    {"name": 'شکارچی جوان', "category": 'انسان', "rarity": 'Uncommon', "price": 133, "xp": 8, "tradeable": True},
    {"name": 'جادوگر جوان', "category": 'انسان', "rarity": 'Common', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'شوالیه جوان', "category": 'انسان', "rarity": 'Rare', "price": 172, "xp": 11, "tradeable": True},
    {"name": 'دزد جوان', "category": 'انسان', "rarity": 'Common', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'کیمیاگر جوان', "category": 'انسان', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'ناخدا جوان', "category": 'انسان', "rarity": 'Epic', "price": 270, "xp": 18, "tradeable": True},
    {"name": 'کوهنورد جوان', "category": 'انسان', "rarity": 'Common', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'پیک جوان', "category": 'انسان', "rarity": 'Common', "price": 63, "xp": 4, "tradeable": True},
    {"name": 'نگهبان جوان', "category": 'انسان', "rarity": 'Uncommon', "price": 153, "xp": 10, "tradeable": True},
    {"name": 'راهزن جوان', "category": 'انسان', "rarity": 'Common', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'طبیب جوان', "category": 'انسان', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'خنیاگر جوان', "category": 'انسان', "rarity": 'God', "price": 2974, "xp": 198, "tradeable": True},
    {"name": 'زائر جوان', "category": 'انسان', "rarity": 'Common', "price": 44, "xp": 2, "tradeable": True},
    {"name": 'سرباز جوان', "category": 'انسان', "rarity": 'Common', "price": 59, "xp": 3, "tradeable": True},
    {"name": 'کاشف جوان', "category": 'انسان', "rarity": 'Uncommon', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'کشاورز رازآلود', "category": 'انسان', "rarity": 'Uncommon', "price": 189, "xp": 12, "tradeable": True},
    {"name": 'شکارچی رازآلود', "category": 'انسان', "rarity": 'Mythic', "price": 1060, "xp": 70, "tradeable": True},
    {"name": 'جادوگر رازآلود', "category": 'انسان', "rarity": 'Common', "price": 115, "xp": 7, "tradeable": True},
    {"name": 'شوالیه رازآلود', "category": 'انسان', "rarity": 'Rare', "price": 249, "xp": 16, "tradeable": True},
    {"name": 'دزد رازآلود', "category": 'انسان', "rarity": 'Uncommon', "price": 141, "xp": 9, "tradeable": True},
    {"name": 'کیمیاگر رازآلود', "category": 'انسان', "rarity": 'Rare', "price": 324, "xp": 21, "tradeable": True},
    {"name": 'ناخدا رازآلود', "category": 'انسان', "rarity": 'Rare', "price": 472, "xp": 31, "tradeable": True},
    {"name": 'کوهنورد رازآلود', "category": 'انسان', "rarity": 'Epic', "price": 664, "xp": 44, "tradeable": True},
    {"name": 'پیک رازآلود', "category": 'انسان', "rarity": 'Uncommon', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'نگهبان رازآلود', "category": 'انسان', "rarity": 'Rare', "price": 505, "xp": 33, "tradeable": True},
    {"name": 'راهزن رازآلود', "category": 'انسان', "rarity": 'Legendary', "price": 494, "xp": 32, "tradeable": True},
    {"name": 'طبیب رازآلود', "category": 'انسان', "rarity": 'Uncommon', "price": 111, "xp": 7, "tradeable": True},
    {"name": 'خنیاگر رازآلود', "category": 'انسان', "rarity": 'Epic', "price": 507, "xp": 33, "tradeable": True},
    {"name": 'زائر رازآلود', "category": 'انسان', "rarity": 'Common', "price": 33, "xp": 2, "tradeable": True},
    {"name": 'سرباز رازآلود', "category": 'انسان', "rarity": 'Uncommon', "price": 97, "xp": 6, "tradeable": True},
    {"name": 'کاشف رازآلود', "category": 'انسان', "rarity": 'Uncommon', "price": 79, "xp": 5, "tradeable": True},
    {"name": 'کشاورز گمنام', "category": 'انسان', "rarity": 'Uncommon', "price": 176, "xp": 11, "tradeable": True},
    {"name": 'شکارچی گمنام', "category": 'انسان', "rarity": 'Uncommon', "price": 215, "xp": 14, "tradeable": True},
    {"name": 'جادوگر گمنام', "category": 'انسان', "rarity": 'Legendary', "price": 954, "xp": 63, "tradeable": True},
    {"name": 'شوالیه گمنام', "category": 'انسان', "rarity": 'Common', "price": 78, "xp": 5, "tradeable": True},
    {"name": 'دزد گمنام', "category": 'انسان', "rarity": 'Uncommon', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'کیمیاگر گمنام', "category": 'انسان', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'ناخدا گمنام', "category": 'انسان', "rarity": 'Common', "price": 46, "xp": 3, "tradeable": True},
    {"name": 'کوهنورد گمنام', "category": 'انسان', "rarity": 'Rare', "price": 135, "xp": 9, "tradeable": True},
    {"name": 'پیک گمنام', "category": 'انسان', "rarity": 'Legendary', "price": 902, "xp": 60, "tradeable": True},
    {"name": 'نگهبان گمنام', "category": 'انسان', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'راهزن گمنام', "category": 'انسان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'طبیب گمنام', "category": 'انسان', "rarity": 'Uncommon', "price": 146, "xp": 9, "tradeable": True},
    {"name": 'خنیاگر گمنام', "category": 'انسان', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'زائر گمنام', "category": 'انسان', "rarity": 'Uncommon', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'سرباز گمنام', "category": 'انسان', "rarity": 'Mythic', "price": 1046, "xp": 69, "tradeable": True},
    {"name": 'کاشف گمنام', "category": 'انسان', "rarity": 'Rare', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'کشاورز افسانه\u200cای', "category": 'انسان', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'شکارچی افسانه\u200cای', "category": 'انسان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'جادوگر افسانه\u200cای', "category": 'انسان', "rarity": 'Legendary', "price": 1553, "xp": 103, "tradeable": True},
    {"name": 'شوالیه افسانه\u200cای', "category": 'انسان', "rarity": 'Epic', "price": 277, "xp": 18, "tradeable": True},
    {"name": 'دزد افسانه\u200cای', "category": 'انسان', "rarity": 'Secret', "price": 4778, "xp": 318, "tradeable": True},
    {"name": 'کیمیاگر افسانه\u200cای', "category": 'انسان', "rarity": 'Legendary', "price": 794, "xp": 52, "tradeable": True},
    {"name": 'ناخدا افسانه\u200cای', "category": 'انسان', "rarity": 'Uncommon', "price": 140, "xp": 9, "tradeable": True},
    {"name": 'کوهنورد افسانه\u200cای', "category": 'انسان', "rarity": 'Legendary', "price": 756, "xp": 50, "tradeable": True},
    {"name": 'پیک افسانه\u200cای', "category": 'انسان', "rarity": 'Mythic', "price": 2387, "xp": 159, "tradeable": True},
    {"name": 'نگهبان افسانه\u200cای', "category": 'انسان', "rarity": 'Rare', "price": 267, "xp": 17, "tradeable": True},
    {"name": 'راهزن افسانه\u200cای', "category": 'انسان', "rarity": 'Legendary', "price": 1210, "xp": 80, "tradeable": True},
    {"name": 'طبیب افسانه\u200cای', "category": 'انسان', "rarity": 'Common', "price": 40, "xp": 2, "tradeable": True},
    {"name": 'خنیاگر افسانه\u200cای', "category": 'انسان', "rarity": 'Uncommon', "price": 116, "xp": 7, "tradeable": True},
    {"name": 'زائر افسانه\u200cای', "category": 'انسان', "rarity": 'Uncommon', "price": 236, "xp": 15, "tradeable": True},
    {"name": 'سرباز افسانه\u200cای', "category": 'انسان', "rarity": 'Uncommon', "price": 94, "xp": 6, "tradeable": True},
    {"name": 'کاشف افسانه\u200cای', "category": 'انسان', "rarity": 'Common', "price": 44, "xp": 2, "tradeable": True},
    {"name": 'کشاورز سرگردان', "category": 'انسان', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'شکارچی سرگردان', "category": 'انسان', "rarity": 'Legendary', "price": 996, "xp": 66, "tradeable": True},
    {"name": 'جادوگر سرگردان', "category": 'انسان', "rarity": 'Common', "price": 40, "xp": 2, "tradeable": True},
    {"name": 'شوالیه سرگردان', "category": 'انسان', "rarity": 'Uncommon', "price": 183, "xp": 12, "tradeable": True},
    {"name": 'دزد سرگردان', "category": 'انسان', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'کیمیاگر سرگردان', "category": 'انسان', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'ناخدا سرگردان', "category": 'انسان', "rarity": 'Legendary', "price": 470, "xp": 31, "tradeable": True},
    {"name": 'کوهنورد سرگردان', "category": 'انسان', "rarity": 'Uncommon', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'پیک سرگردان', "category": 'انسان', "rarity": 'Common', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'نگهبان سرگردان', "category": 'انسان', "rarity": 'Common', "price": 47, "xp": 3, "tradeable": True},
    {"name": 'راهزن سرگردان', "category": 'انسان', "rarity": 'Common', "price": 50, "xp": 3, "tradeable": True},
    {"name": 'طبیب سرگردان', "category": 'انسان', "rarity": 'Uncommon', "price": 150, "xp": 10, "tradeable": True},
    {"name": 'خنیاگر سرگردان', "category": 'انسان', "rarity": 'Mythic', "price": 2339, "xp": 155, "tradeable": True},
    {"name": 'زائر سرگردان', "category": 'انسان', "rarity": 'Epic', "price": 688, "xp": 45, "tradeable": True},
    {"name": 'سرباز سرگردان', "category": 'انسان', "rarity": 'Common', "price": 43, "xp": 2, "tradeable": True},
    {"name": 'کاشف سرگردان', "category": 'انسان', "rarity": 'Epic', "price": 702, "xp": 46, "tradeable": True},
    {"name": 'کشاورز دانا', "category": 'انسان', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'شکارچی دانا', "category": 'انسان', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'جادوگر دانا', "category": 'انسان', "rarity": 'Common', "price": 30, "xp": 2, "tradeable": True},
    {"name": 'شوالیه دانا', "category": 'انسان', "rarity": 'Uncommon', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'دزد دانا', "category": 'انسان', "rarity": 'Common', "price": 51, "xp": 3, "tradeable": True},
    {"name": 'کیمیاگر دانا', "category": 'انسان', "rarity": 'Common', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'ناخدا دانا', "category": 'انسان', "rarity": 'Uncommon', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'کوهنورد دانا', "category": 'انسان', "rarity": 'Legendary', "price": 788, "xp": 52, "tradeable": True},
    {"name": 'پیک دانا', "category": 'انسان', "rarity": 'Epic', "price": 699, "xp": 46, "tradeable": True},
    {"name": 'نگهبان دانا', "category": 'انسان', "rarity": 'Common', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'راهزن دانا', "category": 'انسان', "rarity": 'Legendary', "price": 655, "xp": 43, "tradeable": True},
    {"name": 'طبیب دانا', "category": 'انسان', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'خنیاگر دانا', "category": 'انسان', "rarity": 'Common', "price": 105, "xp": 7, "tradeable": True},
    {"name": 'زائر دانا', "category": 'انسان', "rarity": 'Rare', "price": 295, "xp": 19, "tradeable": True},
    {"name": 'سرباز دانا', "category": 'انسان', "rarity": 'Mythic', "price": 1935, "xp": 129, "tradeable": True},
    {"name": 'کاشف دانا', "category": 'انسان', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'کشاورز شجاع', "category": 'انسان', "rarity": 'Uncommon', "price": 113, "xp": 7, "tradeable": True},
    {"name": 'شکارچی شجاع', "category": 'انسان', "rarity": 'Rare', "price": 170, "xp": 11, "tradeable": True},
    {"name": 'جادوگر شجاع', "category": 'انسان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'شوالیه شجاع', "category": 'انسان', "rarity": 'Epic', "price": 686, "xp": 45, "tradeable": True},
    {"name": 'دزد شجاع', "category": 'انسان', "rarity": 'Epic', "price": 430, "xp": 28, "tradeable": True},
    {"name": 'کیمیاگر شجاع', "category": 'انسان', "rarity": 'Rare', "price": 312, "xp": 20, "tradeable": True},
    {"name": 'ناخدا شجاع', "category": 'انسان', "rarity": 'Epic', "price": 507, "xp": 33, "tradeable": True},
    {"name": 'کوهنورد شجاع', "category": 'انسان', "rarity": 'Uncommon', "price": 189, "xp": 12, "tradeable": True},
    {"name": 'پیک شجاع', "category": 'انسان', "rarity": 'Common', "price": 56, "xp": 3, "tradeable": True},
    {"name": 'نگهبان شجاع', "category": 'انسان', "rarity": 'Mythic', "price": 1548, "xp": 103, "tradeable": True},
    {"name": 'راهزن شجاع', "category": 'انسان', "rarity": 'Uncommon', "price": 172, "xp": 11, "tradeable": True},
    {"name": 'طبیب شجاع', "category": 'انسان', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'خنیاگر شجاع', "category": 'انسان', "rarity": 'Epic', "price": 400, "xp": 26, "tradeable": True},
    {"name": 'زائر شجاع', "category": 'انسان', "rarity": 'Rare', "price": 127, "xp": 8, "tradeable": True},
    {"name": 'سرباز شجاع', "category": 'انسان', "rarity": 'Epic', "price": 432, "xp": 28, "tradeable": True},
    {"name": 'کاشف شجاع', "category": 'انسان', "rarity": 'Common', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'کشاورز مرموز', "category": 'انسان', "rarity": 'Rare', "price": 208, "xp": 13, "tradeable": True},
    {"name": 'شکارچی مرموز', "category": 'انسان', "rarity": 'Common', "price": 78, "xp": 5, "tradeable": True},
    {"name": 'جادوگر مرموز', "category": 'انسان', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'شوالیه مرموز', "category": 'انسان', "rarity": 'Common', "price": 88, "xp": 5, "tradeable": True},
    {"name": 'دزد مرموز', "category": 'انسان', "rarity": 'Legendary', "price": 1023, "xp": 68, "tradeable": True},
    {"name": 'کیمیاگر مرموز', "category": 'انسان', "rarity": 'Epic', "price": 513, "xp": 34, "tradeable": True},
    {"name": 'ناخدا مرموز', "category": 'انسان', "rarity": 'Uncommon', "price": 146, "xp": 9, "tradeable": True},
    {"name": 'کوهنورد مرموز', "category": 'انسان', "rarity": 'Uncommon', "price": 120, "xp": 8, "tradeable": True},
    {"name": 'پیک مرموز', "category": 'انسان', "rarity": 'Rare', "price": 230, "xp": 15, "tradeable": True},
    {"name": 'نگهبان مرموز', "category": 'انسان', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'راهزن مرموز', "category": 'انسان', "rarity": 'Legendary', "price": 1022, "xp": 68, "tradeable": True},
    {"name": 'طبیب مرموز', "category": 'انسان', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'خنیاگر مرموز', "category": 'انسان', "rarity": 'Common', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'زائر مرموز', "category": 'انسان', "rarity": 'Rare', "price": 354, "xp": 23, "tradeable": True},
    {"name": 'سرباز مرموز', "category": 'انسان', "rarity": 'Uncommon', "price": 125, "xp": 8, "tradeable": True},
    {"name": 'کاشف مرموز', "category": 'انسان', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'کشاورز خردمند', "category": 'انسان', "rarity": 'Rare', "price": 149, "xp": 9, "tradeable": True},
    {"name": 'شکارچی خردمند', "category": 'انسان', "rarity": 'Epic', "price": 409, "xp": 27, "tradeable": True},
    {"name": 'جادوگر خردمند', "category": 'انسان', "rarity": 'Common', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'شوالیه خردمند', "category": 'انسان', "rarity": 'Uncommon', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'دزد خردمند', "category": 'انسان', "rarity": 'Epic', "price": 1004, "xp": 66, "tradeable": True},
    {"name": 'کیمیاگر خردمند', "category": 'انسان', "rarity": 'Uncommon', "price": 190, "xp": 12, "tradeable": True},
    {"name": 'ناخدا خردمند', "category": 'انسان', "rarity": 'Rare', "price": 381, "xp": 25, "tradeable": True},
    {"name": 'کوهنورد خردمند', "category": 'انسان', "rarity": 'Legendary', "price": 1629, "xp": 108, "tradeable": True},
    {"name": 'پیک خردمند', "category": 'انسان', "rarity": 'Rare', "price": 369, "xp": 24, "tradeable": True},
    {"name": 'نگهبان خردمند', "category": 'انسان', "rarity": 'Rare', "price": 307, "xp": 20, "tradeable": True},
    {"name": 'راهزن خردمند', "category": 'انسان', "rarity": 'Uncommon', "price": 112, "xp": 7, "tradeable": True},
    {"name": 'طبیب خردمند', "category": 'انسان', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'خنیاگر خردمند', "category": 'انسان', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'زائر خردمند', "category": 'انسان', "rarity": 'Common', "price": 90, "xp": 6, "tradeable": True},
    {"name": 'سرباز خردمند', "category": 'انسان', "rarity": 'Rare', "price": 153, "xp": 10, "tradeable": True},
    {"name": 'کاشف خردمند', "category": 'انسان', "rarity": 'Common', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'کشاورز زخمی', "category": 'انسان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'شکارچی زخمی', "category": 'انسان', "rarity": 'Rare', "price": 157, "xp": 10, "tradeable": True},
    {"name": 'جادوگر زخمی', "category": 'انسان', "rarity": 'Rare', "price": 324, "xp": 21, "tradeable": True},
    {"name": 'شوالیه زخمی', "category": 'انسان', "rarity": 'Legendary', "price": 1200, "xp": 80, "tradeable": True},
    {"name": 'دزد زخمی', "category": 'انسان', "rarity": 'Epic', "price": 709, "xp": 47, "tradeable": True},
    {"name": 'کیمیاگر زخمی', "category": 'انسان', "rarity": 'Rare', "price": 242, "xp": 16, "tradeable": True},
    {"name": 'ناخدا زخمی', "category": 'انسان', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'کوهنورد زخمی', "category": 'انسان', "rarity": 'Common', "price": 94, "xp": 6, "tradeable": True},
    {"name": 'پیک زخمی', "category": 'انسان', "rarity": 'Epic', "price": 474, "xp": 31, "tradeable": True},
    {"name": 'نگهبان زخمی', "category": 'انسان', "rarity": 'Uncommon', "price": 175, "xp": 11, "tradeable": True},
    {"name": 'راهزن زخمی', "category": 'انسان', "rarity": 'Rare', "price": 179, "xp": 11, "tradeable": True},
    {"name": 'طبیب زخمی', "category": 'انسان', "rarity": 'Common', "price": 79, "xp": 5, "tradeable": True},
    {"name": 'خنیاگر زخمی', "category": 'انسان', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'زائر زخمی', "category": 'انسان', "rarity": 'Common', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'سرباز زخمی', "category": 'انسان', "rarity": 'Legendary', "price": 1153, "xp": 76, "tradeable": True},
    {"name": 'کاشف زخمی', "category": 'انسان', "rarity": 'Common', "price": 37, "xp": 2, "tradeable": True},
    {"name": 'کشاورز بی\u200cباک', "category": 'انسان', "rarity": 'Common', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'شکارچی بی\u200cباک', "category": 'انسان', "rarity": 'Common', "price": 41, "xp": 2, "tradeable": True},
    {"name": 'جادوگر بی\u200cباک', "category": 'انسان', "rarity": 'Legendary', "price": 1006, "xp": 67, "tradeable": True},
    {"name": 'شوالیه بی\u200cباک', "category": 'انسان', "rarity": 'Uncommon', "price": 103, "xp": 6, "tradeable": True},
    {"name": 'دزد بی\u200cباک', "category": 'انسان', "rarity": 'Rare', "price": 235, "xp": 15, "tradeable": True},
    {"name": 'کیمیاگر بی\u200cباک', "category": 'انسان', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'ناخدا بی\u200cباک', "category": 'انسان', "rarity": 'Uncommon', "price": 104, "xp": 6, "tradeable": True},
    {"name": 'کوهنورد بی\u200cباک', "category": 'انسان', "rarity": 'Common', "price": 58, "xp": 3, "tradeable": True},
    {"name": 'پیک بی\u200cباک', "category": 'انسان', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'نگهبان بی\u200cباک', "category": 'انسان', "rarity": 'Legendary', "price": 1168, "xp": 77, "tradeable": True},
    {"name": 'راهزن بی\u200cباک', "category": 'انسان', "rarity": 'Rare', "price": 170, "xp": 11, "tradeable": True},
    {"name": 'طبیب بی\u200cباک', "category": 'انسان', "rarity": 'Epic', "price": 415, "xp": 27, "tradeable": True},
    {"name": 'خنیاگر بی\u200cباک', "category": 'انسان', "rarity": 'Rare', "price": 215, "xp": 14, "tradeable": True},
    {"name": 'زائر بی\u200cباک', "category": 'انسان', "rarity": 'Uncommon', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'سرباز بی\u200cباک', "category": 'انسان', "rarity": 'Uncommon', "price": 128, "xp": 8, "tradeable": True},
    {"name": 'کاشف بی\u200cباک', "category": 'انسان', "rarity": 'Uncommon', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'کشاورز خاموش', "category": 'انسان', "rarity": 'Epic', "price": 349, "xp": 23, "tradeable": True},
    {"name": 'شکارچی خاموش', "category": 'انسان', "rarity": 'Common', "price": 101, "xp": 6, "tradeable": True},
    {"name": 'جادوگر خاموش', "category": 'انسان', "rarity": 'Uncommon', "price": 246, "xp": 16, "tradeable": True},
    {"name": 'شوالیه خاموش', "category": 'انسان', "rarity": 'Common', "price": 116, "xp": 7, "tradeable": True},
    {"name": 'دزد خاموش', "category": 'انسان', "rarity": 'Uncommon', "price": 123, "xp": 8, "tradeable": True},
    {"name": 'کیمیاگر خاموش', "category": 'انسان', "rarity": 'Common', "price": 38, "xp": 2, "tradeable": True},
    {"name": 'ناخدا خاموش', "category": 'انسان', "rarity": 'Uncommon', "price": 193, "xp": 12, "tradeable": True},
    {"name": 'کوهنورد خاموش', "category": 'انسان', "rarity": 'Rare', "price": 237, "xp": 15, "tradeable": True},
    {"name": 'پیک خاموش', "category": 'انسان', "rarity": 'Epic', "price": 442, "xp": 29, "tradeable": True},
    {"name": 'نگهبان خاموش', "category": 'انسان', "rarity": 'Common', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'راهزن خاموش', "category": 'انسان', "rarity": 'Epic', "price": 560, "xp": 37, "tradeable": True},
    {"name": 'طبیب خاموش', "category": 'انسان', "rarity": 'Uncommon', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'خنیاگر خاموش', "category": 'انسان', "rarity": 'Rare', "price": 396, "xp": 26, "tradeable": True},
    {"name": 'زائر خاموش', "category": 'انسان', "rarity": 'Common', "price": 101, "xp": 6, "tradeable": True},
    {"name": 'سرباز خاموش', "category": 'انسان', "rarity": 'Uncommon', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'کاشف خاموش', "category": 'انسان', "rarity": 'Common', "price": 58, "xp": 3, "tradeable": True},
    {"name": 'کشاورز سرکش', "category": 'انسان', "rarity": 'Uncommon', "price": 193, "xp": 12, "tradeable": True},
    {"name": 'شکارچی سرکش', "category": 'انسان', "rarity": 'Common', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'جادوگر سرکش', "category": 'انسان', "rarity": 'Rare', "price": 244, "xp": 16, "tradeable": True},
    {"name": 'شوالیه سرکش', "category": 'انسان', "rarity": 'Common', "price": 51, "xp": 3, "tradeable": True},
    {"name": 'دزد سرکش', "category": 'انسان', "rarity": 'Rare', "price": 189, "xp": 12, "tradeable": True},
    {"name": 'کیمیاگر سرکش', "category": 'انسان', "rarity": 'Rare', "price": 302, "xp": 20, "tradeable": True},
    {"name": 'ناخدا سرکش', "category": 'انسان', "rarity": 'Mythic', "price": 3196, "xp": 213, "tradeable": True},
    {"name": 'کوهنورد سرکش', "category": 'انسان', "rarity": 'Mythic', "price": 1535, "xp": 102, "tradeable": True},
    {"name": 'پیک سرکش', "category": 'انسان', "rarity": 'Common', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'نگهبان سرکش', "category": 'انسان', "rarity": 'Uncommon', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'راهزن سرکش', "category": 'انسان', "rarity": 'Common', "price": 76, "xp": 5, "tradeable": True},
    {"name": 'طبیب سرکش', "category": 'انسان', "rarity": 'Common', "price": 78, "xp": 5, "tradeable": True},
    {"name": 'خنیاگر سرکش', "category": 'انسان', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'زائر سرکش', "category": 'انسان', "rarity": 'Uncommon', "price": 135, "xp": 9, "tradeable": True},
    {"name": 'سرباز سرکش', "category": 'انسان', "rarity": 'Common', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'کاشف سرکش', "category": 'انسان', "rarity": 'Common', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'کشاورز فراری', "category": 'انسان', "rarity": 'Uncommon', "price": 63, "xp": 4, "tradeable": True},
    {"name": 'شکارچی فراری', "category": 'انسان', "rarity": 'Epic', "price": 458, "xp": 30, "tradeable": True},
    {"name": 'جادوگر فراری', "category": 'انسان', "rarity": 'Common', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'شوالیه فراری', "category": 'انسان', "rarity": 'Rare', "price": 275, "xp": 18, "tradeable": True},
    {"name": 'دزد فراری', "category": 'انسان', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'کیمیاگر فراری', "category": 'انسان', "rarity": 'Uncommon', "price": 160, "xp": 10, "tradeable": True},
    {"name": 'ناخدا فراری', "category": 'انسان', "rarity": 'Uncommon', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'کوهنورد فراری', "category": 'انسان', "rarity": 'Common', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'پیک فراری', "category": 'انسان', "rarity": 'Common', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'نگهبان فراری', "category": 'انسان', "rarity": 'Common', "price": 30, "xp": 2, "tradeable": True},
    {"name": 'راهزن فراری', "category": 'انسان', "rarity": 'Rare', "price": 340, "xp": 22, "tradeable": True},
    {"name": 'طبیب فراری', "category": 'انسان', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'خنیاگر فراری', "category": 'انسان', "rarity": 'Uncommon', "price": 148, "xp": 9, "tradeable": True},
    {"name": 'زائر فراری', "category": 'انسان', "rarity": 'Rare', "price": 222, "xp": 14, "tradeable": True},
    {"name": 'سرباز فراری', "category": 'انسان', "rarity": 'Uncommon', "price": 122, "xp": 8, "tradeable": True},
    {"name": 'کاشف فراری', "category": 'انسان', "rarity": 'Epic', "price": 258, "xp": 17, "tradeable": True},
    {"name": 'کشاورز وفادار', "category": 'انسان', "rarity": 'Rare', "price": 269, "xp": 17, "tradeable": True},
    {"name": 'شکارچی وفادار', "category": 'انسان', "rarity": 'Uncommon', "price": 156, "xp": 10, "tradeable": True},
    {"name": 'جادوگر وفادار', "category": 'انسان', "rarity": 'Legendary', "price": 1508, "xp": 100, "tradeable": True},
    {"name": 'شوالیه وفادار', "category": 'انسان', "rarity": 'Uncommon', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'دزد وفادار', "category": 'انسان', "rarity": 'Rare', "price": 201, "xp": 13, "tradeable": True},
    {"name": 'کیمیاگر وفادار', "category": 'انسان', "rarity": 'Common', "price": 41, "xp": 2, "tradeable": True},
    {"name": 'ناخدا وفادار', "category": 'انسان', "rarity": 'Rare', "price": 278, "xp": 18, "tradeable": True},
    {"name": 'کوهنورد وفادار', "category": 'انسان', "rarity": 'Rare', "price": 215, "xp": 14, "tradeable": True},
    {"name": 'پیک وفادار', "category": 'انسان', "rarity": 'Rare', "price": 231, "xp": 15, "tradeable": True},
    {"name": 'نگهبان وفادار', "category": 'انسان', "rarity": 'Uncommon', "price": 236, "xp": 15, "tradeable": True},
    {"name": 'راهزن وفادار', "category": 'انسان', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'طبیب وفادار', "category": 'انسان', "rarity": 'Rare', "price": 192, "xp": 12, "tradeable": True},
    {"name": 'خنیاگر وفادار', "category": 'انسان', "rarity": 'Rare', "price": 298, "xp": 19, "tradeable": True},
    {"name": 'زائر وفادار', "category": 'انسان', "rarity": 'Uncommon', "price": 163, "xp": 10, "tradeable": True},
    {"name": 'سرباز وفادار', "category": 'انسان', "rarity": 'Common', "price": 59, "xp": 3, "tradeable": True},
    {"name": 'کاشف وفادار', "category": 'انسان', "rarity": 'Mythic', "price": 2994, "xp": 199, "tradeable": True},
    {"name": 'کشاورز تنها', "category": 'انسان', "rarity": 'Epic', "price": 270, "xp": 18, "tradeable": True},
    {"name": 'شکارچی تنها', "category": 'انسان', "rarity": 'Uncommon', "price": 135, "xp": 9, "tradeable": True},
    {"name": 'جادوگر تنها', "category": 'انسان', "rarity": 'Uncommon', "price": 206, "xp": 13, "tradeable": True},
    {"name": 'شوالیه تنها', "category": 'انسان', "rarity": 'Rare', "price": 209, "xp": 13, "tradeable": True},
    {"name": 'دزد تنها', "category": 'انسان', "rarity": 'Common', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'کیمیاگر تنها', "category": 'انسان', "rarity": 'Rare', "price": 146, "xp": 9, "tradeable": True},
    {"name": 'ناخدا تنها', "category": 'انسان', "rarity": 'Uncommon', "price": 138, "xp": 9, "tradeable": True},
    {"name": 'کوهنورد تنها', "category": 'انسان', "rarity": 'Common', "price": 33, "xp": 2, "tradeable": True},
    {"name": 'پیک تنها', "category": 'انسان', "rarity": 'Common', "price": 45, "xp": 3, "tradeable": True},
    {"name": 'نگهبان تنها', "category": 'انسان', "rarity": 'Common', "price": 51, "xp": 3, "tradeable": True},
    {"name": 'راهزن تنها', "category": 'انسان', "rarity": 'Rare', "price": 438, "xp": 29, "tradeable": True},
    {"name": 'طبیب تنها', "category": 'انسان', "rarity": 'Uncommon', "price": 234, "xp": 15, "tradeable": True},
    {"name": 'خنیاگر تنها', "category": 'انسان', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'زائر تنها', "category": 'انسان', "rarity": 'Common', "price": 111, "xp": 7, "tradeable": True},
    {"name": 'سرباز تنها', "category": 'انسان', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'کاشف تنها', "category": 'انسان', "rarity": 'Rare', "price": 322, "xp": 21, "tradeable": True},
    {"name": 'کشاورز پرشور', "category": 'انسان', "rarity": 'Rare', "price": 309, "xp": 20, "tradeable": True},
    {"name": 'شکارچی پرشور', "category": 'انسان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'جادوگر پرشور', "category": 'انسان', "rarity": 'Uncommon', "price": 226, "xp": 15, "tradeable": True},
    {"name": 'شوالیه پرشور', "category": 'انسان', "rarity": 'Uncommon', "price": 177, "xp": 11, "tradeable": True},
    {"name": 'دزد پرشور', "category": 'انسان', "rarity": 'Uncommon', "price": 195, "xp": 13, "tradeable": True},
    {"name": 'کیمیاگر پرشور', "category": 'انسان', "rarity": 'Legendary', "price": 1045, "xp": 69, "tradeable": True},
    {"name": 'ناخدا پرشور', "category": 'انسان', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'کوهنورد پرشور', "category": 'انسان', "rarity": 'Rare', "price": 134, "xp": 8, "tradeable": True},
    {"name": 'پیک پرشور', "category": 'انسان', "rarity": 'Uncommon', "price": 154, "xp": 10, "tradeable": True},
    {"name": 'نگهبان پرشور', "category": 'انسان', "rarity": 'Rare', "price": 205, "xp": 13, "tradeable": True},
    {"name": 'راهزن پرشور', "category": 'انسان', "rarity": 'Common', "price": 120, "xp": 8, "tradeable": True},
    {"name": 'طبیب پرشور', "category": 'انسان', "rarity": 'Common', "price": 51, "xp": 3, "tradeable": True},
    {"name": 'خنیاگر پرشور', "category": 'انسان', "rarity": 'Uncommon', "price": 243, "xp": 16, "tradeable": True},
    {"name": 'زائر پرشور', "category": 'انسان', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'سرباز پرشور', "category": 'انسان', "rarity": 'Rare', "price": 164, "xp": 10, "tradeable": True},
    {"name": 'کاشف پرشور', "category": 'انسان', "rarity": 'Rare', "price": 244, "xp": 16, "tradeable": True},
    {"name": 'کشاورز کهنه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 196, "xp": 13, "tradeable": True},
    {"name": 'شکارچی کهنه\u200cکار', "category": 'انسان', "rarity": 'Epic', "price": 776, "xp": 51, "tradeable": True},
    {"name": 'جادوگر کهنه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 113, "xp": 7, "tradeable": True},
    {"name": 'شوالیه کهنه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 34, "xp": 2, "tradeable": True},
    {"name": 'دزد کهنه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 342, "xp": 22, "tradeable": True},
    {"name": 'کیمیاگر کهنه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 438, "xp": 29, "tradeable": True},
    {"name": 'ناخدا کهنه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 171, "xp": 11, "tradeable": True},
    {"name": 'کوهنورد کهنه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 108, "xp": 7, "tradeable": True},
    {"name": 'پیک کهنه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 262, "xp": 17, "tradeable": True},
    {"name": 'نگهبان کهنه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 130, "xp": 8, "tradeable": True},
    {"name": 'راهزن کهنه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 93, "xp": 6, "tradeable": True},
    {"name": 'طبیب کهنه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 86, "xp": 5, "tradeable": True},
    {"name": 'خنیاگر کهنه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 43, "xp": 2, "tradeable": True},
    {"name": 'زائر کهنه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 163, "xp": 10, "tradeable": True},
    {"name": 'سرباز کهنه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 111, "xp": 7, "tradeable": True},
    {"name": 'کاشف کهنه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 167, "xp": 11, "tradeable": True},
    {"name": 'کشاورز تازه\u200cکار', "category": 'انسان', "rarity": 'Legendary', "price": 1141, "xp": 76, "tradeable": True},
    {"name": 'شکارچی تازه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 127, "xp": 8, "tradeable": True},
    {"name": 'جادوگر تازه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'شوالیه تازه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 84, "xp": 5, "tradeable": True},
    {"name": 'دزد تازه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 108, "xp": 7, "tradeable": True},
    {"name": 'کیمیاگر تازه\u200cکار', "category": 'انسان', "rarity": 'Epic', "price": 599, "xp": 39, "tradeable": True},
    {"name": 'ناخدا تازه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 90, "xp": 6, "tradeable": True},
    {"name": 'کوهنورد تازه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 313, "xp": 20, "tradeable": True},
    {"name": 'پیک تازه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'نگهبان تازه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'راهزن تازه\u200cکار', "category": 'انسان', "rarity": 'Rare', "price": 209, "xp": 13, "tradeable": True},
    {"name": 'طبیب تازه\u200cکار', "category": 'انسان', "rarity": 'Legendary', "price": 501, "xp": 33, "tradeable": True},
    {"name": 'خنیاگر تازه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 177, "xp": 11, "tradeable": True},
    {"name": 'زائر تازه\u200cکار', "category": 'انسان', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'سرباز تازه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'کاشف تازه\u200cکار', "category": 'انسان', "rarity": 'Uncommon', "price": 161, "xp": 10, "tradeable": True},
    {"name": 'روباه برفی', "category": 'حیوان', "rarity": 'Legendary', "price": 710, "xp": 47, "tradeable": True},
    {"name": 'شیر برفی', "category": 'حیوان', "rarity": 'Epic', "price": 934, "xp": 62, "tradeable": True},
    {"name": 'عقاب برفی', "category": 'حیوان', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'گرگ برفی', "category": 'حیوان', "rarity": 'Uncommon', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'ببر برفی', "category": 'حیوان', "rarity": 'Mythic', "price": 3080, "xp": 205, "tradeable": True},
    {"name": 'پلنگ برفی', "category": 'حیوان', "rarity": 'Epic', "price": 513, "xp": 34, "tradeable": True},
    {"name": 'خرس برفی', "category": 'حیوان', "rarity": 'Common', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'گوزن برفی', "category": 'حیوان', "rarity": 'Uncommon', "price": 137, "xp": 9, "tradeable": True},
    {"name": 'مار برفی', "category": 'حیوان', "rarity": 'Epic', "price": 526, "xp": 35, "tradeable": True},
    {"name": 'کرکس برفی', "category": 'حیوان', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'سنجاب برفی', "category": 'حیوان', "rarity": 'Legendary', "price": 1339, "xp": 89, "tradeable": True},
    {"name": 'جغد برفی', "category": 'حیوان', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'شاهین برفی', "category": 'حیوان', "rarity": 'Uncommon', "price": 152, "xp": 10, "tradeable": True},
    {"name": 'آهو برفی', "category": 'حیوان', "rarity": 'Common', "price": 44, "xp": 2, "tradeable": True},
    {"name": 'خرگوش برفی', "category": 'حیوان', "rarity": 'Common', "price": 79, "xp": 5, "tradeable": True},
    {"name": 'روباه طلایی', "category": 'حیوان', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'شیر طلایی', "category": 'حیوان', "rarity": 'Uncommon', "price": 199, "xp": 13, "tradeable": True},
    {"name": 'عقاب طلایی', "category": 'حیوان', "rarity": 'Rare', "price": 243, "xp": 16, "tradeable": True},
    {"name": 'گرگ طلایی', "category": 'حیوان', "rarity": 'Rare', "price": 407, "xp": 27, "tradeable": True},
    {"name": 'ببر طلایی', "category": 'حیوان', "rarity": 'Legendary', "price": 795, "xp": 53, "tradeable": True},
    {"name": 'پلنگ طلایی', "category": 'حیوان', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'خرس طلایی', "category": 'حیوان', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'گوزن طلایی', "category": 'حیوان', "rarity": 'Rare', "price": 495, "xp": 33, "tradeable": True},
    {"name": 'مار طلایی', "category": 'حیوان', "rarity": 'Uncommon', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'کرکس طلایی', "category": 'حیوان', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'سنجاب طلایی', "category": 'حیوان', "rarity": 'Epic', "price": 371, "xp": 24, "tradeable": True},
    {"name": 'جغد طلایی', "category": 'حیوان', "rarity": 'Uncommon', "price": 52, "xp": 3, "tradeable": True},
    {"name": 'شاهین طلایی', "category": 'حیوان', "rarity": 'Common', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'آهو طلایی', "category": 'حیوان', "rarity": 'Common', "price": 90, "xp": 6, "tradeable": True},
    {"name": 'خرگوش طلایی', "category": 'حیوان', "rarity": 'Common', "price": 56, "xp": 3, "tradeable": True},
    {"name": 'روباه سیاه', "category": 'حیوان', "rarity": 'Common', "price": 28, "xp": 1, "tradeable": True},
    {"name": 'شیر سیاه', "category": 'حیوان', "rarity": 'Mythic', "price": 1476, "xp": 98, "tradeable": True},
    {"name": 'عقاب سیاه', "category": 'حیوان', "rarity": 'Rare', "price": 198, "xp": 13, "tradeable": True},
    {"name": 'گرگ سیاه', "category": 'حیوان', "rarity": 'Common', "price": 30, "xp": 2, "tradeable": True},
    {"name": 'ببر سیاه', "category": 'حیوان', "rarity": 'Rare', "price": 229, "xp": 15, "tradeable": True},
    {"name": 'پلنگ سیاه', "category": 'حیوان', "rarity": 'Common', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'خرس سیاه', "category": 'حیوان', "rarity": 'Uncommon', "price": 78, "xp": 5, "tradeable": True},
    {"name": 'گوزن سیاه', "category": 'حیوان', "rarity": 'Rare', "price": 416, "xp": 27, "tradeable": True},
    {"name": 'مار سیاه', "category": 'حیوان', "rarity": 'Uncommon', "price": 147, "xp": 9, "tradeable": True},
    {"name": 'کرکس سیاه', "category": 'حیوان', "rarity": 'Common', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'سنجاب سیاه', "category": 'حیوان', "rarity": 'Uncommon', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'جغد سیاه', "category": 'حیوان', "rarity": 'Uncommon', "price": 164, "xp": 10, "tradeable": True},
    {"name": 'شاهین سیاه', "category": 'حیوان', "rarity": 'Common', "price": 71, "xp": 4, "tradeable": True},
    {"name": 'آهو سیاه', "category": 'حیوان', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'خرگوش سیاه', "category": 'حیوان', "rarity": 'Common', "price": 115, "xp": 7, "tradeable": True},
    {"name": 'روباه سفید', "category": 'حیوان', "rarity": 'Common', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'شیر سفید', "category": 'حیوان', "rarity": 'Uncommon', "price": 144, "xp": 9, "tradeable": True},
    {"name": 'عقاب سفید', "category": 'حیوان', "rarity": 'Rare', "price": 464, "xp": 30, "tradeable": True},
    {"name": 'گرگ سفید', "category": 'حیوان', "rarity": 'Common', "price": 108, "xp": 7, "tradeable": True},
    {"name": 'ببر سفید', "category": 'حیوان', "rarity": 'Uncommon', "price": 175, "xp": 11, "tradeable": True},
    {"name": 'پلنگ سفید', "category": 'حیوان', "rarity": 'Rare', "price": 283, "xp": 18, "tradeable": True},
    {"name": 'خرس سفید', "category": 'حیوان', "rarity": 'Rare', "price": 393, "xp": 26, "tradeable": True},
    {"name": 'گوزن سفید', "category": 'حیوان', "rarity": 'Rare', "price": 300, "xp": 20, "tradeable": True},
    {"name": 'مار سفید', "category": 'حیوان', "rarity": 'Epic', "price": 333, "xp": 22, "tradeable": True},
    {"name": 'کرکس سفید', "category": 'حیوان', "rarity": 'Rare', "price": 371, "xp": 24, "tradeable": True},
    {"name": 'سنجاب سفید', "category": 'حیوان', "rarity": 'Uncommon', "price": 163, "xp": 10, "tradeable": True},
    {"name": 'جغد سفید', "category": 'حیوان', "rarity": 'Uncommon', "price": 207, "xp": 13, "tradeable": True},
    {"name": 'شاهین سفید', "category": 'حیوان', "rarity": 'Rare', "price": 290, "xp": 19, "tradeable": True},
    {"name": 'آهو سفید', "category": 'حیوان', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'خرگوش سفید', "category": 'حیوان', "rarity": 'Rare', "price": 283, "xp": 18, "tradeable": True},
    {"name": 'روباه زخمی', "category": 'حیوان', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'شیر زخمی', "category": 'حیوان', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'عقاب زخمی', "category": 'حیوان', "rarity": 'Epic', "price": 291, "xp": 19, "tradeable": True},
    {"name": 'گرگ زخمی', "category": 'حیوان', "rarity": 'Rare', "price": 208, "xp": 13, "tradeable": True},
    {"name": 'ببر زخمی', "category": 'حیوان', "rarity": 'Common', "price": 86, "xp": 5, "tradeable": True},
    {"name": 'پلنگ زخمی', "category": 'حیوان', "rarity": 'Common', "price": 65, "xp": 4, "tradeable": True},
    {"name": 'خرس زخمی', "category": 'حیوان', "rarity": 'Uncommon', "price": 135, "xp": 9, "tradeable": True},
    {"name": 'گوزن زخمی', "category": 'حیوان', "rarity": 'Rare', "price": 111, "xp": 7, "tradeable": True},
    {"name": 'مار زخمی', "category": 'حیوان', "rarity": 'Uncommon', "price": 157, "xp": 10, "tradeable": True},
    {"name": 'کرکس زخمی', "category": 'حیوان', "rarity": 'Common', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'سنجاب زخمی', "category": 'حیوان', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'جغد زخمی', "category": 'حیوان', "rarity": 'Common', "price": 40, "xp": 2, "tradeable": True},
    {"name": 'شاهین زخمی', "category": 'حیوان', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'آهو زخمی', "category": 'حیوان', "rarity": 'Uncommon', "price": 116, "xp": 7, "tradeable": True},
    {"name": 'خرگوش زخمی', "category": 'حیوان', "rarity": 'Uncommon', "price": 150, "xp": 10, "tradeable": True},
    {"name": 'روباه وحشی', "category": 'حیوان', "rarity": 'Common', "price": 97, "xp": 6, "tradeable": True},
    {"name": 'شیر وحشی', "category": 'حیوان', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'عقاب وحشی', "category": 'حیوان', "rarity": 'Uncommon', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'گرگ وحشی', "category": 'حیوان', "rarity": 'Legendary', "price": 833, "xp": 55, "tradeable": True},
    {"name": 'ببر وحشی', "category": 'حیوان', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'پلنگ وحشی', "category": 'حیوان', "rarity": 'Rare', "price": 179, "xp": 11, "tradeable": True},
    {"name": 'خرس وحشی', "category": 'حیوان', "rarity": 'Uncommon', "price": 123, "xp": 8, "tradeable": True},
    {"name": 'گوزن وحشی', "category": 'حیوان', "rarity": 'Epic', "price": 501, "xp": 33, "tradeable": True},
    {"name": 'مار وحشی', "category": 'حیوان', "rarity": 'Epic', "price": 285, "xp": 19, "tradeable": True},
    {"name": 'کرکس وحشی', "category": 'حیوان', "rarity": 'Uncommon', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'سنجاب وحشی', "category": 'حیوان', "rarity": 'Uncommon', "price": 187, "xp": 12, "tradeable": True},
    {"name": 'جغد وحشی', "category": 'حیوان', "rarity": 'Uncommon', "price": 88, "xp": 5, "tradeable": True},
    {"name": 'شاهین وحشی', "category": 'حیوان', "rarity": 'Mythic', "price": 1837, "xp": 122, "tradeable": True},
    {"name": 'آهو وحشی', "category": 'حیوان', "rarity": 'Uncommon', "price": 128, "xp": 8, "tradeable": True},
    {"name": 'خرگوش وحشی', "category": 'حیوان', "rarity": 'Epic', "price": 496, "xp": 33, "tradeable": True},
    {"name": 'روباه رام', "category": 'حیوان', "rarity": 'Uncommon', "price": 79, "xp": 5, "tradeable": True},
    {"name": 'شیر رام', "category": 'حیوان', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'عقاب رام', "category": 'حیوان', "rarity": 'Rare', "price": 164, "xp": 10, "tradeable": True},
    {"name": 'گرگ رام', "category": 'حیوان', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'ببر رام', "category": 'حیوان', "rarity": 'Rare', "price": 344, "xp": 22, "tradeable": True},
    {"name": 'پلنگ رام', "category": 'حیوان', "rarity": 'Epic', "price": 352, "xp": 23, "tradeable": True},
    {"name": 'خرس رام', "category": 'حیوان', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'گوزن رام', "category": 'حیوان', "rarity": 'Common', "price": 34, "xp": 2, "tradeable": True},
    {"name": 'مار رام', "category": 'حیوان', "rarity": 'Rare', "price": 383, "xp": 25, "tradeable": True},
    {"name": 'کرکس رام', "category": 'حیوان', "rarity": 'Uncommon', "price": 198, "xp": 13, "tradeable": True},
    {"name": 'سنجاب رام', "category": 'حیوان', "rarity": 'Epic', "price": 699, "xp": 46, "tradeable": True},
    {"name": 'جغد رام', "category": 'حیوان', "rarity": 'Uncommon', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'شاهین رام', "category": 'حیوان', "rarity": 'Uncommon', "price": 162, "xp": 10, "tradeable": True},
    {"name": 'آهو رام', "category": 'حیوان', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'خرگوش رام', "category": 'حیوان', "rarity": 'Common', "price": 71, "xp": 4, "tradeable": True},
    {"name": 'روباه افسانه\u200cای', "category": 'حیوان', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'شیر افسانه\u200cای', "category": 'حیوان', "rarity": 'Epic', "price": 363, "xp": 24, "tradeable": True},
    {"name": 'عقاب افسانه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 194, "xp": 12, "tradeable": True},
    {"name": 'گرگ افسانه\u200cای', "category": 'حیوان', "rarity": 'Legendary', "price": 538, "xp": 35, "tradeable": True},
    {"name": 'ببر افسانه\u200cای', "category": 'حیوان', "rarity": 'Epic', "price": 415, "xp": 27, "tradeable": True},
    {"name": 'پلنگ افسانه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 125, "xp": 8, "tradeable": True},
    {"name": 'خرس افسانه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 188, "xp": 12, "tradeable": True},
    {"name": 'گوزن افسانه\u200cای', "category": 'حیوان', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'مار افسانه\u200cای', "category": 'حیوان', "rarity": 'Rare', "price": 365, "xp": 24, "tradeable": True},
    {"name": 'کرکس افسانه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 149, "xp": 9, "tradeable": True},
    {"name": 'سنجاب افسانه\u200cای', "category": 'حیوان', "rarity": 'Common', "price": 97, "xp": 6, "tradeable": True},
    {"name": 'جغد افسانه\u200cای', "category": 'حیوان', "rarity": 'Rare', "price": 172, "xp": 11, "tradeable": True},
    {"name": 'شاهین افسانه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'آهو افسانه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 148, "xp": 9, "tradeable": True},
    {"name": 'خرگوش افسانه\u200cای', "category": 'حیوان', "rarity": 'Rare', "price": 189, "xp": 12, "tradeable": True},
    {"name": 'روباه غول\u200cپیکر', "category": 'حیوان', "rarity": 'Uncommon', "price": 120, "xp": 8, "tradeable": True},
    {"name": 'شیر غول\u200cپیکر', "category": 'حیوان', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'عقاب غول\u200cپیکر', "category": 'حیوان', "rarity": 'Rare', "price": 152, "xp": 10, "tradeable": True},
    {"name": 'گرگ غول\u200cپیکر', "category": 'حیوان', "rarity": 'Common', "price": 86, "xp": 5, "tradeable": True},
    {"name": 'ببر غول\u200cپیکر', "category": 'حیوان', "rarity": 'Rare', "price": 328, "xp": 21, "tradeable": True},
    {"name": 'پلنگ غول\u200cپیکر', "category": 'حیوان', "rarity": 'Rare', "price": 372, "xp": 24, "tradeable": True},
    {"name": 'خرس غول\u200cپیکر', "category": 'حیوان', "rarity": 'Common', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'گوزن غول\u200cپیکر', "category": 'حیوان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'مار غول\u200cپیکر', "category": 'حیوان', "rarity": 'Secret', "price": 4930, "xp": 328, "tradeable": True},
    {"name": 'کرکس غول\u200cپیکر', "category": 'حیوان', "rarity": 'Epic', "price": 354, "xp": 23, "tradeable": True},
    {"name": 'سنجاب غول\u200cپیکر', "category": 'حیوان', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'جغد غول\u200cپیکر', "category": 'حیوان', "rarity": 'Common', "price": 50, "xp": 3, "tradeable": True},
    {"name": 'شاهین غول\u200cپیکر', "category": 'حیوان', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'آهو غول\u200cپیکر', "category": 'حیوان', "rarity": 'Rare', "price": 236, "xp": 15, "tradeable": True},
    {"name": 'خرگوش غول\u200cپیکر', "category": 'حیوان', "rarity": 'Common', "price": 86, "xp": 5, "tradeable": True},
    {"name": 'روباه کوچک', "category": 'حیوان', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'شیر کوچک', "category": 'حیوان', "rarity": 'Rare', "price": 319, "xp": 21, "tradeable": True},
    {"name": 'عقاب کوچک', "category": 'حیوان', "rarity": 'Common', "price": 112, "xp": 7, "tradeable": True},
    {"name": 'گرگ کوچک', "category": 'حیوان', "rarity": 'Common', "price": 88, "xp": 5, "tradeable": True},
    {"name": 'ببر کوچک', "category": 'حیوان', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'پلنگ کوچک', "category": 'حیوان', "rarity": 'Uncommon', "price": 120, "xp": 8, "tradeable": True},
    {"name": 'خرس کوچک', "category": 'حیوان', "rarity": 'Epic', "price": 464, "xp": 30, "tradeable": True},
    {"name": 'گوزن کوچک', "category": 'حیوان', "rarity": 'Common', "price": 112, "xp": 7, "tradeable": True},
    {"name": 'مار کوچک', "category": 'حیوان', "rarity": 'Uncommon', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'کرکس کوچک', "category": 'حیوان', "rarity": 'Uncommon', "price": 84, "xp": 5, "tradeable": True},
    {"name": 'سنجاب کوچک', "category": 'حیوان', "rarity": 'Rare', "price": 262, "xp": 17, "tradeable": True},
    {"name": 'جغد کوچک', "category": 'حیوان', "rarity": 'Uncommon', "price": 143, "xp": 9, "tradeable": True},
    {"name": 'شاهین کوچک', "category": 'حیوان', "rarity": 'Legendary', "price": 1621, "xp": 108, "tradeable": True},
    {"name": 'آهو کوچک', "category": 'حیوان', "rarity": 'Legendary', "price": 1029, "xp": 68, "tradeable": True},
    {"name": 'خرگوش کوچک', "category": 'حیوان', "rarity": 'Uncommon', "price": 119, "xp": 7, "tradeable": True},
    {"name": 'روباه چابک', "category": 'حیوان', "rarity": 'Common', "price": 47, "xp": 3, "tradeable": True},
    {"name": 'شیر چابک', "category": 'حیوان', "rarity": 'Legendary', "price": 1291, "xp": 86, "tradeable": True},
    {"name": 'عقاب چابک', "category": 'حیوان', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'گرگ چابک', "category": 'حیوان', "rarity": 'Uncommon', "price": 110, "xp": 7, "tradeable": True},
    {"name": 'ببر چابک', "category": 'حیوان', "rarity": 'Uncommon', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'پلنگ چابک', "category": 'حیوان', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'خرس چابک', "category": 'حیوان', "rarity": 'Uncommon', "price": 196, "xp": 13, "tradeable": True},
    {"name": 'گوزن چابک', "category": 'حیوان', "rarity": 'Rare', "price": 228, "xp": 15, "tradeable": True},
    {"name": 'مار چابک', "category": 'حیوان', "rarity": 'Common', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'کرکس چابک', "category": 'حیوان', "rarity": 'Uncommon', "price": 65, "xp": 4, "tradeable": True},
    {"name": 'سنجاب چابک', "category": 'حیوان', "rarity": 'Legendary', "price": 1188, "xp": 79, "tradeable": True},
    {"name": 'جغد چابک', "category": 'حیوان', "rarity": 'Uncommon', "price": 139, "xp": 9, "tradeable": True},
    {"name": 'شاهین چابک', "category": 'حیوان', "rarity": 'Common', "price": 33, "xp": 2, "tradeable": True},
    {"name": 'آهو چابک', "category": 'حیوان', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'خرگوش چابک', "category": 'حیوان', "rarity": 'Rare', "price": 499, "xp": 33, "tradeable": True},
    {"name": 'روباه خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 103, "xp": 6, "tradeable": True},
    {"name": 'شیر خاکستری', "category": 'حیوان', "rarity": 'Rare', "price": 404, "xp": 26, "tradeable": True},
    {"name": 'عقاب خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'گرگ خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 71, "xp": 4, "tradeable": True},
    {"name": 'ببر خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'پلنگ خاکستری', "category": 'حیوان', "rarity": 'Rare', "price": 203, "xp": 13, "tradeable": True},
    {"name": 'خرس خاکستری', "category": 'حیوان', "rarity": 'Rare', "price": 249, "xp": 16, "tradeable": True},
    {"name": 'گوزن خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 88, "xp": 5, "tradeable": True},
    {"name": 'مار خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 114, "xp": 7, "tradeable": True},
    {"name": 'کرکس خاکستری', "category": 'حیوان', "rarity": 'Uncommon', "price": 127, "xp": 8, "tradeable": True},
    {"name": 'سنجاب خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'جغد خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'شاهین خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'آهو خاکستری', "category": 'حیوان', "rarity": 'Mythic', "price": 3078, "xp": 205, "tradeable": True},
    {"name": 'خرگوش خاکستری', "category": 'حیوان', "rarity": 'Common', "price": 50, "xp": 3, "tradeable": True},
    {"name": 'روباه آتشین', "category": 'حیوان', "rarity": 'Epic', "price": 355, "xp": 23, "tradeable": True},
    {"name": 'شیر آتشین', "category": 'حیوان', "rarity": 'Common', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'عقاب آتشین', "category": 'حیوان', "rarity": 'Uncommon', "price": 189, "xp": 12, "tradeable": True},
    {"name": 'گرگ آتشین', "category": 'حیوان', "rarity": 'Epic', "price": 612, "xp": 40, "tradeable": True},
    {"name": 'ببر آتشین', "category": 'حیوان', "rarity": 'Common', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'پلنگ آتشین', "category": 'حیوان', "rarity": 'Rare', "price": 307, "xp": 20, "tradeable": True},
    {"name": 'خرس آتشین', "category": 'حیوان', "rarity": 'Rare', "price": 162, "xp": 10, "tradeable": True},
    {"name": 'گوزن آتشین', "category": 'حیوان', "rarity": 'God', "price": 3411, "xp": 227, "tradeable": True},
    {"name": 'مار آتشین', "category": 'حیوان', "rarity": 'Common', "price": 38, "xp": 2, "tradeable": True},
    {"name": 'کرکس آتشین', "category": 'حیوان', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'سنجاب آتشین', "category": 'حیوان', "rarity": 'Uncommon', "price": 167, "xp": 11, "tradeable": True},
    {"name": 'جغد آتشین', "category": 'حیوان', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'شاهین آتشین', "category": 'حیوان', "rarity": 'Uncommon', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'آهو آتشین', "category": 'حیوان', "rarity": 'Uncommon', "price": 160, "xp": 10, "tradeable": True},
    {"name": 'خرگوش آتشین', "category": 'حیوان', "rarity": 'Rare', "price": 255, "xp": 17, "tradeable": True},
    {"name": 'روباه یخی', "category": 'حیوان', "rarity": 'Common', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'شیر یخی', "category": 'حیوان', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'عقاب یخی', "category": 'حیوان', "rarity": 'Legendary', "price": 562, "xp": 37, "tradeable": True},
    {"name": 'گرگ یخی', "category": 'حیوان', "rarity": 'Rare', "price": 303, "xp": 20, "tradeable": True},
    {"name": 'ببر یخی', "category": 'حیوان', "rarity": 'Rare', "price": 200, "xp": 13, "tradeable": True},
    {"name": 'پلنگ یخی', "category": 'حیوان', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'خرس یخی', "category": 'حیوان', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'گوزن یخی', "category": 'حیوان', "rarity": 'Common', "price": 41, "xp": 2, "tradeable": True},
    {"name": 'مار یخی', "category": 'حیوان', "rarity": 'Common', "price": 78, "xp": 5, "tradeable": True},
    {"name": 'کرکس یخی', "category": 'حیوان', "rarity": 'Uncommon', "price": 165, "xp": 11, "tradeable": True},
    {"name": 'سنجاب یخی', "category": 'حیوان', "rarity": 'Common', "price": 40, "xp": 2, "tradeable": True},
    {"name": 'جغد یخی', "category": 'حیوان', "rarity": 'Epic', "price": 893, "xp": 59, "tradeable": True},
    {"name": 'شاهین یخی', "category": 'حیوان', "rarity": 'Epic', "price": 634, "xp": 42, "tradeable": True},
    {"name": 'آهو یخی', "category": 'حیوان', "rarity": 'Rare', "price": 428, "xp": 28, "tradeable": True},
    {"name": 'خرگوش یخی', "category": 'حیوان', "rarity": 'Epic', "price": 462, "xp": 30, "tradeable": True},
    {"name": 'روباه سایه\u200cای', "category": 'حیوان', "rarity": 'Legendary', "price": 933, "xp": 62, "tradeable": True},
    {"name": 'شیر سایه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 116, "xp": 7, "tradeable": True},
    {"name": 'عقاب سایه\u200cای', "category": 'حیوان', "rarity": 'Rare', "price": 286, "xp": 19, "tradeable": True},
    {"name": 'گرگ سایه\u200cای', "category": 'حیوان', "rarity": 'Common', "price": 108, "xp": 7, "tradeable": True},
    {"name": 'ببر سایه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 172, "xp": 11, "tradeable": True},
    {"name": 'پلنگ سایه\u200cای', "category": 'حیوان', "rarity": 'Epic', "price": 290, "xp": 19, "tradeable": True},
    {"name": 'خرس سایه\u200cای', "category": 'حیوان', "rarity": 'Secret', "price": 11606, "xp": 773, "tradeable": True},
    {"name": 'گوزن سایه\u200cای', "category": 'حیوان', "rarity": 'Rare', "price": 134, "xp": 8, "tradeable": True},
    {"name": 'مار سایه\u200cای', "category": 'حیوان', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'کرکس سایه\u200cای', "category": 'حیوان', "rarity": 'Rare', "price": 255, "xp": 17, "tradeable": True},
    {"name": 'سنجاب سایه\u200cای', "category": 'حیوان', "rarity": 'Epic', "price": 895, "xp": 59, "tradeable": True},
    {"name": 'جغد سایه\u200cای', "category": 'حیوان', "rarity": 'Common', "price": 46, "xp": 3, "tradeable": True},
    {"name": 'شاهین سایه\u200cای', "category": 'حیوان', "rarity": 'Legendary', "price": 674, "xp": 44, "tradeable": True},
    {"name": 'آهو سایه\u200cای', "category": 'حیوان', "rarity": 'Uncommon', "price": 119, "xp": 7, "tradeable": True},
    {"name": 'خرگوش سایه\u200cای', "category": 'حیوان', "rarity": 'Common', "price": 56, "xp": 3, "tradeable": True},
    {"name": 'رز سرخ', "category": 'گل', "rarity": 'Legendary', "price": 1210, "xp": 80, "tradeable": True},
    {"name": 'نیلوفر سرخ', "category": 'گل', "rarity": 'Common', "price": 93, "xp": 6, "tradeable": True},
    {"name": 'لاله سرخ', "category": 'گل', "rarity": 'Uncommon', "price": 165, "xp": 11, "tradeable": True},
    {"name": 'آفتابگردان سرخ', "category": 'گل', "rarity": 'Uncommon', "price": 86, "xp": 5, "tradeable": True},
    {"name": 'مینا سرخ', "category": 'گل', "rarity": 'Uncommon', "price": 178, "xp": 11, "tradeable": True},
    {"name": 'نرگس سرخ', "category": 'گل', "rarity": 'Uncommon', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'یاس سرخ', "category": 'گل', "rarity": 'Legendary', "price": 1063, "xp": 70, "tradeable": True},
    {"name": 'ارکیده سرخ', "category": 'گل', "rarity": 'Rare', "price": 368, "xp": 24, "tradeable": True},
    {"name": 'بنفشه سرخ', "category": 'گل', "rarity": 'Common', "price": 93, "xp": 6, "tradeable": True},
    {"name": 'میخک سرخ', "category": 'گل', "rarity": 'Legendary', "price": 1259, "xp": 83, "tradeable": True},
    {"name": 'رز سفید', "category": 'گل', "rarity": 'Epic', "price": 614, "xp": 40, "tradeable": True},
    {"name": 'نیلوفر سفید', "category": 'گل', "rarity": 'Common', "price": 32, "xp": 2, "tradeable": True},
    {"name": 'لاله سفید', "category": 'گل', "rarity": 'Uncommon', "price": 160, "xp": 10, "tradeable": True},
    {"name": 'آفتابگردان سفید', "category": 'گل', "rarity": 'Uncommon', "price": 172, "xp": 11, "tradeable": True},
    {"name": 'مینا سفید', "category": 'گل', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'نرگس سفید', "category": 'گل', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'یاس سفید', "category": 'گل', "rarity": 'Common', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'ارکیده سفید', "category": 'گل', "rarity": 'Common', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'بنفشه سفید', "category": 'گل', "rarity": 'Common', "price": 97, "xp": 6, "tradeable": True},
    {"name": 'میخک سفید', "category": 'گل', "rarity": 'Uncommon', "price": 120, "xp": 8, "tradeable": True},
    {"name": 'رز آبی', "category": 'گل', "rarity": 'Legendary', "price": 939, "xp": 62, "tradeable": True},
    {"name": 'نیلوفر آبی', "category": 'گل', "rarity": 'Common', "price": 98, "xp": 6, "tradeable": True},
    {"name": 'لاله آبی', "category": 'گل', "rarity": 'Epic', "price": 603, "xp": 40, "tradeable": True},
    {"name": 'آفتابگردان آبی', "category": 'گل', "rarity": 'Legendary', "price": 1450, "xp": 96, "tradeable": True},
    {"name": 'مینا آبی', "category": 'گل', "rarity": 'Common', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'نرگس آبی', "category": 'گل', "rarity": 'Common', "price": 84, "xp": 5, "tradeable": True},
    {"name": 'یاس آبی', "category": 'گل', "rarity": 'Common', "price": 43, "xp": 2, "tradeable": True},
    {"name": 'ارکیده آبی', "category": 'گل', "rarity": 'Epic', "price": 718, "xp": 47, "tradeable": True},
    {"name": 'بنفشه آبی', "category": 'گل', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'میخک آبی', "category": 'گل', "rarity": 'Uncommon', "price": 163, "xp": 10, "tradeable": True},
    {"name": 'رز بنفش', "category": 'گل', "rarity": 'Common', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'نیلوفر بنفش', "category": 'گل', "rarity": 'Uncommon', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'لاله بنفش', "category": 'گل', "rarity": 'Common', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'آفتابگردان بنفش', "category": 'گل', "rarity": 'Common', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'مینا بنفش', "category": 'گل', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'نرگس بنفش', "category": 'گل', "rarity": 'Common', "price": 29, "xp": 1, "tradeable": True},
    {"name": 'یاس بنفش', "category": 'گل', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'ارکیده بنفش', "category": 'گل', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'بنفشه بنفش', "category": 'گل', "rarity": 'Legendary', "price": 925, "xp": 61, "tradeable": True},
    {"name": 'میخک بنفش', "category": 'گل', "rarity": 'God', "price": 4475, "xp": 298, "tradeable": True},
    {"name": 'رز طلایی', "category": 'گل', "rarity": 'Uncommon', "price": 202, "xp": 13, "tradeable": True},
    {"name": 'نیلوفر طلایی', "category": 'گل', "rarity": 'Rare', "price": 282, "xp": 18, "tradeable": True},
    {"name": 'لاله طلایی', "category": 'گل', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'آفتابگردان طلایی', "category": 'گل', "rarity": 'Mythic', "price": 2480, "xp": 165, "tradeable": True},
    {"name": 'مینا طلایی', "category": 'گل', "rarity": 'Rare', "price": 144, "xp": 9, "tradeable": True},
    {"name": 'نرگس طلایی', "category": 'گل', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'یاس طلایی', "category": 'گل', "rarity": 'Uncommon', "price": 108, "xp": 7, "tradeable": True},
    {"name": 'ارکیده طلایی', "category": 'گل', "rarity": 'Rare', "price": 272, "xp": 18, "tradeable": True},
    {"name": 'بنفشه طلایی', "category": 'گل', "rarity": 'Rare', "price": 147, "xp": 9, "tradeable": True},
    {"name": 'میخک طلایی', "category": 'گل', "rarity": 'Rare', "price": 174, "xp": 11, "tradeable": True},
    {"name": 'رز پژمرده', "category": 'گل', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'نیلوفر پژمرده', "category": 'گل', "rarity": 'Common', "price": 111, "xp": 7, "tradeable": True},
    {"name": 'لاله پژمرده', "category": 'گل', "rarity": 'Legendary', "price": 639, "xp": 42, "tradeable": True},
    {"name": 'آفتابگردان پژمرده', "category": 'گل', "rarity": 'Uncommon', "price": 184, "xp": 12, "tradeable": True},
    {"name": 'مینا پژمرده', "category": 'گل', "rarity": 'Uncommon', "price": 149, "xp": 9, "tradeable": True},
    {"name": 'نرگس پژمرده', "category": 'گل', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'یاس پژمرده', "category": 'گل', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'ارکیده پژمرده', "category": 'گل', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'بنفشه پژمرده', "category": 'گل', "rarity": 'Common', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'میخک پژمرده', "category": 'گل', "rarity": 'Rare', "price": 352, "xp": 23, "tradeable": True},
    {"name": 'رز شکوفا', "category": 'گل', "rarity": 'Common', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'نیلوفر شکوفا', "category": 'گل', "rarity": 'Uncommon', "price": 195, "xp": 13, "tradeable": True},
    {"name": 'لاله شکوفا', "category": 'گل', "rarity": 'Mythic', "price": 2835, "xp": 189, "tradeable": True},
    {"name": 'آفتابگردان شکوفا', "category": 'گل', "rarity": 'Epic', "price": 440, "xp": 29, "tradeable": True},
    {"name": 'مینا شکوفا', "category": 'گل', "rarity": 'Legendary', "price": 583, "xp": 38, "tradeable": True},
    {"name": 'نرگس شکوفا', "category": 'گل', "rarity": 'Uncommon', "price": 145, "xp": 9, "tradeable": True},
    {"name": 'یاس شکوفا', "category": 'گل', "rarity": 'Common', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'ارکیده شکوفا', "category": 'گل', "rarity": 'Uncommon', "price": 111, "xp": 7, "tradeable": True},
    {"name": 'بنفشه شکوفا', "category": 'گل', "rarity": 'Uncommon', "price": 110, "xp": 7, "tradeable": True},
    {"name": 'میخک شکوفا', "category": 'گل', "rarity": 'Rare', "price": 354, "xp": 23, "tradeable": True},
    {"name": 'رز کمیاب', "category": 'گل', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'نیلوفر کمیاب', "category": 'گل', "rarity": 'Rare', "price": 191, "xp": 12, "tradeable": True},
    {"name": 'لاله کمیاب', "category": 'گل', "rarity": 'Common', "price": 128, "xp": 8, "tradeable": True},
    {"name": 'آفتابگردان کمیاب', "category": 'گل', "rarity": 'Common', "price": 93, "xp": 6, "tradeable": True},
    {"name": 'مینا کمیاب', "category": 'گل', "rarity": 'Common', "price": 63, "xp": 4, "tradeable": True},
    {"name": 'نرگس کمیاب', "category": 'گل', "rarity": 'Uncommon', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'یاس کمیاب', "category": 'گل', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'ارکیده کمیاب', "category": 'گل', "rarity": 'Common', "price": 65, "xp": 4, "tradeable": True},
    {"name": 'بنفشه کمیاب', "category": 'گل', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'میخک کمیاب', "category": 'گل', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'رز معطر', "category": 'گل', "rarity": 'Common', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'نیلوفر معطر', "category": 'گل', "rarity": 'Common', "price": 113, "xp": 7, "tradeable": True},
    {"name": 'لاله معطر', "category": 'گل', "rarity": 'Uncommon', "price": 231, "xp": 15, "tradeable": True},
    {"name": 'آفتابگردان معطر', "category": 'گل', "rarity": 'Common', "price": 97, "xp": 6, "tradeable": True},
    {"name": 'مینا معطر', "category": 'گل', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'نرگس معطر', "category": 'گل', "rarity": 'Rare', "price": 287, "xp": 19, "tradeable": True},
    {"name": 'یاس معطر', "category": 'گل', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'ارکیده معطر', "category": 'گل', "rarity": 'Uncommon', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'بنفشه معطر', "category": 'گل', "rarity": 'Epic', "price": 786, "xp": 52, "tradeable": True},
    {"name": 'میخک معطر', "category": 'گل', "rarity": 'Uncommon', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'رز یخ\u200cزده', "category": 'گل', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'نیلوفر یخ\u200cزده', "category": 'گل', "rarity": 'Legendary', "price": 418, "xp": 27, "tradeable": True},
    {"name": 'لاله یخ\u200cزده', "category": 'گل', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'آفتابگردان یخ\u200cزده', "category": 'گل', "rarity": 'Common', "price": 28, "xp": 1, "tradeable": True},
    {"name": 'مینا یخ\u200cزده', "category": 'گل', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'نرگس یخ\u200cزده', "category": 'گل', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'یاس یخ\u200cزده', "category": 'گل', "rarity": 'Rare', "price": 316, "xp": 21, "tradeable": True},
    {"name": 'ارکیده یخ\u200cزده', "category": 'گل', "rarity": 'Uncommon', "price": 131, "xp": 8, "tradeable": True},
    {"name": 'بنفشه یخ\u200cزده', "category": 'گل', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'میخک یخ\u200cزده', "category": 'گل', "rarity": 'Uncommon', "price": 155, "xp": 10, "tradeable": True},
    {"name": 'طلا خالص', "category": 'فلز', "rarity": 'Uncommon', "price": 212, "xp": 14, "tradeable": True},
    {"name": 'نقره خالص', "category": 'فلز', "rarity": 'Epic', "price": 538, "xp": 35, "tradeable": True},
    {"name": 'آهن خالص', "category": 'فلز', "rarity": 'Rare', "price": 170, "xp": 11, "tradeable": True},
    {"name": 'مس خالص', "category": 'فلز', "rarity": 'Uncommon', "price": 127, "xp": 8, "tradeable": True},
    {"name": 'پلاتین خالص', "category": 'فلز', "rarity": 'Uncommon', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'تیتانیوم خالص', "category": 'فلز', "rarity": 'Legendary', "price": 896, "xp": 59, "tradeable": True},
    {"name": 'برنز خالص', "category": 'فلز', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'قلع خالص', "category": 'فلز', "rarity": 'Uncommon', "price": 129, "xp": 8, "tradeable": True},
    {"name": 'روی خالص', "category": 'فلز', "rarity": 'Uncommon', "price": 194, "xp": 12, "tradeable": True},
    {"name": 'فولاد خالص', "category": 'فلز', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'طلا براق', "category": 'فلز', "rarity": 'Uncommon', "price": 79, "xp": 5, "tradeable": True},
    {"name": 'نقره براق', "category": 'فلز', "rarity": 'Rare', "price": 320, "xp": 21, "tradeable": True},
    {"name": 'آهن براق', "category": 'فلز', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'مس براق', "category": 'فلز', "rarity": 'Uncommon', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'پلاتین براق', "category": 'فلز', "rarity": 'Rare', "price": 513, "xp": 34, "tradeable": True},
    {"name": 'تیتانیوم براق', "category": 'فلز', "rarity": 'Legendary', "price": 737, "xp": 49, "tradeable": True},
    {"name": 'برنز براق', "category": 'فلز', "rarity": 'Uncommon', "price": 132, "xp": 8, "tradeable": True},
    {"name": 'قلع براق', "category": 'فلز', "rarity": 'Rare', "price": 327, "xp": 21, "tradeable": True},
    {"name": 'روی براق', "category": 'فلز', "rarity": 'Epic', "price": 859, "xp": 57, "tradeable": True},
    {"name": 'فولاد براق', "category": 'فلز', "rarity": 'Legendary', "price": 982, "xp": 65, "tradeable": True},
    {"name": 'طلا زنگ\u200cزده', "category": 'فلز', "rarity": 'Mythic', "price": 2573, "xp": 171, "tradeable": True},
    {"name": 'نقره زنگ\u200cزده', "category": 'فلز', "rarity": 'Epic', "price": 320, "xp": 21, "tradeable": True},
    {"name": 'آهن زنگ\u200cزده', "category": 'فلز', "rarity": 'Uncommon', "price": 167, "xp": 11, "tradeable": True},
    {"name": 'مس زنگ\u200cزده', "category": 'فلز', "rarity": 'Mythic', "price": 1060, "xp": 70, "tradeable": True},
    {"name": 'پلاتین زنگ\u200cزده', "category": 'فلز', "rarity": 'Mythic', "price": 3035, "xp": 202, "tradeable": True},
    {"name": 'تیتانیوم زنگ\u200cزده', "category": 'فلز', "rarity": 'Uncommon', "price": 193, "xp": 12, "tradeable": True},
    {"name": 'برنز زنگ\u200cزده', "category": 'فلز', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'قلع زنگ\u200cزده', "category": 'فلز', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'روی زنگ\u200cزده', "category": 'فلز', "rarity": 'Rare', "price": 117, "xp": 7, "tradeable": True},
    {"name": 'فولاد زنگ\u200cزده', "category": 'فلز', "rarity": 'Common', "price": 34, "xp": 2, "tradeable": True},
    {"name": 'طلا کمیاب', "category": 'فلز', "rarity": 'Rare', "price": 132, "xp": 8, "tradeable": True},
    {"name": 'نقره کمیاب', "category": 'فلز', "rarity": 'Common', "price": 78, "xp": 5, "tradeable": True},
    {"name": 'آهن کمیاب', "category": 'فلز', "rarity": 'Common', "price": 32, "xp": 2, "tradeable": True},
    {"name": 'مس کمیاب', "category": 'فلز', "rarity": 'God', "price": 4275, "xp": 285, "tradeable": True},
    {"name": 'پلاتین کمیاب', "category": 'فلز', "rarity": 'Rare', "price": 304, "xp": 20, "tradeable": True},
    {"name": 'تیتانیوم کمیاب', "category": 'فلز', "rarity": 'Rare', "price": 260, "xp": 17, "tradeable": True},
    {"name": 'برنز کمیاب', "category": 'فلز', "rarity": 'Epic', "price": 699, "xp": 46, "tradeable": True},
    {"name": 'قلع کمیاب', "category": 'فلز', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'روی کمیاب', "category": 'فلز', "rarity": 'Uncommon', "price": 115, "xp": 7, "tradeable": True},
    {"name": 'فولاد کمیاب', "category": 'فلز', "rarity": 'Uncommon', "price": 153, "xp": 10, "tradeable": True},
    {"name": 'طلا سنگین', "category": 'فلز', "rarity": 'Common', "price": 93, "xp": 6, "tradeable": True},
    {"name": 'نقره سنگین', "category": 'فلز', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'آهن سنگین', "category": 'فلز', "rarity": 'Common', "price": 62, "xp": 4, "tradeable": True},
    {"name": 'مس سنگین', "category": 'فلز', "rarity": 'Uncommon', "price": 110, "xp": 7, "tradeable": True},
    {"name": 'پلاتین سنگین', "category": 'فلز', "rarity": 'Rare', "price": 447, "xp": 29, "tradeable": True},
    {"name": 'تیتانیوم سنگین', "category": 'فلز', "rarity": 'Uncommon', "price": 179, "xp": 11, "tradeable": True},
    {"name": 'برنز سنگین', "category": 'فلز', "rarity": 'Common', "price": 56, "xp": 3, "tradeable": True},
    {"name": 'قلع سنگین', "category": 'فلز', "rarity": 'Common', "price": 50, "xp": 3, "tradeable": True},
    {"name": 'روی سنگین', "category": 'فلز', "rarity": 'Epic', "price": 350, "xp": 23, "tradeable": True},
    {"name": 'فولاد سنگین', "category": 'فلز', "rarity": 'Epic', "price": 269, "xp": 17, "tradeable": True},
    {"name": 'طلا درخشان', "category": 'فلز', "rarity": 'Rare', "price": 173, "xp": 11, "tradeable": True},
    {"name": 'نقره درخشان', "category": 'فلز', "rarity": 'Common', "price": 59, "xp": 3, "tradeable": True},
    {"name": 'آهن درخشان', "category": 'فلز', "rarity": 'Epic', "price": 548, "xp": 36, "tradeable": True},
    {"name": 'مس درخشان', "category": 'فلز', "rarity": 'Legendary', "price": 1084, "xp": 72, "tradeable": True},
    {"name": 'پلاتین درخشان', "category": 'فلز', "rarity": 'Mythic', "price": 1584, "xp": 105, "tradeable": True},
    {"name": 'تیتانیوم درخشان', "category": 'فلز', "rarity": 'Rare', "price": 280, "xp": 18, "tradeable": True},
    {"name": 'برنز درخشان', "category": 'فلز', "rarity": 'Rare', "price": 402, "xp": 26, "tradeable": True},
    {"name": 'قلع درخشان', "category": 'فلز', "rarity": 'Uncommon', "price": 130, "xp": 8, "tradeable": True},
    {"name": 'روی درخشان', "category": 'فلز', "rarity": 'Uncommon', "price": 143, "xp": 9, "tradeable": True},
    {"name": 'فولاد درخشان', "category": 'فلز', "rarity": 'Common', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'طلا کهن', "category": 'فلز', "rarity": 'Epic', "price": 850, "xp": 56, "tradeable": True},
    {"name": 'نقره کهن', "category": 'فلز', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'آهن کهن', "category": 'فلز', "rarity": 'Uncommon', "price": 135, "xp": 9, "tradeable": True},
    {"name": 'مس کهن', "category": 'فلز', "rarity": 'Common', "price": 126, "xp": 8, "tradeable": True},
    {"name": 'پلاتین کهن', "category": 'فلز', "rarity": 'Uncommon', "price": 157, "xp": 10, "tradeable": True},
    {"name": 'تیتانیوم کهن', "category": 'فلز', "rarity": 'Common', "price": 58, "xp": 3, "tradeable": True},
    {"name": 'برنز کهن', "category": 'فلز', "rarity": 'Epic', "price": 800, "xp": 53, "tradeable": True},
    {"name": 'قلع کهن', "category": 'فلز', "rarity": 'Legendary', "price": 1016, "xp": 67, "tradeable": True},
    {"name": 'روی کهن', "category": 'فلز', "rarity": 'Epic', "price": 758, "xp": 50, "tradeable": True},
    {"name": 'فولاد کهن', "category": 'فلز', "rarity": 'Rare', "price": 265, "xp": 17, "tradeable": True},
    {"name": 'طلا تیره', "category": 'فلز', "rarity": 'Rare', "price": 328, "xp": 21, "tradeable": True},
    {"name": 'نقره تیره', "category": 'فلز', "rarity": 'Epic', "price": 314, "xp": 20, "tradeable": True},
    {"name": 'آهن تیره', "category": 'فلز', "rarity": 'Epic', "price": 397, "xp": 26, "tradeable": True},
    {"name": 'مس تیره', "category": 'فلز', "rarity": 'Rare', "price": 120, "xp": 8, "tradeable": True},
    {"name": 'پلاتین تیره', "category": 'فلز', "rarity": 'Epic', "price": 393, "xp": 26, "tradeable": True},
    {"name": 'تیتانیوم تیره', "category": 'فلز', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'برنز تیره', "category": 'فلز', "rarity": 'Common', "price": 102, "xp": 6, "tradeable": True},
    {"name": 'قلع تیره', "category": 'فلز', "rarity": 'Common', "price": 105, "xp": 7, "tradeable": True},
    {"name": 'روی تیره', "category": 'فلز', "rarity": 'Common', "price": 112, "xp": 7, "tradeable": True},
    {"name": 'فولاد تیره', "category": 'فلز', "rarity": 'Uncommon', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'طلا خام', "category": 'فلز', "rarity": 'Epic', "price": 818, "xp": 54, "tradeable": True},
    {"name": 'نقره خام', "category": 'فلز', "rarity": 'Legendary', "price": 812, "xp": 54, "tradeable": True},
    {"name": 'آهن خام', "category": 'فلز', "rarity": 'Uncommon', "price": 107, "xp": 7, "tradeable": True},
    {"name": 'مس خام', "category": 'فلز', "rarity": 'Uncommon', "price": 140, "xp": 9, "tradeable": True},
    {"name": 'پلاتین خام', "category": 'فلز', "rarity": 'Common', "price": 98, "xp": 6, "tradeable": True},
    {"name": 'تیتانیوم خام', "category": 'فلز', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'برنز خام', "category": 'فلز', "rarity": 'Common', "price": 45, "xp": 3, "tradeable": True},
    {"name": 'قلع خام', "category": 'فلز', "rarity": 'Epic', "price": 230, "xp": 15, "tradeable": True},
    {"name": 'روی خام', "category": 'فلز', "rarity": 'Common', "price": 103, "xp": 6, "tradeable": True},
    {"name": 'فولاد خام', "category": 'فلز', "rarity": 'Uncommon', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'طلا پرداخت\u200cشده', "category": 'فلز', "rarity": 'Epic', "price": 623, "xp": 41, "tradeable": True},
    {"name": 'نقره پرداخت\u200cشده', "category": 'فلز', "rarity": 'Epic', "price": 419, "xp": 27, "tradeable": True},
    {"name": 'آهن پرداخت\u200cشده', "category": 'فلز', "rarity": 'Legendary', "price": 801, "xp": 53, "tradeable": True},
    {"name": 'مس پرداخت\u200cشده', "category": 'فلز', "rarity": 'Epic', "price": 673, "xp": 44, "tradeable": True},
    {"name": 'پلاتین پرداخت\u200cشده', "category": 'فلز', "rarity": 'Legendary', "price": 1218, "xp": 81, "tradeable": True},
    {"name": 'تیتانیوم پرداخت\u200cشده', "category": 'فلز', "rarity": 'Uncommon', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'برنز پرداخت\u200cشده', "category": 'فلز', "rarity": 'Epic', "price": 327, "xp": 21, "tradeable": True},
    {"name": 'قلع پرداخت\u200cشده', "category": 'فلز', "rarity": 'Uncommon', "price": 202, "xp": 13, "tradeable": True},
    {"name": 'روی پرداخت\u200cشده', "category": 'فلز', "rarity": 'Common', "price": 65, "xp": 4, "tradeable": True},
    {"name": 'فولاد پرداخت\u200cشده', "category": 'فلز', "rarity": 'Epic', "price": 764, "xp": 50, "tradeable": True},
    {"name": 'سیب تازه', "category": 'غذا', "rarity": 'Rare', "price": 224, "xp": 14, "tradeable": True},
    {"name": 'نان تازه', "category": 'غذا', "rarity": 'Rare', "price": 168, "xp": 11, "tradeable": True},
    {"name": 'کیک تازه', "category": 'غذا', "rarity": 'Mythic', "price": 916, "xp": 61, "tradeable": True},
    {"name": 'عسل تازه', "category": 'غذا', "rarity": 'Common', "price": 37, "xp": 2, "tradeable": True},
    {"name": 'پنیر تازه', "category": 'غذا', "rarity": 'Rare', "price": 411, "xp": 27, "tradeable": True},
    {"name": 'انار تازه', "category": 'غذا', "rarity": 'Common', "price": 76, "xp": 5, "tradeable": True},
    {"name": 'خرما تازه', "category": 'غذا', "rarity": 'Uncommon', "price": 115, "xp": 7, "tradeable": True},
    {"name": 'زعفران تازه', "category": 'غذا', "rarity": 'Legendary', "price": 1138, "xp": 75, "tradeable": True},
    {"name": 'شکلات تازه', "category": 'غذا', "rarity": 'Uncommon', "price": 131, "xp": 8, "tradeable": True},
    {"name": 'کباب تازه', "category": 'غذا', "rarity": 'Uncommon', "price": 141, "xp": 9, "tradeable": True},
    {"name": 'سیب طلایی', "category": 'غذا', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'نان طلایی', "category": 'غذا', "rarity": 'Rare', "price": 229, "xp": 15, "tradeable": True},
    {"name": 'کیک طلایی', "category": 'غذا', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'عسل طلایی', "category": 'غذا', "rarity": 'Legendary', "price": 1264, "xp": 84, "tradeable": True},
    {"name": 'پنیر طلایی', "category": 'غذا', "rarity": 'Common', "price": 38, "xp": 2, "tradeable": True},
    {"name": 'انار طلایی', "category": 'غذا', "rarity": 'Uncommon', "price": 190, "xp": 12, "tradeable": True},
    {"name": 'خرما طلایی', "category": 'غذا', "rarity": 'Common', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'زعفران طلایی', "category": 'غذا', "rarity": 'Uncommon', "price": 174, "xp": 11, "tradeable": True},
    {"name": 'شکلات طلایی', "category": 'غذا', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'کباب طلایی', "category": 'غذا', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'سیب خانگی', "category": 'غذا', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'نان خانگی', "category": 'غذا', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'کیک خانگی', "category": 'غذا', "rarity": 'Common', "price": 79, "xp": 5, "tradeable": True},
    {"name": 'عسل خانگی', "category": 'غذا', "rarity": 'Common', "price": 38, "xp": 2, "tradeable": True},
    {"name": 'پنیر خانگی', "category": 'غذا', "rarity": 'Legendary', "price": 1530, "xp": 102, "tradeable": True},
    {"name": 'انار خانگی', "category": 'غذا', "rarity": 'Common', "price": 37, "xp": 2, "tradeable": True},
    {"name": 'خرما خانگی', "category": 'غذا', "rarity": 'Common', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'زعفران خانگی', "category": 'غذا', "rarity": 'Uncommon', "price": 146, "xp": 9, "tradeable": True},
    {"name": 'شکلات خانگی', "category": 'غذا', "rarity": 'Uncommon', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'کباب خانگی', "category": 'غذا', "rarity": 'Uncommon', "price": 114, "xp": 7, "tradeable": True},
    {"name": 'سیب ترد', "category": 'غذا', "rarity": 'Common', "price": 102, "xp": 6, "tradeable": True},
    {"name": 'نان ترد', "category": 'غذا', "rarity": 'Common', "price": 118, "xp": 7, "tradeable": True},
    {"name": 'کیک ترد', "category": 'غذا', "rarity": 'Rare', "price": 280, "xp": 18, "tradeable": True},
    {"name": 'عسل ترد', "category": 'غذا', "rarity": 'Epic', "price": 628, "xp": 41, "tradeable": True},
    {"name": 'پنیر ترد', "category": 'غذا', "rarity": 'Legendary', "price": 904, "xp": 60, "tradeable": True},
    {"name": 'انار ترد', "category": 'غذا', "rarity": 'Uncommon', "price": 162, "xp": 10, "tradeable": True},
    {"name": 'خرما ترد', "category": 'غذا', "rarity": 'Rare', "price": 228, "xp": 15, "tradeable": True},
    {"name": 'زعفران ترد', "category": 'غذا', "rarity": 'Common', "price": 45, "xp": 3, "tradeable": True},
    {"name": 'شکلات ترد', "category": 'غذا', "rarity": 'Rare', "price": 372, "xp": 24, "tradeable": True},
    {"name": 'کباب ترد', "category": 'غذا', "rarity": 'Common', "price": 62, "xp": 4, "tradeable": True},
    {"name": 'سیب شیرین', "category": 'غذا', "rarity": 'Rare', "price": 401, "xp": 26, "tradeable": True},
    {"name": 'نان شیرین', "category": 'غذا', "rarity": 'Common', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'کیک شیرین', "category": 'غذا', "rarity": 'Uncommon', "price": 177, "xp": 11, "tradeable": True},
    {"name": 'عسل شیرین', "category": 'غذا', "rarity": 'Rare', "price": 349, "xp": 23, "tradeable": True},
    {"name": 'پنیر شیرین', "category": 'غذا', "rarity": 'Epic', "price": 273, "xp": 18, "tradeable": True},
    {"name": 'انار شیرین', "category": 'غذا', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'خرما شیرین', "category": 'غذا', "rarity": 'Uncommon', "price": 207, "xp": 13, "tradeable": True},
    {"name": 'زعفران شیرین', "category": 'غذا', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'شکلات شیرین', "category": 'غذا', "rarity": 'Common', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'کباب شیرین', "category": 'غذا', "rarity": 'Uncommon', "price": 118, "xp": 7, "tradeable": True},
    {"name": 'سیب تند', "category": 'غذا', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'نان تند', "category": 'غذا', "rarity": 'Secret', "price": 9306, "xp": 620, "tradeable": True},
    {"name": 'کیک تند', "category": 'غذا', "rarity": 'Uncommon', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'عسل تند', "category": 'غذا', "rarity": 'Legendary', "price": 701, "xp": 46, "tradeable": True},
    {"name": 'پنیر تند', "category": 'غذا', "rarity": 'Common', "price": 59, "xp": 3, "tradeable": True},
    {"name": 'انار تند', "category": 'غذا', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'خرما تند', "category": 'غذا', "rarity": 'Rare', "price": 233, "xp": 15, "tradeable": True},
    {"name": 'زعفران تند', "category": 'غذا', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'شکلات تند', "category": 'غذا', "rarity": 'Common', "price": 48, "xp": 3, "tradeable": True},
    {"name": 'کباب تند', "category": 'غذا', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'سیب خوشمزه', "category": 'غذا', "rarity": 'Uncommon', "price": 155, "xp": 10, "tradeable": True},
    {"name": 'نان خوشمزه', "category": 'غذا', "rarity": 'Legendary', "price": 1061, "xp": 70, "tradeable": True},
    {"name": 'کیک خوشمزه', "category": 'غذا', "rarity": 'Uncommon', "price": 118, "xp": 7, "tradeable": True},
    {"name": 'عسل خوشمزه', "category": 'غذا', "rarity": 'Uncommon', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'پنیر خوشمزه', "category": 'غذا', "rarity": 'Epic', "price": 762, "xp": 50, "tradeable": True},
    {"name": 'انار خوشمزه', "category": 'غذا', "rarity": 'Common', "price": 51, "xp": 3, "tradeable": True},
    {"name": 'خرما خوشمزه', "category": 'غذا', "rarity": 'Rare', "price": 165, "xp": 11, "tradeable": True},
    {"name": 'زعفران خوشمزه', "category": 'غذا', "rarity": 'Rare', "price": 350, "xp": 23, "tradeable": True},
    {"name": 'شکلات خوشمزه', "category": 'غذا', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'کباب خوشمزه', "category": 'غذا', "rarity": 'Rare', "price": 391, "xp": 26, "tradeable": True},
    {"name": 'سیب کمیاب', "category": 'غذا', "rarity": 'Uncommon', "price": 174, "xp": 11, "tradeable": True},
    {"name": 'نان کمیاب', "category": 'غذا', "rarity": 'Uncommon', "price": 144, "xp": 9, "tradeable": True},
    {"name": 'کیک کمیاب', "category": 'غذا', "rarity": 'Uncommon', "price": 125, "xp": 8, "tradeable": True},
    {"name": 'عسل کمیاب', "category": 'غذا', "rarity": 'Rare', "price": 342, "xp": 22, "tradeable": True},
    {"name": 'پنیر کمیاب', "category": 'غذا', "rarity": 'Uncommon', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'انار کمیاب', "category": 'غذا', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'خرما کمیاب', "category": 'غذا', "rarity": 'Rare', "price": 377, "xp": 25, "tradeable": True},
    {"name": 'زعفران کمیاب', "category": 'غذا', "rarity": 'Uncommon', "price": 159, "xp": 10, "tradeable": True},
    {"name": 'شکلات کمیاب', "category": 'غذا', "rarity": 'Uncommon', "price": 224, "xp": 14, "tradeable": True},
    {"name": 'کباب کمیاب', "category": 'غذا', "rarity": 'Uncommon', "price": 112, "xp": 7, "tradeable": True},
    {"name": 'سیب سنتی', "category": 'غذا', "rarity": 'Uncommon', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'نان سنتی', "category": 'غذا', "rarity": 'Epic', "price": 355, "xp": 23, "tradeable": True},
    {"name": 'کیک سنتی', "category": 'غذا', "rarity": 'Legendary', "price": 1198, "xp": 79, "tradeable": True},
    {"name": 'عسل سنتی', "category": 'غذا', "rarity": 'Epic', "price": 847, "xp": 56, "tradeable": True},
    {"name": 'پنیر سنتی', "category": 'غذا', "rarity": 'Uncommon', "price": 185, "xp": 12, "tradeable": True},
    {"name": 'انار سنتی', "category": 'غذا', "rarity": 'Epic', "price": 650, "xp": 43, "tradeable": True},
    {"name": 'خرما سنتی', "category": 'غذا', "rarity": 'Uncommon', "price": 109, "xp": 7, "tradeable": True},
    {"name": 'زعفران سنتی', "category": 'غذا', "rarity": 'Common', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'شکلات سنتی', "category": 'غذا', "rarity": 'Rare', "price": 399, "xp": 26, "tradeable": True},
    {"name": 'کباب سنتی', "category": 'غذا', "rarity": 'Common', "price": 41, "xp": 2, "tradeable": True},
    {"name": 'سیب ویژه', "category": 'غذا', "rarity": 'Uncommon', "price": 162, "xp": 10, "tradeable": True},
    {"name": 'نان ویژه', "category": 'غذا', "rarity": 'Rare', "price": 369, "xp": 24, "tradeable": True},
    {"name": 'کیک ویژه', "category": 'غذا', "rarity": 'Mythic', "price": 1506, "xp": 100, "tradeable": True},
    {"name": 'عسل ویژه', "category": 'غذا', "rarity": 'Rare', "price": 231, "xp": 15, "tradeable": True},
    {"name": 'پنیر ویژه', "category": 'غذا', "rarity": 'Secret', "price": 9723, "xp": 648, "tradeable": True},
    {"name": 'انار ویژه', "category": 'غذا', "rarity": 'Epic', "price": 334, "xp": 22, "tradeable": True},
    {"name": 'خرما ویژه', "category": 'غذا', "rarity": 'God', "price": 3257, "xp": 217, "tradeable": True},
    {"name": 'زعفران ویژه', "category": 'غذا', "rarity": 'Rare', "price": 364, "xp": 24, "tradeable": True},
    {"name": 'شکلات ویژه', "category": 'غذا', "rarity": 'Uncommon', "price": 148, "xp": 9, "tradeable": True},
    {"name": 'کباب ویژه', "category": 'غذا', "rarity": 'Common', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'چکش آهنین', "category": 'وسیله', "rarity": 'Epic', "price": 631, "xp": 42, "tradeable": True},
    {"name": 'طناب آهنین', "category": 'وسیله', "rarity": 'Uncommon', "price": 98, "xp": 6, "tradeable": True},
    {"name": 'چراغ آهنین', "category": 'وسیله', "rarity": 'Rare', "price": 291, "xp": 19, "tradeable": True},
    {"name": 'قطب\u200cنما آهنین', "category": 'وسیله', "rarity": 'Legendary', "price": 1842, "xp": 122, "tradeable": True},
    {"name": 'کوله\u200cپشتی آهنین', "category": 'وسیله', "rarity": 'Rare', "price": 145, "xp": 9, "tradeable": True},
    {"name": 'کلید آهنین', "category": 'وسیله', "rarity": 'Rare', "price": 413, "xp": 27, "tradeable": True},
    {"name": 'ساعت آهنین', "category": 'وسیله', "rarity": 'God', "price": 3175, "xp": 211, "tradeable": True},
    {"name": 'دوربین آهنین', "category": 'وسیله', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'چتر آهنین', "category": 'وسیله', "rarity": 'Epic', "price": 668, "xp": 44, "tradeable": True},
    {"name": 'عصا آهنین', "category": 'وسیله', "rarity": 'Uncommon', "price": 142, "xp": 9, "tradeable": True},
    {"name": 'چکش چوبی', "category": 'وسیله', "rarity": 'Common', "price": 43, "xp": 2, "tradeable": True},
    {"name": 'طناب چوبی', "category": 'وسیله', "rarity": 'Epic', "price": 328, "xp": 21, "tradeable": True},
    {"name": 'چراغ چوبی', "category": 'وسیله', "rarity": 'Rare', "price": 316, "xp": 21, "tradeable": True},
    {"name": 'قطب\u200cنما چوبی', "category": 'وسیله', "rarity": 'Uncommon', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'کوله\u200cپشتی چوبی', "category": 'وسیله', "rarity": 'Epic', "price": 382, "xp": 25, "tradeable": True},
    {"name": 'کلید چوبی', "category": 'وسیله', "rarity": 'Uncommon', "price": 183, "xp": 12, "tradeable": True},
    {"name": 'ساعت چوبی', "category": 'وسیله', "rarity": 'Rare', "price": 290, "xp": 19, "tradeable": True},
    {"name": 'دوربین چوبی', "category": 'وسیله', "rarity": 'Uncommon', "price": 119, "xp": 7, "tradeable": True},
    {"name": 'چتر چوبی', "category": 'وسیله', "rarity": 'Common', "price": 63, "xp": 4, "tradeable": True},
    {"name": 'عصا چوبی', "category": 'وسیله', "rarity": 'Uncommon', "price": 219, "xp": 14, "tradeable": True},
    {"name": 'چکش جادویی', "category": 'وسیله', "rarity": 'Epic', "price": 237, "xp": 15, "tradeable": True},
    {"name": 'طناب جادویی', "category": 'وسیله', "rarity": 'Uncommon', "price": 142, "xp": 9, "tradeable": True},
    {"name": 'چراغ جادویی', "category": 'وسیله', "rarity": 'Legendary', "price": 765, "xp": 51, "tradeable": True},
    {"name": 'قطب\u200cنما جادویی', "category": 'وسیله', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'کوله\u200cپشتی جادویی', "category": 'وسیله', "rarity": 'Rare', "price": 228, "xp": 15, "tradeable": True},
    {"name": 'کلید جادویی', "category": 'وسیله', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'ساعت جادویی', "category": 'وسیله', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'دوربین جادویی', "category": 'وسیله', "rarity": 'Legendary', "price": 631, "xp": 42, "tradeable": True},
    {"name": 'چتر جادویی', "category": 'وسیله', "rarity": 'Uncommon', "price": 119, "xp": 7, "tradeable": True},
    {"name": 'عصا جادویی', "category": 'وسیله', "rarity": 'Common', "price": 33, "xp": 2, "tradeable": True},
    {"name": 'چکش قدیمی', "category": 'وسیله', "rarity": 'Uncommon', "price": 100, "xp": 6, "tradeable": True},
    {"name": 'طناب قدیمی', "category": 'وسیله', "rarity": 'Uncommon', "price": 138, "xp": 9, "tradeable": True},
    {"name": 'چراغ قدیمی', "category": 'وسیله', "rarity": 'Rare', "price": 232, "xp": 15, "tradeable": True},
    {"name": 'قطب\u200cنما قدیمی', "category": 'وسیله', "rarity": 'Uncommon', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'کوله\u200cپشتی قدیمی', "category": 'وسیله', "rarity": 'Common', "price": 47, "xp": 3, "tradeable": True},
    {"name": 'کلید قدیمی', "category": 'وسیله', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'ساعت قدیمی', "category": 'وسیله', "rarity": 'Common', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'دوربین قدیمی', "category": 'وسیله', "rarity": 'Rare', "price": 210, "xp": 14, "tradeable": True},
    {"name": 'چتر قدیمی', "category": 'وسیله', "rarity": 'Rare', "price": 161, "xp": 10, "tradeable": True},
    {"name": 'عصا قدیمی', "category": 'وسیله', "rarity": 'Common', "price": 52, "xp": 3, "tradeable": True},
    {"name": 'چکش دست\u200cساز', "category": 'وسیله', "rarity": 'Rare', "price": 246, "xp": 16, "tradeable": True},
    {"name": 'طناب دست\u200cساز', "category": 'وسیله', "rarity": 'Uncommon', "price": 211, "xp": 14, "tradeable": True},
    {"name": 'چراغ دست\u200cساز', "category": 'وسیله', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'قطب\u200cنما دست\u200cساز', "category": 'وسیله', "rarity": 'Common', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'کوله\u200cپشتی دست\u200cساز', "category": 'وسیله', "rarity": 'Uncommon', "price": 86, "xp": 5, "tradeable": True},
    {"name": 'کلید دست\u200cساز', "category": 'وسیله', "rarity": 'Uncommon', "price": 131, "xp": 8, "tradeable": True},
    {"name": 'ساعت دست\u200cساز', "category": 'وسیله', "rarity": 'Uncommon', "price": 157, "xp": 10, "tradeable": True},
    {"name": 'دوربین دست\u200cساز', "category": 'وسیله', "rarity": 'Rare', "price": 245, "xp": 16, "tradeable": True},
    {"name": 'چتر دست\u200cساز', "category": 'وسیله', "rarity": 'Uncommon', "price": 126, "xp": 8, "tradeable": True},
    {"name": 'عصا دست\u200cساز', "category": 'وسیله', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'چکش شکسته', "category": 'وسیله', "rarity": 'Rare', "price": 251, "xp": 16, "tradeable": True},
    {"name": 'طناب شکسته', "category": 'وسیله', "rarity": 'Common', "price": 104, "xp": 6, "tradeable": True},
    {"name": 'چراغ شکسته', "category": 'وسیله', "rarity": 'Common', "price": 40, "xp": 2, "tradeable": True},
    {"name": 'قطب\u200cنما شکسته', "category": 'وسیله', "rarity": 'Legendary', "price": 685, "xp": 45, "tradeable": True},
    {"name": 'کوله\u200cپشتی شکسته', "category": 'وسیله', "rarity": 'Epic', "price": 652, "xp": 43, "tradeable": True},
    {"name": 'کلید شکسته', "category": 'وسیله', "rarity": 'Rare', "price": 459, "xp": 30, "tradeable": True},
    {"name": 'ساعت شکسته', "category": 'وسیله', "rarity": 'Rare', "price": 139, "xp": 9, "tradeable": True},
    {"name": 'دوربین شکسته', "category": 'وسیله', "rarity": 'Common', "price": 46, "xp": 3, "tradeable": True},
    {"name": 'چتر شکسته', "category": 'وسیله', "rarity": 'Common', "price": 84, "xp": 5, "tradeable": True},
    {"name": 'عصا شکسته', "category": 'وسیله', "rarity": 'Uncommon', "price": 222, "xp": 14, "tradeable": True},
    {"name": 'چکش براق', "category": 'وسیله', "rarity": 'Uncommon', "price": 136, "xp": 9, "tradeable": True},
    {"name": 'طناب براق', "category": 'وسیله', "rarity": 'Uncommon', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'چراغ براق', "category": 'وسیله', "rarity": 'Common', "price": 119, "xp": 7, "tradeable": True},
    {"name": 'قطب\u200cنما براق', "category": 'وسیله', "rarity": 'Mythic', "price": 1570, "xp": 104, "tradeable": True},
    {"name": 'کوله\u200cپشتی براق', "category": 'وسیله', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'کلید براق', "category": 'وسیله', "rarity": 'Uncommon', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'ساعت براق', "category": 'وسیله', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'دوربین براق', "category": 'وسیله', "rarity": 'Uncommon', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'چتر براق', "category": 'وسیله', "rarity": 'Rare', "price": 178, "xp": 11, "tradeable": True},
    {"name": 'عصا براق', "category": 'وسیله', "rarity": 'Rare', "price": 109, "xp": 7, "tradeable": True},
    {"name": 'چکش زنگ\u200cزده', "category": 'وسیله', "rarity": 'Legendary', "price": 1524, "xp": 101, "tradeable": True},
    {"name": 'طناب زنگ\u200cزده', "category": 'وسیله', "rarity": 'Epic', "price": 445, "xp": 29, "tradeable": True},
    {"name": 'چراغ زنگ\u200cزده', "category": 'وسیله', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'قطب\u200cنما زنگ\u200cزده', "category": 'وسیله', "rarity": 'Common', "price": 31, "xp": 2, "tradeable": True},
    {"name": 'کوله\u200cپشتی زنگ\u200cزده', "category": 'وسیله', "rarity": 'Uncommon', "price": 161, "xp": 10, "tradeable": True},
    {"name": 'کلید زنگ\u200cزده', "category": 'وسیله', "rarity": 'Rare', "price": 163, "xp": 10, "tradeable": True},
    {"name": 'ساعت زنگ\u200cزده', "category": 'وسیله', "rarity": 'Uncommon', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'دوربین زنگ\u200cزده', "category": 'وسیله', "rarity": 'Uncommon', "price": 135, "xp": 9, "tradeable": True},
    {"name": 'چتر زنگ\u200cزده', "category": 'وسیله', "rarity": 'Rare', "price": 160, "xp": 10, "tradeable": True},
    {"name": 'عصا زنگ\u200cزده', "category": 'وسیله', "rarity": 'Uncommon', "price": 86, "xp": 5, "tradeable": True},
    {"name": 'چکش کارآمد', "category": 'وسیله', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'طناب کارآمد', "category": 'وسیله', "rarity": 'Common', "price": 43, "xp": 2, "tradeable": True},
    {"name": 'چراغ کارآمد', "category": 'وسیله', "rarity": 'Legendary', "price": 548, "xp": 36, "tradeable": True},
    {"name": 'قطب\u200cنما کارآمد', "category": 'وسیله', "rarity": 'Legendary', "price": 1135, "xp": 75, "tradeable": True},
    {"name": 'کوله\u200cپشتی کارآمد', "category": 'وسیله', "rarity": 'Common', "price": 65, "xp": 4, "tradeable": True},
    {"name": 'کلید کارآمد', "category": 'وسیله', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'ساعت کارآمد', "category": 'وسیله', "rarity": 'Rare', "price": 128, "xp": 8, "tradeable": True},
    {"name": 'دوربین کارآمد', "category": 'وسیله', "rarity": 'Epic', "price": 357, "xp": 23, "tradeable": True},
    {"name": 'چتر کارآمد', "category": 'وسیله', "rarity": 'Common', "price": 109, "xp": 7, "tradeable": True},
    {"name": 'عصا کارآمد', "category": 'وسیله', "rarity": 'Rare', "price": 265, "xp": 17, "tradeable": True},
    {"name": 'چکش مرموز', "category": 'وسیله', "rarity": 'Uncommon', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'طناب مرموز', "category": 'وسیله', "rarity": 'Common', "price": 79, "xp": 5, "tradeable": True},
    {"name": 'چراغ مرموز', "category": 'وسیله', "rarity": 'Common', "price": 84, "xp": 5, "tradeable": True},
    {"name": 'قطب\u200cنما مرموز', "category": 'وسیله', "rarity": 'Uncommon', "price": 128, "xp": 8, "tradeable": True},
    {"name": 'کوله\u200cپشتی مرموز', "category": 'وسیله', "rarity": 'Uncommon', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'کلید مرموز', "category": 'وسیله', "rarity": 'Legendary', "price": 453, "xp": 30, "tradeable": True},
    {"name": 'ساعت مرموز', "category": 'وسیله', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'دوربین مرموز', "category": 'وسیله', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'چتر مرموز', "category": 'وسیله', "rarity": 'Rare', "price": 155, "xp": 10, "tradeable": True},
    {"name": 'عصا مرموز', "category": 'وسیله', "rarity": 'Mythic', "price": 2604, "xp": 173, "tradeable": True},
    {"name": 'پادشاه گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'ملکه گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'قهرمان گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'جادوگر بزرگ گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Rare', "price": 176, "xp": 11, "tradeable": True},
    {"name": 'پیامبر رویا گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 133, "xp": 8, "tradeable": True},
    {"name": 'نگهبان دروازه گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 192, "xp": 12, "tradeable": True},
    {"name": 'روح جنگاور گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 62, "xp": 4, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان گمشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'پادشاه افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Rare', "price": 287, "xp": 19, "tradeable": True},
    {"name": 'ملکه افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 47, "xp": 3, "tradeable": True},
    {"name": 'قهرمان افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Rare', "price": 150, "xp": 10, "tradeable": True},
    {"name": 'جادوگر بزرگ افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'پیامبر رویا افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'نگهبان دروازه افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 71, "xp": 4, "tradeable": True},
    {"name": 'روح جنگاور افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 288, "xp": 19, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان افسانه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Rare', "price": 127, "xp": 8, "tradeable": True},
    {"name": 'پادشاه ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 324, "xp": 21, "tradeable": True},
    {"name": 'ملکه ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'قهرمان ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 63, "xp": 4, "tradeable": True},
    {"name": 'جادوگر بزرگ ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'God', "price": 2526, "xp": 168, "tradeable": True},
    {"name": 'پیامبر رویا ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 739, "xp": 49, "tradeable": True},
    {"name": 'نگهبان دروازه ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'روح جنگاور ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 50, "xp": 3, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان ابدی', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 704, "xp": 46, "tradeable": True},
    {"name": 'پادشاه سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Legendary', "price": 654, "xp": 43, "tradeable": True},
    {"name": 'ملکه سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 31, "xp": 2, "tradeable": True},
    {"name": 'قهرمان سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 103, "xp": 6, "tradeable": True},
    {"name": 'جادوگر بزرگ سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 62, "xp": 4, "tradeable": True},
    {"name": 'پیامبر رویا سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Legendary', "price": 477, "xp": 31, "tradeable": True},
    {"name": 'نگهبان دروازه سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 167, "xp": 11, "tradeable": True},
    {"name": 'روح جنگاور سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان سایه\u200cای', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'پادشاه نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'ملکه نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 137, "xp": 9, "tradeable": True},
    {"name": 'قهرمان نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 207, "xp": 13, "tradeable": True},
    {"name": 'جادوگر بزرگ نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'پیامبر رویا نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'نگهبان دروازه نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'God', "price": 7112, "xp": 474, "tradeable": True},
    {"name": 'روح جنگاور نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Mythic', "price": 2339, "xp": 155, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان نفرین\u200cشده', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Rare', "price": 371, "xp": 24, "tradeable": True},
    {"name": 'پادشاه درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 147, "xp": 9, "tradeable": True},
    {"name": 'ملکه درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 47, "xp": 3, "tradeable": True},
    {"name": 'قهرمان درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 431, "xp": 28, "tradeable": True},
    {"name": 'جادوگر بزرگ درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'God', "price": 5696, "xp": 379, "tradeable": True},
    {"name": 'پیامبر رویا درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 151, "xp": 10, "tradeable": True},
    {"name": 'نگهبان دروازه درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'روح جنگاور درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 349, "xp": 23, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان درخشان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Legendary', "price": 1158, "xp": 77, "tradeable": True},
    {"name": 'پادشاه خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 369, "xp": 24, "tradeable": True},
    {"name": 'ملکه خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 188, "xp": 12, "tradeable": True},
    {"name": 'قهرمان خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'جادوگر بزرگ خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 131, "xp": 8, "tradeable": True},
    {"name": 'پیامبر رویا خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 796, "xp": 53, "tradeable": True},
    {"name": 'نگهبان دروازه خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 28, "xp": 1, "tradeable": True},
    {"name": 'روح جنگاور خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 46, "xp": 3, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان خاموش', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Uncommon', "price": 181, "xp": 12, "tradeable": True},
    {"name": 'پادشاه پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 44, "xp": 2, "tradeable": True},
    {"name": 'ملکه پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 58, "xp": 3, "tradeable": True},
    {"name": 'قهرمان پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 56, "xp": 3, "tradeable": True},
    {"name": 'جادوگر بزرگ پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Epic', "price": 778, "xp": 51, "tradeable": True},
    {"name": 'پیامبر رویا پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'نگهبان دروازه پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'روح جنگاور پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Common', "price": 101, "xp": 6, "tradeable": True},
    {"name": 'اسطوره\u200cی کوهستان پنهان', "category": 'شخصیت\u200cافسانه\u200cای', "rarity": 'Mythic', "price": 1862, "xp": 124, "tradeable": True},
    {"name": 'مریخ سرخ', "category": 'سیاره', "rarity": 'Mythic', "price": 2051, "xp": 136, "tradeable": True},
    {"name": 'زحل سرخ', "category": 'سیاره', "rarity": 'Uncommon', "price": 102, "xp": 6, "tradeable": True},
    {"name": 'مشتری سرخ', "category": 'سیاره', "rarity": 'Rare', "price": 231, "xp": 15, "tradeable": True},
    {"name": 'نپتون سرخ', "category": 'سیاره', "rarity": 'Rare', "price": 392, "xp": 26, "tradeable": True},
    {"name": 'اورانوس سرخ', "category": 'سیاره', "rarity": 'Rare', "price": 121, "xp": 8, "tradeable": True},
    {"name": 'عطارد سرخ', "category": 'سیاره', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'زهره سرخ', "category": 'سیاره', "rarity": 'Common', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'پلوتو سرخ', "category": 'سیاره', "rarity": 'Rare', "price": 233, "xp": 15, "tradeable": True},
    {"name": 'مریخ یخی', "category": 'سیاره', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'زحل یخی', "category": 'سیاره', "rarity": 'Rare', "price": 294, "xp": 19, "tradeable": True},
    {"name": 'مشتری یخی', "category": 'سیاره', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'نپتون یخی', "category": 'سیاره', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'اورانوس یخی', "category": 'سیاره', "rarity": 'Common', "price": 100, "xp": 6, "tradeable": True},
    {"name": 'عطارد یخی', "category": 'سیاره', "rarity": 'Mythic', "price": 2574, "xp": 171, "tradeable": True},
    {"name": 'زهره یخی', "category": 'سیاره', "rarity": 'Common', "price": 97, "xp": 6, "tradeable": True},
    {"name": 'پلوتو یخی', "category": 'سیاره', "rarity": 'Epic', "price": 408, "xp": 27, "tradeable": True},
    {"name": 'مریخ حلقه\u200cدار', "category": 'سیاره', "rarity": 'Common', "price": 32, "xp": 2, "tradeable": True},
    {"name": 'زحل حلقه\u200cدار', "category": 'سیاره', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'مشتری حلقه\u200cدار', "category": 'سیاره', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'نپتون حلقه\u200cدار', "category": 'سیاره', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'اورانوس حلقه\u200cدار', "category": 'سیاره', "rarity": 'Mythic', "price": 3145, "xp": 209, "tradeable": True},
    {"name": 'عطارد حلقه\u200cدار', "category": 'سیاره', "rarity": 'Uncommon', "price": 112, "xp": 7, "tradeable": True},
    {"name": 'زهره حلقه\u200cدار', "category": 'سیاره', "rarity": 'Uncommon', "price": 61, "xp": 4, "tradeable": True},
    {"name": 'پلوتو حلقه\u200cدار', "category": 'سیاره', "rarity": 'Common', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'مریخ غول\u200cپیکر', "category": 'سیاره', "rarity": 'Uncommon', "price": 184, "xp": 12, "tradeable": True},
    {"name": 'زحل غول\u200cپیکر', "category": 'سیاره', "rarity": 'Common', "price": 50, "xp": 3, "tradeable": True},
    {"name": 'مشتری غول\u200cپیکر', "category": 'سیاره', "rarity": 'Common', "price": 121, "xp": 8, "tradeable": True},
    {"name": 'نپتون غول\u200cپیکر', "category": 'سیاره', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'اورانوس غول\u200cپیکر', "category": 'سیاره', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'عطارد غول\u200cپیکر', "category": 'سیاره', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'زهره غول\u200cپیکر', "category": 'سیاره', "rarity": 'Mythic', "price": 3299, "xp": 219, "tradeable": True},
    {"name": 'پلوتو غول\u200cپیکر', "category": 'سیاره', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'مریخ دوردست', "category": 'سیاره', "rarity": 'Epic', "price": 426, "xp": 28, "tradeable": True},
    {"name": 'زحل دوردست', "category": 'سیاره', "rarity": 'Uncommon', "price": 104, "xp": 6, "tradeable": True},
    {"name": 'مشتری دوردست', "category": 'سیاره', "rarity": 'Common', "price": 54, "xp": 3, "tradeable": True},
    {"name": 'نپتون دوردست', "category": 'سیاره', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'اورانوس دوردست', "category": 'سیاره', "rarity": 'Epic', "price": 259, "xp": 17, "tradeable": True},
    {"name": 'عطارد دوردست', "category": 'سیاره', "rarity": 'Rare', "price": 141, "xp": 9, "tradeable": True},
    {"name": 'زهره دوردست', "category": 'سیاره', "rarity": 'Common', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'پلوتو دوردست', "category": 'سیاره', "rarity": 'Rare', "price": 254, "xp": 16, "tradeable": True},
    {"name": 'مریخ سوزان', "category": 'سیاره', "rarity": 'Epic', "price": 775, "xp": 51, "tradeable": True},
    {"name": 'زحل سوزان', "category": 'سیاره', "rarity": 'Uncommon', "price": 195, "xp": 13, "tradeable": True},
    {"name": 'مشتری سوزان', "category": 'سیاره', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'نپتون سوزان', "category": 'سیاره', "rarity": 'Legendary', "price": 536, "xp": 35, "tradeable": True},
    {"name": 'اورانوس سوزان', "category": 'سیاره', "rarity": 'Legendary', "price": 619, "xp": 41, "tradeable": True},
    {"name": 'عطارد سوزان', "category": 'سیاره', "rarity": 'Rare', "price": 265, "xp": 17, "tradeable": True},
    {"name": 'زهره سوزان', "category": 'سیاره', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'پلوتو سوزان', "category": 'سیاره', "rarity": 'Common', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'مریخ آبی', "category": 'سیاره', "rarity": 'Rare', "price": 285, "xp": 19, "tradeable": True},
    {"name": 'زحل آبی', "category": 'سیاره', "rarity": 'Uncommon', "price": 120, "xp": 8, "tradeable": True},
    {"name": 'مشتری آبی', "category": 'سیاره', "rarity": 'Rare', "price": 128, "xp": 8, "tradeable": True},
    {"name": 'نپتون آبی', "category": 'سیاره', "rarity": 'Uncommon', "price": 192, "xp": 12, "tradeable": True},
    {"name": 'اورانوس آبی', "category": 'سیاره', "rarity": 'Common', "price": 110, "xp": 7, "tradeable": True},
    {"name": 'عطارد آبی', "category": 'سیاره', "rarity": 'Common', "price": 114, "xp": 7, "tradeable": True},
    {"name": 'زهره آبی', "category": 'سیاره', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'پلوتو آبی', "category": 'سیاره', "rarity": 'Uncommon', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'مریخ پنهان', "category": 'سیاره', "rarity": 'Uncommon', "price": 100, "xp": 6, "tradeable": True},
    {"name": 'زحل پنهان', "category": 'سیاره', "rarity": 'Epic', "price": 733, "xp": 48, "tradeable": True},
    {"name": 'مشتری پنهان', "category": 'سیاره', "rarity": 'Common', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'نپتون پنهان', "category": 'سیاره', "rarity": 'Uncommon', "price": 97, "xp": 6, "tradeable": True},
    {"name": 'اورانوس پنهان', "category": 'سیاره', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'عطارد پنهان', "category": 'سیاره', "rarity": 'Epic', "price": 659, "xp": 43, "tradeable": True},
    {"name": 'زهره پنهان', "category": 'سیاره', "rarity": 'Common', "price": 113, "xp": 7, "tradeable": True},
    {"name": 'پلوتو پنهان', "category": 'سیاره', "rarity": 'Epic', "price": 248, "xp": 16, "tradeable": True},
    {"name": 'الماس براق', "category": 'سنگ', "rarity": 'Rare', "price": 259, "xp": 17, "tradeable": True},
    {"name": 'یاقوت براق', "category": 'سنگ', "rarity": 'Uncommon', "price": 108, "xp": 7, "tradeable": True},
    {"name": 'زمرد براق', "category": 'سنگ', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'یاقوت\u200cکبود براق', "category": 'سنگ', "rarity": 'Uncommon', "price": 132, "xp": 8, "tradeable": True},
    {"name": 'فیروزه براق', "category": 'سنگ', "rarity": 'Uncommon', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'کهربا براق', "category": 'سنگ', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'عقیق براق', "category": 'سنگ', "rarity": 'Rare', "price": 372, "xp": 24, "tradeable": True},
    {"name": 'مروارید براق', "category": 'سنگ', "rarity": 'Uncommon', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'الماس خام', "category": 'سنگ', "rarity": 'Rare', "price": 281, "xp": 18, "tradeable": True},
    {"name": 'یاقوت خام', "category": 'سنگ', "rarity": 'Epic', "price": 234, "xp": 15, "tradeable": True},
    {"name": 'زمرد خام', "category": 'سنگ', "rarity": 'Epic', "price": 536, "xp": 35, "tradeable": True},
    {"name": 'یاقوت\u200cکبود خام', "category": 'سنگ', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'فیروزه خام', "category": 'سنگ', "rarity": 'Common', "price": 51, "xp": 3, "tradeable": True},
    {"name": 'کهربا خام', "category": 'سنگ', "rarity": 'Epic', "price": 750, "xp": 50, "tradeable": True},
    {"name": 'عقیق خام', "category": 'سنگ', "rarity": 'Legendary', "price": 455, "xp": 30, "tradeable": True},
    {"name": 'مروارید خام', "category": 'سنگ', "rarity": 'Uncommon', "price": 154, "xp": 10, "tradeable": True},
    {"name": 'الماس کمیاب', "category": 'سنگ', "rarity": 'Epic', "price": 779, "xp": 51, "tradeable": True},
    {"name": 'یاقوت کمیاب', "category": 'سنگ', "rarity": 'Common', "price": 73, "xp": 4, "tradeable": True},
    {"name": 'زمرد کمیاب', "category": 'سنگ', "rarity": 'Rare', "price": 214, "xp": 14, "tradeable": True},
    {"name": 'یاقوت\u200cکبود کمیاب', "category": 'سنگ', "rarity": 'Common', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'فیروزه کمیاب', "category": 'سنگ', "rarity": 'Uncommon', "price": 104, "xp": 6, "tradeable": True},
    {"name": 'کهربا کمیاب', "category": 'سنگ', "rarity": 'Uncommon', "price": 78, "xp": 5, "tradeable": True},
    {"name": 'عقیق کمیاب', "category": 'سنگ', "rarity": 'Mythic', "price": 964, "xp": 64, "tradeable": True},
    {"name": 'مروارید کمیاب', "category": 'سنگ', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'الماس شفاف', "category": 'سنگ', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'یاقوت شفاف', "category": 'سنگ', "rarity": 'Legendary', "price": 986, "xp": 65, "tradeable": True},
    {"name": 'زمرد شفاف', "category": 'سنگ', "rarity": 'Common', "price": 76, "xp": 5, "tradeable": True},
    {"name": 'یاقوت\u200cکبود شفاف', "category": 'سنگ', "rarity": 'Common', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'فیروزه شفاف', "category": 'سنگ', "rarity": 'Epic', "price": 699, "xp": 46, "tradeable": True},
    {"name": 'کهربا شفاف', "category": 'سنگ', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'عقیق شفاف', "category": 'سنگ', "rarity": 'Uncommon', "price": 125, "xp": 8, "tradeable": True},
    {"name": 'مروارید شفاف', "category": 'سنگ', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'الماس تیره', "category": 'سنگ', "rarity": 'Common', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'یاقوت تیره', "category": 'سنگ', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'زمرد تیره', "category": 'سنگ', "rarity": 'Uncommon', "price": 149, "xp": 9, "tradeable": True},
    {"name": 'یاقوت\u200cکبود تیره', "category": 'سنگ', "rarity": 'Epic', "price": 732, "xp": 48, "tradeable": True},
    {"name": 'فیروزه تیره', "category": 'سنگ', "rarity": 'Rare', "price": 148, "xp": 9, "tradeable": True},
    {"name": 'کهربا تیره', "category": 'سنگ', "rarity": 'God', "price": 4738, "xp": 315, "tradeable": True},
    {"name": 'عقیق تیره', "category": 'سنگ', "rarity": 'Uncommon', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'مروارید تیره', "category": 'سنگ', "rarity": 'God', "price": 3147, "xp": 209, "tradeable": True},
    {"name": 'الماس درخشان', "category": 'سنگ', "rarity": 'Rare', "price": 438, "xp": 29, "tradeable": True},
    {"name": 'یاقوت درخشان', "category": 'سنگ', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'زمرد درخشان', "category": 'سنگ', "rarity": 'Epic', "price": 401, "xp": 26, "tradeable": True},
    {"name": 'یاقوت\u200cکبود درخشان', "category": 'سنگ', "rarity": 'Uncommon', "price": 146, "xp": 9, "tradeable": True},
    {"name": 'فیروزه درخشان', "category": 'سنگ', "rarity": 'Uncommon', "price": 134, "xp": 8, "tradeable": True},
    {"name": 'کهربا درخشان', "category": 'سنگ', "rarity": 'Epic', "price": 298, "xp": 19, "tradeable": True},
    {"name": 'عقیق درخشان', "category": 'سنگ', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'مروارید درخشان', "category": 'سنگ', "rarity": 'Epic', "price": 573, "xp": 38, "tradeable": True},
    {"name": 'الماس کهن', "category": 'سنگ', "rarity": 'Common', "price": 46, "xp": 3, "tradeable": True},
    {"name": 'یاقوت کهن', "category": 'سنگ', "rarity": 'Epic', "price": 323, "xp": 21, "tradeable": True},
    {"name": 'زمرد کهن', "category": 'سنگ', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'یاقوت\u200cکبود کهن', "category": 'سنگ', "rarity": 'Common', "price": 68, "xp": 4, "tradeable": True},
    {"name": 'فیروزه کهن', "category": 'سنگ', "rarity": 'Epic', "price": 340, "xp": 22, "tradeable": True},
    {"name": 'کهربا کهن', "category": 'سنگ', "rarity": 'Common', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'عقیق کهن', "category": 'سنگ', "rarity": 'Epic', "price": 613, "xp": 40, "tradeable": True},
    {"name": 'مروارید کهن', "category": 'سنگ', "rarity": 'Epic', "price": 504, "xp": 33, "tradeable": True},
    {"name": 'الماس یخی', "category": 'سنگ', "rarity": 'Uncommon', "price": 99, "xp": 6, "tradeable": True},
    {"name": 'یاقوت یخی', "category": 'سنگ', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'زمرد یخی', "category": 'سنگ', "rarity": 'Rare', "price": 221, "xp": 14, "tradeable": True},
    {"name": 'یاقوت\u200cکبود یخی', "category": 'سنگ', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'فیروزه یخی', "category": 'سنگ', "rarity": 'Uncommon', "price": 168, "xp": 11, "tradeable": True},
    {"name": 'کهربا یخی', "category": 'سنگ', "rarity": 'Mythic', "price": 1901, "xp": 126, "tradeable": True},
    {"name": 'عقیق یخی', "category": 'سنگ', "rarity": 'Uncommon', "price": 196, "xp": 13, "tradeable": True},
    {"name": 'مروارید یخی', "category": 'سنگ', "rarity": 'Epic', "price": 694, "xp": 46, "tradeable": True},
    {"name": 'شمشیر طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 222, "xp": 14, "tradeable": True},
    {"name": 'کمان طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 477, "xp": 31, "tradeable": True},
    {"name": 'خنجر طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 60, "xp": 4, "tradeable": True},
    {"name": 'تبر طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 693, "xp": 46, "tradeable": True},
    {"name": 'نیزه طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 210, "xp": 14, "tradeable": True},
    {"name": 'گرز طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 45, "xp": 3, "tradeable": True},
    {"name": 'سپر طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Legendary', "price": 1321, "xp": 88, "tradeable": True},
    {"name": 'چماق طلاکاری\u200cشده', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 65, "xp": 4, "tradeable": True},
    {"name": 'شمشیر جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 41, "xp": 2, "tradeable": True},
    {"name": 'کمان جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 83, "xp": 5, "tradeable": True},
    {"name": 'خنجر جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 33, "xp": 2, "tradeable": True},
    {"name": 'تبر جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 217, "xp": 14, "tradeable": True},
    {"name": 'نیزه جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'گرز جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 105, "xp": 7, "tradeable": True},
    {"name": 'سپر جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'چماق جواهرنشان', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 207, "xp": 13, "tradeable": True},
    {"name": 'شمشیر کهن', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 29, "xp": 1, "tradeable": True},
    {"name": 'کمان کهن', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 359, "xp": 23, "tradeable": True},
    {"name": 'خنجر کهن', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 94, "xp": 6, "tradeable": True},
    {"name": 'تبر کهن', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 881, "xp": 58, "tradeable": True},
    {"name": 'نیزه کهن', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 52, "xp": 3, "tradeable": True},
    {"name": 'گرز کهن', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 130, "xp": 8, "tradeable": True},
    {"name": 'سپر کهن', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 430, "xp": 28, "tradeable": True},
    {"name": 'چماق کهن', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'شمشیر افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 128, "xp": 8, "tradeable": True},
    {"name": 'کمان افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 96, "xp": 6, "tradeable": True},
    {"name": 'خنجر افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 214, "xp": 14, "tradeable": True},
    {"name": 'تبر افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 249, "xp": 16, "tradeable": True},
    {"name": 'نیزه افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 181, "xp": 12, "tradeable": True},
    {"name": 'گرز افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 69, "xp": 4, "tradeable": True},
    {"name": 'سپر افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 124, "xp": 8, "tradeable": True},
    {"name": 'چماق افسانه\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 64, "xp": 4, "tradeable": True},
    {"name": 'شمشیر نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 730, "xp": 48, "tradeable": True},
    {"name": 'کمان نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 37, "xp": 2, "tradeable": True},
    {"name": 'خنجر نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 112, "xp": 7, "tradeable": True},
    {"name": 'تبر نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'نیزه نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 634, "xp": 42, "tradeable": True},
    {"name": 'گرز نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 65, "xp": 4, "tradeable": True},
    {"name": 'سپر نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 164, "xp": 10, "tradeable": True},
    {"name": 'چماق نقره\u200cای', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 109, "xp": 7, "tradeable": True},
    {"name": 'شمشیر زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 87, "xp": 5, "tradeable": True},
    {"name": 'کمان زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 191, "xp": 12, "tradeable": True},
    {"name": 'خنجر زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 346, "xp": 23, "tradeable": True},
    {"name": 'تبر زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 153, "xp": 10, "tradeable": True},
    {"name": 'نیزه زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 82, "xp": 5, "tradeable": True},
    {"name": 'گرز زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 383, "xp": 25, "tradeable": True},
    {"name": 'سپر زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 167, "xp": 11, "tradeable": True},
    {"name": 'چماق زنگارگرفته', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 56, "xp": 3, "tradeable": True},
    {"name": 'شمشیر درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 918, "xp": 61, "tradeable": True},
    {"name": 'کمان درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 149, "xp": 9, "tradeable": True},
    {"name": 'خنجر درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 309, "xp": 20, "tradeable": True},
    {"name": 'تبر درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 170, "xp": 11, "tradeable": True},
    {"name": 'نیزه درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 354, "xp": 23, "tradeable": True},
    {"name": 'گرز درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Rare', "price": 276, "xp": 18, "tradeable": True},
    {"name": 'سپر درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 232, "xp": 15, "tradeable": True},
    {"name": 'چماق درخشان', "category": 'اسلحه_تزئینی', "rarity": 'Legendary', "price": 1726, "xp": 115, "tradeable": True},
    {"name": 'شمشیر مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Epic', "price": 341, "xp": 22, "tradeable": True},
    {"name": 'کمان مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 115, "xp": 7, "tradeable": True},
    {"name": 'خنجر مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'تبر مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 195, "xp": 13, "tradeable": True},
    {"name": 'نیزه مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 103, "xp": 6, "tradeable": True},
    {"name": 'گرز مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Uncommon', "price": 190, "xp": 12, "tradeable": True},
    {"name": 'سپر مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 109, "xp": 7, "tradeable": True},
    {"name": 'چماق مرموز', "category": 'اسلحه_تزئینی', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'ردا شاهانه', "category": 'لباس', "rarity": 'Uncommon', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'کلاه شاهانه', "category": 'لباس', "rarity": 'Rare', "price": 338, "xp": 22, "tradeable": True},
    {"name": 'چکمه شاهانه', "category": 'لباس', "rarity": 'Uncommon', "price": 224, "xp": 14, "tradeable": True},
    {"name": 'دستکش شاهانه', "category": 'لباس', "rarity": 'Common', "price": 43, "xp": 2, "tradeable": True},
    {"name": 'شنل شاهانه', "category": 'لباس', "rarity": 'Epic', "price": 622, "xp": 41, "tradeable": True},
    {"name": 'زره شاهانه', "category": 'لباس', "rarity": 'Uncommon', "price": 208, "xp": 13, "tradeable": True},
    {"name": 'کمربند شاهانه', "category": 'لباس', "rarity": 'Common', "price": 74, "xp": 4, "tradeable": True},
    {"name": 'روسری شاهانه', "category": 'لباس', "rarity": 'Rare', "price": 185, "xp": 12, "tradeable": True},
    {"name": 'ردا جادویی', "category": 'لباس', "rarity": 'Common', "price": 71, "xp": 4, "tradeable": True},
    {"name": 'کلاه جادویی', "category": 'لباس', "rarity": 'Common', "price": 35, "xp": 2, "tradeable": True},
    {"name": 'چکمه جادویی', "category": 'لباس', "rarity": 'Common', "price": 56, "xp": 3, "tradeable": True},
    {"name": 'دستکش جادویی', "category": 'لباس', "rarity": 'Rare', "price": 279, "xp": 18, "tradeable": True},
    {"name": 'شنل جادویی', "category": 'لباس', "rarity": 'Uncommon', "price": 92, "xp": 6, "tradeable": True},
    {"name": 'زره جادویی', "category": 'لباس', "rarity": 'Epic', "price": 469, "xp": 31, "tradeable": True},
    {"name": 'کمربند جادویی', "category": 'لباس', "rarity": 'Rare', "price": 249, "xp": 16, "tradeable": True},
    {"name": 'روسری جادویی', "category": 'لباس', "rarity": 'Uncommon', "price": 106, "xp": 7, "tradeable": True},
    {"name": 'ردا کهنه', "category": 'لباس', "rarity": 'Uncommon', "price": 163, "xp": 10, "tradeable": True},
    {"name": 'کلاه کهنه', "category": 'لباس', "rarity": 'Uncommon', "price": 226, "xp": 15, "tradeable": True},
    {"name": 'چکمه کهنه', "category": 'لباس', "rarity": 'Uncommon', "price": 207, "xp": 13, "tradeable": True},
    {"name": 'دستکش کهنه', "category": 'لباس', "rarity": 'Uncommon', "price": 226, "xp": 15, "tradeable": True},
    {"name": 'شنل کهنه', "category": 'لباس', "rarity": 'Legendary', "price": 908, "xp": 60, "tradeable": True},
    {"name": 'زره کهنه', "category": 'لباس', "rarity": 'Common', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'کمربند کهنه', "category": 'لباس', "rarity": 'Uncommon', "price": 149, "xp": 9, "tradeable": True},
    {"name": 'روسری کهنه', "category": 'لباس', "rarity": 'Common', "price": 57, "xp": 3, "tradeable": True},
    {"name": 'ردا رزمی', "category": 'لباس', "rarity": 'Mythic', "price": 2063, "xp": 137, "tradeable": True},
    {"name": 'کلاه رزمی', "category": 'لباس', "rarity": 'Common', "price": 90, "xp": 6, "tradeable": True},
    {"name": 'چکمه رزمی', "category": 'لباس', "rarity": 'Epic', "price": 721, "xp": 48, "tradeable": True},
    {"name": 'دستکش رزمی', "category": 'لباس', "rarity": 'Rare', "price": 323, "xp": 21, "tradeable": True},
    {"name": 'شنل رزمی', "category": 'لباس', "rarity": 'Legendary', "price": 769, "xp": 51, "tradeable": True},
    {"name": 'زره رزمی', "category": 'لباس', "rarity": 'Uncommon', "price": 153, "xp": 10, "tradeable": True},
    {"name": 'کمربند رزمی', "category": 'لباس', "rarity": 'Legendary', "price": 890, "xp": 59, "tradeable": True},
    {"name": 'روسری رزمی', "category": 'لباس', "rarity": 'Uncommon', "price": 109, "xp": 7, "tradeable": True},
    {"name": 'ردا مخملی', "category": 'لباس', "rarity": 'Rare', "price": 328, "xp": 21, "tradeable": True},
    {"name": 'کلاه مخملی', "category": 'لباس', "rarity": 'Rare', "price": 397, "xp": 26, "tradeable": True},
    {"name": 'چکمه مخملی', "category": 'لباس', "rarity": 'Legendary', "price": 1415, "xp": 94, "tradeable": True},
    {"name": 'دستکش مخملی', "category": 'لباس', "rarity": 'Epic', "price": 739, "xp": 49, "tradeable": True},
    {"name": 'شنل مخملی', "category": 'لباس', "rarity": 'Epic', "price": 263, "xp": 17, "tradeable": True},
    {"name": 'زره مخملی', "category": 'لباس', "rarity": 'Common', "price": 39, "xp": 2, "tradeable": True},
    {"name": 'کمربند مخملی', "category": 'لباس', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'روسری مخملی', "category": 'لباس', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'ردا سفری', "category": 'لباس', "rarity": 'Common', "price": 42, "xp": 2, "tradeable": True},
    {"name": 'کلاه سفری', "category": 'لباس', "rarity": 'Common', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'چکمه سفری', "category": 'لباس', "rarity": 'Epic', "price": 381, "xp": 25, "tradeable": True},
    {"name": 'دستکش سفری', "category": 'لباس', "rarity": 'Rare', "price": 271, "xp": 18, "tradeable": True},
    {"name": 'شنل سفری', "category": 'لباس', "rarity": 'Uncommon', "price": 126, "xp": 8, "tradeable": True},
    {"name": 'زره سفری', "category": 'لباس', "rarity": 'Rare', "price": 302, "xp": 20, "tradeable": True},
    {"name": 'کمربند سفری', "category": 'لباس', "rarity": 'Common', "price": 77, "xp": 5, "tradeable": True},
    {"name": 'روسری سفری', "category": 'لباس', "rarity": 'Legendary', "price": 1347, "xp": 89, "tradeable": True},
    {"name": 'ردا تشریفاتی', "category": 'لباس', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'کلاه تشریفاتی', "category": 'لباس', "rarity": 'Epic', "price": 835, "xp": 55, "tradeable": True},
    {"name": 'چکمه تشریفاتی', "category": 'لباس', "rarity": 'Legendary', "price": 1360, "xp": 90, "tradeable": True},
    {"name": 'دستکش تشریفاتی', "category": 'لباس', "rarity": 'Uncommon', "price": 130, "xp": 8, "tradeable": True},
    {"name": 'شنل تشریفاتی', "category": 'لباس', "rarity": 'Mythic', "price": 2059, "xp": 137, "tradeable": True},
    {"name": 'زره تشریفاتی', "category": 'لباس', "rarity": 'Mythic', "price": 1045, "xp": 69, "tradeable": True},
    {"name": 'کمربند تشریفاتی', "category": 'لباس', "rarity": 'Common', "price": 81, "xp": 5, "tradeable": True},
    {"name": 'روسری تشریفاتی', "category": 'لباس', "rarity": 'Common', "price": 70, "xp": 4, "tradeable": True},
    {"name": 'ردا مرموز', "category": 'لباس', "rarity": 'Common', "price": 49, "xp": 3, "tradeable": True},
    {"name": 'کلاه مرموز', "category": 'لباس', "rarity": 'Uncommon', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'چکمه مرموز', "category": 'لباس', "rarity": 'Legendary', "price": 640, "xp": 42, "tradeable": True},
    {"name": 'دستکش مرموز', "category": 'لباس', "rarity": 'Uncommon', "price": 142, "xp": 9, "tradeable": True},
    {"name": 'شنل مرموز', "category": 'لباس', "rarity": 'Uncommon', "price": 125, "xp": 8, "tradeable": True},
    {"name": 'زره مرموز', "category": 'لباس', "rarity": 'Rare', "price": 353, "xp": 23, "tradeable": True},
    {"name": 'کمربند مرموز', "category": 'لباس', "rarity": 'Common', "price": 45, "xp": 3, "tradeable": True},
    {"name": 'روسری مرموز', "category": 'لباس', "rarity": 'Common', "price": 105, "xp": 7, "tradeable": True},
    {"name": 'کالسکه اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 72, "xp": 4, "tradeable": True},
    {"name": 'موتور جنگی اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 282, "xp": 18, "tradeable": True},
    {"name": 'کشتی بادبانی اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 100, "xp": 6, "tradeable": True},
    {"name": 'بالون هوایی اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 53, "xp": 3, "tradeable": True},
    {"name": 'گاری اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 75, "xp": 5, "tradeable": True},
    {"name": 'قایق اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 156, "xp": 10, "tradeable": True},
    {"name": 'سورتمه اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 281, "xp": 18, "tradeable": True},
    {"name": 'ارابه اسپرت', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 174, "xp": 11, "tradeable": True},
    {"name": 'کالسکه سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 107, "xp": 7, "tradeable": True},
    {"name": 'موتور جنگی سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 162, "xp": 10, "tradeable": True},
    {"name": 'کشتی بادبانی سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 151, "xp": 10, "tradeable": True},
    {"name": 'بالون هوایی سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Epic', "price": 414, "xp": 27, "tradeable": True},
    {"name": 'گاری سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 37, "xp": 2, "tradeable": True},
    {"name": 'قایق سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Legendary', "price": 1567, "xp": 104, "tradeable": True},
    {"name": 'سورتمه سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 127, "xp": 8, "tradeable": True},
    {"name": 'ارابه سلطنتی', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 409, "xp": 27, "tradeable": True},
    {"name": 'کالسکه زرهی', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 95, "xp": 6, "tradeable": True},
    {"name": 'موتور جنگی زرهی', "category": 'وسیله_نقلیه', "rarity": 'Legendary', "price": 596, "xp": 39, "tradeable": True},
    {"name": 'کشتی بادبانی زرهی', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 114, "xp": 7, "tradeable": True},
    {"name": 'بالون هوایی زرهی', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 66, "xp": 4, "tradeable": True},
    {"name": 'گاری زرهی', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 148, "xp": 9, "tradeable": True},
    {"name": 'قایق زرهی', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 194, "xp": 12, "tradeable": True},
    {"name": 'سورتمه زرهی', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 63, "xp": 4, "tradeable": True},
    {"name": 'ارابه زرهی', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 33, "xp": 2, "tradeable": True},
    {"name": 'کالسکه کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'موتور جنگی کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 80, "xp": 5, "tradeable": True},
    {"name": 'کشتی بادبانی کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 300, "xp": 20, "tradeable": True},
    {"name": 'بالون هوایی کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'گاری کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Epic', "price": 273, "xp": 18, "tradeable": True},
    {"name": 'قایق کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 110, "xp": 7, "tradeable": True},
    {"name": 'سورتمه کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 375, "xp": 25, "tradeable": True},
    {"name": 'ارابه کلاسیک', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 154, "xp": 10, "tradeable": True},
    {"name": 'کالسکه سریع', "category": 'وسیله_نقلیه', "rarity": 'Legendary', "price": 1476, "xp": 98, "tradeable": True},
    {"name": 'موتور جنگی سریع', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 41, "xp": 2, "tradeable": True},
    {"name": 'کشتی بادبانی سریع', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 85, "xp": 5, "tradeable": True},
    {"name": 'بالون هوایی سریع', "category": 'وسیله_نقلیه', "rarity": 'Legendary', "price": 936, "xp": 62, "tradeable": True},
    {"name": 'گاری سریع', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 268, "xp": 17, "tradeable": True},
    {"name": 'قایق سریع', "category": 'وسیله_نقلیه', "rarity": 'Legendary', "price": 1241, "xp": 82, "tradeable": True},
    {"name": 'سورتمه سریع', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 55, "xp": 3, "tradeable": True},
    {"name": 'ارابه سریع', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 44, "xp": 2, "tradeable": True},
    {"name": 'کالسکه کهنه', "category": 'وسیله_نقلیه', "rarity": 'Legendary', "price": 1545, "xp": 103, "tradeable": True},
    {"name": 'موتور جنگی کهنه', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 152, "xp": 10, "tradeable": True},
    {"name": 'کشتی بادبانی کهنه', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 63, "xp": 4, "tradeable": True},
    {"name": 'بالون هوایی کهنه', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 140, "xp": 9, "tradeable": True},
    {"name": 'گاری کهنه', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 204, "xp": 13, "tradeable": True},
    {"name": 'قایق کهنه', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 103, "xp": 6, "tradeable": True},
    {"name": 'سورتمه کهنه', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'ارابه کهنه', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 91, "xp": 6, "tradeable": True},
    {"name": 'کالسکه براق', "category": 'وسیله_نقلیه', "rarity": 'Legendary', "price": 613, "xp": 40, "tradeable": True},
    {"name": 'موتور جنگی براق', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 67, "xp": 4, "tradeable": True},
    {"name": 'کشتی بادبانی براق', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 158, "xp": 10, "tradeable": True},
    {"name": 'بالون هوایی براق', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 89, "xp": 5, "tradeable": True},
    {"name": 'گاری براق', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 178, "xp": 11, "tradeable": True},
    {"name": 'قایق براق', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 76, "xp": 5, "tradeable": True},
    {"name": 'سورتمه براق', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 159, "xp": 10, "tradeable": True},
    {"name": 'ارابه براق', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 88, "xp": 5, "tradeable": True},
    {"name": 'کالسکه افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 181, "xp": 12, "tradeable": True},
    {"name": 'موتور جنگی افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Epic', "price": 839, "xp": 55, "tradeable": True},
    {"name": 'کشتی بادبانی افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Epic', "price": 767, "xp": 51, "tradeable": True},
    {"name": 'بالون هوایی افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Common', "price": 36, "xp": 2, "tradeable": True},
    {"name": 'گاری افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 205, "xp": 13, "tradeable": True},
    {"name": 'قایق افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Rare', "price": 302, "xp": 20, "tradeable": True},
    {"name": 'سورتمه افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Epic', "price": 464, "xp": 30, "tradeable": True},
    {"name": 'ارابه افسانه\u200cای', "category": 'وسیله_نقلیه', "rarity": 'Uncommon', "price": 81, "xp": 5, "tradeable": True},
]

ITEM_CATALOG_BY_NAME = {item['name']: item for item in ITEM_CATALOG}
ITEM_CATALOG_BY_NAME[OG_ITEM['name']] = OG_ITEM
# =============================================================================
# ⚔️ جنگ جهانی (World War) - نقشه‌ی ۴×۴ برای بازی نوبتی تصرف سرزمین
# =============================================================================
WAR_GRID_SIZE = 4
WAR_REGION_NAMES = [
    "شمال یخی", "دشت شمالی", "کوهستان شرقی", "ساحل شرقی",
    "جنگل غربی", "دشت مرکزی", "تپه‌های مرکزی", "دلتای شرقی",
    "صحرای غربی", "واحه مرکزی", "باتلاق جنوبی", "بندر جنوبی",
    "کویر غربی", "دشت جنوبی", "جزیره جنوبی", "قطب جنوبی",
]

def build_war_regions():
    regions = []
    idx = 0
    for row in range(WAR_GRID_SIZE):
        for col in range(WAR_GRID_SIZE):
            regions.append({
                "key": f"r{row}c{col}",
                "name": WAR_REGION_NAMES[idx],
                "row": row, "col": col,
            })
            idx += 1
    return regions

WAR_REGIONS = build_war_regions()
WAR_REGIONS_BY_KEY = {r["key"]: r for r in WAR_REGIONS}


def war_region_neighbors(key):
    r = WAR_REGIONS_BY_KEY[key]
    row, col = r["row"], r["col"]
    neighbor_keys = []
    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nr, nc = row + dr, col + dc
        if 0 <= nr < WAR_GRID_SIZE and 0 <= nc < WAR_GRID_SIZE:
            neighbor_keys.append(f"r{nr}c{nc}")
    return neighbor_keys


# رنگ هر بازیکن (چرخشی)، خاکستری = بی‌طرف
WAR_PLAYER_COLORS = [
    (220, 60, 60), (60, 120, 220), (60, 180, 90), (230, 180, 40),
    (170, 70, 200), (240, 130, 40), (40, 190, 190), (220, 90, 160),
]
WAR_NEUTRAL_COLOR = (140, 140, 140)

WAR_NEUTRAL_START_TROOPS = 8
WAR_PLAYER_START_TROOPS = 25
WAR_TICK_INTERVAL_SECONDS = 90
WAR_LOBBY_SECONDS = 90
WAR_MAX_TROOPS_PER_REGION = 400
WAR_WIN_CONTROL_RATIO = 0.7  # ۷۰٪ نقشه یعنی برد

# =============================================================================
# نگاشت فارسی کمیابی‌ها - برای دستور «اسپان کارت [کمیابی]» مخصوص ادمین
# =============================================================================
RARITY_PERSIAN_MAP = {
    "کومون": "Common", "کامان": "Common",
    "آنکامون": "Uncommon", "آن‌کامون": "Uncommon", "آنکومون": "Uncommon",
    "ریر": "Rare",
    "اپیک": "Epic",
    "لجندری": "Legendary", "لجندری": "Legendary",
    "میتیک": "Mythic",
    "گاد": "God",
    "سکرت": "Secret",
    "اوجی": "OG", "او جی": "OG",
}


# ==============================================================================
# بخش ۴: توابع کمکی مشترک
# ==============================================================================
def iran_now():
    return datetime.now(IRAN_TZ)


def jalali_date_str():
    """تبدیل ساده‌ی میلادی به شمسی (بدون کتابخونه‌ی خارجی)."""
    g = iran_now()
    gy, gm, gd = g.year, g.month, g.day
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    for i in range(gm2):
        g_day_no += g_days_in_month[i]
    if gm2 > 1 and ((gy % 4 == 0 and gy % 100 != 0) or (gy % 400 == 0)):
        g_day_no += 1
    g_day_no += gd2
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    for i in range(11):
        if j_day_no < j_days_in_month[i]:
            jm = i + 1
            jd = j_day_no + 1
            break
        j_day_no -= j_days_in_month[i]
    else:
        jm = 12
        jd = j_day_no + 1
    return f"{jd} {PERSIAN_MONTHS[jm - 1]} {jy}"


# =============================================================================
# سیستم سطح (لول) - منحنی سخت‌تر از قبل، تا لول بالا واقعا زحمت داشته باشه
# =============================================================================
def xp_needed_for_level(level):
    """XP لازم برای رسیدن به این لول. منحنی نمایی، نه خطی ساده."""
    return int(80 * (level ** 1.7))


def get_level(xp):
    level = 1
    while xp >= xp_needed_for_level(level + 1):
        level += 1
    return level


def get_badge(xp):
    level = get_level(xp)
    if level >= 50:
        return "👑 اسطوره"
    if level >= 35:
        return "💎 افسانه‌ای"
    if level >= 25:
        return "🔥 نخبه"
    if level >= 15:
        return "🥇 حرفه‌ای"
    if level >= 8:
        return "🥈 پیشرفته"
    if level >= 3:
        return "🥉 مبتدی‌کار"
    return "🌱 تازه‌کار"


# =============================================================================
# تشخیص کاربر هدف - هم با ریپلای، هم با آیدی مستقیم تو متن (رفع باگ قبلی)
# =============================================================================
UID_PATTERN = re.compile(r"u0[a-zA-Z0-9]{20,40}")


def extract_uid_from_text(text):
    m = UID_PATTERN.search(text or "")
    return m.group(0) if m else None


def resolve_target_uid(text_after_command, reply_sender_uid):
    """
    اولویت با ریپلای‌شده‌ست (اگه ریپلای زده باشی)، وگرنه دنبال یه uid
    مستقیم تو متن دستور می‌گرده (مثلا «اضافه کردن سکه 500 u0Hx...»).
    این دقیقا همون چیزیه که تو نسخه‌ی قبلی نبود و باعث باگ می‌شد.
    """
    if reply_sender_uid:
        return reply_sender_uid
    return extract_uid_from_text(text_after_command)


def extract_amount(text):
    """اولین عدد صحیح تو متن رو برمی‌گردونه (برای مقدار سکه و امثالش)."""
    m = re.search(r"\d+", text or "")
    return int(m.group(0)) if m else None


# =============================================================================
# ارسال همگانی با rate-limit - رفع باگ «همگانی به همه نمیره»
# =============================================================================
async def rate_limited_broadcast(send_fn, chat_ids, text):
    """
    send_fn: async تابعی که (chat_id, text) می‌گیره و پیام می‌فرسته.
    به‌جای فرستادن یهویی همه‌ی پیام‌ها، به‌صورت دسته‌ای و با تاخیر می‌فرسته
    تا روبیکا rate-limit نکنه و پیام drop نشه.
    """
    sent, failed = 0, 0
    batch_size = BROADCAST_BATCH_SIZE
    delay = BROADCAST_DELAY_SECONDS
    batch_pause = BROADCAST_BATCH_PAUSE_SECONDS

    for i, chat_id in enumerate(chat_ids):
        try:
            await send_fn(chat_id, text)
            sent += 1
        except Exception as e:
            failed += 1
            print(f"خطا در ارسال همگانی به {chat_id}: {e}")

        await asyncio.sleep(delay)
        if (i + 1) % batch_size == 0:
            await asyncio.sleep(batch_pause)

    return sent, failed


def normalize(text):
    return (text or "").strip()


# =============================================================================
# 🎭 اعمال «طعم» مود روی هر پیامی که ربات می‌فرسته (نه فقط سلام/خوبی)
# =============================================================================
def apply_mood_flavor(base_text, mood_name):
    import random as _random
    mood = MOODS.get(mood_name)
    if not mood:
        return base_text
    if _random.random() < MOOD_FLAVOR_CHANCE:
        flavor = _random.choice(mood.get("flavor", []))
        if flavor:
            return f"{base_text}\n\n{flavor}"
    return base_text


# =============================================================================
# 🎉 پارتی سکه/XP - وضعیت سراسری با انقضای خودکار
# =============================================================================
def is_party_active():
    expires = get_state("party_expires_ts")
    if not expires:
        return False
    return time.time() < float(expires)


def get_active_multiplier():
    if not is_party_active():
        return 1
    try:
        return float(get_state("party_multiplier", "1"))
    except Exception:
        return 1


# ==============================================================================
# بخش ۵: رپر توابع کتابخونه rubka
# ==============================================================================
bot_message_ids = set()


async def maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


async def safe_reply(bot, message, text, **kwargs):
    try:
        result = await maybe_await(message.reply(text, **kwargs))
    except Exception:
        print("=" * 40)
        print("❌ خطا در message.reply:")
        traceback.print_exc()
        try:
            result = await maybe_await(bot.send_message(message.chat_id, text, **kwargs))
        except Exception:
            print("❌ خطا در send_message هم:")
            traceback.print_exc()
            return None

    if isinstance(result, dict):
        mid = result.get("data", {}).get("message_id")
        if mid:
            bot_message_ids.add(mid)
    return result


async def try_remove_member(bot, chat_id, uid):
    for method_name in ("kick_chat_member", "ban_chat_member", "kick_member", "remove_member", "ban_member"):
        if hasattr(bot, method_name):
            try:
                await maybe_await(getattr(bot, method_name)(chat_id, uid))
                return True
            except Exception as e:
                print("خطا در حذف عضو:", e)
    return False


async def try_delete_message(bot, chat_id, message_id):
    if not message_id:
        return False
    for method_name in ("delete_message", "delete_messages", "remove_message"):
        if hasattr(bot, method_name):
            try:
                if method_name == "delete_messages":
                    await maybe_await(getattr(bot, method_name)(chat_id, [message_id]))
                else:
                    await maybe_await(getattr(bot, method_name)(chat_id, message_id))
                return True
            except Exception as e:
                print("خطا در حذف پیام:", e)
    return False


async def safe_send_photo(bot, chat_id, file_path, caption=""):
    """چون متد دقیق ارسال عکس تو rubka مستند نیست، چندتا اسم محتمل رو
    امتحان می‌کنیم. اگه هیچ‌کدوم جواب نداد، بجاش کپشن رو به‌صورت متن
    می‌فرستیم که حداقل بازی متوقف نشه."""
    for method_name in ("send_photo", "send_image", "send_file", "send_document"):
        if hasattr(bot, method_name):
            try:
                result = await maybe_await(getattr(bot, method_name)(chat_id, file_path, caption=caption))
                return result
            except TypeError:
                try:
                    result = await maybe_await(getattr(bot, method_name)(chat_id, file_path))
                    return result
                except Exception as e:
                    print(f"خطا در {method_name} (بدون caption):", e)
            except Exception as e:
                print(f"خطا در {method_name}:", e)
    # هیچ متدی جواب نداد؛ حداقل متن رو بفرست تا بازی از کار نیفته
    try:
        await maybe_await(bot.send_message(chat_id, caption or "🗺️ (نقشه تولید شد ولی ارسال عکس تو این نسخه‌ی rubka پشتیبانی نشد)"))
    except Exception as e:
        print("خطا در ارسال fallback متنی نقشه:", e)
    return None


def is_forwarded_message(message):
    try:
        for attr in ("is_forward", "forwarded", "forward_from"):
            if getattr(message, attr, None):
                return True
        raw = getattr(message, "raw_data", None)
        if isinstance(raw, dict):
            nm = raw.get("new_message", raw)
            if isinstance(nm, dict) and (nm.get("forwarded_from") or nm.get("is_forward")):
                return True
    except Exception:
        pass
    return False


# ==============================================================================
# بخش ۶: هندلرهای عمومی/گپ
# ==============================================================================
async def send_start(reply):
    await reply(f"""🌟═══════════════════════🌟
     👑 به دنیای «{BOT_BRAND}» خوش اومدی 👑
🌟═══════════════════════🌟

من {BOT_NAME_TRIGGER}‌ام؛ دستیار این گروه — بازی، اقتصاد و مدیریت گروه، همه زیر یک سقف.

📜 برای دیدن لیست کامل دستورات بنویس: قابلیت ها
🎭 برای عوض کردن لحنم بنویس: مود [اسم مود]
👑 برای شناخت سازنده بنویس: مالک ربات""")


async def send_help(reply):
    mood_list = "، ".join(MOOD_NAMES)
    await reply(f"""🫡 راهنمای کامل {BOT_BRAND} 👇 (برای بخش‌های بیشتر «قابلیت ها ۲» رو بزن)

━━━━━━━━━━━━━━━
💬 عمومی
━━━━━━━━━━━━━━━
سلام / خوبی / جوک / شانس / فال / تاس / چالش / معما
تاریخ | ساعت | مالک ربات | پیام به ادمین [متن]
مود [اسم] — گزینه‌ها: {mood_list}

━━━━━━━━━━━━━━━
👤 پروفایل و تنظیمات
━━━━━━━━━━━━━━━
پروفایل | آمار | دستاوردها
تنظیم اسم/ایموجی/لقب/اصل [متن]

━━━━━━━━━━━━━━━
💰 اقتصاد
━━━━━━━━━━━━━━━
جایزه روزانه | شکار | شرط [مبلغ] (ریسک بالا!)
فروشگاه | خرید [آیتم]
(ریپلای) هدیه سکه/ایکس پی [مقدار] | هدیه آیتم [اسم]

━━━━━━━━━━━━━━━
🎒 کلکسیون و اسپاون
━━━━━━━━━━━━━━━
هر ۳۰ دقیقه یه آیتم کمیاب (از Common تا OG یکتا) اسپون میشه!
وضعیت اسپاون | بگیرش (تو ۳۰ ثانیه اول)

━━━━━━━━━━━━━━━
🖤 بازار سیاه
━━━━━━━━━━━━━━━
بازار [فیلتر] | خرید بازار [شماره]
فروش [آیتم] [قیمت] | لغو آگهی [شماره]
مزایده [آیتم] [قیمت پایه] [دقیقه] (فقط Epic+) | پیشنهاد [شماره] [مبلغ]
تاریخچه معاملات

━━━━━━━━━━━━━━━
🎮 بازی‌ها
━━━━━━━━━━━━━━━
حدس عدد | دوز | مسابقه
⚽ پنالتی [چپ/وسط/راست] | فروشگاه پنالتی
🔫 رولت روسی [مبلغ] (شانس برد فقط ۱٪!)
🚔 فرار از زندان | 🎭 جرعت حقیقت | 🕵️ دروغ سنج
🗺️ شکار گنج | 🔐 گاوصندوق | 🚔 دزد و پلیس
⚔️ دوئل [مبلغ] (با ریپلای)

━━━━━━━━━━━━━━━
🎉 موقع پارتی («ادمین ابیوز»)
━━━━━━━━━━━━━━━
چرخ شانس | جعبه شانس | دوز غول | وضعیت پارتی
⚔️ حمله (نبرد رئیس) | وضعیت رئیس

━━━━━━━━━━━━━━━
🛡️ کلن/گیلد و لیدربورد
━━━━━━━━━━━━━━━
کلن بساز [اسم] | عضویت در کلن [اسم] | خروج از کلن | کلن‌ها | کلن من
رتبه (XP) | رتبه سکه | رتبه برد | رتبه آیتم

━━━━━━━━━━━━━━━
📂 گروه (فقط داخل گروه)
━━━━━━━━━━━━━━━
آمار گروه | لیست قفل‌ها | لیست ادمین‌ها | فعال
(ریپلای) ادمین/حذف کن/سکوت/رفع سکوت/انتقال مالکیت ربات
قفل [نوع] | باز کردن قفل [نوع]

━━━━━━━━━━━━━━━
👑 فقط ادمین اصلی ربات
━━━━━━━━━━━━━━━
همگانی [متن] | آمار کلی ربات
(ریپلای/آیدی) اضافه‌کردن سکه/ایکس‌پی [مقدار] | کم‌کردن سکه/ایکس‌پی [مقدار] | ریست کاربر
شروع سکه/ایکس‌پی پارتی [مقدار] [مدت] [ضریب] | شروع ادمین ابیوز [مدت] [ضریب] | توقف پارتی
(ریپلای) «ادمین ربات بشه [رمز]» — ادمین سکه می‌سازه (فقط سکه اضافه/کم می‌کنه)
اسپان کارت [کمیابی] — کومون/آنکامون/ریر/اپیک/لجندری/میتیک/گاد/سکرت/اوجی

📛 صدام کن با «{BOT_NAME_TRIGGER}» یا «وانتا» هرجای پیامت

🔗 گپ اصلی ربات: {GROUP_LINK}""")


async def handle_mood_set(reply, uid, text_raw):
    requested = text_raw.replace("مود", "", 1).strip()
    match = next((m for m in MOOD_NAMES if m == requested), None)
    if not match:
        options = "، ".join(MOOD_NAMES)
        await reply(f"مود «{requested}» رو نمی‌شناسم 😅\nگزینه‌ها: {options}")
        return
    update_user(uid, mood=match)
    greet = random.choice(MOODS[match]["greet"])
    await reply(f"🎭 مودت شد: {MOODS[match]['label']}\n\n{greet}")


def get_user_mood(u):
    return u.get("mood") or "کلاسیک"


async def handle_greeting(reply, u):
    mood = get_user_mood(u)
    await reply(random.choice(MOODS.get(mood, MOODS["کلاسیک"])["greet"]))


async def handle_how_are_you(reply, u):
    mood = get_user_mood(u)
    await reply(random.choice(MOODS.get(mood, MOODS["کلاسیک"])["how"]))


async def handle_date(reply):
    g = iran_now()
    await reply(f"📅 شمسی: {jalali_date_str()}\n📅 میلادی: {g.strftime('%Y-%m-%d')}")


async def handle_time(reply):
    g = iran_now()
    await reply(f"🕒 ساعت الان (تهران): {g.strftime('%H:%M:%S')}")


async def handle_dice(reply):
    await reply(f"🎲 عدد تاس: {random.randint(1, 6)}")


async def handle_luck(reply):
    percent = random.randint(1, 100)
    if percent > 80:
        msg = "امروز روز شانسته! 🍀"
    elif percent > 40:
        msg = "روز معمولیه، نه خوب نه بد."
    else:
        msg = "امروز یکم مراقب باش 😅"
    await reply(f"🎯 درصد شانس امروزت: {percent}٪\n{msg}")


FORTUNES = [
    "یه خبر خوب تو راهه 🌟", "صبور باش، نتیجه‌ی زحماتت داره میاد.",
    "یه فرصت جدید سر راهت قرار می‌گیره.", "امروز روز خوبیه برای تصمیم مهم.",
    "یکی به فکرته که فکرشو نمی‌کردی.",
]


async def handle_fortune(reply):
    await reply(f"🔮 فال امروز تو:\n«{random.choice(FORTUNES)}»")


async def handle_challenge(reply):
    await reply(f"🎯 چالش: {random.choice(CHALLENGES)}")


async def handle_joke(reply):
    await reply(f"😂 {random.choice(JOKES)}")


async def handle_contact_admin(reply, bot, uid, u, text_raw):
    """هر کاربری می‌تونه با این دستور مستقیم به ادمین اصلی ربات پیام بفرسته."""
    msg_text = text_raw.replace("پیام به ادمین", "", 1).replace("پیام به مالک", "", 1).strip()
    if not msg_text:
        await reply("بنویس: «پیام به ادمین [متنت]» تا مستقیم برای سازنده‌ی ربات بفرسته.")
        return

    sender_name = u.get("display_name") or f"کاربر {str(uid)[-6:]}"
    forward_text = f"📩 پیام جدید از یه کاربر:\n👤 {sender_name} (آیدی: {uid})\n\n{msg_text}"

    sent_to_anyone = False
    for admin_uid in ADMIN_IDS:
        admin_user = get_user(admin_uid, create_if_missing=False)
        if admin_user and admin_user.get("pv_chat_id"):
            try:
                await maybe_await(bot.send_message(admin_user["pv_chat_id"], forward_text))
                sent_to_anyone = True
            except Exception as e:
                print("خطا در ارسال پیام به ادمین:", e)

    if sent_to_anyone:
        await reply("✅ پیامت برای ادمین ربات ارسال شد. منتظر جواب باش 🙏")
    else:
        await reply("❌ فعلاً هیچ ادمینی پیوی ربات رو استارت نزده که پیامت بهش برسه.")


# --- گپ آزاد (سلام/خوبی/چخبر/عه/نه/آره/جالبه و کلی مورد دیگه) ---
def try_smalltalk(text):
    """اگه متن دقیقا یا شامل یکی از کلیدهای گپ آزاد باشه، یه جواب رندوم
    برمی‌گردونه؛ وگرنه None (تا روتر بره سراغ چک‌های بعدی)."""
    stripped = text.strip()
    if stripped in SMALLTALK_EXACT:
        return random.choice(SMALLTALK_EXACT[stripped])
    for key, answers in SMALLTALK_CONTAINS.items():
        if key in stripped:
            return random.choice(answers)
    return None


def try_name_call(text):
    """اگه یکی از اسم‌های ربات (پرسیا/وانتا) تو متن باشه، یه جواب توجه‌جلب‌کن
    برمی‌گردونه."""
    for trigger in NAME_CALL_TRIGGERS:
        if trigger in text:
            return random.choice(NAME_CALL_RESPONSES)
    return None


# --- ماشین‌حساب ساده ---
def try_calculate(text):
    t = text.replace("ضرب در", "*").replace("تقسیم بر", "/").replace("منهای", "-").replace("بعلاوه", "+")
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*([+\-*/])\s*(-?\d+(?:\.\d+)?)\s*$", t)
    if not m:
        return None
    a, op, b = float(m.group(1)), m.group(2), float(m.group(3))
    try:
        if op == "+":
            result = a + b
        elif op == "-":
            result = a - b
        elif op == "*":
            result = a * b
        elif op == "/":
            if b == 0:
                return "❌ تقسیم بر صفر که نمیشه 😅"
            result = a / b
        else:
            return None
        result = int(result) if result == int(result) else round(result, 4)
        return f"🧮 نتیجه: {result}"
    except Exception:
        return None


# ==============================================================================
# بخش ۷: هندلرهای پروفایل و آمار
# ==============================================================================
def _is_bot_owner(uid):
    return str(uid) in {str(a) for a in ADMIN_IDS}


OWNER_LOADOUT_MIN_PER_CATEGORY = 10


def ensure_owner_loadout(uid):
    """مالک ربات همیشه باید پروفایل خفنی داشته باشه - اگه هنوز آیتم/سکه/لول
    کافی نداره، خودکار پرش می‌کنیم (idempotent: اگه از قبل پر باشه کاری
    نمی‌کنه)."""
    u = get_user(uid)
    if u["coins"] < 1_000_000:
        update_user(uid, coins=1_000_000)
    if u["xp"] < 50_000:
        update_user(uid, xp=50_000)

    shop_owned = get_items(uid, "shop")
    if len(shop_owned) < OWNER_LOADOUT_MIN_PER_CATEGORY:
        for item in list(SHOP_ITEMS.keys())[:OWNER_LOADOUT_MIN_PER_CATEGORY + 5]:
            add_item(uid, "shop", item, 1)

    creatures_owned = get_items(uid, "creature")
    if len(creatures_owned) < OWNER_LOADOUT_MIN_PER_CATEGORY:
        for creature in CREATURES:
            add_item(uid, "creature", creature, 3)

    penalty_owned = get_items(uid, "penalty")
    if len(penalty_owned) < len(PENALTY_SHOP):
        for item in PENALTY_SHOP.keys():
            add_item(uid, "penalty", item, 1)

    catalog_owned = get_items(uid, "catalog")
    if len(catalog_owned) < len(RARITY_INFO) - 1:  # OG جدا حساب میشه
        by_rarity = {}
        for item in ITEM_CATALOG:
            by_rarity.setdefault(item["rarity"], []).append(item)
        for rarity, items in by_rarity.items():
            for item in items[:3]:  # ۳ تا از هر کمیابی
                add_item(uid, "catalog", item["name"], 1)


def display_name(u, fallback_uid):
    return u.get("display_name") or f"کاربر {fallback_uid[-6:]}"


def display_emoji(u):
    return u.get("custom_emoji") or "🙂"


async def handle_set_name(reply, uid, text_raw):
    val = text_raw.replace("تنظیم اسم", "", 1).strip()
    if not val:
        await reply("بنویس: «تنظیم اسم [نام]»")
        return
    update_user(uid, display_name=val[:30])
    await reply(f"✅ اسمت تنظیم شد: {val[:30]}")


async def handle_set_emoji(reply, uid, text_raw):
    val = text_raw.replace("تنظیم ایموجی", "", 1).strip()
    if not val:
        await reply("بنویس: «تنظیم ایموجی 🐉» (فقط یه شکلک بفرست)")
        return
    update_user(uid, custom_emoji=val[:8])
    await reply(f"✅ ایموجی‌ت تنظیم شد: {val[:8]}")


async def handle_set_title(reply, uid, text_raw):
    val = text_raw.replace("تنظیم لقب", "", 1).strip()
    if not val:
        await reply("بنویس: «تنظیم لقب [متن]»")
        return
    update_user(uid, title=val[:30])
    await reply(f"✅ لقبت تنظیم شد: {val[:30]}")


async def handle_set_origin(reply, uid, text_raw):
    val = text_raw.replace("تنظیم اصل", "", 1).strip()
    if not val:
        await reply("بنویس: «تنظیم اصل [متن]»")
        return
    update_user(uid, origin=val[:30])
    await reply(f"✅ اصلیتت تنظیم شد: {val[:30]}")


async def handle_profile(reply, uid, u):
    if _is_bot_owner(uid):
        ensure_owner_loadout(uid)
        u = get_user(uid)  # رفرش بعد از پرکردن

    name = display_name(u, uid)
    emoji = display_emoji(u)
    items = get_items(uid, "shop")
    creatures = get_items(uid, "creature")
    penalty_items = get_items(uid, "penalty")
    catalog_items = get_items(uid, "catalog")
    og_items = get_items(uid, "og")

    items_txt = "، ".join(f"{i['item_name']}×{i['qty']}" for i in items) or "چیزی نداری"
    creatures_txt = "، ".join(f"{i['item_name']}×{i['qty']}" for i in creatures) or "چیزی نداری"
    penalty_txt = "، ".join(f"{i['item_name']}×{i['qty']}" for i in penalty_items) or "چیزی نداری"

    def _with_rarity(i):
        cat_item = ITEM_CATALOG_BY_NAME.get(i["item_name"])
        rarity = f" [{cat_item['rarity']}]" if cat_item else ""
        return f"{i['item_name']}{rarity}×{i['qty']}"

    catalog_txt = "، ".join(_with_rarity(i) for i in catalog_items) or "هنوز کارتی نداری (از اسپاون یا بازار بگیر)"
    og_txt = "، ".join(f"{i['item_name']}×{i['qty']}" for i in og_items) or "نداری (فقط یکی تو کل بازیه!)"

    await reply(f"""👤 پروفایل تو {emoji}

📛 اسم: {name}
⭐ سطح: {get_level(u['xp'])}
✨ XP: {u['xp']}
💰 سکه: {u['coins']:,}
🏅 مدال: {get_badge(u['xp'])}
🎮 بازی‌ها: {u['games']}  |  ✅ بردها: {u['wins']}
💬 پیام‌ها: {u['messages']}
🔥 استریک روزانه: {u['streak']}

🎒 آیتم‌ها: {items_txt}
⚽ آیتم‌های پنالتی: {penalty_txt}
🐉 موجودات: {creatures_txt}
🗃️ کارت‌های کلکسیون: {catalog_txt}
🌟 آیتم OG: {og_txt}

💡 برای شخصی‌سازی: «تنظیم اسم [نام]» یا «تنظیم ایموجی [شکلک]»""")


async def handle_personal_stats(reply, chat_id, uid, u, is_group_admin_fn, is_owner_fn):
    name = display_name(u, uid)
    emoji = display_emoji(u)
    title = u.get("title") or "بدون لقب"
    origin = u.get("origin") or "بدون اصل"
    if is_owner_fn(chat_id, uid):
        rank_label = "مالک 👑"
    elif is_group_admin_fn(chat_id, uid):
        rank_label = "ادمین گروه 🛡️"
    else:
        rank_label = "عضو"
    warn_count = get_warn_count(chat_id, uid)

    await reply(f"""╔════════✦ آمار شخصی ✦════════╗
     {emoji}  {name}
╚═════════════════════════╝

🏷 لقب: {title}
🌍 اصل: {origin}
👑 مقام در این گروه: {rank_label}

💬 کل پیام‌ها: {u['messages']}
⚠️ اخطارها در این گروه: {warn_count}
📅 {jalali_date_str()}

💡 برای سطح/XP/بازی‌ها بنویس «پروفایل»""")


async def handle_leaderboard(reply):
    top = top_users_by_xp(10)
    if not top:
        await reply("هنوز کسی رتبه‌ای کسب نکرده.")
        return
    lines = ["🥇 جدول برترین‌های VANTA 🥇\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        name = display_name(u, u["uid"])
        lines.append(f"{medal} {name} — سطح {get_level(u['xp'])} | {u['xp']} XP | {u['coins']:,} سکه")
    await reply("\n".join(lines))


# ==============================================================================
# بخش ۸: هندلرهای اقتصاد
# ==============================================================================
DAILY_BASE_REWARD = 150
DAILY_STREAK_BONUS = 30  # به ازای هر روز استریک، این مقدار بیشتر می‌گیره
DAILY_MAX_STREAK_BONUS_DAYS = 20


async def handle_daily_reward(reply, uid, u):
    today = date.today().isoformat()
    last = u.get("last_daily_date") or ""

    if last == today:
        await reply("🎁 امروز جایزه‌تو گرفتی، فردا دوباره بیا!")
        return

    # اگه دقیقا دیروز گرفته بوده، استریک ادامه پیدا می‌کنه؛ وگرنه ریست میشه
    yesterday = (date.today().fromordinal(date.today().toordinal() - 1)).isoformat()
    new_streak = (u.get("streak") or 0) + 1 if last == yesterday else 1

    bonus_days = min(new_streak, DAILY_MAX_STREAK_BONUS_DAYS)
    multiplier = get_active_multiplier()
    reward = int((DAILY_BASE_REWARD + bonus_days * DAILY_STREAK_BONUS) * multiplier)

    add_coins(uid, reward)
    update_user(uid, last_daily_date=today, streak=new_streak)

    bonus_txt = f" (×{multiplier:g} پارتی!)" if multiplier > 1 else ""
    await reply(f"""🎁 جایزه‌ی روزانه گرفتی!

💰 مقدار: {reward:,} سکه{bonus_txt}
🔥 استریک فعلی: {new_streak} روز
{"🌟 هرچی استریکت بیشتر بشه سکه‌ی بیشتری می‌گیری (تا ۲۰ روز)" if new_streak < DAILY_MAX_STREAK_BONUS_DAYS else "🏆 به سقف بونوس استریک رسیدی!"}""")


async def handle_shop(reply):
    lines = ["🏪 فروشگاه VANTA 🏪\n"]
    for item, price in SHOP_ITEMS.items():
        lines.append(f"• {item} — {price:,} سکه")
    lines.append("\n💡 برای خرید بنویس: «خرید [اسم آیتم]»")
    await reply("\n".join(lines))


async def handle_buy(reply, uid, u, text_raw):
    item_name = text_raw.replace("خرید", "", 1).strip()
    match = next((name for name in SHOP_ITEMS if name == item_name), None)
    if not match:
        # جستجوی نزدیک (اگه دقیق ننوشته بود)
        match = next((name for name in SHOP_ITEMS if item_name and item_name in name), None)
    if not match:
        await reply("همچین آیتمی تو فروشگاه نیست. بنویس «فروشگاه» برای دیدن لیست کامل.")
        return

    price = SHOP_ITEMS[match]
    if u["coins"] < price:
        need = price - u["coins"]
        await reply(f"❌ سکه‌ت کافی نیست. {need:,} سکه‌ی دیگه لازم داری.")
        return

    remove_coins(uid, price)
    add_item(uid, "shop", match, 1)
    await reply(f"✅ «{match}» رو با {price:,} سکه خریدی! برو تو پروفایلت ببینش.")


HUNT_SUCCESS_CHANCE = 0.30  # قبلا ۰.۴۵ بود؛ حالا سخت‌تره


async def handle_hunt(reply, uid, u):
    count = increment_hunt_count(uid)
    multiplier = get_active_multiplier()

    # هر شکار صدم، همیشه شکار بده - صرف‌نظر از شانس
    if count % 100 == 0:
        loss = random.randint(60, 180)
        remove_coins(uid, loss)
        await reply(f"""🐗💥 شکار بد! یه موجود وحشی بهت حمله کرد و فرار کردی.
💸 {loss:,} سکه از تجهیزاتت آسیب دید و از دست رفت.
(این شکار، شکار صدمت بود؛ هر ۱۰۰ شکار یه‌بار این خطر هست)""")
        return

    roll = random.random()
    if roll > HUNT_SUCCESS_CHANCE:
        await reply("🌲 امروز شکاری پیدا نکردی، بازم امتحان کن. (شکار سخته!)")
        return

    creature = random.choice(CREATURES)
    coins_reward = int(random.randint(40, 150) * multiplier)
    add_item(uid, "creature", creature, 1)
    add_coins(uid, coins_reward)
    bonus_txt = f" (×{multiplier:g} پارتی!)" if multiplier > 1 else ""
    await reply(f"🏹 یه {creature} شکار کردی!\n💰 پاداش: {coins_reward} سکه{bonus_txt}")


# =============================================================================
# 🎲 شرط - ریسک خیلی بالا، فقط برای کسایی که هیجان می‌خوان
# =============================================================================
BET_WIN_CHANCE = 0.22   # شانس برد خیلی پایینه، عمداً
BET_LOSS_MULTIPLIER = 2  # اگه ببازی، ۲ برابر مبلغ شرط رو از دست می‌دی


async def handle_bet(reply, uid, u, text_raw):
    amount_str = "".join(ch for ch in text_raw if ch.isdigit())
    if not amount_str:
        await reply("بنویس: «شرط [مبلغ]» — مثلا: شرط 100\n⚠️ خیلی سخته و ریسکش بالاست!")
        return

    amount = int(amount_str)
    if amount <= 0:
        await reply("مبلغ شرط باید بزرگتر از صفر باشه.")
        return

    loss_amount = amount * BET_LOSS_MULTIPLIER
    if u["coins"] < loss_amount:
        await reply(f"❌ برای این شرط، حداقل {loss_amount:,} سکه لازم داری (چون اگه ببازی {BET_LOSS_MULTIPLIER}× مبلغ رو از دست می‌دی).")
        return

    if random.random() < BET_WIN_CHANCE:
        add_coins(uid, amount)
        await reply(f"🎲✨ شانس آوردی! بردی!\n💰 +{amount:,} سکه")
    else:
        remove_coins(uid, loss_amount)
        await reply(f"🎲💀 باختی! این شرط خیلی سخت بود.\n💸 -{loss_amount:,} سکه ({BET_LOSS_MULTIPLIER}× مبلغ شرط)")


# ==============================================================================
# بخش ۹: هندلرهای مدیریت گروه و پارتی
# ==============================================================================
def is_bot_admin(uid):
    return str(uid) in {str(a) for a in ADMIN_IDS}


def is_owner(chat_id, uid):
    g = get_group(chat_id)
    return bool(g and str(g["owner_uid"]) == str(uid))


def can_manage_group(chat_id, uid):
    return is_bot_admin(uid) or is_owner(chat_id, uid) or is_group_admin(chat_id, uid)


async def handle_transfer_ownership(reply, chat_id, uid, reply_sender_uid):
    """مالک فعلی گروه (یا ادمین اصلی ربات) می‌تونه مالکیت گروه رو با
    ریپلای به یه نفر، به اون منتقل کنه."""
    if not (is_owner(chat_id, uid) or is_bot_admin(uid)):
        await reply("⛔ فقط مالک فعلی گروه می‌تونه مالکیت رو منتقل کنه.")
        return
    if not reply_sender_uid:
        await reply("❌ باید روی پیام کسی که می‌خوای مالک بشه ریپلای بزنی.")
        return
    if not is_group_registered(chat_id):
        await reply("❌ این گروه هنوز ثبت نشده. اول بنویس «فعال».")
        return
    register_group(chat_id, owner_uid=reply_sender_uid)
    await reply(f"👑 مالکیت این گروه به آیدی {reply_sender_uid} منتقل شد.")


# =============================================================================
# 🎉 سکه‌پارتی / ایکس‌پی‌پارتی - فقط ادمین اصلی ربات می‌تونه فعالش کنه
# =============================================================================
# =============================================================================
# 🎉 سکه‌پارتی / ایکس‌پی‌پارتی - فقط ادمین اصلی ربات می‌تونه فعالش کنه
# =============================================================================
PARTY_DEFAULT_DURATION_MINUTES = 15
PARTY_DEFAULT_MULTIPLIER = 3
PARTY_UPDATE_INTERVAL_SECONDS = 180  # هر ۳ دقیقه یه آپدیت وضعیت (نه هر ثانیه!)

_party_background_tasks = set()


async def _party_broadcast_all(bot, text):
    targets = [u["pv_chat_id"] for u in all_users() if u.get("pv_chat_id")]
    if not targets:
        return 0, 0

    async def send_fn(chat_id, msg):
        await maybe_await(bot.send_message(chat_id, msg))

    return await rate_limited_broadcast(send_fn, targets, text)


async def _run_party_background(bot, duration_minutes, multiplier):
    total_seconds = duration_minutes * 60

    # پیام‌های هیجانی شروع پارتی
    for msg in random.sample(PARTY_HYPE_MESSAGES, min(3, len(PARTY_HYPE_MESSAGES))):
        await _party_broadcast_all(bot, msg)
        await asyncio.sleep(4)

    await _party_broadcast_all(bot, random.choice(PARTY_BEAT_DROP))
    await asyncio.sleep(3)

    boss_name = get_state("boss_name", "یه هیولای وحشتناک 👹")
    await _party_broadcast_all(
        bot,
        f"👹 یه رئیس ظاهر شد: {boss_name}\nهمه بریزید تو پیوی ربات و بنویسید «حمله» تا شکستش بدیم!"
    )

    await _party_broadcast_all(
        bot,
        f"🎮 بازیای ویژه‌ی پارتی:\n"
        f"🎡 «چرخ شانس» | 📦 «جعبه شانس» | 👑 «دوز غول» | ⚔️ «حمله» (نبرد رئیس)\n"
        f"همشون فقط الان فعالن و جایزه‌ی خیلی بیشتری می‌دن!"
    )

    elapsed = 0
    while elapsed < total_seconds:
        wait = min(PARTY_UPDATE_INTERVAL_SECONDS, total_seconds - elapsed)
        await asyncio.sleep(wait)
        elapsed += wait
        remaining_min = max(0, round((total_seconds - elapsed) / 60))
        if remaining_min > 0:
            dance = random.choice(PARTY_DANCE_EMOJIS)
            hype = random.choice(PARTY_HYPE_MESSAGES)
            extra = random.choice([random.choice(PARTY_BEAT_DROP), "⚔️ رئیس هنوز منتظرته، برو بهش حمله کن!"])
            await _party_broadcast_all(
                bot,
                f"{dance} {hype}\n"
                f"⚡ سکه/XP همچنان ×{multiplier:g}ـه! ⏳ حدود {remaining_min} دقیقه‌ی دیگه مونده.\n\n{extra}"
            )

    set_state("party_expires_ts", "0")
    set_state("boss_hp", "0")
    await _party_broadcast_all(bot, "🥳 پارتی تموم شد! ممنون که بودید، منتظر پارتی بعدی باشید 💜")


async def handle_party_status(reply):
    if not is_party_active():
        await reply("😴 الان پارتی فعال نیست. منتظر «شروع ادمین ابیوز» از طرف ادمین باش!")
        return
    expires = float(get_state("party_expires_ts", "0"))
    remaining_min = max(0, round((expires - time.time()) / 60))
    multiplier = get_active_multiplier()
    boss_name = get_state("boss_name", "-")
    boss_hp = get_state("boss_hp", "0")
    await reply(f"🎉 پارتی فعاله!\n⚡ ضریب سکه/XP: ×{multiplier:g}\n⏳ حدود {remaining_min} دقیقه مونده\n"
                f"👹 رئیس فعلی: {boss_name} ({boss_hp} HP)\n\n"
                f"🎮 بازیای ویژه: چرخ شانس | جعبه شانس | دوز غول | حمله (نبرد رئیس)")


def _parse_party_numbers(text_raw):
    """همه‌ی عددهای تو متن دستور رو به ترتیب برمی‌گردونه، برای پارس کردن
    مقدار/مدت/ضریب."""
    return [int(n) for n in re.findall(r"\d+", text_raw)]


async def handle_start_coin_party(reply, bot, uid, text_raw):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    nums = _parse_party_numbers(text_raw)
    if not nums:
        await reply("بنویس: «شروع سکه پارتی [مقدار] [مدت‌به‌دقیقه] [ضریب]»\n"
                     "مدت و ضریب اختیاری‌ان، مثلا: شروع سکه پارتی 1 2 3 → ۱ سکه بده، ۲ دقیقه، ×۳")
        return
    amount = nums[0]
    duration_min = nums[1] if len(nums) >= 2 else PARTY_DEFAULT_DURATION_MINUTES
    multiplier = nums[2] if len(nums) >= 3 else PARTY_DEFAULT_MULTIPLIER

    targets = [u for u in all_users() if u.get("pv_chat_id")]
    for u in targets:
        add_coins(u["uid"], amount)

    _activate_party_state(duration_min, multiplier)
    await reply(f"🎉 سکه‌پارتی شروع شد! {amount:,} سکه به {len(targets):,} نفر داده شد.\n"
                f"⚡ سکه/XP بازی‌ها تا {duration_min} دقیقه ×{multiplier:g} شد.")

    _launch_party_task(bot, duration_min, multiplier)
    bonus_msg = f"🎁 ادمین برات {amount:,} سکه هدیه فرستاد + الان سکه/XP بازیا ×{multiplier:g}ـه! بیا بازی کن 🔥"
    await _party_broadcast_all(bot, bonus_msg)


async def handle_start_xp_party(reply, bot, uid, text_raw):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    nums = _parse_party_numbers(text_raw)
    if not nums:
        await reply("بنویس: «شروع ایکس پی پارتی [مقدار] [مدت‌به‌دقیقه] [ضریب]»")
        return
    amount = nums[0]
    duration_min = nums[1] if len(nums) >= 2 else PARTY_DEFAULT_DURATION_MINUTES
    multiplier = nums[2] if len(nums) >= 3 else PARTY_DEFAULT_MULTIPLIER

    targets = [u for u in all_users() if u.get("pv_chat_id")]
    for u in targets:
        add_xp(u["uid"], amount)

    _activate_party_state(duration_min, multiplier)
    await reply(f"🎉 ایکس‌پی‌پارتی شروع شد! {amount:,} XP به {len(targets):,} نفر داده شد.\n"
                f"⚡ سکه/XP بازی‌ها تا {duration_min} دقیقه ×{multiplier:g} شد.")

    _launch_party_task(bot, duration_min, multiplier)
    bonus_msg = f"🎁 ادمین برات {amount:,} XP هدیه فرستاد + الان سکه/XP بازیا ×{multiplier:g}ـه! بیا بازی کن 🔥"
    await _party_broadcast_all(bot, bonus_msg)


async def handle_start_admin_abuse(reply, bot, uid, text_raw):
    """دقیقا چیزی که خواستی: بدون دادن سکه/XP به کسی، فقط جشن + ضریب
    موقت رو راه میندازه. پیش‌فرض: ۲ دقیقه، ×۲ (دوبرابر) — هردوشون رو
    می‌تونی خودت عوض کنی.
    مثال: «شروع ادمین ابیوز» → ۲ دقیقه ×۲
          «شروع ادمین ابیوز 5 3» → ۵ دقیقه ×۳"""
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    nums = _parse_party_numbers(text_raw)
    duration_min = nums[0] if len(nums) >= 1 else 2
    multiplier = nums[1] if len(nums) >= 2 else 2

    _activate_party_state(duration_min, multiplier)
    await reply(f"😈 ادمین ابیوز شروع شد! سکه/XP بازیا تا {duration_min} دقیقه ×{multiplier:g} شد.")
    _launch_party_task(bot, duration_min, multiplier)


def _activate_party_state(duration_min, multiplier):
    expires_at = time.time() + duration_min * 60
    set_state("party_expires_ts", expires_at)
    set_state("party_multiplier", multiplier)
    _spawn_party_boss(multiplier)


def _spawn_party_boss(multiplier):
    """رئیس اول پارتی رو می‌سازه؛ باقی رئیس‌ها خودکار بعد از شکست قبلی‌
    تو py ساخته می‌شن."""
    name = random.choice(BOSS_NAMES)
    hp = int(BOSS_HP_BASE * max(1, multiplier * 0.6))
    set_state("boss_name", name)
    set_state("boss_hp", hp)
    set_state("boss_max_hp", hp)
    return name, hp


def _launch_party_task(bot, duration_min, multiplier):
    task = asyncio.create_task(_run_party_background(bot, duration_min, multiplier))
    _party_background_tasks.add(task)
    task.add_done_callback(_party_background_tasks.discard)


async def handle_stop_party(reply, uid):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    set_state("party_expires_ts", "0")
    await reply("🛑 پارتی همین الان تموم شد.")


# =============================================================================
# 🪙 ادمین سکه - دسترسی محدود، فقط با رمز و فقط از طرف ادمین اصلی
# =============================================================================
async def handle_grant_coin_admin(reply, uid, text_raw, reply_sender_uid):
    if not is_bot_admin(uid):
        await reply("⛔ فقط ادمین اصلی ربات می‌تونه این کارو بکنه.")
        return
    if not reply_sender_uid:
        await reply("❌ باید روی پیام کسی که می‌خوای ادمین سکه بشه ریپلای بزنی.")
        return
    if COIN_ADMIN_PASSWORD not in text_raw:
        await reply("❌ رمز اشتباهه یا ننوشتیش. بنویس: (ریپلای) «ادمین ربات بشه [رمز]»")
        return

    grant_coin_admin(reply_sender_uid, uid)
    await reply(f"✅ آیدی {reply_sender_uid} الان «ادمین سکه» شد — فقط می‌تونه سکه اضافه/کم کنه، هیچ قدرت دیگه‌ای نداره.")


async def handle_revoke_coin_admin(reply, uid, reply_sender_uid):
    if not is_bot_admin(uid):
        await reply("⛔ فقط ادمین اصلی ربات می‌تونه این کارو بکنه.")
        return
    if not reply_sender_uid:
        await reply("❌ باید روی پیام کسی که می‌خوای ازش گرفته بشه ریپلای بزنی.")
        return
    revoke_coin_admin(reply_sender_uid)
    await reply(f"✅ دسترسی ادمین سکه از آیدی {reply_sender_uid} گرفته شد.")


# =============================================================================
# 🃏 اسپان دستی کارت - فقط ادمین اصلی، می‌تونه هر کمیابی‌ای بخواد بگیره
# =============================================================================
async def handle_admin_spawn_card(reply, uid, text_raw):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return


    rest = text_raw.replace("اسپان کارت", "", 1).replace("اسپون کارت", "", 1).strip()
    rarity_key = RARITY_PERSIAN_MAP.get(rest)
    if not rarity_key:
        options = "، ".join(RARITY_PERSIAN_MAP.keys())
        await reply(f"بنویس: «اسپان کارت [کمیابی]»\nگزینه‌ها: {options}")
        return

    if rarity_key == "OG":
        if get_state("og_claimed", "0") == "1":
            await reply("❌ آیتم OG قبلاً توسط یکی گرفته شده، دیگه هیچ‌وقت در دسترس نیست.")
            return
        add_item(uid, "og", OG_ITEM["name"], 1)
        add_xp(uid, OG_ITEM["xp"])
        set_state("og_claimed", "1")
        await reply(f"🌟👑 آیتم یکتای OG رو برای خودت گرفتی: {OG_ITEM['name']}!\nدیگه هیچ‌وقت این آیتم اسپون نمیشه.")
        return

    candidates = [item for item in ITEM_CATALOG if item["rarity"] == rarity_key]
    if not candidates:
        await reply("❌ آیتمی با این کمیابی پیدا نشد.")
        return

    item = random.choice(candidates)
    add_item(uid, "catalog", item["name"], 1)
    add_xp(uid, item["xp"])
    await reply(f"🃏 گرفتیش: {item['name']} ({rarity_key})\n💎 ارزش: {item['price']:,} سکه | ✨ +{item['xp']} XP")


# =============================================================================
# ثبت گروه
# =============================================================================
async def handle_register_group(reply, chat_id, uid, title=""):
    if is_group_registered(chat_id):
        await reply("✅ این گروه از قبل ثبت شده.")
        return
    register_group(chat_id, owner_uid=uid, title=title)
    await reply(f"""✅ گروه با موفقیت ثبت شد!
👑 مالک ثبت‌شده نزد ربات: {uid}

قفل‌های پیش‌فرض (لینک، اسپم، منشن، فوروارد، الفاظ نامناسب) فعال شدن.
برای دیدنشون بنویس: «لیست قفل‌ها»""")


# =============================================================================
# آمار گروه — نکته‌ی مهم: این handler باید قبل از «آمار شخصی» چک بشه چون
# رشته‌ی «آمار گروه» شامل «آمار» هم هست. تو main.py هم به همین ترتیب مرتبیم.
# =============================================================================
async def handle_group_stats(reply, chat_id):
    members_count = count_group_members(chat_id)
    g = get_group(chat_id)
    total_msgs = g["total_messages"] if g else 0
    is_registered = is_group_registered(chat_id)
    owner_uid = g["owner_uid"] if g else "-"
    admins_count = count_group_admins(chat_id)

    await reply(f"""📊 آمار کلی این گروه

👥 تعداد اعضای فعال (پیام‌داده‌ها): {members_count}
💬 کل پیام‌های ثبت‌شده: {total_msgs:,}
🛡️ تعداد ادمین‌های گروه: {admins_count}
✅ وضعیت ثبت: {"ثبت شده" if is_registered else "ثبت نشده (بنویس «فعال»)"}
👑 مالک (نزد ربات): {owner_uid}""")


async def handle_list_locks(reply, chat_id):
    locks = get_locks(chat_id)
    lines = ["🔒 وضعیت قفل‌های این گروه:\n"]
    for name, enabled in locks.items():
        icon = LOCK_ICONS.get(name, "🔸")
        label = LOCK_LABELS.get(name, name)
        status = "فعال ✅" if enabled else "غیرفعال ❌"
        lines.append(f"{icon} {label}: {status}")
    await reply("\n".join(lines))


async def handle_list_admins(reply, chat_id):
    admins = list_group_admins(chat_id)
    if not admins:
        await reply("هنوز هیچ ادمینی (توسط ربات) ثبت نشده.")
        return
    lines = ["🛡️ ادمین‌های این گروه (نزد ربات):\n"]
    for a in admins:
        lines.append(f"• {a}")
    await reply("\n".join(lines))


LOCK_KEY_BY_PERSIAN = {
    "لینک": "link", "اسپم": "spam", "الفاظ نامناسب": "badword",
    "منشن زیاد": "mention", "فوروارد": "forward",
}


def _match_lock_key(text_fragment):
    for fa, key in LOCK_KEY_BY_PERSIAN.items():
        if fa in text_fragment:
            return key, fa
    return None, None


async def handle_lock_toggle(reply, chat_id, uid, text_raw, enable):
    if not can_manage_group(chat_id, uid):
        await reply("⛔ فقط ادمین‌های گروه یا مالک می‌تونن قفل‌ها رو تغییر بدن.")
        return
    fragment = text_raw.replace("باز کردن قفل", "").replace("قفل", "").strip()
    key, fa_label = _match_lock_key(fragment)
    if not key:
        options = "، ".join(LOCK_KEY_BY_PERSIAN.keys())
        await reply(f"نوع قفل رو نشناختم. گزینه‌ها: {options}")
        return
    set_lock(chat_id, key, enable)
    icon = LOCK_ICONS.get(key, "🔒")
    state = "فعال" if enable else "غیرفعال"
    await reply(f"{icon} قفل {fa_label} {state} شد.")


# =============================================================================
# ادمین/حذف/سکوت گروه (با ریپلای)
# =============================================================================
async def handle_make_admin(reply, chat_id, uid, reply_sender_uid):
    if not can_manage_group(chat_id, uid):
        await reply("⛔ فقط مالک یا ادمین اصلی ربات می‌تونه ادمین جدید بسازه.")
        return
    if not reply_sender_uid:
        await reply("❌ باید روی پیام یه نفر ریپلای بزنی.")
        return
    add_group_admin(chat_id, reply_sender_uid)
    await reply(f"✅ کاربر {reply_sender_uid} ادمین این گروه شد.")


async def handle_remove_member(reply, bot, message, chat_id, uid, reply_sender_uid):
    if not can_manage_group(chat_id, uid):
        await reply("⛔ فقط ادمین‌ها می‌تونن این کارو بکنن.")
        return
    if not reply_sender_uid:
        await reply("❌ نتونستم بفهمم روی پیام کی ریپلای زدی.")
        return
    removed = await try_remove_member(bot, chat_id, reply_sender_uid)
    if removed:
        await reply(f"🚫 کاربر {reply_sender_uid} از گروه حذف شد.")
    else:
        await reply("🚫 کتابخونه rubka فعلا متد رسمی حذف عضو رو پشتیبانی نمی‌کنه.")


async def handle_mute(reply, chat_id, uid, reply_sender_uid):
    if not can_manage_group(chat_id, uid):
        await reply("⛔ فقط ادمین‌ها می‌تونن سکوت بدن.")
        return
    if not reply_sender_uid:
        await reply("❌ باید روی پیام یه نفر ریپلای بزنی.")
        return
    mute_user(chat_id, reply_sender_uid)
    await reply(f"🔇 کاربر {reply_sender_uid} ساکت شد.")


async def handle_unmute(reply, chat_id, uid, reply_sender_uid):
    if not can_manage_group(chat_id, uid):
        await reply("⛔ فقط ادمین‌ها می‌تونن رفع سکوت کنن.")
        return
    if not reply_sender_uid:
        await reply("❌ باید روی پیام یه نفر ریپلای بزنی.")
        return
    unmute_user(chat_id, reply_sender_uid)
    await reply(f"🔊 کاربر {reply_sender_uid} رفع سکوت شد.")


# =============================================================================
# فقط ادمین اصلی ربات: سکه دادن با ریپلای یا آیدی مستقیم (رفع باگ اصلی)
# =============================================================================
async def handle_add_coins(reply, uid, text_raw, reply_sender_uid):
    if not (is_bot_admin(uid) or is_coin_admin(uid)):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات یا ادمین سکه‌ست.")
        return
    rest = text_raw.replace("اضافه کردن سکه", "", 1).strip()
    target_uid = resolve_target_uid(rest, reply_sender_uid)
    amount = extract_amount(rest)
    if not target_uid or not amount:
        await reply("بنویس: (ریپلای) «اضافه کردن سکه [مقدار]» یا بدون ریپلای «اضافه کردن سکه [مقدار] [آیدی]»")
        return
    add_coins(target_uid, amount)
    await reply(f"✅ {amount:,} سکه به آیدی {target_uid} اضافه شد.")


async def handle_remove_coins(reply, uid, text_raw, reply_sender_uid):
    if not (is_bot_admin(uid) or is_coin_admin(uid)):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات یا ادمین سکه‌ست.")
        return
    rest = text_raw.replace("کم کردن سکه", "", 1).strip()
    target_uid = resolve_target_uid(rest, reply_sender_uid)
    amount = extract_amount(rest)
    if not target_uid or not amount:
        await reply("بنویس: (ریپلای) «کم کردن سکه [مقدار]» یا بدون ریپلای «کم کردن سکه [مقدار] [آیدی]»")
        return
    new_balance = remove_coins(target_uid, amount)
    await reply(f"✅ {amount:,} سکه از آیدی {target_uid} کم شد. (موجودی فعلی: {new_balance:,})")


async def handle_add_xp(reply, uid, text_raw, reply_sender_uid):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    rest = text_raw.replace("اضافه کردن ایکس پی", "", 1).replace("اضافه کردن اکس پی", "", 1).strip()
    target_uid = resolve_target_uid(rest, reply_sender_uid)
    amount = extract_amount(rest)
    if not target_uid or not amount:
        await reply("بنویس: (ریپلای) «اضافه کردن ایکس پی [مقدار]» یا بدون ریپلای «اضافه کردن ایکس پی [مقدار] [آیدی]»")
        return
    add_xp(target_uid, amount)
    await reply(f"✅ {amount:,} XP به آیدی {target_uid} اضافه شد.")


async def handle_remove_xp(reply, uid, text_raw, reply_sender_uid):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    rest = text_raw.replace("کم کردن ایکس پی", "", 1).replace("کم کردن اکس پی", "", 1).strip()
    target_uid = resolve_target_uid(rest, reply_sender_uid)
    amount = extract_amount(rest)
    if not target_uid or not amount:
        await reply("بنویس: (ریپلای) «کم کردن ایکس پی [مقدار]» یا بدون ریپلای «کم کردن ایکس پی [مقدار] [آیدی]»")
        return
    u = get_user(target_uid)
    new_xp = max(0, u["xp"] - amount)
    update_user(target_uid, xp=new_xp)
    await reply(f"✅ {amount:,} XP از آیدی {target_uid} کم شد. (XP فعلی: {new_xp:,})")


async def handle_reset_user(reply, uid, text_raw, reply_sender_uid):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    rest = text_raw.replace("ریست کاربر", "", 1).strip()
    target_uid = resolve_target_uid(rest, reply_sender_uid)
    if not target_uid:
        await reply("بنویس: (ریپلای) «ریست کاربر» یا بدون ریپلای «ریست کاربر [آیدی]»")
        return
    reset_user(target_uid)
    await reply(f"✅ اطلاعات کاربر {target_uid} کامل ریست شد.")


async def handle_broadcast(reply, bot, uid, text_raw):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای سازنده ربات‌ه.")
        return
    broadcast_text = text_raw.replace("همگانی", "", 1).strip()
    if not broadcast_text:
        await reply("بنویس: «همگانی [متن پیام]»")
        return

    targets = [u["pv_chat_id"] for u in all_users() if u.get("pv_chat_id")]

    async def send_fn(chat_id, text):
        await maybe_await(bot.send_message(chat_id, text))

    sent, failed = await rate_limited_broadcast(send_fn, targets, broadcast_text)
    await reply(f"✅ پیام همگانی ارسال شد.\nموفق: {sent} | ناموفق: {failed}")


async def handle_bot_wide_stats(reply, uid):
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    total_users = count_users()
    total_groups = len(all_registered_groups())
    all_u = all_users()
    total_msgs_all = sum(u.get("messages", 0) for u in all_u)
    pv_started = sum(1 for u in all_u if u.get("pv_chat_id"))
    await reply(f"""📊 آمار کلی ربات

👥 کل کاربران: {total_users:,}
✅ کاربرانی که پیوی استارت کردن: {pv_started:,}
🏘 گروه‌های ثبت‌شده: {total_groups:,}
💬 کل پیام‌های پردازش‌شده: {total_msgs_all:,}""")


# ==============================================================================
# بخش ۱۰: بازی‌ها
# ==============================================================================
# =============================================================================
# حالت‌های فعال (per uid) - همه تو حافظه
# =============================================================================
active_guess = {}       # {uid: {"answer": int, "tries": int}}
active_prison = {}      # {uid: node_key}
active_truthdare = set()  # uid هایی که منتظر جواب «جرعت یا حقیقت» هستن
active_quiz = {}        # {uid: correct_answer}
active_lie = set()      # uid هایی که منتظر یه جمله برای دروغ‌سنج هستن
active_tictactoe = {}   # {uid: [9 خونه]}
active_penalty = set()  # uid هایی که منتظر جهت شوت هستن


async def try_handle_pending_game_input(reply, uid, u, text_raw):
    """اگه کاربر وسط یکی از بازی‌های چندمرحله‌ایه، اینجا پردازش میشه.
    True برمی‌گردونه اگه پردازش کرد (روتر اصلی دیگه ادامه نده)."""
    if uid in active_guess and text_raw.strip().lstrip("-").isdigit():
        await _handle_guess_input(reply, uid, text_raw)
        return True

    if await try_handle_giant_move(reply, uid, text_raw):
        return True

    if uid in active_tictactoe and text_raw.strip().isdigit() and 1 <= int(text_raw.strip()) <= 9:
        await _handle_tictactoe_move(reply, uid, text_raw)
        return True

    if uid in active_prison:
        return await _handle_prison_input(reply, uid, text_raw)

    if uid in active_penalty:
        return await _handle_penalty_input(reply, uid, u, text_raw)

    if uid in active_truthdare:
        return await _handle_truth_dare_input(reply, uid, text_raw)

    if uid in active_quiz:
        return await _handle_quiz_input(reply, uid, text_raw)

    if uid in active_lie:
        await _handle_lie_input(reply, uid, text_raw)
        return True

    return False


# =============================================================================
# 🎮 حدس عدد
# =============================================================================
async def handle_start_guess(reply, uid):
    active_guess[uid] = {"answer": random.randint(1, 100), "tries": 0}
    await reply("🎮 یه عدد بین ۱ تا ۱۰۰ تو ذهنم دارم. حدس بزن!")


async def _handle_guess_input(reply, uid, text_raw):
    state = active_guess[uid]
    guess = int(text_raw.strip())
    state["tries"] += 1

    if guess == state["answer"]:
        multiplier = get_active_multiplier()
        reward = int(max(10, 100 - state["tries"] * 10) * multiplier)
        xp = int(15 * multiplier)
        add_coins(uid, reward)
        add_xp(uid, xp)
        tries = state["tries"]
        del active_guess[uid]
        bonus_txt = f" (×{multiplier:g} پارتی!)" if multiplier > 1 else ""
        await reply(f"🎉 آفرین! درست حدس زدی (تو {tries} بار حدس زدی)\n💰 جایزه: {reward} سکه{bonus_txt} | ✨ +{xp} XP")
    elif guess < state["answer"]:
        await reply("🔼 عدد من بزرگ‌تره!")
    else:
        await reply("🔽 عدد من کوچیک‌تره!")


# =============================================================================
# ⚽ پنالتی + فروشگاه پنالتی
# =============================================================================
PENALTY_DIRECTIONS = ["چپ", "وسط", "راست"]


async def handle_start_penalty(reply, uid, text_raw=""):
    """حالا پنالتی یک‌مرحله‌ایه: می‌تونی همون لحظه جهتت رو هم بگی
    («پنالتی چپ») و بازی فوری تموم بشه؛ یا فقط «پنالتی» بنویسی و جهت
    رو تو پیام بعدی بگی."""
    direction_in_text = next((d for d in PENALTY_DIRECTIONS if d in text_raw), None)
    if direction_in_text:
        await _resolve_penalty(reply, uid, direction_in_text)
        return
    active_penalty.add(uid)
    await reply("⚽ ضربه‌ی پنالتی بزن! کدوم سمت شوت می‌کنی؟\nبنویس: چپ / وسط / راست\n(یا از این به بعد یه‌جا بنویس: «پنالتی چپ»)")


async def _handle_penalty_input(reply, uid, u, text_raw):
    choice = text_raw.strip()
    if choice not in PENALTY_DIRECTIONS:
        return False  # منتظر یکی از سه گزینه بود، این پیام ربطی نداشت
    active_penalty.discard(uid)
    await _resolve_penalty(reply, uid, choice)
    return True


async def _resolve_penalty(reply, uid, choice):
    keeper = random.choice(PENALTY_DIRECTIONS)

    owned = {i["item_name"] for i in get_items(uid, "penalty")}
    luck_boost = 0
    for item in owned:
        if item in ("توپ طلایی", "کفش سرعتی", "توپ الماسی", "مدال قهرمانی"):
            luck_boost += 0.05

    goal = (choice != keeper) or (random.random() < luck_boost)
    multiplier = get_active_multiplier()

    if goal:
        base_reward = random.randint(60, 150)
        bonus = sum(PENALTY_COIN_BONUS.get(i, 0) for i in owned)
        reward = int((base_reward + bonus) * multiplier)
        xp = int(10 * multiplier)
        add_coins(uid, reward)
        add_xp(uid, xp)
        bonus_txt = f" (×{multiplier:g} پارتی!)" if multiplier > 1 else ""
        await reply(f"⚽🥅 گـــل!! دروازه‌بان سمت {keeper} پرید، تو {choice} زدی!\n💰 جایزه: {reward} سکه{bonus_txt} | ✨ +{xp} XP")
    else:
        await reply(f"🧤 دروازه‌بان سمت {keeper} پرید و مهارش کرد! بازم امتحان کن.")


async def handle_penalty_shop(reply):
    lines = ["⚽ فروشگاه پنالتی ⚽\n"]
    for item, price in PENALTY_SHOP.items():
        emoji = PENALTY_EMOJI.get(item, "•")
        bonus = PENALTY_COIN_BONUS.get(item)
        extra = f" (+{bonus} سکه هر گل)" if bonus else ""
        lines.append(f"{emoji} {item} — {price:,} سکه{extra}")
    lines.append("\n💡 برای خرید: «خرید پنالتی [اسم آیتم]»")
    await reply("\n".join(lines))


async def handle_buy_penalty_item(reply, uid, u, text_raw):
    item_name = text_raw.replace("خرید پنالتی", "", 1).strip()
    match = next((n for n in PENALTY_SHOP if n == item_name), None)
    if not match:
        await reply("همچین آیتمی تو فروشگاه پنالتی نیست. بنویس «فروشگاه پنالتی».")
        return
    price = PENALTY_SHOP[match]
    if u["coins"] < price:
        await reply(f"❌ سکه‌ت کافی نیست. {price - u['coins']:,} سکه‌ی دیگه لازم داری.")
        return
    remove_coins(uid, price)
    add_item(uid, "penalty", match, 1)
    await reply(f"✅ «{match}» رو خریدی! تو بازی‌های بعدی پنالتی کمکت می‌کنه.")


# =============================================================================
# 🔫 رولت روسی + فروشگاه رولت
# =============================================================================
async def handle_roulette(reply, uid, u, text_raw):
    bet_str = "".join(ch for ch in text_raw if ch.isdigit())
    if not bet_str:
        await reply("بنویس: «رولت روسی [مبلغ شرط]» — مثلا: رولت روسی 200\n⚠️ شانس برد فقط ۱٪ ئه، خیلی خیلی سخته!")
        return
    bet = int(bet_str)
    if bet <= 0:
        await reply("مبلغ شرط باید بزرگتر از صفر باشه.")
        return
    if u["coins"] < bet:
        await reply("❌ سکه‌ت برای این شرط کافی نیست.")
        return

    # بالانس شده: شانس برد فقط ۱٪، آیتم‌ها کمی کمکت می‌کنن ولی سقف داره
    # (که همچنان خیلی سخت بمونه و راهی برای پولدار شدن سریع نباشه)
    owned = {i["item_name"] for i in get_items(uid, "roulette")}
    win_chance = 0.01
    if "خشاب شانس" in owned:
        win_chance += 0.005
    if "طلسم بقا" in owned:
        win_chance += 0.005
    win_chance = min(win_chance, 0.03)  # سقف ۳٪، حتی با همه‌ی آیتم‌ها

    if random.random() < win_chance:
        add_coins(uid, bet)
        add_xp(uid, 8)
        await reply(f"🔫✨ باورنکردنیه!! بردی!\n💰 {bet:,} سکه بردی! (مجموع: {bet*2:,})")
    else:
        remove_coins(uid, bet)
        await reply(f"🔫💥 بنـــگ! باختی (شانس برد فقط ۱٪ بود).\n💸 {bet:,} سکه رو از دست دادی.")


async def handle_roulette_shop(reply):
    lines = ["🔫 فروشگاه رولت روسی 🔫\n"]
    for item, price in ROULETTE_SHOP.items():
        emoji = ROULETTE_EMOJI.get(item, "•")
        lines.append(f"{emoji} {item} — {price:,} سکه")
    lines.append("\n💡 این آیتم‌ها شانس مرگ رو کم می‌کنن.\nبرای خرید: «خرید رولت [اسم آیتم]»")
    await reply("\n".join(lines))


async def handle_buy_roulette_item(reply, uid, u, text_raw):
    item_name = text_raw.replace("خرید رولت", "", 1).strip()
    match = next((n for n in ROULETTE_SHOP if n == item_name), None)
    if not match:
        await reply("همچین آیتمی تو فروشگاه رولت نیست. بنویس «فروشگاه رولت».")
        return
    price = ROULETTE_SHOP[match]
    if u["coins"] < price:
        await reply(f"❌ سکه‌ت کافی نیست. {price - u['coins']:,} سکه‌ی دیگه لازم داری.")
        return
    remove_coins(uid, price)
    add_item(uid, "roulette", match, 1)
    await reply(f"✅ «{match}» رو خریدی! شانس مرگت تو رولت کمتر شد.")


# =============================================================================
# 🚔 فرار از زندان
# =============================================================================
async def handle_start_prison(reply, uid):
    active_prison[uid] = "start"
    await reply(PRISON_STORY["start"]["text"])


async def _handle_prison_input(reply, uid, text_raw):
    node_key = active_prison[uid]
    node = PRISON_STORY[node_key]
    options = node.get("options", {})
    choice = text_raw.strip()

    if choice not in options:
        return False  # منتظر یکی از گزینه‌های این مرحله بود

    next_key = options[choice]
    next_node = PRISON_STORY[next_key]

    if "end" in next_node:
        del active_prison[uid]
        if next_node["end"] == "win":
            multiplier = get_active_multiplier()
            reward = int(random.randint(*PRISON_WIN_REWARD) * multiplier)
            xp = int(25 * multiplier)
            add_coins(uid, reward)
            add_xp(uid, xp)
            bonus_txt = f" (×{multiplier:g} پارتی!)" if multiplier > 1 else ""
            await reply(f"{next_node['text']}\n\n💰 جایزه‌ی فرار موفق: {reward} سکه{bonus_txt} | ✨ +{xp} XP")
        else:
            await reply(f"{next_node['text']}\n\n😅 دفعه‌ی بعد بیشتر شانس بیار.")
    else:
        active_prison[uid] = next_key
        await reply(next_node["text"])
    return True


# =============================================================================
# 🎭 جرعت حقیقت
# =============================================================================
async def handle_start_truth_dare(reply, uid):
    active_truthdare.add(uid)
    await reply("🎭 جرعت یا حقیقت؟\nبنویس: جرعت / حقیقت")


async def _handle_truth_dare_input(reply, uid, text_raw):
    choice = text_raw.strip()
    if choice == "حقیقت":
        active_truthdare.discard(uid)
        await reply(f"❓ سوال: {random.choice(TRUTHS)}")
        return True
    if choice == "جرعت":
        active_truthdare.discard(uid)
        await reply(f"🔥 جرعت: {random.choice(DARES)}")
        return True
    return False


# =============================================================================
# 🕵️ دروغ‌سنج
# =============================================================================
async def handle_start_lie_detector(reply, uid):
    active_lie.add(uid)
    await reply("🕵️ یه جمله بگو تا دروغ‌سنج بررسیش کنه...")


async def _handle_lie_input(reply, uid, text_raw):
    active_lie.discard(uid)
    percent = random.randint(0, 100)
    if percent >= 70:
        verdict = "😇 کاملاً راست می‌گی!"
    elif percent >= 40:
        verdict = "🤔 یه‌جوریاست، شک دارم."
    else:
        verdict = "🤥 داری دروغ می‌گی!"
    await reply(f"🕵️ دروغ‌سنج: {percent}٪ راستگویی\n{verdict}")


# =============================================================================
# ❌⭕ دوز (با ربات)
# =============================================================================
WIN_LINES = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),
    (0, 3, 6), (1, 4, 7), (2, 5, 8),
    (0, 4, 8), (2, 4, 6),
]


def _render_board(board):
    symbols = [c if c != " " else str(i + 1) for i, c in enumerate(board)]
    rows = []
    for r in range(3):
        rows.append(" | ".join(symbols[r * 3:r * 3 + 3]))
    return "\n---------\n".join(rows)


def _check_winner(board):
    for a, b, c in WIN_LINES:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]
    if " " not in board:
        return "draw"
    return None


async def handle_start_tictactoe(reply, uid):
    active_tictactoe[uid] = [" "] * 9
    await reply(f"❌⭕ دوز شروع شد! تو X هستی، من O.\nیه خونه (۱ تا ۹) رو بنویس.\n\n{_render_board(active_tictactoe[uid])}")


async def _handle_tictactoe_move(reply, uid, text_raw):
    board = active_tictactoe[uid]
    pos = int(text_raw.strip()) - 1
    if board[pos] != " ":
        await reply("این خونه پره، یکی دیگه رو انتخاب کن.")
        return

    board[pos] = "X"
    winner = _check_winner(board)
    if winner:
        del active_tictactoe[uid]
        await _finish_tictactoe(reply, uid, winner, board)
        return

    empty_cells = [i for i, c in enumerate(board) if c == " "]
    bot_move = random.choice(empty_cells)
    board[bot_move] = "O"
    winner = _check_winner(board)
    if winner:
        del active_tictactoe[uid]
        await _finish_tictactoe(reply, uid, winner, board)
        return

    await reply(_render_board(board))


async def _finish_tictactoe(reply, uid, winner, board):
    board_txt = _render_board(board)
    if winner == "X":
        add_coins(uid, 100)
        add_xp(uid, 20)
        await reply(f"{board_txt}\n\n🎉 بردی! 💰 +100 سکه | ✨ +20 XP")
    elif winner == "O":
        await reply(f"{board_txt}\n\n😅 این‌بار من بردم، دوباره امتحان کن.")
    else:
        await reply(f"{board_txt}\n\n🤝 مساوی شد!")


# =============================================================================
# 👹 نبرد رئیس (Boss Battle) - فقط موقع پارتی، همه با هم حمله می‌کنن
# =============================================================================
_boss_damage_contrib = {}  # {uid: مجموع دمیجی که زده}


def spawn_boss(multiplier=1):
    name = random.choice(BOSS_NAMES)
    hp = int(BOSS_HP_BASE * max(1, multiplier * 0.6))  # با ضریب پارتی، باس هم قوی‌تر میشه
    set_state("boss_name", name)
    set_state("boss_hp", hp)
    set_state("boss_max_hp", hp)
    _boss_damage_contrib.clear()
    return name, hp


def get_boss_status():
    name = get_state("boss_name")
    if not name:
        return None
    hp = int(float(get_state("boss_hp", "0")))
    max_hp = int(float(get_state("boss_max_hp", "1")))
    return {"name": name, "hp": hp, "max_hp": max_hp}


def _hp_bar(hp, max_hp, length=10):
    filled = max(0, min(length, round((hp / max_hp) * length))) if max_hp else 0
    return "🟥" * filled + "⬛" * (length - filled)


async def handle_attack(reply, uid):
    if not is_party_active():
        await reply("👹 هیچ رئیسی الان نیست! فقط موقع پارتی نبرد رئیس فعاله.")
        return

    boss = get_boss_status()
    if not boss or boss["hp"] <= 0:
        await reply("👹 الان رئیسی برای حمله نیست.")
        return

    dmg = random.randint(BOSS_ATTACK_MIN, BOSS_ATTACK_MAX)
    new_hp = max(0, boss["hp"] - dmg)
    set_state("boss_hp", new_hp)
    _boss_damage_contrib[uid] = _boss_damage_contrib.get(uid, 0) + dmg
    attack_line = random.choice(BOSS_ATTACK_MESSAGES)

    if new_hp <= 0:
        await reply(f"{attack_line} 💥 {dmg} دمیج زدی!\n\n👑 {boss['name']} نابود شد!!")
        await _resolve_boss_defeat(reply, boss)
        return

    bar = _hp_bar(new_hp, boss["max_hp"])
    await reply(f"{attack_line} 💥 {dmg} دمیج زدی!\n{boss['name']}\n{bar}  {new_hp:,}/{boss['max_hp']:,} HP")


async def _resolve_boss_defeat(reply, boss):
    multiplier = get_active_multiplier()

    if not _boss_damage_contrib:
        return

    total_dmg = sum(_boss_damage_contrib.values())
    top_uid = max(_boss_damage_contrib, key=_boss_damage_contrib.get)

    lines = ["🏆 نتیجه‌ی نبرد:"]
    for u_id, dmg in sorted(_boss_damage_contrib.items(), key=lambda x: -x[1]):
        share = dmg / total_dmg
        coins = int(1500 * share * multiplier)
        xp = int(150 * share * multiplier)
        add_coins(u_id, coins)
        add_xp(u_id, xp)
        tag = " 👑 (بیشترین دمیج!)" if u_id == top_uid else ""
        lines.append(f"• {u_id[-6:]}: {dmg:,} دمیج → 💰{coins:,} | ✨{xp}{tag}")

    await reply("\n".join(lines))
    # یه رئیس جدید بلافاصله ظاهر میشه تا نبرد ادامه پیدا کنه
    new_name, new_hp = spawn_boss(multiplier)
    await reply(f"👹 یه رئیس جدید ظاهر شد: {new_name}\n{_hp_bar(new_hp, new_hp)}  {new_hp:,}/{new_hp:,} HP\nبنویس «حمله» تا شروع کنی!")


async def handle_boss_status(reply):
    if not is_party_active():
        await reply("👹 الان پارتی فعال نیست، رئیسی هم نیست.")
        return
    boss = get_boss_status()
    if not boss:
        await reply("👹 هنوز رئیسی ظاهر نشده.")
        return
    bar = _hp_bar(boss["hp"], boss["max_hp"])
    await reply(f"👹 {boss['name']}\n{bar}  {boss['hp']:,}/{boss['max_hp']:,} HP\nبنویس «حمله» تا بهش ضربه بزنی!")


# =============================================================================
# 🎡 چرخ شانس پارتی + 📦 جعبه شانس پارتی - فقط موقع پارتی فعالن
# =============================================================================
PARTY_MINIGAME_COOLDOWN_SECONDS = 20  # تا کسی پشت‌سرهم اسپم نکنه
_last_wheel_spin = {}
_last_box_open = {}


async def handle_party_wheel(reply, uid):
    if not is_party_active():
        await reply("🎡 «چرخ شانس» فقط موقع پارتیه! منتظر «شروع ادمین ابیوز» باش.")
        return

    now = time.time()
    last = _last_wheel_spin.get(uid, 0)
    if now - last < PARTY_MINIGAME_COOLDOWN_SECONDS:
        wait = int(PARTY_MINIGAME_COOLDOWN_SECONDS - (now - last))
        await reply(f"⏳ چرخ داغه، {wait} ثانیه دیگه دوباره امتحان کن.")
        return
    _last_wheel_spin[uid] = now

    prize = weighted_choice(PARTY_WHEEL_PRIZES)
    multiplier = get_active_multiplier()
    coins = int(prize["coins"] * multiplier)
    xp = int(prize["xp"] * multiplier)
    if coins:
        add_coins(uid, coins)
    if xp:
        add_xp(uid, xp)

    await reply(f"🎡 چرخ شانس می‌چرخه... {prize['label']}!\n💰 +{coins:,} سکه | ✨ +{xp} XP")


async def handle_party_box(reply, uid):
    if not is_party_active():
        await reply("📦 «جعبه شانس» فقط موقع پارتیه! منتظر «شروع ادمین ابیوز» باش.")
        return

    now = time.time()
    last = _last_box_open.get(uid, 0)
    if now - last < PARTY_MINIGAME_COOLDOWN_SECONDS:
        wait = int(PARTY_MINIGAME_COOLDOWN_SECONDS - (now - last))
        await reply(f"⏳ یکم صبر کن، {wait} ثانیه دیگه یه جعبه‌ی دیگه باز کن.")
        return
    _last_box_open[uid] = now

    prize = weighted_choice(PARTY_BOX_PRIZES)
    multiplier = get_active_multiplier()
    coins = int(prize["coins"] * multiplier)
    xp = int(prize["xp"] * multiplier)
    if coins:
        add_coins(uid, coins)
    if xp:
        add_xp(uid, xp)

    await reply(f"📦 جعبه رو باز کردی... {prize['label']}!\n💰 +{coins:,} سکه | ✨ +{xp} XP")


# =============================================================================
# 👑 دوز غول - فقط موقع پارتی فعاله، تخته‌ی ۶×۶ و برد با ۴تا پشت‌سرهم
# =============================================================================
GIANT_SIZE = 6
GIANT_WIN_LEN = 4
active_giant_tictactoe = {}  # {uid: [36 خونه]}


def _giant_render(board):
    rows = []
    for r in range(GIANT_SIZE):
        row_cells = []
        for c in range(GIANT_SIZE):
            idx = r * GIANT_SIZE + c
            cell = board[idx]
            row_cells.append(cell if cell != " " else f"{idx+1:>2}")
        rows.append(" ".join(row_cells))
    return "\n".join(rows)


def _giant_check_winner(board):
    size = GIANT_SIZE
    directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
    for r in range(size):
        for c in range(size):
            cell = board[r * size + c]
            if cell == " ":
                continue
            for dr, dc in directions:
                count = 0
                rr, cc = r, c
                while 0 <= rr < size and 0 <= cc < size and board[rr * size + cc] == cell:
                    count += 1
                    if count >= GIANT_WIN_LEN:
                        return cell
                    rr += dr
                    cc += dc
    if " " not in board:
        return "draw"
    return None


async def handle_start_giant_tictactoe(reply, uid):
    if not is_party_active():
        await reply("👑 «دوز غول» فقط موقع پارتی سکه/XP فعاله! منتظر پارتی بعدی باش.")
        return
    active_giant_tictactoe[uid] = [" "] * (GIANT_SIZE * GIANT_SIZE)
    await reply(f"👑 دوز غول شروع شد! ({GIANT_SIZE}×{GIANT_SIZE}, ۴تا پشت‌سرهم برنده‌ست)\nتو X هستی، من O. یه خونه (۱ تا {GIANT_SIZE*GIANT_SIZE}) رو بنویس.\n\n{_giant_render(active_giant_tictactoe[uid])}")


async def try_handle_giant_move(reply, uid, text_raw):
    if uid not in active_giant_tictactoe:
        return False
    stripped = text_raw.strip()
    if not stripped.isdigit():
        return False
    n = int(stripped)
    if not (1 <= n <= GIANT_SIZE * GIANT_SIZE):
        return False

    if not is_party_active():
        del active_giant_tictactoe[uid]
        await reply("👑 پارتی تموم شد، دوز غول هم متوقف شد.")
        return True

    board = active_giant_tictactoe[uid]
    pos = n - 1
    if board[pos] != " ":
        await reply("این خونه پره، یکی دیگه رو انتخاب کن.")
        return True

    board[pos] = "X"
    winner = _giant_check_winner(board)
    if not winner:
        empty_cells = [i for i, c in enumerate(board) if c == " "]
        bot_move = random.choice(empty_cells)
        board[bot_move] = "O"
        winner = _giant_check_winner(board)

    board_txt = _giant_render(board)
    if winner:
        del active_giant_tictactoe[uid]
        multiplier = get_active_multiplier()
        if winner == "X":
            reward = int(500 * multiplier)
            xp = int(100 * multiplier)
            add_coins(uid, reward)
            add_xp(uid, xp)
            await reply(f"{board_txt}\n\n👑🎉 بردی! این جایزه‌ی ویژه‌ی پارتی بود!\n💰 +{reward:,} سکه | ✨ +{xp} XP")
        elif winner == "O":
            await reply(f"{board_txt}\n\n😅 این‌بار من بردم، دوباره امتحان کن (تا وقتی پارتیه)!")
        else:
            await reply(f"{board_txt}\n\n🤝 مساوی شد!")
    else:
        await reply(board_txt)
    return True


# =============================================================================
# 📝 مسابقه (کوییز)
# =============================================================================
async def handle_start_quiz(reply, uid):
    question, answer = random.choice(QUIZ)
    active_quiz[uid] = answer.strip().lower()
    await reply(f"📝 سوال: {question}")


async def _handle_quiz_input(reply, uid, text_raw):
    correct = active_quiz[uid]
    del active_quiz[uid]
    given = text_raw.strip().lower()
    if given == correct or correct in given:
        add_coins(uid, 80)
        add_xp(uid, 12)
        await reply("✅ درست بود! 💰 +80 سکه | ✨ +12 XP")
    else:
        await reply(f"❌ جواب درست: {correct}")
    return True


# =============================================================================
# 🧩 معما (تک‌سوالی، بدون نیاز به وضعیت چندمرحله‌ای)
# =============================================================================
async def handle_riddle(reply):
    q, a = random.choice(RIDDLES)
    await reply(f"🧩 معما: {q}\n\n(جوابشو تو ذهنت نگه دار؛ بازی حدس‌گیری خودکارش رو بعدا اضافه می‌کنیم)")


# ==============================================================================
# بخش ۱۱: بازار سیاه و هدیه/انتقال
# ==============================================================================
MIN_AUCTION_RARITY_ORDER = ["Epic", "Legendary", "Mythic", "God", "Secret", "OG"]


# =============================================================================
# 🎁 هدیه / انتقال بین کاربران
# =============================================================================
async def handle_gift_coins(reply, uid, u, text_raw, reply_sender_uid):
    if not reply_sender_uid:
        await reply("❌ باید روی پیام کسی که می‌خوای بهش هدیه بدی ریپلای بزنی.")
        return
    if str(reply_sender_uid) == str(uid):
        await reply("❌ نمی‌تونی به خودت هدیه بدی.")
        return
    amount = extract_amount(text_raw)
    if not amount or amount <= 0:
        await reply("بنویس: (ریپلای) «هدیه سکه [مقدار]»")
        return
    if u["coins"] < amount:
        await reply("❌ سکه‌ت کافی نیست.")
        return

    remove_coins(uid, amount)
    add_coins(reply_sender_uid, amount)
    await reply(f"🎁 {amount:,} سکه به آیدی {reply_sender_uid} هدیه دادی!")


async def handle_gift_xp(reply, uid, u, text_raw, reply_sender_uid):
    if not reply_sender_uid:
        await reply("❌ باید روی پیام کسی که می‌خوای بهش هدیه بدی ریپلای بزنی.")
        return
    if str(reply_sender_uid) == str(uid):
        await reply("❌ نمی‌تونی به خودت هدیه بدی.")
        return
    amount = extract_amount(text_raw)
    if not amount or amount <= 0:
        await reply("بنویس: (ریپلای) «هدیه ایکس پی [مقدار]»")
        return
    if u["xp"] < amount:
        await reply("❌ XP کافی نداری.")
        return

    update_user(uid, xp=u["xp"] - amount)
    add_xp(reply_sender_uid, amount)
    await reply(f"🎁 {amount:,} XP به آیدی {reply_sender_uid} هدیه دادی!")


async def handle_gift_item(reply, uid, text_raw, reply_sender_uid):
    if not reply_sender_uid:
        await reply("❌ باید روی پیام کسی که می‌خوای بهش هدیه بدی ریپلای بزنی.")
        return
    if str(reply_sender_uid) == str(uid):
        await reply("❌ نمی‌تونی به خودت هدیه بدی.")
        return
    item_name = text_raw.replace("هدیه آیتم", "", 1).strip()
    if not item_name:
        await reply("بنویس: (ریپلای) «هدیه آیتم [اسم آیتم]»")
        return

    owned = find_owned_item(uid, item_name)
    if not owned:
        await reply("❌ این آیتم رو نداری.")
        return

    removed = remove_item(uid, owned["category"], item_name, 1)
    if not removed:
        await reply("❌ موجودی کافی نداری.")
        return
    add_item(reply_sender_uid, owned["category"], item_name, 1)
    await reply(f"🎁 «{item_name}» رو به آیدی {reply_sender_uid} هدیه دادی!")


# =============================================================================
# 🖤 بازار سیاه
# =============================================================================
def _get_item_rarity_info(item_name):
    catalog_item = ITEM_CATALOG_BY_NAME.get(item_name)
    return catalog_item["rarity"] if catalog_item else None


async def handle_sell(reply, uid, text_raw):
    """فروش [آیتم] [قیمت]"""
    rest = text_raw.replace("فروش", "", 1).strip()
    price = extract_amount(rest)
    if not price or price <= 0:
        await reply("بنویس: «فروش [اسم آیتم] [قیمت]»")
        return
    item_name = rest.rsplit(str(price), 1)[0].strip()
    if not item_name:
        await reply("بنویس: «فروش [اسم آیتم] [قیمت]»")
        return

    owned = find_owned_item(uid, item_name)
    if not owned:
        await reply("❌ این آیتم رو نداری که بفروشیش.")
        return

    removed = remove_item(uid, owned["category"], item_name, 1)
    if not removed:
        await reply("❌ مشکلی تو موجودیت پیش اومد.")
        return

    listing_id = create_listing(uid, item_name, owned["category"], 1, price)
    await reply(f"✅ «{item_name}» با قیمت {price:,} سکه تو بازار سیاه ثبت شد (آگهی #{listing_id}).\n"
                f"💡 مالیات فروش {int(MARKET_TAX_RATE*100)}٪ از قیمت کم میشه وقتی فروخته بشه.")


async def handle_auction(reply, uid, text_raw):
    """مزایده [آیتم] [قیمت پایه] [دقیقه] - فقط برای آیتم‌های Epic به بالا"""
    rest = text_raw.replace("مزایده", "", 1).strip()
    nums = [int(n) for n in rest.split() if n.isdigit()]
    if len(nums) < 2:
        await reply("بنویس: «مزایده [اسم آیتم] [قیمت پایه] [مدت به دقیقه]»\n(فقط برای آیتم‌های Epic و بالاتر)")
        return
    base_price, duration_min = nums[0], nums[1]
    item_name = rest
    for n in nums:
        item_name = item_name.replace(str(n), "")
    item_name = item_name.strip()

    owned = find_owned_item(uid, item_name)
    if not owned:
        await reply("❌ این آیتم رو نداری.")
        return

    rarity = _get_item_rarity_info(item_name)
    if rarity not in MIN_AUCTION_RARITY_ORDER:
        await reply(f"❌ مزایده فقط برای آیتم‌های Epic به بالا مجازه. «{item_name}» کمیابیش کافی نیست.")
        return

    removed = remove_item(uid, owned["category"], item_name, 1)
    if not removed:
        await reply("❌ مشکلی تو موجودیت پیش اومد.")
        return

    listing_id = create_listing(uid, item_name, owned["category"], 1, base_price, is_auction=True, auction_minutes=duration_min)
    await reply(f"🔨 مزایده‌ی «{item_name}» شروع شد! قیمت پایه: {base_price:,} سکه، مدت: {duration_min} دقیقه (آگهی #{listing_id}).\n"
                f"بقیه می‌تونن با «پیشنهاد {listing_id} [مبلغ]» رقابت کنن.")


async def handle_bid(reply, uid, u, text_raw):
    """پیشنهاد [شماره آگهی] [مبلغ]"""
    parts = text_raw.replace("پیشنهاد", "", 1).split()
    nums = [int(p) for p in parts if p.isdigit()]
    if len(nums) < 2:
        await reply("بنویس: «پیشنهاد [شماره آگهی] [مبلغ]»")
        return
    listing_id, bid_amount = nums[0], nums[1]

    listing = get_listing(listing_id)
    if not listing or listing["status"] != "active" or not listing["is_auction"]:
        await reply("❌ همچین مزایده‌ی فعالی پیدا نشد.")
        return
    if time.time() >= listing["auction_end_ts"]:
        await reply("❌ این مزایده تموم شده.")
        return
    if str(listing["seller_uid"]) == str(uid):
        await reply("❌ نمی‌تونی رو مزایده‌ی خودت پیشنهاد بدی.")
        return
    if bid_amount <= listing["current_bid"]:
        await reply(f"❌ پیشنهادت باید بیشتر از {listing['current_bid']:,} باشه.")
        return
    if u["coins"] < bid_amount:
        await reply("❌ سکه‌ت برای این پیشنهاد کافی نیست.")
        return

    update_listing_bid(listing_id, bid_amount, uid)
    await reply(f"✅ پیشنهادت ({bid_amount:,} سکه) برای «{listing['item_name']}» ثبت شد.")


async def process_expired_auctions():
    """هر مزایده‌ای که زمانش تموم شده رو می‌بنده - برنده رو مشخص می‌کنه یا
    به فروشنده برمی‌گردونه اگه کسی پیشنهاد نداده بود. این تابع باید دوره‌ای
    (مثلا هر دقیقه) صدا زده بشه."""
    for listing in get_expired_active_auctions():
        if listing["current_bidder"]:
            buyer = listing["current_bidder"]
            price = listing["current_bid"]
            tax = int(price * MARKET_TAX_RATE)
            net = price - tax
            remove_coins(buyer, price)
            add_coins(listing["seller_uid"], net)
            add_item(buyer, listing["category"], listing["item_name"], listing["qty"])
            log_transaction(listing["seller_uid"], buyer, listing["item_name"], price, tax)
            update_listing_status(listing["id"], "sold")
        else:
            add_item(listing["seller_uid"], listing["category"], listing["item_name"], listing["qty"])
            update_listing_status(listing["id"], "expired")


async def handle_market_list(reply, text_raw):
    """بازار [فیلتر اختیاری]"""
    filter_text = text_raw.replace("بازار", "", 1).strip()
    listings = list_active_listings(limit=15, item_name_contains=filter_text or None)
    if not listings:
        await reply("📭 الان هیچ آگهی فعالی نیست." if not filter_text else f"📭 چیزی با «{filter_text}» پیدا نشد.")
        return

    lines = ["🖤 بازار سیاه — آگهی‌های فعال:\n"]
    for l in listings:
        tag = "🔨 مزایده" if l["is_auction"] else "🏷 فروش مستقیم"
        price_show = l["current_bid"] if l["is_auction"] and l["current_bid"] else l["price"]
        lines.append(f"#{l['id']} — {l['item_name']} | {tag} | {price_show:,} سکه")
    lines.append("\n💡 خرید: «خرید بازار [شماره]» | مزایده: «پیشنهاد [شماره] [مبلغ]»")
    await reply("\n".join(lines))


async def handle_market_buy(reply, uid, u, text_raw):
    """خرید بازار [شماره آگهی]"""
    amount = extract_amount(text_raw)
    if not amount:
        await reply("بنویس: «خرید بازار [شماره آگهی]»")
        return
    listing_id = amount

    listing = get_listing(listing_id)
    if not listing or listing["status"] != "active":
        await reply("❌ همچین آگهی فعالی پیدا نشد.")
        return
    if listing["is_auction"]:
        await reply("❌ این یه مزایده‌ست، باید با «پیشنهاد» شرکت کنی، نه خرید مستقیم.")
        return
    if str(listing["seller_uid"]) == str(uid):
        await reply("❌ نمی‌تونی جنس خودتو بخری.")
        return
    price = listing["price"]
    if u["coins"] < price:
        await reply(f"❌ سکه‌ت کافی نیست. {price - u['coins']:,} سکه‌ی دیگه لازم داری.")
        return

    tax = int(price * MARKET_TAX_RATE)
    net_to_seller = price - tax

    remove_coins(uid, price)
    add_coins(listing["seller_uid"], net_to_seller)
    add_item(uid, listing["category"], listing["item_name"], listing["qty"])
    log_transaction(listing["seller_uid"], uid, listing["item_name"], price, tax)
    update_listing_status(listing_id, "sold")

    unlock_achievement(uid, "first_market_purchase")
    unlock_achievement(listing["seller_uid"], "first_market_sale")

    await reply(f"✅ «{listing['item_name']}» رو با {price:,} سکه خریدی!")


async def handle_market_cancel(reply, uid, text_raw):
    """لغو آگهی [شماره]"""
    amount = extract_amount(text_raw)
    if not amount:
        await reply("بنویس: «لغو آگهی [شماره]»")
        return
    listing_id = amount
    listing = get_listing(listing_id)
    if not listing or listing["status"] != "active":
        await reply("❌ همچین آگهی فعالی پیدا نشد.")
        return
    if str(listing["seller_uid"]) != str(uid):
        await reply("❌ این آگهی مال تو نیست.")
        return

    add_item(uid, listing["category"], listing["item_name"], listing["qty"])
    update_listing_status(listing_id, "cancelled")
    await reply(f"✅ آگهی #{listing_id} لغو شد و آیتمت برگشت.")


async def handle_market_history(reply, uid):
    history = get_user_transaction_history(uid, limit=10)
    if not history:
        await reply("📭 هنوز هیچ معامله‌ای نداشتی.")
        return
    lines = ["📜 تاریخچه‌ی معاملات تو:\n"]
    for h in history:
        role = "فروختی" if str(h["seller_uid"]) == str(uid) else "خریدی"
        lines.append(f"• {h['item_name']} — {role} به قیمت {h['price']:,} (مالیات: {h['tax']:,})")
    await reply("\n".join(lines))


# ==============================================================================
# بخش ۱۲: کلن/گیلد، لیدربورد، دستاورد
# ==============================================================================
# =============================================================================
# 🛡️ کلن / گیلد
# =============================================================================
async def handle_create_clan(reply, uid, text_raw):
    name = text_raw.replace("کلن بساز", "", 1).strip()
    if not name:
        await reply("بنویس: «کلن بساز [اسم کلن]»")
        return
    if get_user_clan(uid):
        await reply("❌ تو از قبل عضو یه کلنی. اول ازش خارج شو («خروج از کلن»).")
        return
    if get_clan_by_name(name):
        await reply("❌ این اسم کلن قبلاً گرفته شده.")
        return

    clan_id = create_clan(name, uid)
    await reply(f"🛡️ کلن «{name}» ساخته شد! تو رهبرشی.\nبقیه می‌تونن با «عضویت در کلن {name}» بهت ملحق بشن.")


async def handle_join_clan(reply, uid, text_raw):
    name = text_raw.replace("عضویت در کلن", "", 1).strip()
    if not name:
        await reply("بنویس: «عضویت در کلن [اسم کلن]»")
        return
    if get_user_clan(uid):
        await reply("❌ تو از قبل عضو یه کلنی.")
        return
    clan = get_clan_by_name(name)
    if not clan:
        await reply("❌ همچین کلنی پیدا نشد. بنویس «کلن‌ها» برای دیدن لیست.")
        return
    join_clan(clan["id"], uid)
    await reply(f"✅ به کلن «{name}» ملحق شدی!")


async def handle_leave_clan(reply, uid):
    clan = get_user_clan(uid)
    if not clan:
        await reply("❌ تو عضو هیچ کلنی نیستی.")
        return
    leave_clan(clan["id"], uid)
    await reply(f"👋 از کلن «{clan['name']}» خارج شدی.")


async def handle_clan_list(reply):
    clans = all_clans(limit=20)
    if not clans:
        await reply("📭 هنوز هیچ کلنی ساخته نشده. بنویس «کلن بساز [اسم]» تا اولیش رو بسازی!")
        return
    lines = ["🛡️ لیست کلن‌ها:\n"]
    for c in clans:
        members_count = len(get_clan_members(c["id"]))
        lines.append(f"• {c['name']} — {members_count} عضو")
    await reply("\n".join(lines))


async def handle_my_clan(reply, uid):
    clan = get_user_clan(uid)
    if not clan:
        await reply("❌ تو عضو هیچ کلنی نیستی. بنویس «کلن‌ها» یا «کلن بساز [اسم]».")
        return
    members = get_clan_members(clan["id"])
    role = "👑 رهبر" if str(clan["owner_uid"]) == str(uid) else "عضو"
    await reply(f"🛡️ کلن: {clan['name']}\nمقام تو: {role}\nتعداد اعضا: {len(members)}")


# =============================================================================
# 🏆 لیدربوردهای اضافی (سکه، برد، تعداد آیتم)
# =============================================================================
def _display_name_short(u):
    return u.get("display_name") or f"کاربر {u['uid'][-6:]}"


async def handle_leaderboard_coins(reply):
    top = top_users_by_coins(10)
    if not top:
        await reply("هنوز کسی سکه‌ای نداره.")
        return
    lines = ["🥇 برترین‌های سکه 💰\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {_display_name_short(u)} — {u['coins']:,} سکه")
    await reply("\n".join(lines))


async def handle_leaderboard_wins(reply):
    top = top_users_by_wins(10)
    if not top:
        await reply("هنوز کسی بردی نداشته.")
        return
    lines = ["🥇 برترین‌های برد 🏆\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, u in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        lines.append(f"{medal} {_display_name_short(u)} — {u['wins']:,} برد")
    await reply("\n".join(lines))


async def handle_leaderboard_items(reply):
    top = top_users_by_item_count(10)
    if not top:
        await reply("هنوز کسی آیتمی نداره.")
        return
    lines = ["🥇 برترین‌های کلکسیون 🎒\n"]
    medals = ["🥇", "🥈", "🥉"]
    for i, row in enumerate(top):
        medal = medals[i] if i < 3 else f"{i+1}."
        u = get_user(row["uid"])
        lines.append(f"{medal} {_display_name_short(u)} — {row['total_items']:,} آیتم")
    await reply("\n".join(lines))


# =============================================================================
# 🏅 دستاوردها
# =============================================================================
ACHIEVEMENT_LABELS = {
    "first_trade": "🤝 اولین معامله",
    "first_market_purchase": "🛒 اولین خرید بازار",
    "first_market_sale": "💰 اولین فروش بازار",
    "boss_slayer": "👹 نابودگر رئیس",
    "level_10": "⭐ رسیدن به سطح ۱۰",
    "level_25": "🌟 رسیدن به سطح ۲۵",
    "rich_1m": "💎 میلیونر (۱ میلیون سکه)",
}


async def handle_achievements(reply, uid, u):
    unlocked = set(get_user_achievements(uid))

    # چک خودکار چندتا دستاورد ساده بر اساس وضعیت فعلی
    if u["coins"] >= 1_000_000:
        unlock_achievement(uid, "rich_1m")
        unlocked.add("rich_1m")
    level = get_level(u["xp"])
    if level >= 10:
        unlock_achievement(uid, "level_10")
        unlocked.add("level_10")
    if level >= 25:
        unlock_achievement(uid, "level_25")
        unlocked.add("level_25")

    lines = ["🏅 دستاوردهای تو:\n"]
    for key, label in ACHIEVEMENT_LABELS.items():
        status = "✅" if key in unlocked else "🔒"
        lines.append(f"{status} {label}")
    await reply("\n".join(lines))


# ==============================================================================
# بخش ۱۳: مینی‌گیم‌های جدید
# ==============================================================================
# =============================================================================
# 🗺️ شکار گنج - ۵ نقطه، فقط یکیش گنج داره
# =============================================================================
active_treasure = {}  # {uid: True} یعنی منتظر انتخاب نقطه‌ست
TREASURE_ENTRY_FEE = 50
TREASURE_REWARD_RANGE = (100, 500)
TREASURE_TRAP_LOSS_RANGE = (30, 100)


async def handle_start_treasure(reply, uid, u):
    if u["coins"] < TREASURE_ENTRY_FEE:
        await reply(f"❌ برای شکار گنج {TREASURE_ENTRY_FEE} سکه ورودی لازمه، سکه‌ت کافی نیست.")
        return
    remove_coins(uid, TREASURE_ENTRY_FEE)
    active_treasure[uid] = True
    await reply(f"🗺️ نقشه‌ی گنج پیدا کردی! ({TREASURE_ENTRY_FEE} سکه ورودی گرفته شد)\n"
                "۵ نقطه‌ی حفاری هست:\n1️⃣ 2️⃣ 3️⃣ 4️⃣ 5️⃣\nیه عدد از ۱ تا ۵ رو بنویس تا اونجا رو حفر کنی.")


async def try_handle_treasure_input(reply, uid, text_raw):
    if uid not in active_treasure:
        return False
    stripped = text_raw.strip()
    if stripped not in ("1", "2", "3", "4", "5", "۱", "۲", "۳", "۴", "۵"):
        return False

    del active_treasure[uid]
    winning_spot = random.randint(1, 5)
    chosen = {"۱": 1, "۲": 2, "۳": 3, "۴": 4, "۵": 5}.get(stripped, int(stripped) if stripped.isdigit() else 0)

    if chosen == winning_spot:
        reward = random.randint(*TREASURE_REWARD_RANGE)
        add_coins(uid, reward)
        add_xp(uid, 20)
        await reply(f"💰 پیدا کردی! یه گنج تو نقطه‌ی {chosen} بود!\n💰 +{reward:,} سکه | ✨ +20 XP")
    else:
        if random.random() < 0.4:
            loss = random.randint(*TREASURE_TRAP_LOSS_RANGE)
            remove_coins(uid, loss)
            await reply(f"💥 تله بود! گنج تو نقطه‌ی {winning_spot} بود، تو {chosen} رو زدی.\n💸 -{loss:,} سکه از تله")
        else:
            await reply(f"🕳 هیچی نبود. گنج واقعی تو نقطه‌ی {winning_spot} بود.")
    return True


# =============================================================================
# 🔐 گاوصندوق - کد رو با ۵ تلاش محدود حدس بزن
# =============================================================================
active_safe = {}  # {uid: {"code": int, "tries": int, "max_tries": int}}
SAFE_ENTRY_FEE = 100
SAFE_MAX_TRIES = 5
SAFE_REWARD_RANGE = (400, 1200)


async def handle_start_safe(reply, uid, u):
    if u["coins"] < SAFE_ENTRY_FEE:
        await reply(f"❌ برای باز کردن گاوصندوق {SAFE_ENTRY_FEE} سکه ورودی لازمه.")
        return
    remove_coins(uid, SAFE_ENTRY_FEE)
    active_safe[uid] = {"code": random.randint(1, 50), "tries": 0, "max_tries": SAFE_MAX_TRIES}
    await reply(f"🔐 گاوصندوق قفله! ({SAFE_ENTRY_FEE} سکه ورودی گرفته شد)\n"
                f"کد بین ۱ تا ۵۰ ئه. فقط {SAFE_MAX_TRIES} تلاش داری. یه عدد بنویس!")


async def try_handle_safe_input(reply, uid, text_raw):
    if uid not in active_safe:
        return False
    if not text_raw.strip().isdigit():
        return False

    state = active_safe[uid]
    guess = int(text_raw.strip())
    state["tries"] += 1
    remaining = state["max_tries"] - state["tries"]

    if guess == state["code"]:
        reward = random.randint(*SAFE_REWARD_RANGE)
        add_coins(uid, reward)
        add_xp(uid, 35)
        del active_safe[uid]
        await reply(f"🔓 بازش کردی!! کد {state['code']} بود!\n💰 +{reward:,} سکه | ✨ +35 XP")
        return True

    if remaining <= 0:
        del active_safe[uid]
        await reply(f"🔒 تلاش‌هات تموم شد! کد درست {state['code']} بود. گاوصندوق برای همیشه قفل موند.")
        return True

    hint = "بزرگ‌تره 🔼" if guess < state["code"] else "کوچیک‌تره 🔽"
    await reply(f"❌ اشتباهه، {hint} ({remaining} تلاش مونده)")
    return True


# =============================================================================
# 🚔 دزد و پلیس - ریسک بالا برای دزدی بزرگ
# =============================================================================
THIEF_ENTRY_FEE = 80
THIEF_SUCCESS_CHANCE = 0.35
THIEF_REWARD_RANGE = (200, 700)
THIEF_CAUGHT_LOSS_RANGE = (100, 300)


async def handle_thief_police(reply, uid, u):
    if u["coins"] < THIEF_ENTRY_FEE:
        await reply(f"❌ برای این دزدی {THIEF_ENTRY_FEE} سکه هزینه‌ی تجهیزات لازمه.")
        return
    remove_coins(uid, THIEF_ENTRY_FEE)

    if random.random() < THIEF_SUCCESS_CHANCE:
        reward = random.randint(*THIEF_REWARD_RANGE)
        add_coins(uid, reward)
        add_xp(uid, 15)
        await reply(f"🕵️ موفق شدی فرار کنی با غنیمت!\n💰 +{reward:,} سکه | ✨ +15 XP")
    else:
        loss = random.randint(*THIEF_CAUGHT_LOSS_RANGE)
        remove_coins(uid, loss)
        await reply(f"🚔 پلیس گرفتت! جریمه شدی.\n💸 -{loss:,} سکه")


# =============================================================================
# ⚔️ دوئل - بین دو بازیکن واقعی (با ریپلای)
# =============================================================================
pending_duels = {}  # {target_uid: {"challenger": uid, "amount": int, "expires_ts": float}}
DUEL_EXPIRY_SECONDS = 120
DUEL_TAX_RATE = 0.05


async def handle_start_duel(reply, uid, u, text_raw, reply_sender_uid):
    if not reply_sender_uid:
        await reply("❌ باید روی پیام کسی که می‌خوای باهاش دوئل کنی ریپلای بزنی.")
        return
    if str(reply_sender_uid) == str(uid):
        await reply("❌ نمی‌تونی با خودت دوئل کنی.")
        return
    amount_str = "".join(ch for ch in text_raw if ch.isdigit())
    if not amount_str:
        await reply("بنویس: (ریپلای) «دوئل [مبلغ شرط]»")
        return
    amount = int(amount_str)
    if amount <= 0:
        await reply("مبلغ باید بزرگتر از صفر باشه.")
        return
    if u["coins"] < amount:
        await reply("❌ سکه‌ت برای این دوئل کافی نیست.")
        return

    pending_duels[reply_sender_uid] = {
        "challenger": uid, "amount": amount, "expires_ts": time.time() + DUEL_EXPIRY_SECONDS
    }
    await reply(f"⚔️ درخواست دوئل با شرط {amount:,} سکه فرستاده شد!\n"
                f"طرف مقابل باید تا {DUEL_EXPIRY_SECONDS} ثانیه بنویسه «قبول دوئل» تا شروع بشه.")


async def try_handle_duel_accept(reply, uid, u, text_raw):
    if text_raw.strip() != "قبول دوئل":
        return False
    challenge = pending_duels.get(uid)
    if not challenge:
        await reply("❌ هیچ درخواست دوئلی برات ثبت نشده.")
        return True
    if time.time() >= challenge["expires_ts"]:
        del pending_duels[uid]
        await reply("⌛ زمان قبول این دوئل تموم شده.")
        return True

    challenger = challenge["challenger"]
    amount = challenge["amount"]
    challenger_u = get_user(challenger)

    if u["coins"] < amount:
        del pending_duels[uid]
        await reply("❌ سکه‌ت برای قبول این دوئل کافی نیست.")
        return True
    if challenger_u["coins"] < amount:
        del pending_duels[uid]
        await reply("❌ طرف مقابل دیگه سکه‌ی کافی نداره، دوئل لغو شد.")
        return True

    del pending_duels[uid]

    winner = random.choice([uid, challenger])
    loser = challenger if winner == uid else uid
    pot = amount * 2
    tax = int(pot * DUEL_TAX_RATE)
    net = pot - tax

    remove_coins(uid, amount)
    remove_coins(challenger, amount)
    add_coins(winner, net)
    increment_wins(winner)

    await reply(f"⚔️💥 دوئل تموم شد!\n👑 برنده: آیدی {winner}\n💰 جایزه: {net:,} سکه (بعد از {int(DUEL_TAX_RATE*100)}٪ مالیات)")
    return True


# ==============================================================================
# بخش ۱۴: سیستم اسپاون
# ==============================================================================
SPAWN_INTERVAL_SECONDS = 30 * 60   # هر ۳۰ دقیقه
SPAWN_WINDOW_SECONDS = 30          # ۳۰ ثانیه در دسترسه

_spawn_background_tasks = set()


def _weighted_catalog_choice():
    """یه آیتم بر اساس وزن کمیابی انتخاب می‌کنه. OG فقط اگه هنوز کسی
    نگرفته باشدش، با شانس خیلی خیلی کم وارد استخر میشه."""
    pool = list(ITEM_CATALOG)
    weights = [RARITY_INFO[item["rarity"]]["spawn_weight"] for item in pool]

    if get_state("og_claimed", "0") != "1":
        pool = pool + [OG_ITEM]
        weights = weights + [3]  # شانس خیلی ناچیز نسبت به بقیه (مجموع وزن‌ها هزاران واحده)

    return random.choices(pool, weights=weights, k=1)[0]


def get_current_spawn():
    name = get_state("spawn_item_name")
    if not name:
        return None
    expires = float(get_state("spawn_expires_ts", "0"))
    if time.time() >= expires:
        return None
    return {
        "name": name,
        "category": get_state("spawn_category", ""),
        "rarity": get_state("spawn_rarity", "Common"),
        "price": int(get_state("spawn_price", "0")),
        "xp": int(get_state("spawn_xp", "0")),
        "expires_ts": expires,
    }


def clear_spawn():
    set_state("spawn_item_name", "")
    set_state("spawn_expires_ts", "0")


async def handle_claim_spawn(reply, uid):
    spawn = get_current_spawn()
    if not spawn:
        await reply("😅 الان هیچی اسپون نشده. منتظر اسپون بعدی باش (هر ۳۰ دقیقه یه‌بار).")
        return

    clear_spawn()  # فوراً پاک میشه تا کس دیگه‌ای هم‌زمان نگیرتش (race condition)

    category = "og" if spawn["rarity"] == "OG" else "catalog"
    add_item(uid, category, spawn["name"], 1)
    add_xp(uid, spawn["xp"])

    if spawn["rarity"] == "OG":
        set_state("og_claimed", "1")
        await reply(f"🌟👑 باورنکردنیه!! تو تنها آیتم OG کل بازی رو گرفتی: {spawn['name']}!\n"
                    f"این آیتم دیگه هیچ‌وقت اسپون نمیشه، مال تو شد برای همیشه!\n✨ +{spawn['xp']:,} XP")
    else:
        rarity_label = RARITY_INFO[spawn["rarity"]]["label"]
        await reply(f"🎁 گرفتیش! {spawn['name']} ({rarity_label})\n💎 ارزش: {spawn['price']:,} سکه | ✨ +{spawn['xp']} XP\n"
                    f"می‌تونی تو بازار سیاه بفروشیش: «فروش {spawn['name']} [قیمت]»")


async def handle_spawn_status(reply):
    spawn = get_current_spawn()
    if not spawn:
        await reply("😴 الان چیزی اسپون نشده. هر ۳۰ دقیقه یه آیتم تصادفی ظاهر میشه، منتظر باش!")
        return
    remaining = max(0, int(spawn["expires_ts"] - time.time()))
    rarity_label = RARITY_INFO[spawn["rarity"]]["label"]
    await reply(f"✨ یه آیتم اسپون شده: {spawn['name']} ({rarity_label})\n"
                f"⏳ {remaining} ثانیه فرصت داری، بنویس «بگیرش»!")


async def _do_spawn(bot):
    item = _weighted_catalog_choice()

    set_state("spawn_item_name", item["name"])
    set_state("spawn_category", item["category"])
    set_state("spawn_rarity", item["rarity"])
    set_state("spawn_price", item["price"])
    set_state("spawn_xp", item["xp"])
    set_state("spawn_expires_ts", time.time() + SPAWN_WINDOW_SECONDS)

    rarity_label = RARITY_INFO[item["rarity"]]["label"]

    # هم به پیوی کسایی که استارت زدن، هم به همه‌ی گروه‌های ثبت‌شده می‌فرسته
    pv_targets = [u["pv_chat_id"] for u in all_users() if u.get("pv_chat_id")]
    group_targets = [g["chat_id"] for g in all_registered_groups()]
    targets = pv_targets + group_targets

    async def send_fn(chat_id, text):
        await maybe_await(bot.send_message(chat_id, text))

    text = (f"✨ یه آیتم اسپون شد: {item['name']} ({rarity_label})!\n"
            f"⏳ فقط {SPAWN_WINDOW_SECONDS} ثانیه فرصت داری، زودتر بنویس «بگیرش»!")
    await rate_limited_broadcast(send_fn, targets, text)

    await asyncio.sleep(SPAWN_WINDOW_SECONDS)
    # اگه هنوزم اسپون فعاله (کسی نگرفته)، پاکش کن
    if get_current_spawn() and get_current_spawn()["name"] == item["name"]:
        clear_spawn()


async def _spawn_loop(bot):
    # اولین اسپون تقریباً فوریه (بعد از یه مکث کوتاه که ربات کامل بالا بیاد)
    # نه اینکه ۳۰ دقیقه صبر کنیم تا چیزی نشون داده بشه
    await asyncio.sleep(20)
    try:
        await _do_spawn(bot)
    except Exception as e:
        print("خطا تو اولین اسپون:", e)

    while True:
        await asyncio.sleep(SPAWN_INTERVAL_SECONDS)
        try:
            await _do_spawn(bot)
        except Exception as e:
            print("خطا تو اسپون:", e)


def start_spawn_loop(bot):
    task = asyncio.create_task(_spawn_loop(bot))
    _spawn_background_tasks.add(task)
    task.add_done_callback(_spawn_background_tasks.discard)


# ==============================================================================
# بخش ۱۵: روتر اصلی و اجرای ربات
# ==============================================================================
bot = Robot(token=BOT_TOKEN)
init_db()

_background_jobs_started = False


async def _periodic_auction_check():
    while True:
        await asyncio.sleep(60)
        try:
            await process_expired_auctions()
        except Exception as e:
            print("خطا تو چک کردن مزایده‌های تموم‌شده:", e)


def get_uid(message):
    uid = getattr(message, "sender_id", None)
    return uid if uid else message.chat_id


def is_group(chat_id):
    return str(chat_id).startswith("g")


# --- ضد پیام تکراری (چند instance هم‌زمان یا fetch دوباره) ---
processed_message_ids = set()
MAX_PROCESSED_IDS = 5000

# {message_id: sender_uid} — برای اینکه بفهمیم ریپلای‌شده روی پیام کدوم کاربره
message_senders = {}


@bot.on_message()
async def dispatcher(bot: Robot, message: Message):
    global _background_jobs_started
    if not _background_jobs_started:
        # این کارای پس‌زمینه (اسپاون، چک مزایده) رو همینجا استارت می‌زنیم -
        # نه تو یه ترد/loop جدا - چون این تابع دقیقاً رو همون event loopـی
        # اجرا میشه که خود ربات (bot.send_message و بقیه) روش کار می‌کنه.
        # این‌جوری هیچ‌وقت قاطی loop پیش نمیاد.
        _background_jobs_started = True
        asyncio.create_task(_spawn_loop(bot))
        asyncio.create_task(_periodic_auction_check())
        print("✅ کارای پس‌زمینه (اسپاون + چک مزایده) استارت خوردن")

    dedupe_id = getattr(message, "message_id", None)
    if dedupe_id:
        if dedupe_id in processed_message_ids:
            return
        processed_message_ids.add(dedupe_id)
        if len(processed_message_ids) > MAX_PROCESSED_IDS:
            processed_message_ids.clear()

    text_raw = (message.text or "").strip()
    chat_id = str(message.chat_id)
    uid = str(get_uid(message))

    msg_id = getattr(message, "message_id", None)
    if msg_id:
        message_senders[msg_id] = uid
        if len(message_senders) > 5000:
            message_senders.clear()

    reply_to_id = getattr(message, "reply_to_message_id", None)
    reply_sender_uid = message_senders.get(reply_to_id) if reply_to_id else None

    async def reply(text, **kwargs):
        return await safe_reply(bot, message, text, **kwargs)

    # ---- /start ----
    if text_raw == "/start":
        get_user(uid)
        if not is_group(chat_id):
            update_user(uid, pv_chat_id=chat_id)
        await send_start(reply)
        return

    u = get_user(uid)
    increment_messages(uid)
    if not is_group(chat_id) and not u.get("pv_chat_id"):
        update_user(uid, pv_chat_id=chat_id)

    if is_group(chat_id):
        add_group_member(chat_id, uid)
        increment_group_messages(chat_id)

    # ---- الان که u رو داریم، reply رو دوباره تعریف می‌کنیم تا مود روش اثر بذاره ----
    async def reply(text, **kwargs):
        flavored = apply_mood_flavor(text, u.get("mood") or "کلاسیک")
        return await safe_reply(bot, message, flavored, **kwargs)

    # ---- هر ۱۰۰ پیامی که کل ربات پردازش می‌کنه، یه نق معروف می‌زنه 😔 ----
    total_msgs = increment_global_counter("total_messages_processed")
    if total_msgs % 100 == 0:
        await reply("منو یه استارت میکنین؟😔")

    # ---- قفل‌های گروه (فقط برای غیرادمین‌ها) ----
    if is_group(chat_id) and not can_manage_group(chat_id, uid):
        locks = get_locks(chat_id)
        low = text_raw.lower()

        if locks.get("link") and ("http://" in low or "https://" in low or "t.me/" in low or "rubika.ir/" in low):
            deleted = await try_delete_message(bot, chat_id, msg_id)
            if deleted:
                await reply("🔗 لینک ارسالی‌ات به دلیل فعال بودن قفل لینک، حذف شد.")
            else:
                await reply("🔗 ارسال لینک در این گروه غیرمجازه (قفل لینک فعاله)!")
            return

        if locks.get("badword") and BADWORDS and any(bw in low for bw in BADWORDS):
            count = add_warn(chat_id, uid)
            await reply(f"🤬 لطفاً از الفاظ نامناسب استفاده نکن. (اخطار {count})")
            return

        if locks.get("mention") and text_raw.count("@") >= 4:
            count = add_warn(chat_id, uid)
            await reply(f"📢 منشن‌دادن بیش‌ازحد مجاز نیست. (اخطار {count})")
            return

        if locks.get("forward") and is_forwarded_message(message):
            deleted = await try_delete_message(bot, chat_id, msg_id)
            if deleted:
                await reply("↪️ فوروارد در این گروه غیرمجازه، پیامت حذف شد.")
            else:
                await reply("↪️ فوروارد در این گروه غیرمجازه!")
            return

    # =========================================================================
    # ---- اگه وسط یکی از بازی‌های چندمرحله‌ایه، اولویت با ادامه‌ی همون بازیه ----
    # =========================================================================
    if await try_handle_pending_game_input(reply, uid, u, text_raw):
        return

    if await try_handle_treasure_input(reply, uid, text_raw):
        return

    if await try_handle_safe_input(reply, uid, text_raw):
        return

    if await try_handle_duel_accept(reply, uid, u, text_raw):
        return

    # =========================================================================
    # ---- مود (بررسی زودهنگام چون startswith مشخصیه) ----
    # =========================================================================
    if text_raw.startswith("مود"):
        await handle_mood_set(reply, uid, text_raw)
        return

    # =========================================================================
    # ---- تنظیمات پروفایل (پیشوندهای مشخص) ----
    # =========================================================================
    if text_raw.startswith("تنظیم اسم"):
        await handle_set_name(reply, uid, text_raw); return
    if text_raw.startswith("تنظیم ایموجی"):
        await handle_set_emoji(reply, uid, text_raw); return
    if text_raw.startswith("تنظیم لقب"):
        await handle_set_title(reply, uid, text_raw); return
    if text_raw.startswith("تنظیم اصل"):
        await handle_set_origin(reply, uid, text_raw); return

    # =========================================================================
    # ⚠️ نکته‌ی مهم رفع باگ: «آمار گروه» / «آمار اعضا» حتما باید قبل از
    # «آمار» (شخصی) چک بشن، چون هردو شامل کلمه‌ی «آمار» هستن.
    # =========================================================================
    if "آمار گروه" in text_raw or "آمار اعضا" in text_raw:
        if not is_group(chat_id):
            await reply("❌ این دستور فقط داخل گروه جواب میده")
        else:
            await handle_group_stats(reply, chat_id)
        return

    if "آمار کلی ربات" in text_raw:
        await handle_bot_wide_stats(reply, uid)
        return

    if "پروفایل" in text_raw:
        await handle_profile(reply, uid, u); return

    if "آمار" in text_raw:  # آمار شخصی — بعد از چک‌های بالا امن شد
        await handle_personal_stats(
            reply, chat_id, uid, u, is_group_admin, is_owner
        )
        return

    # ⚠️ لیدربوردهای اختصاصی باید قبل از «رتبه» عمومی چک بشن
    if "رتبه سکه" in text_raw:
        await handle_leaderboard_coins(reply); return
    if "رتبه برد" in text_raw:
        await handle_leaderboard_wins(reply); return
    if "رتبه آیتم" in text_raw:
        await handle_leaderboard_items(reply); return
    if "رتبه" in text_raw:
        await handle_leaderboard(reply); return

    if "دستاوردها" in text_raw:
        await handle_achievements(reply, uid, u); return

    # =========================================================================
    # ---- هدیه / انتقال بین کاربران ----
    # =========================================================================
    if text_raw.startswith("هدیه سکه"):
        await handle_gift_coins(reply, uid, u, text_raw, reply_sender_uid); return
    if text_raw.startswith("هدیه ایکس پی") or text_raw.startswith("هدیه اکس پی"):
        await handle_gift_xp(reply, uid, u, text_raw, reply_sender_uid); return
    if text_raw.startswith("هدیه آیتم"):
        await handle_gift_item(reply, uid, text_raw, reply_sender_uid); return

    # =========================================================================
    # ---- بازار سیاه ----
    # =========================================================================
    if text_raw.startswith("خرید بازار"):
        await handle_market_buy(reply, uid, u, text_raw); return
    if text_raw.startswith("لغو آگهی"):
        await handle_market_cancel(reply, uid, text_raw); return
    if text_raw.startswith("مزایده"):
        await handle_auction(reply, uid, text_raw); return
    if text_raw.startswith("پیشنهاد"):
        await handle_bid(reply, uid, u, text_raw); return
    if text_raw.startswith("فروش"):
        await handle_sell(reply, uid, text_raw); return
    if "تاریخچه معاملات" in text_raw:
        await handle_market_history(reply, uid); return
    if text_raw.startswith("بازار"):
        await handle_market_list(reply, text_raw); return

    # =========================================================================
    # ---- سیستم اسپاون ----
    # =========================================================================
    if text_raw == "بگیرش":
        await handle_claim_spawn(reply, uid); return
    if "وضعیت اسپاون" in text_raw or "وضعیت اسپون" in text_raw:
        await handle_spawn_status(reply); return

    # =========================================================================
    # ---- کلن/گیلد ----
    # =========================================================================
    if text_raw.startswith("کلن بساز"):
        await handle_create_clan(reply, uid, text_raw); return
    if text_raw.startswith("عضویت در کلن"):
        await handle_join_clan(reply, uid, text_raw); return
    if text_raw == "خروج از کلن":
        await handle_leave_clan(reply, uid); return
    if text_raw == "کلن‌ها" or text_raw == "کلن ها":
        await handle_clan_list(reply); return
    if text_raw == "کلن من":
        await handle_my_clan(reply, uid); return

    # =========================================================================
    # ---- اقتصاد ----
    # =========================================================================
    if "جایزه روزانه" in text_raw:
        await handle_daily_reward(reply, uid, u); return

    # ⚠️ فروشگاه‌های اختصاصی (پنالتی/رولت) باید قبل از «خرید» و «فروشگاه» عمومی چک بشن
    if text_raw.startswith("خرید پنالتی"):
        await handle_buy_penalty_item(reply, uid, u, text_raw); return

    if text_raw.startswith("خرید رولت"):
        await handle_buy_roulette_item(reply, uid, u, text_raw); return

    if text_raw.startswith("خرید"):
        await handle_buy(reply, uid, u, text_raw); return

    if "فروشگاه پنالتی" in text_raw:
        await handle_penalty_shop(reply); return

    if "فروشگاه رولت" in text_raw:
        await handle_roulette_shop(reply); return

    if "فروشگاه" in text_raw:
        await handle_shop(reply); return

    # ⚠️ «شکار گنج» باید قبل از «شکار» عمومی چک بشه
    if "شکار گنج" in text_raw:
        await handle_start_treasure(reply, uid, u); return

    if "شکار" in text_raw:
        await handle_hunt(reply, uid, u); return

    if "گاوصندوق" in text_raw:
        await handle_start_safe(reply, uid, u); return

    if "دزد و پلیس" in text_raw:
        await handle_thief_police(reply, uid, u); return

    if text_raw.startswith("دوئل"):
        await handle_start_duel(reply, uid, u, text_raw, reply_sender_uid); return

    # =========================================================================
    # ---- بازی‌ها ----
    # =========================================================================
    if "حدس عدد" in text_raw:
        await handle_start_guess(reply, uid); return

    if text_raw.startswith("رولت روسی"):
        await handle_roulette(reply, uid, u, text_raw); return

    if "پنالتی" in text_raw:
        await handle_start_penalty(reply, uid, text_raw); return

    if "فرار از زندان" in text_raw:
        await handle_start_prison(reply, uid); return

    if "جرعت حقیقت" in text_raw or "جرأت حقیقت" in text_raw:
        await handle_start_truth_dare(reply, uid); return

    if "دروغ سنج" in text_raw or "دروغ‌سنج" in text_raw:
        await handle_start_lie_detector(reply, uid); return

    if text_raw == "دوز":
        await handle_start_tictactoe(reply, uid); return

    if "مسابقه" in text_raw:
        await handle_start_quiz(reply, uid); return

    if "معما" in text_raw:
        await handle_riddle(reply); return

    if "چالش" in text_raw:
        await handle_challenge(reply); return

    if "جوک" in text_raw:
        await handle_joke(reply); return

    if "دوز غول" in text_raw:
        await handle_start_giant_tictactoe(reply, uid); return

    if "چرخ شانس" in text_raw:
        await handle_party_wheel(reply, uid); return

    if "جعبه شانس" in text_raw:
        await handle_party_box(reply, uid); return

    if "وضعیت پارتی" in text_raw:
        await handle_party_status(reply); return

    if text_raw == "حمله":
        await handle_attack(reply, uid); return

    if "وضعیت رئیس" in text_raw:
        await handle_boss_status(reply); return

    if text_raw.startswith("شرط"):
        await handle_bet(reply, uid, u, text_raw); return

    if "تاس" in text_raw:
        await handle_dice(reply); return

    if "شانس" in text_raw:
        await handle_luck(reply); return

    if "فال" in text_raw:
        await handle_fortune(reply); return

    # =========================================================================
    # ---- مدیریت گروه ----
    # =========================================================================
    if text_raw == "فعال":
        if not is_group(chat_id):
            await reply("❌ این دستور فقط داخل گروه کار می‌کنه.")
        else:
            await handle_register_group(reply, chat_id, uid); return
        return

    if "لیست قفل" in text_raw:
        await handle_list_locks(reply, chat_id); return

    if "لیست ادمین" in text_raw:
        await handle_list_admins(reply, chat_id); return

    if text_raw.startswith("باز کردن قفل"):
        await handle_lock_toggle(reply, chat_id, uid, text_raw, enable=False); return

    if text_raw.startswith("قفل"):
        await handle_lock_toggle(reply, chat_id, uid, text_raw, enable=True); return

    if text_raw == "ادمین" and reply_to_id:
        await handle_make_admin(reply, chat_id, uid, reply_sender_uid); return

    if text_raw == "حذف کن" and reply_to_id:
        await handle_remove_member(reply, bot, message, chat_id, uid, reply_sender_uid); return

    if text_raw == "سکوت" and reply_to_id:
        await handle_mute(reply, chat_id, uid, reply_sender_uid); return

    if text_raw == "رفع سکوت" and reply_to_id:
        await handle_unmute(reply, chat_id, uid, reply_sender_uid); return

    if text_raw.startswith("انتقال مالکیت ربات") or text_raw.startswith("انتقال مالکیت"):
        if not reply_to_id:
            await reply("❌ باید روی پیام کسی که می‌خوای مالک بشه ریپلای بزنی.")
        else:
            await handle_transfer_ownership(reply, chat_id, uid, reply_sender_uid)
        return

    # =========================================================================
    # ---- فقط ادمین اصلی ربات (ریپلای یا آیدی مستقیم — رفع باگ اصلی) ----
    # =========================================================================
    if text_raw.startswith("شروع ادمین ابیوز"):
        await handle_start_admin_abuse(reply, bot, uid, text_raw); return

    if text_raw.startswith("شروع سکه پارتی"):
        await handle_start_coin_party(reply, bot, uid, text_raw); return

    if text_raw.startswith("شروع ایکس پی پارتی") or text_raw.startswith("شروع اکس پی پارتی"):
        await handle_start_xp_party(reply, bot, uid, text_raw); return

    if text_raw in ("توقف پارتی", "پایان پارتی"):
        await handle_stop_party(reply, uid); return

    if text_raw.startswith("ادمین ربات بشه"):
        await handle_grant_coin_admin(reply, uid, text_raw, reply_sender_uid); return

    if text_raw == "حذف ادمین سکه" and reply_to_id:
        await handle_revoke_coin_admin(reply, uid, reply_sender_uid); return

    if text_raw.startswith("اسپان کارت") or text_raw.startswith("اسپون کارت"):
        await handle_admin_spawn_card(reply, uid, text_raw); return

    if text_raw.startswith("همگانی"):
        await handle_broadcast(reply, bot, uid, text_raw); return

    if text_raw.startswith("اضافه کردن ایکس پی") or text_raw.startswith("اضافه کردن اکس پی"):
        await handle_add_xp(reply, uid, text_raw, reply_sender_uid); return

    if text_raw.startswith("کم کردن ایکس پی") or text_raw.startswith("کم کردن اکس پی"):
        await handle_remove_xp(reply, uid, text_raw, reply_sender_uid); return

    if text_raw.startswith("اضافه کردن سکه"):
        await handle_add_coins(reply, uid, text_raw, reply_sender_uid); return

    if text_raw.startswith("کم کردن سکه"):
        await handle_remove_coins(reply, uid, text_raw, reply_sender_uid); return

    if text_raw.startswith("ریست کاربر"):
        await handle_reset_user(reply, uid, text_raw, reply_sender_uid); return

    # =========================================================================
    # ---- عمومی / گپ ----
    # =========================================================================
    if text_raw in ("قابلیت ها", "قابلیت‌ها", "کمک"):
        await send_help(reply); return

    if "تاریخ" in text_raw:
        await handle_date(reply); return

    if "ساعت" in text_raw:
        await handle_time(reply); return

    if text_raw in ("سلام", "سلام پرسیا", f"سلام {BOT_NAME_TRIGGER}"):
        await handle_greeting(reply, u); return

    if "خوبی" in text_raw or "حالت چطوره" in text_raw:
        await handle_how_are_you(reply, u); return

    calc_result = try_calculate(text_raw)
    if calc_result:
        await reply(calc_result); return

    if text_raw.startswith("پیام به ادمین") or text_raw.startswith("پیام به مالک"):
        await handle_contact_admin(reply, bot, uid, u, text_raw); return

    if "مالک ربات" in text_raw:
        await reply(f"👑 سازنده‌ی من: {OWNER_USERNAME}\n{OWNER_LINK}")
        return

    # =========================================================================
    # ---- گپ آزاد (کلی جواب برای سلام/خوبی/چخبر/عه/نه/آره/جالبه و...) ----
    # این باید نزدیک انتهای روتر باشه تا هیچ دستور دیگه‌ای رو قاپ نزنه
    # =========================================================================
    smalltalk_reply = try_smalltalk(text_raw)
    if smalltalk_reply:
        await reply(smalltalk_reply); return

    # ---- صدا زدن ربات با اسم (پرسیا / وانتا) ----
    name_reply = try_name_call(text_raw)
    if name_reply:
        await reply(name_reply); return

    # اگه هیچ‌کدوم مچ نشد، سکوت می‌کنیم (مثل قبل)


# ---------------------------------------------------------------------------
# سرور کوچیک keep-alive برای هاستینگ‌های رایگان (Render/Railway)
# ---------------------------------------------------------------------------
def start_keep_alive_server():
    port_env = os.environ.get("PORT")
    if not port_env:
        return
    try:
        port = int(port_env)
    except ValueError:
        return

    class PingHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK - VantaPersiaBot v2 is alive")

        def log_message(self, format, *args):
            pass

    try:
        server = HTTPServer(("0.0.0.0", port), PingHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"✅ keep-alive روی پورت {port} بالا اومد")
    except Exception as e:
        print("خطا در keep-alive:", e)


if __name__ == "__main__":
    start_keep_alive_server()
    print("🚀 VANTA PERSIA v2 در حال اجراست...")
    bot.run()
