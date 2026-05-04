"""
settings.py - پیکربندی و ثابت‌های اصلی ربات یوتیوب Bale Ultimate
شامل توکن، متدهای جستجو و دانلود، زنجیره‌های پیش‌فرض، تنظیمات UI و آپلود
"""

import os
import sys

# ──────────────────────────────────────
# توکن و API
# ──────────────────────────────────────
BOT_TOKEN = os.environ.get("BALE_BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ BALE_BOT_TOKEN در متغیرهای محیطی یافت نشد.")
    sys.exit(1)

API_BASE = f"https://tapi.bale.ai/bot{BOT_TOKEN}"

# ──────────────────────────────────────
# زمان‌بندی شبکه
# ──────────────────────────────────────
REQUEST_TIMEOUT = 30          # ثانیه - زمان انتظار برای درخواست‌های معمولی
LONG_POLL_TIMEOUT = 50        # ثانیه - زمان انتظار برای getUpdates

# ──────────────────────────────────────
# محدودیت‌های اندازه فایل
# ──────────────────────────────────────
MAX_SEND_SIZE = 20 * 1024 * 1024       # 20 مگابایت - حداکثر اندازه ارسال در Bale
ZIP_PART_SIZE = 20 * 1024 * 1024       # 20 مگابایت - اندازه هر بخش در روش ZIP
MAX_VIDEO_DURATION = 7200             # ثانیه - حداکثر مدت ویدئو (2 ساعت)
MAX_DOWNLOAD_RETRIES = 3              # تعداد تلاش مجدد برای هر بخش در آپلود ناموفق

# ──────────────────────────────────────
# مسیرها و دایرکتوری‌ها
# ──────────────────────────────────────
DATA_DIR = "data"
QUEUE_FILE = os.path.join(DATA_DIR, "queue.json")
ADMIN_FILE = os.path.join(DATA_DIR, "admin.json")
METHOD_CONFIG_FILE = os.path.join(DATA_DIR, "method_config.json")
UPLOAD_STATE_FILE = os.path.join(DATA_DIR, "upload_state.json")
LOG_FILE = "bot.log"
DOWNLOADS_DIR = "downloads"
DEBUG_DIR = "debug"

# ──────────────────────────────────────
# تنظیمات شبکه و FFmpeg
# ──────────────────────────────────────
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
FFMPEG_PATH = "ffmpeg"   # فرض بر این است که در PATH سیستم موجود باشد

# ──────────────────────────────────────
# تنظیمات ادمین
# ──────────────────────────────────────
DEFAULT_ADMIN_CHAT_ID = 46829437   # در صورت نبود admin.json استفاده می‌شود

# ──────────────────────────────────────
# تعریف متدهای جستجو (SEARCH)
# هر متد شامل نام، ایموجی، توضیحات، نیاز به کلید، حداکثر نتایج و وضعیت فعال بودن
# ──────────────────────────────────────
SEARCH_METHODS = {
    "simatwa_search": {
        "name": "Simatwa Search API",
        "emoji": "🔍",
        "description": "جستجوی Simatwa (yt-search-api)",
        "requires_key": False,
        "max_results": 20,
        "enabled": True
    },
    "samzong": {
        "name": "samzong yt-search-api",
        "emoji": "🔎",
        "description": "API جستجوی samzong مبتنی بر yt-dlp",
        "requires_key": False,
        "max_results": 20,
        "enabled": True
    },
    "piped": {
        "name": "Piped API",
        "emoji": "🔎",
        "description": "API رایگان Piped (kavin.rocks)",
        "requires_key": False,
        "max_results": 20,
        "enabled": True
    },
    "innertube2": {
        "name": "InnerTube v2",
        "emoji": "🔎",
        "description": "پروتکل داخلی یوتیوب (InnerTube)",
        "requires_key": False,
        "max_results": 20,
        "enabled": True
    },
    "scrapetube": {
        "name": "Scrapetube",
        "emoji": "🔎",
        "description": "فقط شناسه ویدئوها (کتابخانه scrapetube)",
        "requires_key": False,
        "max_results": 10,
        "enabled": True
    }
}

# ──────────────────────────────────────
# تعریف متدهای دانلود (DOWNLOAD)
# هر متد شامل نام، ایموجی، توضیحات، حداکثر کیفیت و وضعیت فعال بودن
# ──────────────────────────────────────
DOWNLOAD_METHODS = {
    "hubytconvert": {
        "name": "hub.ytconvert.org",
        "emoji": "📥",
        "description": "API سریع hub.ytconvert",
        "requires_key": False,
        "max_quality": "1080p",
        "enabled": True
    },
    "y2mate": {
        "name": "y2mate-api",
        "emoji": "📥",
        "description": "کتابخانه y2mate (پایتون)",
        "requires_key": False,
        "max_quality": "1080p",
        "enabled": True
    },
    "simatwa": {
        "name": "Simatwa Download API",
        "emoji": "📥",
        "description": "API دانلود Simatwa با کیفیت تا 8K",
        "requires_key": False,
        "max_quality": "8k",
        "enabled": True
    },
    "dark0013": {
        "name": "DownloaderAPI",
        "emoji": "📥",
        "description": "API مبتنی بر FastAPI/yt-dlp (dark0013)",
        "requires_key": False,
        "max_quality": "1080p",
        "enabled": True
    },
    "pointedsec": {
        "name": "yt-converter-api",
        "emoji": "📥",
        "description": "API مبدل Go (pointedsec)",
        "requires_key": False,
        "max_quality": "1080p",
        "enabled": True
    },
    "tmwgsicp": {
        "name": "video-download-api",
        "emoji": "📥",
        "description": "API دانلود 30+ پلتفرم (tmwgsicp)",
        "requires_key": False,
        "max_quality": "1080p",
        "enabled": True
    },
    "cobalt": {
        "name": "Cobalt.tools",
        "emoji": "📥",
        "description": "API اختصاصی cobalt.tools",
        "requires_key": False,
        "max_quality": "1080p",
        "enabled": True
    }
}

