"""
main.py - بدنهٔ اصلی ربات یوتیوب Bale Ultimate (نسخه اصلاح‌شده)
تمامی باگ‌های گزارش‌شده (send_document، next_info، جستجوی ۳ برابری، پایداری صف) برطرف شده‌اند.
"""

import os
import sys
import json
import time
import uuid
import shutil
import threading
import queue
from typing import Optional, Dict, List, Any

import requests

import settings
from utils import (
    get_logger,
    load_json,
    save_json,
    extract_video_id,
    split_file_binary,
    split_video_playable,
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
os.makedirs(DATA_DIR, exist_ok=True)

# صف jobها (thread‑safe)
job_queue: queue.Queue = queue.Queue()

# وضعیت کاربر
user_state: Dict[int, str] = {}

# آخرین نتایج جستجو
last_search: Dict[str, Any] = {}
last_search_lock = threading.Lock()

# تنظیمات متدها و UI (در حافظه، همگام با فایل)
method_config: Dict[str, Any] = {}
method_config_lock = threading.Lock()

# -------------------------------------------------------------------
# ابزارهای API بله
# -------------------------------------------------------------------
def _api(method: str, data: Optional[dict] = None, files: Optional[dict] = None) -> Optional[dict]:
    """ارسال درخواست به API بله و بازگرداندن JSON پاسخ."""
    url = f"{API_BASE}/{method}"
    try:
        if files:
            # درخواست multipart/form-data
            resp = requests.post(url, data=data, files=files, timeout=30)
        else:
            resp = requests.post(url, json=data, timeout=settings.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        _log.error(f"API call {method} failed: {e}")
        return None


def send_message(chat_id: int, text: str, reply_markup: Optional[str] = None) -> Optional[dict]:
    """ارسال پیام متنی."""
    data = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return _api("sendMessage", data)


def send_document(chat_id: int, file_path: str, caption: str = "") -> bool:
    """
    ارسال فایل (سند) به کاربر.
    اصلاح: نام فایل به‌صورت tuple به requests ارسال می‌شود.
    """
    if not os.path.exists(file_path):
        _log.error(f"send_document: file not found {file_path}")
        return False
    try:
        with open(file_path, "rb") as f:
            # 🔧 اصلاح: ارائه نام فایل برای multipart
            files = {"document": (os.path.basename(file_path), f)}
            data = {"chat_id": chat_id, "caption": caption}
            result = _api("sendDocument", data=data, files=files)
            return result is not None and result.get("ok")
    except Exception as e:
        _log.error(f"send_document failed: {e}")
        return False


def get_updates(offset: int = 0, timeout: int = 50) -> List[dict]:
    """long‑polling برای دریافت به‌روزرسانی‌ها."""
    data = {"offset": offset, "timeout": timeout}
    url = f"{API_BASE}/getUpdates"
    try:
        resp = requests.post(url, json=data, timeout=timeout + 10)
        resp.raise_for_status()
        result = resp.json()
        return result.get("result", [])
    except Exception as e:
        _log.error(f"getUpdates error: {e}")
        time.sleep(2)
        return []


def edit_reply_markup(chat_id: int, message_id: int, reply_markup: str) -> Optional[dict]:
    """ویرایش کیبورد inline یک پیام."""
    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup
    }
    return _api("editMessageReplyMarkup", data)


def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: Optional[str] = None) -> Optional[dict]:
    """ویرایش متن و کیبورد پیام."""
    data = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return _api("editMessageText", data)


def answer_callback_query(callback_query_id: str, text: str = "") -> Optional[dict]:
    """پاسخ به callback query."""
    data = {"callback_query_id": callback_query_id, "text": text}
    return _api("answerCallbackQuery", data)


# -------------------------------------------------------------------
# مدیریت تنظیمات
# -------------------------------------------------------------------
def load_method_config() -> dict:
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
        config = method_config
    if category == "search":
        default = settings.DEFAULT_SEARCH_CHAIN
        enabled = config.get("enabled_search_methods", list(settings.SEARCH_METHODS.keys()))
    else:
        default = settings.DEFAULT_DOWNLOAD_CHAIN
        enabled = config.get("enabled_download_methods", list(settings.DOWNLOAD_METHODS.keys()))
    return [m for m in default if m in enabled]


# -------------------------------------------------------------------
# آخرین جستجو
# -------------------------------------------------------------------
def load_last_search() -> Dict[str, Any]:
    return load_json("data/last_search.json", {})

def save_last_search(data: Dict[str, Any]) -> None:
    save_json("data/last_search.json", data)


