/**
 * TTS Studio v4.0 - Ultra High Speed & Drama Multi-Role Controller
 */

const state = {
    mode: 'standard',
    voices: [],
    selectedVoice: null,
    currentAudioUrl: null,
    currentBlob: null,
    isLoading: false,
    history: [],
    dramaSegments: [],
    filter: {
        category: 'vi',
        gender: 'all',
        search: ''
    }
};

const SAMPLE_TEXTS = {
    vi_greeting: "Xin chào bạn! Chào mừng bạn đến với ứng dụng chuyển đổi văn bản thành giọng nói AI chất lượng cao. Bạn có thể chọn nhiều giọng đọc khác nhau và điều chỉnh tốc độ tùy thích.",
    vi_story: "Ngày xửa ngày xưa, tại một ngôi làng yên bình dưới chân núi, có một dòng suối trong vắt chảy qua từng kẽ đá. Người dân nơi đây luôn sống chan hòa, cùng nhau chia sẻ niềm vui và tiếng cười mỗi sớm mai.",
    vi_news: "Bản tin công nghệ: Trí tuệ nhân tạo thế hệ mới đang mang lại những bước đột phá mạnh mẽ trong lĩnh vực xử lý ngôn ngữ tự nhiên và tổng hợp giọng nói chân thực như người thật.",
    en_intro: "Hello and welcome! This is a state-of-the-art Text to Speech synthesis system powered by neural artificial intelligence. Feel free to explore different accents and customize your playback.",
    en_tech: "Artificial intelligence and machine learning are revolutionizing modern audio synthesis, producing lifelike human speech with expressive tones, accurate intonation, and emotional depth."
};

const USER_STORY_SAMPLE = `**Ngày tôi thuê một anh chàng đẹp trai đến hôn mình ngay trước mặt người đàn ông mà ông ngoại ép tôi phải sống chung suốt ba tháng, tôi đã nghĩ kế hoạch của mình hoàn hảo.**

Cho đến khi người đàn ông kia bình tĩnh đặt ly cà phê xuống, nhìn chúng tôi ôm nhau giữa sảnh rạp chiếu phim rồi hỏi:
“Diễn xong chưa?”

Tôi cứng người.
Anh chàng tôi thuê cũng cứng người.
Đỗ Gia Huy chậm rãi đứng dậy, chỉnh lại cổ tay áo.
“Xong rồi thì tới lượt tôi.”

Tôi còn chưa hiểu “tới lượt” là thế nào, anh đã bước tới, kéo tôi ra khỏi vòng tay người kia.
Rồi trước ánh mắt chết lặng của toàn bộ nhân viên, anh cúi đầu sát tai tôi:
“Muốn chọc tôi tức thì ít nhất cũng nên thuê người đẹp trai hơn tôi một chút.”

Tôi: “…”
Mẹ nó.
Kế hoạch số ba.
Lại thất bại.

---

Ông ngoại tôi qua đời vào đầu mùa hè.
Ông để lại cho tôi thứ tôi yêu nhất trên đời: Rạp chiếu phim Ánh Trăng.
Và kèm theo nó là thứ tôi ghét nhất trên đời: Đỗ Gia Huy.

Ngày luật sư đọc di chúc, tôi đang khóc đến sưng cả mắt.
Luật sư: “Rạp chiếu phim Ánh Trăng cùng toàn bộ quyền khai thác được giao cho cô Nguyễn Bảo An và anh Đỗ Gia Huy đồng quản lý trong thời gian thử thách chín mươi ngày.”
Tôi: “Không được! Rạp là của ông ngoại tôi!”
Đỗ Gia Huy: “Bây giờ một nửa quyền quản lý thuộc về tôi. Chín giờ sáng mai họp. Đừng đến muộn.”`;

let elements = {};
let progressTimer = null;
let noticeModalResolve = null;

const NOTICE_STYLES = {
    info: {
        icon: 'fa-circle-info',
        classes: ['bg-indigo-100', 'text-indigo-600', 'dark:bg-indigo-950', 'dark:text-indigo-300']
    },
    success: {
        icon: 'fa-circle-check',
        classes: ['bg-emerald-100', 'text-emerald-600', 'dark:bg-emerald-950', 'dark:text-emerald-300']
    },
    warning: {
        icon: 'fa-triangle-exclamation',
        classes: ['bg-amber-100', 'text-amber-600', 'dark:bg-amber-950', 'dark:text-amber-300']
    },
    error: {
        icon: 'fa-circle-exclamation',
        classes: ['bg-rose-100', 'text-rose-600', 'dark:bg-rose-950', 'dark:text-rose-300']
    }
};

console.info('TTS Studio frontend build: 20260820-3 (Blob audio pipeline)');
const NOTICE_STYLE_CLASSES = Object.values(NOTICE_STYLES).flatMap(style => style.classes);

document.addEventListener('DOMContentLoaded', () => {
    initElements();
    initTheme();
    initEventListeners();
    loadVoices();
    loadHistoryFromStorage();
});

