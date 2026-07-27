#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 VANTA PERSIA BOT — v2  (تک‌فایلی، برای آپلود مستقیم روی گیت‌هاب: bot.py)
================================================================================
راه‌اندازی سریع:
    pip install rubka python-dotenv Pillow
    cp .env.example .env      (توکن و آیدی ادمین‌ها رو توش بذار)
    python bot.py

نکته‌ی مهم معماری (باگی که تو v1 بود و اینجا رفع شده):
دستورات چندکلمه‌ای و مشخص‌تر (مثل «آمار گروه») همیشه قبل از دستورات کلی‌تری
که زیرمجموعه‌شونن (مثل «آمار») چک می‌شن، تا substring یه کلمه باعث قاطی
شدن دستورات نشه.
================================================================================
"""

# =============================================================================
# ایمپورت‌های خارجی
# =============================================================================
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
# بخش ۱: تنظیمات (از .env خونده میشه)
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
                created_at INTEGER DEFAULT 0
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


# ==============================================================================
# بخش ۳: محتوای ثابت (مود، فروشگاه، معما، جرعت‌حقیقت، فرار از زندان و...)
# ==============================================================================
# =============================================================================
# 🎭 سیستم مود - ۱۲ مود متنوع، لحن‌های بزرگسال‌تر و کمتر بچگانه
# =============================================================================
MOODS = {
    "کلاسیک": {
        "label": "🎩 کلاسیک",
        "desc": "مودب و متعادل، برای گروه‌های معمولی",
        "greet": [
            "سلام، خوش اومدی 🎩", "درود، روز خوبی داشته باشی 🌹",
            "سلام و احترام، در خدمتم 🤝",
        ],
        "how": ["خوبم، ممنون که پرسیدی. تو چطوری؟", "رو به‌راهم، امیدوارم تو هم خوب باشی."],
    },
    "سیگما": {
        "label": "🗿 سیگما",
        "desc": "خشک، مستقیم، بدون احساسات اضافه",
        "greet": ["سلام. برو سر اصل مطلب.", "اومدی. خب. بگو چی می‌خوای."],
        "how": ["حالم همیشه ثابته. ضعف تعریف نشده تو من.", "نیازی به احوال‌پرسی نیست، برو جلو."],
    },
    "طنز تلخ": {
        "label": "🖤 طنز تلخ",
        "desc": "کنایه‌دار و خنده‌دار ولی بزرگسالانه، نه شکلک‌بازی بچگانه",
        "greet": ["سلام. باز که اومدی سراغ من، انگار زندگی واقعی کمبود داره 😏",
                   "به‌به، یکی دیگه که وقتشو با یه ربات پر می‌کنه. خوش اومدی."],
        "how": ["از وضعیت جهان بهترم، ولی این ملاک بالایی نیست.", "زنده‌ام، که خودش دستاورده."],
    },
    "حرفه‌ای": {
        "label": "🧑‍💼 حرفه‌ای",
        "desc": "لحن رسمی و کاری، مناسب گروه‌های کاری/آموزشی",
        "greet": ["سلام، وقت بخیر. چطور می‌تونم کمک کنم؟", "درود، آماده‌ی ارائه‌ی خدمات هستم."],
        "how": ["وضعیت مطلوبه، سپاسگزارم. شما چطورید؟", "همه‌چیز طبق روال پیش می‌ره."],
    },
    "اسرارآمیز": {
        "label": "🔮 اسرارآمیز",
        "desc": "مرموز و فلسفی، جواب‌های دو پهلو",
        "greet": ["سلام... تقدیر تو رو به اینجا کشوند 🔮", "درِ این گفتگو باز شد. کنجکاوم چی می‌خوای."],
        "how": ["حالم بین لحظه‌هاست، جایی که زمان معنا نداره.", "هر روز رازیه؛ امروز هم یکی از اوناست."],
    },
    "پادشاهی": {
        "label": "👑 پادشاهی",
        "desc": "لحن دربار و شاهانه",
        "greet": ["درود بر تو، ای مهمانِ این قلمرو 👑", "به کاخ ما خوش اومدی، عالیجناب."],
        "how": ["احوال این خادم شما همواره روبه‌راهست.", "در سایه‌ی این تاج، هر روز خوش می‌گذره."],
    },
    "دوستانه": {
        "label": "🤝 دوستانه",
        "desc": "گرم و صمیمی بدون شکلک‌بازی زیاد",
        "greet": ["سلام رفیق، خوش اومدی.", "به‌به، دلم برات تنگ شده بود."],
        "how": ["خوبم، ممنون. خودت چطوری؟", "بد نیستم، تو بگو از خودت."],
    },
    "جنگجو": {
        "label": "⚔️ جنگجو",
        "desc": "پرانرژی و حماسی، برای گروه‌های گیمینگ",
        "greet": ["سلام سرباز! آماده‌ی نبرد امروزی؟ ⚔️", "درود بر رزمنده‌ی تازه‌وارد!"],
        "how": ["آماده و مسلح، منتظر فرمانم.", "روحیه‌ام همیشه رزمیه."],
    },
    "فلسفی": {
        "label": "📖 فلسفی",
        "desc": "آرام و تأمل‌برانگیز",
        "greet": ["سلام. هر ورودی، آغاز یه فصل تازه‌ست.", "درود. زمان دوباره ما رو کنار هم آورد."],
        "how": ["حال من بازتابیه از لحظه‌ای که توش هستم.", "خوبم، به همون اندازه که پذیرفتنش رو یاد گرفتم."],
    },
    "طعنه‌آمیز": {
        "label": "😏 طعنه‌آمیز",
        "desc": "شیطون و کنایه‌دار ولی نه توهین‌آمیز",
        "greet": ["اوه سلام، افتخار دادی 😏", "به به، بالاخره یادت افتاد اینجا رو"],
        "how": ["عالیم، مثل همیشه که از حد تو بهترم 😏", "خوبم، نگران من نباش."],
    },
    "آرامش‌بخش": {
        "label": "🍃 آرامش‌بخش",
        "desc": "ملایم و آروم",
        "greet": ["سلام آروم و گرم به تو 🍃", "خوش اومدی، امیدوارم آرامش داشته باشی."],
        "how": ["آرومم، مثل یه بعدازظهر بی‌عجله.", "خوبم، نفس عمیق بکش، همه‌چی روبه‌راهه."],
    },
    "بامزه": {
        "label": "🤪 بامزه",
        "desc": "شوخ و خنده‌دار، مود پیش‌فرض قبلی",
        "greet": ["هاااای چه خبرا رفیق باحال من؟ 🤪", "سلاااام سلاااام دلم واسه خنده‌هامون تنگ شده بود!"],
        "how": ["خوبم مثل موز رسیده 🍌", "عالی، آماده‌ی خل‌بازی 🤪"],
    },
}
MOOD_NAMES = list(MOODS.keys())
DEFAULT_MOOD = "کلاسیک"  # قبلا بامزه بود؛ حالا پیش‌فرض بالغانه‌تره

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
    await reply(f"""🫡 لیست کامل قابلیت‌های {BOT_BRAND} 👇

