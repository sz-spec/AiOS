'use client';

import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { useChat } from '@/hooks/useChat';
import { VoiceInput } from '@/components/shared/VoiceInput';
import { CollapsibleMessage } from '@/components/shared/CollapsibleMessage';
import { ModelStatusBadge } from '@/components/chat/ModelStatusBadge';

function SendIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

export default function ChatPage() {
  const { messages, sendMessage, isLoading, models, selectedModel, setSelectedModel, endSession, hostCapability } = useChat();

  // W7.2 — Map status codes to a single-char unicode prefix used inside
  // the native <option> text (native selects can't render React).
  const statusPrefix = (s?: string): string => {
    if (s === 'local_ready') return '● ';
    if (s === 'available_lan') return '○ ';
    if (s === 'available_https') return '⬇ ';
    if (s === 'cloud_fallback_only') return '☁ ';
    return '';
  };

  // The richly-rendered badge for the SELECTED model (shown below the select).
  const selectedModelMeta = useMemo(
    () => models.find((m) => m.id === selectedModel),
    [models, selectedModel],
  );

  // Open-weight families exposed by the curated catalog. Order chosen
  // for operator familiarity (Llama → Mistral → Gemma → Qwen → Phi).
  const openWeightFamilies: Array<{ family: string; label: string }> = [
    { family: 'llama', label: 'Meta Llama (Local)' },
    { family: 'mistral', label: 'Mistral (Local)' },
    { family: 'gemma', label: 'Google Gemma (Local)' },
    { family: 'qwen', label: 'Alibaba Qwen (Local)' },
    { family: 'phi', label: 'Microsoft Phi (Local)' },
  ];
  const [input, setInput] = useState('');
  const [showInput, setShowInput] = useState(true);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 120) + 'px';
    }
  }, [input]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      sendMessage(input.trim());
      setInput('');
      if (textareaRef.current) {
        textareaRef.current.style.height = 'auto';
      }
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
  };

  const handleVoiceTranscript = useCallback((transcript: string) => {
    setInput(prev => prev ? `${prev} ${transcript}` : transcript);
  }, []);

  // Persist conversation summary on unmount / page close
  const endSessionRef = useRef(endSession);
  endSessionRef.current = endSession;
  const messagesRef = useRef(messages);
  messagesRef.current = messages;

  useEffect(() => {
    const handleBeforeUnload = () => {
      endSessionRef.current(messagesRef.current);
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => {
      window.removeEventListener('beforeunload', handleBeforeUnload);
      endSessionRef.current(messagesRef.current);
    };
  }, []);

  // Toggle input visibility with keyboard
  useEffect(() => {
    const handleKeyPress = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowInput(!showInput);
      }
    };
    window.addEventListener('keydown', handleKeyPress);
    return () => window.removeEventListener('keydown', handleKeyPress);
  }, [showInput]);

  // Get the last few messages to display as subtitles
  const recentMessages = messages.slice(-6);

  return (
    <div
      ref={containerRef}
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100vh',
        backgroundColor: '#0a0a0a',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Cinematic gradient overlay */}
      <div style={{
        position: 'absolute',
        inset: 0,
        background: 'radial-gradient(ellipse at center, #1a1a1a 0%, #0a0a0a 70%)',
        pointerEvents: 'none',
      }} />

      {/* Film grain effect */}
      <div style={{
        position: 'absolute',
        inset: 0,
        opacity: 0.03,
        backgroundImage: `url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E")`,
        pointerEvents: 'none',
      }} />

      {/* Minimal header - model selector */}
      <header style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        padding: '16px 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        zIndex: 20,
        opacity: showInput ? 1 : 0,
        transition: 'opacity 0.5s ease',
      }}>
        <select
          value={selectedModel}
          onChange={(e) => setSelectedModel(e.target.value)}
          aria-label="Select AI model"
          style={{
            padding: '8px 16px',
            backgroundColor: 'rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '8px',
            color: 'rgba(255,255,255,0.6)',
            fontSize: '13px',
            cursor: 'pointer',
            outline: 'none',
            minWidth: '180px',
          }}
        >
          <optgroup label="OpenAI" style={{ backgroundColor: '#1a1a1a' }}>
            {models.filter(m => m.provider === 'openai').map((m) => (
              <option key={m.id} value={m.id} style={{ backgroundColor: '#1a1a1a' }}>
                {m.id}
              </option>
            ))}
          </optgroup>
          <optgroup label="Anthropic" style={{ backgroundColor: '#1a1a1a' }}>
            {models.filter(m => m.provider === 'anthropic').map((m) => (
              <option key={m.id} value={m.id} style={{ backgroundColor: '#1a1a1a' }}>
                {m.id}
              </option>
            ))}
          </optgroup>
          <optgroup label="Google" style={{ backgroundColor: '#1a1a1a' }}>
            {models.filter(m => m.provider === 'google').map((m) => (
              <option key={m.id} value={m.id} style={{ backgroundColor: '#1a1a1a' }}>
                {m.id}
              </option>
            ))}
          </optgroup>
          <optgroup label="xAI (Grok)" style={{ backgroundColor: '#1a1a1a' }}>
            {models.filter(m => m.provider === 'xai').map((m) => (
              <option key={m.id} value={m.id} style={{ backgroundColor: '#1a1a1a' }}>
                {m.id}
              </option>
            ))}
          </optgroup>
          <optgroup label="Alibaba (Qwen)" style={{ backgroundColor: '#1a1a1a' }}>
            {models.filter(m => m.provider === 'alibaba').map((m) => (
              <option key={m.id} value={m.id} style={{ backgroundColor: '#1a1a1a' }}>
                {m.id}
              </option>
            ))}
          </optgroup>
          <optgroup label="DeepSeek" style={{ backgroundColor: '#1a1a1a' }}>
            {models.filter(m => m.provider === 'deepseek').map((m) => (
              <option key={m.id} value={m.id} style={{ backgroundColor: '#1a1a1a' }}>
                {m.id}
              </option>
            ))}
          </optgroup>
          <optgroup label="Local (Ollama)" style={{ backgroundColor: '#1a1a1a' }}>
            {models.filter(m => m.provider === 'local').map((m) => (
              <option key={m.id} value={m.id} style={{ backgroundColor: '#1a1a1a' }}>
                {statusPrefix(m.status)}{m.id}
              </option>
            ))}
          </optgroup>

          {/* W7.2 — Open-weight curated catalog. Each option carries a
              status prefix: ● local-ready, ○ LAN, ⬇ HTTPS, ☁ cloud only. */}
          {openWeightFamilies.map(({ family, label }) => {
            const opts = models.filter((m) => m.family === family);
            if (opts.length === 0) return null;
            return (
              <optgroup
                key={family}
                label={label}
                style={{ backgroundColor: '#1a1a1a' }}
              >
                {opts.map((m) => (
                  <option
                    key={m.id}
                    value={m.id}
                    style={{ backgroundColor: '#1a1a1a' }}
                  >
                    {statusPrefix(m.status)}
                    {m.display_name ?? m.id}
                  </option>
                ))}
              </optgroup>
            );
          })}
        </select>

        {/* W7.2 — Hardware-aware status panel for the selected model. */}
        {selectedModelMeta?.status ? (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '4px 12px',
              borderRadius: 8,
              backgroundColor: 'rgba(255,255,255,0.04)',
              fontSize: 11,
              color: 'rgba(255,255,255,0.7)',
              marginLeft: 8,
            }}
          >
            <ModelStatusBadge status={selectedModelMeta.status} />
            {selectedModelMeta.tier ? (
              <span>
                tier: <code>{selectedModelMeta.tier}</code>
              </span>
            ) : null}
            {hostCapability ? (
              <span>
                · host: <code>{hostCapability.gpu_name ?? hostCapability.platform}</code>
                {' '}({hostCapability.tier})
              </span>
            ) : null}
          </div>
        ) : null}
        <div style={{
          fontSize: '11px',
          color: 'rgba(255,255,255,0.3)',
          letterSpacing: '0.1em',
        }}>
          ESC to toggle UI
        </div>
      </header>

      {/* Center area - empty space for cinematic feel */}
      <div style={{ flex: 1 }} />

      {/* Subtitle area - messages appear here */}
      <div style={{
        position: 'relative',
        padding: '0 48px 120px',
        minHeight: '40vh',
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        alignItems: 'center',
        zIndex: 10,
      }}>
        {messages.length === 0 ? (
          <div style={{
            textAlign: 'center',
            animation: 'fadeIn 1s ease',
          }}>
            <div style={{
              fontSize: '48px',
              fontWeight: 200,
              color: 'rgba(255,255,255,0.9)',
              marginBottom: '16px',
              letterSpacing: '-0.02em',
              fontFamily: 'system-ui, -apple-system, sans-serif',
            }}>
              V
            </div>
            <p style={{
              color: 'rgba(255,255,255,0.4)',
              fontSize: '16px',
              fontWeight: 300,
              letterSpacing: '0.05em',
            }}>
              Start a conversation
            </p>
          </div>
        ) : (
          <div role="log" aria-label="Chat messages" style={{
            width: '100%',
            maxWidth: '900px',
            display: 'flex',
            flexDirection: 'column',
            gap: '24px',
          }}>
            {recentMessages.map((msg, i) => (
              <div
                key={messages.indexOf(msg)}
                style={{
                  textAlign: 'center',
                  animation: i === recentMessages.length - 1 ? 'subtitleIn 0.6s ease' : 'none',
                  opacity: 1 - (recentMessages.length - 1 - i) * 0.15,
                }}
              >
                {/* Speaker label */}
                <div style={{
                  fontSize: '11px',
                  fontWeight: 500,
                  color: msg.role === 'user' ? 'rgba(217, 119, 6, 0.7)' : 'rgba(255,255,255,0.4)',
                  letterSpacing: '0.15em',
                  textTransform: 'uppercase',
                  marginBottom: '8px',
                }}>
                  {msg.role === 'user' ? 'YOU' : 'V'}
                </div>

                {/* Message content - subtitle style */}
                <div style={{
                  display: 'inline-block',
                  padding: '12px 24px',
                  backgroundColor: 'rgba(0,0,0,0.7)',
                  borderRadius: '4px',
                  maxWidth: '80%',
                }}>
                  <CollapsibleMessage
                    content={msg.content}
                    isAssistant={msg.role === 'assistant'}
                    agentName="V"
                    style={{
                      margin: 0,
                      fontSize: msg.role === 'user' ? '18px' : '20px',
                      fontWeight: msg.role === 'user' ? 400 : 300,
                      color: msg.role === 'user' ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,1)',
                      lineHeight: 1.6,
                      letterSpacing: '0.01em',
                      fontFamily: 'system-ui, -apple-system, sans-serif',
                      textShadow: '0 2px 4px rgba(0,0,0,0.5)',
                    }}
                  />
                </div>
              </div>
            ))}

            {/* Loading indicator */}
            {isLoading && (
              <div style={{
                textAlign: 'center',
                animation: 'subtitleIn 0.6s ease',
              }}>
                <div style={{
                  fontSize: '11px',
                  fontWeight: 500,
                  color: 'rgba(255,255,255,0.4)',
                  letterSpacing: '0.15em',
                  textTransform: 'uppercase',
                  marginBottom: '8px',
                }}>
                  V
                </div>
                <div style={{
                  display: 'inline-block',
                  padding: '12px 24px',
                  backgroundColor: 'rgba(0,0,0,0.7)',
                  borderRadius: '4px',
                }}>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center', justifyContent: 'center' }}>
                    <span style={{
                      width: '6px',
                      height: '6px',
                      borderRadius: '50%',
                      backgroundColor: 'rgba(255,255,255,0.6)',
                      animation: 'pulse 1.4s ease-in-out infinite',
                    }} />
                    <span style={{
                      width: '6px',
                      height: '6px',
                      borderRadius: '50%',
                      backgroundColor: 'rgba(255,255,255,0.6)',
                      animation: 'pulse 1.4s ease-in-out 0.2s infinite',
                    }} />
                    <span style={{
                      width: '6px',
                      height: '6px',
                      borderRadius: '50%',
                      backgroundColor: 'rgba(255,255,255,0.6)',
                      animation: 'pulse 1.4s ease-in-out 0.4s infinite',
                    }} />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input area - movie theater style */}
      <div style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        padding: '24px',
        background: 'linear-gradient(transparent, rgba(0,0,0,0.9) 40%)',
        zIndex: 20,
        opacity: showInput ? 1 : 0,
        transform: showInput ? 'translateY(0)' : 'translateY(100%)',
        transition: 'all 0.5s ease',
      }}>
        <form onSubmit={handleSubmit} style={{ maxWidth: '700px', margin: '0 auto' }}>
          <div style={{
            display: 'flex',
            alignItems: 'flex-end',
            gap: '12px',
            padding: '12px 20px',
            backgroundColor: 'rgba(255,255,255,0.05)',
            border: '1px solid rgba(255,255,255,0.1)',
            borderRadius: '16px',
            backdropFilter: 'blur(20px)',
          }}>
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type your message..."
              aria-label="Chat message input"
              disabled={isLoading}
              rows={1}
              style={{
                flex: 1,
                padding: '8px 0',
                backgroundColor: 'transparent',
                border: 'none',
                color: 'rgba(255,255,255,0.9)',
                fontSize: '16px',
                lineHeight: 1.5,
                resize: 'none',
                outline: 'none',
                maxHeight: '120px',
                fontFamily: 'system-ui, -apple-system, sans-serif',
              }}
            />
            <VoiceInput
              onTranscript={handleVoiceTranscript}
              disabled={isLoading}
              showLanguageSelector={false}
              size="medium"
            />
            <button
              type="submit"
              disabled={isLoading || !input.trim()}
              aria-label="Send message"
              style={{
                width: '40px',
                height: '40px',
                borderRadius: '12px',
                backgroundColor: input.trim() ? 'rgba(217, 119, 6, 0.9)' : 'rgba(255,255,255,0.1)',
                color: input.trim() ? 'white' : 'rgba(255,255,255,0.3)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'all 0.2s',
                flexShrink: 0,
                border: 'none',
                cursor: input.trim() ? 'pointer' : 'default',
              }}
            >
              <SendIcon />
            </button>
          </div>
        </form>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 1; }
        }

        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }

        @keyframes subtitleIn {
          from {
            opacity: 0;
            transform: translateY(20px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        textarea::placeholder {
          color: rgba(255,255,255,0.3);
        }

        select option {
          background-color: #1a1a1a;
          color: rgba(255,255,255,0.9);
        }
      `}</style>
    </div>
  );
}
