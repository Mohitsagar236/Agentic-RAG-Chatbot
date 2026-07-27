import { sendQuestion, setComposerValue } from './chat.js';
import { stripMarkdownForSpeech } from './markdown.js';
import { setAppInert, showToast, trapFocus } from './ui.js';

const LANGUAGE_LOCALES = {
  en: 'en-US',
  hi: 'hi-IN',
  es: 'es-ES',
  fr: 'fr-FR',
  de: 'de-DE',
  zh: 'zh-CN',
  ar: 'ar-SA',
  pt: 'pt-BR',
  ja: 'ja-JP',
  ko: 'ko-KR',
  ru: 'ru-RU',
  it: 'it-IT',
  tr: 'tr-TR',
};

let recognition = null;
let isRecording = false;
let finalTranscript = '';
let speechUtterance = null;

let voiceModeActive = false;
let voiceModeState = 'idle';
let voiceModeRecognition = null;
let voiceModeFinalText = '';
let voiceModeSilenceTimer = 0;
let voiceModeReturnFocus = null;

function speechRecognitionClass() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null;
}

function selectedLocale() {
  const value = document.getElementById('voice-lang')?.value || 'auto';
  return value === 'auto' ? (navigator.language || 'en-US') : (LANGUAGE_LOCALES[value] || value);
}

function setRecordingUi(active) {
  const micButton = document.getElementById('mic-btn');
  const status = document.getElementById('voice-status');
  const label = document.getElementById('voice-label');
  micButton.classList.toggle('recording', active);
  micButton.setAttribute('aria-pressed', String(active));
  micButton.setAttribute('aria-label', active ? 'Stop browser voice input' : 'Start browser voice input');
  status.style.display = active ? 'flex' : 'none';
  if (active) label.textContent = 'Listening…';
}

function startRecording() {
  if (isRecording) return;
  if (document.getElementById('question-input').disabled) {
    showToast('Upload and ingest documents before using voice input.', 'error');
    return;
  }

  const SpeechRecognition = speechRecognitionClass();
  if (!SpeechRecognition) {
    showToast('Browser voice input is not supported. Try Chrome or Edge.', 'error');
    return;
  }

  stopSpeaking();
  recognition = new SpeechRecognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = selectedLocale();
  finalTranscript = '';
  isRecording = true;
  setRecordingUi(true);

  recognition.onresult = (event) => {
    let interim = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0].transcript;
      if (event.results[index].isFinal) {
        finalTranscript += `${finalTranscript ? ' ' : ''}${transcript}`;
      } else {
        interim = transcript;
      }
    }
    const combined = finalTranscript + (interim ? `${finalTranscript ? ' ' : ''}${interim}` : '');
    setComposerValue(combined, { focus: false });
    document.getElementById('voice-label').textContent = interim
      ? `Listening… "${interim}"`
      : 'Listening…';
  };

  recognition.onerror = (event) => {
    if (event.error !== 'aborted') showToast(`Browser voice error: ${event.error}`, 'error');
    stopRecording();
  };
  recognition.onend = () => {
    if (isRecording) stopRecording();
  };

  try {
    recognition.start();
  } catch (error) {
    showToast(`Could not start browser voice input: ${error.message}`, 'error');
    stopRecording();
  }
}

function stopRecording() {
  isRecording = false;
  setRecordingUi(false);
  if (recognition) {
    const activeRecognition = recognition;
    recognition = null;
    try { activeRecognition.stop(); } catch { /* Already stopped. */ }
  }
  finalTranscript = '';
  const input = document.getElementById('question-input');
  if (input.value.trim() && !input.disabled) input.focus();
}

function setSpeakingUi(active) {
  const status = document.getElementById('tts-status');
  status.style.display = active ? 'flex' : 'none';
}