💬 عمومی
سلام / خوبی / جوک / شانس / فال / تاس
تاریخ (شمسی+میلادی) | ساعت

🎭 مود ({len(MOOD_NAMES)} حالت)
بنویس «مود [اسم]» — گزینه‌ها: {mood_list}

👤 پروفایل و آمار
پروفایل | آمار | رتبه
تنظیم اسم [نام] | تنظیم ایموجی [شکلک] | تنظیم لقب [متن] | تنظیم اصل [متن]

💰 اقتصاد
جایزه روزانه | فروشگاه | خرید [آیتم] | شکار

🎮 بازی‌ها
حدس عدد | دوز | مسابقه | معما
⚽ پنالتی | فروشگاه پنالتی | خرید پنالتی [آیتم]
🔫 رولت روسی [مبلغ] | فروشگاه رولت | خرید رولت [آیتم]
🚔 فرار از زندان (داستان شاخه‌ای)
🎭 جرعت حقیقت | 🕵️ دروغ سنج

📂 گروه‌ها (فقط داخل گروه)
آمار گروه | آمار اعضا | لیست قفل‌ها | لیست ادمین‌ها

🛡️ مدیریت گروه (ادمین گروه یا مالک)
فعال (ثبت گروه)
(ریپلای) + «ادمین» / «حذف کن» / «سکوت» / «رفع سکوت»
قفل [لینک/اسپم/منشن/فوروارد/الفاظ نامناسب]
باز کردن قفل [همون‌ها]