# -------------------------------------------------------------------
# ساخت کیبوردهای inline
# -------------------------------------------------------------------
def _inline_keyboard(buttons: List[List[dict]]) -> str:
    return json.dumps({"inline_keyboard": buttons}, ensure_ascii=False)


def build_main_menu() -> str:
    return _inline_keyboard([
        [{"text": "🔍 جستجوی یوتیوب", "callback_data": "search"}],
        [{"text": "📥 دانلود با لینک", "callback_data": "download_link"}],
        [{"text": "⚙️ تنظیمات", "callback_data": "settings"}],
        [{"text": "ℹ️ راهنما", "callback_data": "help"}],
        [{"text": "📄 دریافت لاگ", "callback_data": "log"}],
    ])


def build_search_keyboard(results: List[dict], page: int, total_pages: int, method_used: str) -> str:
    buttons = []
    start = page * settings.UI_SETTINGS["result_page_size"]
    end = start + settings.UI_SETTINGS["result_page_size"]
    page_results = results[start:end]
    for vid in page_results:
        vid_id = vid.get("video_id", "")
        title = vid.get("title") or vid_id
        short_title = title[:30] + "..." if len(title) > 30 else title
        buttons.append([
            {"text": f"📋 {short_title}", "callback_data": f"info|{vid_id}|{method_used}"},
            {"text": "🖼", "callback_data": f"thumb|{vid_id}|{method_used}"},
            {"text": "📥", "callback_data": f"dl|{vid_id}|{method_used}"},
        ])

    nav = []
    if page > 0:
        nav.append({"text": "◀️", "callback_data": f"page|{page-1}"})
    if page < total_pages - 1:
        nav.append({"text": "▶️", "callback_data": f"page|{page+1}"})
    if nav:
        buttons.append(nav)
    control = [
        {"text": "🔁 متد بعدی", "callback_data": f"next_search|{method_used}"},
        {"text": "📥 دانلود همه", "callback_data": "batch_dl"},
    ]
    buttons.append(control)
    return _inline_keyboard(buttons)


def build_info_keyboard(video_id: str, method_used: str) -> str:
    """
    🔧 اصلاح: دکمهٔ «متد بعدی» حالا video_id را همراه دارد.
    """
    return _inline_keyboard([
        [{"text": "📥 دانلود", "callback_data": f"dl|{video_id}|{method_used}"}],
        [{"text": "🖼 تامنیل", "callback_data": f"thumb|{video_id}|{method_used}"}],
        [{"text": "🔁 متد بعدی", "callback_data": f"next_info|{video_id}|{method_used}"}],
    ])


def build_settings_menu() -> str:
    return _inline_keyboard([
        [{"text": "🔗 زنجیرهٔ دانلود", "callback_data": "settings_chain|download"}],
        [{"text": "🔗 زنجیرهٔ جستجو", "callback_data": "settings_chain|search"}],
        [{"text": "📦 حالت آپلود", "callback_data": "settings_upload_mode"}],
        [{"text": "🔢 تعداد نتایج در صفحه", "callback_data": "settings_page_size"}],
        [{"text": "🔙 بازگشت", "callback_data": "main_menu"}],
    ])


def build_method_chain_keyboard(category: str) -> str:
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


# -------------------------------------------------------------------
# مدیریت jobها و persistence صف (🔧 اصلاح‌شده)
# -------------------------------------------------------------------
def enqueue_job(job: dict) -> None:
    """اضافه کردن job به صف درون‌حافظه و ذخیره در فایل."""
    job["id"] = str(uuid.uuid4())
    # ذخیره در فایل (ماندگاری)
    try:
        queue_data = load_json(settings.QUEUE_FILE, [])
        queue_data.append(job)
        save_json(settings.QUEUE_FILE, queue_data)
    except Exception as e:
        _log.error(f"Failed to persist job: {e}")
    job_queue.put(job)
    _log.info(f"Job enqueued: {job['id']} / {job.get('command')}")


