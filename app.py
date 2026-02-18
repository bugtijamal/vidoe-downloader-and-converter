from flask import (
    Flask, render_template, jsonify, send_file,
    after_this_request, request, Response
)
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import yt_dlp
import os
import uuid
import re
import threading
import time
import requests
from datetime import timedelta
from urllib.parse import urlparse, parse_qs
from io import BytesIO

from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TDRC, TCON
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import Picture
from PIL import Image

import logging
import traceback
import subprocess
import hashlib
import shutil
import base64

# psutil is optional but recommended (for killing ffmpeg cleanly)
try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

# ================== Logging ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('converter.log', encoding='utf-8', mode='a')
    ]
)
logger = logging.getLogger(__name__)

yt_dlp_logger = logging.getLogger('yt_dlp')
yt_dlp_logger.setLevel(logging.WARNING)

# ================== Flask App ==================
app = Flask(__name__)
CORS(app)

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri="memory://"
)

# ================== Config ==================
DOWNLOAD_FOLDER = 'downloads'
TEMP_FOLDER = 'temp'
MAX_DURATION = 14400           # 4 hours max
CLEANUP_AGE = 600              # 10 minutes
MAX_CONCURRENT_DOWNLOADS = 5
DOWNLOAD_TIMEOUT = 3600        # 1 hour max download time
STALL_TIMEOUT = 180            # 3 minutes without progress
PROCESSING_STALL_TIMEOUT = 600 # 10 minutes for processing
FFMPEG_TIMEOUT = 1800          # 30 minutes for ffmpeg

for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
    os.makedirs(folder, exist_ok=True)

conversion_progress = {}
progress_lock = threading.Lock()
active_downloads = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)

active_processes = {}
process_lock = threading.Lock()

video_info_cache = {}
cache_lock = threading.Lock()

thumbnail_cache = {}
thumbnail_cache_lock = threading.Lock()

VIDEO_QUALITIES = {
    'best':  'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
    '1080p': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best',
    '720p':  'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
    '480p':  'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best',
    '360p':  'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best',
    '144p':  'bestvideo[height<=144][ext=mp4]+bestaudio[ext=m4a]/best[height<=144][ext=mp4]/best',
}

VIDEO_ENCODE_SETTINGS = {
    '1080p': {'scale': '-2:1080', 'crf': '20', 'maxrate': '5000k', 'bufsize': '10000k'},
    '720p':  {'scale': '-2:720',  'crf': '22', 'maxrate': '2500k', 'bufsize': '5000k'},
    '480p':  {'scale': '-2:480',  'crf': '24', 'maxrate': '1500k', 'bufsize': '3000k'},
    '360p':  {'scale': '-2:360',  'crf': '26', 'maxrate': '800k',  'bufsize': '1600k'},
    '144p':  {'scale': '-2:144',  'crf': '28', 'maxrate': '400k',  'bufsize': '800k'},
    'best':  {'scale': None,      'crf': '20', 'maxrate': None,    'bufsize': None},
}

AUDIO_FORMATS = {
    'mp3': {
        'extension': 'mp3',
        'codec': 'libmp3lame',
        'bitrate': '320k',
        'sample_rate': '44100',
        'mime': 'audio/mpeg',
        'name': 'MP3',
        'quality': '320kbps',
        'description': 'Universal compatibility - works on all devices',
        'icon': '🎵',
        'recommended': True
    },
    'aac': {
        'extension': 'm4a',
        'codec': 'aac',
        'bitrate': '256k',
        'sample_rate': '44100',
        'mime': 'audio/mp4',
        'name': 'AAC (M4A)',
        'quality': '256kbps',
        'description': 'Best for Apple devices - excellent quality, smaller size',
        'icon': '🍎',
        'recommended': True
    },
    'opus': {
        'extension': 'opus',
        'codec': 'libopus',
        'bitrate': '192k',
        'sample_rate': '48000',
        'mime': 'audio/opus',
        'name': 'OPUS',
        'quality': '192kbps',
        'description': 'Modern codec - best quality/size ratio',
        'icon': '⚡',
        'recommended': False
    },
    'ogg': {
        'extension': 'ogg',
        'codec': 'libvorbis',
        'bitrate': '192k',
        'sample_rate': '44100',
        'mime': 'audio/ogg',
        'name': 'OGG Vorbis',
        'quality': '192kbps',
        'description': 'Open source - great for Android & desktop',
        'icon': '🤖',
        'recommended': False
    }
}

# ================== Title / Artist Helpers ==================
def extract_clean_title(info):
    """Extract a clean title without views/reactions/etc."""
    # Prefer clean fields
    for field in ['track', 'alt_title']:
        v = info.get(field)
        if v and len(v.strip()) >= 3:
            return v.strip()

    # Fallback to title
    title = info.get('title') or info.get('fulltitle') or ''
    if not title:
        desc = info.get('description', '')
        if desc:
            title = desc.split('\n')[0][:150]

    if not title:
        return 'download'

    # Remove "56K views", "2.8K reactions", "123 likes", etc.
    cleaned = re.sub(
        r'[\s\|\-_•·:]*\d+[\d,\.]*\s*[KkMmBb]?\s*'
        r'(views?|reactions?|likes?|comments?|shares?|plays?)[\s\|\-_•·:]*',
        ' ',
        title,
        flags=re.IGNORECASE
    )

    # Remove platform suffixes
    cleaned = re.sub(
        r'\s*[\|\-•·:]\s*(Facebook|Instagram|TikTok|YouTube|Reels?|Watch)\s*$',
        '',
        cleaned,
        flags=re.IGNORECASE
    )

    cleaned = re.sub(r'[\s_]+', ' ', cleaned).strip(' _-|•·')
    return cleaned if len(cleaned) >= 3 else 'download'


