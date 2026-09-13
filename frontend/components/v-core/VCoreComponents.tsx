/**
 * V Core UI Components
 * 
 * Part 1: Control Plane & Business Core
 * - Organization management
 * - Team members
 * - Entity/Data management
 * - Record views
 */

'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useVCore, Organization, Member, EntityDefinition, VCoreRecord } from '../../hooks/useVCore';
import { VoiceInput } from '../shared/VoiceInput';

// ============================================
// Control Plane Components
// ============================================

interface OrganizationCardProps {
  org: Organization;
  isSelected?: boolean;
  onClick?: () => void;
}

export function OrganizationCard({ org, isSelected, onClick }: OrganizationCardProps) {
  const planColors: Record<string, string> = {
    free: '#6b7280',
    starter: '#3b82f6',
    professional: '#8b5cf6',
    enterprise: '#f59e0b',
  };

  return (
    <div
      onClick={onClick}
      style={{
        padding: '1rem',
        background: isSelected ? 'var(--color-accent-light, #e5f1ff)' : 'var(--color-background, #fff)',
        border: `2px solid ${isSelected ? 'var(--color-accent, #007AFF)' : 'var(--color-border, #e5e5ea)'}`,
        borderRadius: '12px',
        cursor: 'pointer',
        transition: 'all 0.2s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
        <div
          style={{
            width: '40px',
            height: '40px',
            borderRadius: '10px',
            background: 'linear-gradient(135deg, #007AFF, #5856D6)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'white',
            fontWeight: 600,
            fontSize: '1.125rem',
          }}
        >
          {org.name.charAt(0).toUpperCase()}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: '1rem' }}>{org.name}</div>
          <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
            {org.slug}
          </div>
        </div>
        <span
          style={{
            padding: '0.25rem 0.625rem',
            background: planColors[org.plan] + '20',
            color: planColors[org.plan],
            borderRadius: '999px',
            fontSize: '0.75rem',
            fontWeight: 500,
            textTransform: 'capitalize',
          }}
        >
          {org.plan}
        </span>
      </div>
      <div
        style={{
          display: 'flex',
          gap: '1rem',
          marginTop: '0.75rem',
          fontSize: '0.8125rem',
          color: 'var(--color-text-secondary, #6e6e73)',
        }}
      >
        <span>👥 {org.maxUsers} users</span>
        <span>🤖 {org.maxAgents} agents</span>
        <span>⚡ {org.maxWorkflows} workflows</span>
      </div>
    </div>
  );
}

interface MemberListProps {
  members: Member[];
  onInvite?: () => void;
}

export function MemberList({ members, onInvite }: MemberListProps) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
        <h3 style={{ margin: 0, fontSize: '1.125rem', fontWeight: 600 }}>Team Members</h3>
        {onInvite && (
          <button
            onClick={onInvite}
            style={{
              padding: '0.5rem 1rem',
              background: 'var(--color-accent, #007AFF)',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              fontSize: '0.875rem',
              fontWeight: 500,
              cursor: 'pointer',
            }}
          >
            + Invite
          </button>
        )}
      </div>
      {members.map((member) => (
        <div
          key={member.id}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '0.75rem',
            padding: '0.75rem',
            background: 'var(--color-background-secondary, #f5f5f7)',
            borderRadius: '10px',
          }}
        >
          <div
            style={{
              width: '36px',
              height: '36px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, #34C759, #30D158)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'white',
              fontWeight: 600,
              fontSize: '0.875rem',
            }}
          >
            {member.user.name.charAt(0).toUpperCase()}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontWeight: 500, fontSize: '0.9375rem' }}>{member.user.name}</div>
            <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
              {member.user.email}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <span
              style={{
                padding: '0.25rem 0.5rem',
                background: member.isOwner ? '#fef3c7' : '#e5e7eb',
                color: member.isOwner ? '#92400e' : '#374151',
                borderRadius: '6px',
                fontSize: '0.75rem',
                fontWeight: 500,
              }}
            >
              {member.isOwner ? '👑 Owner' : member.role?.name || 'Member'}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

interface InviteModalProps {
  isOpen: boolean;
  onClose: () => void;
  onInvite: (email: string, roleId: string) => void;
  roles: { id: string; name: string }[];
}

