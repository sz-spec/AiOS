'use client';

import { X, Type, Palette, Move } from 'lucide-react';
import { useVisualEditStore } from '@/lib/store/visualEditStore';
import { StyleControls } from './StyleControls';
import { useState } from 'react';

type PanelTab = 'content' | 'style' | 'layout';

export function PropertyPanel() {
  const { selectedElement, selectElement, applyTextChange } = useVisualEditStore();
  const [activeTab, setActiveTab] = useState<PanelTab>('style');

  if (!selectedElement) return null;

  const tabs: { id: PanelTab; label: string; icon: typeof Type }[] = [
    { id: 'content', label: 'Content', icon: Type },
    { id: 'style', label: 'Style', icon: Palette },
    { id: 'layout', label: 'Layout', icon: Move },
  ];

  return (
    <div style={{
      width: '280px',
      borderLeft: '1px solid var(--border-light)',
      backgroundColor: 'var(--bg-primary)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        padding: '12px 16px',
        borderBottom: '1px solid var(--border-light)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ fontSize: '13px', fontWeight: 600 }}>
            &lt;{selectedElement.tagName.toLowerCase()}&gt;
          </div>
        </div>
        <button
          onClick={() => selectElement(null)}
          style={{ padding: '4px', color: 'var(--text-tertiary)', backgroundColor: 'transparent' }}
        >
          <X size={16} />
        </button>
      </div>

      {/* Tabs */}
      <div style={{
        display: 'flex',
        borderBottom: '1px solid var(--border-light)',
      }}>
        {tabs.map((tab) => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              style={{
                flex: 1,
                padding: '8px',
                fontSize: '12px',
                fontWeight: 500,
                color: isActive ? 'var(--accent)' : 'var(--text-tertiary)',
                borderBottom: isActive ? '2px solid var(--accent)' : '2px solid transparent',
                backgroundColor: 'transparent',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '4px',
              }}
            >
              <Icon size={13} />
              {tab.label}
            </button>
          );
        })}
      </div>

      {/* Panel Content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '16px' }}>
        {activeTab === 'content' && (
          <div>
            <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
              Text Content
            </label>
            <textarea
              value={selectedElement.text}
              onChange={(e) => applyTextChange(e.target.value)}
              style={{
                width: '100%',
                minHeight: '100px',
                padding: '10px',
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-light)',
                fontSize: '13px',
                resize: 'vertical',
                backgroundColor: 'var(--bg-input)',
              }}
            />
          </div>
        )}

        {activeTab === 'style' && <StyleControls />}

        {activeTab === 'layout' && (
          <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
            <div style={{ marginBottom: '12px' }}>
              <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-tertiary)', marginBottom: '4px' }}>Position</div>
              <div>
                x: {Math.round(selectedElement.rect.left)}px, y: {Math.round(selectedElement.rect.top)}px
              </div>
            </div>
            <div>
              <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-tertiary)', marginBottom: '4px' }}>Size</div>
              <div>
                {Math.round(selectedElement.rect.width)} x {Math.round(selectedElement.rect.height)}px
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Apply button */}
      <div style={{ padding: '12px 16px', borderTop: '1px solid var(--border-light)' }}>
        <button
          onClick={async () => {
            // Send visual edit to backend
            try {
              await fetch('/api/v1/code/apply-visual-edit', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  element: selectedElement,
                }),
              });
            } catch {
              // TODO: error handling
            }
          }}
          style={{
            width: '100%',
            padding: '10px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'var(--accent)',
            color: 'white',
            fontSize: '13px',
            fontWeight: 500,
          }}
        >
          Apply Changes
        </button>
      </div>
    </div>
  );
}
