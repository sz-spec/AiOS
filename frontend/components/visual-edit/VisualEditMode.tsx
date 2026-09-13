'use client';

import { useVisualEditStore } from '@/lib/store/visualEditStore';
import { PropertyPanel } from './PropertyPanel';

interface VisualEditModeProps {
  children: React.ReactNode;
}

/**
 * Wraps the preview area to enable visual editing.
 * When active, clicking elements in the preview selects them for property editing.
 * The PropertyPanel slides in from the right to show controls.
 */
export function VisualEditMode({ children }: VisualEditModeProps) {
  const { isActive, selectedElement } = useVisualEditStore();

  return (
    <div style={{ display: 'flex', flex: 1 }}>
      <div style={{ flex: 1, position: 'relative' }}>
        {children}
        {/* Visual edit overlay indicator */}
        {isActive && (
          <div style={{
            position: 'absolute',
            top: '8px',
            left: '50%',
            transform: 'translateX(-50%)',
            padding: '4px 12px',
            borderRadius: 'var(--radius-full)',
            backgroundColor: 'var(--accent)',
            color: 'white',
            fontSize: '11px',
            fontWeight: 600,
            pointerEvents: 'none',
            zIndex: 10,
            opacity: 0.9,
          }}>
            Visual Edit Mode — Click any element to edit
          </div>
        )}
      </div>
      {isActive && selectedElement && <PropertyPanel />}
    </div>
  );
}