export function InviteModal({ isOpen, onClose, onInvite, roles }: InviteModalProps) {
  const [email, setEmail] = useState('');
  const [roleId, setRoleId] = useState('');

  if (!isOpen) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.4)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: 'white',
          borderRadius: '16px',
          padding: '1.5rem',
          width: '100%',
          maxWidth: '400px',
          boxShadow: '0 20px 60px rgba(0,0,0,0.2)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3 style={{ margin: '0 0 1rem', fontSize: '1.25rem', fontWeight: 600 }}>Invite Team Member</h3>
        
        <div style={{ marginBottom: '1rem' }}>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem', fontWeight: 500 }}>
            Email Address
          </label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="colleague@company.com"
            style={{
              width: '100%',
              padding: '0.75rem',
              border: '1px solid var(--color-border, #d2d2d7)',
              borderRadius: '8px',
              fontSize: '1rem',
              boxSizing: 'border-box',
            }}
          />
        </div>

        <div style={{ marginBottom: '1.5rem' }}>
          <label style={{ display: 'block', marginBottom: '0.5rem', fontSize: '0.875rem', fontWeight: 500 }}>
            Role
          </label>
          <select
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
            style={{
              width: '100%',
              padding: '0.75rem',
              border: '1px solid var(--color-border, #d2d2d7)',
              borderRadius: '8px',
              fontSize: '1rem',
              background: 'white',
            }}
          >
            <option value="">Select a role</option>
            {roles.map((role) => (
              <option key={role.id} value={role.id}>{role.name}</option>
            ))}
          </select>
        </div>

        <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end' }}>
          <button
            onClick={onClose}
            style={{
              padding: '0.75rem 1.25rem',
              background: 'var(--color-background-secondary, #f5f5f7)',
              border: 'none',
              borderRadius: '8px',
              fontSize: '0.9375rem',
              cursor: 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={() => {
              onInvite(email, roleId);
              setEmail('');
              setRoleId('');
              onClose();
            }}
            disabled={!email || !roleId}
            style={{
              padding: '0.75rem 1.25rem',
              background: 'var(--color-accent, #007AFF)',
              color: 'white',
              border: 'none',
              borderRadius: '8px',
              fontSize: '0.9375rem',
              fontWeight: 500,
              cursor: 'pointer',
              opacity: !email || !roleId ? 0.5 : 1,
            }}
          >
            Send Invite
          </button>
        </div>
      </div>
    </div>
  );
}

// ============================================
// Business Core Components
// ============================================

interface EntityCardProps {
  entity: EntityDefinition;
  recordCount?: number;
  onClick?: () => void;
}

export function EntityCard({ entity, recordCount = 0, onClick }: EntityCardProps) {
  return (
    <div
      onClick={onClick}
      style={{
        padding: '1.25rem',
        background: 'white',
        border: '1px solid var(--color-border, #e5e5ea)',
        borderRadius: '12px',
        cursor: 'pointer',
        transition: 'all 0.2s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.75rem' }}>
        <span
          style={{
            width: '40px',
            height: '40px',
            borderRadius: '10px',
            background: entity.color + '20',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '1.25rem',
          }}
        >
          {entity.icon}
        </span>
        <div>
          <div style={{ fontWeight: 600, fontSize: '1rem' }}>{entity.label}</div>
          <div style={{ fontSize: '0.8125rem', color: 'var(--color-text-secondary, #6e6e73)' }}>
            {entity.labelPlural}
          </div>
        </div>
      </div>
      <div style={{ fontSize: '0.875rem', color: 'var(--color-text-secondary, #6e6e73)', marginBottom: '0.75rem' }}>
        {entity.description || 'No description'}
      </div>
      <div style={{ display: 'flex', gap: '1rem', fontSize: '0.8125rem', color: 'var(--color-text-tertiary, #86868b)' }}>
        <span>📋 {entity.fields.length} fields</span>
        <span>📊 {recordCount} records</span>
        <span>👁️ {entity.defaultView}</span>
      </div>
    </div>
  );
}

