'use client';

export type StudioViewMode = 'auto' | 'wizard' | 'expert' | 'party';
interface ViewModeSelectorProps {
  viewMode: StudioViewMode;
  onModeChange: (mode: Exclude<StudioViewMode, 'auto'>) => void;
  onBack: () => void;
}

export function ViewModeSelector({ viewMode, onModeChange, onBack }: ViewModeSelectorProps) {
  return (
    <div style={{
      position: 'fixed',
      top: '16px',
      right: '16px',
      zIndex: 50,
      display: 'flex',
      backgroundColor: 'var(--bg-primary)',
      borderRadius: '10px',
      boxShadow: '0 4px 12px rgba(0,0,0,0.1)',
      border: '1px solid var(--border-light)',
      overflow: 'hidden',
    }}>
      {[
        { mode: 'wizard' as const, label: 'Guided', icon: '&#127919;', color: 'var(--accent)' },
        { mode: 'expert' as const, label: 'Expert', icon: '&#128295;', color: '#8b5cf6' },
        { mode: 'party' as const, label: 'Party', icon: '&#127881;', color: '#a855f7' },
      ].map((item) => (
        <button
          key={item.mode}
          onClick={() => {
            onModeChange(item.mode);
          }}
          style={{
            padding: '8px 16px',
            fontSize: '13px',
            fontWeight: 500,
            border: 'none',
            cursor: 'pointer',
            transition: 'all 0.15s',
            backgroundColor: viewMode === item.mode ? item.color : 'transparent',
            color: viewMode === item.mode ? 'white' : 'var(--text-secondary)',
          }}
        >
          <span dangerouslySetInnerHTML={{ __html: item.icon }} /> {item.label}
        </button>
      ))}
      <button
        onClick={onBack}
        style={{
          padding: '8px 12px',
          fontSize: '13px',
          fontWeight: 500,
          border: 'none',
          borderLeft: '1px solid var(--border-light)',
          backgroundColor: 'transparent',
          color: 'var(--text-tertiary)',
          cursor: 'pointer',
        }}
        title="Back to projects"
      >
        &#8592;
      </button>
    </div>
  );
}
