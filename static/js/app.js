// ===== Constants =====
const PLACEHOLDERS = {
    loading: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="320" height="180"%3E%3Crect fill="%23f0f0f0" width="320" height="180"/%3E%3Ctext x="50%25" y="50%25" font-family="Arial" font-size="16" fill="%23666" text-anchor="middle" dominant-baseline="middle"%3ELoading...%3C/text%3E%3C/svg%3E',
    noThumbnail: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="320" height="180"%3E%3Crect fill="%23e0e0e0" width="320" height="180"/%3E%3Ctext x="50%25" y="50%25" font-family="Arial" font-size="14" fill="%23999" text-anchor="middle" dominant-baseline="middle"%3ENo Preview%3C/text%3E%3C/svg%3E'
};

const API_BASE = window.location.origin || 'http://localhost:5000';

// ===== DOM Elements =====
const elements = {
    videoUrl: document.getElementById('videoUrl'),
    clearBtn: document.getElementById('clearBtn'),
    convertBtn: document.getElementById('convertBtn'),
    convertBtnText: document.getElementById('convertBtnText'),
    audioFormatBtn: document.getElementById('audioFormatBtn'),
    videoFormatBtn: document.getElementById('videoFormatBtn'),
    qualitySelection: document.getElementById('qualitySelection'),
    qualitySelect: document.getElementById('qualitySelect'),
    audioQualityInfo: document.getElementById('audioQualityInfo'),
    videoPreview: document.getElementById('videoPreview'),
    previewLoading: document.getElementById('previewLoading'),
    thumbnail: document.getElementById('thumbnail'),
    durationBadge: document.getElementById('durationBadge'),
    videoTitle: document.getElementById('videoTitle'),
    uploader: document.getElementById('uploader'),
    platform: document.getElementById('platform'),
    videoStats: document.getElementById('videoStats'),
    progressSection: document.getElementById('progressSection'),
    progressStatus: document.getElementById('progressStatus'),
    progressPercent: document.getElementById('progressPercent'),
    progressPercentDetail: document.getElementById('progressPercentDetail'),
    progressFill: document.getElementById('progressFill'),
    downloadSpeed: document.getElementById('downloadSpeed'),
    eta: document.getElementById('eta'),
    progressInfo: document.getElementById('progressInfo'),
    downloadedSize: document.getElementById('downloadedSize'),
    totalSize: document.getElementById('totalSize'),
    progressStage: document.getElementById('progressStage'),
    stageProgress: document.getElementById('stageProgress'),
    cancelBtn: document.getElementById('cancelBtn'),
    downloadSection: document.getElementById('downloadSection'),
    downloadFileName: document.getElementById('downloadFileName'),
    downloadBtn: document.getElementById('downloadBtn'),
    downloadBtnText: document.getElementById('downloadBtnText'),
    newConvertBtn: document.getElementById('newConvertBtn'),
    formatBadge: document.getElementById('formatBadge'),
    qualityTag: document.getElementById('qualityTag'),
    thumbnailTag: document.getElementById('thumbnailTag'),
    metadataTag: document.getElementById('metadataTag'),
    fileSizeValue: document.getElementById('fileSizeValue'),
    errorSection: document.getElementById('errorSection'),
    errorMessage: document.getElementById('errorMessage'),
    retryBtn: document.getElementById('retryBtn'),
    platforms: document.getElementById('platforms'),
    toast: document.getElementById('toast'),
    toastMessage: document.getElementById('toastMessage'),
    toastIcon: document.getElementById('toastIcon')
};

// Debug: Check if progress elements exist
console.log('🔍 Progress Elements Check:');
console.log('  downloadSpeed:', !!elements.downloadSpeed);
console.log('  eta:', !!elements.eta);
console.log('  downloadedSize:', !!elements.downloadedSize);
console.log('  totalSize:', !!elements.totalSize);

// ===== State =====
let state = {
    taskId: null,
    title: '',
    format: 'audio',
    quality: '720p',
    audioFormat: 'mp3',
    hasThumbnail: false,
    fileSize: 0,
    progressInterval: null,
    debounceTimer: null,
    cancelRequested: false,
    currentStage: 'starting',
    downloadedBytes: 0,
    totalBytes: 0,
    lastPercent: 0,
    isConverting: false,
    previewUrl: null,
    previewAvailable: false
};

