'use client';

import React, { useState, useRef, useEffect } from 'react';
import { useVoiceInput, SUPPORTED_LANGUAGES, LanguageCode } from '@/hooks/useVoiceInput';

interface VoiceInputProps {
  onTranscript: (transcript: string) => void;
  disabled?: boolean;
  showLanguageSelector?: boolean;
  size?: 'small' | 'medium' | 'large';
  className?: string;
}

export function VoiceInput({
  onTranscript,
  disabled = false,
  showLanguageSelector = true,
  size = 'medium',
  className = '',
}: VoiceInputProps) {
  const [showDropdown, setShowDropdown] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const {
    isListening,
    isSupported,
    error,
    selectedLanguage,
    setSelectedLanguage,
    toggleListening,
  } = useVoiceInput({
    onTranscript,
  });

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    }

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const sizeStyles = {
    small: { button: 28, icon: 14 },
    medium: { button: 36, icon: 18 },
    large: { button: 44, icon: 22 },
  };

  const currentSize = sizeStyles[size];

  if (!isSupported) {
    return null;
  }

  const selectedLangInfo = SUPPORTED_LANGUAGES.find(l => l.code === selectedLanguage);

  return (
    <div
      className={className}
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '4px',
        position: 'relative',
      }}
      ref={dropdownRef}
    >
      {/* Main microphone button */}
      <button
        type="button"
        onClick={toggleListening}
        disabled={disabled}
        title={isListening ? 'Stop listening' : `Start voice input (${selectedLangInfo?.name})`}
        aria-label={isListening ? 'Stop voice recording' : 'Start voice recording'}
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: currentSize.button,
          height: currentSize.button,
          borderRadius: '50%',
          border: 'none',
          backgroundColor: isListening ? 'var(--error, #dc2626)' : 'var(--bg-tertiary, #f3f3f3)',
          color: isListening ? 'white' : 'var(--text-secondary, #666)',
          cursor: disabled ? 'not-allowed' : 'pointer',
          opacity: disabled ? 0.5 : 1,
          transition: 'all 0.2s ease',
          animation: isListening ? 'voice-pulse 1.5s ease-in-out infinite' : 'none',
          boxShadow: isListening ? '0 0 0 4px rgba(220, 38, 38, 0.3)' : 'none',
        }}
      >
        <MicrophoneIcon size={currentSize.icon} isListening={isListening} />
      </button>

      {/* Language selector */}
      {showLanguageSelector && (
        <button
          type="button"
          onClick={() => setShowDropdown(!showDropdown)}
          disabled={disabled || isListening}
          title="Select language"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            padding: '4px 8px',
            borderRadius: '6px',
            border: '1px solid var(--border, #e5e5e5)',
            backgroundColor: 'var(--bg-primary, white)',
            color: 'var(--text-secondary, #666)',
            cursor: disabled || isListening ? 'not-allowed' : 'pointer',
            opacity: disabled || isListening ? 0.5 : 1,
            fontSize: '12px',
            gap: '4px',
          }}
        >
          <span>{selectedLangInfo?.flag}</span>
          <ChevronIcon />
        </button>
      )}

      {/* Language dropdown - opens upward for visibility */}
      {showDropdown && (
        <div
          style={{
            position: 'absolute',
            bottom: '100%',
            right: 0,
            marginBottom: '4px',
            backgroundColor: 'var(--bg-primary, white)',
            border: '1px solid var(--border, #e5e5e5)',
            borderRadius: '8px',
            boxShadow: 'var(--shadow-lg, 0 -10px 25px -5px rgba(0, 0, 0, 0.1))',
            zIndex: 1000,
            minWidth: '160px',
            maxHeight: '300px',
            overflowY: 'auto',
          }}
        >
          {SUPPORTED_LANGUAGES.map((lang) => (
            <button
              key={lang.code}
              type="button"
              onClick={() => {
                setSelectedLanguage(lang.code);
                setShowDropdown(false);
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                width: '100%',
                padding: '8px 12px',
                border: 'none',
                backgroundColor: lang.code === selectedLanguage ? 'var(--bg-tertiary, #f3f3f3)' : 'transparent',
                color: 'var(--text-primary, #0d0d0d)',
                cursor: 'pointer',
                fontSize: '13px',
                textAlign: 'left',
              }}
            >
              <span>{lang.flag}</span>
              <span>{lang.name}</span>
              {lang.code === selectedLanguage && (
                <CheckIcon style={{ marginLeft: 'auto' }} />
              )}
            </button>
          ))}
        </div>
      )}

      {/* Error tooltip */}
      {error && (
        <div
          role="status"
          style={{
            position: 'absolute',
            bottom: '100%',
            left: '50%',
            transform: 'translateX(-50%)',
            marginBottom: '8px',
            padding: '8px 12px',
            backgroundColor: 'var(--error, #dc2626)',
            color: 'white',
            borderRadius: '6px',
            fontSize: '12px',
            whiteSpace: 'nowrap',
            zIndex: 1000,
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}

// Microphone SVG Icon
function MicrophoneIcon({ size, isListening }: { size: number; isListening: boolean }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
      {isListening && (
        <>
          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1" opacity="0.3">
            <animate
              attributeName="r"
              from="10"
              to="14"
              dur="1s"
              repeatCount="indefinite"
            />
            <animate
              attributeName="opacity"
              from="0.3"
              to="0"
              dur="1s"
              repeatCount="indefinite"
            />
          </circle>
        </>
      )}
    </svg>
  );
}

// Chevron Icon
function ChevronIcon() {
  return (
    <svg
      width={12}
      height={12}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

// Check Icon
function CheckIcon({ style }: { style?: React.CSSProperties }) {
  return (
    <svg
      width={14}
      height={14}
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--accent, #d97706)"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      style={style}
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export default VoiceInput;
