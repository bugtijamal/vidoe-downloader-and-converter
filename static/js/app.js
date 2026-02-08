// ===== Constants =====
const PLACEHOLDERS = {
    loading: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="320" height="180"%3E%3Crect fill="%23f0f0f0" width="320" height="180"/%3E%3Ctext x="50%25" y="50%25" font-family="Arial" font-size="16" fill="%23666" text-anchor="middle" dominant-baseline="middle"%3ELoading...%3C/text%3E%3C/svg%3E',
    noThumbnail: 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="320" height="180"%3E%3Crect fill="%23e0e0e0" width="320" height="180"/%3E%3Ctext x="50%25" y="50%25" font-family="Arial" font-size="14" fill="%23999" text-anchor="middle" dominant-baseline="middle"%3ENo Thumbnail%3C/text%3E%3C/svg%3E'
};

// ===== API Base URL =====
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
    viewCount: document.getElementById('viewCount'),
    likeCount: document.getElementById('likeCount'),
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

// Check if critical elements exist
console.log('🔍 Checking DOM elements...');
let missingElements = [];
Object.entries(elements).forEach(([key, element]) => {
    if (!element) {
        missingElements.push(key);
    }
});
if (missingElements.length > 0) {
    console.warn('⚠️ Missing elements:', missingElements);
}

// ===== State =====
let state = {
    taskId: null,
    title: '',
    format: 'audio',       // 'audio' or 'video'
    quality: '720p',       // for video
    audioFormat: 'mp3',    // 'mp3' | 'aac' | 'opus' | 'ogg'
    hasThumbnail: false,
    fileSize: 0,
    progressInterval: null,
    debounceTimer: null,
    cancelRequested: false,
    currentStage: 'starting',
    downloadedBytes: 0,
    totalBytes: 0,
    stageStartTime: null,
    lastPercent: 0,
    isConverting: false,
    previewUrl: null 
};

// ===== Progress Stages Configuration =====
const STAGES = {
    initializing: { icon: '🎬', label: 'Initializing', color: '#8b5cf6', message: 'Preparing download...' },
    connecting:   { icon: '🔗', label: 'Connecting',  color: '#6366f1', message: 'Connecting to server...' },
    starting:     { icon: '🚀', label: 'Starting',    color: '#6366f1', message: 'Starting download...' },
    downloading:  { icon: '📥', label: 'Download',    color: '#06b6d4', message: 'Downloading video...' },
    processing:   { icon: '⚙️', label: 'Processing',  color: '#f59e0b', message: 'Processing media...' },
    converting:   { icon: '🎵', label: 'Converting',  color: '#ec4899', message: 'Converting format...' },
    embedding:    { icon: '🖼️', label: 'Embedding',   color: '#ec4899', message: 'Adding metadata and artwork...' },
    complete:     { icon: '✓',  label: 'Complete',    color: '#10b981', message: 'Ready for download!' },
    retrying:     { icon: '🔄', label: 'Retrying',    color: '#f59e0b', message: 'Connection issue, retrying...' },
    error:        { icon: '✗',  label: 'Error',       color: '#ef4444', message: 'An error occurred...' }
};

// ===== Initialize =====
document.addEventListener('DOMContentLoaded', () => {
    console.log('🚀 App initialized');
    console.log('🌐 API Base:', API_BASE);
    loadPlatforms();
    loadAudioFormats();
    setupEventListeners();
    setupFAQ();
    updateUIForFormat();
});

