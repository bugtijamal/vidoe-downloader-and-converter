from flask import Flask, render_template, jsonify, send_file, after_this_request, request
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
from datetime import datetime, timedelta
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TCON, COMM
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.flac import Picture
from PIL import Image
from io import BytesIO
from urllib.parse import urlparse, parse_qs
import logging
import traceback
import subprocess
import hashlib
import shutil
import base64

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

# Rate limiter
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["1000 per day", "200 per hour"],
    storage_uri="memory://"
)

# ================== Config ==================
DOWNLOAD_FOLDER = 'downloads'
TEMP_FOLDER = 'temp'
MAX_DURATION = 3600       # max 1 hour
CLEANUP_AGE = 300         # 5 minutes
MAX_CONCURRENT_DOWNLOADS = 5

for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
    os.makedirs(folder, exist_ok=True)

conversion_progress = {}
progress_lock = threading.Lock()
active_downloads = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)

video_info_cache = {}
cache_lock = threading.Lock()

VIDEO_QUALITIES = {
    'best':  'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
    '1080p': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best',
    '720p':  'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best',
    '480p':  'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best',
    '360p':  'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best',
}

VIDEO_ENCODE_SETTINGS = {
    '1080p': {'scale': '-2:1080', 'crf': '20', 'maxrate': '5000k', 'bufsize': '10000k'},
    '720p':  {'scale': '-2:720',  'crf': '22', 'maxrate': '2500k', 'bufsize': '5000k'},
    '480p':  {'scale': '-2:480',  'crf': '24', 'maxrate': '1500k', 'bufsize': '3000k'},
    '360p':  {'scale': '-2:360',  'crf': '26', 'maxrate': '800k',  'bufsize': '1600k'},
    '144p':  {'scale': '-2:144',  'crf': '28', 'maxrate': '400k',  'bufsize': '800k'},
    # 'best' = keep original resolution, but still H.264 + AAC
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
        'description': 'Universal compatibility - Works on all devices',
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
        'description': 'Best for iPhone/Apple - Excellent quality, smaller size',
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
        'description': 'Best quality/size ratio - Modern codec',
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
        'description': 'Open source - Good quality for Android',
        'icon': '🤖',
        'recommended': False
    }
}

# ================== Background Cleanup ==================
def cleanup_old_files():
    while True:
        try:
            now = time.time()
            cleaned = 0
            for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
                if os.path.exists(folder):
                    for filename in os.listdir(folder):
                        filepath = os.path.join(folder, filename)
                        try:
                            if os.path.isfile(filepath):
                                file_age = now - os.path.getmtime(filepath)
                                if file_age > CLEANUP_AGE:
                                    task_id = filename.split('.')[0]
                                    with progress_lock:
                                        if task_id not in conversion_progress:
                                            os.remove(filepath)
                                            cleaned += 1
                        except (PermissionError, FileNotFoundError, OSError):
                            pass
            if cleaned > 0:
                logger.info(f"[CLEANUP] Removed {cleaned} old files")
        except Exception as e:
            logger.error(f"[CLEANUP ERROR] {e}")
        time.sleep(300)

threading.Thread(target=cleanup_old_files, daemon=True).start()

# ================== Helper Functions ==================
def normalize_youtube_url(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '').replace('m.', '')
        if 'youtube.com' in domain:
            query_params = parse_qs(parsed.query)
            if 'v' in query_params:
                video_id = query_params['v'][0]
                return f"https://www.youtube.com/watch?v={video_id}"
            if '/shorts/' in parsed.path:
                m = re.search(r'/shorts/([a-zA-Z0-9_-]+)', parsed.path)
                if m:
                    video_id = m.group(1)
                    return f"https://www.youtube.com/watch?v={video_id}"
        elif 'youtu.be' in domain:
            video_id = parsed.path.strip('/')
            if video_id:
                return f"https://www.youtube.com/watch?v={video_id}"
        return url
    except Exception:
        return url