// ===== Progress Stages =====
const STAGES = {
    initializing: { icon: '🎬', label: 'Initializing', message: 'Preparing...' },
    connecting:   { icon: '🔗', label: 'Connecting',  message: 'Connecting...' },
    starting:     { icon: '🚀', label: 'Starting',    message: 'Starting download...' },
    downloading:  { icon: '📥', label: 'Downloading', message: 'Downloading...' },
    processing:   { icon: '⚙️', label: 'Processing',  message: 'Processing...' },
    embedding:    { icon: '🖼️', label: 'Embedding',   message: 'Adding metadata...' },
    complete:     { icon: '✓',  label: 'Complete',    message: 'Ready!' },
    error:        { icon: '✗',  label: 'Error',       message: 'Error occurred' }
};

// ===== Initialize =====
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 App initialized');
    loadPlatforms();
    loadAudioFormats();
    setupEventListeners();
    setupFAQ();
    updateUIForFormat();
    hidePreview();
});

// ===== Preview Functions =====
function showPreview() {
    if (elements.videoPreview) elements.videoPreview.classList.add('active');
    state.previewAvailable = true;
}

function hidePreview() {
    if (elements.videoPreview) elements.videoPreview.classList.remove('active');
    if (elements.previewLoading) elements.previewLoading.style.display = 'none';
    state.previewAvailable = false;
}

function showPreviewLoading() {
    if (elements.videoPreview) elements.videoPreview.classList.add('active');
    if (elements.previewLoading) elements.previewLoading.style.display = 'flex';
}

// ===== Load Platforms =====
async function loadPlatforms() {
    try {
        const response = await fetch(`${API_BASE}/api/supported-platforms`);
        if (!response.ok) throw new Error();
        const data = await response.json();
        
        if (elements.platforms && data.platforms) {
            elements.platforms.innerHTML = data.platforms.map(p => `
                <div class="platform-badge">
                    <i class="${p.icon}" style="color: ${p.color}"></i>
                    <span>${p.name}</span>
                </div>
            `).join('');
        }
    } catch (e) {
        console.error('Platform load error:', e);
    }
}

// ===== Load Audio Formats =====
async function loadAudioFormats() {
    try {
        const response = await fetch(`${API_BASE}/api/audio-formats`);
        if (!response.ok) throw new Error();
        const data = await response.json();
        
        const grid = document.getElementById('audioFormatGrid');
        if (!grid) return;
        
        grid.innerHTML = data.formats.map(f => `
            <div class="audio-format-option ${f.recommended ? 'recommended' : ''} ${f.id === 'mp3' ? 'active' : ''}" 
                 data-format="${f.id}">
                <div class="format-option-header">
                    <span class="format-icon">${f.icon}</span>
                    <div>
                        <span class="format-name">${f.name}</span>
                        <span class="format-quality">${f.quality}</span>
                    </div>
                </div>
                <p class="format-description">${f.description}</p>
            </div>
        `).join('');
        
        document.querySelectorAll('.audio-format-option').forEach(opt => {
            opt.addEventListener('click', function() {
                document.querySelectorAll('.audio-format-option').forEach(o => o.classList.remove('active'));
                this.classList.add('active');
                state.audioFormat = this.dataset.format;
                updateUIForFormat();
            });
        });
    } catch (e) {
        console.error('Audio format load error:', e);
    }
}

// ===== Event Listeners =====
function setupEventListeners() {
    if (elements.audioFormatBtn) elements.audioFormatBtn.addEventListener('click', () => setFormat('audio'));
    if (elements.videoFormatBtn) elements.videoFormatBtn.addEventListener('click', () => setFormat('video'));
    if (elements.qualitySelect) elements.qualitySelect.addEventListener('change', (e) => state.quality = e.target.value);
    
    if (elements.clearBtn) {
        elements.clearBtn.addEventListener('click', () => {
            elements.videoUrl.value = '';
            hideAllSections();
            hidePreview();
            elements.videoUrl.focus();
        });
    }

    if (elements.videoUrl) {
        elements.videoUrl.addEventListener('input', () => {
            const url = elements.videoUrl.value.trim();
            clearTimeout(state.debounceTimer);
            
            if (elements.downloadSection) elements.downloadSection.classList.remove('active');
            
            if (!url) {
                hideAllSections();
                hidePreview();
                return;
            }
            
            state.debounceTimer = setTimeout(() => {
                if (isValidUrl(url)) {
                    fetchVideoInfo(url);
                } else {
                    hidePreview();
                }
            }, 800);
        });
    }

    if (elements.convertBtn) elements.convertBtn.addEventListener('click', handleConvert);
    if (elements.downloadBtn) elements.downloadBtn.addEventListener('click', handleDownload);
    if (elements.newConvertBtn) elements.newConvertBtn.addEventListener('click', resetConverter);
    if (elements.retryBtn) elements.retryBtn.addEventListener('click', handleConvert);
    if (elements.cancelBtn) elements.cancelBtn.addEventListener('click', handleCancel);
}