def worker_loop() -> None:
    """پردازش jobها در thread جداگانه، با حذف ایمن از فایل پس از اتمام."""
    _log.info("Worker thread started.")

    # بارگذاری jobهای قبلی از فایل (در صورت وجود) و انتقال به صف حافظه
    try:
        pending = load_json(settings.QUEUE_FILE, [])
        for j in pending:
            job_queue.put(j)
        _log.info(f"Loaded {len(pending)} pending jobs from file.")
    except Exception as e:
        _log.error(f"Error loading queue file: {e}")

    while True:
        try:
            job = job_queue.get(timeout=5)
        except queue.Empty:
            continue

        try:
            _log.info(f"Processing job: {job['id']} ({job.get('command')})")
            process_job(job)
            _log.info(f"Job {job['id']} finished.")
        except Exception as e:
            _log.exception(f"Job {job['id']} failed: {e}")
        finally:
            # 🔧 اصلاح: حذف job از فایل بعد از پردازش
            try:
                jobs = load_json(settings.QUEUE_FILE, [])
                jobs = [j for j in jobs if j.get("id") != job.get("id")]
                save_json(settings.QUEUE_FILE, jobs)
            except Exception as e:
                _log.error(f"Failed to remove job from file: {e}")
            job_queue.task_done()


# -------------------------------------------------------------------
# اجرای jobها
# -------------------------------------------------------------------
def process_job(job: dict) -> None:
    cmd = job.get("command")
    chat_id = job.get("chat_id")

    if cmd == "search":
        query = job["query"]
        limit = job.get("limit", settings.UI_SETTINGS["result_page_size"])
        chain = job.get("chain", get_enabled_chain("search"))
        start_method = job.get("start_method")

        # 🔧 اصلاح: جستجو با یک حد معقول (۳۰ نتیجه) به‌جای limit*3
        results, method_used = youtube_core.search_youtube(
            query, limit=30, chain=chain, start_method=start_method
        )
        if not results:
            send_message(chat_id, "❌ هیچ نتیجه‌ای یافت نشد.")
            return

        # ذخیرهٔ آخرین جستجو
        with last_search_lock:
            last_search.clear()
            last_search["results"] = results
            last_search["method_used"] = method_used
            last_search["query"] = query
            last_search["chain"] = chain
            save_last_search(last_search)

        total_pages = (len(results) + limit - 1) // limit
        page = 0

        # ارسال تامنیل‌ها
        for idx, vid in enumerate(results):
            try:
                thumb_path, _ = youtube_core.download_thumbnail(vid["video_id"], "downloads")
                if thumb_path:
                    caption = f"{vid.get('title')} - روش: {method_used}" if settings.UI_SETTINGS["show_method_in_output"] else vid.get("title")
                    send_document(chat_id, thumb_path, caption=caption)
                    safe_remove(thumb_path)
            except Exception as e:
                _log.error(f"Thumbnail send error for {vid.get('video_id')}: {e}")

        header = f"🔍 نتایج جستجو برای: {query}\nروش: {method_used}\nصفحه ۱ از {total_pages}"
        keyboard = build_search_keyboard(results, page, total_pages, method_used)
        send_message(chat_id, header, reply_markup=keyboard)

    elif cmd == "info":
        video_id = job["video_id"]
        chain = job.get("chain", settings.DEFAULT_INFO_CHAIN)
        start_method = job.get("start_method")
        info_dict, method_used = youtube_core.get_video_info(
            video_id, chain=chain, start_method=start_method
        )
        if not info_dict:
            send_message(chat_id, "❌ اطلاعات ویدئو دریافت نشد.")
            return

        title = info_dict.get("title", "بدون عنوان")
        duration = info_dict.get("duration", "?")
        views = info_dict.get("view_count", "?")
        uploader = info_dict.get("uploader", "?")
        desc = info_dict.get("description", "")
        if desc and len(desc) > 200:
            desc = desc[:200] + "..."

        msg = (
            f"🎬 {title}\n"
            f"⏱ مدت: {duration}\n"
            f"👁 بازدید: {views}\n"
            f"👤 آپلودکننده: {uploader}\n"
            f"📝 توضیحات: {desc if desc else '---'}\n"
            f"روش: {method_used}"
        )

        try:
            thumb_path, _ = youtube_core.download_thumbnail(video_id, "downloads")
            if thumb_path:
                send_document(chat_id, thumb_path, caption="تصویر بند‌انگشتی")
                safe_remove(thumb_path)
        except Exception as e:
            _log.error(f"Info thumbnail error: {e}")

        keyboard = build_info_keyboard(video_id, method_used)
        send_message(chat_id, msg, reply_markup=keyboard)

    elif cmd == "download":
        video_id = job["video_id"]
        save_dir = os.path.join("downloads", job["id"])
        os.makedirs(save_dir, exist_ok=True)
        chain = job.get("chain", get_enabled_chain("download"))
        start_method = job.get("start_method")

        file_path, method_used = youtube_core.download_video(
            video_id, save_dir, chain=chain, start_method=start_method
        )
        if not file_path or not os.path.exists(file_path):
            send_message(chat_id, "❌ دانلود ناموفق بود.")
            safe_remove(save_dir)
            return

        upload_mode = method_config.get("upload_mode", "playable_chunks")
        chunk_dur = method_config.get("chunk_duration_seconds", 60)

        if upload_mode == "zip":
            zip_base = os.path.join(save_dir, f"video_{video_id}")
            shutil.make_archive(zip_base, 'zip', os.path.dirname(file_path), os.path.basename(file_path))
            zip_path = zip_base + ".zip"
            parts = split_file_binary(zip_path, f"video_{video_id}", ".zip")
            safe_remove(zip_path)
        else:
            parts = split_video_playable(file_path, save_dir, segment_duration=chunk_dur)

        if not parts:
            send_message(chat_id, "❌ آماده‌سازی فایل برای ارسال ناموفق بود.")
            safe_remove(save_dir)
            return

        state_file = f"data/upload_state_{job['id']}.json"

        def __send_doc(chat_id_int: int, fpath: str, caption: str) -> bool:
            return send_document(chat_id_int, fpath, caption)

        success = upload_manager(parts, chat_id, __send_doc, state_file, max_retries=3)
        if success:
            send_message(chat_id, f"✅ ویدئو با موفقیت آپلود شد. روش: {method_used}")
        else:
            send_message(chat_id, "⚠️ آپلود با خطا مواجه شد. ممکن است برخی بخش‌ها ارسال نشده باشند.")

        safe_remove(save_dir)

    elif cmd == "batch_download":
        with last_search_lock:
            results = last_search.get("results", [])
        if not results:
            send_message(chat_id, "❌ نتایج جستجویی برای دانلود موجود نیست.")
            return
        for vid in results:
            dl_job = {
                "command": "download",
                "video_id": vid["video_id"],
                "chat_id": chat_id,
                "chain": get_enabled_chain("download"),
            }
            enqueue_job(dl_job)


