"""
youtube_core.py - هسته اصلی عملیات یوتیوب (جستجو، دانلود، اطلاعات)
شامل 12 متد API با موتور fallback هوشمند.
نسخه نهایی: رفع کامل باگ y2mate و view_count در piped.
"""

import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

import settings
from utils import get_logger, download_file, extract_video_id

_log = get_logger("youtube_core")


# ═══════════════════════════════════════════════════════════
# موتور fallback
# ═══════════════════════════════════════════════════════════
def run_with_fallback(
    chain: List[str],
    operation_func: Callable[[str, dict], Any],
    start_method: Optional[str] = None,
    **kwargs
) -> Tuple[Any, Optional[str]]:
    """
    متدهای یک زنجیره را به ترتیب صدا می‌زند؛ اولین موفقیت را برمی‌گرداند.
    اگر start_method داده شود، فقط از آن متد به بعد تلاش می‌کند.
    """
    if not chain:
        return None, None

    start_idx = 0
    if start_method:
        try:
            start_idx = chain.index(start_method)
        except ValueError:
            _log.warning(f"start_method '{start_method}' در زنجیره وجود ندارد. از ابتدا شروع می‌شود.")

    for method in chain[start_idx:]:
        _log.info(f"اجرای متد: {method}")
        try:
            result = operation_func(method, kwargs)
            if result is not None:
                # لیست خالی یعنی متد کار کرد ولی نتیجه‌ای نیافت
                if isinstance(result, list) and len(result) == 0:
                    _log.debug(f"متد {method} نتیجه خالی برگرداند، ادامه...")
                    continue
                _log.info(f"موفقیت با متد: {method}")
                return result, method
        except Exception as e:
            _log.error(f"خطای غیرمنتظره در متد {method}: {e}", exc_info=True)
    return None, None


# ═══════════════════════════════════════════════════════════
# helper‌ها
# ═══════════════════════════════════════════════════════════
def _parse_innertube_renderer(data: dict, limit: int) -> List[Dict]:
    """تجزیه پاسخ جستجوی innertube و استخراج videoRendererها."""
    results = []
    try:
        contents = data.get("contents", {})
        # ساختار کلاسیک
        two_col = contents.get("twoColumnSearchResultsRenderer", {})
        primary = two_col.get("primaryContents", {})
        sections = primary.get("sectionListRenderer", {}).get("contents", [])
        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for item in items:
                video = item.get("videoRenderer")
                if video:
                    results.append(_extract_video_from_renderer(video))
                    if len(results) >= limit:
                        return results
        # ساختار rich grid جدید
        if not results:
            rich = contents.get("richGridRenderer", {}).get("contents", [])
            for item in rich:
                video = item.get("richItemRenderer", {}).get("content", {}).get("videoRenderer")
                if video:
                    results.append(_extract_video_from_renderer(video))
                    if len(results) >= limit:
                        return results
    except Exception as e:
        _log.error(f"خطا در تجزیه innertube: {e}")
    return results[:limit]


def _extract_video_from_renderer(renderer: dict) -> dict:
    """استخراج فیلدهای اصلی از یک videoRenderer."""
    video_id = renderer.get("videoId", "")
    title_runs = renderer.get("title", {}).get("runs", [{}])
    title = "".join(run.get("text", "") for run in title_runs) if title_runs else ""
    thumb = renderer.get("thumbnail", {}).get("thumbnails", [{}])
    thumb_url = thumb[0].get("url", "") if thumb else ""
    duration = renderer.get("lengthText", {}).get("simpleText", "")
    uploader_runs = renderer.get("ownerText", {}).get("runs", [{}])
    uploader = "".join(run.get("text", "") for run in uploader_runs) if uploader_runs else ""
    return {
        "video_id": video_id,
        "title": title,
        "duration": duration,
        "thumbnail_url": thumb_url,
        "uploader": uploader
    }


