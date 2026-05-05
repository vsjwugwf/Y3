"""
main.py - بدنهٔ اصلی ربات یوتیوب Bale (نسخه ۳)
مدیریت پیام‌ها، callbackها، صف jobها، دستورات کوتاه متنی و تنظیمات شیشه‌ای.
"""

import os
import sys
import json
import time
import uuid
import re
import hashlib
import threading
import queue
import shutil
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

import settings
from utils import (
    get_logger,
    load_json,
    save_json,
    extract_video_id,
    split_file_binary,
    split_video_by_size,
    upload_manager,
    safe_remove,
    download_file,
)
import youtube_core

_log = get_logger("main")

# -------------------------------------------------------------------
# مقداردهی اولیه
# -------------------------------------------------------------------
BOT_TOKEN = settings.BOT_TOKEN
API_BASE = settings.API_BASE
DATA_DIR = settings.DATA_DIR
DOWNLOADS_DIR = settings.DOWNLOADS_DIR
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# صف jobها (thread‑safe)
task_queue: queue.Queue = queue.Queue()
queue_lock = threading.Lock()

# وضعیت کاربر
user_state: Dict[int, str] = {}
user_state_lock = threading.Lock()

# تنظیمات متدها و UI
method_config: Dict[str, Any] = {}
method_config_lock = threading.Lock()

# نگاشت کدهای کوتاه به video_id
cmd_map: Dict[str, str] = {}
cmd_map_lock = threading.Lock()

# مسیر فایل cmd_map
CMD_MAP_FILE = os.path.join(DATA_DIR, "cmd_map.json")

# -------------------------------------------------------------------
# احراز هویت ادمین
# -------------------------------------------------------------------
def get_admin_chat_id() -> int:
    """خواندن chat_id ادمین از فایل یا پیش‌فرض."""
    if os.path.exists(settings.ADMIN_FILE):
        data = load_json(settings.ADMIN_FILE)
        return data.get("chat_id", settings.DEFAULT_ADMIN_CHAT_ID)
    return settings.DEFAULT_ADMIN_CHAT_ID

def is_admin(chat_id: int) -> bool:
    """بررسی اینکه chat_id متعلق به ادمین است."""
    return chat_id == get_admin_chat_id()

# -------------------------------------------------------------------
# ابزارهای API بله
# -------------------------------------------------------------------
def _api(method: str, data: Optional[dict] = None, files: Optional[dict] = None) -> Optional[dict]:
    """ارسال درخواست به API بله و بازگرداندن JSON پاسخ."""
    url = f"{API_BASE}/{method}"
    try:
        if files:
            resp = requests.post(url, data=data, files=files, timeout=30)
        else:
            resp = requests.post(url, json=data, timeout=settings.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _log.error(f"API call {method} failed: {e}")
        return None


def send_message(chat_id: int, text: str, reply_markup: Optional[str] = None) -> Optional[dict]:
    """ارسال پیام متنی (با inline keyboard اختیاری)."""
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return _api("sendMessage", data)


def send_document(chat_id: int, file_path: str, caption: str = "") -> bool:
    """
    ارسال فایل (سند) به کاربر.
    بازگشت True در صورت موفقیت.
    """
    if not os.path.exists(file_path):
        _log.error(f"send_document: file not found {file_path}")
        return False
    try:
        with open(file_path, "rb") as f:
            files = {"document": (os.path.basename(file_path), f)}
            data = {"chat_id": chat_id, "caption": caption}
            result = _api("sendDocument", data=data, files=files)
            return result is not None and result.get("ok")
    except Exception as e:
        _log.error(f"send_document failed: {e}")
        return False


def get_updates(offset: int = 0, timeout: int = 50) -> List[dict]:
    """دریافت به‌روزرسانی‌ها با long‑polling."""
    data_payload = {"offset": offset, "timeout": timeout}
    url = f"{API_BASE}/getUpdates"
    try:
        resp = requests.post(url, json=data_payload, timeout=timeout + 10)
        resp.raise_for_status()
        result = resp.json()
        return result.get("result", [])
    except Exception as e:
        _log.error(f"getUpdates error: {e}")
        time.sleep(2)
        return []


def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: Optional[str] = None) -> Optional[dict]:
    """ویرایش متن و کیبورد یک پیام."""
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return _api("editMessageText", data)