function speakText(text) {
  const clean = stripMarkdownForSpeech(text);
  if (!clean) return;
  if (!('speechSynthesis' in window)) {
    showToast('Text-to-speech is not supported in this browser.', 'error');
    return;
  }

  stopSpeaking();
  speechUtterance = new SpeechSynthesisUtterance(clean);
  speechUtterance.lang = selectedLocale();
  speechUtterance.rate = 1;
  speechUtterance.pitch = 1;
  speechUtterance.onstart = () => setSpeakingUi(true);
  speechUtterance.onend = speechUtterance.onerror = () => {
    speechUtterance = null;
    setSpeakingUi(false);
  };
  window.speechSynthesis.speak(speechUtterance);
}

function stopSpeaking() {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
  speechUtterance = null;
  setSpeakingUi(false);
}

function setVoiceModeState(state) {
  voiceModeState = state;
  const orb = document.getElementById('vm-orb');
  orb.className = `vm-orb vm-orb-${state}`;
  const labels = {
    idle: 'Tap orb to speak',
    listening: 'Listening…',
    processing: 'Thinking…',
    speaking: 'Speaking…',
  };
  const actions = {
    idle: 'Start listening',
    listening: 'Stop listening and send',
    processing: 'Processing question',
    speaking: 'Speaking response',
  };
  document.getElementById('vm-status').textContent = labels[state] || '';
  orb.setAttribute('aria-label', actions[state] || 'Voice control');
  orb.disabled = state === 'processing' || state === 'speaking';
}

function clearVoiceModeTimer() {
  window.clearTimeout(voiceModeSilenceTimer);
  voiceModeSilenceTimer = 0;
}

function cleanupVoiceModeRecognition() {
  clearVoiceModeTimer();
  if (voiceModeRecognition) {
    const activeRecognition = voiceModeRecognition;
    voiceModeRecognition = null;
    try { activeRecognition.stop(); } catch { /* Already stopped. */ }
  }
}

function startVoiceModeListening() {
  if (!voiceModeActive || voiceModeState === 'listening') return;
  const SpeechRecognition = speechRecognitionClass();
  if (!SpeechRecognition) {
    setVoiceModeState('idle');
    document.getElementById('vm-status').textContent = 'Browser voice input is not supported.';
    return;
  }

  stopSpeaking();
  window.speechSynthesis?.cancel();
  voiceModeFinalText = '';
  document.getElementById('vm-transcript').textContent = '';
  voiceModeRecognition = new SpeechRecognition();
  voiceModeRecognition.continuous = true;
  voiceModeRecognition.interimResults = true;
  voiceModeRecognition.lang = selectedLocale();
  setVoiceModeState('listening');

  voiceModeRecognition.onresult = (event) => {
    let interim = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const transcript = event.results[index][0].transcript;
      if (event.results[index].isFinal) {
        voiceModeFinalText += `${voiceModeFinalText ? ' ' : ''}${transcript}`;
        clearVoiceModeTimer();
        voiceModeSilenceTimer = window.setTimeout(() => {
          if (voiceModeState === 'listening' && voiceModeFinalText.trim()) stopAndProcessVoiceMode();
        }, 1_600);
      } else {
        interim = transcript;
      }
    }
    document.getElementById('vm-transcript').textContent =
      voiceModeFinalText + (interim ? `${voiceModeFinalText ? ' ' : ''}${interim}` : '');
  };
  voiceModeRecognition.onerror = (event) => {
    if (voiceModeActive && voiceModeState === 'listening') setVoiceModeState('idle');
    if (event.error !== 'aborted') {
      document.getElementById('vm-status').textContent = `Browser voice error: ${event.error}`;
    }
  };
  voiceModeRecognition.onend = () => {
    if (voiceModeActive && voiceModeState === 'listening') setVoiceModeState('idle');
  };

  try {
    voiceModeRecognition.start();
  } catch (error) {
    setVoiceModeState('idle');
    document.getElementById('vm-status').textContent = error.message;
  }
}