def _find_downloaded_file(video_id: str, save_dir: str, ext: str = ".mp4") -> Optional[str]:
    """پیدا کردن فایل دانلود شده با پیشوند video_id و پسوند ext."""
    if not os.path.isdir(save_dir):
        return None
    for fname in os.listdir(save_dir):
        if fname.startswith(video_id) and fname.endswith(ext):
            return os.path.join(save_dir, fname)
    return None


# ═══════════════════════════════════════════════════════════
# متدهای جستجو (Search)
# ═══════════════════════════════════════════════════════════
def _search_simatwa_search(query: str, limit: int) -> Optional[List[Dict]]:
    """Simatwa Search API (نیازمند نمونه در حال اجرا)"""
    base = getattr(settings, "SIMATWA_API_BASE", None)
    if not base:
        _log.warning("SIMATWA_API_BASE تنظیم نشده. متد simatwa_search رد می‌شود.")
        return None
    try:
        resp = requests.get(
            f"{base}/api/search",
            params={"q": query, "limit": limit},
            timeout=settings.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        results = []
        for item in items[:limit]:
            results.append({
                "video_id": item.get("id", ""),
                "title": item.get("title", ""),
                "duration": item.get("duration", 0),
                "thumbnail_url": item.get("thumbnail", ""),
                "uploader": item.get("uploader", "")
            })
        return results
    except Exception as e:
        _log.error(f"simatwa_search failed: {e}")
        return None


def _search_samzong(query: str, limit: int) -> Optional[List[Dict]]:
    """samzong yt-search-api (عمومی یا خودمیزبان)"""
    base = getattr(settings, "SAMZONG_API_BASE", "https://yt-search-api-sable.vercel.app")
    token = getattr(settings, "SAMZONG_API_TOKEN", None)
    headers = {"User-Agent": settings.USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = requests.get(
            f"{base}/search",
            params={"platform": "youtube", "q": query},
            headers=headers,
            timeout=settings.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", [])
        results = []
        for item in items[:limit]:
            results.append({
                "video_id": item.get("videoId", ""),
                "title": item.get("title", ""),
                "duration": None,               # این API مدت زمان ندارد
                "thumbnail_url": item.get("thumbnailUrl", ""),
                "uploader": item.get("uploader", "")
            })
        return results
    except Exception as e:
        _log.error(f"samzong search failed: {e}")
        return None


def _search_piped(query: str, limit: int) -> Optional[List[Dict]]:
    """Piped API عمومی"""
    try:
        resp = requests.get(
            "https://pipedapi.kavin.rocks/search",
            params={"q": query, "filter": "videos"},
            timeout=settings.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        items = [i for i in data.get("items", []) if i.get("type") == "video"]
        results = []
        for item in items[:limit]:
            results.append({
                "video_id": item.get("videoId", ""),
                "title": item.get("title", ""),
                "duration": item.get("duration", 0),
                "thumbnail_url": item.get("thumbnail", ""),
                "uploader": item.get("uploaderName", "")
            })
        return results
    except Exception as e:
        _log.error(f"piped search failed: {e}")
        return None


def _search_innertube2(query: str, limit: int) -> Optional[List[Dict]]:
    """InnerTube v2 (کتابخانه innertube)"""
    try:
        from innertube import InnerTube
    except ImportError:
        _log.warning("innertube library نصب نیست. متد innertube2 رد می‌شود.")
        return None
    try:
        client = InnerTube("WEB")
        data = client.search(query)
        return _parse_innertube_renderer(data, limit)
    except Exception as e:
        _log.error(f"innertube2 search failed: {e}")
        return None


def _search_scrapetube(query: str, limit: int) -> Optional[List[Dict]]:
    """scrapetube (فقط شناسه ویدئوها)"""
    try:
        import scrapetube
    except ImportError:
        _log.warning("scrapetube library نصب نیست.")
        return None
    try:
        videos = scrapetube.get_search(query, limit=limit)
        results = []
        for v in videos:
            vid = v.get("videoId", "")
            results.append({
                "video_id": vid,
                "title": None,
                "duration": None,
                "thumbnail_url": f"https://img.youtube.com/vi/{vid}/mqdefault.jpg",
                "uploader": None
            })
        return results
    except Exception as e:
        _log.error(f"scrapetube search failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# متدهای دانلود (Download)
# ═══════════════════════════════════════════════════════════
def _download_hubytconvert(video_id: str, save_dir: str) -> Optional[str]:
    """hub.ytconvert.org (مطمئن‌ترین متد)"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "User-Agent": settings.USER_AGENT,
        "Accept": "application/json",
        "Referer": "https://media.ytmp3.gg/",
        "Origin": "https://media.ytmp3.gg",
        "Content-Type": "application/json"
    }
    try:
        s = requests.Session()
        s.get("https://media.ytmp3.gg/", headers={"User-Agent": settings.USER_AGENT}, timeout=10)
    except Exception:
        pass

    try:
        resp = s.post(
            "https://hub.ytconvert.org/api/download",
            json={
                "url": url,
                "os": "linux",
                "output": {"type": "video", "format": "mp4", "quality": "720p"}
            },
            headers=headers,
            timeout=settings.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        status_url = data.get("statusUrl")
        if not status_url:
            _log.error("hubytconvert: statusUrl دریافت نشد.")
            return None
    except Exception as e:
        _log.error(f"hubytconvert init failed: {e}")
        return None

    for attempt in range(1, 91):
        try:
            status_resp = s.get(status_url, headers=headers, timeout=10)
            status_resp.raise_for_status()
            status_data = status_resp.json()
            if status_data.get("status") == "completed":
                dl_url = status_data.get("downloadUrl")
                if not dl_url:
                    _log.error("hubytconvert: completed but no downloadUrl")
                    return None
                fname = f"{video_id}.mp4"
                return download_file(dl_url, save_dir, fname, timeout=180)
            elif status_data.get("status") == "error":
                _log.error(f"hubytconvert error: {status_data.get('message', 'unknown')}")
                return None
            time.sleep(2)
        except Exception as e:
            _log.error(f"hubytconvert polling error: {e}")
            return None
    _log.error("hubytconvert timeout (90 تلاش)")
    return None


def _download_y2mate(video_id: str, save_dir: str) -> Optional[str]:
    """y2mate downloader (نیازمند cf_clearance) – اصلاح‌شده: ارسال format به save"""
    cf_clearance = getattr(settings, "Y2MATE_CF_CLEARANCE", None)
    if not cf_clearance:
        _log.warning("Y2MATE_CF_CLEARANCE تنظیم نشده. متد y2mate رد می‌شود.")
        return None
    try:
        from y2mate_api import Handler
    except ImportError:
        _log.warning("y2mate_api library نصب نیست.")
        return None

    try:
        # ساخت session با کوکی ضد کلاودفلر
        sess = requests.Session()
        sess.cookies.set("cf_clearance", cf_clearance, domain="y2mate.com")
        sess.headers.update({"User-Agent": settings.USER_AGENT})

        handler = Handler(f"https://youtube.com/watch?v={video_id}", session=sess)

        # انتخاب بهترین فرمت mp4
        best_format = None
        best_quality = -1
        for meta in handler.run():
            if meta.get("type") == "video" and meta.get("f", "").startswith("mp4"):
                quality_str = meta.get("quality", "0p")
                try:
                    quality_num = int(quality_str.rstrip("p"))
                except ValueError:
                    quality_num = 0
                if quality_num > best_quality:
                    best_quality = quality_num
                    best_format = meta

        if best_format is None:
            _log.error("y2mate: هیچ فرمت ویدیویی mp4 یافت نشد.")
            return None

        _log.info(f"y2mate: انتخاب کیفیت {best_format.get('quality')} - شروع دانلود با format=...")

        # 🔧 اصلاح اصلی: پاس دادن best_format به متد save
        file_path = handler.save(save_dir, format=best_format)
        if file_path and os.path.isfile(file_path):
            return file_path

        # fallback: جستجوی فایل با الگوی video_id
        downloaded = _find_downloaded_file(video_id, save_dir)
        if downloaded:
            return downloaded

        # در نهایت، آخرین فایل mp4 ایجاد شده در پوشه
        try:
            mp4_files = [f for f in os.listdir(save_dir) if f.endswith(".mp4")]
            if mp4_files:
                mp4_files.sort(key=lambda f: os.path.getmtime(os.path.join(save_dir, f)), reverse=True)
                return os.path.join(save_dir, mp4_files[0])
        except Exception:
            pass

        _log.error("y2mate: فایل دانلود شده یافت نشد.")
        return None

    except Exception as e:
        _log.error(f"y2mate download failed: {e}")
        return None


def _download_simatwa(video_id: str, save_dir: str) -> Optional[str]:
    """Simatwa downloader (نیازمند نمونه در حال اجرا)"""
    base = getattr(settings, "SIMATWA_API_BASE", None)
    if not base:
        _log.warning("SIMATWA_API_BASE تنظیم نشده.")
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        dl_url = f"{base}/api/download?url={url}&type=video&quality=720p"
        return download_file(dl_url, save_dir, f"{video_id}.mp4", timeout=180)
    except Exception as e:
        _log.error(f"simatwa download failed: {e}")
        return None


def _download_dark0013(video_id: str, save_dir: str) -> Optional[str]:
    """dark0013 DownloaderAPI (نیازمند نمونه در حال اجرا)"""
    base = getattr(settings, "DARK0013_API_BASE", None)
    if not base:
        _log.warning("DARK0013_API_BASE تنظیم نشده.")
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        dl_url = f"{base}/download?url={url}&type=video"
        return download_file(dl_url, save_dir, f"{video_id}.mp4", timeout=180)
    except Exception as e:
        _log.error(f"dark0013 download failed: {e}")
        return None


def _download_pointedsec(video_id: str, save_dir: str) -> Optional[str]:
    """pointedsec yt-converter-api (نیازمند نمونه + توکن)"""
    base = getattr(settings, "POINTEDSEC_API_BASE", None)
    token = getattr(settings, "POINTEDSEC_API_TOKEN", None)
    if not base or not token:
        _log.warning("POINTEDSEC_API_BASE یا POINTEDSEC_API_TOKEN تنظیم نشده.")
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        # مرحله تبدیل
        r = requests.post(
            f"{base}/convert",
            json={"url": url, "format": "mp4", "quality": "720p"},
            headers=headers,
            timeout=settings.REQUEST_TIMEOUT
        )
        r.raise_for_status()
        job = r.json()
        file_id = job.get("fileId") or job.get("id")
        if not file_id:
            _log.error("pointedsec: fileId دریافت نشد.")
            return None

        # دانلود
        dl_resp = requests.get(
            f"{base}/download/{file_id}",
            headers=headers,
            stream=True,
            timeout=180
        )
        dl_resp.raise_for_status()
        dest = os.path.join(save_dir, f"{video_id}.mp4")
        with open(dest, 'wb') as f:
            for chunk in dl_resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return dest
    except Exception as e:
        _log.error(f"pointedsec download failed: {e}")
        return None


def _download_tmwgsicp(video_id: str, save_dir: str) -> Optional[str]:
    """tmwgsicp video-download-api (نیازمند نمونه)"""
    base = getattr(settings, "TMWGSICP_API_BASE", None)
    if not base:
        _log.warning("TMWGSICP_API_BASE تنظیم نشده.")
        return None
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        r = requests.post(
            f"{base}/api/download",
            json={"url": url, "type": "video"},
            timeout=settings.REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        dl_url = data.get("downloadUrl") or data.get("url")
        if not dl_url:
            _log.error("tmwgsicp: downloadUrl دریافت نشد.")
            return None
        return download_file(dl_url, save_dir, f"{video_id}.mp4", timeout=180)
    except Exception as e:
        _log.error(f"tmwgsicp download failed: {e}")
        return None


def _download_cobalt(video_id: str, save_dir: str) -> Optional[str]:
    """cobalt.tools API (عمومی، ممکن است محدود شود)"""
    url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": settings.USER_AGENT
    }
    try:
        r = requests.post(
            "https://api.cobalt.tools/api/json",
            json={"url": url, "videoQuality": "1080", "filenameStyle": "basic"},
            headers=headers,
            timeout=settings.REQUEST_TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        status = data.get("status")
        if status in ("tunnel", "redirect"):
            dl_url = data.get("url")
            if not dl_url:
                _log.error("cobalt: no URL in response")
                return None
            return download_file(dl_url, save_dir, f"{video_id}.mp4", timeout=180)
        elif status == "picker":
            picker = data.get("picker", [])
            for opt in picker:
                if opt.get("type") == "video":
                    dl_url = opt.get("url")
                    if dl_url:
                        return download_file(dl_url, save_dir, f"{video_id}.mp4", timeout=180)
            _log.error("cobalt picker but no video option")
            return None
        else:
            _log.error(f"cobalt status: {status}, error: {data.get('error', '')}")
            return None
    except Exception as e:
        _log.error(f"cobalt download failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# متدهای اطلاعات ویدئو (Info)
# ═══════════════════════════════════════════════════════════
def _info_simatwa_search(video_id: str) -> Optional[Dict]:
    """تلاش برای دریافت اطلاعات از Simatwa search (query = video_id)"""
    try:
        results = _search_simatwa_search(video_id, 1)
        if results and results[0].get("video_id") == video_id:
            item = results[0]
            return {
                "video_id": item["video_id"],
                "title": item["title"],
                "duration": item["duration"],
                "view_count": None,
                "thumbnail_url": item["thumbnail_url"],
                "uploader": item["uploader"],
                "description": None
            }
    except Exception as e:
        _log.error(f"_info_simatwa_search failed: {e}")
    return None


def _info_samzong(video_id: str) -> Optional[Dict]:
    """تلاش برای دریافت اطلاعات از samzong (search)"""
    try:
        results = _search_samzong(video_id, 1)
        if results and results[0].get("video_id") == video_id:
            item = results[0]
            return {
                "video_id": item["video_id"],
                "title": item["title"],
                "duration": item["duration"],
                "view_count": None,
                "thumbnail_url": item["thumbnail_url"],
                "uploader": item["uploader"],
                "description": None
            }
    except Exception as e:
        _log.error(f"_info_samzong failed: {e}")
    return None


def _info_piped(video_id: str) -> Optional[Dict]:
    """اطلاعات کامل از Piped streams (با view_count به جای views)"""
    try:
        resp = requests.get(
            f"https://pipedapi.kavin.rocks/streams/{video_id}",
            timeout=settings.REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "video_id": video_id,
            "title": data.get("title", ""),
            "duration": data.get("duration", 0),
            "view_count": data.get("views", 0),   # اصلاح: کلید داخلی views به view_count مپ شد
            "thumbnail_url": data.get("thumbnailUrl", ""),
            "uploader": data.get("uploader", ""),
            "description": data.get("description", "")
        }
    except Exception as e:
        _log.error(f"piped info failed: {e}")
        return None


def _info_innertube2(video_id: str) -> Optional[Dict]:
    """اطلاعات از InnerTube"""
    try:
        from innertube import InnerTube
    except ImportError:
        return None
    try:
        client = InnerTube("WEB")
        # ممکن است اسم متد فرق کند
        video_info = client.get_video(video_id) if hasattr(client, 'get_video') else client.video(video_id)
        if not video_info:
            return None
        details = video_info.get("videoDetails", {})
        return {
            "video_id": details.get("videoId", video_id),
            "title": details.get("title", ""),
            "duration": int(details.get("lengthSeconds", 0)),
            "view_count": int(details.get("viewCount", 0)),
            "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg",
            "uploader": details.get("author", ""),
            "description": details.get("shortDescription", "")
        }
    except Exception as e:
        _log.error(f"innertube2 info failed: {e}")
        return None


def _info_oembed(video_id: str) -> Optional[Dict]:
    """اطلاعات پایه از oEmbed"""
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
    try:
        resp = requests.get(url, timeout=settings.REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return {
            "video_id": video_id,
            "title": data.get("title", ""),
            "duration": None,
            "view_count": None,
            "thumbnail_url": data.get("thumbnail_url", f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"),
            "uploader": data.get("author_name", ""),
            "description": None
        }
    except Exception as e:
        _log.error(f"oembed info failed: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# توابع عمومی (Public API)
# ═══════════════════════════════════════════════════════════
def search_youtube(
    query: str,
    limit: int = 10,
    chain: Optional[List[str]] = None,
    start_method: Optional[str] = None
) -> Tuple[List[Dict], Optional[str]]:
    """
    جستجوی یوتیوب با fallback. برمی‌گرداند (لیست نتایج, نام متد موفق).
    """
    if chain is None:
        chain = settings.DEFAULT_SEARCH_CHAIN

    search_map = {
        "simatwa_search": _search_simatwa_search,
        "samzong": _search_samzong,
        "piped": _search_piped,
        "innertube2": _search_innertube2,
        "scrapetube": _search_scrapetube
    }

    def op(method: str, kw: dict) -> Optional[List[Dict]]:
        func = search_map.get(method)
        if not func:
            return None
        return func(query=kw["query"], limit=kw["limit"])

    return run_with_fallback(chain, op, start_method, query=query, limit=limit)


def get_video_info(
    video_id: str,
    chain: Optional[List[str]] = None,
    start_method: Optional[str] = None
) -> Tuple[Dict, Optional[str]]:
    """
    دریافت اطلاعات ویدئو (دیکشنری) با fallback.
    """
    if chain is None:
        chain = settings.DEFAULT_INFO_CHAIN

    info_map = {
        "simatwa_search": _info_simatwa_search,
        "samzong": _info_samzong,
        "piped": _info_piped,
        "innertube2": _info_innertube2,
        "oembed": _info_oembed
    }

    def op(method: str, kw: dict) -> Optional[Dict]:
        func = info_map.get(method)
        if not func:
            return None
        return func(video_id=kw["video_id"])

    result, method = run_with_fallback(chain, op, start_method, video_id=video_id)
    if result is None:
        return {}, method
    return result, method


def download_video(
    video_id: str,
    save_dir: str,
    chain: Optional[List[str]] = None,
    start_method: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    دانلود ویدئو با fallback. برمی‌گرداند (مسیر فایل, نام متد).
    """
    os.makedirs(save_dir, exist_ok=True)
    if chain is None:
        chain = settings.DEFAULT_DOWNLOAD_CHAIN.copy()
    if "hubytconvert" not in chain:
        chain.insert(0, "hubytconvert")

    download_map = {
        "hubytconvert": _download_hubytconvert,
        "y2mate": _download_y2mate,
        "simatwa": _download_simatwa,
        "dark0013": _download_dark0013,
        "pointedsec": _download_pointedsec,
        "tmwgsicp": _download_tmwgsicp,
        "cobalt": _download_cobalt
    }

    def op(method: str, kw: dict) -> Optional[str]:
        func = download_map.get(method)
        if not func:
            return None
        return func(video_id=kw["video_id"], save_dir=kw["save_dir"])

    return run_with_fallback(chain, op, start_method, video_id=video_id, save_dir=save_dir)


def download_thumbnail(video_id: str, save_dir: str) -> Tuple[Optional[str], str]:
    """
    دانلود تصویر بندانگشتی با کیفیت‌های مختلف. برمی‌گرداند (مسیر, "direct").
    """
    os.makedirs(save_dir, exist_ok=True)
    qualities = ["maxresdefault.jpg", "sddefault.jpg", "hqdefault.jpg", "mqdefault.jpg"]
    for quality in qualities:
        thumb_url = f"https://img.youtube.com/vi/{video_id}/{quality}"
        try:
            head = requests.head(thumb_url, timeout=5)
            if head.status_code == 200:
                fname = f"{video_id}_thumb.jpg"
                path = download_file(thumb_url, save_dir, fname, timeout=30)
                if path:
                    return path, "direct"
        except Exception:
            continue
    return None, "direct"
