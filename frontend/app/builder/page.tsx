'use client';

import { useState, useCallback } from 'react';
import { useCodegen } from '@/hooks/useCodegen';
import { VoiceInput } from '@/components/shared/VoiceInput';

const LANGUAGES = [
  { id: 'typescript', label: 'TypeScript' },
  { id: 'python', label: 'Python' },
  { id: 'javascript', label: 'JavaScript' },
  { id: 'react', label: 'React' },
  { id: 'nodejs', label: 'Node.js' },
  { id: 'html', label: 'HTML' },
  { id: 'css', label: 'CSS' },
  { id: 'sql', label: 'SQL' },
];

function CopyIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function DownloadIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="7 10 12 15 17 10" />
      <line x1="12" y1="15" x2="12" y2="3" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z" />
      <polyline points="13 2 13 9 20 9" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}

export default function BuilderPage() {
  const { generateCode, generatedFiles, isGenerating, progress, error } = useCodegen();
  const [prompt, setPrompt] = useState('');
  const [language, setLanguage] = useState('typescript');
  const [activeFile, setActiveFile] = useState<number>(0);

  const handleGenerate = () => {
    if (prompt.trim()) {
      generateCode({ prompt: prompt.trim(), language });
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && e.metaKey) {
      e.preventDefault();
      handleGenerate();
    }
  };

  const copyToClipboard = () => {
    if (generatedFiles[activeFile]) {
      navigator.clipboard.writeText(generatedFiles[activeFile].content);
    }
  };

  const handleVoiceTranscript = useCallback((transcript: string) => {
    setPrompt(prev => prev ? `${prev} ${transcript}` : transcript);
  }, []);

  return (
    <div style={{ display: 'flex', height: '100vh', backgroundColor: 'var(--bg-primary)' }}>
      {/* Left Panel - Input */}
      <aside
        style={{
          width: '340px',
          borderRight: '1px solid var(--border-light)',
          display: 'flex',
          flexDirection: 'column',
          backgroundColor: 'var(--bg-secondary)',
        }}
      >
        <div style={{ padding: '20px', borderBottom: '1px solid var(--border-light)' }}>
          <h2 style={{ margin: '0 0 4px', fontSize: '18px', fontWeight: 600 }}>Code Builder</h2>
          <p style={{ margin: 0, fontSize: '13px', color: 'var(--text-secondary)' }}>
            Generate code from natural language
          </p>
        </div>

        <div style={{ flex: 1, overflow: 'auto', padding: '20px' }}>
          <div style={{ marginBottom: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <label style={{ fontSize: '13px', fontWeight: 500 }}>
                Describe what to build
              </label>
              <VoiceInput
                onTranscript={handleVoiceTranscript}
                disabled={isGenerating}
                size="small"
              />
            </div>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Create a function that validates email addresses..."
              style={{
                width: '100%',
                height: '140px',
                padding: '12px',
                backgroundColor: 'var(--bg-primary)',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
                fontSize: '14px',
                lineHeight: 1.5,
                resize: 'vertical',
                outline: 'none',
              }}
            />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', marginBottom: '8px', fontSize: '13px', fontWeight: 500 }}>
              Language
            </label>
            <select
              value={language}
              onChange={(e) => setLanguage(e.target.value)}
              style={{
                width: '100%',
                padding: '10px 12px',
                backgroundColor: 'var(--bg-primary)',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-md)',
                color: 'var(--text-primary)',
                fontSize: '14px',
                outline: 'none',
              }}
            >
              {LANGUAGES.map((l) => (
                <option key={l.id} value={l.id}>{l.label}</option>
              ))}
            </select>
          </div>

          <button
            onClick={handleGenerate}
            disabled={isGenerating || !prompt.trim()}
            style={{
              width: '100%',
              padding: '12px',
              backgroundColor: 'var(--accent)',
              borderRadius: 'var(--radius-md)',
              color: 'white',
              fontSize: '14px',
              fontWeight: 500,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
              opacity: isGenerating || !prompt.trim() ? 0.6 : 1,
            }}
          >
            <PlayIcon />
            {isGenerating ? 'Generating...' : 'Generate Code'}
          </button>

          <p style={{ marginTop: '12px', fontSize: '12px', color: 'var(--text-tertiary)', textAlign: 'center' }}>
            Press Cmd+Enter to generate
          </p>

          {/* Progress */}
          {isGenerating && progress && (
            <div
              style={{
                marginTop: '20px',
                padding: '16px',
                backgroundColor: 'var(--bg-primary)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '8px', fontSize: '13px' }}>
                <span style={{ fontWeight: 500 }}>{progress.stage}</span>
                <span style={{ color: 'var(--text-secondary)' }}>{progress.percent}%</span>
              </div>
              <div style={{ height: '4px', backgroundColor: 'var(--bg-tertiary)', borderRadius: '2px', overflow: 'hidden' }}>
                <div
                  style={{
                    height: '100%',
                    width: `${progress.percent}%`,
                    backgroundColor: 'var(--accent)',
                    transition: 'width 0.3s',
                  }}
                />
              </div>
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                {progress.message}
              </div>
            </div>
          )}

          {error && (
            <div
              style={{
                marginTop: '20px',
                padding: '14px',
                backgroundColor: 'rgba(220, 38, 38, 0.1)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--error)',
                color: 'var(--error)',
                fontSize: '13px',
              }}
            >
              {error}
            </div>
          )}
        </div>

        {/* Files List */}
        <div style={{ borderTop: '1px solid var(--border-light)', padding: '16px' }}>
          <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-tertiary)', marginBottom: '12px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            Generated Files ({generatedFiles.length})
          </div>
          {generatedFiles.length === 0 ? (
            <div style={{ fontSize: '13px', color: 'var(--text-tertiary)' }}>No files yet</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {generatedFiles.map((file, i) => (
                <button
                  key={i}
                  onClick={() => setActiveFile(i)}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 10px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: activeFile === i ? 'var(--bg-hover)' : 'transparent',
                    color: activeFile === i ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontSize: '13px',
                    textAlign: 'left',
                  }}
                >
                  <FileIcon />
                  {file.path}
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>

      {/* Main - Code Display */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        {/* Toolbar */}
        <div
          style={{
            padding: '12px 20px',
            borderBottom: '1px solid var(--border-light)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <FileIcon />
            <span style={{ fontSize: '14px', fontWeight: 500 }}>
              {generatedFiles[activeFile]?.path || 'No file selected'}
            </span>
            {generatedFiles[activeFile] && (
              <span
                style={{
                  padding: '2px 8px',
                  backgroundColor: 'var(--bg-tertiary)',
                  borderRadius: 'var(--radius-full)',
                  fontSize: '11px',
                  color: 'var(--text-secondary)',
                  textTransform: 'uppercase',
                }}
              >
                {generatedFiles[activeFile].language}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', gap: '8px' }}>
            <button
              onClick={copyToClipboard}
              disabled={!generatedFiles[activeFile]}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 12px',
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-primary)',
                fontSize: '13px',
                opacity: generatedFiles[activeFile] ? 1 : 0.5,
              }}
            >
              <CopyIcon />
              Copy
            </button>
            <button
              disabled={!generatedFiles[activeFile]}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '6px 12px',
                backgroundColor: 'var(--bg-secondary)',
                border: '1px solid var(--border-light)',
                borderRadius: 'var(--radius-sm)',
                color: 'var(--text-primary)',
                fontSize: '13px',
                opacity: generatedFiles[activeFile] ? 1 : 0.5,
              }}
            >
              <DownloadIcon />
              Download
            </button>
          </div>
        </div>

        {/* Code */}
        <div style={{ flex: 1, overflow: 'auto', padding: '20px', backgroundColor: 'var(--bg-secondary)' }}>
          {generatedFiles[activeFile] ? (
            <pre
              style={{
                margin: 0,
                padding: '20px',
                backgroundColor: 'var(--bg-primary)',
                borderRadius: 'var(--radius-md)',
                border: '1px solid var(--border-light)',
                fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
                fontSize: '13px',
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                color: 'var(--text-primary)',
              }}
            >
              {generatedFiles[activeFile].content}
            </pre>
          ) : (
            <div style={{ textAlign: 'center', marginTop: '120px' }}>
              <div
                style={{
                  width: '64px',
                  height: '64px',
                  margin: '0 auto 20px',
                  borderRadius: 'var(--radius-lg)',
                  backgroundColor: 'var(--bg-tertiary)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--text-tertiary)',
                }}
              >
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="16 18 22 12 16 6" />
                  <polyline points="8 6 2 12 8 18" />
                </svg>
              </div>
              <h3 style={{ margin: '0 0 8px', fontSize: '18px', fontWeight: 600 }}>Ready to generate code</h3>
              <p style={{ margin: 0, fontSize: '14px', color: 'var(--text-secondary)' }}>
                Describe what you want to build in the left panel
              </p>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