// ===== Load Supported Platforms =====
async function loadPlatforms() {
    try {
        console.log('📡 Loading platforms...');
        const response = await fetch(`${API_BASE}/api/supported-platforms`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        
        if (elements.platforms && data.platforms) {
            elements.platforms.innerHTML = data.platforms.map(p => `
                <div class="platform-badge">
                    <i class="${p.icon}" style="color: ${p.color}"></i>
                    <span>${p.name}</span>
                </div>
            `).join('');
            console.log('✅ Platforms loaded');
        }
    } catch (error) {
        console.error('❌ Platform load error:', error);
    }
}

// ===== Load Audio Formats =====
async function loadAudioFormats() {
    try {
        console.log('📡 Loading audio formats...');
        const response = await fetch(`${API_BASE}/api/audio-formats`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();
        
        const audioFormatGrid = document.getElementById('audioFormatGrid');
        if (!audioFormatGrid) return;
        
        audioFormatGrid.innerHTML = data.formats.map(format => `
            <div class="audio-format-option ${format.recommended ? 'recommended' : ''} ${format.id === 'mp3' ? 'active' : ''}" 
                 data-format="${format.id}">
                <div class="format-option-header">
                    <span class="format-icon">${format.icon}</span>
                    <div>
                        <span class="format-name">${format.name}</span>
                        <span class="format-quality">${format.quality}</span>
                    </div>
                </div>
                <p class="format-description">${format.description}</p>
            </div>
        `).join('');
        
        // Add click handlers
        document.querySelectorAll('.audio-format-option').forEach(option => {
            option.addEventListener('click', function() {
                document.querySelectorAll('.audio-format-option').forEach(opt => {
                    opt.classList.remove('active');
                });
                this.classList.add('active');
                state.audioFormat = this.dataset.format;
                updateAudioFormatInfo();
                updateUIForFormat(); // update button text
            });
        });
        
        updateAudioFormatInfo();
        console.log('✅ Audio formats loaded');
    } catch (error) {
        console.error('❌ Audio formats load error:', error);
    }
}

// ===== Update Audio Format Info =====
function updateAudioFormatInfo() {
    const audioFormatInfo = document.getElementById('audioFormatInfo');
    if (!audioFormatInfo) return;
    
    const formatDescriptions = {
        'mp3':  '📱 MP3: Best choice for universal compatibility - works on all devices and players.',
        'aac':  '🍎 AAC/M4A: Optimized for Apple devices - excellent quality with smaller file size.',
        'opus': '⚡ OPUS: Modern codec with best quality-to-size ratio - great for modern players and browsers.',
        'ogg':  '🤖 OGG Vorbis: Open source format - excellent for Android and desktop players.'
    };
    
    audioFormatInfo.textContent = formatDescriptions[state.audioFormat] || formatDescriptions['mp3'];
}

// ===== Event Listeners =====
function setupEventListeners() {
    if (elements.audioFormatBtn) {
        elements.audioFormatBtn.addEventListener('click', () => setFormat('audio'));
    }
    if (elements.videoFormatBtn) {
        elements.videoFormatBtn.addEventListener('click', () => setFormat('video'));
    }
    if (elements.qualitySelect) {
        elements.qualitySelect.addEventListener('change', (e) => state.quality = e.target.value);
    }
    
    if (elements.clearBtn) {
        elements.clearBtn.addEventListener('click', () => {
            elements.videoUrl.value = '';
            hideAllSections();
            elements.videoUrl.focus();
        });
    }

    if (elements.videoUrl) {
        elements.videoUrl.addEventListener('input', () => {
            const url = elements.videoUrl.value.trim();
            clearTimeout(state.debounceTimer);
            
            if (elements.downloadSection) {
                elements.downloadSection.classList.remove('active');
            }
            
            if (!url) {
                hideAllSections();
                return;
            }
            
            state.debounceTimer = setTimeout(() => {
                if (isValidUrl(url)) {
                    fetchVideoInfo(url);
                }
            }, 800);
        });
    }
        // ===== Click preview thumbnail to play video (front-end only) =====
    const previewThumb = document.querySelector('.preview-thumbnail');
    const playOverlay = document.querySelector('.play-overlay');

    function playPreviewVideo() {
        if (!state.previewUrl) {
            showToast('No preview URL available', 'error', 2000);
            return;
        }

        // Try to build an embeddable URL (YouTube)
        const embedUrl = buildEmbedUrl(state.previewUrl);
        if (!embedUrl) {
            // For non-YouTube, fallback: open original in new tab
            window.open(state.previewUrl, '_blank');
            return;
        }

        const container = document.querySelector('.preview-thumbnail');
        if (!container) return;

        // Inject iframe into the same preview area
        container.innerHTML = `
            <iframe 
                src="${embedUrl}" 
                title="Video preview" 
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture; web-share" 
                allowfullscreen>
            </iframe>
        `;
    }

    if (previewThumb) {
        previewThumb.addEventListener('click', playPreviewVideo);
    }
    if (playOverlay) {
        // Stop the click from bubbling if needed
        playOverlay.addEventListener('click', (e) => {
            e.stopPropagation();
            playPreviewVideo();
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
    
    if (elements.audioFormatBtn) {
        elements.audioFormatBtn.classList.toggle('active', format === 'audio');
    }
    if (elements.videoFormatBtn) {
        elements.videoFormatBtn.classList.toggle('active', format === 'video');
    }
    
    const toggle = document.querySelector('.format-toggle');
    if (toggle) toggle.classList.toggle('video-mode', format === 'video');
    
    const card = document.querySelector('.converter-card');
    if (card) card.classList.toggle('video-mode', format === 'video');
    
    updateUIForFormat();
}

function updateUIForFormat() {
    const isVideo = state.format === 'video';
    
    // Video quality selection
    if (elements.qualitySelection) {
        elements.qualitySelection.classList.toggle('active', isVideo);
    }
    
    // Audio info
    if (elements.audioQualityInfo) {
        elements.audioQualityInfo.classList.toggle('hidden', isVideo);
    }
    
    // Audio format selection
    const audioFormatSelection = document.getElementById('audioFormatSelection');
    if (audioFormatSelection) {
        audioFormatSelection.classList.toggle('active', !isVideo);
    }
    
    // Convert button style & text
    if (elements.convertBtn) {
        elements.convertBtn.classList.toggle('video-mode', isVideo);
    }
    if (elements.convertBtnText) {
        const selectedFormat = state.audioFormat.toUpperCase();
        elements.convertBtnText.textContent = isVideo ? 'Download MP4' : `Convert to ${selectedFormat}`;
    }
    
    if (elements.progressSection) {
        elements.progressSection.classList.toggle('video-mode', isVideo);
    }
}

// ===== FAQ Setup =====
function setupFAQ() {
    document.querySelectorAll('.faq-item').forEach(item => {
        const question = item.querySelector('.faq-question');
        if (question) {
            question.addEventListener('click', () => {
                const isActive = item.classList.contains('active');
                document.querySelectorAll('.faq-item').forEach(i => i.classList.remove('active'));
                if (!isActive) item.classList.add('active');
            });
        }
    });
}

// ===== URL Validation =====
function isValidUrl(url) {
    if (!url || typeof url !== 'string') return false;
    
    const patterns = [
        /youtube\.com\/watch\?v=/,
        /youtube\.com\/shorts/,
        /youtu\.be\//,
        /facebook\.com\/reel\//,
        /facebook\.com\/.*\/videos\//,
        /facebook\.com\/watch/,
        /fb\.watch\//,
        /fb\.com/,
        /instagram\.com\/(p|reel|reels|tv)/,
        /tiktok\.com/,
        /vm\.tiktok\.com/,
        /(twitter|x)\.com.*\/status/,
        /t\.co\//
    ];
    return patterns.some(pattern => pattern.test(url));
}

// ===== Fetch Video Info =====
async function fetchVideoInfo(url) {
    console.log('📡 Fetching info for:', url);
    state.previewUrl = url;
    hideAllSections();
    
    if (elements.thumbnail) elements.thumbnail.src = PLACEHOLDERS.loading;
    if (elements.videoTitle) elements.videoTitle.textContent = 'Loading...';
    if (elements.uploader) elements.uploader.textContent = 'Please wait...';
    if (elements.platform) elements.platform.textContent = '...';
    if (elements.durationBadge) elements.durationBadge.textContent = '--:--';
    if (elements.videoStats) elements.videoStats.style.display = 'none';
    
    if (elements.videoPreview) {
        elements.videoPreview.classList.add('active');
    }
    if (elements.previewLoading) {
        elements.previewLoading.style.display = 'block';
    }
    
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 20000);
        
        const response = await fetch(`${API_BASE}/api/video-info`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify({ url }),
            signal: controller.signal
        });
        
        clearTimeout(timeoutId);
        
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ error: `Server error: ${response.status}` }));
            throw new Error(errorData.error || `HTTP ${response.status}`);
        }
        
        const data = await response.json();
        console.log('✅ Got data:', data);
        
        if (data.error) {
            throw new Error(data.error);
        }
        
        if (elements.thumbnail && data.thumbnail) {
            const img = new Image();
            img.onload = () => {
                elements.thumbnail.src = data.thumbnail;
            };
            img.onerror = () => {
                elements.thumbnail.src = PLACEHOLDERS.noThumbnail;
            };
            img.src = data.thumbnail;
        } else if (elements.thumbnail) {
            elements.thumbnail.src = PLACEHOLDERS.noThumbnail;
        }
        
        if (elements.videoTitle) elements.videoTitle.textContent = data.title || 'Unknown';
        if (elements.uploader) elements.uploader.textContent = data.uploader || 'Unknown';
        if (elements.platform) elements.platform.textContent = data.platform || 'Unknown';
        if (elements.durationBadge) elements.durationBadge.textContent = data.duration_formatted || '--:--';
        
        if (elements.videoStats) {
            elements.videoStats.style.display = 'none';
        }
        
        if (data.available_qualities && data.available_qualities.length > 0) {
            updateQualityOptions(data.available_qualities);
        }
        
        showToast('✅ Video loaded!', 'success', 2000);
        
    } catch (error) {
        console.error('❌ Fetch error:', error);
        
        if (error.name === 'AbortError') {
            showToast('⏱️ Request timeout - please try again', 'error', 3000);
        } else {
            showToast('❌ ' + error.message, 'error', 3000);
        }
        
        hideAllSections();
    } finally {
        if (elements.previewLoading) {
            elements.previewLoading.style.display = 'none';
        }
    }
}

