'use client';

import React, { useState, useRef, useEffect } from 'react';
import { useInference, SlotInfo } from '@/hooks/useInference';

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function SendIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="22" y1="2" x2="11" y2="13" />
      <polygon points="22 2 15 22 11 13 2 9 22 2" />
    </svg>
  );
}

function CpuIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="4" width="16" height="16" rx="2" />
      <rect x="9" y="9" width="6" height="6" />
      <line x1="9" y1="1" x2="9" y2="4" />
      <line x1="15" y1="1" x2="15" y2="4" />
      <line x1="9" y1="20" x2="9" y2="23" />
      <line x1="15" y1="20" x2="15" y2="23" />
      <line x1="20" y1="9" x2="23" y2="9" />
      <line x1="20" y1="14" x2="23" y2="14" />
      <line x1="1" y1="9" x2="4" y2="9" />
      <line x1="1" y1="14" x2="4" y2="14" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

function RefreshIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="23 4 23 10 17 10" />
      <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Status dot color helper
// ---------------------------------------------------------------------------

function statusColor(status: SlotInfo['status']): string {
  switch (status) {
    case 'loaded':
      return '#34d399';
    case 'streaming':
      return '#fbbf24';
    case 'empty':
    default:
      return 'rgba(255,255,255,0.25)';
  }
}

function statusLabel(status: SlotInfo['status']): string {
  switch (status) {
    case 'loaded':
      return 'Loaded';
    case 'streaming':
      return 'Streaming';
    case 'empty':
    default:
      return 'Empty';
  }
}

// ---------------------------------------------------------------------------
// Page Component
// ---------------------------------------------------------------------------

