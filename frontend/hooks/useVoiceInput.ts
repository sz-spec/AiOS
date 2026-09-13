'use client';

import { useState, useCallback, useEffect, useRef } from 'react';

// Supported languages with their Web Speech API language codes
export const SUPPORTED_LANGUAGES = [
  { code: 'en-US', name: 'English', flag: '🇺🇸' },
  { code: 'es-ES', name: 'Spanish', flag: '🇪🇸' },
  { code: 'fr-FR', name: 'French', flag: '🇫🇷' },
  { code: 'de-DE', name: 'German', flag: '🇩🇪' },
  { code: 'it-IT', name: 'Italian', flag: '🇮🇹' },
  { code: 'pt-BR', name: 'Portuguese', flag: '🇧🇷' },
  { code: 'zh-CN', name: 'Chinese', flag: '🇨🇳' },
  { code: 'ja-JP', name: 'Japanese', flag: '🇯🇵' },
  { code: 'ko-KR', name: 'Korean', flag: '🇰🇷' },
  { code: 'ar-SA', name: 'Arabic', flag: '🇸🇦' },
  { code: 'he-IL', name: 'Hebrew', flag: '🇮🇱' },
] as const;

export type LanguageCode = typeof SUPPORTED_LANGUAGES[number]['code'];

interface UseVoiceInputOptions {
  onTranscript?: (transcript: string) => void;
  onError?: (error: string) => void;
  continuous?: boolean;
  interimResults?: boolean;
}

interface UseVoiceInputReturn {
  isListening: boolean;
  isSupported: boolean;
  transcript: string;
  error: string | null;
  selectedLanguage: LanguageCode;
  setSelectedLanguage: (lang: LanguageCode) => void;
  startListening: () => void;
  stopListening: () => void;
  toggleListening: () => void;
  clearTranscript: () => void;
}

// Type definitions for Web Speech API
interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList;
  resultIndex: number;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message?: string;
}

interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: SpeechRecognitionEvent) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
}

declare global {
  interface Window {
    SpeechRecognition: new () => SpeechRecognition;
    webkitSpeechRecognition: new () => SpeechRecognition;
  }
}

const STORAGE_KEY = 'v-creator-voice-language';

export function useVoiceInput(options: UseVoiceInputOptions = {}): UseVoiceInputReturn {
  const {
    onTranscript,
    onError,
    continuous = true,
    interimResults = true,
  } = options;

  const [isListening, setIsListening] = useState(false);
  const [isSupported, setIsSupported] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [selectedLanguage, setSelectedLanguageState] = useState<LanguageCode>('en-US');

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const isMountedRef = useRef(true);
  const wantsListeningRef = useRef(false);

  // Check for browser support and load saved language preference
  useEffect(() => {
    isMountedRef.current = true;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    setIsSupported(!!SpeechRecognition);

    // Load saved language preference
    if (typeof window !== 'undefined') {
      const savedLang = localStorage.getItem(STORAGE_KEY);
      if (savedLang && SUPPORTED_LANGUAGES.some(l => l.code === savedLang)) {
        setSelectedLanguageState(savedLang as LanguageCode);
      }
    }

    return () => {
      isMountedRef.current = false;
      if (recognitionRef.current) {
        recognitionRef.current.abort();
      }
    };
  }, []);

  const setSelectedLanguage = useCallback((lang: LanguageCode) => {
    setSelectedLanguageState(lang);
    if (typeof window !== 'undefined') {
      localStorage.setItem(STORAGE_KEY, lang);
    }
    // Restart recognition with new language if currently listening
    if (recognitionRef.current && isListening) {
      recognitionRef.current.abort();
      // Will restart with new language in onend handler
    }
  }, [isListening]);

  const startListening = useCallback(() => {
    if (!isSupported) {
      const errorMsg = 'Speech recognition is not supported in this browser. Please use Chrome for best results.';
      setError(errorMsg);
      onError?.(errorMsg);
      return;
    }

    setError(null);
    setTranscript('');
    wantsListeningRef.current = true;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();

    recognition.continuous = continuous;
    recognition.interimResults = interimResults;
    recognition.lang = selectedLanguage;

    recognition.onstart = () => {
      if (isMountedRef.current) {
        setIsListening(true);
      }
    };

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      let finalTranscript = '';
      let interimTranscript = '';

      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) {
          finalTranscript += result[0].transcript;
        } else {
          interimTranscript += result[0].transcript;
        }
      }

      const currentTranscript = finalTranscript || interimTranscript;

      if (isMountedRef.current && currentTranscript) {
        setTranscript(currentTranscript);
        if (finalTranscript) {
          onTranscript?.(finalTranscript);
        }
      }
    };

    recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
      if (!isMountedRef.current) return;

      let errorMessage = 'Speech recognition error';

      switch (event.error) {
        case 'not-allowed':
          errorMessage = 'Microphone access was denied. Please allow microphone access and try again.';
          break;
        case 'no-speech':
          // In continuous mode, silence is normal — let auto-restart handle it
          if (wantsListeningRef.current) return;
          errorMessage = 'No speech detected. Please try again.';
          break;
        case 'audio-capture':
          errorMessage = 'No microphone found. Please ensure a microphone is connected.';
          break;
        case 'network':
          errorMessage = 'Network error occurred. Please check your connection.';
          break;
        case 'aborted':
          // User stopped, not really an error
          return;
        default:
          errorMessage = `Speech recognition error: ${event.error}`;
      }

      setError(errorMessage);
      setIsListening(false);
      onError?.(errorMessage);
    };

    recognition.onend = () => {
      if (isMountedRef.current) {
        // Auto-restart if user hasn't manually stopped (browser may end on silence/timeout)
        if (wantsListeningRef.current) {
          try {
            recognition.start();
            return;
          } catch {
            // Fall through to stop
          }
        }
        setIsListening(false);
      }
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
    } catch (err) {
      const errorMsg = 'Failed to start speech recognition. Please try again.';
      setError(errorMsg);
      onError?.(errorMsg);
    }
  }, [isSupported, continuous, interimResults, selectedLanguage, onTranscript, onError]);

  const stopListening = useCallback(() => {
    wantsListeningRef.current = false;
    if (recognitionRef.current) {
      recognitionRef.current.stop();
    }
    setIsListening(false);
  }, []);

  const toggleListening = useCallback(() => {
    if (isListening) {
      stopListening();
    } else {
      startListening();
    }
  }, [isListening, startListening, stopListening]);

  const clearTranscript = useCallback(() => {
    setTranscript('');
    setError(null);
  }, []);

  return {
    isListening,
    isSupported,
    transcript,
    error,
    selectedLanguage,
    setSelectedLanguage,
    startListening,
    stopListening,
    toggleListening,
    clearTranscript,
  };
}