def extract_clean_artist(info):
    """Extract a clean artist/uploader name."""
    artist = (
        info.get('artist') or
        info.get('creator') or
        info.get('uploader') or
        info.get('channel') or
        ''
    )

    if artist:
        artist = re.sub(
            r'\s*[-–]\s*(Official|VEVO|Music|Records|Channel).*$', '',
            artist, flags=re.IGNORECASE
        )

    return artist.strip() or 'Unknown'


def sanitize_filename(title, max_length=100):
    """Convert title to a filesystem-safe filename."""
    if not title:
        return 'download'

    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
    filename = re.sub(r'[\s\-]+', '_', filename)
    filename = re.sub(r'[^\w\-_.]', '', filename)
    filename = re.sub(r'_+', '_', filename).strip('._')

    if len(filename) > max_length:
        filename = filename[:max_length].strip('._')

    return filename or 'download'


# ================== Thumbnail Helpers ==================
def get_best_thumbnail(info):
    """Return the best thumbnail URL from yt-dlp info."""
    thumbs = info.get('thumbnails', [])
    if not thumbs:
        return info.get('thumbnail', '')

    thumbs_sorted = sorted(
        thumbs,
        key=lambda x: (x.get('height', 0) or 0) * (x.get('width', 0) or 0),
        reverse=True
    )
    for t in thumbs_sorted:
        url = t.get('url', '')
        if url and not url.endswith('.webp'):
            return url
    return thumbs_sorted[0].get('url', '') if thumbs_sorted else info.get('thumbnail', '')


def fetch_thumbnail_bytes(url):
    """Fetch thumbnail bytes (supports Facebook/Instagram/TikTok)."""
    if not url:
        return None
    try:
        headers = {
            'User-Agent':
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36',
            'Accept':
                'image/avif,image/webp,image/apng,image/svg+xml,'
                'image/*,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.facebook.com/',
        }
        resp = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        resp.raise_for_status()

        ctype = resp.headers.get('content-type', '')
        if 'image' in ctype or len(resp.content) > 1000:
            return resp.content
        return None
    except Exception as e:
        logger.warning(f"[THUMB FETCH] Failed: {e}")
        return None


