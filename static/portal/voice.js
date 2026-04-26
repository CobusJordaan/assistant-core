/**
 * Draadloze AI — Voice Mode
 *
 * State machine: idle → recording → processing → speaking → idle
 *
 * STT modes:
 *   - Browser Web Speech API (Chrome/Safari)
 *   - Whisper fallback via MediaRecorder upload (Firefox/other)
 */

(function () {
    'use strict';

    // --- State ---
    let voiceState = 'idle'; // idle | recording | processing | speaking
    let sttMode = null;       // 'browser' | 'whisper'
    let recognition = null;   // SpeechRecognition instance
    let mediaStream = null;   // getUserMedia stream
    let mediaRecorder = null; // MediaRecorder for Whisper mode
    let audioChunks = [];
    let audioContext = null;
    let analyser = null;
    let animFrame = null;
    let recordTimeout = null;
    let finalTranscript = '';
    let audioElement = null;
    let audioSourceNode = null;

    // --- Config (set by chat.html) ---
    const isVoiceAllowed = typeof voiceAllowed !== 'undefined' && voiceAllowed;
    const isVoiceEnabled = typeof voiceEnabled !== 'undefined' && voiceEnabled;
    const browserSttAllowed = typeof allowBrowserStt !== 'undefined' && allowBrowserStt;
    const whisperAllowed = typeof allowWhisperFallback !== 'undefined' && allowWhisperFallback;

    // --- DOM refs ---
    const overlay = document.getElementById('voice-overlay');
    const statusEl = document.getElementById('voice-status');
    const transcriptEl = document.getElementById('voice-transcript');
    const barsContainer = document.getElementById('voice-bars');
    const stopBtn = document.getElementById('voice-stop-btn');
    const micBtn = document.getElementById('btn-mic');

    // --- Init ---
    function init() {
        if (!micBtn) return;

        // Hide mic if voice not allowed or not enabled
        if (!isVoiceAllowed || !isVoiceEnabled) {
            micBtn.style.display = 'none';
            return;
        }

        // Detect STT mode
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (SpeechRecognition && browserSttAllowed) {
            sttMode = 'browser';
        } else if (whisperAllowed) {
            sttMode = 'whisper';
        } else {
            // No STT available — hide mic
            micBtn.style.display = 'none';
            return;
        }

        // Create waveform bars
        if (barsContainer) {
            for (let i = 0; i < 7; i++) {
                const bar = document.createElement('div');
                bar.className = 'voice-bar';
                barsContainer.appendChild(bar);
            }
        }
    }

    // --- Toggle voice mode ---
    window.toggleVoice = function () {
        if (voiceState === 'idle') {
            startVoice();
        } else if (voiceState === 'recording') {
            stopVoice();
        }
    };

    // --- Start voice ---
    async function startVoice() {
        if (voiceState !== 'idle') return;

        if (!isVoiceEnabled) {
            showChatAlert('Voice is not available yet.');
            return;
        }

        if (!sttMode) {
            showChatAlert('Voice is not supported on this browser yet.');
            return;
        }

        // Request microphone
        try {
            mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        } catch (e) {
            showChatAlert('Microphone access is required for voice mode.');
            return;
        }

        voiceState = 'recording';
        finalTranscript = '';

        // Show overlay
        overlay.classList.add('visible');
        overlay.classList.remove('speaking');
        stopBtn.classList.add('recording');
        micBtn.classList.add('recording');

        // Set up audio analyser for waveform
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        const source = audioContext.createMediaStreamSource(mediaStream);
        analyser = audioContext.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        animateWaveform();

        // Max recording timeout
        const maxSeconds = 60;
        recordTimeout = setTimeout(() => {
            if (voiceState === 'recording') stopVoice();
        }, maxSeconds * 1000);

        if (sttMode === 'browser') {
            startBrowserSTT();
        } else {
            startWhisperRecording();
        }
    }

    // --- Browser Speech API ---
    let silenceTimer = null;
    const SILENCE_TIMEOUT = 3000; // ms of silence before auto-stop

    function startBrowserSTT() {
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        recognition = new SpeechRecognition();
        const langMode = getLanguageMode();
        recognition.lang = langMode === 'af' ? 'af-ZA' : langMode === 'en' ? 'en-US' : 'en-ZA';
        recognition.interimResults = true;
        recognition.continuous = false; // non-continuous — stops faster on silence
        recognition.maxAlternatives = 1;

        statusEl.textContent = 'Listening (Browser)...';
        transcriptEl.textContent = '';

        recognition.onresult = function (e) {
            let interim = '';
            finalTranscript = '';
            for (let i = 0; i < e.results.length; i++) {
                const result = e.results[i];
                if (result.isFinal) {
                    finalTranscript += result[0].transcript;
                } else {
                    interim += result[0].transcript;
                }
            }
            transcriptEl.textContent = finalTranscript + interim;

            // Reset silence timer on each result
            clearTimeout(silenceTimer);
            if (finalTranscript) {
                // Got final text — auto-submit after brief pause
                silenceTimer = setTimeout(function () {
                    if (voiceState === 'recording' && recognition) {
                        recognition.stop();
                    }
                }, SILENCE_TIMEOUT);
            }
        };

        recognition.onerror = function (e) {
            console.error('Speech recognition error:', e.error);
            clearTimeout(silenceTimer);
            if (e.error === 'not-allowed') {
                cleanupVoice();
                showChatAlert('Microphone access is required for voice mode.');
            }
        };

        recognition.onend = function () {
            clearTimeout(silenceTimer);
            // Recognition ended (auto-stop on silence or manual stop)
            if (voiceState === 'recording') {
                processTranscript(finalTranscript);
            }
        };

        recognition.start();
    }

    // --- Whisper recording via MediaRecorder ---
    function startWhisperRecording() {
        statusEl.textContent = 'Listening (Whisper)...';
        transcriptEl.textContent = '';
        audioChunks = [];

        mediaRecorder = new MediaRecorder(mediaStream, {
            mimeType: MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
                ? 'audio/webm;codecs=opus'
                : 'audio/webm',
        });

        mediaRecorder.ondataavailable = function (e) {
            if (e.data.size > 0) audioChunks.push(e.data);
        };

        mediaRecorder.onstop = function () {
            if (voiceState === 'processing' || voiceState === 'idle') return;
            const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType });
            transcribeWithWhisper(blob);
        };

        mediaRecorder.start();
    }

    // --- Stop voice ---
    window.stopVoice = function () {
        if (voiceState !== 'recording') return;

        clearTimeout(recordTimeout);

        if (sttMode === 'browser' && recognition) {
            recognition.stop();
            // onend handler will call processTranscript
        } else if (sttMode === 'whisper' && mediaRecorder && mediaRecorder.state === 'recording') {
            voiceState = 'processing';
            statusEl.textContent = 'Transcribing...';
            stopBtn.classList.remove('recording');
            mediaRecorder.stop();
        }
    };

    // --- Whisper upload ---
    async function transcribeWithWhisper(blob) {
        voiceState = 'processing';
        statusEl.textContent = 'Transcribing...';
        stopBtn.classList.remove('recording');

        try {
            const fd = new FormData();
            fd.append('file', blob, 'audio.webm');

            const resp = await fetch('/portal/api/voice/transcribe', { method: 'POST', body: fd });
            const data = await resp.json();

            if (!resp.ok) {
                throw new Error(data.error || 'Transcription failed');
            }

            const text = (data.text || '').trim();
            if (!text) {
                statusEl.textContent = 'No speech detected. Try again.';
                setTimeout(cleanupVoice, 2000);
                return;
            }

            transcriptEl.textContent = text;
            processTranscript(text);
        } catch (e) {
            console.error('Whisper error:', e);
            statusEl.textContent = 'Could not transcribe audio. Please try again.';
            setTimeout(cleanupVoice, 2500);
        }
    }

    // --- Process final transcript ---
    async function processTranscript(text) {
        text = (text || '').trim();
        if (!text) {
            statusEl.textContent = 'No speech detected. Try again.';
            setTimeout(cleanupVoice, 2000);
            return;
        }

        voiceState = 'processing';
        statusEl.textContent = 'Processing...';
        stopBtn.classList.remove('recording');
        transcriptEl.textContent = text;

        // Stop mic stream
        stopMicStream();

        try {
            const resp = await fetch('/portal/api/voice', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    transcript: text,
                    conversation_id: activeConvId || null,
                    language: getLanguageMode(),
                }),
            });

            const data = await resp.json();

            if (!resp.ok) {
                throw new Error(data.error || 'Voice request failed');
            }

            // Update conversation ID
            if (data.conversation_id) {
                if (!activeConvId) {
                    const emptyEl = document.getElementById('chat-empty');
                    if (emptyEl) emptyEl.remove();
                }
                activeConvId = data.conversation_id;
                if (typeof currentConvId !== 'undefined') currentConvId = data.conversation_id;
                window.history.replaceState({}, '', '/portal/c/' + activeConvId);
                if (typeof updateSidebar === 'function') updateSidebar();
            }

            // Add messages to chat
            appendMessage('user', text);
            appendMessage('assistant', data.reply_text);

            // Play audio (errors here should not show "something went wrong")
            try {
                if (data.audio_url) {
                    playAudio(data.audio_url);
                } else {
                    cleanupVoice();
                }
            } catch (audioErr) {
                console.error('Audio playback error:', audioErr);
                cleanupVoice();
            }
        } catch (e) {
            console.error('Voice API error:', e);
            statusEl.textContent = 'Something went wrong. Please try again.';
            setTimeout(cleanupVoice, 2500);
        }
    }

    // --- Play TTS audio ---
    function playAudio(url) {
        voiceState = 'speaking';
        statusEl.textContent = 'Speaking...';
        overlay.classList.add('speaking');

        // Use a plain Audio element — no AudioContext routing
        // This avoids issues where AudioContext captures audio output
        // and then fails to route it to speakers
        if (!audioElement) {
            audioElement = new Audio();
        }

        audioElement.src = url;

        // Simple bar animation during playback (no frequency data needed)
        animateSpeakingBars();

        audioElement.onended = function () {
            cleanupVoice();
        };

        audioElement.onerror = function () {
            console.error('Audio playback error');
            cleanupVoice();
        };

        audioElement.play().catch(function (e) {
            console.error('Audio play failed:', e);
            cleanupVoice();
        });
    }

    // --- Simple speaking animation (no AudioContext needed) ---
    function animateSpeakingBars() {
        if (!barsContainer) return;
        const bars = barsContainer.children;

        function draw() {
            if (voiceState !== 'speaking') return;
            for (let i = 0; i < bars.length; i++) {
                const height = 10 + Math.random() * 50;
                bars[i].style.height = height + 'px';
            }
            animFrame = requestAnimationFrame(draw);
        }
        draw();
    }

    // --- Waveform animation ---
    function animateWaveform() {
        if (!analyser || !barsContainer) return;

        const bars = barsContainer.children;
        const dataArray = new Uint8Array(analyser.frequencyBinCount);

        function draw() {
            if (voiceState === 'idle') return;

            analyser.getByteFrequencyData(dataArray);

            // Pick 7 frequency bands
            const step = Math.floor(dataArray.length / 7);
            for (let i = 0; i < bars.length; i++) {
                const value = dataArray[i * step] || 0;
                const height = Math.max(4, (value / 255) * 70);
                bars[i].style.height = height + 'px';
            }

            animFrame = requestAnimationFrame(draw);
        }

        draw();
    }

    // --- Cleanup ---
    function cleanupVoice() {
        voiceState = 'idle';

        // Stop recognition
        if (recognition) {
            try { recognition.abort(); } catch (e) {}
            recognition = null;
        }

        // Stop media recorder
        if (mediaRecorder && mediaRecorder.state === 'recording') {
            try { mediaRecorder.stop(); } catch (e) {}
        }
        mediaRecorder = null;
        audioChunks = [];

        // Stop mic
        stopMicStream();

        // Stop animation
        if (animFrame) {
            cancelAnimationFrame(animFrame);
            animFrame = null;
        }

        // Reset bars
        if (barsContainer) {
            for (const bar of barsContainer.children) {
                bar.style.height = '4px';
            }
        }

        // Close audio context
        if (audioContext && audioContext.state !== 'closed') {
            // Don't close if we have an audioSourceNode (can't reconnect)
            // audioContext.close();
        }

        // Clear timeout
        clearTimeout(recordTimeout);

        // Hide overlay
        overlay.classList.remove('visible', 'speaking');
        stopBtn.classList.remove('recording');
        if (micBtn) micBtn.classList.remove('recording');

        // Reset text
        if (statusEl) statusEl.textContent = '';
        if (transcriptEl) transcriptEl.textContent = '';
    }

    window.closeVoice = cleanupVoice;

    function stopMicStream() {
        if (mediaStream) {
            mediaStream.getTracks().forEach(function (t) { t.stop(); });
            mediaStream = null;
        }
    }

    // --- Chat alert helper (uses existing showChatAlert if available) ---
    function showChatAlert(msg) {
        if (typeof window.showChatAlert === 'function') {
            window.showChatAlert(msg);
        } else {
            alert(msg);
        }
    }

    // --- Init on DOM ready ---
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
