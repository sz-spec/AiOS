'use client';

import { useState, useEffect } from 'react';
import { Shield, User, Clock } from 'lucide-react';

interface AuditEntry {
  id: string;
  user: string;
  action: string;
  resource: string;
  timestamp: string;
}

interface AuditLogProps {
  teamId?: string;
}

export function AuditLog({ teamId }: AuditLogProps) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const res = await fetch(`/api/v1/audit/log${teamId ? `?team_id=${teamId}` : ''}`);
        if (res.ok) {
          const data = await res.json();
          setEntries(data.entries || []);
        }
      } catch { /* ignore */ }
      setIsLoading(false);
    }
    load();
  }, [teamId]);

  return (
    <div style={{ padding: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '24px' }}>
        <Shield size={20} style={{ color: 'var(--accent)' }} />
        <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Audit Log</h3>
      </div>

      {isLoading ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>Loading...</div>
      ) : entries.length === 0 ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: '14px' }}>No activity recorded yet.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0' }}>
          {entries.map((entry) => (
            <div key={entry.id} style={{
              display: 'flex',
              alignItems: 'center',
              gap: '12px',
              padding: '12px 0',
              borderBottom: '1px solid var(--border-light)',
              fontSize: '13px',
            }}>
              <div style={{
                width: '28px', height: '28px', borderRadius: '50%',
                backgroundColor: 'var(--bg-tertiary)', display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <User size={14} style={{ color: 'var(--text-tertiary)' }} />
              </div>
              <div style={{ flex: 1 }}>
                <span style={{ fontWeight: 500 }}>{entry.user}</span>
                {' '}{entry.action}{' '}
                <span style={{ fontWeight: 500 }}>{entry.resource}</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '4px', color: 'var(--text-tertiary)', fontSize: '12px' }}>
                <Clock size={12} />
                {new Date(entry.timestamp).toLocaleString()}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