# ──────────────────────────────────────
# تعریف متدهای دریافت اطلاعات ویدئو (INFO)
# این متدها برای دریافت جزئیات یک ویدئو (بدون جستجو) استفاده می‌شوند.
# شامل زیرمجموعه‌ای از متدهای جستجو + oembed
# ──────────────────────────────────────
INFO_METHODS = {
    "simatwa_search": {
        "name": "Simatwa Search (info)",
        "emoji": "ℹ️",
        "description": "جزئیات ویدئو از Simatwa API",
        "requires_key": False,
        "max_results": 1,
        "enabled": True
    },
    "samzong": {
        "name": "samzong Search (info)",
        "emoji": "ℹ️",
        "description": "جزئیات ویدئو از samzong API",
        "requires_key": False,
        "max_results": 1,
        "enabled": True
    },
    "piped": {
        "name": "Piped (info)",
        "emoji": "ℹ️",
        "description": "جزئیات ویدئو از Piped API",
        "requires_key": False,
        "max_results": 1,
        "enabled": True
    },
    "innertube2": {
        "name": "InnerTube (info)",
        "emoji": "ℹ️",
        "description": "جزئیات ویدئو از InnerTube",
        "requires_key": False,
        "max_results": 1,
        "enabled": True
    },
    "oembed": {
        "name": "YouTube oEmbed",
        "emoji": "ℹ️",
        "description": "اطلاعات پایه ویدئو از oEmbed یوتیوب",
        "requires_key": False,
        "max_results": 1,
        "enabled": True
    }
}

# ──────────────────────────────────────
# زنجیره‌های پیش‌فرض (ترتیب اولویت)
# ──────────────────────────────────────
DEFAULT_SEARCH_CHAIN = [
    "simatwa_search",
    "samzong",
    "piped",
    "innertube2",
    "scrapetube"
]

DEFAULT_INFO_CHAIN = [
    "simatwa_search",
    "samzong",
    "piped",
    "innertube2",
    "oembed"
]

DEFAULT_DOWNLOAD_CHAIN = [
    "hubytconvert",
    "y2mate",
    "simatwa",
    "dark0013",
    "pointedsec",
    "tmwgsicp",
    "cobalt"
]

# ──────────────────────────────────────
# تنظیمات ظاهری UI
# ──────────────────────────────────────
UI_SETTINGS = {
    "show_method_in_output": True,        # نمایش نام متد موفق در خروجی
    "show_thumbnails": True,              # نمایش تصویر بند‌انگشتی
    "show_download_button": True,         # دکمه دانلود زیر هر نتیجه
    "show_next_method_button": True,      # دکمه «متد بعدی» برای امتحان متد دیگر
    "result_page_size": 5,                # تعداد نتایج در هر صفحه
    "emoji_enabled": True                 # استفاده از ایموجی در پیام‌ها
}

# ──────────────────────────────────────
# تنظیمات پیش‌فرض نشست (در فایل method_config.json ذخیره می‌شود)
# ──────────────────────────────────────
DEFAULT_SESSION_SETTINGS = {
    "search_chain": DEFAULT_SEARCH_CHAIN.copy(),
    "info_chain": DEFAULT_INFO_CHAIN.copy(),
    "download_chain": DEFAULT_DOWNLOAD_CHAIN.copy(),
    "enabled_search_methods": list(SEARCH_METHODS.keys()),
    "enabled_download_methods": list(DOWNLOAD_METHODS.keys()),
    "upload_mode": "playable_chunks",        # یا "zip"
    "chunk_duration_seconds": 60,            # مدت زمان هر بخش در حالت playable_chunks
    "result_page_size": 5
}

# ──────────────────────────────────────
# حالت‌های آپلود
# ──────────────────────────────────────
UPLOAD_MODES = {
    "zip": {
        "description": "فشرده‌سازی کل ویدئو به ZIP و تقسیم به بخش‌های 20 مگابایتی"
    },
    "playable_chunks": {
        "description": "تقسیم ویدئو به قطعات 60 ثانیه‌ای قابل پخش با FFmpeg"
    }
}

# ──────────────────────────────────────
# کتابخانه‌های مورد نیاز (جهت اطلاع)
# ──────────────────────────────────────
REQUIRED_LIBS = [
    "requests",
    "yt_dlp",
    "scrapetube",
    "pybalt",
    "beautifulsoup4",
    "pytube",
    "pytubefix"
]

# ──────────────────────────────────────
# حالت دیباگ
# ──────────────────────────────────────
DEBUG_MODE = os.environ.get("DEBUG", "0") == "1"
