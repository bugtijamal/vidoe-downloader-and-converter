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

# Try to import psutil for better process management
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
MAX_DURATION = 14400          # 4 hours max
CLEANUP_AGE = 600             # 10 minutes
MAX_CONCURRENT_DOWNLOADS = 5
DOWNLOAD_TIMEOUT = 3600       # 1 hour max download time
STALL_TIMEOUT = 180           # 3 minutes without progress = stalled
PROCESSING_STALL_TIMEOUT = 600  # 10 minutes for processing stage
FFMPEG_TIMEOUT = 1800         # 30 minutes for ffmpeg conversion

for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
    os.makedirs(folder, exist_ok=True)

conversion_progress = {}
progress_lock = threading.Lock()
active_downloads = threading.Semaphore(MAX_CONCURRENT_DOWNLOADS)

# Track active processes
active_processes = {}
process_lock = threading.Lock()

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


# ================== Helper: Calculate Dynamic Timeout ==================
def calculate_timeout(duration, file_size_estimate=None):
    """Calculate timeout based on video duration and estimated file size"""
    if not duration:
        return DOWNLOAD_TIMEOUT
    
    # Base: 10 minutes + 2 minutes per 10 minutes of video
    base_timeout = 600
    duration_factor = (duration // 600) * 120
    
    # For very long videos (> 1 hour), add extra time
    if duration > 3600:
        extra_time = ((duration - 3600) // 600) * 60
    else:
        extra_time = 0
    
    total_timeout = base_timeout + duration_factor + extra_time
    
    # Cap at 2 hours
    return min(total_timeout, 7200)


def calculate_ffmpeg_timeout(duration):
    """Calculate ffmpeg timeout based on video duration"""
    if not duration:
        return FFMPEG_TIMEOUT
    
    # Base: 5 minutes + 30 seconds per minute of video
    base_timeout = 300
    duration_factor = (duration // 60) * 30
    
    total_timeout = base_timeout + duration_factor
    
    # Cap at 2 hours
    return min(total_timeout, 7200)


def kill_process_tree(pid):
    """Kill a process and all its children"""
    if not HAS_PSUTIL:
        try:
            os.kill(pid, 9)
        except:
            pass
        return True
    
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        
        # Kill children first
        for child in children:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        
        # Kill parent
        try:
            parent.kill()
        except psutil.NoSuchProcess:
            pass
            
        return True
    except Exception as e:
        logger.error(f"[KILL] Error killing process {pid}: {e}")
        return False


def run_ffmpeg_with_progress(cmd, task_id, timeout=1800, stage="processing"):
    """Run ffmpeg command with progress updates to prevent stall detection"""
    try:
        # Start the process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            bufsize=1
        )
        
        # Store process for potential killing
        with process_lock:
            active_processes[task_id] = process
        
        start_time = time.time()
        last_update = time.time()
        
        # Monitor the process
        while True:
            # Check if process is still running
            poll = process.poll()
            if poll is not None:
                # Process finished
                stdout, stderr = process.communicate()
                
                with process_lock:
                    if task_id in active_processes:
                        del active_processes[task_id]
                
                if poll == 0:
                    return True, ""
                else:
                    return False, stderr
            
            # Check timeout
            if time.time() - start_time > timeout:
                logger.error(f"[FFMPEG] Timeout for task {task_id[:8]}")
                try:
                    process.kill()
                except:
                    pass
                with process_lock:
                    if task_id in active_processes:
                        del active_processes[task_id]
                return False, "FFmpeg timeout"
            
            # Update progress periodically to prevent stall detection
            if time.time() - last_update > 10:  # Update every 10 seconds
                with progress_lock:
                    if task_id in conversion_progress:
                        conversion_progress[task_id]['last_update'] = time.time()
                        current = conversion_progress[task_id]
                        if current.get('status') == stage:
                            elapsed = int(time.time() - start_time)
                            base_msg = current.get('message', 'Processing').split('(')[0].strip()
                            current['message'] = f"{base_msg} ({elapsed}s elapsed)..."
                last_update = time.time()
            
            time.sleep(0.5)
            
    except Exception as e:
        logger.error(f"[FFMPEG] Exception: {e}")
        with process_lock:
            if task_id in active_processes:
                del active_processes[task_id]
        return False, str(e)


# ================== Stall Detection ==================
def check_stalled_downloads():
    """Background thread to detect and handle stalled downloads"""
    while True:
        try:
            time.sleep(15)  # Check every 15 seconds
            current_time = time.time()
            
            with progress_lock:
                for task_id, info in list(conversion_progress.items()):
                    status = info.get('status', '')
                    
                    # Skip completed, error, or cancelled tasks
                    if status in ['completed', 'error', 'cancelled', 'unknown']:
                        continue
                    
                    last_update = info.get('last_update', 0)
                    if last_update == 0:
                        continue
                    
                    stall_time = current_time - last_update
                    
                    # Different stall timeouts for different stages
                    if status == 'downloading':
                        timeout = STALL_TIMEOUT
                    elif status in ['processing', 'embedding']:
                        timeout = PROCESSING_STALL_TIMEOUT  # More time for processing
                    else:
                        timeout = STALL_TIMEOUT
                    
                    if stall_time > timeout:
                        logger.warning(f"[STALL] Task {task_id[:8]} stalled for {int(stall_time)}s in status '{status}'")
                        
                        # Try to kill any active process
                        with process_lock:
                            if task_id in active_processes:
                                process = active_processes[task_id]
                                try:
                                    if hasattr(process, 'pid'):
                                        kill_process_tree(process.pid)
                                    else:
                                        process.kill()
                                    del active_processes[task_id]
                                    logger.info(f"[STALL] Killed process for {task_id[:8]}")
                                except Exception as e:
                                    logger.error(f"[STALL] Error killing process: {e}")
                        
                        # Mark as error
                        conversion_progress[task_id] = {
                            'status': 'error',
                            'percent': info.get('percent', 0),
                            'message': f'Process stalled after {int(stall_time)}s. Please try again.',
                            'last_update': current_time
                        }
                        
                        # Cleanup files
                        try:
                            for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
                                for fname in os.listdir(folder):
                                    if fname.startswith(task_id):
                                        try:
                                            os.remove(os.path.join(folder, fname))
                                        except:
                                            pass
                        except Exception as e:
                            logger.error(f"[STALL] Cleanup error: {e}")
                            
        except Exception as e:
            logger.error(f"[STALL-CHECKER] Error: {e}")

# Start stall detection thread
threading.Thread(target=check_stalled_downloads, daemon=True).start()


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
                                    task_id = filename.split('.')[0].split('_')[0]
                                    with progress_lock:
                                        task_info = conversion_progress.get(task_id, {})
                                        # Don't delete files for active tasks
                                        if task_info.get('status') not in ['downloading', 'processing', 'embedding', 'connecting', 'starting']:
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
    """Download and optimize thumbnail image for artwork"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36'
        }
        resp = requests.get(url, timeout=15, headers=headers, stream=True)
        resp.raise_for_status()
        
        img = Image.open(BytesIO(resp.content))

        if img.mode != 'RGB':
            img = img.convert('RGB')

        w, h = img.size
        if w != h:
            min_side = min(w, h)
            left = (w - min_side) // 2
            top = (h - min_side) // 2
            right = left + min_side
            bottom = top + min_side
            img = img.crop((left, top, right, bottom))

        target_size = 600
        if img.width != target_size or img.height != target_size:
            img = img.resize((target_size, target_size), Image.Resampling.LANCZOS)

        img.save(save_path, 'JPEG', quality=95, optimize=True, progressive=False)
        
        if os.path.exists(save_path):
            saved_size = os.path.getsize(save_path)
            logger.info(f"[THUMBNAIL] Saved: {save_path} ({saved_size} bytes)")
            return True
        
        return False

    except Exception as e:
        logger.error(f"[THUMBNAIL ERROR] {e}")
        return False


def embed_artwork_mp3_ffmpeg(audio_file, thumbnail_path, output_file, task_id):
    """Embed artwork in MP3 with progress tracking"""
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
        
        success, error = run_ffmpeg_with_progress(cmd, task_id, timeout=120, stage="embedding")
        if success and os.path.exists(output_file):
            return True
        
        logger.error(f"[FFMPEG-MP3] {error[:200] if error else 'unknown error'}")
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
    """Enhanced progress hook with better tracking for long downloads"""
    try:
        with progress_lock:
            if task_id not in conversion_progress:
                return
            
            # Always update last_update timestamp
            conversion_progress[task_id]['last_update'] = time.time()
            
            if d['status'] == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                downloaded = d.get('downloaded_bytes', 0)
                speed = d.get('speed', 0)
                eta = d.get('eta', 0)
                
                if total > 0:
                    percent = min((downloaded / total) * 85, 85)
                    
                    # Format sizes for display
                    downloaded_mb = downloaded / (1024 * 1024)
                    total_mb = total / (1024 * 1024)
                    
                    message = f'Downloading... {int(percent)}% ({downloaded_mb:.1f}/{total_mb:.1f} MB)'
                    
                    if speed and speed > 0:
                        speed_mb = speed / (1024 * 1024)
                        message += f' @ {speed_mb:.1f} MB/s'
                    
                    if eta and eta > 0:
                        eta_min = int(eta // 60)
                        eta_sec = int(eta % 60)
                        if eta_min > 0:
                            message += f' - ETA: {eta_min}m {eta_sec}s'
                        else:
                            message += f' - ETA: {eta_sec}s'
                else:
                    current = conversion_progress.get(task_id, {})
                    percent = min(current.get('percent', 5) + 0.3, 85)
                    message = f'Downloading... {int(percent)}%'
                
                current_percent = conversion_progress[task_id].get('percent', 0)
                # Update more frequently (every 0.5%)
                if abs(percent - current_percent) >= 0.5 or (time.time() - conversion_progress[task_id].get('last_message_update', 0)) > 5:
                    conversion_progress[task_id].update({
                        'status': 'downloading',
                        'percent': percent,
                        'speed': speed,
                        'eta': eta,
                        'downloaded_bytes': downloaded,
                        'total_bytes': total,
                        'message': message,
                        'last_message_update': time.time()
                    })
                    
            elif d['status'] == 'finished':
                conversion_progress[task_id].update({
                    'status': 'processing',
                    'percent': 87,
                    'message': 'Download complete. Processing file...',
                    'last_update': time.time()
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
    """Enhanced yt-dlp options for better handling of long videos"""
    return {
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
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'referer': 'https://www.youtube.com/',
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-us,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
        },
        'http_chunk_size': 10485760,
        'prefer_ffmpeg': True,
        'keepvideo': False,
        'no_color': True,
        'concurrent_fragment_downloads': 4,
        'buffersize': 1024 * 16,
        'noprogress': False,
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
                hours = MAX_DURATION // 3600
                return jsonify({'error': f'Video too long. Max {hours} hours allowed.'}), 400

            # Format duration nicely
            if duration:
                hours = duration // 3600
                minutes = (duration % 3600) // 60
                seconds = duration % 60
                if hours > 0:
                    duration_formatted = f"{hours}:{minutes:02d}:{seconds:02d}"
                else:
                    duration_formatted = f"{minutes}:{seconds:02d}"
            else:
                duration_formatted = 'Unknown'

            result = {
                'success': True,
                'title': info.get('title', 'Unknown'),
                'duration': duration,
                'duration_formatted': duration_formatted,
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
            'message': 'Preparing download...',
            'last_update': time.time()
        }

    logger.info(f"[CONVERT] Started {task_id[:8]} - {format_type} ({audio_format if format_type=='audio' else quality})")

    output_filename = f"{task_id}"
    output_path = os.path.join(DOWNLOAD_FOLDER, output_filename)
    thumbnail_path = os.path.join(TEMP_FOLDER, f"{task_id}_thumb.jpg")

    def run_conversion():
        acquired = False
        start_time = time.time()
        video_duration = 0
        
        try:
            acquired = active_downloads.acquire(timeout=120)
            if not acquired:
                raise Exception("Server busy. Too many concurrent downloads. Please try again.")

            with progress_lock:
                conversion_progress[task_id].update({
                    'status': 'connecting',
                    'percent': 2,
                    'message': 'Connecting to server...',
                    'last_update': time.time()
                })

            # First, get video info to calculate timeout
            try:
                ydl_opts_info = get_base_ydl_opts()
                ydl_opts_info.update({'skip_download': True})
                with yt_dlp.YoutubeDL(ydl_opts_info) as ydl:
                    pre_info = ydl.extract_info(url, download=False)
                    if pre_info.get('_type') == 'playlist' and pre_info.get('entries'):
                        pre_info = pre_info['entries'][0]
                    video_duration = pre_info.get('duration', 0)
                    
                    if video_duration > 3600:
                        logger.info(f"[CONVERT] Long video detected: {video_duration//60} minutes")
            except Exception as e:
                logger.warning(f"[CONVERT] Could not get duration: {e}")
                video_duration = 3600  # Assume 1 hour if unknown

            # Calculate dynamic timeout
            download_timeout = calculate_timeout(video_duration)
            ffmpeg_timeout = calculate_ffmpeg_timeout(video_duration)
            
            logger.info(f"[CONVERT] Timeouts - Download: {download_timeout}s, FFmpeg: {ffmpeg_timeout}s")

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
                    'message': 'Starting download...',
                    'last_update': time.time()
                })

            # Download with timeout using threading
            download_completed = threading.Event()
            download_error = [None]
            info_data = [None]

            def download_thread():
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(url, download=True)
                        info_data[0] = info
                    download_completed.set()
                except Exception as e:
                    download_error[0] = str(e)
                    download_completed.set()

            dl_thread = threading.Thread(target=download_thread, daemon=True)
            dl_thread.start()

            # Wait for download with timeout
            if not download_completed.wait(timeout=download_timeout):
                logger.error(f"[CONVERT] Download timed out after {download_timeout}s")
                raise Exception(f"Download timed out after {download_timeout//60} minutes. The video might be too large or the connection is slow.")

            if download_error[0]:
                raise Exception(download_error[0])

            info = info_data[0]
            if not info:
                raise Exception("Failed to extract video info")

            title = info.get('title', 'media')
            clean_title = sanitize_filename(title)

            # Update progress
            with progress_lock:
                conversion_progress[task_id].update({
                    'status': 'processing',
                    'percent': 86,
                    'message': 'Download complete. Locating file...',
                    'last_update': time.time()
                })

            # Locate downloaded file
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
            logger.info(f"[CONVERT] Downloaded: {downloaded_file} ({file_size / (1024*1024):.1f} MB)")

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
                    raise Exception(f"{cfg['name']} conversion failed: {error[:200] if error else 'unknown'}")

                # Download thumbnail
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
                    'artist': info.get('uploader', 'Unknown'),
                    'year': year,
                    'genre': 'Music'
                }

                if thumb_ok:
                    with progress_lock:
                        conversion_progress[task_id].update({
                            'status': 'embedding',
                            'percent': 95,
                            'message': 'Embedding artwork...',
                            'last_update': time.time()
                        })
                    if audio_format == 'mp3':
                        if not embed_artwork_mp3_ffmpeg(audio_temp, thumbnail_path, final_audio, task_id):
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

                # Update progress during cleanup
                with progress_lock:
                    conversion_progress[task_id]['last_update'] = time.time()

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

            # ===== VIDEO BRANCH (OPTIMIZED - NO RE-ENCODING FOR BEST QUALITY) =====
            else:
                # Check if we need to re-encode or can use the file directly
                needs_encoding = True
                needs_full_encoding = True
                output_file = None
                
                file_ext = os.path.splitext(downloaded_file)[1].lower()
                
                # If quality is "best", try to avoid re-encoding
                if quality == 'best':
                    # If already MP4, use it directly without re-encoding
                    if file_ext == '.mp4':
                        logger.info(f"[VIDEO] Quality='best' and file is MP4. Skipping re-encoding!")
                        output_file = downloaded_file
                        needs_encoding = False
                        
                        with progress_lock:
                            conversion_progress[task_id].update({
                                'status': 'processing',
                                'percent': 95,
                                'message': 'Video ready (no conversion needed)...',
                                'last_update': time.time()
                            })
                    
                    # If WebM or MKV, try fast stream copy (no re-encoding, just remux)
                    elif file_ext in ['.webm', '.mkv']:
                        logger.info(f"[VIDEO] Quality='best' and file is {file_ext}. Will remux to MP4 (fast, no re-encoding).")
                        needs_encoding = True
                        needs_full_encoding = False  # Just remux, don't re-encode
                    else:
                        logger.info(f"[VIDEO] Quality='best' but file is {file_ext}. Will convert to MP4.")
                        needs_encoding = True
                        needs_full_encoding = True
                else:
                    # User selected specific quality (1080p, 720p, etc.) - need to re-encode
                    logger.info(f"[VIDEO] Quality='{quality}'. Will re-encode to {quality}.")
                    needs_encoding = True
                    needs_full_encoding = True
                
                # Process video if needed
                if needs_encoding:
                    with progress_lock:
                        if needs_full_encoding:
                            msg = f'Converting video to MP4 ({quality})...'
                        else:
                            msg = 'Remuxing to MP4 (fast)...'
                        conversion_progress[task_id].update({
                            'status': 'processing',
                            'percent': 88,
                            'message': msg,
                            'last_update': time.time()
                        })

                    desired_mp4 = output_path + '.mp4'

                    if os.path.abspath(downloaded_file) == os.path.abspath(desired_mp4):
                        temp_mp4 = output_path + '__enc.mp4'
                    else:
                        temp_mp4 = desired_mp4

                    # Build ffmpeg command
                    if needs_full_encoding:
                        # Full re-encoding (for quality changes)
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

                        # Only add scale filter if we're downscaling
                        if encode_cfg['scale']:
                            cmd.extend(['-vf', f"scale={encode_cfg['scale']}"])

                        # Use CRF for quality control
                        if encode_cfg['crf']:
                            cmd.extend(['-crf', encode_cfg['crf']])

                        # Optionally cap bitrate for smaller files
                        if encode_cfg['maxrate'] and encode_cfg['bufsize']:
                            cmd.extend(['-maxrate', encode_cfg['maxrate'], '-bufsize', encode_cfg['bufsize']])

                        cmd.append(temp_mp4)
                        
                        logger.info(f"[VIDEO] Full re-encoding with timeout {ffmpeg_timeout}s")
                    else:
                        # Fast remux (just change container, no re-encoding)
                        # This is MUCH faster (seconds instead of minutes)
                        cmd = [
                            'ffmpeg', '-y',
                            '-i', downloaded_file,
                            '-c', 'copy',  # Copy streams without re-encoding
                            '-movflags', '+faststart',
                            temp_mp4
                        ]
                        logger.info(f"[VIDEO] Fast remux (no re-encoding)")
                        # Use shorter timeout for remux
                        ffmpeg_timeout = min(ffmpeg_timeout, 300)  # Max 5 min for remux

                    success, error = run_ffmpeg_with_progress(cmd, task_id, timeout=ffmpeg_timeout, stage="processing")
                    
                    if not success or not os.path.exists(temp_mp4):
                        # If remux failed, try full encoding as fallback
                        if not needs_full_encoding:
                            logger.warning(f"[VIDEO] Remux failed, falling back to full encoding")
                            with progress_lock:
                                conversion_progress[task_id].update({
                                    'status': 'processing',
                                    'percent': 89,
                                    'message': 'Remux failed, converting with encoding...',
                                    'last_update': time.time()
                                })
                            
                            cmd = [
                                'ffmpeg', '-y',
                                '-i', downloaded_file,
                                '-map', '0:v:0',
                                '-map', '0:a:0?',
                                '-c:v', 'libx264',
                                '-preset', 'veryfast',
                                '-crf', '20',
                                '-c:a', 'aac',
                                '-b:a', '160k',
                                '-movflags', '+faststart',
                                temp_mp4
                            ]
                            ffmpeg_timeout = calculate_ffmpeg_timeout(video_duration)
                            success, error = run_ffmpeg_with_progress(cmd, task_id, timeout=ffmpeg_timeout, stage="processing")
                            
                            if not success or not os.path.exists(temp_mp4):
                                raise Exception(f"ffmpeg error: {error[:200] if error else 'unknown error'}")
                        else:
                            raise Exception(f"ffmpeg error: {error[:200] if error else 'unknown error'}")

                    # Rename temp to final if needed
                    if os.path.abspath(temp_mp4) != os.path.abspath(desired_mp4):
                        if os.path.exists(desired_mp4):
                            os.remove(desired_mp4)
                        os.rename(temp_mp4, desired_mp4)

                    output_file = desired_mp4

                    # Update progress
                    with progress_lock:
                        conversion_progress[task_id]['last_update'] = time.time()

                    # Remove original downloaded file if different from output
                    try:
                        if os.path.exists(downloaded_file) and os.path.abspath(downloaded_file) != os.path.abspath(output_file):
                            os.remove(downloaded_file)
                            logger.info(f"[VIDEO] Removed original: {downloaded_file}")
                    except Exception as e:
                        logger.warning(f"[VIDEO] Could not remove original: {e}")
                
                thumb_downloaded = False

            # Verify final output
            if not os.path.exists(output_file):
                raise Exception("Final output file not found")
            final_size = os.path.getsize(output_file)
            if final_size < 1000:
                raise Exception("Output file is too small")

            ext = os.path.splitext(output_file)[1][1:]

            # Calculate total time taken
            total_time = time.time() - start_time
            time_str = f"{int(total_time//60)}m {int(total_time%60)}s" if total_time >= 60 else f"{int(total_time)}s"

            with progress_lock:
                conversion_progress[task_id] = {
                    'status': 'completed',
                    'percent': 100,
                    'message': f'Ready! (took {time_str})',
                    'title': clean_title,
                    'filename': os.path.basename(output_file),
                    'file_size': final_size,
                    'has_thumbnail': thumb_downloaded if format_type == 'audio' else False,
                    'format': format_type,
                    'audio_format': audio_format if format_type == 'audio' else None,
                    'quality': AUDIO_FORMATS[audio_format]['quality'] if format_type == 'audio' else quality,
                    'extension': ext,
                    'last_update': time.time()
                }

            logger.info(f"[CONVERT] ✓ {clean_title[:30]} ({final_size / (1024*1024):.1f} MB, {ext}) in {time_str}")

        except Exception as e:
            err = str(e)[:300]
            logger.error(f"[CONVERT] ✗ {task_id[:8]}: {err}")
            logger.error(traceback.format_exc())
            
            # Kill any remaining processes
            with process_lock:
                if task_id in active_processes:
                    try:
                        process = active_processes[task_id]
                        if hasattr(process, 'pid'):
                            kill_process_tree(process.pid)
                        else:
                            process.kill()
                        del active_processes[task_id]
                    except:
                        pass
            
            with progress_lock:
                conversion_progress[task_id] = {
                    'status': 'error',
                    'percent': 0,
                    'message': f'Error: {err}',
                    'last_update': time.time()
                }
            
            # Cleanup partial files
            try:
                for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
                    for fname in os.listdir(folder):
                        if fname.startswith(task_id):
                            try:
                                os.remove(os.path.join(folder, fname))
                            except:
                                pass
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


@app.route('/api/cancel/<task_id>', methods=['POST'])
def cancel_download(task_id):
    """Cancel a running download"""
    # Kill any active process
    with process_lock:
        if task_id in active_processes:
            try:
                process = active_processes[task_id]
                if hasattr(process, 'pid'):
                    kill_process_tree(process.pid)
                else:
                    process.kill()
                del active_processes[task_id]
                logger.info(f"[CANCEL] Killed process for {task_id[:8]}")
            except Exception as e:
                logger.error(f"[CANCEL] Error killing process: {e}")
    
    # Update status
    with progress_lock:
        if task_id in conversion_progress:
            conversion_progress[task_id] = {
                'status': 'cancelled',
                'percent': 0,
                'message': 'Download cancelled by user',
                'last_update': time.time()
            }
    
    # Cleanup files
    try:
        for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
            for fname in os.listdir(folder):
                if fname.startswith(task_id):
                    try:
                        os.remove(os.path.join(folder, fname))
                    except:
                        pass
    except Exception:
        pass
    
    logger.info(f"[CANCEL] Task {task_id[:8]} cancelled")
    return jsonify({'status': 'cancelled'})


@app.route('/api/progress/<task_id>')
@limiter.exempt 
def get_progress(task_id):
    with progress_lock:
        prog = conversion_progress.get(task_id, {
            'status': 'unknown',
            'percent': 0,
            'message': 'Task not found'
        }).copy()
    
    # Remove internal tracking fields from response
    prog.pop('last_update', None)
    prog.pop('last_message_update', None)
    
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
    active_procs = 0
    with process_lock:
        active_procs = len(active_processes)
    
    return jsonify({
        'status': 'healthy',
        'active_downloads': MAX_CONCURRENT_DOWNLOADS - active_downloads._value,
        'active_processes': active_procs,
        'cached_videos': len(video_info_cache),
        'active_tasks': len(conversion_progress),
        'max_duration_hours': MAX_DURATION // 3600,
        'psutil_available': HAS_PSUTIL
    })


# Emergency endpoint to kill stuck tasks
@app.route('/api/admin/kill-all', methods=['POST'])
def kill_all_downloads():
    """Emergency: Kill all active downloads"""
    killed_procs = 0
    
    # Kill all processes
    with process_lock:
        for task_id, process in list(active_processes.items()):
            try:
                if hasattr(process, 'pid'):
                    kill_process_tree(process.pid)
                else:
                    process.kill()
                killed_procs += 1
            except:
                pass
        active_processes.clear()
    
    # Clear all progress
    killed_tasks = 0
    with progress_lock:
        for task_id in list(conversion_progress.keys()):
            if conversion_progress[task_id].get('status') not in ['completed', 'error', 'cancelled']:
                conversion_progress[task_id] = {
                    'status': 'error',
                    'percent': 0,
                    'message': 'Manually terminated'
                }
                killed_tasks += 1
    
    # Cleanup all files
    cleaned = 0
    for folder in [DOWNLOAD_FOLDER, TEMP_FOLDER]:
        try:
            for fname in os.listdir(folder):
                try:
                    os.remove(os.path.join(folder, fname))
                    cleaned += 1
                except:
                    pass
        except:
            pass
    
    logger.warning(f"[ADMIN] Killed {killed_procs} processes, {killed_tasks} tasks, cleaned {cleaned} files")
    return jsonify({'killed_processes': killed_procs, 'killed_tasks': killed_tasks, 'cleaned_files': cleaned})


# Status endpoint for debugging
@app.route('/api/admin/status')
def admin_status():
    """Get detailed status for debugging"""
    with progress_lock:
        tasks = {}
        for task_id, info in conversion_progress.items():
            tasks[task_id[:8]] = {
                'status': info.get('status'),
                'percent': info.get('percent'),
                'message': info.get('message', '')[:50],
                'age': int(time.time() - info.get('last_update', time.time()))
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
    logger.info("[SERVER] Starting Flask Video Downloader")
    logger.info(f"[SERVER] Max video duration: {MAX_DURATION // 3600} hours")
    logger.info(f"[SERVER] Download timeout: {DOWNLOAD_TIMEOUT // 60} minutes")
    logger.info(f"[SERVER] Stall timeout: {STALL_TIMEOUT}s (download), {PROCESSING_STALL_TIMEOUT}s (processing)")
    logger.info(f"[SERVER] Download folder: {os.path.abspath(DOWNLOAD_FOLDER)}")
    logger.info(f"[SERVER] psutil available: {HAS_PSUTIL}")
    logger.info("[SERVER] Optimization: 'best' quality skips re-encoding for MP4 files")
    logger.info("=" * 60)
    
    if not HAS_PSUTIL:
        logger.warning("[SERVER] psutil not installed. Install with: pip install psutil")
    
    app.run(debug=False, host='0.0.0.0', port=5000)