function updateQualityOptions(backendQualities) {
    if (!elements.qualitySelect) return;
    
    const staticQualities = ['best', '1080p', '720p', '480p', '360p', '144p'];
    
    const allQualities = staticQualities; 

    const labels = {
        'best':  'Best Quality (Source)',
        '1080p': '1080p (Full HD)',
        '720p':  '720p (HD)',
        '480p':  '480p (SD)',
        '360p':  '360p (Low)',
        '144p':  '144p (Very Low)',
    };
    
    elements.qualitySelect.innerHTML = allQualities.map(q => {
        const selected = q === '720p' ? 'selected' : '';
        return `<option value="${q}" ${selected}>${labels[q] || q}</option>`;
    }).join('');
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
    state.currentStage = 'initializing';
    state.downloadedBytes = 0;
    state.totalBytes = 0;
    state.stageStartTime = Date.now();
    state.lastPercent = 0;

    setLoading(true);
    hideAllSectionsExceptPreview();

    initProgressUI();
    if (elements.progressSection) {
        elements.progressSection.classList.add('active');
    }
    if (elements.cancelBtn) {
        elements.cancelBtn.classList.add('show');
    }

    if (elements.progressFill) elements.progressFill.style.width = '1%';
    if (elements.progressPercent) elements.progressPercent.textContent = '1%';
    if (elements.progressPercentDetail) elements.progressPercentDetail.textContent = '1%';

    try {
        console.log('🔄 Converting:', url, 'Format:', state.format, 'Audio Format:', state.audioFormat);

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
        console.log('📦 Convert response:', data);

        if (!response.ok || data.error) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }

        if (!data.task_id) {
            throw new Error('Invalid response: missing task_id');
        }

        state.taskId = data.task_id;

        setTimeout(() => {
            if (!state.cancelRequested) {
                startProgressTracking();
            }
        }, 500);

    } catch (error) {
        console.error('❌ Convert error:', error);
        state.isConverting = false;
        showError(error.message || 'Conversion failed');
        setLoading(false);
    }
}

