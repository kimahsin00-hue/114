"""
sqlite3 커넥션 관리 + 테이블 초기화 + 패널 영속성(watchdog용) 저장.
원본과 동일하게 커넥션 하나를 재사용합니다 (discord.py는 단일 asyncio 루프이므로 안전).
"""
import json
import os
import sqlite3
from datetime import datetime

from config import DB_PATH, PANEL_FILE, KST

_db_conn = None


def get_db():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
        _db_conn.execute('PRAGMA journal_mode=WAL;')
        _db_conn.execute('PRAGMA busy_timeout=15000;')
    return _db_conn


def init_db():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS pearl_history (item_id INTEGER, timestamp DATETIME, total_trades INTEGER, stock INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS rift_history (id INTEGER PRIMARY KEY CHECK(id=1), reporter_id INTEGER, reporter_name TEXT, kill_time DATETIME)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS item_cache (item_id INTEGER, sid INTEGER, price INTEGER, stock INTEGER, count INTEGER, last_updated DATETIME, PRIMARY KEY (item_id, sid))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS status_reports (id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT, user_id INTEGER, content TEXT, timestamp DATETIME, expire_time DATETIME)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS boss_alert_channels (guild_id INTEGER PRIMARY KEY, channel_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS status_notify_channels (s_type TEXT PRIMARY KEY, channel_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS boss_alert_users (user_id INTEGER PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS status_alert_users (user_id INTEGER PRIMARY KEY)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS boss_alert_settings (user_id INTEGER, time_str TEXT, PRIMARY KEY (user_id, time_str))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS weekly_dm_settings (
        guild_id INTEGER PRIMARY KEY, title TEXT, message TEXT, enabled INTEGER DEFAULT 1,
        weekday INTEGER NOT NULL DEFAULT 5, hour INTEGER NOT NULL DEFAULT 18, minute INTEGER NOT NULL DEFAULT 0
    )''')
    for _col, _ddl in (
        ("weekday", "ALTER TABLE weekly_dm_settings ADD COLUMN weekday INTEGER NOT NULL DEFAULT 5"),
        ("hour", "ALTER TABLE weekly_dm_settings ADD COLUMN hour INTEGER NOT NULL DEFAULT 18"),
        ("minute", "ALTER TABLE weekly_dm_settings ADD COLUMN minute INTEGER NOT NULL DEFAULT 0"),
    ):
        try:
            cursor.execute(_ddl)
        except sqlite3.OperationalError:
            pass  # 이미 컬럼이 존재함
    cursor.execute('''CREATE TABLE IF NOT EXISTS broadcast_channels (guild_id INTEGER PRIMARY KEY, channel_id INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS tts_settings (guild_id INTEGER PRIMARY KEY, text_ch_id INTEGER, voice_ch_id INTEGER, enabled INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS music_channels (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS party_applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, user_id INTEGER NOT NULL, user_name TEXT NOT NULL,
        job TEXT NOT NULL, stats TEXT NOT NULL, timing TEXT NOT NULL, channel_id INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS party_panel (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS party_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, kind TEXT NOT NULL, participants TEXT NOT NULL,
        event_date TEXT NOT NULL, event_time TEXT NOT NULL, created_by INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        reminded INTEGER NOT NULL DEFAULT 0
    )''')
    try:
        cursor.execute("ALTER TABLE party_schedule ADD COLUMN reminded INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # 이미 컬럼이 존재함
    cursor.execute('''CREATE TABLE IF NOT EXISTS party_calendar_panel (guild_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL)''')
    conn.commit()


def save_panel(name, msg):
    """watchdog이 재시작 후 패널 메시지를 다시 찾아 붙일 수 있도록 채널ID/메시지ID를 기록."""
    data = {}
    if os.path.exists(PANEL_FILE):
        try:
            with open(PANEL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    data[name] = [msg.channel.id, msg.id]
    with open(PANEL_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