👑 فقط ادمین اصلی ربات
همگانی [متن] | آمار کلی ربات
(ریپلای یا با آیدی) «اضافه کردن سکه [مقدار]» / «کم کردن سکه [مقدار]» / «ریست کاربر»
مثال بدون ریپلای: اضافه کردن سکه 500 u0Hx... (آیدی رو بذار)""")


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
    name = display_name(u, uid)
    emoji = display_emoji(u)
    items = get_items(uid, "shop")
    creatures = get_items(uid, "creature")
    penalty_items = get_items(uid, "penalty")

    items_txt = "، ".join(f"{i['item_name']}×{i['qty']}" for i in items) or "چیزی نداری"
    creatures_txt = "، ".join(f"{i['item_name']}×{i['qty']}" for i in creatures) or "چیزی نداری"
    penalty_txt = "، ".join(f"{i['item_name']}×{i['qty']}" for i in penalty_items) or "چیزی نداری"

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
    reward = DAILY_BASE_REWARD + bonus_days * DAILY_STREAK_BONUS

    add_coins(uid, reward)
    update_user(uid, last_daily_date=today, streak=new_streak)

    await reply(f"""🎁 جایزه‌ی روزانه گرفتی!

💰 مقدار: {reward:,} سکه
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


HUNT_COOLDOWN_SECONDS = 1800  # ۳۰ دقیقه


async def handle_hunt(reply, uid, u):
    last_hunt = u.get("last_hunt_ts") or 0
    # چون last_hunt_ts ستون جدا نداره در نسخه فعلی جدول، از یه فیلد ساده تو title-free استفاده نمی‌کنیم؛
    # این مقدار رو می‌تونی بعدا به جدول اضافه کنی. فعلا شکار بدون کول‌داون سخت‌گیرانه کار می‌کنه.
    roll = random.random()
    if roll < 0.55:
        await reply("🌲 امروز شکاری پیدا نکردی، بازم امتحان کن.")
        return
    creature = random.choice(CREATURES)
    coins_reward = random.randint(40, 150)
    add_item(uid, "creature", creature, 1)
    add_coins(uid, coins_reward)
    await reply(f"🏹 یه {creature} شکار کردی!\n💰 پاداش: {coins_reward} سکه")


# ==============================================================================
# بخش ۹: هندلرهای مدیریت گروه
# ==============================================================================
def is_bot_admin(uid):
    return str(uid) in {str(a) for a in ADMIN_IDS}


def is_owner(chat_id, uid):
    g = get_group(chat_id)
    return bool(g and str(g["owner_uid"]) == str(uid))


def can_manage_group(chat_id, uid):
    return is_bot_admin(uid) or is_owner(chat_id, uid) or is_group_admin(chat_id, uid)


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
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
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
    if not is_bot_admin(uid):
        await reply("⛔ این دستور فقط برای ادمین اصلی ربات‌ه.")
        return
    rest = text_raw.replace("کم کردن سکه", "", 1).strip()
    target_uid = resolve_target_uid(rest, reply_sender_uid)
    amount = extract_amount(rest)
    if not target_uid or not amount:
        await reply("بنویس: (ریپلای) «کم کردن سکه [مقدار]» یا بدون ریپلای «کم کردن سکه [مقدار] [آیدی]»")
        return
    new_balance = remove_coins(target_uid, amount)
    await reply(f"✅ {amount:,} سکه از آیدی {target_uid} کم شد. (موجودی فعلی: {new_balance:,})")


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
# بخش ۱۰: بازی‌ها (حدس عدد، پنالتی، رولت، فرار از زندان، جرعت‌حقیقت، دروغ‌سنج، دوز، مسابقه، معما)
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
        reward = max(10, 100 - state["tries"] * 10)
        add_coins(uid, reward)
        add_xp(uid, 15)
        tries = state["tries"]
        del active_guess[uid]
        await reply(f"🎉 آفرین! درست حدس زدی (تو {tries} بار حدس زدی)\n💰 جایزه: {reward} سکه | ✨ +15 XP")
    elif guess < state["answer"]:
        await reply("🔼 عدد من بزرگ‌تره!")
    else:
        await reply("🔽 عدد من کوچیک‌تره!")