function initElements() {
    elements = {
        themeToggle: document.getElementById('themeToggle'),
        statusIndicator: document.getElementById('statusIndicator'),
        statusText: document.getElementById('statusText'),
        
        modeStandardTab: document.getElementById('modeStandardTab'),
        modeDramaTab: document.getElementById('modeDramaTab'),
        standardModeView: document.getElementById('standardModeView'),
        dramaModeView: document.getElementById('dramaModeView'),
        
        textInput: document.getElementById('textInput'),
        charCount: document.getElementById('charCount'),
        wordCount: document.getElementById('wordCount'),
        estimatedTime: document.getElementById('estimatedTime'),
        clearTextBtn: document.getElementById('clearTextBtn'),
        pasteTextBtn: document.getElementById('pasteTextBtn'),
        sampleSelect: document.getElementById('sampleSelect'),
        rateSlider: document.getElementById('rateSlider'),
        rateVal: document.getElementById('rateVal'),
        pitchSlider: document.getElementById('pitchSlider'),
        pitchVal: document.getElementById('pitchVal'),
        volumeSlider: document.getElementById('volumeSlider'),
        volumeVal: document.getElementById('volumeVal'),
        standardEmotion: document.getElementById('standardEmotion'),
        standardEmotionIntensity: document.getElementById('standardEmotionIntensity'),
        standardEmotionIntensityValue: document.getElementById('standardEmotionIntensityValue'),
        resetSettingsBtn: document.getElementById('resetSettingsBtn'),
        generateBtn: document.getElementById('generateBtn'),
        generateBtnText: document.getElementById('generateBtnText'),
        generateSpinner: document.getElementById('generateSpinner'),
        
        voiceSearch: document.getElementById('voiceSearch'),
        genderFilter: document.getElementById('genderFilter'),
        categoryTabs: document.querySelectorAll('.category-tab'),
        voiceListContainer: document.getElementById('voiceListContainer'),
        voiceCountBadge: document.getElementById('voiceCountBadge'),
        selectedVoiceDisplay: document.getElementById('selectedVoiceDisplay'),
        
        loadStorySampleBtn: document.getElementById('loadStorySampleBtn'),
        dramaNarratorVoice: document.getElementById('dramaNarratorVoice'),
        dramaMaleVoice: document.getElementById('dramaMaleVoice'),
        dramaFemaleVoice: document.getElementById('dramaFemaleVoice'),
        dramaTextInput: document.getElementById('dramaTextInput'),
        dramaCharCount: document.getElementById('dramaCharCount'),
        dramaClearBtn: document.getElementById('dramaClearBtn'),
        dramaParseBtn: document.getElementById('dramaParseBtn'),
        dramaParseProgress: document.getElementById('dramaParseProgress'),
        dramaParseProgressText: document.getElementById('dramaParseProgressText'),
        dramaParseProgressPercent: document.getElementById('dramaParseProgressPercent'),
        dramaParseProgressFill: document.getElementById('dramaParseProgressFill'),
        dramaSegmentsCard: document.getElementById('dramaSegmentsCard'),
        dramaTotalSegmentsBadge: document.getElementById('dramaTotalSegmentsBadge'),
        dramaSegmentsList: document.getElementById('dramaSegmentsList'),
        dramaGenerateAllBtn: document.getElementById('dramaGenerateAllBtn'),
        dramaGenerateBtnText: document.getElementById('dramaGenerateBtnText'),
        dramaGenerateSpinner: document.getElementById('dramaGenerateSpinner'),
        dramaProgressContainer: document.getElementById('dramaProgressContainer'),
        dramaProgressText: document.getElementById('dramaProgressText'),
        dramaProgressPercent: document.getElementById('dramaProgressPercent'),
        dramaProgressBarFill: document.getElementById('dramaProgressBarFill'),
        
        playerCard: document.getElementById('playerCard'),
        audioPlayer: document.getElementById('audioPlayer'),
        playPauseBtn: document.getElementById('playPauseBtn'),
        playIcon: document.getElementById('playIcon'),
        pauseIcon: document.getElementById('pauseIcon'),
        progressBar: document.getElementById('progressBar'),
        currentTimeText: document.getElementById('currentTimeText'),
        totalDurationText: document.getElementById('totalDurationText'),
        downloadAudioBtn: document.getElementById('downloadAudioBtn'),
        playbackSpeedBtn: document.getElementById('playbackSpeedBtn'),
        waveformContainer: document.getElementById('waveformContainer'),
        playerVoiceName: document.getElementById('playerVoiceName'),
        playerTextSnippet: document.getElementById('playerTextSnippet'),

        noticeModal: document.getElementById('noticeModal'),
        noticeModalBackdrop: document.getElementById('noticeModalBackdrop'),
        noticeModalIcon: document.getElementById('noticeModalIcon'),
        noticeModalTitle: document.getElementById('noticeModalTitle'),
        noticeModalMessage: document.getElementById('noticeModalMessage'),
        noticeModalClose: document.getElementById('noticeModalClose'),
        noticeModalCancel: document.getElementById('noticeModalCancel'),
        noticeModalConfirm: document.getElementById('noticeModalConfirm'),
        
        historyContainer: document.getElementById('historyContainer'),
        clearHistoryBtn: document.getElementById('clearHistoryBtn'),
        historyEmptyNotice: document.getElementById('historyEmptyNotice')
    };
}

function initTheme() {
    const savedTheme = localStorage.getItem('tts_theme') || 
        (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    if (savedTheme === 'dark') {
        document.documentElement.classList.add('dark');
    } else {
        document.documentElement.classList.remove('dark');
    }
}

function initEventListeners() {
    elements.noticeModalClose?.addEventListener('click', () => closeNoticeModal(false));
    elements.noticeModalBackdrop?.addEventListener('click', () => closeNoticeModal(false));
    elements.noticeModalCancel?.addEventListener('click', () => closeNoticeModal(false));
    elements.noticeModalConfirm?.addEventListener('click', () => closeNoticeModal(true));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !elements.noticeModal?.classList.contains('hidden')) {
            closeNoticeModal(false);
        }
    });

    if (elements.themeToggle) {
        elements.themeToggle.addEventListener('click', () => {
            const isDark = document.documentElement.classList.toggle('dark');
            localStorage.setItem('tts_theme', isDark ? 'dark' : 'light');
        });
    }

    if (elements.modeStandardTab && elements.modeDramaTab) {
        elements.modeStandardTab.addEventListener('click', () => switchMode('standard'));
        elements.modeDramaTab.addEventListener('click', () => switchMode('drama'));
    }

    if (elements.textInput) {
        elements.textInput.addEventListener('input', updateTextCounters);
        updateTextCounters();
    }
    document.querySelectorAll('[data-cue]').forEach(button => button.addEventListener('click', () => {
        const input = elements.textInput;
        if (!input) return;
        const start = input.selectionStart, end = input.selectionEnd;
        input.setRangeText(`${button.dataset.cue} `, start, end, 'end');
        input.focus();
        input.dispatchEvent(new Event('input', {bubbles: true}));
    }));

    if (elements.clearTextBtn) {
        elements.clearTextBtn.addEventListener('click', () => {
            elements.textInput.value = '';
            updateTextCounters();
            elements.textInput.focus();
        });
    }

    if (elements.pasteTextBtn) {
        elements.pasteTextBtn.addEventListener('click', async () => {
            try {
                const text = await navigator.clipboard.readText();
                if (text) {
                    elements.textInput.value = text;
                    updateTextCounters();
                }
            } catch (err) {
                console.error('Clipboard error:', err);
            }
        });
    }

    if (elements.sampleSelect) {
        elements.sampleSelect.addEventListener('change', (e) => {
            const val = e.target.value;
            if (val && SAMPLE_TEXTS[val]) {
                elements.textInput.value = SAMPLE_TEXTS[val];
                updateTextCounters();
            }
        });
    }

    if (elements.rateSlider) {
        elements.rateSlider.addEventListener('input', (e) => {
            elements.rateVal.innerText = `${parseFloat(e.target.value).toFixed(2)}x`;
            updateTextCounters();
        });
    }
    if (elements.pitchSlider) {
        elements.pitchSlider.addEventListener('input', (e) => {
            const val = parseInt(e.target.value);
            elements.pitchVal.innerText = `${val > 0 ? '+' : ''}${val}Hz`;
        });
    }
    if (elements.volumeSlider) {
        elements.volumeSlider.addEventListener('input', (e) => {
            const val = parseInt(e.target.value);
            elements.volumeVal.innerText = `${val > 0 ? '+' : ''}${val}%`;
        });
    }
    if (elements.standardEmotionIntensity) {
        elements.standardEmotionIntensity.addEventListener('input', event => {
            elements.standardEmotionIntensityValue.innerText = `${event.target.value}%`;
        });
    }
    if (elements.standardEmotion) {
        elements.standardEmotion.addEventListener('change', event => {
            const automatic = event.target.value === 'auto';
            elements.standardEmotionIntensity.disabled = automatic;
            elements.standardEmotionIntensityValue.innerText = automatic ? 'AI quyết định' : `${elements.standardEmotionIntensity.value}%`;
        });
        elements.standardEmotion.dispatchEvent(new Event('change'));
    }
    if (elements.resetSettingsBtn) {
        elements.resetSettingsBtn.addEventListener('click', resetSettings);
    }

    if (elements.voiceSearch) {
        elements.voiceSearch.addEventListener('input', (e) => {
            state.filter.search = e.target.value.toLowerCase().trim();
            renderVoiceList();
        });
    }
    if (elements.genderFilter) {
        elements.genderFilter.addEventListener('change', (e) => {
            state.filter.gender = e.target.value;
            renderVoiceList();
        });
    }
    if (elements.categoryTabs) {
        elements.categoryTabs.forEach(tab => {
            tab.addEventListener('click', () => {
                elements.categoryTabs.forEach(t => {
                    t.classList.remove('bg-indigo-600', 'text-white', 'shadow-sm');
                    t.classList.add('text-slate-600', 'dark:text-slate-300', 'hover:bg-slate-100', 'dark:hover:bg-slate-800');
                });
                tab.classList.add('bg-indigo-600', 'text-white', 'shadow-sm');
                tab.classList.remove('text-slate-600', 'dark:text-slate-300', 'hover:bg-slate-100', 'dark:hover:bg-slate-800');
                state.filter.category = tab.dataset.category;
                renderVoiceList();
            });
        });
    }

    if (elements.generateBtn) {
        elements.generateBtn.addEventListener('click', handleGenerateStandardTTS);
    }

    if (elements.loadStorySampleBtn) {
        elements.loadStorySampleBtn.addEventListener('click', () => {
            elements.dramaTextInput.value = USER_STORY_SAMPLE;
            updateDramaCounters();
            handleParseDialogue();
        });
    }

    if (elements.dramaTextInput) {
        elements.dramaTextInput.addEventListener('input', updateDramaCounters);
    }

    if (elements.dramaClearBtn) {
        elements.dramaClearBtn.addEventListener('click', () => {
            elements.dramaTextInput.value = '';
            updateDramaCounters();
            state.dramaSegments = [];
            elements.dramaSegmentsCard.classList.add('hidden');
        });
    }

    if (elements.dramaParseBtn) {
        elements.dramaParseBtn.addEventListener('click', handleParseDialogue);
    }

    if (elements.dramaGenerateAllBtn) {
        elements.dramaGenerateAllBtn.addEventListener('click', handleGenerateDramaTTS);
    }

    if (elements.playPauseBtn) elements.playPauseBtn.addEventListener('click', toggleAudioPlay);
    if (elements.audioPlayer) {
        elements.audioPlayer.addEventListener('timeupdate', updateAudioProgress);
        elements.audioPlayer.addEventListener('ended', () => {
            if (elements.playIcon) elements.playIcon.classList.remove('hidden');
            if (elements.pauseIcon) elements.pauseIcon.classList.add('hidden');
            if (elements.waveformContainer) elements.waveformContainer.classList.remove('playing');
        });
    }
    if (elements.progressBar) {
        elements.progressBar.addEventListener('input', (e) => {
            if (elements.audioPlayer && elements.audioPlayer.duration) {
                elements.audioPlayer.currentTime = (e.target.value / 100) * elements.audioPlayer.duration;
            }
        });
    }

    if (elements.playbackSpeedBtn) {
        const speeds = [1.0, 1.25, 1.5, 2.0, 0.75];
        let currentSpeedIdx = 0;
        elements.playbackSpeedBtn.addEventListener('click', () => {
            currentSpeedIdx = (currentSpeedIdx + 1) % speeds.length;
            const s = speeds[currentSpeedIdx];
            if (elements.audioPlayer) elements.audioPlayer.playbackRate = s;
            elements.playbackSpeedBtn.innerText = `${s}x`;
        });
    }

    if (elements.clearHistoryBtn) {
        elements.clearHistoryBtn.addEventListener('click', async () => {
            const confirmed = await showConfirmModal('Xóa lịch sử?', 'Toàn bộ lịch sử tạo âm thanh trên thiết bị này sẽ bị xóa.');
            if (confirmed) {
                state.history = [];
                saveHistoryToStorage();
                renderHistory();
            }
        });
    }
}

