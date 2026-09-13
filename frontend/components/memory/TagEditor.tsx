'use client';

import React, { useState, useRef, useEffect } from 'react';
import { TAG_CATEGORIES, TagCategory } from '@/hooks/useMemory';

interface TagEditorProps {
  tags: string[];
  onAddTag: (tag: string) => void;
  onRemoveTag: (tag: string) => void;
  compact?: boolean;
}

// Tag colors by category
const TAG_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  urgent: { bg: '#fef2f2', text: '#dc2626', border: '#fecaca' },
  important: { bg: '#fff7ed', text: '#ea580c', border: '#fed7aa' },
  'low-priority': { bg: '#f0fdf4', text: '#16a34a', border: '#bbf7d0' },
  active: { bg: '#eff6ff', text: '#2563eb', border: '#bfdbfe' },
  archived: { bg: '#f5f5f5', text: '#737373', border: '#e5e5e5' },
  pending: { bg: '#fefce8', text: '#ca8a04', border: '#fef08a' },
  resolved: { bg: '#f0fdf4', text: '#16a34a', border: '#bbf7d0' },
  frontend: { bg: '#fdf4ff', text: '#a855f7', border: '#e9d5ff' },
  backend: { bg: '#ecfeff', text: '#0891b2', border: '#a5f3fc' },
  database: { bg: '#fef3c7', text: '#d97706', border: '#fde68a' },
  api: { bg: '#dbeafe', text: '#3b82f6', border: '#93c5fd' },
  ui: { bg: '#fce7f3', text: '#db2777', border: '#fbcfe8' },
  devops: { bg: '#e0e7ff', text: '#4f46e5', border: '#c7d2fe' },
  bug: { bg: '#fef2f2', text: '#dc2626', border: '#fecaca' },
  feature: { bg: '#dbeafe', text: '#2563eb', border: '#93c5fd' },
  improvement: { bg: '#d1fae5', text: '#059669', border: '#a7f3d0' },
  documentation: { bg: '#e0e7ff', text: '#6366f1', border: '#c7d2fe' },
  config: { bg: '#f3f4f6', text: '#4b5563', border: '#d1d5db' },
};

const DEFAULT_COLOR = { bg: '#f3f4f6', text: '#374151', border: '#e5e7eb' };