// ===== Initialize Progress UI =====
function initProgressUI() {
    document.querySelectorAll('.stage').forEach(stage => {
        stage.classList.remove('active', 'completed');
    });
    
    activateStage('initializing');
    
    if (elements.progressFill) elements.progressFill.style.width = '0%';
    if (elements.progressPercent) elements.progressPercent.textContent = '0%';
    if (elements.progressPercentDetail) elements.progressPercentDetail.textContent = '0%';
    
    if (elements.downloadSpeed) elements.downloadSpeed.textContent = '--';
    if (elements.eta) elements.eta.textContent = '--';
    if (elements.downloadedSize) elements.downloadedSize.textContent = '0 MB';
    if (elements.totalSize) elements.totalSize.textContent = '--';
    if (elements.progressStage) elements.progressStage.textContent = 'Starting';
    if (elements.progressInfo) elements.progressInfo.textContent = STAGES.initializing.message;
    if (elements.stageProgress) elements.stageProgress.textContent = '';
}

// ===== Activate Stage =====
function activateStage(stageName) {
    state.currentStage = stageName;
    state.stageStartTime = Date.now();
    
    const stage = STAGES[stageName];
    if (!stage) return;
    
    if (elements.progressStatus) {
        elements.progressStatus.innerHTML = `<span class="status-icon">${stage.icon}</span> <span class="status-text">${stage.message}</span>`;
    }
    if (elements.progressStage) {
        elements.progressStage.textContent = stage.label;
    }
    if (elements.progressInfo) {
        elements.progressInfo.textContent = stage.message;
    }
    
    document.querySelectorAll('.stage').forEach(el => {
        const elStage = el.dataset.stage;
        el.classList.remove('active');
        if (elStage === stageName) {
            el.classList.add('active');
        }
    });
    
    if (elements.progressFill && stageName === 'downloading') {
        if (state.format === 'video') {
            elements.progressFill.style.background = 'var(--gradient-video, linear-gradient(90deg, #3b82f6, #8b5cf6))';
        } else {
            elements.progressFill.style.background = 'var(--gradient-audio, linear-gradient(90deg, #ec4899, #8b5cf6))';
        }
    }
}