function switchMode(mode) {
    state.mode = mode;
    if (mode === 'standard') {
        elements.modeStandardTab.className = "px-3 py-1.5 rounded-lg text-xs font-bold bg-white dark:bg-indigo-600 text-indigo-600 dark:text-white shadow-sm transition-all flex items-center space-x-1.5";
        elements.modeDramaTab.className = "px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all flex items-center space-x-1.5";
        elements.standardModeView.classList.remove('hidden');
        elements.dramaModeView.classList.add('hidden');
    } else {
        elements.modeDramaTab.className = "px-3 py-1.5 rounded-lg text-xs font-bold bg-white dark:bg-indigo-600 text-indigo-600 dark:text-white shadow-sm transition-all flex items-center space-x-1.5";
        elements.modeStandardTab.className = "px-3 py-1.5 rounded-lg text-xs font-semibold text-slate-600 dark:text-slate-300 hover:text-indigo-600 dark:hover:text-indigo-400 transition-all flex items-center space-x-1.5";
        elements.dramaModeView.classList.remove('hidden');
        elements.standardModeView.classList.add('hidden');
    }
}

function updateTextCounters() {
    if (!elements.textInput) return;
    const text = elements.textInput.value;
    const words = text.trim() ? text.trim().split(/\s+/).length : 0;
    elements.charCount.innerText = `${text.length.toLocaleString()} ký tự`;
    elements.wordCount.innerText = `${words.toLocaleString()} từ`;
    if (elements.estimatedTime) {
        elements.estimatedTime.innerText = formatConversionEstimate(text.length, words);
    }
}

function formatDuration(seconds) {
    if (!Number.isFinite(seconds) || seconds <= 0) return '0 phút';
    const minutes = Math.max(1, Math.round(seconds / 60));
    if (minutes < 60) return `${minutes} phút`;
    const hours = Math.floor(minutes / 60);
    const remaining = minutes % 60;
    return remaining ? `${hours} giờ ${remaining} phút` : `${hours} giờ`;
}

function formatConversionEstimate(charCount, wordCount, speedOverride = null) {
    if (!charCount || !wordCount) return 'Ước tính: --';
    const speed = Math.max(0.5, speedOverride ?? parseFloat(elements.rateSlider?.value || '1'));
    const chunkCount = Math.max(1, Math.ceil(charCount / 1400));
    const audioSeconds = (wordCount / (150 * speed)) * 60;
    const cpuChunkSeconds = chunkCount * 1.5;
    const synthesisSeconds = audioSeconds * 0.4;
    const expected = Math.max(cpuChunkSeconds, synthesisSeconds) + 30;
    return `VieNeu ước tính: ${formatDuration(expected * 0.8)}–${formatDuration(expected * 1.4)}`;
}

function updateDramaCounters() {
    if (!elements.dramaTextInput) return;
    const text = elements.dramaTextInput.value;
    const words = text.trim() ? text.trim().split(/\s+/).length : 0;
    elements.dramaCharCount.innerText = `${text.length.toLocaleString()} ký tự • ${words.toLocaleString()} từ • ${formatConversionEstimate(text.length, words, 1)}`;
}

function resetSettings() {
    if (elements.rateSlider) { elements.rateSlider.value = 1.0; elements.rateVal.innerText = '1.00x'; }
    if (elements.pitchSlider) { elements.pitchSlider.value = 0; elements.pitchVal.innerText = '0Hz'; }
    if (elements.volumeSlider) { elements.volumeSlider.value = 0; elements.volumeVal.innerText = '0%'; }
    if (elements.standardEmotion) elements.standardEmotion.value = 'auto';
    if (elements.standardEmotionIntensity) elements.standardEmotionIntensity.value = 60;
    if (elements.standardEmotionIntensityValue) elements.standardEmotionIntensityValue.innerText = 'AI quyết định';
    updateTextCounters();
}