// ===== Format Selection =====
function setFormat(format) {
    state.format = format;
    if (elements.audioFormatBtn) elements.audioFormatBtn.classList.toggle('active', format === 'audio');
    if (elements.videoFormatBtn) elements.videoFormatBtn.classList.toggle('active', format === 'video');
    
    document.querySelector('.format-toggle')?.classList.toggle('video-mode', format === 'video');
    document.querySelector('.converter-card')?.classList.toggle('video-mode', format === 'video');
    
    updateUIForFormat();
}

function updateUIForFormat() {
    const isVideo = state.format === 'video';
    
    if (elements.qualitySelection) elements.qualitySelection.classList.toggle('active', isVideo);
    if (elements.audioQualityInfo) elements.audioQualityInfo.classList.toggle('hidden', isVideo);
    
    const audioFormatSection = document.getElementById('audioFormatSelection');
    if (audioFormatSection) audioFormatSection.classList.toggle('active', !isVideo);
    
    if (elements.convertBtn) elements.convertBtn.classList.toggle('video-mode', isVideo);
    if (elements.convertBtnText) {
        elements.convertBtnText.textContent = isVideo ? 'Download MP4' : `Convert to ${state.audioFormat.toUpperCase()}`;
    }
}

// ===== FAQ =====
function setupFAQ() {
    document.querySelectorAll('.faq-item').forEach(item => {
        item.querySelector('.faq-question')?.addEventListener('click', () => {
            const isActive = item.classList.contains('active');
            document.querySelectorAll('.faq-item').forEach(i => i.classList.remove('active'));
            if (!isActive) item.classList.add('active');
        });
    });
}

// ===== URL Validation =====
function isValidUrl(url) {
    if (!url) return false;
    
    const u = url.toLowerCase();
    if (u.includes('facebook.com/profile.php') && !u.includes('v=')) return false;
    if (u.match(/facebook\.com\/?$/)) return false;
    if (u.match(/youtube\.com\/?$/)) return false;
    
    const patterns = [
        /youtube\.com\/watch\?v=/,
        /youtube\.com\/shorts\//,
        /youtu\.be\/[a-zA-Z0-9_-]+/,
        /facebook\.com\/watch/,
        /facebook\.com\/reel\//,
        /facebook\.com\/[^/]+\/videos\/\d+/,
        /fb\.watch\//,
        /instagram\.com\/(p|reel|reels|tv)\/[a-zA-Z0-9_-]+/,
        /tiktok\.com/,
        /vm\.tiktok\.com/,
        /(twitter|x)\.com\/[^/]+\/status\/\d+/
    ];
    
    return patterns.some(p => p.test(url));
}