def validate_url(url):
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.replace('www.', '').replace('m.', '').replace('web.', '')
        if domain in ['youtube.com', 'youtu.be']:
            if domain == 'youtube.com':
                query = parse_qs(parsed.query)
                if 'v' in query or '/shorts/' in parsed.path or '/watch' in parsed.path:
                    return True
                return False
            return len(parsed.path) > 1
        if any(p in domain for p in ['facebook.com', 'fb.watch', 'fb.com',
                                     'instagram.com', 'tiktok.com',
                                     'vm.tiktok.com', 'twitter.com',
                                     'x.com', 't.co']):
            return True
        return False
    except Exception:
        return False

def download_thumbnail(url, save_path):
    """
    Download and optimize thumbnail image for artwork:
    - Center-crops to a square (no white padding)
    - Resizes to 600x600
    - Saves as JPEG
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36'
        }
        resp = requests.get(url, timeout=15, headers=headers, stream=True)
        resp.raise_for_status()
        
        img = Image.open(BytesIO(resp.content))

        # Convert to RGB (strip alpha)
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Center-crop to a square (no padding)
        w, h = img.size
        if w != h:
            # Take the shorter side to make a square
            min_side = min(w, h)
            left = (w - min_side) // 2
            top = (h - min_side) // 2
            right = left + min_side
            bottom = top + min_side
            img = img.crop((left, top, right, bottom))

        # Resize to 600x600 (or smaller if image is small)
        target_size = 600
        if img.width != target_size or img.height != target_size:
            img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)

        # Save as JPEG
        img.save(save_path, 'JPEG', quality=95, optimize=True, progressive=False)
        
        if os.path.exists(save_path):
            saved_size = os.path.getsize(save_path)
            logger.info(f"[THUMBNAIL] Saved square thumbnail: {save_path} ({saved_size} bytes, {img.width}x{img.height})")
            return True
        
        logger.error(f"[THUMBNAIL] File not saved: {save_path}")
        return False

    except Exception as e:
        logger.error(f"[THUMBNAIL ERROR] {e}")
        return False

def embed_artwork_mp3_ffmpeg(audio_file, thumbnail_path, output_file):
    try:
        cmd = [
            'ffmpeg', '-y',
            '-i', audio_file,
            '-i', thumbnail_path,
            '-map', '0:a:0',
            '-map', '1:v:0',
            '-c', 'copy',
            '-id3v2_version', '3',
            '-metadata:s:v', 'title=Album cover',
            '-metadata:s:v', 'comment=Cover (front)',
            output_file
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and os.path.exists(output_file):
            return True
        logger.error(f"[FFMPEG-MP3] {r.stderr[:200]}")
        return False
    except Exception as e:
        logger.error(f"[FFMPEG-MP3] Error: {e}")
        return False

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
        # audio.tags.add(TALB(encoding=3, text=metadata.get('album', 'Downloaded Audio')))
        if metadata.get('year'):
            try:
                audio.tags.add(TDRC(encoding=3, text=str(metadata['year'])))
            except:
                pass
        if metadata.get('genre'):
            audio.tags.add(TCON(encoding=3, text=metadata['genre']))
        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, 'rb') as f:
                img_data = f.read()
            if img_data:
                audio.tags.add(APIC(encoding=3, mime='image/jpeg', type=3, desc='', data=img_data))
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
        # audio['\xa9alb'] = metadata.get('album', 'Downloaded Audio')
        if metadata.get('year'):
            audio['\xa9day'] = str(metadata['year'])
        if metadata.get('genre'):
            audio['\xa9gen'] = metadata['genre']
        if thumbnail_path and os.path.exists(thumbnail_path):
            with open(thumbnail_path, 'rb') as f:
                img_data = f.read()
            if img_data:
                audio['covr'] = [MP4Cover(img_data, imageformat=MP4Cover.FORMAT_JPEG)]
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
        # audio['ALBUM'] = metadata.get('album', 'Downloaded Audio')
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
            picture.width = img.width
            picture.height = img.height
            picture.depth = 24
            encoded_data = base64.b64encode(picture.write()).decode('ascii')
            audio['METADATA_BLOCK_PICTURE'] = encoded_data
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
        # audio['ALBUM'] = metadata.get('album', 'Downloaded Audio')
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
            picture.width = img.width
            picture.height = img.height
            picture.depth = 24
            encoded_data = base64.b64encode(picture.write()).decode('ascii')
            audio['METADATA_BLOCK_PICTURE'] = encoded_data
        audio.save()
        return True
    except Exception as e:
        logger.error(f"[OGG-METADATA] {e}")
        return False

def get_best_thumbnail(info):
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

def progress_hook(d, task_id):
    try:
        with progress_lock:
            if task_id not in conversion_progress:
                return
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                if total > 0:
                    percent = min((downloaded / total) * 85, 85)
                else:
                    current = conversion_progress.get(task_id, {})
                    percent = min(current.get('percent', 5) + 0.5, 85)
                current_percent = conversion_progress[task_id].get('percent', 0)
                if abs(percent - current_percent) >= 1:
                    conversion_progress[task_id] = {
                        'status': 'downloading',
                        'percent': percent,
                        'speed': d.get('speed', 0),
                        'eta': d.get('eta', 0),
                        'downloaded_bytes': downloaded,
                        'total_bytes': total,
                        'message': f'Downloading... {int(percent)}%'
                    }
            elif d['status'] == 'finished':
                conversion_progress[task_id] = {
                    'status': 'processing',
                    'percent': 87,
                    'message': 'Processing file...'
                }
    except Exception:
        pass

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
    return sorted(list(available), key=lambda x: int(x.replace('p', '').replace('best', '9999')), reverse=True)

def sanitize_filename(filename, max_length=100):
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', filename)
    filename = re.sub(r'[\s\-]+', '_', filename)
    filename = re.sub(r'[^\w\s\-_.]', '', filename)
    filename = filename.strip('._')
    if len(filename) > max_length:
        filename = filename[:max_length]
    return filename or 'download'

def get_base_ydl_opts():
    return {
        'quiet': True,
        'no_warnings': True,
        'noplaylist': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'socket_timeout': 30,
        'retries': 5,
        'fragment_retries': 5,
        'ignoreerrors': False,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
        'referer': 'https://www.youtube.com/',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
        },
        'http_chunk_size': 10485760,
        'prefer_ffmpeg': True,
    }

# ================== Routes ==================

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/video-info', methods=['POST'])
@limiter.limit("60 per minute")
def get_video_info():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    url = normalize_youtube_url(url)
    if not validate_url(url):
        return jsonify({'error': 'Unsupported URL'}), 400

    url_hash = hashlib.md5(url.encode()).hexdigest()
    with cache_lock:
        if url_hash in video_info_cache:
            cached = video_info_cache[url_hash]
            if time.time() - cached['cached_at'] < 300:
                return jsonify(cached['data'])

    try:
        ydl_opts = get_base_ydl_opts()
        ydl_opts.update({'skip_download': True})
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info.get('_type') == 'playlist' and info.get('entries'):
                info = info['entries'][0]

            duration = info.get('duration', 0)
            if duration and duration > MAX_DURATION:
                return jsonify({'error': f'Video too long. Max {MAX_DURATION // 60} minutes.'}), 400

            result = {
                'success': True,
                'title': info.get('title', 'Unknown'),
                'duration': duration,
                'duration_formatted': str(timedelta(seconds=duration)) if duration else 'Unknown',
                'thumbnail': get_best_thumbnail(info),
                'uploader': info.get('uploader', info.get('channel', 'Unknown')),
                'platform': info.get('extractor', 'Unknown').replace(':', ' ').title(),
                'available_qualities': get_available_formats(info)
            }

            with cache_lock:
                video_info_cache[url_hash] = {
                    'data': result,
                    'cached_at': time.time()
                }
                if len(video_info_cache) > 100:
                    oldest = min(video_info_cache.keys(), key=lambda k: video_info_cache[k]['cached_at'])
                    del video_info_cache[oldest]

            return jsonify(result)
    except Exception as e:
        logger.error(f"[INFO ERROR] {e}")
        return jsonify({'error': 'Could not fetch video info'}), 400

@app.route('/api/audio-formats')
def get_audio_formats():
    formats_list = []
    for fid, cfg in AUDIO_FORMATS.items():
        formats_list.append({
            'id': fid,
            'name': cfg['name'],
            'quality': cfg['quality'],
            'description': cfg['description'],
            'extension': cfg['extension'],
            'icon': cfg['icon'],
            'recommended': cfg.get('recommended', False)
        })
    return jsonify({'formats': formats_list})

@app.route('/api/convert', methods=['POST'])
@limiter.limit("20 per minute")
def convert_media():
    data = request.get_json() or {}
    url = data.get('url', '').strip()
    format_type = data.get('format', 'audio')
    quality = data.get('quality', 'best')
    audio_format = data.get('audioFormat', 'mp3')

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    url = normalize_youtube_url(url)
    if not validate_url(url):
        return jsonify({'error': 'Unsupported URL'}), 400

    if format_type == 'audio' and audio_format not in AUDIO_FORMATS:
        audio_format = 'mp3'

    task_id = str(uuid.uuid4())
    with progress_lock:
        conversion_progress[task_id] = {
            'status': 'initializing',
            'percent': 1,
            'message': 'Preparing download...'
        }

    logger.info(f"[CONVERT] Started {task_id[:8]} - {format_type} ({audio_format if format_type=='audio' else quality})")

    output_filename = f"{task_id}"
    output_path = os.path.join(DOWNLOAD_FOLDER, output_filename)
    thumbnail_path = os.path.join(TEMP_FOLDER, f"{task_id}_thumb.jpg")

    def run_conversion():
        acquired = False
        try:
            acquired = active_downloads.acquire(timeout=60)
            if not acquired:
                raise Exception("Too many concurrent downloads. Please try again.")

            with progress_lock:
                conversion_progress[task_id].update({
                    'status': 'connecting',
                    'percent': 3,
                    'message': 'Connecting to server...'
                })

            ydl_opts = get_base_ydl_opts()

            def progress_hook_with_id(d):
                progress_hook(d, task_id)

            if format_type == 'audio':
                ydl_opts.update({
                    'format': 'bestaudio[ext=m4a]/bestaudio/best',
                    'outtmpl': output_path + '.%(ext)s',
                    'progress_hooks': [progress_hook_with_id],
                })
            else:
                ydl_opts.update({
                    'format': VIDEO_QUALITIES.get(quality, VIDEO_QUALITIES['best']),
                    'outtmpl': output_path + '.%(ext)s',
                    'progress_hooks': [progress_hook_with_id],
                    'merge_output_format': 'mp4',
                })

            with progress_lock:
                conversion_progress[task_id].update({
                    'status': 'starting',
                    'percent': 5,
                    'message': 'Starting download...'
                })

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title', 'media')
                clean_title = sanitize_filename(title)

            # Locate downloaded file
            downloaded_file = None
            for ext in ['.m4a', '.mp3', '.mp4', '.webm', '.mkv', '.opus', '.ogg']:
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

            # ===== AUDIO BRANCH =====
            if format_type == 'audio':
                cfg = AUDIO_FORMATS[audio_format]
                ext = cfg['extension']

                with progress_lock:
                    conversion_progress[task_id].update({
                        'status': 'processing',
                        'percent': 88,
                        'message': f'Converting to {cfg["name"]}...'
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
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if r.returncode != 0 or not os.path.exists(audio_temp):
                    last_line = (r.stderr or '').splitlines()[-1] if r.stderr else 'unknown'
                    raise Exception(f"{cfg['name']} conversion failed: {last_line}")

                # Download thumbnail
                thumb_ok = False
                try:
                    with progress_lock:
                        conversion_progress[task_id].update({
                            'status': 'processing',
                            'percent': 92,
                            'message': 'Downloading artwork...'
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
                    'artist': info.get('uploader', 'Unknown'),
                    # 'album': 'Downloaded Audio',
                    'year': year,
                    'genre': 'Music'
                }

                if thumb_ok:
                    with progress_lock:
                        conversion_progress[task_id].update({
                            'status': 'embedding',
                            'percent': 95,
                            'message': 'Embedding artwork...'
                        })
                    if audio_format == 'mp3':
                        if not embed_artwork_mp3_ffmpeg(audio_temp, thumbnail_path, final_audio):
                            shutil.copy(audio_temp, final_audio)
                            embed_metadata_mp3_mutagen(final_audio, metadata, thumbnail_path)
                    elif audio_format == 'aac':
                        shutil.copy(audio_temp, final_audio)
                        embed_metadata_aac(final_audio, metadata, thumbnail_path)
                    elif audio_format == 'opus':
                        shutil.copy(audio_temp, final_audio)
                        embed_metadata_opus(final_audio, metadata, thumbnail_path)
                    elif audio_format == 'ogg':
                        shutil.copy(audio_temp, final_audio)
                        embed_metadata_ogg(final_audio, metadata, thumbnail_path)
                else:
                    shutil.copy(audio_temp, final_audio)
                    if audio_format == 'mp3':
                        embed_metadata_mp3_mutagen(final_audio, metadata, None)
                    elif audio_format == 'aac':
                        embed_metadata_aac(final_audio, metadata, None)
                    elif audio_format == 'opus':
                        embed_metadata_opus(final_audio, metadata, None)
                    elif audio_format == 'ogg':
                        embed_metadata_ogg(final_audio, metadata, None)

                # Cleanup temps
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

            # ===== VIDEO BRANCH =====
            else:
                # ===== VIDEO BRANCH – re-encode to target size =====
                with progress_lock:
                    conversion_progress[task_id].update({
                        'status': 'processing',
                        'percent': 88,
                        'message': 'Converting video to MP4 (H.264 + AAC)...'
                    })

                desired_mp4 = output_path + '.mp4'

                # Avoid input == output path (causes "Invalid argument" in ffmpeg)
                if os.path.abspath(downloaded_file) == os.path.abspath(desired_mp4):
                    temp_mp4 = output_path + '__enc.mp4'
                else:
                    temp_mp4 = desired_mp4

                # Pick encode settings based on requested quality
                encode_cfg = VIDEO_ENCODE_SETTINGS.get(quality, VIDEO_ENCODE_SETTINGS['best'])

                cmd = [
                    'ffmpeg', '-y',
                    '-i', downloaded_file,
                    '-map', '0:v:0',
                    '-map', '0:a:0?',
                    '-c:v', 'libx264',
                    '-preset', 'veryfast',
                    '-profile:v', 'high',
                    '-level:v', '4.0',
                    '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac',
                    '-b:a', '160k',
                    '-ac', '2',
                    '-movflags', '+faststart'
                ]

                # Add scale filter if we want to downscale (e.g., 360p)
                if encode_cfg['scale']:
                    cmd.extend(['-vf', f"scale={encode_cfg['scale']}"])

                # Use CRF for quality control
                if encode_cfg['crf']:
                    cmd.extend(['-crf', encode_cfg['crf']])

                # Optionally cap bitrate for smaller files
                if encode_cfg['maxrate'] and encode_cfg['bufsize']:
                    cmd.extend(['-maxrate', encode_cfg['maxrate'], '-bufsize', encode_cfg['bufsize']])

                cmd.append(temp_mp4)

                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                    if r.returncode != 0 or not os.path.exists(temp_mp4):
                        last_line = (r.stderr or '').splitlines()[-1] if r.stderr else 'unknown error'
                        raise Exception(f"ffmpeg error: {last_line}")

                    # Rename temp to final if needed
                    if os.path.abspath(temp_mp4) != os.path.abspath(desired_mp4):
                        if os.path.exists(desired_mp4):
                            os.remove(desired_mp4)
                        os.rename(temp_mp4, desired_mp4)

                    output_file = desired_mp4

                    # Remove original downloaded file if it's different
                    try:
                        if os.path.exists(downloaded_file) and os.path.abspath(downloaded_file) != os.path.abspath(output_file):
                            os.remove(downloaded_file)
                    except Exception as e:
                        logger.warning(f"[VIDEO] Could not remove original: {e}")

                except subprocess.TimeoutExpired:
                    raise Exception("Video conversion timed out. Please try again.")
                except Exception as e:
                    logger.error(f"[VIDEO] Conversion failed: {e}")
                    raise

                thumb_downloaded = False  # we are not embedding artwork into the video file

            # Verify final output
            if not os.path.exists(output_file):
                raise Exception("Final output file not found")
            final_size = os.path.getsize(output_file)
            if final_size < 1000:
                raise Exception("Output file is too small")

            ext = os.path.splitext(output_file)[1][1:]

            with progress_lock:
                conversion_progress[task_id] = {
                    'status': 'completed',
                    'percent': 100,
                    'message': 'Ready!',
                    'title': clean_title,
                    'filename': os.path.basename(output_file),
                    'file_size': final_size,
                    'has_thumbnail': thumb_downloaded,
                    'format': format_type,
                    'audio_format': audio_format if format_type == 'audio' else None,
                    'quality': AUDIO_FORMATS[audio_format]['quality'] if format_type == 'audio' else quality,
                    'extension': ext
                }

            logger.info(f"[CONVERT] ✓ {clean_title[:30]} ({final_size} bytes, {ext})")

        except Exception as e:
            err = str(e)[:200]
            logger.error(f"[CONVERT] ✗ {task_id[:8]}: {err}")
            logger.error(traceback.format_exc())
            with progress_lock:
                conversion_progress[task_id] = {
                    'status': 'error',
                    'percent': 0,
                    'message': f'Error: {err}'
                }
            # Cleanup partial files
            try:
                for fname in os.listdir(DOWNLOAD_FOLDER):
                    if fname.startswith(task_id):
                        os.remove(os.path.join(DOWNLOAD_FOLDER, fname))
                for fname in os.listdir(TEMP_FOLDER):
                    if fname.startswith(task_id):
                        os.remove(os.path.join(TEMP_FOLDER, fname))
            except Exception:
                pass
        finally:
            if acquired:
                active_downloads.release()

    threading.Thread(target=run_conversion, daemon=True).start()

    return jsonify({
        'success': True,
        'task_id': task_id,
        'message': 'Conversion started'
    })

@app.route('/api/progress/<task_id>')
@limiter.exempt 
def get_progress(task_id):
    with progress_lock:
        prog = conversion_progress.get(task_id, {
            'status': 'unknown',
            'percent': 0,
            'message': 'Task not found'
        }).copy()
    return jsonify(prog)

@app.route('/api/download/<task_id>')
@limiter.limit("50 per minute")
def download_file(task_id):
    title = request.args.get('title', 'media')

    with progress_lock:
        info = conversion_progress.get(task_id, {})
    is_video = info.get('format') == 'video'

    if is_video:
        preferred_exts = ['.mp4']
    else:
        preferred_exts = ['.mp3', '.m4a', '.opus', '.ogg']

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
            time.sleep(10)
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
            {'name': 'YouTube', 'icon': 'fab fa-youtube',    'color': '#FF0000'},
            {'name': 'YouTube Shorts', 'icon': 'fab fa-youtube', 'color': '#FF0000'},
            {'name': 'Facebook', 'icon': 'fab fa-facebook',  'color': '#1877F2'},
            {'name': 'Instagram', 'icon': 'fab fa-instagram','color': '#E4405F'},
            {'name': 'TikTok', 'icon': 'fab fa-tiktok',      'color': '#000000'},
            {'name': 'Twitter/X', 'icon': 'fab fa-twitter',  'color': '#1DA1F2'},
        ]
    })

@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'active_downloads': MAX_CONCURRENT_DOWNLOADS - active_downloads._value,
        'cached_videos': len(video_info_cache),
        'active_tasks': len(conversion_progress)
    })

if __name__ == '__main__':
    logger.info("[SERVER] Starting Flask app with multiple audio formats and QuickTime-friendly video...")
    logger.info(f"[SERVER] Download folder: {os.path.abspath(DOWNLOAD_FOLDER)}")
    app.run(debug=False, host='0.0.0.0', port=5000)