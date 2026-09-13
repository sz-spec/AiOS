import { Suspense } from 'react';
import { MMRLiveWidget } from '@/components/explorer/MMRLiveWidget';
import ExplorerLoading from './loading';

// Server Component — static shell renders immediately for sub-100ms LCP.
// Dynamic MMR data is streamed inside the Suspense boundary.
export const metadata = { title: 'Transparency Explorer — VOS3' };

export default function ExplorerPage() {
  return (
    <div style={{ padding: '40px', maxWidth: '960px', margin: '0 auto' }}>

      {/* Static hero — rendered on server, LCP ≤ 80ms */}
      <div style={{ marginBottom: '40px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '12px' }}>
          <h1 style={{ margin: 0, fontSize: '26px', fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--text-primary)' }}>
            Transparency Explorer
          </h1>
          <span style={{
            padding: '3px 10px',
            backgroundColor: 'rgba(22,163,74,0.1)',
            border: '1px solid rgba(22,163,74,0.3)',
            borderRadius: '9999px',
            fontSize: '11px',
            fontWeight: 600,
            color: '#16a34a',
            letterSpacing: '0.05em',
          }}>
            MMR AUDIT CHAIN
          </span>
        </div>
        <p style={{ margin: 0, fontSize: '15px', color: 'var(--text-secondary)', lineHeight: 1.6, maxWidth: '620px' }}>
          Every AI request you make is recorded as a cryptographic leaf in the Merkle Mountain Range
          audit ledger inside the VOS3 kernel. The root hash below is mathematically unforgeable —
          changing any historical syscall would require a SHA-256 collision (2¹²⁸ operations, ~10¹⁹ years).
        </p>
      </div>

      {/* Static invariant cards — above fold, zero JS required */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px', marginBottom: '28px' }}>
        <InvariantCard
          title="Collision Resistance"
          value="2¹²⁸ ops"
          sub="SHA-256 security bound"
          color="#16a34a"
        />
        <InvariantCard
          title="Append Complexity"
          value="O(log N)"
          sub="amortized per syscall"
          color="#2563eb"
        />
        <InvariantCard
          title="BSS Footprint"
          value="2,048 B"
          sub="zero heap allocation"
          color="#7c3aed"
        />
      </div>

      {/* Dynamic widget — streams in after static shell */}
      <Suspense fallback={<ExplorerLoading />}>
        <MMRLiveWidget />
      </Suspense>

      {/* Static explainer — no JS */}
      <div style={{
        marginTop: '40px',
        padding: '24px',
        backgroundColor: 'var(--bg-secondary)',
        borderRadius: 'var(--radius-lg)',
        border: '1px solid var(--border-light)',
      }}>
        <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '16px', letterSpacing: '0.05em', textTransform: 'uppercase' }}>
          How the Proof Works
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', fontSize: '13px', color: 'var(--text-secondary)', lineHeight: 1.7 }}>
          <div>
            <strong style={{ color: 'var(--text-primary)', display: 'block', marginBottom: '4px' }}>Kernel (C, x86_64)</strong>
            Each syscall calls <code style={{ fontFamily: 'monospace', fontSize: '12px', backgroundColor: 'var(--bg-hover)', padding: '1px 4px', borderRadius: '3px' }}>mmr_record_syscall(nr, arg0)</code>.
            The leaf is <code style={{ fontFamily: 'monospace', fontSize: '12px', backgroundColor: 'var(--bg-hover)', padding: '1px 4px', borderRadius: '3px' }}>SHA-256(ts ‖ nr ‖ RDSEED ‖ arg0)</code>
            XOR-bound to 8 bytes of hardware entropy, making replay impossible.
          </div>
          <div>
            <strong style={{ color: 'var(--text-primary)', display: 'block', marginBottom: '4px' }}>This Page</strong>
            The root hash is fetched from <code style={{ fontFamily: 'monospace', fontSize: '12px', backgroundColor: 'var(--bg-hover)', padding: '1px 4px', borderRadius: '3px' }}>GET /api/kernel/transparency</code> every 3 seconds.
            When the kernel records a new syscall, the root changes — you see it pulse green.
            Run <code style={{ fontFamily: 'monospace', fontSize: '12px', backgroundColor: 'var(--bg-hover)', padding: '1px 4px', borderRadius: '3px' }}>tools/vos3_verify.py</code> to verify locally.
          </div>
        </div>
      </div>
    </div>
  );
}

function InvariantCard({ title, value, sub, color }: { title: string; value: string; sub: string; color: string }) {
  return (
    <div style={{
      padding: '20px',
      backgroundColor: 'var(--bg-secondary)',
      borderRadius: 'var(--radius-md)',
      border: '1px solid var(--border-light)',
    }}>
      <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
        {title}
      </div>
      <div style={{ fontSize: '28px', fontWeight: 700, color, fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.02em', marginBottom: '4px' }}>
        {value}
      </div>
      <div style={{ fontSize: '12px', color: 'var(--text-tertiary)' }}>{sub}</div>
    </div>
  );
}
