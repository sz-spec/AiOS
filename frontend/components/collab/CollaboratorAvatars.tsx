'use client';

import { useState } from 'react';
import { UserPlus, X } from 'lucide-react';

interface Collaborator {
  id: string;
  email: string;
  name: string;
  role: 'admin' | 'editor' | 'viewer';
  online: boolean;
  color: string;
}

interface CollaboratorAvatarsProps {
  projectId: string;
}

const AVATAR_COLORS = ['#3b82f6', '#8b5cf6', '#ec4899', '#10b981', '#f59e0b', '#ef4444'];

export function CollaboratorAvatars({ projectId }: CollaboratorAvatarsProps) {
  const [collaborators, setCollaborators] = useState<Collaborator[]>([]);
  const [showInvite, setShowInvite] = useState(false);
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<'editor' | 'viewer'>('editor');

  const invite = async () => {
    if (!inviteEmail.trim()) return;
    try {
      await fetch('/api/v1/collab/invite', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          email: inviteEmail,
          role: inviteRole,
        }),
      });
      setCollaborators((prev) => [...prev, {
        id: `user-${Date.now()}`,
        email: inviteEmail,
        name: inviteEmail.split('@')[0],
        role: inviteRole,
        online: false,
        color: AVATAR_COLORS[prev.length % AVATAR_COLORS.length],
      }]);
      setInviteEmail('');
      setShowInvite(false);
    } catch { /* ignore */ }
  };

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
      {/* Online indicators */}
      {collaborators.map((c) => (
        <div
          key={c.id}
          title={`${c.name} (${c.role})${c.online ? ' - online' : ''}`}
          style={{
            width: '28px',
            height: '28px',
            borderRadius: '50%',
            backgroundColor: c.color,
            color: 'white',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '11px',
            fontWeight: 600,
            border: c.online ? '2px solid #059669' : '2px solid var(--bg-secondary)',
            marginLeft: '-4px',
          }}
        >
          {c.name[0].toUpperCase()}
        </div>
      ))}

      {/* Invite button */}
      <div style={{ position: 'relative' }}>
        <button
          onClick={() => setShowInvite(!showInvite)}
          style={{
            width: '28px',
            height: '28px',
            borderRadius: '50%',
            border: '1px dashed var(--border-medium)',
            backgroundColor: 'transparent',
            color: 'var(--text-tertiary)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginLeft: '4px',
          }}
        >
          <UserPlus size={13} />
        </button>

        {showInvite && (
          <div style={{
            position: 'absolute',
            top: '36px',
            right: 0,
            width: '280px',
            padding: '16px',
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border-light)',
            borderRadius: 'var(--radius-md)',
            boxShadow: 'var(--shadow-lg)',
            zIndex: 50,
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
              <span style={{ fontSize: '14px', fontWeight: 600 }}>Invite</span>
              <button onClick={() => setShowInvite(false)} style={{ padding: '2px', backgroundColor: 'transparent', color: 'var(--text-tertiary)' }}>
                <X size={14} />
              </button>
            </div>
            <input
              value={inviteEmail}
              onChange={(e) => setInviteEmail(e.target.value)}
              placeholder="Email address"
              style={{
                width: '100%', padding: '8px 10px', borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-light)', fontSize: '13px', marginBottom: '8px',
              }}
            />
            <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
              {(['editor', 'viewer'] as const).map((role) => (
                <button
                  key={role}
                  onClick={() => setInviteRole(role)}
                  style={{
                    flex: 1, padding: '6px', borderRadius: 'var(--radius-sm)',
                    border: `1px solid ${inviteRole === role ? 'var(--accent)' : 'var(--border-light)'}`,
                    backgroundColor: inviteRole === role ? 'var(--bg-accent-light)' : 'transparent',
                    fontSize: '12px', fontWeight: 500, textTransform: 'capitalize',
                  }}
                >
                  {role}
                </button>
              ))}
            </div>
            <button
              onClick={invite}
              style={{
                width: '100%', padding: '8px', borderRadius: 'var(--radius-sm)',
                backgroundColor: 'var(--accent)', color: 'white', fontSize: '13px', fontWeight: 500,
              }}
            >
              Send Invite
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