async function loadVoices() {
    try {
        const res = await fetch('/api/voices');
        if (!res.ok) throw new Error('Không thể tải danh sách giọng đọc');
        const data = await res.json();
        state.voices = data.voices || [];
        
        elements.voiceCountBadge.innerText = `${state.voices.length} giọng`;
        elements.statusText.innerText = 'VieNeu v3 Turbo • Local';
        elements.statusIndicator.className = 'w-2 h-2 rounded-full bg-emerald-500 animate-pulse';
        
        populateDramaVoiceOptions();

        const defaultVoice = state.voices.find(v => v.id === 'vieneu-ngoc-linh') || state.voices[0];
        if (defaultVoice) selectVoice(defaultVoice);
        renderVoiceList();
    } catch (err) {
        console.error('Voices load error:', err);
        elements.statusText.innerText = 'Lỗi kết nối máy chủ';
        elements.statusIndicator.className = 'w-2 h-2 rounded-full bg-rose-500';
    }
}

function populateDramaVoiceOptions() {
    const vnVoices = state.voices.filter(v => v.isVietnamese);
    const otherVoices = state.voices.filter(v => !v.isVietnamese);

    const generateOptionsHtml = (selectedId) => {
        let html = '<optgroup label="🇻🇳 Tiếng Việt">';
        vnVoices.forEach(v => {
            html += `<option value="${v.id}" ${v.id === selectedId ? 'selected' : ''}>${v.flag} ${v.displayName}</option>`;
        });
        html += '</optgroup><optgroup label="🌐 Quốc tế (Chuẩn hóa)">';
        otherVoices.slice(0, 20).forEach(v => {
            html += `<option value="${v.id}" ${v.id === selectedId ? 'selected' : ''}>${v.flag} ${v.displayName} (${v.languageName})</option>`;
        });
        html += '</optgroup>';
        return html;
    };

    if (elements.dramaNarratorVoice) elements.dramaNarratorVoice.innerHTML = generateOptionsHtml('vieneu-ngoc-linh');
    if (elements.dramaMaleVoice) elements.dramaMaleVoice.innerHTML = generateOptionsHtml('vieneu-thanh-binh');
    if (elements.dramaFemaleVoice) elements.dramaFemaleVoice.innerHTML = generateOptionsHtml('vieneu-ngoc-linh');
}