async function stopAndProcessVoiceMode() {
  cleanupVoiceModeRecognition();
  const text = voiceModeFinalText.trim();
  if (!text) {
    if (voiceModeActive) startVoiceModeListening();
    return;
  }

  setVoiceModeState('processing');
  document.getElementById('vm-transcript').textContent = text;
  document.getElementById('vm-response').textContent = '';
  const result = await sendQuestion(text);
  if (!voiceModeActive) return;

  if (!result.ok) {
    document.getElementById('vm-status').textContent =
      result.reason === 'aborted' ? 'Response stopped.' : 'Unable to answer. Tap the orb to retry.';
    setVoiceModeState('idle');
    return;
  }

  const preview = stripMarkdownForSpeech(result.answer);
  document.getElementById('vm-response').textContent =
    preview.length > 130 ? `${preview.slice(0, 130)}…` : preview;
  speakVoiceModeThenListen(result.answer);
}

function speakVoiceModeThenListen(text) {
  if (!voiceModeActive || !('speechSynthesis' in window)) {
    if (voiceModeActive) window.setTimeout(startVoiceModeListening, 600);
    return;
  }

  window.speechSynthesis.cancel();
  setVoiceModeState('speaking');
  const utterance = new SpeechSynthesisUtterance(stripMarkdownForSpeech(text));
  utterance.lang = selectedLocale();
  utterance.rate = 1.05;
  utterance.pitch = 1;
  utterance.onend = utterance.onerror = () => {
    if (voiceModeActive) window.setTimeout(startVoiceModeListening, 700);
  };
  window.speechSynthesis.speak(utterance);
}

function openVoiceMode() {
  if (document.getElementById('question-input').disabled) {
    showToast('Upload and ingest documents before starting voice mode.', 'error');
    return;
  }
  stopRecording();
  stopSpeaking();
  voiceModeReturnFocus = document.activeElement;
  voiceModeActive = true;
  const overlay = document.getElementById('voice-mode-overlay');
  overlay.classList.add('open');
  overlay.setAttribute('aria-hidden', 'false');
  setAppInert(true);
  document.getElementById('vm-transcript').textContent = '';
  document.getElementById('vm-response').textContent = '';
  setVoiceModeState('idle');
  document.getElementById('vm-orb').focus();
  startVoiceModeListening();
}

function closeVoiceMode() {
  if (!voiceModeActive) return;
  voiceModeActive = false;
  cleanupVoiceModeRecognition();
  stopSpeaking();
  window.speechSynthesis?.cancel();
  const overlay = document.getElementById('voice-mode-overlay');
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
  setAppInert(false);
  setVoiceModeState('idle');
  document.getElementById('vm-transcript').textContent = '';
  document.getElementById('vm-response').textContent = '';
  if (voiceModeReturnFocus?.isConnected) voiceModeReturnFocus.focus();
  voiceModeReturnFocus = null;
}

function handleVoiceModeKeydown(event) {
  if (!voiceModeActive) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    closeVoiceMode();
    return;
  }
  trapFocus(event, document.querySelector('.vm-container'));
}

export function setupVoice() {
  document.getElementById('mic-btn').addEventListener('click', () => {
    if (isRecording) stopRecording();
    else startRecording();
  });
  document.getElementById('voice-stop-btn').addEventListener('click', stopRecording);
  document.getElementById('tts-stop-btn').addEventListener('click', stopSpeaking);
  document.getElementById('vm-open-btn').addEventListener('click', openVoiceMode);
  document.getElementById('vm-end-btn').addEventListener('click', closeVoiceMode);
  document.getElementById('vm-orb').addEventListener('click', () => {
    if (voiceModeState === 'idle') startVoiceModeListening();
    else if (voiceModeState === 'listening') stopAndProcessVoiceMode();
  });
  document.addEventListener('keydown', handleVoiceModeKeydown);
  window.addEventListener('rag:speak', (event) => speakText(event.detail?.text));
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stopRecording();
  });
}
