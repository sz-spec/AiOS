'use client';

import React, { useState, useRef, useEffect } from 'react';
import { Insight, useMemoryChat } from '@/hooks/useMemory';
import { useVoiceInput } from '@/hooks/useVoiceInput';

interface MemoryChatPanelProps {
  insight: Insight | null;
  onClose: () => void;
  onApplyEdit: (insightId: string, newContent: string) => void;
}

export function MemoryChatPanel({ insight, onClose, onApplyEdit }: MemoryChatPanelProps) {
  const { messages, isProcessing, sendMessage, clearChat } = useMemoryChat(insight);
  const [inputValue, setInputValue] = useState('');
  const [pendingEdit, setPendingEdit] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Voice input integration
  const {
    isListening,
    isSupported: voiceSupported,
    transcript,
    toggleListening,
    clearTranscript,
  } = useVoiceInput({
    onTranscript: (text) => {
      setInputValue(prev => prev + (prev ? ' ' : '') + text);
    },
  });

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Update input from voice transcript
  useEffect(() => {
    if (transcript && !isListening) {
      setInputValue(prev => prev + (prev ? ' ' : '') + transcript);
      clearTranscript();
    }
  }, [transcript, isListening, clearTranscript]);

  const handleSubmit = async () => {
    if (!inputValue.trim() || isProcessing) return;

    const message = inputValue;
    setInputValue('');

    const suggestedEdit = await sendMessage(message);
    if (suggestedEdit) {
      setPendingEdit(suggestedEdit);
    }
  };

  const applyPendingEdit = () => {
    if (pendingEdit && insight) {
      onApplyEdit(insight.id, pendingEdit);
      setPendingEdit(null);
      clearChat();
      onClose();
    }
  };

  if (!insight) return null;

  return (
    <div
      style={{
        position: 'fixed',
        right: 0,
        top: 0,
        bottom: 0,
        width: '420px',
        backgroundColor: '#fff',
        boxShadow: '-4px 0 24px rgba(0,0,0,0.12)',
        display: 'flex',
        flexDirection: 'column',
        zIndex: 1000,
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: '16px 20px',
          borderBottom: '1px solid #eee',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
        }}
      >
        <div>
          <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600, color: '#fff' }}>
            Discuss Memory
          </h3>
          <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'rgba(255,255,255,0.8)' }}>
            Ask questions or request edits
          </p>
        </div>
        <button
          onClick={onClose}
          style={{
            width: '32px',
            height: '32px',
            borderRadius: '8px',
            border: 'none',
            backgroundColor: 'rgba(255,255,255,0.2)',
            color: '#fff',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '18px',
          }}
        >
          x
        </button>
      </div>

      {/* Selected Memory Preview */}
      <div
        style={{
          padding: '16px 20px',
          backgroundColor: '#f8f9fa',
          borderBottom: '1px solid #eee',
        }}
      >
        <div style={{ fontSize: '11px', color: '#666', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Selected Memory
        </div>
        <p style={{ margin: 0, fontSize: '14px', color: '#111', lineHeight: 1.5 }}>
          {insight.content.length > 150
            ? insight.content.slice(0, 150) + '...'
            : insight.content
          }
        </p>
        <span
          style={{
            display: 'inline-block',
            marginTop: '8px',
            padding: '3px 8px',
            backgroundColor: '#e5e5e5',
            borderRadius: '4px',
            fontSize: '11px',
            color: '#666',
          }}
        >
          {insight.metadata.memory_type}
        </span>
      </div>

      {/* Messages */}
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          padding: '16px 20px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: '#999', padding: '32px 0' }}>
            <p style={{ fontSize: '14px', marginBottom: '16px' }}>
              Ask me about this memory or request changes
            </p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', justifyContent: 'center' }}>
              {[
                'Make it clearer',
                'Summarize this',
                'Add more detail',
                'Change the tone',
              ].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => setInputValue(suggestion)}
                  style={{
                    padding: '6px 12px',
                    borderRadius: '16px',
                    border: '1px solid #e5e5e5',
                    backgroundColor: '#fff',
                    color: '#666',
                    fontSize: '12px',
                    cursor: 'pointer',
                  }}
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <div
            key={msg.id}
            style={{
              alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
              maxWidth: '85%',
            }}
          >
            <div
              style={{
                padding: '10px 14px',
                borderRadius: msg.role === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                backgroundColor: msg.role === 'user' ? '#667eea' : '#f0f0f0',
                color: msg.role === 'user' ? '#fff' : '#111',
                fontSize: '14px',
                lineHeight: 1.5,
              }}
            >
              {msg.content}
            </div>

            {/* Show apply button if there's a suggested edit */}
            {msg.suggestedEdit && (
              <div
                style={{
                  marginTop: '8px',
                  padding: '12px',
                  backgroundColor: '#e8f5e9',
                  borderRadius: '8px',
                  border: '1px solid #c8e6c9',
                }}
              >
                <div style={{ fontSize: '11px', color: '#388e3c', marginBottom: '6px', fontWeight: 600 }}>
                  SUGGESTED EDIT
                </div>
                <p style={{ margin: '0 0 10px', fontSize: '13px', color: '#2e7d32' }}>
                  {msg.suggestedEdit}
                </p>
                <button
                  onClick={() => setPendingEdit(msg.suggestedEdit!)}
                  style={{
                    padding: '6px 14px',
                    backgroundColor: '#4caf50',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '6px',
                    fontSize: '12px',
                    fontWeight: 600,
                    cursor: 'pointer',
                  }}
                >
                  Apply This Edit
                </button>
              </div>
            )}

            <div style={{ fontSize: '10px', color: '#999', marginTop: '4px', textAlign: msg.role === 'user' ? 'right' : 'left' }}>
              {msg.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </div>
          </div>
        ))}

        {isProcessing && (
          <div style={{ alignSelf: 'flex-start', padding: '10px 14px', backgroundColor: '#f0f0f0', borderRadius: '16px' }}>
            <div style={{ display: 'flex', gap: '4px' }}>
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#999', animation: 'bounce 0.6s infinite' }} />
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#999', animation: 'bounce 0.6s infinite 0.1s' }} />
              <span style={{ width: '8px', height: '8px', borderRadius: '50%', backgroundColor: '#999', animation: 'bounce 0.6s infinite 0.2s' }} />
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Pending Edit Confirmation */}
      {pendingEdit && (
        <div
          style={{
            padding: '16px 20px',
            backgroundColor: '#fff3e0',
            borderTop: '1px solid #ffe0b2',
          }}
        >
          <div style={{ fontSize: '12px', color: '#e65100', marginBottom: '8px', fontWeight: 600 }}>
            Apply this edit?
          </div>
          <p style={{ margin: '0 0 12px', fontSize: '13px', color: '#bf360c' }}>
            {pendingEdit.length > 100 ? pendingEdit.slice(0, 100) + '...' : pendingEdit}
          </p>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={applyPendingEdit}
              style={{
                flex: 1,
                padding: '10px',
                backgroundColor: '#ff9800',
                color: '#fff',
                border: 'none',
                borderRadius: '8px',
                fontSize: '13px',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Apply Edit
            </button>
            <button
              onClick={() => setPendingEdit(null)}
              style={{
                padding: '10px 16px',
                backgroundColor: '#fff',
                color: '#666',
                border: '1px solid #ddd',
                borderRadius: '8px',
                fontSize: '13px',
                cursor: 'pointer',
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Input Area */}
      <div
        style={{
          padding: '16px 20px',
          borderTop: '1px solid #eee',
          backgroundColor: '#fff',
        }}
      >
        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-end' }}>
          <div style={{ flex: 1, position: 'relative' }}>
            <textarea
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmit();
                }
              }}
              placeholder={isListening ? 'Listening...' : 'Ask about this memory or request edits...'}
              rows={2}
              style={{
                width: '100%',
                padding: '12px',
                paddingRight: '44px',
                border: `2px solid ${isListening ? '#667eea' : '#e5e5e5'}`,
                borderRadius: '12px',
                fontSize: '14px',
                resize: 'none',
                outline: 'none',
                transition: 'border-color 0.2s',
              }}
            />

            {/* Voice button inside input */}
            {voiceSupported && (
              <button
                onClick={toggleListening}
                title={isListening ? 'Stop listening' : 'Voice input'}
                style={{
                  position: 'absolute',
                  right: '8px',
                  bottom: '8px',
                  width: '32px',
                  height: '32px',
                  borderRadius: '50%',
                  border: 'none',
                  backgroundColor: isListening ? '#dc2626' : '#f0f0f0',
                  color: isListening ? '#fff' : '#666',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  animation: isListening ? 'pulse 1.5s infinite' : 'none',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <line x1="12" x2="12" y1="19" y2="22" />
                </svg>
              </button>
            )}
          </div>

          <button
            onClick={handleSubmit}
            disabled={!inputValue.trim() || isProcessing}
            style={{
              padding: '12px 20px',
              backgroundColor: inputValue.trim() && !isProcessing ? '#667eea' : '#e5e5e5',
              color: inputValue.trim() && !isProcessing ? '#fff' : '#999',
              border: 'none',
              borderRadius: '12px',
              fontSize: '14px',
              fontWeight: 600,
              cursor: inputValue.trim() && !isProcessing ? 'pointer' : 'not-allowed',
              transition: 'background-color 0.2s',
            }}
          >
            Send
          </button>
        </div>

        <p style={{ margin: '8px 0 0', fontSize: '11px', color: '#999', textAlign: 'center' }}>
          Press Enter to send, Shift+Enter for new line
        </p>
      </div>

      {/* CSS animations */}
      <style jsx global>{`
        @keyframes bounce {
          0%, 60%, 100% { transform: translateY(0); }
          30% { transform: translateY(-4px); }
        }
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.4); }
          70% { box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); }
          100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); }
        }
      `}</style>
    </div>
  );
}

export default MemoryChatPanel;