def download_thumbnail(url, save_path):
    """Download thumbnail and make it a 600x600 JPEG for artwork."""
    try:
        img_data = fetch_thumbnail_bytes(url)
        if not img_data:
            return False

        img = Image.open(BytesIO(img_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')

        w, h = img.size
        if w != h:
            min_side = min(w, h)
            left = (w - min_side) // 2
            top = (h - min_side) // 2
            img = img.crop((left, top, left + min_side, top + min_side))

        if img.width != 600:
            img = img.resize((600, 600), Image.Resampling.LANCZOS)

        img.save(save_path, 'JPEG', quality=95, optimize=True)
        return os.path.exists(save_path)
    except Exception as e:
        logger.error(f"[THUMB SAVE] Error: {e}")
        return False


# ================== Timeout Helpers ==================
def calculate_timeout(duration):
    if not duration:
        return DOWNLOAD_TIMEOUT
    base = 600
    factor = (duration // 600) * 120
    extra = ((duration - 3600) // 600) * 60 if duration > 3600 else 0
    return min(base + factor + extra, 7200)


def calculate_ffmpeg_timeout(duration):
    if not duration:
        return FFMPEG_TIMEOUT
    base = 300
    factor = (duration // 60) * 30
    return min(base + factor, 7200)


# ================== Process Helpers ==================
def kill_process_tree(pid):
    """Kill a process and its children (if psutil is available)."""
    if not HAS_PSUTIL:
        try:
            os.kill(pid, 9)
        except Exception:
            pass
        return True

    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        return True
    except Exception as e:
        logger.error(f"[KILL] Error: {e}")
        return False


def run_ffmpeg_with_progress(cmd, task_id, timeout=1800, stage="processing"):
    """Run ffmpeg and keep progress alive to avoid stall detection."""
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )

        with process_lock:
            active_processes[task_id] = process

        start_time = time.time()
        last_update = start_time

        while True:
            poll = process.poll()
            if poll is not None:
                _, stderr = process.communicate()
                with process_lock:
                    active_processes.pop(task_id, None)
                if poll == 0:
                    return True, ""
                return False, stderr

            if time.time() - start_time > timeout:
                logger.error(f"[FFMPEG] Timeout for {task_id[:8]}")
                try:
                    process.kill()
                except Exception:
                    pass
                with process_lock:
                    active_processes.pop(task_id, None)
                return False, "FFmpeg timeout"

            if time.time() - last_update > 10:
                with progress_lock:
                    if task_id in conversion_progress:
                        conversion_progress[task_id]['last_update'] = time.time()
                        cur = conversion_progress[task_id]
                        if cur.get('status') == stage:
                            elapsed = int(time.time() - start_time)
                            base = cur.get('message', 'Processing').split('(')[0].strip()
                            cur['message'] = f"{base} ({elapsed}s)..."
                last_update = time.time()

            time.sleep(0.5)
    except Exception as e:
        logger.error(f"[FFMPEG] Exception: {e}")
        with process_lock:
            active_processes.pop(task_id, None)
        return False, str(e)


# ================== Stall Detection ==================
def check_stalled_downloads():
    while True:
        try:
            time.sleep(15)
            now = time.time()
            with progress_lock:
                for task_id, info in list(conversion_progress.items()):
                    status = info.get('status', '')
                    if status in ['completed', 'error', 'cancelled', 'unknown']:
                        continue
                    last = info.get('last_update', 0)
                    if not last:
                        continue
                    stall = now - last
                    timeout = PROCESSING_STALL_TIMEOUT if status in ['processing', 'embedding'] else STALL_TIMEOUT
                    if stall > timeout:
                        logger.warning(f"[STALL] {task_id[:8]} stalled in '{status}' for {int(stall)}s")
                        with process_lock:
                            if task_id in active_processes:
                                proc = active_processes[task_id]
                                try:
                                    if hasattr(proc, 'pid'):
                                        kill_process_tree(proc.pid)
                                    else:
                                        proc.kill()
                                    active_processes.pop(task_id, None)
                                except Exception:
                                    pass
                        conversion_progress[task_id] = {
                            'status': 'error',
                            'percent': info.get('percent', 0),
                            'message': 'Process stalled. Please try again.',
                            'last_update': now
                        }
                        for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
                            try:
                                for fname in os.listdir(folder):
                                    if fname.startswith(task_id):
                                        os.remove(os.path.join(folder, fname))
                            except Exception:
                                pass
        except Exception as e:
            logger.error(f"[STALL CHECK] Error: {e}")


threading.Thread(target=check_stalled_downloads, daemon=True).start()


# ================== Cleanup Thread ==================
def cleanup_old_files():
    while True:
        try:
            now = time.time()
            cleaned = 0
            for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
                if not os.path.exists(folder):
                    continue
                for name in os.listdir(folder):
                    path = os.path.join(folder, name)
                    try:
                        if os.path.isfile(path):
                            age = now - os.path.getmtime(path)
                            if age > CLEANUP_AGE:
                                task_id = name.split('.')[0].split('_')[0]
                                with progress_lock:
                                    info = conversion_progress.get(task_id, {})
                                    if info.get('status') not in ['downloading', 'processing', 'embedding', 'connecting', 'starting']:
                                        os.remove(path)
                                        cleaned += 1
                    except Exception:
                        pass
            # cleanup thumbnail cache
            with thumbnail_cache_lock:
                old_keys = [k for k, v in thumbnail_cache.items() if now - v.get('time', 0) > 300]
                for k in old_keys:
                    thumbnail_cache.pop(k, None)
            if cleaned:
                logger.info(f"[CLEANUP] Removed {cleaned} files")
        except Exception as e:
            logger.error(f"[CLEANUP] Error: {e}")
        time.sleep(300)


threading.Thread(target=cleanup_old_files, daemon=True).start()


# ================== URL Helpers ==================
def normalize_youtube_url(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '').replace('m.', '')
        if 'youtube.com' in domain:
            query = parse_qs(parsed.query)
            if 'v' in query:
                return f"https://www.youtube.com/watch?v={query['v'][0]}"
            if '/shorts/' in parsed.path:
                m = re.search(r'/shorts/([a-zA-Z0-9_-]+)', parsed.path)
                if m:
                    return f"https://www.youtube.com/watch?v={m.group(1)}"
        elif 'youtu.be' in domain:
            vid = parsed.path.strip('/')
            if vid:
                return f"https://www.youtube.com/watch?v={vid}"
        return url
    except Exception:
        return url


def validate_url(url):
    """Return True if URL looks like a direct video link we support."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '').replace('m.', '').replace('web.', '').lower()
        path = parsed.path

        # YouTube
        if domain in ['youtube.com', 'youtu.be']:
            if domain == 'youtube.com':
                query = parse_qs(parsed.query)
                return 'v' in query or '/shorts/' in path or '/watch' in path
            return len(path.strip('/')) > 0

        # Facebook - block profile/home, allow watch/reel/videos
        if 'facebook.com' in domain or domain in ['fb.watch', 'fb.com']:
            if '/profile.php' in path and 'v=' not in parsed.query:
                return False
            if path in ['', '/']:
                return False
            valid_fb = [
                '/watch', '/reel/', '/reels/', '/videos/', 'video.php', 'v='
            ]
            return any(p in url for p in valid_fb)

        # Other platforms
        if any(p in domain for p in [
            'instagram.com', 'tiktok.com', 'vm.tiktok.com',
            'twitter.com', 'x.com', 't.co'
        ]):
            return True

        return False
    except Exception:
        return False


def get_platform(url):
    d = urlparse(url).netloc.lower()
    if 'youtube' in d or 'youtu.be' in d:
        return 'YouTube'
    if 'facebook' in d or 'fb.' in d:
        return 'Facebook'
    if 'instagram' in d:
        return 'Instagram'
    if 'tiktok' in d:
        return 'TikTok'
    if 'twitter' in d or 'x.com' in d:
        return 'Twitter/X'
    return 'Unknown'


# ================== Metadata Embedding ==================
def embed_metadata_mp3_mutagen(mp3_path, metadata, thumbnail_path=None):
    try:
        audio = MP3(mp3_path, ID3=ID3)
        if audio.tags:
            audio.tags.delete()
        audio.add_tags()
        if metadata.get('title'):
            audio.tags.add(TIT2(encoding=3, text=metadata['title']))
        if metadata.get('artist'):
            audio.tags.add(TPE1(encoding=3, text=metadata['artist']))
        if metadata.get('year'):
            audio.tags.add(TDRC(encoding=3, text=str(metadata['year'])))
        if metadata.get('genre'):
            audio.tags.add(TCON(encoding=3, text=metadata['genre']))
        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, 'rb') as f:
                audio.tags.add(APIC(
                    encoding=3, mime='image/jpeg', type=3, desc='', data=f.read()
                ))
        audio.save(v2_version=3)
        return True
    except Exception as e:
        logger.error(f"[MP3-METADATA] {e}")
        return False


def embed_metadata_aac(m4a_path, metadata, thumbnail_path=None):
    try:
        audio = MP4(m4a_path)
        if metadata.get('title'):
            audio['\xa9nam'] = metadata['title']
        if metadata.get('artist'):
            audio['\xa9ART'] = metadata['artist']
        if metadata.get('year'):
            audio['\xa9day'] = str(metadata['year'])
        if metadata.get('genre'):
            audio['\xa9gen'] = metadata['genre']
        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, 'rb') as f:
                audio['covr'] = [MP4Cover(f.read(), imageformat=MP4Cover.FORMAT_JPEG)]
        audio.save()
        return True
    except Exception as e:
        logger.error(f"[AAC-METADATA] {e}")
        return False


def embed_metadata_opus(opus_file, metadata, thumbnail_path=None):
    try:
        audio = OggOpus(opus_file)
        if metadata.get('title'):
            audio['TITLE'] = metadata['title']
        if metadata.get('artist'):
            audio['ARTIST'] = metadata['artist']
        if metadata.get('year'):
            audio['DATE'] = str(metadata['year'])
        if metadata.get('genre'):
            audio['GENRE'] = metadata['genre']
        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, 'rb') as f:
                img_data = f.read()
            picture = Picture()
            picture.type = 3
            picture.mime = 'image/jpeg'
            picture.desc = 'Cover'
            picture.data = img_data
            img = Image.open(BytesIO(img_data))
            picture.width, picture.height = img.size
            picture.depth = 24
            audio['METADATA_BLOCK_PICTURE'] = base64.b64encode(
                picture.write()
            ).decode('ascii')
        audio.save()
        return True
    except Exception as e:
        logger.error(f"[OPUS-METADATA] {e}")
        return False


def embed_metadata_ogg(ogg_file, metadata, thumbnail_path=None):
    try:
        audio = OggVorbis(ogg_file)
        if metadata.get('title'):
            audio['TITLE'] = metadata['title']
        if metadata.get('artist'):
            audio['ARTIST'] = metadata['artist']
        if metadata.get('year'):
            audio['DATE'] = str(metadata['year'])
        if metadata.get('genre'):
            audio['GENRE'] = metadata['genre']
        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, 'rb') as f:
                img_data = f.read()
            picture = Picture()
            picture.type = 3
            picture.mime = 'image/jpeg'
            picture.desc = 'Cover'
            picture.data = img_data
            img = Image.open(BytesIO(img_data))
            picture.width, picture.height = img.size
            picture.depth = 24
            audio['METADATA_BLOCK_PICTURE'] = base64.b64encode(
                picture.write()
            ).decode('ascii')
        audio.save()
        return True
    except Exception as e:
        logger.error(f"[OGG-METADATA] {e}")
        return False


# ================== Progress Hook ==================
def progress_hook(d, task_id):
    """Update conversion_progress with speed, eta, sizes (numeric + string)."""
    try:
        with progress_lock:
            if task_id not in conversion_progress:
                return

            # Always update timestamp
            conversion_progress[task_id]['last_update'] = time.time()

            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)

                # Raw numeric speed (bytes/s) - may be None
                raw_speed = d.get('speed') or 0
                # Human readable speed from yt-dlp, e.g. "1.23MiB/s"
                speed_str = d.get('_speed_str') or ''

                # ETA in seconds
                eta = d.get('eta') or 0

                # Compute percent
                if total > 0:
                    percent = min((downloaded / total) * 85, 85)
                else:
                    percent = min(conversion_progress[task_id].get('percent', 5) + 0.3, 85)

                # Build message
                downloaded_mb = downloaded / (1024 * 1024) if downloaded else 0
                total_mb = total / (1024 * 1024) if total else 0

                if total_mb > 0:
                    msg = f"Downloading... {int(percent)}% ({downloaded_mb:.1f}/{total_mb:.1f} MB)"
                else:
                    msg = f"Downloading... {int(percent)}%"

                # Append speed to message if available
                if speed_str:
                    msg += f" @ {speed_str}"

                # Store everything in progress dict
                conversion_progress[task_id].update({
                    'status': 'downloading',
                    'percent': percent,
                    'speed': raw_speed,          # numeric bytes/s
                    'speed_str': speed_str,      # human readable, e.g. "1.23MiB/s"
                    'eta': eta,
                    'downloaded_bytes': downloaded,
                    'total_bytes': total,
                    'message': msg
                })

            elif d['status'] == 'finished':
                conversion_progress[task_id].update({
                    'status': 'processing',
                    'percent': 87,
                    'message': 'Download complete. Processing...',
                    'speed': 0,
                    'speed_str': '',
                    'eta': 0
                })

    except Exception as e:
        logger.error(f"[PROGRESS] Error: {e}")


def get_available_formats(info):
    formats = info.get('formats', [])
    available = set(['best'])
    for f in formats:
        h = f.get('height')
        if h:
            if h >= 1080:
                available.add('1080p')
            elif h >= 720:
                available.add('720p')
            elif h >= 480:
                available.add('480p')
            elif h >= 360:
                available.add('360p')
            elif h >= 144:
                available.add('144p')
    return sorted(
        list(available),
        key=lambda x: int(x.replace('p', '').replace('best', '9999')),
        reverse=True
    )


def get_base_ydl_opts():
    opts = {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'socket_timeout': 60,
        'retries': 10,
        'fragment_retries': 10,
        'file_access_retries': 5,
        'extractor_retries': 5,
        'ignoreerrors': False,
        'user_agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36'
        ),
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept':
                'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        },
        'http_chunk_size': 10485760,
        'prefer_ffmpeg': True,
        'concurrent_fragment_downloads': 4
    }

    # Optional cookies for Facebook if present
    if os.path.exists('cookies.txt'):
        opts['cookiefile'] = 'cookies.txt'

    return opts


# ================== Routes ==================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/thumbnail/<thumb_id>')
def thumbnail_proxy(thumb_id):
    """Serve cached or on-demand thumbnail bytes."""
    with thumbnail_cache_lock:
        cached = thumbnail_cache.get(thumb_id)

    if not cached:
        return Response('Not found', status=404)

    img_data = cached.get('data')
    if not img_data:
        url = cached.get('url')
        img_data = fetch_thumbnail_bytes(url)
        if img_data:
            with thumbnail_cache_lock:
                thumbnail_cache[thumb_id]['data'] = img_data

    if not img_data:
        return Response('Failed', status=500)

    return Response(img_data, mimetype='image/jpeg')


@app.route('/api/video-info', methods=['POST'])
@limiter.limit("60 per minute")
def video_info():
    data = request.get_json() or {}
    url = data.get('url', '').strip()

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    url = normalize_youtube_url(url)
    if not validate_url(url):
        return jsonify({'error': 'Unsupported or invalid URL'}), 400

    url_hash = hashlib.md5(url.encode()).hexdigest()
    with cache_lock:
        if url_hash in video_info_cache:
            cached = video_info_cache[url_hash]
            if time.time() - cached['cached_at'] < 300:
                return jsonify(cached['data'])

    try:
        platform = get_platform(url)
        ydl_opts = get_base_ydl_opts()
        ydl_opts['skip_download'] = True

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get('_type') == 'playlist' and info.get('entries'):
                info = info['entries'][0]

        duration = info.get('duration', 0)
        if duration and duration > MAX_DURATION:
            return jsonify({
                'error': f'Video too long. Max {MAX_DURATION // 3600} hours allowed.'
            }), 400

        if duration:
            td = str(timedelta(seconds=duration))
            if duration >= 3600:
                duration_formatted = td
            else:
                # strip hours if 0
                h, m, s = duration // 3600, (duration % 3600) // 60, duration % 60
                duration_formatted = f"{m}:{s:02d}"
        else:
            duration_formatted = 'Unknown'

        title = extract_clean_title(info)
        artist = extract_clean_artist(info)
        thumb_url = get_best_thumbnail(info)

        # Proxy thumbnails for some platforms
        if platform in ['Facebook', 'Instagram', 'TikTok'] and thumb_url:
            thumb_id = hashlib.md5(thumb_url.encode()).hexdigest()[:16]
            img_data = fetch_thumbnail_bytes(thumb_url)
            with thumbnail_cache_lock:
                thumbnail_cache[thumb_id] = {
                    'url': thumb_url,
                    'data': img_data,
                    'time': time.time()
                }
            thumb_url = f"/api/thumbnail/{thumb_id}" if img_data else ''

        result = {
            'success': True,
            'title': title,
            'duration': duration,
            'duration_formatted': duration_formatted,
            'thumbnail': thumb_url or '',
            'uploader': artist,
            'platform': platform,
            'available_qualities': get_available_formats(info) if info.get('formats') else ['best']
        }

        with cache_lock:
            video_info_cache[url_hash] = {
                'data': result,
                'cached_at': time.time()
            }
            if len(video_info_cache) > 100:
                oldest = min(
                    video_info_cache.keys(),
                    key=lambda k: video_info_cache[k]['cached_at']
                )
                video_info_cache.pop(oldest, None)

        return jsonify(result)
    except Exception as e:
        logger.error(f"[INFO] Error: {e}")
        return jsonify({'error': 'Could not fetch video info'}), 400


@app.route('/api/audio-formats')
def audio_formats():
    return jsonify({
        'formats': [
            {
                'id': fid,
                'name': cfg['name'],
                'quality': cfg['quality'],
                'description': cfg['description'],
                'extension': cfg['extension'],
                'icon': cfg['icon'],
                'recommended': cfg.get('recommended', False)
            }
            for fid, cfg in AUDIO_FORMATS.items()
        ]
    })


@app.route('/api/convert', methods=['POST'])
@limiter.limit("20 per minute")
def convert():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    format_type = data.get('format', 'audio')
    quality = data.get('quality', 'best')
    audio_format = data.get('audioFormat', 'mp3')

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    url = normalize_youtube_url(url)
    if not validate_url(url):
        return jsonify({'error': 'Unsupported or invalid URL'}), 400

    if format_type == 'audio' and audio_format not in AUDIO_FORMATS:
        audio_format = 'mp3'

    task_id = str(uuid.uuid4())
    with progress_lock:
        conversion_progress[task_id] = {
            'status': 'initializing',
            'percent': 1,
            'message': 'Preparing download...',
            'last_update': time.time()
        }

    logger.info(f"[CONVERT] Start {task_id[:8]} - {format_type}/{audio_format if format_type=='audio' else quality}")

    output_path = os.path.join(DOWNLOAD_FOLDER, task_id)
    thumbnail_path = os.path.join(TEMP_FOLDER, f"{task_id}_thumb.jpg")

    def run_conversion():
        acquired = False
        start_time = time.time()
        title = 'download'
        artist = 'Unknown'
        video_duration = 0

        try:
            acquired = active_downloads.acquire(timeout=120)
            if not acquired:
                raise Exception("Server busy. Too many concurrent downloads, try again.")

            with progress_lock:
                conversion_progress[task_id].update({
                    'status': 'connecting',
                    'percent': 3,
                    'message': 'Connecting...',
                    'last_update': time.time()
                })

            # Pre-fetch info to get duration + clean title/artist
            try:
                info_opts = get_base_ydl_opts()
                info_opts['skip_download'] = True
                with yt_dlp.YoutubeDL(info_opts) as ydl:
                    pre_info = ydl.extract_info(url, download=False)
                    if pre_info.get('_type') == 'playlist' and pre_info.get('entries'):
                        pre_info = pre_info['entries'][0]
                    video_duration = pre_info.get('duration', 0)
                    title = extract_clean_title(pre_info)
                    artist = extract_clean_artist(pre_info)
            except Exception as e:
                logger.warning(f"[CONVERT] Pre-info error: {e}")
                title = 'download'
                artist = 'Unknown'
                video_duration = 3600

            download_timeout = calculate_timeout(video_duration)
            ffmpeg_timeout = calculate_ffmpeg_timeout(video_duration)

            ydl_opts = get_base_ydl_opts()

            def ph(d):
                progress_hook(d, task_id)

            ydl_opts['progress_hooks'] = [ph]
            ydl_opts['outtmpl'] = output_path + '.%(ext)s'

            if format_type == 'audio':
                ydl_opts['format'] = 'bestaudio[ext=m4a]/bestaudio/best'
            else:
                ydl_opts['format'] = VIDEO_QUALITIES.get(quality, VIDEO_QUALITIES['best'])
                ydl_opts['merge_output_format'] = 'mp4'

            with progress_lock:
                conversion_progress[task_id].update({
                    'status': 'starting',
                    'percent': 5,
                    'message': 'Starting download...',
                    'last_update': time.time()
                })

            # Run download in a thread with timeout
            download_done = threading.Event()
            download_error = [None]
            info_data = [None]

            def dl_thread():
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info_data[0] = ydl.extract_info(url, download=True)
                    download_done.set()
                except Exception as e:
                    download_error[0] = str(e)
                    download_done.set()

            t = threading.Thread(target=dl_thread, daemon=True)
            t.start()

            if not download_done.wait(timeout=download_timeout):
                raise Exception(f"Download timed out after {download_timeout//60} minutes.")

            if download_error[0]:
                raise Exception(download_error[0])

            info = info_data[0]
            if not info:
                raise Exception("Failed to get video info from yt-dlp")

            with progress_lock:
                conversion_progress[task_id].update({
                    'status': 'processing',
                    'percent': 86,
                    'message': 'Download complete. Locating file...',
                    'last_update': time.time()
                })

            # Find downloaded file
            downloaded_file = None
            for ext in ['.mp4', '.m4a', '.mp3', '.webm', '.mkv', '.opus', '.ogg', '.wav', '.flac']:
                p = output_path + ext
                if os.path.exists(p):
                    downloaded_file = p
                    break
            if not downloaded_file:
                for fname in os.listdir(DOWNLOAD_FOLDER):
                    if fname.startswith(task_id):
                        downloaded_file = os.path.join(DOWNLOAD_FOLDER, fname)
                        break

            if not downloaded_file or not os.path.exists(downloaded_file):
                raise Exception("Downloaded file not found")

            file_size = os.path.getsize(downloaded_file)
            logger.info(f"[CONVERT] Downloaded: {downloaded_file} ({file_size/(1024*1024):.1f} MB)")

            # ===== AUDIO BRANCH =====
            if format_type == 'audio':
                cfg = AUDIO_FORMATS[audio_format]
                ext = cfg['extension']

                with progress_lock:
                    conversion_progress[task_id].update({
                        'status': 'processing',
                        'percent': 88,
                        'message': f'Converting to {cfg["name"]}...',
                        'last_update': time.time()
                    })

                audio_temp = output_path + f'_temp.{ext}'

                cmd = [
                    'ffmpeg', '-y',
                    '-i', downloaded_file,
                    '-vn',
                    '-c:a', cfg['codec'],
                    '-b:a', cfg['bitrate'],
                    '-ar', cfg['sample_rate'],
                    '-ac', '2',
                    audio_temp
                ]

                success, error = run_ffmpeg_with_progress(cmd, task_id, timeout=ffmpeg_timeout, stage="processing")
                if not success or not os.path.exists(audio_temp):
                    raise Exception(f"Audio conversion failed: {error[:150] if error else 'unknown error'}")

                # Thumbnail for artwork
                thumb_ok = False
                try:
                    with progress_lock:
                        conversion_progress[task_id].update({
                            'status': 'processing',
                            'percent': 92,
                            'message': 'Downloading artwork...',
                            'last_update': time.time()
                        })
                    thumb_url = get_best_thumbnail(info)
                    if thumb_url:
                        thumb_ok = download_thumbnail(thumb_url, thumbnail_path)
                except Exception as e:
                    logger.error(f"[THUMB] {e}")

                final_audio = output_path + f'.{ext}'

                upload_date = info.get('upload_date', '')
                year = upload_date[:4] if upload_date and len(upload_date) >= 4 else None

                metadata = {
                    'title': title,
                    'artist': artist,
                    'year': year,
                    'genre': 'Music'
                }

                with progress_lock:
                    conversion_progress[task_id].update({
                        'status': 'embedding',
                        'percent': 95,
                        'message': 'Embedding metadata...',
                        'last_update': time.time()
                    })

                if audio_format == 'mp3':
                    shutil.copy(audio_temp, final_audio)
                    embed_metadata_mp3_mutagen(final_audio, metadata, thumbnail_path if thumb_ok else None)
                elif audio_format == 'aac':
                    shutil.copy(audio_temp, final_audio)
                    embed_metadata_aac(final_audio, metadata, thumbnail_path if thumb_ok else None)
                elif audio_format == 'opus':
                    shutil.copy(audio_temp, final_audio)
                    embed_metadata_opus(final_audio, metadata, thumbnail_path if thumb_ok else None)
                elif audio_format == 'ogg':
                    shutil.copy(audio_temp, final_audio)
                    embed_metadata_ogg(final_audio, metadata, thumbnail_path if thumb_ok else None)
                else:
                    shutil.copy(audio_temp, final_audio)

                # Cleanup
                try:
                    if os.path.exists(audio_temp):
                        os.remove(audio_temp)
                    if os.path.exists(downloaded_file) and downloaded_file != final_audio:
                        os.remove(downloaded_file)
                    if os.path.exists(thumbnail_path):
                        os.remove(thumbnail_path)
                except Exception:
                    pass

                output_file = final_audio
                thumb_downloaded = thumb_ok

            # ===== VIDEO BRANCH (QuickTime-compatible MP4) =====
            else:
                with progress_lock:
                    conversion_progress[task_id].update({
                        'status': 'processing',
                        'percent': 88,
                        'message': 'Converting to QuickTime-compatible MP4...',
                        'last_update': time.time()
                    })

                desired_mp4 = output_path + '.mp4'
                temp_mp4 = output_path + '__enc.mp4' if os.path.abspath(downloaded_file) == desired_mp4 else desired_mp4

                encode_cfg = VIDEO_ENCODE_SETTINGS.get(quality, VIDEO_ENCODE_SETTINGS['best'])

                cmd = [
                    'ffmpeg', '-y',
                    '-i', downloaded_file,
                    '-map', '0:v:0',
                    '-map', '0:a:0?',
                    '-c:v', 'libx264',
                    '-preset', 'medium',
                    '-profile:v', 'high',
                    '-level', '4.0',
                    '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    '-ar', '48000',
                    '-ac', '2',
                    '-movflags', '+faststart',
                    '-f', 'mp4'
                ]

                if encode_cfg['scale']:
                    cmd.extend(['-vf', f"scale={encode_cfg['scale']}"])
                if encode_cfg['crf']:
                    cmd.extend(['-crf', encode_cfg['crf']])
                if encode_cfg['maxrate'] and encode_cfg['bufsize']:
                    cmd.extend(['-maxrate', encode_cfg['maxrate'], '-bufsize', encode_cfg['bufsize']])

                cmd.append(temp_mp4)

                success, error = run_ffmpeg_with_progress(cmd, task_id, timeout=ffmpeg_timeout, stage="processing")
                if not success or not os.path.exists(temp_mp4):
                    raise Exception(f"Video conversion failed: {error[:200] if error else 'unknown error'}")

                if os.path.abspath(temp_mp4) != os.path.abspath(desired_mp4):
                    if os.path.exists(desired_mp4):
                        os.remove(desired_mp4)
                    os.rename(temp_mp4, desired_mp4)

                output_file = desired_mp4

                try:
                    if os.path.exists(downloaded_file) and os.path.abspath(downloaded_file) != os.path.abspath(output_file):
                        os.remove(downloaded_file)
                except Exception as e:
                    logger.warning(f"[VIDEO] Could not remove original: {e}")

                thumb_downloaded = False

            # Verify final file
            if not os.path.exists(output_file):
                raise Exception("Final output file not found")
            final_size = os.path.getsize(output_file)
            if final_size < 1000:
                raise Exception("Output file too small")

            ext = os.path.splitext(output_file)[1][1:]
            safe_title = sanitize_filename(title)
            total_time = time.time() - start_time
            time_str = f"{int(total_time//60)}m {int(total_time%60)}s" if total_time >= 60 else f"{int(total_time)}s"

            with progress_lock:
                conversion_progress[task_id] = {
                    'status': 'completed',
                    'percent': 100,
                    'message': f'Ready! (took {time_str})',
                    'title': safe_title,
                    'filename': os.path.basename(output_file),
                    'file_size': final_size,
                    'has_thumbnail': thumb_downloaded if format_type == 'audio' else False,
                    'format': format_type,
                    'audio_format': audio_format if format_type == 'audio' else None,
                    'quality': AUDIO_FORMATS[audio_format]['quality'] if format_type == 'audio' else quality,
                    'extension': ext,
                    'last_update': time.time()
                }

            logger.info(f"[CONVERT] ✓ {safe_title[:30]} ({final_size/(1024*1024):.1f}MB, {ext}) in {time_str}")

        except Exception as e:
            err = str(e)[:300]
            logger.error(f"[CONVERT] ✗ {task_id[:8]}: {err}")
            logger.error(traceback.format_exc())
            with process_lock:
                if task_id in active_processes:
                    try:
                        p = active_processes[task_id]
                        if hasattr(p, 'pid'):
                            kill_process_tree(p.pid)
                        else:
                            p.kill()
                    except Exception:
                        pass
                    active_processes.pop(task_id, None)
            with progress_lock:
                conversion_progress[task_id] = {
                    'status': 'error',
                    'percent': 0,
                    'message': f'Error: {err}',
                    'last_update': time.time()
                }
            for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
                try:
                    for fname in os.listdir(folder):
                        if fname.startswith(task_id):
                            os.remove(os.path.join(folder, fname))
                except Exception:
                    pass
        finally:
            if acquired:
                active_downloads.release()

    threading.Thread(target=run_conversion, daemon=True).start()

    return jsonify({'success': True, 'task_id': task_id, 'message': 'Conversion started'})


@app.route('/api/cancel/<task_id>', methods=['POST'])
def cancel(task_id):
    with process_lock:
        if task_id in active_processes:
            try:
                p = active_processes[task_id]
                if hasattr(p, 'pid'):
                    kill_process_tree(p.pid)
                else:
                    p.kill()
            except Exception:
                pass
            active_processes.pop(task_id, None)

    with progress_lock:
        if task_id in conversion_progress:
            conversion_progress[task_id] = {
                'status': 'cancelled',
                'percent': 0,
                'message': 'Download cancelled by user',
                'last_update': time.time()
            }

    for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
        try:
            for fname in os.listdir(folder):
                if fname.startswith(task_id):
                    os.remove(os.path.join(folder, fname))
        except Exception:
            pass

    logger.info(f"[CANCEL] Task {task_id[:8]} cancelled")
    return jsonify({'status': 'cancelled'})


@app.route('/api/progress/<task_id>')
@limiter.exempt
def progress(task_id):
    with progress_lock:
        prog = conversion_progress.get(task_id, {
            'status': 'unknown',
            'percent': 0,
            'message': 'Task not found'
        }).copy()
    prog.pop('last_update', None)
    return jsonify(prog)


@app.route('/api/download/<task_id>')
@limiter.limit("50 per minute")
def download(task_id):
    title = request.args.get('title', 'download')

    with progress_lock:
        info = conversion_progress.get(task_id, {})
    is_video = info.get('format') == 'video'

    preferred_exts = ['.mp4'] if is_video else ['.mp3', '.m4a', '.opus', '.ogg']

    file_found = None
    if os.path.exists(DOWNLOAD_FOLDER):
        for ext in preferred_exts:
            p = os.path.join(DOWNLOAD_FOLDER, f"{task_id}{ext}")
            if os.path.exists(p):
                file_found = p
                break
        if not file_found:
            for fname in os.listdir(DOWNLOAD_FOLDER):
                if fname.startswith(task_id):
                    candidate = os.path.join(DOWNLOAD_FOLDER, fname)
                    if is_video and not candidate.lower().endswith('.mp4'):
                        continue
                    file_found = candidate
                    break

    if not file_found or not os.path.exists(file_found):
        return jsonify({'error': 'File not found'}), 404

    ext = os.path.splitext(file_found)[1].lower()
    mime_types = {
        '.mp3': 'audio/mpeg',
        '.m4a': 'audio/mp4',
        '.opus': 'audio/opus',
        '.ogg': 'audio/ogg',
        '.mp4': 'video/mp4',
        '.webm': 'video/webm',
    }
    mimetype = mime_types.get(ext, 'application/octet-stream')

    @after_this_request
    def cleanup(response):
        def delayed():
            time.sleep(30)
            with progress_lock:
                conversion_progress.pop(task_id, None)
        threading.Thread(target=delayed, daemon=True).start()
        return response

    safe_title = sanitize_filename(title)
    return send_file(
        file_found,
        as_attachment=True,
        download_name=f"{safe_title}{ext}",
        mimetype=mimetype
    )


@app.route('/api/supported-platforms')
def supported_platforms():
    return jsonify({
        'platforms': [
            {'name': 'YouTube',   'icon': 'fab fa-youtube',   'color': '#FF0000'},
            {'name': 'Facebook',  'icon': 'fab fa-facebook',  'color': '#1877F2'},
            {'name': 'Instagram', 'icon': 'fab fa-instagram', 'color': '#E4405F'},
            {'name': 'TikTok',    'icon': 'fab fa-tiktok',    'color': '#000000'},
            {'name': 'Twitter/X', 'icon': 'fab fa-twitter',   'color': '#1DA1F2'},
        ]
    })


@app.route('/health')
def health():
    with process_lock:
        active_procs = len(active_processes)
    with progress_lock:
        active_tasks = len(conversion_progress)
    with cache_lock:
        cached_videos = len(video_info_cache)
    return jsonify({
        'status': 'healthy',
        'active_downloads': MAX_CONCURRENT_DOWNLOADS - active_downloads._value,
        'active_processes': active_procs,
        'active_tasks': active_tasks,
        'cached_videos': cached_videos,
        'max_duration_hours': MAX_DURATION // 3600,
        'psutil_available': HAS_PSUTIL
    })


@app.route('/api/admin/kill-all', methods=['POST'])
def admin_kill_all():
    """Emergency kill of all running processes and tasks."""
    with process_lock:
        for task_id, p in list(active_processes.items()):
            try:
                if hasattr(p, 'pid'):
                    kill_process_tree(p.pid)
                else:
                    p.kill()
            except Exception:
                pass
        active_processes.clear()

    with progress_lock:
        for tid in list(conversion_progress.keys()):
            if conversion_progress[tid].get('status') not in ['completed', 'error', 'cancelled']:
                conversion_progress[tid] = {
                    'status': 'error',
                    'percent': 0,
                    'message': 'Manually terminated'
                }

    for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
        try:
            for fname in os.listdir(folder):
                os.remove(os.path.join(folder, fname))
        except Exception:
            pass

    return jsonify({'status': 'all_killed'})


@app.route('/api/admin/status')
def admin_status():
    with progress_lock:
        tasks = {
            tid[:8]: {
                'status': info.get('status'),
                'percent': info.get('percent'),
                'message': info.get('message', '')[:60],
                'age': int(time.time() - info.get('last_update', time.time()))
            }
            for tid, info in conversion_progress.items()
        }
    with process_lock:
        procs = list(active_processes.keys())
    return jsonify({
        'tasks': tasks,
        'active_processes': [p[:8] for p in procs],
        'semaphore_available': active_downloads._value
    })


if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("[SERVER] Starting Flask Video/Audio Downloader")
    logger.info(f"[SERVER] Max video duration: {MAX_DURATION // 3600} hours")
    logger.info(f"[SERVER] Download timeout: {DOWNLOAD_TIMEOUT // 60} minutes")
    logger.info(f"[SERVER] FFMPEG timeout: {FFMPEG_TIMEOUT // 60} minutes")
    logger.info(f"[SERVER] Download folder: {os.path.abspath(DOWNLOAD_FOLDER)}")
    logger.info(f"[SERVER] psutil available: {HAS_PSUTIL}")
    logger.info("[SERVER] Video output: QuickTime-compatible MP4 (H.264 + AAC)")
    logger.info("=" * 60)
    app.run(debug=False, host='0.0.0.0', port=5000)