export default function TerminalPage() {
  const {
    messages,
    selectedSlot,
    setSelectedSlot,
    sendMessage,
    isStreaming,
    slotStatuses,
    isDesktop,
    refreshSlots,
    clearMessages,
  } = useInference();

  const [input, setInput] = useState('');
  const [temperature, setTemperature] = useState(100);
  const [maxTokens, setMaxTokens] = useState(128);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Scroll to bottom when messages change.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Refresh slot info on mount.
  useEffect(() => {
    refreshSlots();
  }, [refreshSlots]);

  // Auto-resize textarea.
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
      inputRef.current.style.height = Math.min(inputRef.current.scrollHeight, 120) + 'px';
    }
  }, [input]);

  const handleSubmit = (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!input.trim() || isStreaming) return;
    sendMessage(input.trim(), selectedSlot, maxTokens, temperature);
    setInput('');
    if (inputRef.current) {
      inputRef.current.style.height = 'auto';
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const desktop = isDesktop();

  // -------------------------------------------------------------------------
  // Non-desktop fallback
  // -------------------------------------------------------------------------
  if (!desktop) {
    return (
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          height: '100vh',
          backgroundColor: '#0a0a0a',
          color: 'rgba(255,255,255,0.8)',
          fontFamily: 'system-ui, -apple-system, sans-serif',
          gap: '16px',
          padding: '24px',
          textAlign: 'center',
        }}
      >
        <CpuIcon />
        <h1
          style={{
            fontSize: '24px',
            fontWeight: 300,
            color: 'rgba(255,255,255,0.9)',
            margin: 0,
          }}
        >
          Kernel Inference Terminal
        </h1>
        <p style={{ color: 'rgba(255,255,255,0.5)', fontSize: '15px', maxWidth: '420px', lineHeight: 1.6 }}>
          This terminal communicates directly with VOS3 kernel inference slots
          via the Tauri IPC bridge. It is only available in the VOS3 Desktop
          application.
        </p>
        <div
          style={{
            marginTop: '8px',
            padding: '12px 24px',
            backgroundColor: 'rgba(251,191,36,0.1)',
            border: '1px solid rgba(251,191,36,0.3)',
            borderRadius: '8px',
            color: 'rgba(251,191,36,0.9)',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          Desktop only -- launch VOS3 Enclave to use this feature
        </div>
      </div>
    );
  }

  // -------------------------------------------------------------------------
  // Desktop split-pane UI
  // -------------------------------------------------------------------------
  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        backgroundColor: '#0a0a0a',
        fontFamily: 'system-ui, -apple-system, sans-serif',
        overflow: 'hidden',
      }}
    >
      {/* ----------------------------------------------------------------- */}
      {/* Left sidebar -- Slot list                                         */}
      {/* ----------------------------------------------------------------- */}
      <aside
        style={{
          width: '250px',
          flexShrink: 0,
          borderRight: '1px solid rgba(255,255,255,0.08)',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: '#0d0d0d',
        }}
      >
        {/* Sidebar header */}
        <div
          style={{
            padding: '16px',
            borderBottom: '1px solid rgba(255,255,255,0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <span
            style={{
              fontSize: '12px',
              fontWeight: 600,
              color: 'rgba(255,255,255,0.5)',
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
            }}
          >
            Inference Slots
          </span>
          <button
            onClick={refreshSlots}
            title="Refresh slot statuses"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '28px',
              height: '28px',
              borderRadius: '6px',
              backgroundColor: 'transparent',
              border: 'none',
              color: 'rgba(255,255,255,0.4)',
              cursor: 'pointer',
              transition: 'background-color 0.15s',
            }}
            onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.08)')}
            onMouseOut={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            <RefreshIcon />
          </button>
        </div>

        {/* Slot list */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '8px' }}>
          {slotStatuses.map((slot) => {
            const active = slot.id === selectedSlot;
            return (
              <button
                key={slot.id}
                onClick={() => setSelectedSlot(slot.id)}
                aria-pressed={active}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '12px',
                  width: '100%',
                  padding: '10px 12px',
                  marginBottom: '4px',
                  borderRadius: '8px',
                  backgroundColor: active ? 'rgba(102,126,234,0.15)' : 'transparent',
                  border: active ? '1px solid rgba(102,126,234,0.3)' : '1px solid transparent',
                  color: active ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.6)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.15s',
                  fontSize: '13px',
                }}
                onMouseOver={(e) => {
                  if (!active) e.currentTarget.style.backgroundColor = 'rgba(255,255,255,0.05)';
                }}
                onMouseOut={(e) => {
                  if (!active) e.currentTarget.style.backgroundColor = 'transparent';
                }}
              >
                {/* Status dot */}
                <span
                  style={{
                    width: '8px',
                    height: '8px',
                    borderRadius: '50%',
                    backgroundColor: statusColor(slot.status),
                    flexShrink: 0,
                    boxShadow:
                      slot.status === 'streaming'
                        ? '0 0 6px rgba(251,191,36,0.6)'
                        : slot.status === 'loaded'
                        ? '0 0 6px rgba(52,211,153,0.4)'
                        : 'none',
                    transition: 'background-color 0.2s',
                  }}
                />
                {/* Slot info */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 500, fontSize: '13px' }}>
                    Slot {slot.id}
                  </div>
                  <div
                    style={{
                      fontSize: '11px',
                      color: 'rgba(255,255,255,0.35)',
                      marginTop: '2px',
                    }}
                  >
                    {statusLabel(slot.status)}
                  </div>
                </div>
              </button>
            );
          })}
        </div>

        {/* Sidebar footer -- clear button */}
        <div
          style={{
            padding: '12px 16px',
            borderTop: '1px solid rgba(255,255,255,0.08)',
          }}
        >
          <button
            onClick={clearMessages}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              width: '100%',
              padding: '8px 12px',
              borderRadius: '8px',
              backgroundColor: 'transparent',
              border: '1px solid rgba(255,255,255,0.08)',
              color: 'rgba(255,255,255,0.4)',
              fontSize: '12px',
              cursor: 'pointer',
              transition: 'all 0.15s',
            }}
            onMouseOver={(e) => {
              e.currentTarget.style.backgroundColor = 'rgba(239,68,68,0.1)';
              e.currentTarget.style.borderColor = 'rgba(239,68,68,0.3)';
              e.currentTarget.style.color = 'rgba(239,68,68,0.8)';
            }}
            onMouseOut={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent';
              e.currentTarget.style.borderColor = 'rgba(255,255,255,0.08)';
              e.currentTarget.style.color = 'rgba(255,255,255,0.4)';
            }}
          >
            <TrashIcon />
            Clear history
          </button>
        </div>
      </aside>

      {/* ----------------------------------------------------------------- */}
      {/* Right main area -- Chat                                           */}
      {/* ----------------------------------------------------------------- */}
      <main
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
          position: 'relative',
        }}
      >
        {/* Header bar */}
        <div
          style={{
            padding: '12px 20px',
            borderBottom: '1px solid rgba(255,255,255,0.08)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            backgroundColor: 'rgba(0,0,0,0.3)',
            flexShrink: 0,
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
            <CpuIcon />
            <span
              style={{
                fontSize: '14px',
                fontWeight: 500,
                color: 'rgba(255,255,255,0.8)',
              }}
            >
              Slot {selectedSlot}
            </span>
            <span
              style={{
                fontSize: '11px',
                padding: '2px 8px',
                borderRadius: '4px',
                backgroundColor:
                  slotStatuses[selectedSlot]?.status === 'loaded'
                    ? 'rgba(52,211,153,0.15)'
                    : slotStatuses[selectedSlot]?.status === 'streaming'
                    ? 'rgba(251,191,36,0.15)'
                    : 'rgba(255,255,255,0.05)',
                color: statusColor(slotStatuses[selectedSlot]?.status ?? 'empty'),
                fontWeight: 500,
              }}
            >
              {statusLabel(slotStatuses[selectedSlot]?.status ?? 'empty')}
            </span>
          </div>
          <div
            style={{
              fontSize: '11px',
              color: 'rgba(255,255,255,0.3)',
            }}
          >
            VOS3 Kernel Inference
          </div>
        </div>

        {/* Message history */}
        <div
          role="log"
          aria-label="Inference messages"
          style={{
            flex: 1,
            overflowY: 'auto',
            padding: '20px',
            display: 'flex',
            flexDirection: 'column',
            gap: '16px',
          }}
        >
          {messages.length === 0 ? (
            <div
              style={{
                flex: 1,
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '12px',
                color: 'rgba(255,255,255,0.3)',
              }}
            >
              <CpuIcon />
              <div style={{ fontSize: '15px', fontWeight: 300 }}>
                Send a message to Slot {selectedSlot}
              </div>
              <div style={{ fontSize: '12px', color: 'rgba(255,255,255,0.2)' }}>
                Messages are sent to the kernel via VBus for on-device inference
              </div>
            </div>
          ) : (
            <>
              {messages
                .filter((m) => m.slotId === selectedSlot)
                .map((msg) => (
                  <div
                    key={msg.id}
                    style={{
                      display: 'flex',
                      justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                    }}
                  >
                    <div
                      style={{
                        maxWidth: '75%',
                        padding: '12px 16px',
                        borderRadius:
                          msg.role === 'user'
                            ? '16px 16px 4px 16px'
                            : '16px 16px 16px 4px',
                        backgroundColor:
                          msg.role === 'user'
                            ? 'rgba(102,126,234,0.2)'
                            : 'rgba(255,255,255,0.05)',
                        border:
                          msg.role === 'user'
                            ? '1px solid rgba(102,126,234,0.3)'
                            : '1px solid rgba(255,255,255,0.08)',
                      }}
                    >
                      {/* Role label */}
                      <div
                        style={{
                          fontSize: '10px',
                          fontWeight: 600,
                          color:
                            msg.role === 'user'
                              ? 'rgba(102,126,234,0.8)'
                              : 'rgba(52,211,153,0.8)',
                          textTransform: 'uppercase',
                          letterSpacing: '0.1em',
                          marginBottom: '6px',
                        }}
                      >
                        {msg.role === 'user' ? 'You' : `Slot ${msg.slotId}`}
                      </div>
                      {/* Content */}
                      <div
                        style={{
                          fontSize: '14px',
                          lineHeight: 1.6,
                          color: 'rgba(255,255,255,0.9)',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                          fontFamily:
                            msg.role === 'assistant'
                              ? "'SF Mono', 'Fira Code', 'Consolas', monospace"
                              : 'system-ui, -apple-system, sans-serif',
                        }}
                      >
                        {msg.content}
                      </div>
                      {/* Timestamp */}
                      <div
                        style={{
                          fontSize: '10px',
                          color: 'rgba(255,255,255,0.2)',
                          marginTop: '6px',
                          textAlign: msg.role === 'user' ? 'right' : 'left',
                        }}
                      >
                        {msg.timestamp.toLocaleTimeString()}
                      </div>
                    </div>
                  </div>
                ))}

              {/* Streaming indicator */}
              {isStreaming && (
                <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                  <div
                    style={{
                      padding: '12px 16px',
                      borderRadius: '16px 16px 16px 4px',
                      backgroundColor: 'rgba(255,255,255,0.05)',
                      border: '1px solid rgba(255,255,255,0.08)',
                    }}
                  >
                    <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                      <span
                        style={{
                          width: '6px',
                          height: '6px',
                          borderRadius: '50%',
                          backgroundColor: '#667eea',
                          animation: 'pulse 1.4s ease-in-out infinite',
                        }}
                      />
                      <span
                        style={{
                          width: '6px',
                          height: '6px',
                          borderRadius: '50%',
                          backgroundColor: '#667eea',
                          animation: 'pulse 1.4s ease-in-out 0.2s infinite',
                        }}
                      />
                      <span
                        style={{
                          width: '6px',
                          height: '6px',
                          borderRadius: '50%',
                          backgroundColor: '#667eea',
                          animation: 'pulse 1.4s ease-in-out 0.4s infinite',
                        }}
                      />
                    </div>
                  </div>
                </div>
              )}

              <div ref={messagesEndRef} />
            </>
          )}
        </div>

        {/* ----------------------------------------------------------------- */}
        {/* Input area                                                        */}
        {/* ----------------------------------------------------------------- */}
        <div
          style={{
            padding: '16px 20px',
            borderTop: '1px solid rgba(255,255,255,0.08)',
            backgroundColor: 'rgba(0,0,0,0.3)',
            flexShrink: 0,
          }}
        >
          {/* Parameter controls */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '20px',
              marginBottom: '12px',
              flexWrap: 'wrap',
            }}
          >
            {/* Temperature slider */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label
                htmlFor="temperature-slider"
                style={{
                  fontSize: '11px',
                  fontWeight: 500,
                  color: 'rgba(255,255,255,0.4)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  whiteSpace: 'nowrap',
                }}
              >
                Temp
              </label>
              <input
                id="temperature-slider"
                type="range"
                min={0}
                max={200}
                step={1}
                value={temperature}
                onChange={(e) => setTemperature(Number(e.target.value))}
                style={{
                  width: '100px',
                  accentColor: '#667eea',
                  cursor: 'pointer',
                }}
              />
              <span
                style={{
                  fontSize: '12px',
                  fontFamily: 'monospace',
                  color: 'rgba(255,255,255,0.6)',
                  minWidth: '32px',
                  textAlign: 'right',
                }}
              >
                {(temperature / 100).toFixed(2)}
              </span>
            </div>

            {/* Max tokens input */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <label
                htmlFor="max-tokens-input"
                style={{
                  fontSize: '11px',
                  fontWeight: 500,
                  color: 'rgba(255,255,255,0.4)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.05em',
                  whiteSpace: 'nowrap',
                }}
              >
                Max Tokens
              </label>
              <input
                id="max-tokens-input"
                type="number"
                min={1}
                max={4096}
                value={maxTokens}
                onChange={(e) => setMaxTokens(Math.max(1, Math.min(4096, Number(e.target.value) || 1)))}
                style={{
                  width: '72px',
                  padding: '4px 8px',
                  backgroundColor: 'rgba(255,255,255,0.05)',
                  border: '1px solid rgba(255,255,255,0.1)',
                  borderRadius: '6px',
                  color: 'rgba(255,255,255,0.8)',
                  fontSize: '12px',
                  fontFamily: 'monospace',
                  outline: 'none',
                  textAlign: 'center',
                }}
              />
            </div>
          </div>

          {/* Text input + send */}
          <form onSubmit={handleSubmit}>
            <div
              style={{
                display: 'flex',
                alignItems: 'flex-end',
                gap: '12px',
                padding: '10px 16px',
                backgroundColor: 'rgba(255,255,255,0.04)',
                border: '1px solid rgba(255,255,255,0.1)',
                borderRadius: '14px',
              }}
            >
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={`Send to Slot ${selectedSlot}...`}
                aria-label="Inference message input"
                disabled={isStreaming}
                rows={1}
                style={{
                  flex: 1,
                  padding: '6px 0',
                  backgroundColor: 'transparent',
                  border: 'none',
                  color: 'rgba(255,255,255,0.9)',
                  fontSize: '15px',
                  lineHeight: 1.5,
                  resize: 'none',
                  outline: 'none',
                  maxHeight: '120px',
                  fontFamily: 'system-ui, -apple-system, sans-serif',
                }}
              />
              <button
                type="submit"
                disabled={isStreaming || !input.trim()}
                aria-label="Send message"
                style={{
                  width: '38px',
                  height: '38px',
                  borderRadius: '10px',
                  backgroundColor: input.trim()
                    ? 'rgba(102,126,234,0.9)'
                    : 'rgba(255,255,255,0.08)',
                  color: input.trim() ? 'white' : 'rgba(255,255,255,0.25)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'all 0.2s',
                  flexShrink: 0,
                  border: 'none',
                  cursor: input.trim() && !isStreaming ? 'pointer' : 'default',
                }}
              >
                <SendIcon />
              </button>
            </div>
          </form>
        </div>
      </main>

      {/* Animations */}
      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 0.3; }
          50% { opacity: 1; }
        }

        textarea::placeholder {
          color: rgba(255,255,255,0.25);
        }

        input[type="number"]::-webkit-inner-spin-button,
        input[type="number"]::-webkit-outer-spin-button {
          opacity: 0.5;
        }

        /* Custom scrollbar for message area */
        [role="log"]::-webkit-scrollbar {
          width: 6px;
        }
        [role="log"]::-webkit-scrollbar-track {
          background: transparent;
        }
        [role="log"]::-webkit-scrollbar-thumb {
          background: rgba(255,255,255,0.1);
          border-radius: 3px;
        }
        [role="log"]::-webkit-scrollbar-thumb:hover {
          background: rgba(255,255,255,0.2);
        }
      `}</style>
    </div>
  );
}