def edit_reply_markup(chat_id: int, message_id: int, reply_markup: str) -> Optional[dict]:
    """ویرایش کیبورد inline یک پیام."""
    data = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
    return _api("editMessageReplyMarkup", data)


def answer_callback_query(callback_query_id: str, text: str = "") -> Optional[dict]:
    """پاسخ به یک callback query."""
    data = {"callback_query_id": callback_query_id, "text": text}
    return _api("answerCallbackQuery", data)


# -------------------------------------------------------------------
# مدیریت cmd_map (کدهای کوتاه)
# -------------------------------------------------------------------
def load_cmd_map() -> Dict[str, str]:
    """بارگذاری نگاشت کدهای کوتاه از فایل."""
    return load_json(CMD_MAP_FILE, {})

def save_cmd_map(cmap: Dict[str, str]) -> None:
    """ذخیرهٔ نگاشت کدهای کوتاه در فایل."""
    save_json(CMD_MAP_FILE, cmap)

def generate_short_code(video_id: str) -> str:
    """تولید کد کوتاه ۴ نویسه‌ای (هگز) بر اساس MD5 video_id."""
    global cmd_map
    full_hash = hashlib.md5(video_id.encode()).hexdigest()
    with cmd_map_lock:
        for i in range(4, len(full_hash) + 1):
            code = full_hash[:i]
            if code not in cmd_map or cmd_map[code] == video_id:
                return code
    return full_hash  # fallback (نباید به اینجا برسد)

def register_video_command(video_id: str) -> str:
    """ثبت video_id و برگرداندن کد کوتاه."""
    global cmd_map
    with cmd_map_lock:
        code = generate_short_code(video_id)
        cmd_map[code] = video_id
        save_cmd_map(cmd_map)
    return code

# -------------------------------------------------------------------
# مدیریت تنظیمات متدها و UI
# -------------------------------------------------------------------
def load_method_config() -> Dict[str, Any]:
    """بارگذاری تنظیمات از فایل، یا ایجاد پیش‌فرض."""
    default = settings.DEFAULT_SESSION_SETTINGS.copy()
    config = load_json(settings.METHOD_CONFIG_FILE, default)
    for key, val in default.items():
        if key not in config:
            config[key] = val
    return config

def save_method_config(config: dict) -> None:
    save_json(settings.METHOD_CONFIG_FILE, config)

def get_enabled_chain(category: str) -> List[str]:
    """ساخت زنجیرهٔ فعال بر اساس متدهای enabled و ترتیب پیش‌فرض."""
    with method_config_lock:
        config = method_config.copy()
    if category == "search":
        default = settings.DEFAULT_SEARCH_CHAIN
        enabled = config.get("enabled_search_methods", list(settings.SEARCH_METHODS.keys()))
    else:
        default = settings.DEFAULT_DOWNLOAD_CHAIN
        enabled = config.get("enabled_download_methods", list(settings.DOWNLOAD_METHODS.keys()))
    return [m for m in default if m in enabled]

# -------------------------------------------------------------------
# ساخت کیبوردهای inline
# -------------------------------------------------------------------
def _inline_keyboard(buttons: List[List[dict]]) -> str:
    """تبدیل لیست دکمه‌ها به JSON مورد نیاز Bale."""
    return json.dumps({"inline_keyboard": buttons}, ensure_ascii=False)


def build_main_menu() -> str:
    """منوی اصلی."""
    return _inline_keyboard([
        [{"text": "🔍 جستجوی یوتیوب", "callback_data": "search"}],
        [{"text": "📥 دانلود با لینک", "callback_data": "download_link"}],
        [{"text": "⚙️ تنظیمات", "callback_data": "settings"}],
        [{"text": "ℹ️ راهنما", "callback_data": "help"}],
        [{"text": "📄 دریافت لاگ", "callback_data": "log"}],
    ])