# -------------------------------------------------------------------
# مدیریت callback
# -------------------------------------------------------------------
def handle_callback(chat_id: int, data: str, message_id: int, callback_query_id: str) -> None:
    answer_callback_query(callback_query_id)

    parts = data.split("|")
    action = parts[0]

    if action == "search":
        user_state[chat_id] = "awaiting_query"
        send_message(chat_id, "🔍 لطفاً عبارت جستجو را وارد کنید.")

    elif action == "download_link":
        user_state[chat_id] = "awaiting_url"
        send_message(chat_id, "📥 لطفاً لینک یوتیوب را ارسال کنید.")

    elif action == "info":
        video_id = parts[1]
        method_used = parts[2] if len(parts) > 2 else ""
        job = {
            "command": "info",
            "video_id": video_id,
            "chat_id": chat_id,
            "chain": settings.DEFAULT_INFO_CHAIN,
        }
        enqueue_job(job)

    elif action == "dl":
        video_id = parts[1]
        method_used = parts[2] if len(parts) > 2 else ""
        job = {
            "command": "download",
            "video_id": video_id,
            "chat_id": chat_id,
            "chain": get_enabled_chain("download"),
        }
        enqueue_job(job)

    elif action == "thumb":
        video_id = parts[1]
        try:
            thumb_path, _ = youtube_core.download_thumbnail(video_id, "downloads")
            if thumb_path:
                send_document(chat_id, thumb_path, caption="تصویر بند‌انگشتی")
                safe_remove(thumb_path)
            else:
                send_message(chat_id, "❌ دریافت تامنیل ناموفق بود.")
        except Exception as e:
            _log.error(f"Thumb callback error: {e}")

    elif action == "page":
        page = int(parts[1])
        with last_search_lock:
            results = last_search.get("results", [])
            method_used = last_search.get("method_used", "")
            query = last_search.get("query", "")
        if not results:
            send_message(chat_id, "❌ نتایج جستجو منقضی شده است.")
            return
        limit = method_config.get("result_page_size", 5)
        total_pages = (len(results) + limit - 1) // limit
        page = max(0, min(page, total_pages - 1))
        keyboard = build_search_keyboard(results, page, total_pages, method_used)
        header = f"🔍 نتایج جستجو برای: {query}\nروش: {method_used}\nصفحه {page+1} از {total_pages}"
        edit_message_text(chat_id, message_id, header, reply_markup=keyboard)

    elif action == "next_search":
        method_used = parts[1]
        with last_search_lock:
            query = last_search.get("query", "")
            chain = last_search.get("chain", get_enabled_chain("search"))
        if not query:
            send_message(chat_id, "❌ اطلاعات جستجو نامعتبر است.")
            return
        try:
            idx = chain.index(method_used)
            next_method = chain[idx+1] if idx+1 < len(chain) else None
        except ValueError:
            next_method = None
        if not next_method:
            send_message(chat_id, "⛔ تمام متدها امتحان شدند.")
            return
        job = {
            "command": "search",
            "query": query,
            "limit": method_config.get("result_page_size", 5),
            "chain": chain,
            "start_method": next_method,
            "chat_id": chat_id,
        }
        enqueue_job(job)

    elif action == "next_info":
        # 🔧 اصلاح: حالا video_id از callback گرفته می‌شود
        video_id = parts[1]
        method_used = parts[2]
        chain = settings.DEFAULT_INFO_CHAIN
        try:
            idx = chain.index(method_used)
            next_method = chain[idx+1] if idx+1 < len(chain) else None
        except ValueError:
            next_method = None
        if not next_method:
            send_message(chat_id, "⛔ تمام متدهای اطلاعات امتحان شدند.")
            return
        job = {
            "command": "info",
            "video_id": video_id,
            "chat_id": chat_id,
            "chain": chain,
            "start_method": next_method,
        }
        enqueue_job(job)

    elif action == "batch_dl":
        job = {"command": "batch_download", "chat_id": chat_id}
        enqueue_job(job)

    elif action == "settings":
        keyboard = build_settings_menu()
        send_message(chat_id, "⚙️ تنظیمات", reply_markup=keyboard)

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
        answer_callback_query(callback_query_id, text="ذخیره شد.")
        send_message(chat_id, "تنظیمات ذخیره شد.", reply_markup=build_settings_menu())

    elif action == "settings_upload_mode":
        with method_config_lock:
            current = method_config.get("upload_mode", "playable_chunks")
            new_mode = "zip" if current == "playable_chunks" else "playable_chunks"
            method_config["upload_mode"] = new_mode
            save_method_config(method_config)
        answer_callback_query(callback_query_id, text=f"حالت آپلود: {new_mode}")

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
        answer_callback_query(callback_query_id, text=f"تعداد نتایج: {new_size}")

    elif action == "main_menu":
        send_message(chat_id, "منوی اصلی", reply_markup=build_main_menu())

    elif action == "help":
        help_text = (
            "🤖 ربات دانلودر یوتیوب\n\n"
            "🔍 جستجو: عبارت خود را بفرستید.\n"
            "📥 دانلود با لینک: لینک ویدئو را بفرستید.\n"
            "⚙️ تنظیمات: متدهای دانلود/جستجو، حالت آپلود و تعداد نتایج.\n"
            "📄 دریافت لاگ: فایل bot.log ارسال می‌شود.\n\n"
            "روش‌ها: hubytconvert (پیش‌فرض)، y2mate و ... با قابلیت تعویض زنجیره."
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
    if text.startswith("/start"):
        send_message(chat_id, "به ربات یوتیوب خوش آمدید.", reply_markup=build_main_menu())
        return
    if text.startswith("/help"):
        handle_callback(chat_id, "help", 0, "0")
        return
    if text.startswith("/log"):
        handle_callback(chat_id, "log", 0, "0")
        return

    state = user_state.get(chat_id)
    if state == "awaiting_query":
        limit = method_config.get("result_page_size", 5)
        chain = get_enabled_chain("search")
        job = {
            "command": "search",
            "query": text,
            "limit": limit,
            "chain": chain,
            "chat_id": chat_id,
        }
        enqueue_job(job)
        user_state[chat_id] = None
        send_message(chat_id, "⏳ در حال جستجو...")

    elif state == "awaiting_url":
        vid = extract_video_id(text)
        if not vid:
            send_message(chat_id, "❌ لینک یوتیوب معتبر نیست.")
            return
        job = {
            "command": "info",
            "video_id": vid,
            "chain": settings.DEFAULT_INFO_CHAIN,
            "chat_id": chat_id,
        }
        enqueue_job(job)
        user_state[chat_id] = None
        send_message(chat_id, "⏳ در حال دریافت اطلاعات...")
    else:
        pass


# -------------------------------------------------------------------
# حلقهٔ اصلی
# -------------------------------------------------------------------
def main() -> None:
    global method_config
    _log.info("Bot starting...")
    method_config = load_method_config()

    worker = threading.Thread(target=worker_loop, daemon=True)
    worker.start()

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
