'use client';

import React, { useState, useEffect } from 'react';
import { useVoiceControl } from '@/hooks/useVoiceControl';
import { SUPPORTED_LANGUAGES, LanguageCode } from '@/hooks/useVoiceInput';

interface VoiceControllerProps {
  onChatMessage?: (message: string) => void;
  position?: 'bottom-right' | 'bottom-left' | 'top-right' | 'top-left';
}

export function VoiceController({
  onChatMessage,
  position = 'bottom-right'
}: VoiceControllerProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [showHelp, setShowHelp] = useState(false);
  const [showLanguages, setShowLanguages] = useState(false);

  const {
    isListening,
    isSpeaking,
    isProcessing,
    isHandsFreeMode,
    lastCommand,
    transcript,
    error,
    language,
    speak,
    stopSpeaking,
    startListening,
    stopListening,
    toggleHandsFreeMode,
    setLanguage,
    supportedLanguages,
  } = useVoiceControl({
    onCommand: (command) => {
      if (command.type === 'chat' && command.target && onChatMessage) {
        onChatMessage(command.target);
      }
    },
  });

  // Position styles
  const positionStyles: Record<string, React.CSSProperties> = {
    'bottom-right': { bottom: 20, right: 20 },
    'bottom-left': { bottom: 20, left: 20 },
    'top-right': { top: 80, right: 20 },
    'top-left': { top: 80, left: 20 },
  };

  const currentLang = supportedLanguages.find(l => l.code === language);

  return (
    <div
      style={{
        position: 'fixed',
        ...positionStyles[position],
        zIndex: 9999,
        display: 'flex',
        flexDirection: 'column',
        alignItems: position.includes('right') ? 'flex-end' : 'flex-start',
        gap: 8,
      }}
    >
      {/* Transcript Display */}
      {(isListening || transcript) && (
        <div
          style={{
            backgroundColor: 'var(--bg-primary, white)',
            border: '1px solid var(--border-light, #e5e5e5)',
            borderRadius: 12,
            padding: '12px 16px',
            maxWidth: 300,
            boxShadow: 'var(--shadow-lg, 0 10px 25px -5px rgba(0, 0, 0, 0.1))',
          }}
        >
          <div style={{ fontSize: 12, color: 'var(--text-tertiary, #999)', marginBottom: 4 }}>
            {isListening ? (isHandsFreeMode ? 'Listening (hands-free)...' : 'Listening...') : 'Last heard:'}
          </div>
          <div style={{ fontSize: 14, color: 'var(--text-primary, #0d0d0d)' }}>
            {transcript || 'Say something...'}
          </div>
          {lastCommand && (
            <div style={{ fontSize: 11, color: 'var(--accent, #d97706)', marginTop: 4 }}>
              Command: {lastCommand.type} {lastCommand.target ? `→ ${lastCommand.target}` : ''}
            </div>
          )}
        </div>
      )}

      {/* Error Display */}
      {error && (
        <div
          style={{
            backgroundColor: 'var(--error, #dc2626)',
            color: 'white',
            borderRadius: 8,
            padding: '8px 12px',
            fontSize: 12,
            maxWidth: 250,
          }}
        >
          {error}
        </div>
      )}

      {/* Help Panel */}
      {showHelp && (
        <div
          style={{
            backgroundColor: 'var(--bg-primary, white)',
            border: '1px solid var(--border-light, #e5e5e5)',
            borderRadius: 12,
            padding: 16,
            maxWidth: 280,
            boxShadow: 'var(--shadow-lg, 0 10px 25px -5px rgba(0, 0, 0, 0.1))',
          }}
        >
          <div style={{ fontWeight: 600, marginBottom: 12, fontSize: 14 }}>Voice Commands</div>
          <div style={{ fontSize: 13, lineHeight: 1.8 }}>
            <div><strong>Navigation:</strong></div>
            <div style={{ color: 'var(--text-secondary, #666)', marginLeft: 8 }}>
              &ldquo;Go to chat&rdquo; / &ldquo;Open agents&rdquo; / &ldquo;Show settings&rdquo;
            </div>
            <div style={{ marginTop: 8 }}><strong>Actions:</strong></div>
            <div style={{ color: 'var(--text-secondary, #666)', marginLeft: 8 }}>
              &ldquo;Create entity&rdquo; / &ldquo;List workflows&rdquo; / &ldquo;Run workflow&rdquo;
            </div>
            <div style={{ marginTop: 8 }}><strong>Chat:</strong></div>
            <div style={{ color: 'var(--text-secondary, #666)', marginLeft: 8 }}>
              &ldquo;Ask...&rdquo; / &ldquo;Tell me...&rdquo; / &ldquo;What is...&rdquo;
            </div>
            <div style={{ marginTop: 8 }}><strong>Control:</strong></div>
            <div style={{ color: 'var(--text-secondary, #666)', marginLeft: 8 }}>
              &ldquo;Help&rdquo; / &ldquo;Stop&rdquo;
            </div>
            {isHandsFreeMode && (
              <div style={{ marginTop: 12, padding: 8, backgroundColor: 'var(--bg-tertiary, #f3f3f3)', borderRadius: 6 }}>
                Say &ldquo;<strong>Hey VOS</strong>&rdquo; to activate
              </div>
            )}
          </div>
        </div>
      )}

      {/* Language Selector */}
      {showLanguages && (
        <div
          style={{
            backgroundColor: 'var(--bg-primary, white)',
            border: '1px solid var(--border-light, #e5e5e5)',
            borderRadius: 12,
            padding: 8,
            maxHeight: 300,
            overflowY: 'auto',
            boxShadow: 'var(--shadow-lg, 0 10px 25px -5px rgba(0, 0, 0, 0.1))',
          }}
        >
          {supportedLanguages.map((lang) => (
            <button
              key={lang.code}
              onClick={() => {
                setLanguage(lang.code);
                setShowLanguages(false);
              }}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                width: '100%',
                padding: '8px 12px',
                border: 'none',
                backgroundColor: lang.code === language ? 'var(--bg-tertiary, #f3f3f3)' : 'transparent',
                borderRadius: 6,
                cursor: 'pointer',
                fontSize: 13,
              }}
            >
              <span>{lang.flag}</span>
              <span>{lang.name}</span>
            </button>
          ))}
        </div>
      )}

      {/* Expanded Controls */}
      {isExpanded && (
        <div
          style={{
            display: 'flex',
            gap: 8,
            backgroundColor: 'var(--bg-primary, white)',
            border: '1px solid var(--border-light, #e5e5e5)',
            borderRadius: 28,
            padding: 6,
            boxShadow: 'var(--shadow-md, 0 4px 6px -1px rgba(0, 0, 0, 0.1))',
          }}
        >
          {/* Hands-free toggle */}
          <button
            onClick={toggleHandsFreeMode}
            title={isHandsFreeMode ? 'Disable hands-free' : 'Enable hands-free mode'}
            style={{
              width: 40,
              height: 40,
              borderRadius: '50%',
              border: 'none',
              backgroundColor: isHandsFreeMode ? 'var(--accent, #d97706)' : 'var(--bg-tertiary, #f3f3f3)',
              color: isHandsFreeMode ? 'white' : 'var(--text-secondary, #666)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <HandsFreeIcon />
          </button>

          {/* Language selector */}
          <button
            onClick={() => setShowLanguages(!showLanguages)}
            title="Change language"
            style={{
              width: 40,
              height: 40,
              borderRadius: '50%',
              border: 'none',
              backgroundColor: showLanguages ? 'var(--bg-tertiary, #f3f3f3)' : 'transparent',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: 18,
            }}
          >
            {currentLang?.flag || '🌐'}
          </button>

          {/* Help button */}
          <button
            onClick={() => setShowHelp(!showHelp)}
            title="Voice commands help"
            style={{
              width: 40,
              height: 40,
              borderRadius: '50%',
              border: 'none',
              backgroundColor: showHelp ? 'var(--bg-tertiary, #f3f3f3)' : 'transparent',
              color: 'var(--text-secondary, #666)',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <HelpIcon />
          </button>

          {/* Stop speaking */}
          {isSpeaking && (
            <button
              onClick={stopSpeaking}
              title="Stop speaking"
              style={{
                width: 40,
                height: 40,
                borderRadius: '50%',
                border: 'none',
                backgroundColor: 'var(--bg-tertiary, #f3f3f3)',
                color: 'var(--text-secondary, #666)',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
              }}
            >
              <StopIcon />
            </button>
          )}
        </div>
      )}

      {/* Main Voice Button */}
      <button
        onClick={() => {
          if (isListening && !isHandsFreeMode) {
            stopListening();
          } else if (!isListening) {
            startListening();
          }
          if (!isExpanded) {
            setIsExpanded(true);
          }
        }}
        onDoubleClick={() => setIsExpanded(!isExpanded)}
        title={isListening ? 'Stop listening' : 'Start voice control (double-click for options)'}
        style={{
          width: 56,
          height: 56,
          borderRadius: '50%',
          border: 'none',
          backgroundColor: isListening
            ? 'var(--error, #dc2626)'
            : isProcessing
              ? 'var(--accent, #d97706)'
              : 'var(--bg-primary, white)',
          color: isListening || isProcessing ? 'white' : 'var(--text-primary, #0d0d0d)',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: isListening
            ? '0 0 0 4px rgba(220, 38, 38, 0.3), 0 4px 12px rgba(0,0,0,0.15)'
            : '0 4px 12px rgba(0,0,0,0.15)',
          transition: 'all 0.2s ease',
          animation: isListening ? 'voice-pulse 1.5s ease-in-out infinite' : 'none',
        }}
      >
        {isProcessing ? (
          <ProcessingIcon />
        ) : isSpeaking ? (
          <SpeakingIcon />
        ) : (
          <MicrophoneIcon size={24} />
        )}
      </button>

      {/* Status indicator */}
      {isHandsFreeMode && !isExpanded && (
        <div
          style={{
            position: 'absolute',
            top: -4,
            right: -4,
            width: 16,
            height: 16,
            borderRadius: '50%',
            backgroundColor: 'var(--accent, #d97706)',
            border: '2px solid white',
          }}
        />
      )}
    </div>
  );
}

// Icons
function MicrophoneIcon({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </svg>
  );
}

function HandsFreeIcon() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <circle cx="12" cy="12" r="10" strokeDasharray="4 4" />
    </svg>
  );
}

function HelpIcon() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="10" />
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

function StopIcon() {
  return (
    <svg width={20} height={20} viewBox="0 0 24 24" fill="currentColor">
      <rect x="6" y="6" width="12" height="12" rx="2" />
    </svg>
  );
}

function ProcessingIcon() {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <circle cx="12" cy="12" r="10" strokeDasharray="32" strokeDashoffset="32">
        <animate attributeName="stroke-dashoffset" dur="1s" repeatCount="indefinite" values="32;0" />
      </circle>
    </svg>
  );
}

function SpeakingIcon() {
  return (
    <svg width={24} height={24} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
      <path d="M15.54 8.46a5 5 0 0 1 0 7.07">
        <animate attributeName="opacity" dur="0.5s" repeatCount="indefinite" values="1;0.3;1" />
      </path>
      <path d="M19.07 4.93a10 10 0 0 1 0 14.14">
        <animate attributeName="opacity" dur="0.7s" repeatCount="indefinite" values="1;0.3;1" />
      </path>
    </svg>
  );
}

export default VoiceController;