// ===== Complete Stage =====
function completeStage(stageName) {
    const stageEl = document.querySelector(`.stage[data-stage="${stageName}"]`);
    if (stageEl) {
        stageEl.classList.remove('active');
        stageEl.classList.add('completed');
    }
}

// ===== Update Stages Progress =====
function updateStagesProgress(percent) {
    const stages = ['starting', 'downloading', 'processing', 'embedding', 'complete'];
    const progressPerStage = 100 / (stages.length - 1);
    
    stages.forEach((stage, index) => {
        const stageEl = document.querySelector(`.stage[data-stage="${stage}"]`);
        if (!stageEl) return;
        
        const stageThreshold = index * progressPerStage;
        
        if (percent > stageThreshold + progressPerStage) {
            stageEl.classList.add('completed');
            stageEl.classList.remove('active');
        } else if (percent >= stageThreshold && percent <= stageThreshold + progressPerStage) {
            stageEl.classList.add('active');
            stageEl.classList.remove('completed');
        } else {
            stageEl.classList.remove('active', 'completed');
        }
    });
}

// ===== Progress Tracking =====
function startProgressTracking() {
    console.log('🚀 Starting progress tracking for task:', state.taskId);
    
    if (state.progressInterval) {
        clearInterval(state.progressInterval);
    }
    
    let consecutiveErrors = 0;
    const maxConsecutiveErrors = 5;
    
    state.progressInterval = setInterval(async () => {
        if (state.cancelRequested) {
            clearInterval(state.progressInterval);
            state.progressInterval = null;
            showError('Download cancelled by user');
            return;
        }

        try {
            const response = await fetch(`${API_BASE}/api/progress/${state.taskId}`);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            
            const data = await response.json();
            consecutiveErrors = 0;
            updateProgress(data);

            if (data.status === 'completed') {
                clearInterval(state.progressInterval);
                state.progressInterval = null;
                
                state.title = data.title || state.title || 'download';
                state.hasThumbnail = data.has_thumbnail || false;
                state.fileSize = data.file_size || 0;
                
                if (state.lastPercent < 100) {
                    animateProgressTo100();
                } else {
                    completeConversion();
                }
            } else if (data.status === 'error') {
                clearInterval(state.progressInterval);
                state.progressInterval = null;
                state.isConverting = false;
                if (elements.cancelBtn) {
                    elements.cancelBtn.classList.remove('show');
                }
                showError(data.error || data.message || 'Conversion failed');
            }
        } catch (error) {
            console.error('❌ Progress error:', error);
            consecutiveErrors++;
            
            if (consecutiveErrors >= maxConsecutiveErrors) {
                clearInterval(state.progressInterval);
                state.progressInterval = null;
                state.isConverting = false;
                showError('Lost connection to server. Please try again.');
            }
        }
    }, 3000);
}

