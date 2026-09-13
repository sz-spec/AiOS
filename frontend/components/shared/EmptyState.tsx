'use client';

import React from 'react';

interface ChecklistItem {
  text: string;
  completed?: boolean;
}

interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  checklist?: ChecklistItem[];
  backendEndpoint?: string;
  children?: React.ReactNode;
}

export function EmptyState({
  icon,
  title,
  description,
  checklist,
  backendEndpoint,
  children,
}: EmptyStateProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '60vh',
        padding: '48px 24px',
        textAlign: 'center',
      }}
    >
      {/* Icon */}
      {icon && (
        <div
          style={{
            width: '80px',
            height: '80px',
            borderRadius: 'var(--radius-xl, 24px)',
            backgroundColor: 'var(--bg-tertiary, #f3f3f3)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            marginBottom: '24px',
            color: 'var(--text-tertiary, #999)',
          }}
        >
          {icon}
        </div>
      )}

      {/* Title */}
      <h2
        style={{
          margin: '0 0 8px',
          fontSize: '24px',
          fontWeight: 600,
          color: 'var(--text-primary, #0d0d0d)',
        }}
      >
        {title}
      </h2>

      {/* Description */}
      {description && (
        <p
          style={{
            margin: '0 0 24px',
            fontSize: '15px',
            color: 'var(--text-secondary, #666)',
            maxWidth: '480px',
            lineHeight: 1.6,
          }}
        >
          {description}
        </p>
      )}

      {/* Implementation Checklist */}
      {checklist && checklist.length > 0 && (
        <div
          style={{
            backgroundColor: 'var(--bg-secondary, #f9f9f9)',
            border: '1px solid var(--border-light, #e5e5e5)',
            borderRadius: 'var(--radius-lg, 16px)',
            padding: '20px 24px',
            maxWidth: '400px',
            width: '100%',
            textAlign: 'left',
            marginBottom: '24px',
          }}
        >
          <h3
            style={{
              margin: '0 0 16px',
              fontSize: '13px',
              fontWeight: 600,
              color: 'var(--text-tertiary, #999)',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}
          >
            Implementation Checklist
          </h3>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none' }}>
            {checklist.map((item, index) => (
              <li
                key={index}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: '12px',
                  padding: '8px 0',
                  borderTop: index > 0 ? '1px solid var(--border-light, #e5e5e5)' : 'none',
                }}
              >
                {/* Checkbox */}
                <div
                  style={{
                    width: '20px',
                    height: '20px',
                    borderRadius: '6px',
                    border: `2px solid ${item.completed ? 'var(--success, #059669)' : 'var(--border-medium, #d4d4d4)'}`,
                    backgroundColor: item.completed ? 'var(--success, #059669)' : 'transparent',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                    marginTop: '2px',
                  }}
                >
                  {item.completed && (
                    <svg
                      width="12"
                      height="12"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="white"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <polyline points="20 6 9 17 4 12" />
                    </svg>
                  )}
                </div>
                <span
                  style={{
                    fontSize: '14px',
                    color: item.completed ? 'var(--text-tertiary, #999)' : 'var(--text-primary, #0d0d0d)',
                    textDecoration: item.completed ? 'line-through' : 'none',
                    lineHeight: 1.5,
                  }}
                >
                  {item.text}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Backend endpoint info */}
      {backendEndpoint && (
        <div
          style={{
            backgroundColor: 'var(--bg-tertiary, #f3f3f3)',
            padding: '12px 16px',
            borderRadius: 'var(--radius-md, 12px)',
            marginBottom: '24px',
          }}
        >
          <span style={{ fontSize: '12px', color: 'var(--text-tertiary, #999)' }}>Backend endpoint: </span>
          <code
            style={{
              fontSize: '13px',
              fontFamily: "'SF Mono', 'Fira Code', Consolas, monospace",
              color: 'var(--accent, #d97706)',
            }}
          >
            {backendEndpoint}
          </code>
        </div>
      )}

      {/* Custom content */}
      {children}
    </div>
  );
}

// Pre-built empty state icons
export function DocumentIcon({ size = 40 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
      <polyline points="10 9 9 9 8 9" />
    </svg>
  );
}

export function DatabaseIcon({ size = 40 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </svg>
  );
}

export function SearchIcon({ size = 40 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  );
}

export function BrainIcon({ size = 40 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96.44 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 4.44-1.54" />
      <path d="M14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96.44 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-4.44-1.54" />
    </svg>
  );
}

export default EmptyState;