interface RecordTableProps {
  entity: EntityDefinition;
  records: VCoreRecord[];
  onRowClick?: (record: VCoreRecord) => void;
  onDelete?: (recordId: string) => void;
}

export function RecordTable({ entity, records, onRowClick, onDelete }: RecordTableProps) {
  const visibleFields = entity.fields.filter(f => !f.name.startsWith('created_') && !f.name.startsWith('updated_')).slice(0, 5);

  const renderCellValue = (value: any, field: any) => {
    if (value === null || value === undefined) return '-';
    
    if (field.type === 'select' && field.options) {
      const option = field.options.find((o: any) => o.value === value);
      if (option) {
        return (
          <span
            style={{
              padding: '0.25rem 0.5rem',
              background: option.color ? option.color + '20' : '#e5e7eb',
              color: option.color || '#374151',
              borderRadius: '6px',
              fontSize: '0.75rem',
              fontWeight: 500,
            }}
          >
            {option.label}
          </span>
        );
      }
    }
    
    if (field.type === 'boolean') {
      return value ? '✓' : '✗';
    }
    
    return String(value);
  };

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.875rem' }}>
        <thead>
          <tr style={{ background: 'var(--color-background-secondary, #f5f5f7)' }}>
            {visibleFields.map((field) => (
              <th
                key={field.id}
                style={{
                  padding: '0.75rem 1rem',
                  textAlign: 'left',
                  fontWeight: 600,
                  borderBottom: '1px solid var(--color-border, #e5e5ea)',
                }}
              >
                {field.label}
              </th>
            ))}
            {onDelete && (
              <th style={{ padding: '0.75rem 1rem', width: '60px', borderBottom: '1px solid var(--color-border, #e5e5ea)' }} />
            )}
          </tr>
        </thead>
        <tbody>
          {records.map((record) => (
            <tr
              key={record.id}
              onClick={() => onRowClick?.(record)}
              style={{ cursor: onRowClick ? 'pointer' : 'default' }}
            >
              {visibleFields.map((field) => (
                <td
                  key={field.id}
                  style={{
                    padding: '0.75rem 1rem',
                    borderBottom: '1px solid var(--color-border-light, #e5e5ea)',
                  }}
                >
                  {renderCellValue(record.data[field.name], field)}
                </td>
              ))}
              {onDelete && (
                <td style={{ padding: '0.75rem 1rem', borderBottom: '1px solid var(--color-border-light, #e5e5ea)' }}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDelete(record.id);
                    }}
                    style={{
                      padding: '0.25rem 0.5rem',
                      background: '#fee2e2',
                      color: '#dc2626',
                      border: 'none',
                      borderRadius: '4px',
                      fontSize: '0.75rem',
                      cursor: 'pointer',
                    }}
                  >
                    🗑
                  </button>
                </td>
              )}
            </tr>
          ))}
          {records.length === 0 && (
            <tr>
              <td
                colSpan={visibleFields.length + (onDelete ? 1 : 0)}
                style={{ padding: '2rem', textAlign: 'center', color: 'var(--color-text-tertiary, #86868b)' }}
              >
                No records yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

interface RecordFormProps {
  entity: EntityDefinition;
  initialData?: Record<string, any>;
  onSubmit: (data: Record<string, any>) => void;
  onCancel: () => void;
}

export function RecordForm({ entity, initialData, onSubmit, onCancel }: RecordFormProps) {
  const [formData, setFormData] = useState<Record<string, any>>(initialData || {});
  const editableFields = entity.fields.filter(f =>
    !['created_at', 'updated_at', 'created_by', 'updated_by'].includes(f.type)
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(formData);
  };

  const handleVoiceTranscript = useCallback((fieldName: string) => (transcript: string) => {
    setFormData(prev => ({
      ...prev,
      [fieldName]: prev[fieldName] ? `${prev[fieldName]} ${transcript}` : transcript,
    }));
  }, []);

  // Check if field supports voice input
  const isTextBasedField = (fieldType: string) => {
    return ['text', 'short_text', 'long_text', 'email', 'url'].includes(fieldType);
  };

  return (
    <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {editableFields.map((field) => (
        <div key={field.id}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '0.5rem' }}>
            <label style={{ fontSize: '0.875rem', fontWeight: 500 }}>
              {field.label}
              {field.isRequired && <span style={{ color: '#ef4444' }}> *</span>}
            </label>
            {isTextBasedField(field.type) && (
              <VoiceInput
                onTranscript={handleVoiceTranscript(field.name)}
                size="small"
              />
            )}
          </div>
          
          {field.type === 'select' ? (
            <select
              value={formData[field.name] || ''}
              onChange={(e) => setFormData({ ...formData, [field.name]: e.target.value })}
              required={field.isRequired}
              style={{
                width: '100%',
                padding: '0.75rem',
                border: '1px solid var(--color-border, #d2d2d7)',
                borderRadius: '8px',
                fontSize: '1rem',
                background: 'white',
              }}
            >
              <option value="">Select...</option>
              {field.options?.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          ) : field.type === 'long_text' ? (
            <textarea
              value={formData[field.name] || ''}
              onChange={(e) => setFormData({ ...formData, [field.name]: e.target.value })}
              required={field.isRequired}
              rows={3}
              style={{
                width: '100%',
                padding: '0.75rem',
                border: '1px solid var(--color-border, #d2d2d7)',
                borderRadius: '8px',
                fontSize: '1rem',
                resize: 'vertical',
                boxSizing: 'border-box',
              }}
            />
          ) : field.type === 'boolean' ? (
            <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <input
                type="checkbox"
                checked={formData[field.name] || false}
                onChange={(e) => setFormData({ ...formData, [field.name]: e.target.checked })}
              />
              <span style={{ fontSize: '0.875rem' }}>Yes</span>
            </label>
          ) : field.type === 'number' || field.type === 'decimal' || field.type === 'currency' ? (
            <input
              type="number"
              value={formData[field.name] || ''}
              onChange={(e) => setFormData({ ...formData, [field.name]: Number(e.target.value) })}
              required={field.isRequired}
              step={field.type === 'number' ? 1 : 0.01}
              style={{
                width: '100%',
                padding: '0.75rem',
                border: '1px solid var(--color-border, #d2d2d7)',
                borderRadius: '8px',
                fontSize: '1rem',
                boxSizing: 'border-box',
              }}
            />
          ) : field.type === 'date' ? (
            <input
              type="date"
              value={formData[field.name] || ''}
              onChange={(e) => setFormData({ ...formData, [field.name]: e.target.value })}
              required={field.isRequired}
              style={{
                width: '100%',
                padding: '0.75rem',
                border: '1px solid var(--color-border, #d2d2d7)',
                borderRadius: '8px',
                fontSize: '1rem',
                boxSizing: 'border-box',
              }}
            />
          ) : (
            <input
              type={field.type === 'email' ? 'email' : field.type === 'phone' ? 'tel' : field.type === 'url' ? 'url' : 'text'}
              value={formData[field.name] || ''}
              onChange={(e) => setFormData({ ...formData, [field.name]: e.target.value })}
              required={field.isRequired}
              style={{
                width: '100%',
                padding: '0.75rem',
                border: '1px solid var(--color-border, #d2d2d7)',
                borderRadius: '8px',
                fontSize: '1rem',
                boxSizing: 'border-box',
              }}
            />
          )}
        </div>
      ))}

      <div style={{ display: 'flex', gap: '0.75rem', justifyContent: 'flex-end', marginTop: '0.5rem' }}>
        <button
          type="button"
          onClick={onCancel}
          style={{
            padding: '0.75rem 1.25rem',
            background: 'var(--color-background-secondary, #f5f5f7)',
            border: 'none',
            borderRadius: '8px',
            fontSize: '0.9375rem',
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
        <button
          type="submit"
          style={{
            padding: '0.75rem 1.25rem',
            background: 'var(--color-accent, #007AFF)',
            color: 'white',
            border: 'none',
            borderRadius: '8px',
            fontSize: '0.9375rem',
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          {initialData ? 'Update' : 'Create'}
        </button>
      </div>
    </form>
  );
}

// ============================================
// Export
// ============================================

export default {
  OrganizationCard,
  MemberList,
  InviteModal,
  EntityCard,
  RecordTable,
  RecordForm,
};