function updateProgress(data) {
    const percent = Math.min(Math.max(Math.round(data.percent || 0), 0), 100);
    
    if (percent < state.lastPercent) {
        return;
    }
    
    if (state.lastPercent === 0 && percent === 100) {
        animateProgressTo100();
        return;
    }
    
    state.lastPercent = percent;

    if (elements.progressFill) {
        elements.progressFill.style.width = `${percent}%`;
    }
    if (elements.progressPercent) {
        elements.progressPercent.textContent = `${percent}%`;
    }
    if (elements.progressPercentDetail) {
        elements.progressPercentDetail.textContent = `${percent}%`;
    }
    
    const status = (data.status || 'starting').toLowerCase();
    
    if (status === 'initializing') {
        activateStage('initializing');
    } else if (status === 'connecting') {
        if (state.currentStage === 'initializing') {
            completeStage('initializing');
        }
        activateStage('connecting');
    } else if (status === 'starting') {
        if (['initializing', 'connecting'].includes(state.currentStage)) {
            completeStage(state.currentStage);
        }
        activateStage('starting');
    } else if (status === 'downloading') {
        if (state.currentStage !== 'downloading') {
            completeStage(state.currentStage);
            activateStage('downloading');
        }
    } else if (status === 'processing' || status === 'converting') {
        if (state.currentStage === 'downloading') {
            completeStage('downloading');
        }
        activateStage('processing');
    } else if (status === 'embedding') {
        if (['processing', 'converting'].includes(state.currentStage)) {
            completeStage(state.currentStage);
        }
        activateStage('embedding');
    } else if (status === 'retrying') {
        activateStage('retrying');
        if (elements.stageProgress && data.message) {
            elements.stageProgress.textContent = data.message;
        }
    }
    
    if (data.message && elements.progressInfo) {
        elements.progressInfo.textContent = data.message;
    }
    
    if (data.speed && elements.downloadSpeed) {
        const speed = data.speed >= 1048576 ? 
            `${(data.speed / 1048576).toFixed(2)} MB/s` : 
            `${(data.speed / 1024).toFixed(0)} KB/s`;
        elements.downloadSpeed.textContent = speed;
    }
    
    if (data.eta && elements.eta) {
        const eta = data.eta < 60 ? 
            `${Math.round(data.eta)}s` : 
            `${Math.floor(data.eta / 60)}m ${Math.round(data.eta % 60)}s`;
        elements.eta.textContent = eta;
    }
    
    if (data.downloaded_bytes && elements.downloadedSize) {
        elements.downloadedSize.textContent = formatBytes(data.downloaded_bytes);
    }
    if (data.total_bytes && elements.totalSize) {
        elements.totalSize.textContent = formatBytes(data.total_bytes);
    }
    
    if (elements.stageProgress) {
        if (status === 'downloading' && percent > 0) {
            elements.stageProgress.textContent = `${percent}% complete`;
        } else if (status === 'processing' || status === 'converting') {
            elements.stageProgress.textContent = 'Optimizing file...';
        } else if (status === 'embedding') {
            elements.stageProgress.textContent = 'Adding metadata...';
        } else if (status === 'initializing') {
            elements.stageProgress.textContent = 'Preparing...';
        } else if (status === 'connecting') {
            elements.stageProgress.textContent = 'Connecting...';
        }
    }
    
    updateStagesProgress(percent);
}

