export default function ExplorerLoading() {
  return (
    <div style={{ padding: '40px', maxWidth: '960px', margin: '0 auto' }}>
      {/* Header skeleton */}
      <div style={{ marginBottom: '40px' }}>
        <div style={{ width: '280px', height: '32px', backgroundColor: 'var(--bg-hover)', borderRadius: '6px', marginBottom: '12px', animation: 'pulse 1.5s ease-in-out infinite' }} />
        <div style={{ width: '520px', height: '18px', backgroundColor: 'var(--bg-hover)', borderRadius: '4px', opacity: 0.6, animation: 'pulse 1.5s ease-in-out infinite' }} />
      </div>
      {/* Cards skeleton */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '16px', marginBottom: '28px' }}>
        {[1, 2, 3].map((i) => (
          <div key={i} style={{ height: '120px', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-light)', animation: 'pulse 1.5s ease-in-out infinite' }} />
        ))}
      </div>
      <div style={{ height: '260px', backgroundColor: 'var(--bg-secondary)', borderRadius: 'var(--radius-lg)', border: '1px solid var(--border-light)', animation: 'pulse 1.5s ease-in-out infinite' }} />
      <style>{`@keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.5} }`}</style>
    </div>
  );
}