// ===== Fetch Video Info =====
async function fetchVideoInfo(url) {
    console.log('📡 Fetching:', url);
    state.previewUrl = url;
    
    hideAllSections();
    showPreviewLoading();
    
    if (elements.thumbnail) elements.thumbnail.src = PLACEHOLDERS.loading;
    if (elements.videoTitle) elements.videoTitle.textContent = 'Loading...';
    if (elements.uploader) elements.uploader.textContent = '';
    if (elements.durationBadge) elements.durationBadge.textContent = '--:--';
    
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 20000);
        
        const response = await fetch(`${API_BASE}/api/video-info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url }),
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.error || `Error ${response.status}`);
        }
        
        const data = await response.json();
        console.log('✅ Data:', data);
        
        if (data.error) throw new Error(data.error);
        
        const hasData = (data.title && data.title !== 'Unknown') || data.thumbnail;
        
        if (!hasData) {
            hidePreview();
            state.title = data.title || 'download';
            showToast('ℹ️ No preview, but you can convert!', 'info', 3000);
            return;
        }
        
        showPreview();
        if (elements.previewLoading) elements.previewLoading.style.display = 'none';
        
        if (elements.thumbnail && data.thumbnail) {
            const thumbUrl = data.thumbnail.startsWith('/') ? `${API_BASE}${data.thumbnail}` : data.thumbnail;
            const img = new Image();
            img.onload = () => elements.thumbnail.src = thumbUrl;
            img.onerror = () => elements.thumbnail.src = PLACEHOLDERS.noThumbnail;
            img.src = thumbUrl;
        } else if (elements.thumbnail) {
            elements.thumbnail.src = PLACEHOLDERS.noThumbnail;
        }
        
        if (elements.videoTitle) elements.videoTitle.textContent = data.title || 'Unknown';
        if (elements.uploader) elements.uploader.textContent = data.uploader || '';
        if (elements.platform) elements.platform.textContent = data.platform || '';
        if (elements.durationBadge) {
            elements.durationBadge.textContent = data.duration_formatted || '';
            elements.durationBadge.style.display = data.duration_formatted ? 'flex' : 'none';
        }
        
        state.title = data.title || 'download';
        
        if (data.available_qualities) updateQualityOptions(data.available_qualities);
        
        showToast('✅ Video loaded!', 'success', 2000);
        
    } catch (error) {
        console.error('❌ Error:', error);
        hidePreview();
        showToast('❌ ' + error.message, 'error', 3000);
    }
}

function updateQualityOptions(qualities) {
    if (!elements.qualitySelect) return;
    
    const labels = {
        'best': 'Best Quality', '1080p': '1080p HD', '720p': '720p',
        '480p': '480p', '360p': '360p', '144p': '144p'
    };
    
    elements.qualitySelect.innerHTML = ['best', '1080p', '720p', '480p', '360p', '144p'].map(q => 
        `<option value="${q}" ${q === '720p' ? 'selected' : ''}>${labels[q]}</option>`
    ).join('');
}

// ===== Handle Convert =====
async function handleConvert() {
    if (state.isConverting) return;

    const url = elements.videoUrl?.value?.trim();
    if (!url || !isValidUrl(url)) {
        showError('Please enter a valid URL');
        return;
    }

    state.isConverting = true;
    state.cancelRequested = false;
    state.lastPercent = 0;
    state.downloadedBytes = 0;
    state.totalBytes = 0;

    setLoading(true);
    hideAllSectionsExceptPreview();
    initProgressUI();
    
    if (elements.progressSection) elements.progressSection.classList.add('active');
    if (elements.cancelBtn) elements.cancelBtn.classList.add('show');

    try {
        const response = await fetch(`${API_BASE}/api/convert`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                url, 
                format: state.format, 
                quality: state.quality,
                audioFormat: state.audioFormat
            })
        });

        const data = await response.json();
        if (!response.ok || data.error) throw new Error(data.error || 'Conversion failed');
        if (!data.task_id) throw new Error('Invalid response');

        state.taskId = data.task_id;
        console.log('🎯 Task started:', state.taskId);
        
        setTimeout(() => {
            if (!state.cancelRequested) startProgressTracking();
        }, 500);

    } catch (error) {
        console.error('Convert error:', error);
        state.isConverting = false;
        showError(error.message);
        setLoading(false);
    }
}

// ===== Progress UI =====
function initProgressUI() {
    document.querySelectorAll('.stage').forEach(s => s.classList.remove('active', 'completed'));
    activateStage('initializing');
    
    if (elements.progressFill) elements.progressFill.style.width = '0%';
    if (elements.progressPercent) elements.progressPercent.textContent = '0%';
    if (elements.progressPercentDetail) elements.progressPercentDetail.textContent = '0%';
    if (elements.downloadSpeed) elements.downloadSpeed.textContent = '--';
    if (elements.eta) elements.eta.textContent = '--';
    if (elements.downloadedSize) elements.downloadedSize.textContent = '0 MB';
    if (elements.totalSize) elements.totalSize.textContent = '--';
    if (elements.progressInfo) elements.progressInfo.textContent = 'Starting...';
    if (elements.stageProgress) elements.stageProgress.textContent = '';
}

function activateStage(name) {
    state.currentStage = name;
    const stage = STAGES[name];
    if (!stage) return;
    
    if (elements.progressStatus) {
        elements.progressStatus.innerHTML = `<span>${stage.icon}</span> <span>${stage.message}</span>`;
    }
    if (elements.progressStage) elements.progressStage.textContent = stage.label;
    if (elements.progressInfo) elements.progressInfo.textContent = stage.message;
    
    document.querySelectorAll('.stage').forEach(el => {
        el.classList.remove('active');
        if (el.dataset.stage === name) el.classList.add('active');
    });
}

function completeStage(name) {
    const el = document.querySelector(`.stage[data-stage="${name}"]`);
    if (el) {
        el.classList.remove('active');
        el.classList.add('completed');
    }
}

// ===== Progress Tracking =====
function startProgressTracking() {
    console.log('🚀 Starting progress tracking');
    
    if (state.progressInterval) clearInterval(state.progressInterval);
    
    let errors = 0;
    
    state.progressInterval = setInterval(async () => {
        if (state.cancelRequested) {
            clearInterval(state.progressInterval);
            state.progressInterval = null;
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/api/progress/${state.taskId}`);
            if (!response.ok) throw new Error();
            
            const data = await response.json();
            console.log('📊 Progress:', data);  // Debug log
            
            errors = 0;
            updateProgress(data);

            if (data.status === 'completed') {
                clearInterval(state.progressInterval);
                state.progressInterval = null;
                state.title = data.title || state.title || 'download';
                state.hasThumbnail = data.has_thumbnail || false;
                state.fileSize = data.file_size || 0;
                animateProgressTo100();
            } else if (data.status === 'error') {
                clearInterval(state.progressInterval);
                state.progressInterval = null;
                state.isConverting = false;
                if (elements.cancelBtn) elements.cancelBtn.classList.remove('show');
                showError(data.message || 'Failed');
            }
        } catch (e) {
            errors++;
            console.error('Progress error:', e);
            if (errors >= 5) {
                clearInterval(state.progressInterval);
                state.progressInterval = null;
                state.isConverting = false;
                showError('Connection lost');
            }
        }
    }, 2000);  // Poll every 2 seconds for more responsive updates
}

