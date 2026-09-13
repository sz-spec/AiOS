'use client';

import { useState, useRef, useEffect } from 'react';
import { Send, Loader2, User, Sparkles } from 'lucide-react';
import { useChatStore, ChatMessage } from '@/lib/store/chatStore';

interface ProjectChatProps {
  projectId: string;
  onFilesUpdated?: (files: Record<string, string>) => void;
}

export function ProjectChat({ projectId, onFilesUpdated }: ProjectChatProps) {
  const { messages, isLoading, addMessage, setIsLoading, setProjectId } = useChatStore();
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setProjectId(projectId);
  }, [projectId, setProjectId]);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || isLoading) return;

    setInput('');
    addMessage({
      id: `user-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: new Date().toISOString(),
    });

    setIsLoading(true);

    try {
      const res = await fetch('/api/v1/edit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          instruction: text,
        }),
      });

      const data = await res.json();

      addMessage({
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: data.message || 'Changes applied!',
        timestamp: new Date().toISOString(),
      });

      if (data.updated_files && onFilesUpdated) {
        onFilesUpdated(data.updated_files);
      }
    } catch {
      addMessage({
        id: `assistant-${Date.now()}`,
        role: 'assistant',
        content: 'Sorry, something went wrong. Please try again.',
        timestamp: new Date().toISOString(),
      });
    }

    setIsLoading(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      height: '100%',
      backgroundColor: 'var(--bg-primary)',
    }}>
      {/* Messages */}
      <div
        ref={scrollRef}
        style={{
          flex: 1,
          overflow: 'auto',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px',
        }}
      >
        {messages.length === 0 && (
          <div style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexDirection: 'column',
            gap: '12px',
            color: 'var(--text-tertiary)',
          }}>
            <Sparkles size={32} />
            <div style={{ fontSize: '14px', fontWeight: 500 }}>Ask AI to make changes</div>
            <div style={{ fontSize: '13px', textAlign: 'center', maxWidth: '240px' }}>
              Try &quot;Make the header bigger&quot; or &quot;Add a contact form&quot;
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}

        {isLoading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '8px', color: 'var(--text-tertiary)', fontSize: '13px' }}>
            <Loader2 size={14} style={{ animation: 'spin 1.5s linear infinite' }} />
            AI is making changes...
          </div>
        )}
      </div>

      {/* Input */}
      <div style={{
        padding: '12px 16px',
        borderTop: '1px solid var(--border-light)',
      }}>
        <div style={{
          display: 'flex',
          gap: '8px',
          alignItems: 'flex-end',
        }}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe a change..."
            rows={1}
            style={{
              flex: 1,
              padding: '10px 14px',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-light)',
              backgroundColor: 'var(--bg-input)',
              fontSize: '14px',
              resize: 'none',
              outline: 'none',
              maxHeight: '120px',
            }}
            onInput={(e) => {
              const target = e.target as HTMLTextAreaElement;
              target.style.height = 'auto';
              target.style.height = `${Math.min(target.scrollHeight, 120)}px`;
            }}
          />
          <button
            onClick={sendMessage}
            disabled={!input.trim() || isLoading}
            style={{
              width: '40px',
              height: '40px',
              borderRadius: 'var(--radius-md)',
              backgroundColor: input.trim() ? 'var(--accent)' : 'var(--bg-tertiary)',
              color: input.trim() ? 'white' : 'var(--text-tertiary)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
              cursor: input.trim() ? 'pointer' : 'default',
            }}
          >
            <Send size={16} />
          </button>
        </div>
      </div>

      <style>{`
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === 'user';
  return (
    <div style={{
      display: 'flex',
      gap: '10px',
      alignItems: 'flex-start',
    }}>
      <div style={{
        width: '28px',
        height: '28px',
        borderRadius: '50%',
        backgroundColor: isUser ? 'var(--bg-tertiary)' : 'var(--bg-accent-light)',
        color: isUser ? 'var(--text-secondary)' : 'var(--accent)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
      }}>
        {isUser ? <User size={14} /> : <Sparkles size={14} />}
      </div>
      <div style={{
        fontSize: '14px',
        lineHeight: 1.6,
        color: 'var(--text-primary)',
        paddingTop: '4px',
      }}>
        {message.content}
      </div>
    </div>
  );
}