// ===== Format Bytes =====
function formatBytes(bytes) {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// ===== Animate Progress to 100% =====
function animateProgressTo100() {
    const startPercent = state.lastPercent;
    const duration = 1500;
    const startTime = Date.now();
    
    const animate = () => {
        const elapsed = Date.now() - startTime;
        const progress = Math.min(elapsed / duration, 1);
        
        const easeOut = 1 - Math.pow(1 - progress, 3);
        const currentPercent = Math.round(startPercent + (100 - startPercent) * easeOut);
        
        if (elements.progressFill) {
            elements.progressFill.style.width = `${currentPercent}%`;
        }
        if (elements.progressPercent) {
            elements.progressPercent.textContent = `${currentPercent}%`;
        }
        if (elements.progressPercentDetail) {
            elements.progressPercentDetail.textContent = `${currentPercent}%`;
        }
        
        state.lastPercent = currentPercent;
        
        if (progress < 1) {
            requestAnimationFrame(animate);
        } else {
            completeConversion();
        }
    };
    
    requestAnimationFrame(animate);
}

// ===== Complete Conversion =====
function completeConversion() {
    completeStage(state.currentStage);
    activateStage('complete');
    
    if (elements.progressFill) elements.progressFill.style.width = '100%';
    if (elements.progressPercent) elements.progressPercent.textContent = '100%';
    if (elements.progressPercentDetail) elements.progressPercentDetail.textContent = '100%';
    if (elements.cancelBtn) elements.cancelBtn.classList.remove('show');
    
    state.isConverting = false;
    state.lastPercent = 100;
    
    showDownloadSection();
}

// ===== Hide Sections Helpers =====
function hideAllSectionsExceptPreview() {
    if (elements.progressSection) elements.progressSection.classList.remove('active');
    if (elements.downloadSection) elements.downloadSection.classList.remove('active');
    if (elements.errorSection) elements.errorSection.classList.remove('active');
    // Keep videoPreview visible
}

function hideAllSections() {
    if (elements.videoPreview) elements.videoPreview.classList.remove('active');
    if (elements.previewLoading) elements.previewLoading.style.display = 'none';
    if (elements.progressSection) elements.progressSection.classList.remove('active');
    if (elements.downloadSection) elements.downloadSection.classList.remove('active');
    if (elements.errorSection) elements.errorSection.classList.remove('active');
}

// Build an embeddable URL for preview (YouTube only for now)
function buildEmbedUrl(originalUrl) {
    try {
        const u = new URL(originalUrl);
        const host = u.hostname.replace('www.', '').replace('m.', '');
        
        // YouTube normal link
        if (host === 'youtube.com') {
            const params = u.searchParams;
            let videoId = params.get('v');

            // Shorts: /shorts/VIDEO_ID
            if (!videoId && u.pathname.includes('/shorts/')) {
                const m = u.pathname.match(/\/shorts\/([^/?]+)/);
                if (m) videoId = m[1];
            }

            if (videoId) {
                return `https://www.youtube.com/embed/${videoId}?autoplay=1`;
            }
        }

        // youtu.be short link
        if (host === 'youtu.be') {
            const videoId = u.pathname.replace('/', '');
            if (videoId) {
                return `https://www.youtube.com/embed/${videoId}?autoplay=1`;
            }
        }

        // For now we only handle YouTube; return null for others
        return null;
    } catch (e) {
        console.warn('buildEmbedUrl error:', e);
        return null;
    }
}

// ===== Handle Cancel =====
function handleCancel() {
    if (!elements.cancelBtn) return;
    
    state.cancelRequested = true;
    state.isConverting = false;
    
    elements.cancelBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i><span>Cancelling...</span>';
    elements.cancelBtn.disabled = true;
    
    showToast('🚫 Download cancelled', 'error', 3000);
    
    setTimeout(() => {
        if (state.progressInterval) {
            clearInterval(state.progressInterval);
            state.progressInterval = null;
        }
        
        if (elements.cancelBtn) {
            elements.cancelBtn.classList.remove('show');
            elements.cancelBtn.innerHTML = '<i class="fas fa-times"></i><span>Cancel Download</span>';
            elements.cancelBtn.disabled = false;
        }
        
        if (elements.progressSection) {
            elements.progressSection.classList.remove('active');
        }
        
        setLoading(false);
    }, 1000);
}

// ===== Show Download Section =====
function showDownloadSection() {
    setLoading(false);
   
    if (elements.progressSection) {
        elements.progressSection.classList.remove('active');
    }
    if (elements.downloadSection) {
        elements.downloadSection.classList.add('active');
    }
    
    const isVideo = state.format === 'video';
    
    const extMap = {
        mp3: 'mp3',
        aac: 'm4a',
        opus: 'opus',
        ogg: 'ogg'
    };
    
    const ext = isVideo ? 'mp4' : (extMap[state.audioFormat] || 'mp3');
    
    if (elements.formatBadge) {
        const formatLabel = isVideo 
            ? 'MP4 Video' 
            : (state.audioFormat === 'aac' ? 'AAC (M4A) Audio'
               : state.audioFormat === 'opus' ? 'OPUS Audio'
               : state.audioFormat === 'ogg' ? 'OGG Vorbis Audio'
               : 'MP3 Audio');
        const formatIcon = isVideo ? 'fa-video' : 'fa-music';
        elements.formatBadge.innerHTML = `<i class="fas ${formatIcon}"></i><span>${formatLabel}</span>`;
    }
    
    if (elements.downloadFileName) {
        elements.downloadFileName.textContent = `${state.title}.${ext}`;
    }
    
    if (elements.qualityTag) {
        if (isVideo) {
            elements.qualityTag.textContent = state.quality;
        } else {
            const qualityMap = {
                mp3: '320kbps',
                aac: '256kbps',
                opus: '192kbps',
                ogg: '192kbps'
            };
            elements.qualityTag.textContent = qualityMap[state.audioFormat] || '320kbps';
        }
    }
    
    if (elements.thumbnailTag) {
        elements.thumbnailTag.style.display = !isVideo && state.hasThumbnail ? 'inline-flex' : 'none';
    }
    if (elements.metadataTag) {
        elements.metadataTag.style.display = isVideo ? 'none' : 'inline-flex';
    }
    
    if (elements.fileSizeValue && state.fileSize) {
        const sizeText = state.fileSize >= 1048576 ? 
            `${(state.fileSize / 1048576).toFixed(2)} MB` : 
            `${(state.fileSize / 1024).toFixed(0)} KB`;
        elements.fileSizeValue.textContent = sizeText;
    }
    
    if (elements.downloadBtnText) {
        elements.downloadBtnText.textContent = `Download ${ext.toUpperCase()}`;
    }
    
    showToast(`🎉 ${isVideo ? 'Video' : 'Audio'} ready!`, 'success', 3000);
}

// ===== Handle Download =====
function handleDownload() {
    if (!state.taskId) {
        showToast('❌ No file to download', 'error', 2000);
        return;
    }
    
    const extMap = {
        mp3: 'mp3',
        aac: 'm4a',
        opus: 'opus',
        ogg: 'ogg'
    };
    
    const ext = state.format === 'video' ? 'mp4' : (extMap[state.audioFormat] || 'mp3');
    const url = `${API_BASE}/api/download/${state.taskId}?title=${encodeURIComponent(state.title)}`;
    
    const link = document.createElement('a');
    link.href = url;
    link.download = `${state.title}.${ext}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    
    showToast('📥 Download started!', 'success', 2000);
}

// ===== Show Error =====
function showError(message) {
    setLoading(false);
    
    if (state.progressInterval) {
        clearInterval(state.progressInterval);
        state.progressInterval = null;
    }
    
    state.cancelRequested = false;
    state.isConverting = false;
    
    if (elements.cancelBtn) {
        elements.cancelBtn.classList.remove('show');
        elements.cancelBtn.innerHTML = '<i class="fas fa-times"></i><span>Cancel Download</span>';
        elements.cancelBtn.disabled = false;
    }
    
    hideAllSectionsExceptPreview();
    
    if (elements.errorMessage) {
        elements.errorMessage.textContent = message;
    }
    if (elements.errorSection) {
        elements.errorSection.classList.add('active');
    }
    
    activateStage('error');
    if (elements.progressInfo) {
        elements.progressInfo.textContent = message;
    }
}

// ===== Reset Converter =====
function resetConverter() {
    if (state.progressInterval) {
        clearInterval(state.progressInterval);
        state.progressInterval = null;
    }
    
    state.taskId = null;
    state.title = '';
    state.cancelRequested = false;
    state.currentStage = 'starting';
    state.lastPercent = 0;
    state.isConverting = false;
    state.downloadedBytes = 0;
    state.totalBytes = 0;
    
    if (elements.videoUrl) elements.videoUrl.value = '';
    
    if (elements.progressFill) elements.progressFill.style.width = '0%';
    if (elements.progressPercent) elements.progressPercent.textContent = '0%';
    if (elements.progressPercentDetail) elements.progressPercentDetail.textContent = '0%';
    if (elements.downloadSpeed) elements.downloadSpeed.textContent = '--';
    if (elements.eta) elements.eta.textContent = '--';
    if (elements.downloadedSize) elements.downloadedSize.textContent = '0 MB';
    if (elements.totalSize) elements.totalSize.textContent = '--';
    if (elements.progressStage) elements.progressStage.textContent = '--';
    if (elements.progressInfo) elements.progressInfo.textContent = '';
    if (elements.stageProgress) elements.stageProgress.textContent = '';
    if (elements.cancelBtn) elements.cancelBtn.classList.remove('show');
    
    document.querySelectorAll('.stage').forEach(stage => {
        stage.classList.remove('active', 'completed');
    });
    
    hideAllSections();
    
    if (elements.videoUrl) {
        elements.videoUrl.focus();
    }
}

// ===== Set Loading State =====
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

// ===== Toast Notification =====
function showToast(message, type = 'info', duration = 3000) {
    if (!elements.toast || !elements.toastMessage || !elements.toastIcon) return;
    
    elements.toastMessage.textContent = message;
    elements.toast.className = `toast ${type}`;
    
    const icons = {
        info: 'fa-info-circle',
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle'
    };
    elements.toastIcon.className = `fas ${icons[type] || icons.info}`;
    
    elements.toast.classList.add('show');
    setTimeout(() => {
        if (elements.toast) {
            elements.toast.classList.remove('show');
        }
    }, duration);
}

// ===== Smooth Scroll for Navigation =====
document.querySelectorAll('a[href^="#"]').forEach(anchor => {
    anchor.addEventListener('click', function(e) {
        const targetId = this.getAttribute('href');
        if (!targetId || targetId === '#') return;
        e.preventDefault();
        const target = document.querySelector(targetId);
        if (target) target.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
})

// ===== Keyboard Shortcuts =====
document.addEventListener('keydown', (e) => {
    // Escape key to cancel
    if (e.key === 'Escape' && state.isConverting) {
        handleCancel();
    }
    
    // Enter to convert when URL input is focused
    if (e.key === 'Enter' && document.activeElement === elements.videoUrl && !state.isConverting) {
        handleConvert();
    }
});

console.log('📦 MediaGrab JS loaded');