function updateProgress(data) {
    const percent = Math.min(Math.max(Math.round(data.percent || 0), 0), 100);
    
    if (percent < state.lastPercent) return;
    state.lastPercent = percent;

    // Update progress bar
    if (elements.progressFill) elements.progressFill.style.width = `${percent}%`;
    if (elements.progressPercent) elements.progressPercent.textContent = `${percent}%`;
    if (elements.progressPercentDetail) elements.progressPercentDetail.textContent = `${percent}%`;
    
    const status = (data.status || 'starting').toLowerCase();
    
    // Stage transitions
    if (status === 'downloading' && state.currentStage !== 'downloading') {
        completeStage(state.currentStage);
        activateStage('downloading');
    } else if ((status === 'processing' || status === 'converting') && state.currentStage === 'downloading') {
        completeStage('downloading');
        activateStage('processing');
    } else if (status === 'embedding') {
        completeStage(state.currentStage);
        activateStage('embedding');
    }
    
    // Update message
    if (data.message && elements.progressInfo) {
        elements.progressInfo.textContent = data.message;
    }
    
    // ===== SPEED =====
  if (elements.downloadSpeed) {
    // Prefer human-readable speed_str from backend
    if (data.speed_str && typeof data.speed_str === 'string' && data.speed_str.trim() !== '') {
        elements.downloadSpeed.textContent = data.speed_str;
    } else {
        // Fallback to numeric speed in bytes/s
        const speed = Number(data.speed) || 0;
        if (speed > 0) {
            if (speed >= 1048576) {
                elements.downloadSpeed.textContent = `${(speed / 1048576).toFixed(2)} MB/s`;
            } else if (speed >= 1024) {
                elements.downloadSpeed.textContent = `${(speed / 1024).toFixed(0)} KB/s`;
            } else {
                elements.downloadSpeed.textContent = `${Math.round(speed)} B/s`;
            }
        } else if ((data.status || '').toLowerCase() === 'downloading') {
            elements.downloadSpeed.textContent = 'Calculating...';
        } else {
            elements.downloadSpeed.textContent = '--';
        }
    }
}
    
    // ===== ETA =====
    if (elements.eta) {
        const eta = Number(data.eta) || 0;
        console.log('ETA value:', eta);  // Debug
        
        if (eta > 0) {
            if (eta >= 3600) {
                elements.eta.textContent = `${Math.floor(eta / 3600)}h ${Math.floor((eta % 3600) / 60)}m`;
            } else if (eta >= 60) {
                elements.eta.textContent = `${Math.floor(eta / 60)}m ${Math.round(eta % 60)}s`;
            } else {
                elements.eta.textContent = `${Math.round(eta)}s`;
            }
        } else if (status === 'downloading') {
            elements.eta.textContent = 'Calculating...';
        } else {
            elements.eta.textContent = '--';
        }
    }
    
    // ===== DOWNLOADED SIZE =====
    if (elements.downloadedSize) {
        const downloaded = Number(data.downloaded_bytes) || 0;
        console.log('Downloaded bytes:', downloaded);  // Debug
        
        if (downloaded > 0) {
            elements.downloadedSize.textContent = formatBytes(downloaded);
        } else {
            elements.downloadedSize.textContent = '0 MB';
        }
    }
    
    // ===== TOTAL SIZE =====
    if (elements.totalSize) {
        const total = Number(data.total_bytes) || 0;
        console.log('Total bytes:', total);  // Debug
        
        if (total > 0) {
            elements.totalSize.textContent = formatBytes(total);
        } else if (status === 'downloading') {
            elements.totalSize.textContent = 'Calculating...';
        } else {
            elements.totalSize.textContent = '--';
        }
    }
    
    // ===== STAGE PROGRESS TEXT =====
    if (elements.stageProgress) {
        const downloaded = Number(data.downloaded_bytes) || 0;
        const total = Number(data.total_bytes) || 0;
        
        if (status === 'downloading' && total > 0) {
            elements.stageProgress.textContent = `${formatBytes(downloaded)} / ${formatBytes(total)}`;
        } else if (status === 'downloading') {
            elements.stageProgress.textContent = `${percent}%`;
        } else if (status === 'processing') {
            elements.stageProgress.textContent = 'Converting...';
        } else if (status === 'embedding') {
            elements.stageProgress.textContent = 'Finalizing...';
        }
    }
}

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function animateProgressTo100() {
    const start = state.lastPercent;
    const duration = 1000;
    const startTime = Date.now();
    
    const animate = () => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const current = Math.round(start + (100 - start) * (1 - Math.pow(1 - progress, 3)));
        
        if (elements.progressFill) elements.progressFill.style.width = `${current}%`;
        if (elements.progressPercent) elements.progressPercent.textContent = `${current}%`;
        
        if (progress < 1) requestAnimationFrame(animate);
        else completeConversion();
    };
    
    requestAnimationFrame(animate);
}

