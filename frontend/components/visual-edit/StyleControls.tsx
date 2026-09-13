'use client';

import { useVisualEditStore } from '@/lib/store/visualEditStore';

const fontFamilies = [
  'Inter, sans-serif',
  'Arial, sans-serif',
  'Georgia, serif',
  'Courier New, monospace',
  'system-ui, sans-serif',
];

const fontSizes = ['12px', '14px', '16px', '18px', '20px', '24px', '28px', '32px', '40px', '48px'];

export function StyleControls() {
  const { selectedElement, applyStyleChange } = useVisualEditStore();

  if (!selectedElement) return null;

  const { styles } = selectedElement;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      {/* Colors */}
      <div>
        <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
          Text Color
        </label>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <input
            type="color"
            value={styles.color || '#000000'}
            onChange={(e) => applyStyleChange('color', e.target.value)}
            style={{ width: '32px', height: '32px', border: '1px solid var(--border-light)', borderRadius: '4px', padding: '2px', cursor: 'pointer' }}
          />
          <input
            type="text"
            value={styles.color || ''}
            onChange={(e) => applyStyleChange('color', e.target.value)}
            style={{ flex: 1, padding: '6px 10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)', fontSize: '12px' }}
          />
        </div>
      </div>

      <div>
        <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
          Background Color
        </label>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <input
            type="color"
            value={styles.backgroundColor || '#ffffff'}
            onChange={(e) => applyStyleChange('backgroundColor', e.target.value)}
            style={{ width: '32px', height: '32px', border: '1px solid var(--border-light)', borderRadius: '4px', padding: '2px', cursor: 'pointer' }}
          />
          <input
            type="text"
            value={styles.backgroundColor || ''}
            onChange={(e) => applyStyleChange('backgroundColor', e.target.value)}
            style={{ flex: 1, padding: '6px 10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)', fontSize: '12px' }}
          />
        </div>
      </div>

      {/* Typography */}
      <div>
        <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
          Font Size
        </label>
        <select
          value={styles.fontSize || '16px'}
          onChange={(e) => applyStyleChange('fontSize', e.target.value)}
          style={{ width: '100%', padding: '6px 10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)', fontSize: '13px', backgroundColor: 'var(--bg-input)' }}
        >
          {fontSizes.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      </div>

      <div>
        <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
          Font Family
        </label>
        <select
          value={styles.fontFamily || 'Inter, sans-serif'}
          onChange={(e) => applyStyleChange('fontFamily', e.target.value)}
          style={{ width: '100%', padding: '6px 10px', borderRadius: 'var(--radius-sm)', border: '1px solid var(--border-light)', fontSize: '13px', backgroundColor: 'var(--bg-input)' }}
        >
          {fontFamilies.map((f) => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>

      {/* Spacing */}
      <div>
        <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
          Padding
        </label>
        <input
          type="range"
          min="0"
          max="64"
          value={parseInt(styles.padding) || 0}
          onChange={(e) => applyStyleChange('padding', `${e.target.value}px`)}
          style={{ width: '100%' }}
        />
        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', textAlign: 'right' }}>{styles.padding || '0px'}</div>
      </div>

      <div>
        <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
          Margin
        </label>
        <input
          type="range"
          min="0"
          max="64"
          value={parseInt(styles.margin) || 0}
          onChange={(e) => applyStyleChange('margin', `${e.target.value}px`)}
          style={{ width: '100%' }}
        />
        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', textAlign: 'right' }}>{styles.margin || '0px'}</div>
      </div>

      <div>
        <label style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-secondary)', marginBottom: '6px', display: 'block' }}>
          Border Radius
        </label>
        <input
          type="range"
          min="0"
          max="32"
          value={parseInt(styles.borderRadius) || 0}
          onChange={(e) => applyStyleChange('borderRadius', `${e.target.value}px`)}
          style={{ width: '100%' }}
        />
        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', textAlign: 'right' }}>{styles.borderRadius || '0px'}</div>
      </div>
    </div>
  );
}