function renderVoiceList() {
    if (!elements.voiceListContainer) return;
    const { category, gender, search } = state.filter;
    
    const filtered = state.voices.filter(v => {
        if (category === 'vi' && !v.isVietnamese) return false;
        if (category === 'en' && !v.locale.startsWith('en-')) return false;
        if (category === 'ja' && !v.locale.startsWith('ja-')) return false;
        if (category === 'ko' && !v.locale.startsWith('ko-')) return false;
        if (category === 'zh' && !v.locale.startsWith('zh-')) return false;
        if (category === 'other' && (v.isVietnamese || v.locale.startsWith('en-') || v.locale.startsWith('ja-') || v.locale.startsWith('ko-') || v.locale.startsWith('zh-'))) return false;
        
        if (gender !== 'all' && v.gender.toLowerCase() !== gender.toLowerCase()) return false;
        
        if (search) {
            const s = search.toLowerCase();
            if (!v.name.toLowerCase().includes(s) && !v.languageName.toLowerCase().includes(s) && !v.locale.toLowerCase().includes(s)) return false;
        }
        return true;
    });

    if (filtered.length === 0) {
        elements.voiceListContainer.innerHTML = '<div class="py-12 text-center text-slate-400 text-sm">Không tìm thấy giọng đọc phù hợp.</div>';
        return;
    }

    elements.voiceListContainer.innerHTML = filtered.map(voice => {
        const isSelected = state.selectedVoice && state.selectedVoice.id === voice.id;
        const isFemale = voice.gender.toLowerCase() === 'female';
        
        return `
            <div onclick='handleSelectVoiceById("${voice.id}")' 
                 class="voice-card p-3 rounded-xl border transition-all cursor-pointer ${
                     isSelected 
                     ? 'selected border-indigo-500 bg-indigo-50/70 dark:bg-indigo-950/40 dark:border-indigo-500 ring-2 ring-indigo-400/30' 
                     : 'border-slate-200 dark:border-slate-800 bg-white/70 dark:bg-slate-900/60 hover:border-slate-300 dark:hover:border-slate-700'
                 }">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-3 min-w-0">
                        <span class="text-2xl select-none">${voice.flag || '🌐'}</span>
                        <div class="min-w-0">
                            <div class="flex items-center space-x-2">
                                <span class="font-semibold text-sm text-slate-900 dark:text-white truncate">${voice.name}</span>
                                <span class="text-[11px] px-2 py-0.5 rounded-full font-medium ${isFemale ? 'bg-pink-100 text-pink-700 dark:bg-pink-950/60 dark:text-pink-300' : 'bg-blue-100 text-blue-700 dark:bg-blue-950/60 dark:text-blue-300'}">
                                    <i class="fa-solid ${isFemale ? 'fa-venus' : 'fa-mars'} mr-0.5"></i>
                                    ${isFemale ? 'Nữ' : 'Nam'}
                                </span>
                                ${voice.isVietnamese ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 dark:bg-emerald-950/60 text-emerald-700 dark:text-emerald-300 font-bold">VN</span>' : ''}
                            </div>
                            <p class="text-xs text-slate-500 dark:text-slate-400 truncate mt-0.5">
                                ${voice.languageName} (${voice.locale})
                            </p>
                        </div>
                    </div>
                    
                    <div class="flex items-center space-x-2">
                        <button onclick='event.stopPropagation(); previewVoiceSample("${voice.id}")' title="Nghe thử mẫu" class="p-1.5 rounded-lg text-slate-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:bg-slate-100 dark:hover:bg-slate-800">
                            <i class="fa-solid fa-volume-low text-xs"></i>
                        </button>
                        <div>
                            ${isSelected ? '<i class="fa-solid fa-circle-check text-indigo-600 dark:text-indigo-400 text-lg"></i>' : '<i class="fa-regular fa-circle text-slate-300 dark:text-slate-600"></i>'}
                        </div>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

window.handleSelectVoiceById = function(voiceId) {
    const v = state.voices.find(item => item.id === voiceId);
    if (v) selectVoice(v);
};

function selectVoice(voice) {
    state.selectedVoice = voice;
    const isFemale = voice.gender.toLowerCase() === 'female';

    if (elements.selectedVoiceDisplay) {
        elements.selectedVoiceDisplay.innerHTML = `
            <div class="flex items-center justify-between">
                <div class="flex items-center space-x-2.5">
                    <span class="text-2xl">${voice.flag || '🌐'}</span>
                    <div>
                        <div class="flex items-center space-x-2">
                            <span class="font-bold text-slate-900 dark:text-white text-sm">${voice.name}</span>
                            <span class="text-xs px-2 py-0.5 rounded-full font-medium ${isFemale ? 'bg-pink-100 text-pink-700 dark:bg-pink-900/50 dark:text-pink-300' : 'bg-blue-100 text-blue-700 dark:bg-blue-900/50 dark:text-blue-300'}">${isFemale ? 'Nữ' : 'Nam'}</span>
                            ${voice.isVietnamese ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-300 font-bold">Chuẩn AI</span>' : ''}
                        </div>
                        <p class="text-xs text-slate-500 dark:text-slate-400">${voice.languageName} • ${voice.id}</p>
                    </div>
                </div>
                <button onclick='previewVoiceSample("${voice.id}")' class="px-3 py-1.5 rounded-lg bg-indigo-50 dark:bg-indigo-950/60 hover:bg-indigo-100 dark:hover:bg-indigo-900 text-indigo-600 dark:text-indigo-300 text-xs font-semibold flex items-center space-x-1.5">
                    <i class="fa-solid fa-play text-[10px]"></i>
                    <span>Nghe thử</span>
                </button>
            </div>
        `;
    }
    renderVoiceList();
}

window.previewVoiceSample = async function(voiceId) {
    const voice = state.voices.find(v => v.id === voiceId);
    if (!voice) return;
    try {
        const payload = { text: voice.sampleText || `Hello from ${voice.name}`, voice: voice.id };
        const res = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(formatErrorDetail(errData));
        }
        await receiveStreamAndPlay(res, `${voice.name} (${voice.languageName})`, payload.text);
    } catch (err) {
        console.error('Voice preview error:', err);
        showNoticeModal('error', 'Không thể nghe thử giọng', err.message);
    }
};
// 1. HIGH-SPEED STANDARD TTS SUBMIT  (True Streaming — audio plays in ~2s)
async function generateAutoExpressiveStandard(text) {
    const narratorVoice = state.selectedVoice.id;
    const maleVoice = state.voices.find(voice => voice.gender === 'Male')?.id || 'vieneu-thanh-binh';
    const femaleVoice = state.voices.find(voice => voice.gender === 'Female')?.id || 'vieneu-ngoc-linh';
    elements.generateBtnText.innerText = 'AI đang hiểu nội dung và tách từng đoạn…';
    let response = await fetch('/api/parse-dialogue/ai', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({text, narrator_voice: narratorVoice, male_voice: maleVoice, female_voice: femaleVoice})
    });
    if (!response.ok) throw new Error(formatErrorDetail(await response.json().catch(() => ({}))));
    const started = await response.json();
    let analysis;
    while (true) {
        await new Promise(resolve => setTimeout(resolve, 700));
        response = await fetch(`/api/parse-dialogue/ai/${started.job_id}`);
        if (!response.ok) throw new Error('Không đọc được tiến trình phân tích tự động');
        const job = await response.json();
        elements.generateBtnText.innerText = `${job.message || 'AI đang phân tích'} · ${job.progress || 0}%`;
        if (job.status === 'completed') { analysis = job.result; break; }
        if (job.status === 'failed') throw new Error(job.message || 'Phân tích AI thất bại');
    }
    const segments = analysis?.segments || [];
    if (!segments.length) throw new Error('Model không tạo được đoạn đọc nào');
    elements.generateBtnText.innerText = `Đang tổng hợp ${segments.length} đoạn biểu cảm…`;
    showPlayerLoadingState(`AI tự động · ${segments.length} đoạn`, text);
    response = await fetch('/api/tts/drama', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({segments})
    });
    if (!response.ok) throw new Error(formatErrorDetail(await response.json().catch(() => ({}))));
    await receiveStreamAndPlay(response, `AI biểu cảm · ${segments.length} đoạn`, text);
}

async function handleGenerateStandardTTS() {
    const text = normalizeStandardText(elements.textInput.value);
    if (!text) {
        showNoticeModal('warning', 'Chưa có nội dung', 'Vui lòng nhập văn bản cần đọc.');
        elements.textInput.focus();
        return;
    }
    if (!state.selectedVoice) {
        showNoticeModal('warning', 'Chưa chọn giọng đọc', 'Vui lòng chọn một giọng đọc trước khi tạo âm thanh.');
        return;
    }

    // Keep the editor and every downstream consumer on the exact normalized input.
    elements.textInput.value = text;
    updateTextCounters();

    setStandardLoading(true);

    try {
        const speedRatio = parseFloat(elements.rateSlider.value);
        const speedPercent = Math.round((speedRatio - 1.0) * 100);
        const rateStr = `${speedPercent >= 0 ? '+' : ''}${speedPercent}%`;

        const pitchVal = parseInt(elements.pitchSlider.value);
        const pitchStr = `${pitchVal >= 0 ? '+' : ''}${pitchVal}Hz`;

        const volVal = parseInt(elements.volumeSlider.value);
        const volStr = `${volVal >= 0 ? '+' : ''}${volVal}%`;

        if (elements.standardEmotion?.value === 'auto') {
            await generateAutoExpressiveStandard(text);
            return;
        }

        const payload = {
            text: text,
            voice: state.selectedVoice.id,
            rate: rateStr,
            pitch: pitchStr,
            volume: volStr,
            emotion: elements.standardEmotion?.value || 'neutral',
            emotion_intensity: parseInt(elements.standardEmotionIntensity?.value || '60', 10) / 100,
            auto_transliterate: true
        };

        // Show player UI immediately before network call completes
        showPlayerLoadingState(`${state.selectedVoice.name} (${state.selectedVoice.languageName})`, text);

        const res = await fetch('/api/tts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(formatErrorDetail(errData));
        }

        // Collect full stream then set src — provides seamless scrubbing
        await receiveStreamAndPlay(res, `${state.selectedVoice.name} (${state.selectedVoice.languageName})`, text);

        addToHistory({
            id: Date.now(),
            text: text,
            voiceName: state.selectedVoice.name,
            voiceId: state.selectedVoice.id,
            flag: state.selectedVoice.flag,
            gender: state.selectedVoice.gender,
            lang: state.selectedVoice.languageName,
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        });

    } catch (err) {
        console.error('TTS Generation Error:', err);
        showNoticeModal('error', 'Không thể tạo âm thanh', err.message);
    } finally {
        setStandardLoading(false);
    }
}

function setStandardLoading(loading) {
    state.isLoading = loading;
    if (loading) {
        elements.generateBtn.disabled = true;
        elements.generateBtn.classList.add('opacity-75', 'cursor-not-allowed');
        elements.generateSpinner.classList.remove('hidden');
        elements.generateBtnText.innerText = '⚡ VieNeu đang tổng hợp trên máy...';
    } else {
        elements.generateBtn.disabled = false;
        elements.generateBtn.classList.remove('opacity-75', 'cursor-not-allowed');
        elements.generateSpinner.classList.add('hidden');
        elements.generateBtnText.innerText = 'Đọc văn bản ngay';
    }
}

// 2. DRAMA / MULTI-ROLE LOGIC
async function handleParseDialogue() {
    const text = elements.dramaTextInput.value.trim();
    if (!text) {
        showNoticeModal('warning', 'Chưa có kịch bản', 'Vui lòng nhập đoạn truyện hoặc kịch bản cần phân vai.');
        elements.dramaTextInput.focus();
        return;
    }

    elements.dramaParseBtn.disabled = true;
    elements.dramaParseBtn.innerHTML = '<i class="fa-solid fa-spinner animate-spin mr-1.5"></i> Đang phân tích...';
    elements.dramaParseProgress.classList.add('hidden');
    elements.dramaParseProgressPercent.innerText = '0%';
    elements.dramaParseProgressFill.style.width = '0%';

    try {
        const mode = document.querySelector('input[name="dramaAnalysisMode"]:checked')?.value || 'fast';
        const payload = {
            text: text,
            narrator_voice: elements.dramaNarratorVoice.value,
            male_voice: elements.dramaMaleVoice.value,
            female_voice: elements.dramaFemaleVoice.value
        };

        let res = await fetch(mode === 'ai' ? '/api/parse-dialogue/ai' : '/api/parse-dialogue', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(formatErrorDetail(errData));
        }

        let data = await res.json();
        if (mode === 'ai') {
            elements.dramaParseProgress.classList.remove('hidden');
            const jobId = data.job_id;
            while (true) {
                await new Promise(resolve => setTimeout(resolve, 700));
                const statusRes = await fetch(`/api/parse-dialogue/ai/${jobId}`);
                if (!statusRes.ok) throw new Error('Không đọc được tiến trình phân tích AI');
                const job = await statusRes.json();
                const progress = Math.max(0, Math.min(100, job.progress || 0));
                elements.dramaParseProgressText.innerText = job.message || 'Đang phân tích...';
                elements.dramaParseProgressPercent.innerText = `${progress}%`;
                elements.dramaParseProgressFill.style.width = `${progress}%`;
                if (job.status === 'completed') {
                    data = job.result;
                    break;
                }
                if (job.status === 'failed') throw new Error(job.message || 'Phân tích AI thất bại');
            }
        }
        state.dramaSegments = data.segments || [];
        
        elements.dramaSegmentsCard.classList.remove('hidden');
        elements.dramaTotalSegmentsBadge.innerText = `${state.dramaSegments.length} đoạn thoại`;
        renderDramaSegments();

        if (data.fallback) {
            elements.dramaParseProgressText.innerText = 'AI không khả dụng — kết quả đang dùng chế độ Nhanh';
            console.warn('AI fallback:', data.fallback_reason);
        }

        elements.dramaSegmentsCard.scrollIntoView({ behavior: 'smooth', block: 'start' });

    } catch (err) {
        console.error('Parse Error:', err);
        showNoticeModal('error', 'Không thể phân tích kịch bản', err.message);
    } finally {
        elements.dramaParseBtn.disabled = false;
        elements.dramaParseBtn.innerHTML = '<i class="fa-solid fa-wand-magic-sparkles mr-1.5"></i> ⚡ Phân tích & Tách vai kịch bản';
    }
}

function renderDramaSegments() {
    if (!elements.dramaSegmentsList) return;

    if (state.dramaSegments.length === 0) {
        elements.dramaSegmentsList.innerHTML = '<p class="text-center py-6 text-slate-400 text-sm">Không có đoạn thoại nào.</p>';
        return;
    }

    elements.dramaSegmentsList.innerHTML = state.dramaSegments.map((seg, idx) => {
        const isMale = seg.role === 'male';
        const isFemale = seg.role === 'female';
        
        const cardBorder = isMale 
            ? 'border-blue-300 dark:border-blue-900 bg-blue-50/40 dark:bg-blue-950/20' 
            : isFemale 
            ? 'border-pink-300 dark:border-pink-900 bg-pink-50/40 dark:bg-pink-950/20' 
            : 'border-purple-200 dark:border-purple-900/60 bg-purple-50/30 dark:bg-purple-950/20';

        const roleBadge = isMale 
            ? '<span class="text-[11px] px-2.5 py-0.5 rounded-full font-bold bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200"><i class="fa-solid fa-mars mr-1"></i> Nhân vật Nam</span>'
            : isFemale 
            ? '<span class="text-[11px] px-2.5 py-0.5 rounded-full font-bold bg-pink-100 text-pink-700 dark:bg-pink-900 dark:text-pink-200"><i class="fa-solid fa-venus mr-1"></i> Nhân vật Nữ</span>'
            : '<span class="text-[11px] px-2.5 py-0.5 rounded-full font-bold bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-200"><i class="fa-solid fa-book-open mr-1"></i> Người dẫn chuyện</span>';

        const emotionLabels = { neutral: 'Tự nhiên', joyful: 'Vui vẻ', sad: 'Buồn', angry: 'Tức giận', fearful: 'Sợ hãi', surprised: 'Ngạc nhiên', tender: 'Dịu dàng', tense: 'Căng thẳng', whisper: 'Thì thầm' };
        const emotionOptions = Object.entries(emotionLabels).map(([value, label]) =>
            `<option value="${value}" ${seg.emotion === value ? 'selected' : ''}>${label}</option>`
        ).join('');

        return `
            <div class="p-4 rounded-xl border ${cardBorder} space-y-2.5 transition-all">
                <div class="flex flex-wrap items-center justify-between gap-2">
                    <div class="flex items-center space-x-2">
                        <span class="text-xs font-mono font-bold text-slate-400">#${idx + 1}</span>
                        ${roleBadge}
                        <span class="text-xs font-semibold text-slate-700 dark:text-slate-300">${escapeHtml(seg.speaker)}</span>
                        <span class="text-[11px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300">🎭 ${emotionLabels[seg.emotion] || 'Tự nhiên'}</span>
                    </div>

                    <div class="flex items-center space-x-2">
                        <select onchange="updateSegmentRole(${idx}, this.value)" class="text-xs p-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200">
                            <option value="narrator" ${seg.role === 'narrator' ? 'selected' : ''}>Dẫn chuyện</option>
                            <option value="male" ${seg.role === 'male' ? 'selected' : ''}>Nhân vật Nam</option>
                            <option value="female" ${seg.role === 'female' ? 'selected' : ''}>Nhân vật Nữ</option>
                        </select>

                        <select onchange="updateSegmentVoice(${idx}, this.value)" class="text-xs p-1.5 rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200">
                            ${state.voices.map(voice => `<option value="${voice.id}" ${seg.voice === voice.id ? 'selected' : ''}>${voice.displayName}</option>`).join('')}
                        </select>

                        <select title="Biểu cảm" onchange="updateSegmentEmotion(${idx}, this.value)" class="text-xs p-1.5 rounded-lg border border-amber-200 dark:border-amber-800 bg-white dark:bg-slate-900 text-slate-800 dark:text-slate-200">
                            ${emotionOptions}
                        </select>

                        <button onclick="deleteDramaSegment(${idx})" title="Xóa đoạn này" class="p-1.5 text-slate-400 hover:text-rose-500 rounded-lg hover:bg-slate-100 dark:hover:bg-slate-800">
                            <i class="fa-regular fa-trash-can text-xs"></i>
                        </button>
                    </div>
                </div>

                <p class="text-sm text-slate-800 dark:text-slate-100 bg-white/70 dark:bg-slate-900/60 p-2.5 rounded-lg border border-slate-200/50 dark:border-slate-800/50">
                    ${escapeHtml(seg.text)}
                </p>
                <p class="text-xs text-slate-500 dark:text-slate-400"><i class="fa-solid fa-masks-theater mr-1"></i>${escapeHtml(seg.delivery_hint || 'Đọc tự nhiên')} · nghỉ ${seg.pause_after_ms ?? 180}ms</p>
            </div>
        `;
    }).join('');
}

window.updateSegmentRole = function(idx, newRole) {
    if (!state.dramaSegments[idx]) return;
    state.dramaSegments[idx].role = newRole;
    if (newRole === 'male') {
        state.dramaSegments[idx].voice = elements.dramaMaleVoice.value;
    } else if (newRole === 'female') {
        state.dramaSegments[idx].voice = elements.dramaFemaleVoice.value;
    } else {
        state.dramaSegments[idx].voice = elements.dramaNarratorVoice.value;
    }
    renderDramaSegments();
};

window.updateSegmentVoice = function(idx, newVoice) {
    if (!state.dramaSegments[idx]) return;
    state.dramaSegments[idx].voice = newVoice;
};

window.updateSegmentEmotion = function(idx, emotion) {
    const seg = state.dramaSegments[idx];
    if (!seg) return;
    const presets = {
        neutral: [0, 0, 0, 180, 'Đọc tự nhiên'], joyful: [7, 7, 3, 130, 'Tươi vui, linh hoạt'],
        sad: [-10, -6, -4, 280, 'Chậm và trầm buồn'], angry: [6, 4, 8, 190, 'Dứt khoát, kìm nén'],
        fearful: [5, 8, -1, 220, 'Run nhẹ, bất an'], surprised: [4, 10, 3, 220, 'Bất ngờ, nhấn câu'],
        tender: [-7, 2, -3, 240, 'Nhẹ nhàng, ấm áp'], tense: [-3, 3, 1, 260, 'Căng và dè chừng'],
        whisper: [-8, -2, -12, 250, 'Hạ giọng thì thầm']
    };
    const [rate, pitch, volume, pause, hint] = presets[emotion] || presets.neutral;
    seg.emotion = emotion; seg.emotion_intensity = 0.7;
    seg.rate = `${rate >= 0 ? '+' : ''}${rate}%`;
    seg.pitch = `${pitch >= 0 ? '+' : ''}${pitch}Hz`;
    seg.volume = `${volume >= 0 ? '+' : ''}${volume}%`;
    seg.pause_after_ms = pause; seg.delivery_hint = hint;
    renderDramaSegments();
};

window.deleteDramaSegment = function(idx) {
    state.dramaSegments.splice(idx, 1);
    elements.dramaTotalSegmentsBadge.innerText = `${state.dramaSegments.length} đoạn thoại`;
    renderDramaSegments();
};

// 3. ULTRA HIGH-SPEED DRAMA TTS GENERATION — True Streaming
async function handleGenerateDramaTTS() {
    if (!state.dramaSegments || state.dramaSegments.length === 0) {
        showNoticeModal('warning', 'Chưa có đoạn kịch bản', 'Hãy phân tích hoặc thêm đoạn kịch bản trước khi tạo âm thanh.');
        return;
    }

    setDramaLoading(true);

    let progress = 5;
    elements.dramaProgressContainer.classList.remove('hidden');
    elements.dramaProgressText.innerText = `⚡ Đang xử lý song song ${state.dramaSegments.length} đoạn kịch bản...`;
    elements.dramaProgressPercent.innerText = `${progress}%`;
    elements.dramaProgressBarFill.style.width = `${progress}%`;

    clearInterval(progressTimer);
    progressTimer = setInterval(() => {
        if (progress < 85) {
            progress += 3;
            elements.dramaProgressPercent.innerText = `${progress}%`;
            elements.dramaProgressBarFill.style.width = `${progress}%`;
        }
    }, 500);

    try {
        const payload = { segments: state.dramaSegments };

        const res = await fetch('/api/tts/drama', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(formatErrorDetail(errData));
        }

        elements.dramaProgressPercent.innerText = '95%';
        elements.dramaProgressBarFill.style.width = '95%';
        elements.dramaProgressText.innerText = 'Đang ghép nối âm thanh và nạp vào trình phát...';

        const label = `🎭 Kịch bản phân vai (${state.dramaSegments.length} đoạn thoại)`;
        await receiveStreamAndPlay(res, label, `Kịch bản truyện phân vai đa nhân vật (${state.dramaSegments.length} phân đoạn)`);

        elements.dramaProgressPercent.innerText = '100%';
        elements.dramaProgressBarFill.style.width = '100%';
        elements.dramaProgressText.innerText = '✅ Hoàn tất! Đang phát âm thanh...';

        addToHistory({
            id: Date.now(),
            text: `[Phân vai truyện] ${state.dramaSegments[0].text.substring(0, 60)}... (${state.dramaSegments.length} phân đoạn)`,
            voiceName: 'Phân Vai (Đa Nhân Vật)',
            voiceId: 'drama-multi-role',
            flag: '🎭',
            gender: 'Đa Giọng',
            lang: 'Tiếng Việt',
            time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        });

    } catch (err) {
        clearInterval(progressTimer);
        console.error('Drama TTS Error:', err);
        showNoticeModal('error', 'Không thể tạo âm thanh', err.message);
    } finally {
        setDramaLoading(false);
    }
}

function setDramaLoading(loading) {
    state.isLoading = loading;
    if (loading) {
        elements.dramaGenerateAllBtn.disabled = true;
        elements.dramaGenerateAllBtn.classList.add('opacity-75', 'cursor-not-allowed');
        elements.dramaGenerateSpinner.classList.remove('hidden');
        elements.dramaGenerateBtnText.innerText = '⚡ VieNeu đang tổng hợp từng vai...';
    } else {
        elements.dramaGenerateAllBtn.disabled = false;
        elements.dramaGenerateAllBtn.classList.remove('opacity-75', 'cursor-not-allowed');
        elements.dramaGenerateSpinner.classList.add('hidden');
        elements.dramaGenerateBtnText.innerText = '🎧 Tạo & Ghép toàn bộ âm thanh';
    }
}

// ── Streaming Helpers ──────────────────────────────────────────────────────────

function formatErrorDetail(errData) {
    if (typeof errData.detail === 'string') return errData.detail;
    if (Array.isArray(errData.detail)) {
        return errData.detail.map(d => d.msg || JSON.stringify(d)).join('; ');
    }
    return 'Lỗi không xác định từ máy chủ';
}

function openNoticeModal(type, title, message, confirmMode = false) {
    const style = NOTICE_STYLES[type] || NOTICE_STYLES.info;
    elements.noticeModalIcon.classList.remove(...NOTICE_STYLE_CLASSES);
    elements.noticeModalIcon.classList.add(...style.classes);
    elements.noticeModalIcon.innerHTML = `<i class="fa-solid ${style.icon} text-xl"></i>`;
    elements.noticeModalTitle.innerText = title;
    elements.noticeModalMessage.innerText = String(message || '');
    elements.noticeModalCancel.classList.toggle('hidden', !confirmMode);
    elements.noticeModalConfirm.innerText = confirmMode ? 'Xác nhận' : 'Đã hiểu';
    elements.noticeModal.classList.remove('hidden');
    elements.noticeModal.classList.add('flex');
    document.body.classList.add('overflow-hidden');
    requestAnimationFrame(() => elements.noticeModalConfirm.focus());
}

function showNoticeModal(type, title, message) {
    if (noticeModalResolve) closeNoticeModal(false);
    openNoticeModal(type, title, message);
}

function showConfirmModal(title, message) {
    if (noticeModalResolve) closeNoticeModal(false);
    openNoticeModal('warning', title, message, true);
    return new Promise(resolve => {
        noticeModalResolve = resolve;
    });
}

function closeNoticeModal(confirmed) {
    if (!elements.noticeModal || elements.noticeModal.classList.contains('hidden')) return;
    elements.noticeModal.classList.add('hidden');
    elements.noticeModal.classList.remove('flex');
    document.body.classList.remove('overflow-hidden');
    const resolve = noticeModalResolve;
    noticeModalResolve = null;
    if (resolve) resolve(confirmed);
}

function normalizeStandardText(value) {
    return value
        .normalize('NFC')
        .replace(/[\u200B\uFEFF\u00AD]/g, '')
        .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, ' ')
        .replace(/```[\s\S]*?```/g, ' ')
        .replace(/(?:\*\*|__|~~|`)/g, '')
        .replace(/^\s{0,3}#{1,6}\s*/gm, '')
        .replace(/^\s*[-*_]{3,}\s*$/gm, '\n')
        .replace(/[ \t]+/g, ' ')
        .replace(/\n{3,}/g, '\n\n')
        .trim();
}

// Read the complete chunked MP3 response into a Blob to support long recordings.
async function receiveStreamAndPlay(response, title, textSnippet) {
    const jobId = response.headers.get('X-Audio-Job-Id');
    elements.downloadAudioBtn.disabled = true;
    elements.downloadAudioBtn.classList.add('opacity-50', 'cursor-not-allowed');
    elements.downloadAudioBtn.title = 'File MP3 đang được xử lý';
    elements.downloadAudioBtn.onclick = null;
    const reader = response.body.getReader();
    const chunks = [];

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (value && value.byteLength > 0) {
            chunks.push(value);
        }
    }

    const blob = new Blob(chunks, { type: 'audio/mpeg' });
    setupPlayerWithBlob(blob, title, textSnippet, jobId);
}

function enableCompletedDownload(jobId, fallbackUrl) {
    elements.downloadAudioBtn.disabled = false;
    elements.downloadAudioBtn.classList.remove('opacity-50', 'cursor-not-allowed');
    elements.downloadAudioBtn.title = 'Tải file MP3 hoàn chỉnh';
    elements.downloadAudioBtn.onclick = () => {
        const a = document.createElement('a');
        a.href = jobId ? `/api/download/${encodeURIComponent(jobId)}` : fallbackUrl;
        if (!jobId) a.download = `TTS_Audio_${Date.now()}.mp3`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    };
}

// Immediately show player card with loading state (before response arrives)
function showPlayerLoadingState(title, textSnippet) {
    elements.playerCard.classList.remove('hidden');
    elements.playerVoiceName.innerText = `⏳ ${title}`;
    elements.playerTextSnippet.innerText = `"${textSnippet.length > 100 ? textSnippet.substring(0, 100) + '...' : textSnippet}"`;
    elements.downloadAudioBtn.disabled = true;
    elements.downloadAudioBtn.classList.add('opacity-50', 'cursor-not-allowed');
    elements.downloadAudioBtn.title = 'File MP3 đang được xử lý';
    elements.downloadAudioBtn.onclick = null;
    elements.playerCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}


function setupPlayerWithBlob(audioBlob, title, textSnippet, jobId = null) {
    state.currentBlob = audioBlob;
    if (state.currentAudioUrl) URL.revokeObjectURL(state.currentAudioUrl);

    
    const audioUrl = URL.createObjectURL(audioBlob);
    state.currentAudioUrl = audioUrl;

    elements.playerCard.classList.remove('hidden');
    elements.playerCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    
    elements.audioPlayer.src = audioUrl;
    elements.playerVoiceName.innerText = title;
    elements.playerTextSnippet.innerText = `"${textSnippet.length > 100 ? textSnippet.substring(0, 100) + '...' : textSnippet}"`;
    
    enableCompletedDownload(jobId, audioUrl);

    elements.audioPlayer.play().catch(e => console.log('Autoplay prevented:', e));
    elements.playIcon.classList.add('hidden');
    elements.pauseIcon.classList.remove('hidden');
    elements.waveformContainer.classList.add('playing');
}

function toggleAudioPlay() {
    if (!elements.audioPlayer.src) return;
    if (elements.audioPlayer.paused) {
        elements.audioPlayer.play();
        elements.playIcon.classList.add('hidden');
        elements.pauseIcon.classList.remove('hidden');
        elements.waveformContainer.classList.add('playing');
    } else {
        elements.audioPlayer.pause();
        elements.playIcon.classList.remove('hidden');
        elements.pauseIcon.classList.add('hidden');
        elements.waveformContainer.classList.remove('playing');
    }
}

function updateAudioProgress() {
    if (!elements.audioPlayer) return;
    const current = elements.audioPlayer.currentTime;
    const duration = elements.audioPlayer.duration || 0;

    if (duration > 0 && elements.progressBar) {
        elements.progressBar.value = (current / duration) * 100;
        elements.currentTimeText.innerText = formatTime(current);
        elements.totalDurationText.innerText = formatTime(duration);
    }
}

function formatTime(seconds) {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs < 10 ? '0' : ''}${secs}`;
}

function addToHistory(item) {
    state.history.unshift(item);
    if (state.history.length > 20) state.history.pop();
    saveHistoryToStorage();
    renderHistory();
}

function saveHistoryToStorage() {
    try {
        localStorage.setItem('tts_history', JSON.stringify(state.history));
    } catch (e) {
        console.warn('History save error:', e);
    }
}

function loadHistoryFromStorage() {
    try {
        const saved = localStorage.getItem('tts_history');
        if (saved) {
            state.history = JSON.parse(saved);
            renderHistory();
        }
    } catch (e) {
        console.warn('History load error:', e);
    }
}

function renderHistory() {
    if (!elements.historyContainer || !elements.historyEmptyNotice) return;
    if (!state.history || state.history.length === 0) {
        elements.historyEmptyNotice.classList.remove('hidden');
        elements.historyContainer.innerHTML = '';
        if (elements.clearHistoryBtn) elements.clearHistoryBtn.classList.add('hidden');
        return;
    }

    elements.historyEmptyNotice.classList.add('hidden');
    if (elements.clearHistoryBtn) elements.clearHistoryBtn.classList.remove('hidden');

    elements.historyContainer.innerHTML = state.history.map(item => {
        return `
            <div class="p-3.5 rounded-xl border border-slate-200 dark:border-slate-800 bg-white/60 dark:bg-slate-900/50 hover:bg-slate-50 dark:hover:bg-slate-850 transition-all flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div class="flex-1 min-w-0">
                    <div class="flex items-center space-x-2 text-xs text-slate-500 dark:text-slate-400 mb-1">
                        <span>${item.flag || '🌐'} <strong>${item.voiceName}</strong></span>
                        <span>•</span>
                        <span>${item.lang}</span>
                        <span>•</span>
                        <span>${item.time}</span>
                    </div>
                    <p class="text-sm text-slate-800 dark:text-slate-200 line-clamp-2 select-text font-normal">
                        ${escapeHtml(item.text)}
                    </p>
                </div>
                <div class="flex items-center space-x-2 shrink-0 self-end sm:self-center">
                    <button onclick='copyHistoryText(${item.id})' title="Sao chép văn bản" class="p-1.5 rounded-lg text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors">
                        <i class="fa-regular fa-copy text-sm"></i>
                    </button>
                    <button onclick='deleteHistoryItem(${item.id})' title="Xóa" class="p-1.5 rounded-lg text-slate-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-slate-100 dark:hover:bg-slate-800 transition-colors">
                        <i class="fa-regular fa-trash-can text-sm"></i>
                    </button>
                </div>
            </div>
        `;
    }).join('');
}

window.copyHistoryText = function(id) {
    const item = state.history.find(h => h.id === id);
    if (!item) return;
    navigator.clipboard.writeText(item.text);
    showNoticeModal('success', 'Đã sao chép', 'Nội dung đã được sao chép vào bộ nhớ tạm.');
};

window.deleteHistoryItem = function(id) {
    state.history = state.history.filter(h => h.id !== id);
    saveHistoryToStorage();
    renderHistory();
};

function escapeHtml(str) {
    if (!str) return '';
    return str
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