function completeConversion() {
    completeStage(state.currentStage);
    activateStage('complete');
    if (elements.cancelBtn) elements.cancelBtn.classList.remove('show');
    state.isConverting = false;
    showDownloadSection();
}

// ===== Sections =====
function hideAllSectionsExceptPreview() {
    if (elements.progressSection) elements.progressSection.classList.remove('active');
    if (elements.downloadSection) elements.downloadSection.classList.remove('active');
    if (elements.errorSection) elements.errorSection.classList.remove('active');
}

function hideAllSections() {
    hideAllSectionsExceptPreview();
}

// ===== Cancel =====
function handleCancel() {
    state.cancelRequested = true;
    state.isConverting = false;
    
    if (elements.cancelBtn) {
        elements.cancelBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Cancelling...';
        elements.cancelBtn.disabled = true;
    }
    
    if (state.taskId) {
        fetch(`${API_BASE}/api/cancel/${state.taskId}`, { method: 'POST' }).catch(() => {});
    }
    
    showToast('🚫 Cancelled', 'error', 2000);
    
    setTimeout(() => {
        if (state.progressInterval) {
            clearInterval(state.progressInterval);
            state.progressInterval = null;
        }
        if (elements.cancelBtn) {
            elements.cancelBtn.classList.remove('show');
            elements.cancelBtn.innerHTML = '<i class="fas fa-times"></i> Cancel';
            elements.cancelBtn.disabled = false;
        }
        if (elements.progressSection) elements.progressSection.classList.remove('active');
        setLoading(false);
    }, 1000);
}