def build_settings_menu() -> str:
    """منوی تنظیمات شیشه‌ای."""
    return _inline_keyboard([
        [{"text": "🔗 زنجیرهٔ دانلود", "callback_data": "settings_chain|download"}],
        [{"text": "🔗 زنجیرهٔ جستجو", "callback_data": "settings_chain|search"}],
        [{"text": "📦 حالت آپلود", "callback_data": "settings_upload_mode"}],
        [{"text": "🎚️ کیفیت دانلود", "callback_data": "settings_quality"}],
        [{"text": "🔍 حالت جستجو", "callback_data": "settings_search_mode"}],
        [{"text": "🔢 نتایج در صفحه", "callback_data": "settings_page_size"}],
        [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
    ])


def build_method_chain_keyboard(category: str) -> str:
    """کیبورد مدیریت زنجیره (فعال/غیرفعال کردن متدها)."""
    with method_config_lock:
        config = method_config.copy()
    if category == "search":
        methods = settings.SEARCH_METHODS
        enabled_list = config.get("enabled_search_methods", list(methods.keys()))
    else:
        methods = settings.DOWNLOAD_METHODS
        enabled_list = config.get("enabled_download_methods", list(methods.keys()))

    buttons = []
    for key, meta in methods.items():
        status = "✅" if key in enabled_list else "❌"
        name = meta.get("name", key)
        buttons.append([{"text": f"{status} {name}", "callback_data": f"toggle_method|{category}|{key}"}])
    buttons.append([{"text": "💾 ذخیره", "callback_data": f"save_chain|{category}"}])
    return _inline_keyboard(buttons)


def build_quality_keyboard() -> str:
    """کیبورد انتخاب کیفیت دانلود."""
    with method_config_lock:
        current = method_config.get("download_quality", "720p")
    qualities = ["144p", "360p", "720p", "1080p"]
    buttons = []
    for q in qualities:
        marker = "✅" if q == current else "⬜"
        buttons.append([{"text": f"{marker} {q}", "callback_data": f"quality_set|{q}"}])
    buttons.append([{"text": "🔙 بازگشت", "callback_data": "settings"}])
    return _inline_keyboard(buttons)


# -------------------------------------------------------------------
# صف jobها و worker
# -------------------------------------------------------------------
def enqueue_job(job: dict) -> None:
    """اضافه کردن job به صف درون‌حافظه و ذخیره در فایل."""
    job["id"] = str(uuid.uuid4())
    try:
        queue_data = load_json(settings.QUEUE_FILE, [])
        queue_data.append(job)
        save_json(settings.QUEUE_FILE, queue_data)
    except Exception as e:
        _log.error(f"Failed to persist job: {e}")
    task_queue.put(job)
    _log.info(f"Job enqueued: {job['id']} / {job.get('command')}")


def worker_loop() -> None:
    """پردازش jobها در thread جداگانه، با حذف ایمن از فایل پس از اتمام."""
    _log.info("Worker thread started.")
    # بارگذاری jobهای قبلی از فایل
    try:
        pending = load_json(settings.QUEUE_FILE, [])
        for j in pending:
            task_queue.put(j)
        _log.info(f"Loaded {len(pending)} pending jobs from file.")
    except Exception as e:
        _log.error(f"Error loading queue file: {e}")

    while True:
        try:
            job = task_queue.get(timeout=5)
        except queue.Empty:
            continue

        try:
            _log.info(f"Processing job: {job['id']} ({job.get('command')})")
            process_job(job)
            _log.info(f"Job {job['id']} finished.")
        except Exception as e:
            _log.exception(f"Job {job['id']} failed: {e}")
        finally:
            # حذف job از فایل بعد از پردازش
            try:
                jobs = load_json(settings.QUEUE_FILE, [])
                jobs = [j for j in jobs if j.get("id") != job.get("id")]
                save_json(settings.QUEUE_FILE, jobs)
            except Exception as e:
                _log.error(f"Failed to remove job from file: {e}")
            task_queue.task_done()


# -------------------------------------------------------------------
# اجرای jobها
# -------------------------------------------------------------------
def process_job(job: dict) -> None:
    """پردازش یک job بر اساس command."""
    cmd = job.get("command")
    chat_id = job.get("chat_id")

    if cmd == "search":
        query = job["query"]
        mode = job.get("mode") or method_config.get("search_mode", "browser")
        limit = method_config.get("result_page_size", 5)

        results, method_used = youtube_core.search_youtube(
            query, limit=limit, mode=mode, enrich=False
        )
        if not results:
            send_message(chat_id, "❌ هیچ نتیجه‌ای یافت نشد.")
            return

        # ساخت پیام نتایج با کدهای کوتاه
        lines = [f"🔍 نتایج جستجو برای: {query} (روش: {method_used})\n"]
        for i, vid in enumerate(results[:limit], 1):
            vid_id = vid.get("video_id", "")
            code = register_video_command(vid_id)
            title = vid.get("title") or "بدون عنوان"
            duration = vid.get("duration") or "?"
            uploader = vid.get("uploader") or "?"
            lines.append(f"{i}️⃣ {title}")
            lines.append(f"   ⏱ {duration} | 👤 {uploader}")
            lines.append(f"   📋 /H{code}")
            lines.append(f"   📥 /Download_{code}")
            lines.append("")

        text = "\n".join(lines)
        send_message(chat_id, text)

    elif cmd == "info":
        video_id = job["video_id"]
        info_dict, method_used = youtube_core.get_video_info(video_id)

        if not info_dict or not info_dict.get("title"):
            send_message(chat_id, "❌ اطلاعات ویدئو یافت نشد.")
            return

        # دانلود و ارسال تامنیل
        try:
            thumb_path, _ = youtube_core.download_thumbnail(video_id, DOWNLOADS_DIR)
            if thumb_path:
                caption = f"{info_dict.get('title', '')} - {method_used}" if settings.UI_SETTINGS["show_method_in_output"] else info_dict.get("title", "")
                send_document(chat_id, thumb_path, caption=caption)
                safe_remove(thumb_path)
        except Exception as e:
            _log.error(f"Info thumbnail error: {e}")

        # ساخت پیام اطلاعات
        title = info_dict.get("title", "بدون عنوان")
        duration = info_dict.get("duration", "?")
        views = info_dict.get("view_count", "?")
        likes = info_dict.get("like_count", "?")
        uploader = info_dict.get("uploader", "?")
        desc = info_dict.get("description", "")
        if desc and len(desc) > 300:
            desc = desc[:300] + "..."

        msg = (
            f"🎬 {title}\n"
            f"⏱ مدت: {duration}\n"
            f"👁 بازدید: {views}\n"
            f"❤️ لایک: {likes}\n"
            f"👤 آپلودکننده: {uploader}\n"
            f"📝 توضیحات: {desc if desc else '---'}\n"
            f"🔮 روش: {method_used}"
        )
        send_message(chat_id, msg)

    elif cmd == "download":
        video_id = job["video_id"]
        save_dir = os.path.join(DOWNLOADS_DIR, job["id"])
        os.makedirs(save_dir, exist_ok=True)
        quality = job.get("quality", method_config.get("download_quality", "720p"))
        chain = job.get("chain", get_enabled_chain("download"))

        file_path, method_used = youtube_core.download_video(
            video_id, save_dir, chain=chain, quality=quality
        )
        if not file_path or not os.path.exists(file_path):
            send_message(chat_id, "❌ دانلود ناموفق بود.")
            safe_remove(save_dir)
            return

        # آپلود بر اساس حالت
        upload_mode = method_config.get("upload_mode", "playable_chunks")
        if upload_mode == "zip":
            # ساخت zip و تقسیم
            zip_base = os.path.join(save_dir, f"video_{video_id}")
            shutil.make_archive(zip_base, 'zip', os.path.dirname(file_path), os.path.basename(file_path))
            zip_path = zip_base + ".zip"
            parts = split_file_binary(zip_path, f"video_{video_id}", ".zip")
            safe_remove(zip_path)  # پاک کردن zip اصلی بعد از split
        else:
            parts = split_video_by_size(file_path, save_dir, max_size_bytes=settings.MAX_CHUNK_SIZE)

        if not parts:
            send_message(chat_id, "❌ آماده‌سازی فایل برای ارسال ناموفق بود.")
            safe_remove(save_dir)
            return

        state_file = os.path.join(DATA_DIR, f"upload_state_{job['id']}.json")

        def _send_doc(chat_id_int: int, fpath: str, caption: str) -> bool:
            return send_document(chat_id_int, fpath, caption)

        success = upload_manager(parts, chat_id, _send_doc, state_file, max_retries=3)
        if success:
            send_message(chat_id, f"✅ ویدئو با موفقیت آپلود شد. روش: {method_used}")
        else:
            send_message(chat_id, "⚠️ آپلود با خطا مواجه شد. ممکن است برخی بخش‌ها ارسال نشده باشند.")

        safe_remove(save_dir)


# -------------------------------------------------------------------
# مدیریت callbackها
# -------------------------------------------------------------------
def handle_callback(chat_id: int, data: str, message_id: int, callback_query_id: str) -> None:
    """پردازش callbackهای inline."""
    if not is_admin(chat_id):
        answer_callback_query(callback_query_id, "⛔ دسترسی ندارید.")
        return

    answer_callback_query(callback_query_id)
    parts = data.split("|")
    action = parts[0]

    if action == "main_menu":
        send_message(chat_id, "منوی اصلی", reply_markup=build_main_menu())

    elif action == "search":
        with user_state_lock:
            user_state[chat_id] = "awaiting_query"
        send_message(chat_id, "🔍 لطفاً عبارت جستجو را وارد کنید.")

    elif action == "download_link":
        with user_state_lock:
            user_state[chat_id] = "awaiting_url"
        send_message(chat_id, "📥 لطفاً لینک یوتیوب را ارسال کنید.")

    elif action == "settings":
        send_message(chat_id, "⚙️ تنظیمات", reply_markup=build_settings_menu())

    elif action == "settings_chain":
        category = parts[1]
        keyboard = build_method_chain_keyboard(category)
        send_message(chat_id, f"مدیریت زنجیرهٔ {category}", reply_markup=keyboard)

    elif action == "toggle_method":
        category = parts[1]
        method_key = parts[2]
        with method_config_lock:
            if category == "search":
                enabled = method_config.get("enabled_search_methods", [])
                if method_key in enabled:
                    enabled.remove(method_key)
                else:
                    enabled.append(method_key)
                method_config["enabled_search_methods"] = enabled
            else:
                enabled = method_config.get("enabled_download_methods", [])
                if method_key in enabled:
                    enabled.remove(method_key)
                else:
                    enabled.append(method_key)
                method_config["enabled_download_methods"] = enabled
        new_keyboard = build_method_chain_keyboard(category)
        edit_reply_markup(chat_id, message_id, new_keyboard)

    elif action == "save_chain":
        category = parts[1]
        with method_config_lock:
            save_method_config(method_config)
        answer_callback_query(callback_query_id, "تنظیمات ذخیره شد.")
        send_message(chat_id, "تنظیمات ذخیره شد.", reply_markup=build_settings_menu())

    elif action == "settings_upload_mode":
        with method_config_lock:
            current = method_config.get("upload_mode", "playable_chunks")
            new_mode = "zip" if current == "playable_chunks" else "playable_chunks"
            method_config["upload_mode"] = new_mode
            save_method_config(method_config)
        answer_callback_query(callback_query_id, f"حالت آپلود: {new_mode}")

    elif action == "settings_quality":
        keyboard = build_quality_keyboard()
        send_message(chat_id, "🎚️ کیفیت دانلود را انتخاب کنید:", reply_markup=keyboard)

    elif action == "quality_set":
        quality = parts[1]
        with method_config_lock:
            method_config["download_quality"] = quality
            save_method_config(method_config)
        answer_callback_query(callback_query_id, f"کیفیت: {quality}")
        # به‌روزرسانی کیبورد
        new_keyboard = build_quality_keyboard()
        edit_reply_markup(chat_id, message_id, new_keyboard)

    elif action == "settings_search_mode":
        with method_config_lock:
            current = method_config.get("search_mode", "browser")
            new_mode = "api" if current == "browser" else "browser"
            method_config["search_mode"] = new_mode
            save_method_config(method_config)
        answer_callback_query(callback_query_id, f"حالت جستجو: {new_mode}")

    elif action == "settings_page_size":
        sizes = [5, 10, 15]
        with method_config_lock:
            current = method_config.get("result_page_size", 5)
            try:
                idx = sizes.index(current)
                new_size = sizes[(idx + 1) % len(sizes)]
            except ValueError:
                new_size = 5
            method_config["result_page_size"] = new_size
            save_method_config(method_config)
        answer_callback_query(callback_query_id, f"تعداد نتایج: {new_size}")

    elif action == "help":
        help_text = (
            "🤖 ربات دانلودر یوتیوب (نسخه ۳)\n\n"
            "دستورات:\n"
            "🔍 جستجو: عبارت خود را بفرستید.\n"
            "📥 دانلود با لینک: لینک ویدئو را بفرستید.\n"
            "/H{کد} – اطلاعات کامل ویدئو\n"
            "/Download_{کد} – دانلود ویدئو\n\n"
            "⚙️ تنظیمات: حالت جستجو (browser/api)، کیفیت، زنجیرهٔ دانلود و ..."
        )
        send_message(chat_id, help_text, reply_markup=build_main_menu())

    elif action == "log":
        if os.path.exists(settings.LOG_FILE):
            send_document(chat_id, settings.LOG_FILE, caption="فایل لاگ")
        else:
            send_message(chat_id, "❌ فایل لاگ موجود نیست.")


# -------------------------------------------------------------------
# مدیریت پیام‌های متنی
# -------------------------------------------------------------------
def handle_message(chat_id: int, text: str) -> None:
    """پردازش پیام متنی ورودی."""
    if not is_admin(chat_id):
        send_message(chat_id, "⛔ دسترسی ندارید.")
        return

    # دستورات اسلش
    if text.startswith("/start"):
        send_message(chat_id, "به ربات یوتیوب خوش آمدید. 🎬", reply_markup=build_main_menu())
        return

    if text.startswith("/help"):
        handle_callback(chat_id, "help", 0, "0")
        return

    if text.startswith("/log"):
        handle_callback(chat_id, "log", 0, "0")
        return

    # بررسی دستورات H (اطلاعات)
    match_h = re.match(r'^/H([a-f0-9]+)$', text, re.IGNORECASE)
    if match_h:
        code = match_h.group(1).lower()
        with cmd_map_lock:
            video_id = cmd_map.get(code)
        if video_id:
            enqueue_job({"command": "info", "video_id": video_id, "chat_id": chat_id})
            send_message(chat_id, f"⏳ دریافت اطلاعات برای /H{code} ...")
        else:
            send_message(chat_id, "❌ فرمان نامعتبر (کد یافت نشد).")
        return

    # بررسی دستورات Download_
    match_dl = re.match(r'^/Download_([a-f0-9]+)$', text, re.IGNORECASE)
    if match_dl:
        code = match_dl.group(1).lower()
        with cmd_map_lock:
            video_id = cmd_map.get(code)
        if video_id:
            enqueue_job({"command": "download", "video_id": video_id, "chat_id": chat_id})
            send_message(chat_id, f"⏳ دانلود /Download_{code} شروع شد...")
        else:
            send_message(chat_id, "❌ فرمان نامعتبر (کد یافت نشد).")
        return

    # بررسی state کاربر
    with user_state_lock:
        state = user_state.get(chat_id)

    if state == "awaiting_query":
        enqueue_job({
            "command": "search",
            "query": text,
            "mode": method_config.get("search_mode", "browser"),
            "chat_id": chat_id,
        })
        with user_state_lock:
            user_state[chat_id] = None
        send_message(chat_id, "⏳ در حال جستجو...")
        return

    if state == "awaiting_url":
        vid = extract_video_id(text)
        if not vid:
            send_message(chat_id, "❌ لینک یوتیوب معتبر نیست. لطفاً یک لینک معتبر ارسال کنید.")
            return
        enqueue_job({"command": "info", "video_id": vid, "chat_id": chat_id})
        with user_state_lock:
            user_state[chat_id] = None
        send_message(chat_id, "⏳ دریافت اطلاعات...")
        return

    # در غیر این صورت، نادیده گرفته می‌شود
    pass


# -------------------------------------------------------------------
# حلقهٔ اصلی
# -------------------------------------------------------------------
def main() -> None:
    global method_config, cmd_map

    _log.info("Bot starting...")

    # بارگذاری تنظیمات و cmd_map
    method_config = load_method_config()
    cmd_map = load_cmd_map()

    # راه‌اندازی worker
    worker = threading.Thread(target=worker_loop, daemon=True)
    worker.start()

    # حلقهٔ long‑polling
    offset = 0
    while True:
        try:
            updates = get_updates(offset=offset, timeout=settings.LONG_POLL_TIMEOUT)
            for upd in updates:
                offset = upd["update_id"] + 1
                if "message" in upd:
                    msg = upd["message"]
                    chat_id = msg["chat"]["id"]
                    if "text" in msg:
                        # پردازش در همان thread (ترتیبی) برای سادگی
                        handle_message(chat_id, msg["text"])
                elif "callback_query" in upd:
                    cb = upd["callback_query"]
                    chat_id = cb["message"]["chat"]["id"]
                    message_id = cb["message"]["message_id"]
                    data = cb["data"]
                    callback_id = cb["id"]
                    handle_callback(chat_id, data, message_id, callback_id)
        except Exception as e:
            _log.exception(f"Main loop error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
