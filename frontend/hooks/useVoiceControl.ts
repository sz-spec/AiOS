'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { useRouter } from 'next/navigation';
import { SUPPORTED_LANGUAGES, LanguageCode } from './useVoiceInput';

// Voice command types
export type VoiceCommandType =
  | 'navigate'
  | 'create'
  | 'list'
  | 'search'
  | 'execute'
  | 'help'
  | 'stop'
  | 'chat'
  | 'settings'
  | 'unknown';

export interface VoiceCommand {
  type: VoiceCommandType;
  target?: string;
  params?: Record<string, string>;
  rawText: string;
  confidence: number;
}

export interface VoiceControlState {
  isListening: boolean;
  isSpeaking: boolean;
  isProcessing: boolean;
  isHandsFreeMode: boolean;
  lastCommand: VoiceCommand | null;
  transcript: string;
  error: string | null;
  language: LanguageCode;
}

interface UseVoiceControlOptions {
  onCommand?: (command: VoiceCommand) => void;
  onNavigate?: (path: string) => void;
  wakeWord?: string;
  autoSpeak?: boolean;
}

// Navigation mappings
const NAVIGATION_COMMANDS: Record<string, string> = {
  'home': '/',
  'dashboard': '/',
  'chat': '/chat',
  'builder': '/builder',
  'code': '/builder',
  'agents': '/agents',
  'agent': '/agents',
  'v-core': '/v-core',
  'vcore': '/v-core',
  'core': '/v-core',
  'business': '/v-core',
  'rag': '/rag',
  'documents': '/rag',
  'settings': '/settings',
  'config': '/settings',
  'configuration': '/settings',
};

// Command patterns
const COMMAND_PATTERNS = [
  { pattern: /^(go to|navigate to|open|show me|take me to)\s+(.+)$/i, type: 'navigate' as const },
  { pattern: /^(create|add|new)\s+(a\s+)?(.+)$/i, type: 'create' as const },
  { pattern: /^(list|show|display)\s+(all\s+)?(.+)$/i, type: 'list' as const },
  { pattern: /^(search|find|look for)\s+(.+)$/i, type: 'search' as const },
  { pattern: /^(run|execute|start|trigger)\s+(.+)$/i, type: 'execute' as const },
  { pattern: /^(help|what can you do|commands)$/i, type: 'help' as const },
  { pattern: /^(stop|cancel|nevermind|never mind)$/i, type: 'stop' as const },
  { pattern: /^(ask|tell me|what is|how do|explain)\s+(.+)$/i, type: 'chat' as const },
];

// Speech synthesis voices cache
let cachedVoices: SpeechSynthesisVoice[] = [];

