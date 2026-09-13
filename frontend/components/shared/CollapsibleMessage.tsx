'use client';

import React, { useState, ReactNode, CSSProperties } from 'react';

interface CollapsibleMessageProps {
  content: string;
  isAssistant: boolean;
  agentName?: string;
  renderContent?: (text: string) => ReactNode;
  style?: CSSProperties;
}

const MAX_SHORT_LINES = 4;
const MAX_SHORT_CHARS = 300;
const PREVIEW_LINES = 3;
const SAVE_THRESHOLD = 1500;

export function CollapsibleMessage({
  content,
  isAssistant,
  agentName,
  renderContent,
  style,
}: CollapsibleMessageProps) {
  const [expanded, setExpanded] = useState(false);

  const lines = content.split('\n');
  const isShort = lines.length <= MAX_SHORT_LINES && content.length <= MAX_SHORT_CHARS;
  const shouldCollapse = isAssistant && !isShort;
  const showSave = isAssistant && content.length > SAVE_THRESHOLD;

  const renderText = (text: string) =>
    renderContent ? renderContent(text) : text;

  const handleSave = () => {
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${(agentName || 'agent').replace(/\s+/g, '-').toLowerCase()}-response.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // No collapse needed
  if (!shouldCollapse) {
    return (
      <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', ...style }}>
        {renderText(content)}
      </div>
    );
  }

  const previewText = lines.slice(0, PREVIEW_LINES).join('\n');

  return (
    <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', ...style }}>
      {expanded ? (
        <>
          {renderText(content)}
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '6px' }}>
            <button
              onClick={() => setExpanded(false)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--accent, #8B5CF6)',
                fontSize: '12px',
                fontWeight: 500,
                cursor: 'pointer',
                padding: '2px 0',
              }}
            >
              Collapse
            </button>
            {showSave && (
              <button
                onClick={handleSave}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-tertiary, #888)',
                  fontSize: '11px',
                  cursor: 'pointer',
                  padding: '2px 0',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '3px',
                }}
                title="Save as .md"
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                .md
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <div style={{ position: 'relative' }}>
            {renderText(previewText)}
            <div
              style={{
                position: 'absolute',
                bottom: 0,
                left: 0,
                right: 0,
                height: '24px',
                background: 'linear-gradient(transparent, var(--bg-primary, #0a0a0a))',
                pointerEvents: 'none',
              }}
            />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
            <button
              onClick={() => setExpanded(true)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--accent, #8B5CF6)',
                fontSize: '12px',
                fontWeight: 500,
                cursor: 'pointer',
                padding: '2px 0',
              }}
            >
              Elaborate
            </button>
            {showSave && (
              <button
                onClick={handleSave}
                style={{
                  background: 'none',
                  border: 'none',
                  color: 'var(--text-tertiary, #888)',
                  fontSize: '11px',
                  cursor: 'pointer',
                  padding: '2px 0',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '3px',
                }}
                title="Save as .md"
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                .md
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