export function TagEditor({ tags, onAddTag, onRemoveTag, compact = false }: TagEditorProps) {
  const [showDropdown, setShowDropdown] = useState(false);
  const [customTag, setCustomTag] = useState('');
  const [activeCategory, setActiveCategory] = useState<TagCategory>('priority');
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setShowDropdown(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  const getTagColor = (tag: string) => TAG_COLORS[tag] || DEFAULT_COLOR;

  const handleAddCustomTag = () => {
    if (customTag.trim() && !tags.includes(customTag.trim().toLowerCase())) {
      onAddTag(customTag.trim().toLowerCase());
      setCustomTag('');
    }
  };

  const categories = Object.keys(TAG_CATEGORIES) as TagCategory[];

  return (
    <div style={{ position: 'relative' }} ref={dropdownRef}>
      {/* Display existing tags */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
        {tags.map((tag) => {
          const color = getTagColor(tag);
          return (
            <span
              key={tag}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '4px',
                padding: compact ? '2px 6px' : '4px 10px',
                backgroundColor: color.bg,
                color: color.text,
                border: `1px solid ${color.border}`,
                borderRadius: '12px',
                fontSize: compact ? '11px' : '12px',
                fontWeight: 500,
              }}
            >
              {tag}
              <button
                onClick={() => onRemoveTag(tag)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: compact ? '14px' : '16px',
                  height: compact ? '14px' : '16px',
                  borderRadius: '50%',
                  border: 'none',
                  backgroundColor: 'transparent',
                  color: color.text,
                  cursor: 'pointer',
                  padding: 0,
                  fontSize: '14px',
                  lineHeight: 1,
                }}
                title="Remove tag"
              >
                ×
              </button>
            </span>
          );
        })}

        {/* Add tag button */}
        <button
          onClick={() => setShowDropdown(!showDropdown)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: '4px',
            padding: compact ? '2px 8px' : '4px 10px',
            backgroundColor: '#f5f5f5',
            color: '#666',
            border: '1px dashed #ccc',
            borderRadius: '12px',
            fontSize: compact ? '11px' : '12px',
            cursor: 'pointer',
            transition: 'all 0.2s',
          }}
        >
          <span style={{ fontSize: '14px' }}>+</span>
          {!compact && 'Add Tag'}
        </button>
      </div>

      {/* Dropdown for adding tags */}
      {showDropdown && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: '8px',
            backgroundColor: '#fff',
            borderRadius: '12px',
            boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
            border: '1px solid #e5e5e5',
            zIndex: 1000,
            width: '320px',
            overflow: 'hidden',
          }}
        >
          {/* Category tabs */}
          <div
            style={{
              display: 'flex',
              borderBottom: '1px solid #eee',
              overflowX: 'auto',
              scrollbarWidth: 'none',
            }}
          >
            {categories.map((cat) => (
              <button
                key={cat}
                onClick={() => setActiveCategory(cat)}
                style={{
                  padding: '10px 14px',
                  border: 'none',
                  backgroundColor: activeCategory === cat ? '#f5f5f5' : 'transparent',
                  color: activeCategory === cat ? '#111' : '#666',
                  fontSize: '12px',
                  fontWeight: activeCategory === cat ? 600 : 400,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  textTransform: 'capitalize',
                  borderBottom: activeCategory === cat ? '2px solid #667eea' : '2px solid transparent',
                }}
              >
                {cat}
              </button>
            ))}
          </div>

          {/* Tag options */}
          <div style={{ padding: '12px', maxHeight: '200px', overflowY: 'auto' }}>
            {activeCategory === 'custom' ? (
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  value={customTag}
                  onChange={(e) => setCustomTag(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleAddCustomTag()}
                  placeholder="Enter custom tag..."
                  style={{
                    flex: 1,
                    padding: '8px 12px',
                    border: '1px solid #ddd',
                    borderRadius: '8px',
                    fontSize: '13px',
                    outline: 'none',
                  }}
                  autoFocus
                />
                <button
                  onClick={handleAddCustomTag}
                  disabled={!customTag.trim()}
                  style={{
                    padding: '8px 16px',
                    backgroundColor: customTag.trim() ? '#667eea' : '#e5e5e5',
                    color: customTag.trim() ? '#fff' : '#999',
                    border: 'none',
                    borderRadius: '8px',
                    fontSize: '13px',
                    cursor: customTag.trim() ? 'pointer' : 'not-allowed',
                  }}
                >
                  Add
                </button>
              </div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {TAG_CATEGORIES[activeCategory].map((tag) => {
                  const isSelected = tags.includes(tag);
                  const color = getTagColor(tag);
                  return (
                    <button
                      key={tag}
                      onClick={() => isSelected ? onRemoveTag(tag) : onAddTag(tag)}
                      style={{
                        padding: '6px 12px',
                        backgroundColor: isSelected ? color.bg : '#f5f5f5',
                        color: isSelected ? color.text : '#666',
                        border: `1px solid ${isSelected ? color.border : '#e5e5e5'}`,
                        borderRadius: '16px',
                        fontSize: '12px',
                        cursor: 'pointer',
                        transition: 'all 0.2s',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px',
                      }}
                    >
                      {isSelected && <span>✓</span>}
                      {tag}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Quick actions */}
          <div
            style={{
              padding: '10px 12px',
              borderTop: '1px solid #eee',
              backgroundColor: '#fafafa',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <span style={{ fontSize: '11px', color: '#999' }}>
              {tags.length} tag{tags.length !== 1 ? 's' : ''} selected
            </span>
            <button
              onClick={() => setShowDropdown(false)}
              style={{
                padding: '4px 12px',
                backgroundColor: 'transparent',
                color: '#667eea',
                border: 'none',
                fontSize: '12px',
                cursor: 'pointer',
                fontWeight: 500,
              }}
            >
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// Compact tag display (read-only)
export function TagDisplay({ tags }: { tags: string[] }) {
  if (!tags || tags.length === 0) return null;

  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
      {tags.map((tag) => {
        const color = TAG_COLORS[tag] || DEFAULT_COLOR;
        return (
          <span
            key={tag}
            style={{
              padding: '2px 8px',
              backgroundColor: color.bg,
              color: color.text,
              border: `1px solid ${color.border}`,
              borderRadius: '10px',
              fontSize: '11px',
              fontWeight: 500,
            }}
          >
            {tag}
          </span>
        );
      })}
    </div>
  );
}

export default TagEditor;