export function useVoiceControl(options: UseVoiceControlOptions = {}) {
  const {
    onCommand,
    onNavigate,
    wakeWord = 'hey vos',
    autoSpeak = true
  } = options;

  const router = useRouter();

  const [state, setState] = useState<VoiceControlState>({
    isListening: false,
    isSpeaking: false,
    isProcessing: false,
    isHandsFreeMode: false,
    lastCommand: null,
    transcript: '',
    error: null,
    language: 'en-US',
  });

  const recognitionRef = useRef<any>(null);
  const synthRef = useRef<SpeechSynthesis | null>(null);
  const isMountedRef = useRef(true);
  const wakeWordDetectedRef = useRef(false);
  const silenceTimerRef = useRef<NodeJS.Timeout | null>(null);

  // Initialize speech synthesis
  useEffect(() => {
    if (typeof window !== 'undefined') {
      synthRef.current = window.speechSynthesis;

      // Load voices
      const loadVoices = () => {
        cachedVoices = synthRef.current?.getVoices() || [];
      };

      loadVoices();
      synthRef.current?.addEventListener('voiceschanged', loadVoices);

      return () => {
        synthRef.current?.removeEventListener('voiceschanged', loadVoices);
      };
    }
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      stopListening();
      stopSpeaking();
    };
  }, []);

  // Parse voice command from transcript
  const parseCommand = useCallback((text: string): VoiceCommand => {
    const normalizedText = text.toLowerCase().trim();

    // Check for navigation commands
    for (const [key, path] of Object.entries(NAVIGATION_COMMANDS)) {
      if (normalizedText.includes(key)) {
        return {
          type: 'navigate',
          target: path,
          rawText: text,
          confidence: 0.9,
        };
      }
    }

    // Check command patterns
    for (const { pattern, type } of COMMAND_PATTERNS) {
      const match = normalizedText.match(pattern);
      if (match) {
        return {
          type,
          target: match[match.length - 1]?.trim(),
          rawText: text,
          confidence: 0.85,
        };
      }
    }

    // Default to chat if no command matched
    return {
      type: 'chat',
      target: text,
      rawText: text,
      confidence: 0.7,
    };
  }, []);

  // Execute command
  const executeCommand = useCallback(async (command: VoiceCommand) => {
    setState(s => ({ ...s, isProcessing: true, lastCommand: command }));

    try {
      switch (command.type) {
        case 'navigate':
          if (command.target) {
            const path = NAVIGATION_COMMANDS[command.target.toLowerCase()] || command.target;
            if (onNavigate) {
              onNavigate(path);
            } else {
              router.push(path);
            }
            speak(`Navigating to ${command.target}`);
          }
          break;

        case 'create':
          speak(`Creating ${command.target || 'new item'}. Please use the interface to complete.`);
          // Navigate to appropriate creation page
          if (command.target?.includes('entity') || command.target?.includes('record')) {
            router.push('/v-core');
          } else if (command.target?.includes('workflow')) {
            router.push('/v-core');
          } else if (command.target?.includes('agent')) {
            router.push('/agents');
          }
          break;

        case 'list':
          speak(`Showing ${command.target || 'items'}`);
          if (command.target?.includes('entity') || command.target?.includes('entities')) {
            router.push('/v-core');
          } else if (command.target?.includes('workflow')) {
            router.push('/v-core');
          } else if (command.target?.includes('agent')) {
            router.push('/agents');
          }
          break;

        case 'search':
          speak(`Searching for ${command.target}`);
          // Trigger search in current context
          break;

        case 'execute':
          speak(`Executing ${command.target}`);
          break;

        case 'help':
          speak(`You can say: Go to chat, show agents, create entity, run workflow, or ask me anything.`);
          break;

        case 'stop':
          speak('Stopping. Say hey vos to activate again.');
          stopListening();
          break;

        case 'settings':
          router.push('/settings');
          speak('Opening settings');
          break;

        case 'chat':
          // Send to chat for AI processing
          if (command.target) {
            speak('Let me think about that...');
            // The parent component should handle this
          }
          break;

        default:
          speak("I didn't understand that command. Try saying help for available commands.");
      }

      onCommand?.(command);
    } catch (error) {
      console.error('Command execution error:', error);
      speak('Sorry, there was an error executing that command.');
    } finally {
      setState(s => ({ ...s, isProcessing: false }));
    }
  }, [router, onCommand, onNavigate]);

  // Text to speech
  const speak = useCallback((text: string, options?: { rate?: number; pitch?: number }) => {
    if (!synthRef.current || !autoSpeak) return;

    // Cancel any ongoing speech
    synthRef.current.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = options?.rate || 1.0;
    utterance.pitch = options?.pitch || 1.0;

    // Try to get a good voice for the language
    const langCode = state.language.split('-')[0];
    const preferredVoice = cachedVoices.find(v =>
      v.lang.startsWith(langCode) && (v.name.includes('Google') || v.name.includes('Samantha'))
    ) || cachedVoices.find(v => v.lang.startsWith(langCode));

    if (preferredVoice) {
      utterance.voice = preferredVoice;
    }

    utterance.onstart = () => {
      if (isMountedRef.current) {
        setState(s => ({ ...s, isSpeaking: true }));
      }
    };

    utterance.onend = () => {
      if (isMountedRef.current) {
        setState(s => ({ ...s, isSpeaking: false }));
      }
    };

    utterance.onerror = () => {
      if (isMountedRef.current) {
        setState(s => ({ ...s, isSpeaking: false }));
      }
    };

    synthRef.current.speak(utterance);
  }, [autoSpeak, state.language]);

  // Stop speaking
  const stopSpeaking = useCallback(() => {
    synthRef.current?.cancel();
    setState(s => ({ ...s, isSpeaking: false }));
  }, []);

  // Start listening
  const startListening = useCallback((continuous = false) => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setState(s => ({ ...s, error: 'Speech recognition not supported' }));
      return;
    }

    // Stop any existing recognition
    if (recognitionRef.current) {
      recognitionRef.current.abort();
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = continuous;
    recognition.interimResults = true;
    recognition.lang = state.language;

    recognition.onstart = () => {
      if (isMountedRef.current) {
        setState(s => ({
          ...s,
          isListening: true,
          isHandsFreeMode: continuous,
          error: null
        }));
      }
    };

    recognition.onresult = (event: any) => {
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

      if (isMountedRef.current) {
        setState(s => ({ ...s, transcript: currentTranscript }));
      }

      // Process final transcript
      if (finalTranscript) {
        const lowerTranscript = finalTranscript.toLowerCase();

        // Check for wake word in hands-free mode
        if (continuous && !wakeWordDetectedRef.current) {
          if (lowerTranscript.includes(wakeWord)) {
            wakeWordDetectedRef.current = true;
            speak('Yes?');
            // Reset after timeout
            setTimeout(() => {
              wakeWordDetectedRef.current = false;
            }, 10000);
            return;
          }
          return; // Ignore if wake word not detected
        }

        // Reset silence timer
        if (silenceTimerRef.current) {
          clearTimeout(silenceTimerRef.current);
        }

        // Parse and execute command
        const command = parseCommand(finalTranscript);
        executeCommand(command);

        // Reset wake word detection after command
        if (continuous) {
          silenceTimerRef.current = setTimeout(() => {
            wakeWordDetectedRef.current = false;
          }, 3000);
        }
      }
    };

    recognition.onerror = (event: any) => {
      if (!isMountedRef.current) return;

      if (event.error !== 'aborted' && event.error !== 'no-speech') {
        setState(s => ({ ...s, error: `Voice error: ${event.error}` }));
      }

      // Restart in continuous mode
      if (continuous && event.error === 'no-speech') {
        setTimeout(() => {
          if (isMountedRef.current && state.isHandsFreeMode) {
            recognition.start();
          }
        }, 100);
      }
    };

    recognition.onend = () => {
      if (isMountedRef.current) {
        // Restart in continuous mode
        if (continuous && state.isHandsFreeMode) {
          setTimeout(() => {
            if (isMountedRef.current) {
              try {
                recognition.start();
              } catch (e) {
                // Already started
              }
            }
          }, 100);
        } else {
          setState(s => ({ ...s, isListening: false }));
        }
      }
    };

    recognitionRef.current = recognition;

    try {
      recognition.start();
    } catch (e) {
      setState(s => ({ ...s, error: 'Failed to start voice recognition' }));
    }
  }, [state.language, state.isHandsFreeMode, wakeWord, parseCommand, executeCommand, speak]);

  // Stop listening
  const stopListening = useCallback(() => {
    if (recognitionRef.current) {
      recognitionRef.current.abort();
      recognitionRef.current = null;
    }
    wakeWordDetectedRef.current = false;
    if (silenceTimerRef.current) {
      clearTimeout(silenceTimerRef.current);
    }
    setState(s => ({
      ...s,
      isListening: false,
      isHandsFreeMode: false,
      transcript: ''
    }));
  }, []);

  // Toggle hands-free mode
  const toggleHandsFreeMode = useCallback(() => {
    if (state.isHandsFreeMode) {
      stopListening();
      speak('Hands-free mode disabled');
    } else {
      speak('Hands-free mode enabled. Say hey vos to give commands.');
      startListening(true);
    }
  }, [state.isHandsFreeMode, startListening, stopListening, speak]);

  // Set language
  const setLanguage = useCallback((lang: LanguageCode) => {
    setState(s => ({ ...s, language: lang }));
    if (typeof window !== 'undefined') {
      localStorage.setItem('vos3-voice-language', lang);
    }
  }, []);

  return {
    ...state,
    speak,
    stopSpeaking,
    startListening: () => startListening(false),
    stopListening,
    toggleHandsFreeMode,
    setLanguage,
    parseCommand,
    executeCommand,
    supportedLanguages: SUPPORTED_LANGUAGES,
  };
}

export default useVoiceControl;