// ===== Download Section =====
function showDownloadSection() {
    setLoading(false);
    if (elements.progressSection) elements.progressSection.classList.remove('active');
    if (elements.downloadSection) elements.downloadSection.classList.add('active');
    
    const isVideo = state.format === 'video';
    const extMap = { mp3: 'mp3', aac: 'm4a', opus: 'opus', ogg: 'ogg' };
    const ext = isVideo ? 'mp4' : (extMap[state.audioFormat] || 'mp3');
    
    if (elements.formatBadge) {
        const label = isVideo ? 'MP4 Video' : `${state.audioFormat.toUpperCase()} Audio`;
        elements.formatBadge.innerHTML = `<i class="fas ${isVideo ? 'fa-video' : 'fa-music'}"></i><span>${label}</span>`;
    }
    
    if (elements.downloadFileName) elements.downloadFileName.textContent = `${state.title}.${ext}`;
    
    if (elements.qualityTag) {
        const qMap = { mp3: '320kbps', aac: '256kbps', opus: '192kbps', ogg: '192kbps' };
        elements.qualityTag.textContent = isVideo ? state.quality : (qMap[state.audioFormat] || '320kbps');
    }
    
    if (elements.thumbnailTag) elements.thumbnailTag.style.display = (!isVideo && state.hasThumbnail) ? 'inline-flex' : 'none';
    if (elements.metadataTag) elements.metadataTag.style.display = isVideo ? 'none' : 'inline-flex';
    
    if (elements.fileSizeValue && state.fileSize) {
        elements.fileSizeValue.textContent = state.fileSize >= 1048576 
            ? `${(state.fileSize / 1048576).toFixed(2)} MB` 
            : `${(state.fileSize / 1024).toFixed(0)} KB`;
    }
    
    if (elements.downloadBtnText) elements.downloadBtnText.textContent = `Download ${ext.toUpperCase()}`;
    
    showToast('🎉 Ready!', 'success', 2000);
}

// ===== Handle Download =====
function handleDownload() {
    if (!state.taskId) {
        showToast('❌ No file', 'error', 2000);
        return;
    }
    
    const extMap = { mp3: 'mp3', aac: 'm4a', opus: 'opus', ogg: 'ogg' };
    const ext = state.format === 'video' ? 'mp4' : (extMap[state.audioFormat] || 'mp3');
    
    const link = document.createElement('a');
    link.href = `${API_BASE}/api/download/${state.taskId}?title=${encodeURIComponent(state.title)}`;
    link.download = `${state.title}.${ext}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    showToast('📥 Downloading...', 'success', 2000);
}

// ===== Error =====
function showError(message) {
    setLoading(false);
    
    if (state.progressInterval) {
        clearInterval(state.progressInterval);
        state.progressInterval = null;
    }
    
    state.isConverting = false;
    if (elements.cancelBtn) elements.cancelBtn.classList.remove('show');
    
    hideAllSectionsExceptPreview();
    
    if (elements.errorMessage) elements.errorMessage.textContent = message;
    if (elements.errorSection) elements.errorSection.classList.add('active');
    
    activateStage('error');
}

// ===== Reset =====
function resetConverter() {
    if (state.progressInterval) {
        clearInterval(state.progressInterval);
        state.progressInterval = null;
    }
    
    state.taskId = null;
    state.title = '';
    state.isConverting = false;
    state.lastPercent = 0;
    
    if (elements.videoUrl) elements.videoUrl.value = '';
    
    hideAllSections();
    hidePreview();
    
    if (elements.videoUrl) elements.videoUrl.focus();
}

// ===== Loading =====
function setLoading(loading) {
    if (elements.convertBtn) {
        elements.convertBtn.disabled = loading;
        elements.convertBtn.classList.toggle('loading', loading);
    }
    if (elements.videoUrl) elements.videoUrl.disabled = loading;
    if (elements.audioFormatBtn) elements.audioFormatBtn.disabled = loading;
    if (elements.videoFormatBtn) elements.videoFormatBtn.disabled = loading;
    if (elements.qualitySelect) elements.qualitySelect.disabled = loading;
}

// ===== Toast =====
function showToast(message, type = 'info', duration = 3000) {
    if (!elements.toast || !elements.toastMessage) return;
    
    elements.toastMessage.textContent = message;
    elements.toast.className = `toast ${type}`;
    
    const icons = { info: 'fa-info-circle', success: 'fa-check-circle', error: 'fa-exclamation-circle' };
    if (elements.toastIcon) elements.toastIcon.className = `fas ${icons[type] || icons.info}`;
    
    elements.toast.classList.add('show');
    setTimeout(() => elements.toast?.classList.remove('show'), duration);
}

// ===== Keyboard =====
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && state.isConverting) handleCancel();
    if (e.key === 'Enter' && document.activeElement === elements.videoUrl && !state.isConverting) handleConvert();
});

console.log('📦 MediaGrab loaded');