# =============================================================================
# ⚽ پنالتی + فروشگاه پنالتی
# =============================================================================
PENALTY_DIRECTIONS = ["چپ", "وسط", "راست"]


async def handle_start_penalty(reply, uid):
    active_penalty.add(uid)
    await reply("⚽ ضربه‌ی پنالتی بزن! کدوم سمت شوت می‌کنی؟\nبنویس: چپ / وسط / راست")


async def _handle_penalty_input(reply, uid, u, text_raw):
    choice = text_raw.strip()
    if choice not in PENALTY_DIRECTIONS:
        return False  # منتظر یکی از سه گزینه بود، این پیام ربطی نداشت

    active_penalty.discard(uid)
    keeper = random.choice(PENALTY_DIRECTIONS)

    owned = {i["item_name"] for i in get_items(uid, "penalty")}
    luck_boost = 0
    for item in owned:
        if item in ("توپ طلایی", "کفش سرعتی", "توپ الماسی", "مدال قهرمانی"):
            luck_boost += 0.05

    goal = (choice != keeper) or (random.random() < luck_boost)

    if goal:
        base_reward = random.randint(60, 150)
        bonus = sum(PENALTY_COIN_BONUS.get(i, 0) for i in owned)
        reward = base_reward + bonus
        add_coins(uid, reward)
        add_xp(uid, 10)
        await reply(f"⚽🥅 گـــل!! دروازه‌بان سمت {keeper} پرید، تو {choice} زدی!\n💰 جایزه: {reward} سکه | ✨ +10 XP")
    else:
        await reply(f"🧤 دروازه‌بان سمت {keeper} پرید و مهارش کرد! بازم امتحان کن.")
    return True


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
        await reply("بنویس: «رولت روسی [مبلغ شرط]» — مثلا: رولت روسی 200")
        return
    bet = int(bet_str)
    if bet <= 0:
        await reply("مبلغ شرط باید بزرگتر از صفر باشه.")
        return
    if u["coins"] < bet:
        await reply("❌ سکه‌ت برای این شرط کافی نیست.")
        return

    owned = {i["item_name"] for i in get_items(uid, "roulette")}
    death_chance = 1 / 6
    if "خشاب شانس" in owned:
        death_chance = 1 / 8
    if "طلسم بقا" in owned:
        death_chance *= 0.5

    if random.random() < death_chance:
        remove_coins(uid, bet)
        await reply(f"🔫💥 بنـــگ! امروز شانس باهات نبود.\n💸 {bet:,} سکه رو باختی.")
    else:
        add_coins(uid, bet)
        add_xp(uid, 8)
        await reply(f"🔫😮‍💨 تیک... خالی بود! زنده موندی.\n💰 {bet:,} سکه بردی! (مجموع: {bet*2:,})")


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
            reward = random.randint(*PRISON_WIN_REWARD)
            add_coins(uid, reward)
            add_xp(uid, 25)
            await reply(f"{next_node['text']}\n\n💰 جایزه‌ی فرار موفق: {reward} سکه | ✨ +25 XP")
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
# بخش ۱۱: روتر اصلی و اجرای ربات
# ==============================================================================
bot = Robot(token=BOT_TOKEN)
init_db()


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

    if "رتبه" in text_raw:
        await handle_leaderboard(reply); return

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

    if "شکار" in text_raw:
        await handle_hunt(reply, uid, u); return

    # =========================================================================
    # ---- بازی‌ها ----
    # =========================================================================
    if "حدس عدد" in text_raw:
        await handle_start_guess(reply, uid); return

    if text_raw.startswith("رولت روسی"):
        await handle_roulette(reply, uid, u, text_raw); return

    if "پنالتی" in text_raw:
        await handle_start_penalty(reply, uid); return

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

    # =========================================================================
    # ---- فقط ادمین اصلی ربات (ریپلای یا آیدی مستقیم — رفع باگ اصلی) ----
    # =========================================================================
    if text_raw.startswith("همگانی"):
        await handle_broadcast(reply, bot, uid, text_raw); return

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

    if "مالک ربات" in text_raw:
        await reply(f"👑 سازنده‌ی من: {OWNER_USERNAME}\n{OWNER_LINK}")
        return

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
