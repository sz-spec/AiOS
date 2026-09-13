'use client';

import React, { useState, useRef, useEffect } from 'react';
import { useInsights, Insight } from '@/hooks/useMemory';
import { useVoiceInput } from '@/hooks/useVoiceInput';
import { MemoryChatPanel, TagEditor } from '@/components/memory';
import { useConfirm } from '@/hooks/useConfirm';

const categoryLabels: Record<string, { label: string; description: string }> = {
  conversation: { label: 'Conversations', description: 'Discussion notes' },
  decision: { label: 'Decisions', description: 'Choices made' },
  code_change: { label: 'Changes', description: 'Modifications' },
  learning: { label: 'Learnings', description: 'Patterns discovered' },
  error: { label: 'Issues', description: 'Problems found' },
  solution: { label: 'Solutions', description: 'Fixes applied' },
  context: { label: 'Context', description: 'Background info' },
};

export default function InsightsPage() {
  const {
    insights,
    stats,
    isLoading,
    error,
    selectedIds,
    fetchRecent,
    searchInsights,
    addInsight,
    updateInsight,
    addTag,
    removeTag,
    suggestTags,
    getConsolidatedGroups,
    deleteInsight,
    bulkDelete,
    exportInsights,
    importInsights,
    clearAll,
    toggleSelect,
    selectAll,
    clearSelection,
  } = useInsights();
  const { confirm, ConfirmDialog: ConfirmMount } = useConfirm();

  const [inputValue, setInputValue] = useState('');
  const [searchMode, setSearchMode] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState('');
  const [filterType, setFilterType] = useState<string>('');
  const [showActions, setShowActions] = useState(false);
  const [chatInsight, setChatInsight] = useState<Insight | null>(null);
  const [viewMode, setViewMode] = useState<'list' | 'grouped' | 'consolidated'>('list');
  const [suggestedTags, setSuggestedTags] = useState<string[]>([]);
  const [consolidatedGroups, setConsolidatedGroups] = useState<any[]>([]);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const fileInputRef = useRef<HTMLInputElement>(null);

  const groupedInsights = insights.reduce((acc, insight) => {
    const type = insight.metadata.memory_type || 'context';
    if (!acc[type]) acc[type] = [];
    acc[type].push(insight);
    return acc;
  }, {} as Record<string, Insight[]>);

  const toggleGroup = (group: string) => {
    setExpandedGroups(prev => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  };

  const expandAllGroups = () => {
    setExpandedGroups(new Set(Object.keys(groupedInsights)));
  };

  useEffect(() => {
    if (viewMode === 'consolidated') {
      getConsolidatedGroups().then(setConsolidatedGroups);
    }
  }, [viewMode, getConsolidatedGroups]);

  useEffect(() => {
    if (inputValue.length > 10 && !searchMode) {
      const timer = setTimeout(() => {
        suggestTags(inputValue).then(setSuggestedTags);
      }, 500);
      return () => clearTimeout(timer);
    } else {
      setSuggestedTags([]);
    }
  }, [inputValue, searchMode, suggestTags]);

  const {
    isListening,
    isSupported: voiceSupported,
    transcript,
    toggleListening,
    clearTranscript,
  } = useVoiceInput({
    onTranscript: (text) => {
      setInputValue(prev => prev + (prev ? ' ' : '') + text);
    },
  });

  useEffect(() => {
    if (transcript && !isListening) {
      setInputValue(prev => prev + (prev ? ' ' : '') + transcript);
      clearTranscript();
    }
  }, [transcript, isListening, clearTranscript]);

  const handleApplyEdit = async (insightId: string, newContent: string) => {
    await updateInsight(insightId, newContent);
    setChatInsight(null);
  };

  const handleSubmit = async () => {
    if (!inputValue.trim()) return;

    if (searchMode) {
      await searchInsights(inputValue, 20, filterType || undefined);
    } else {
      const type = detectInsightType(inputValue);
      await addInsight(inputValue, type);
      setInputValue('');
    }
  };

  const detectInsightType = (text: string): string => {
    const lower = text.toLowerCase();
    if (lower.includes('decided') || lower.includes('decision') || lower.includes('chose')) return 'decision';
    if (lower.includes('error') || lower.includes('bug') || lower.includes('issue') || lower.includes('problem')) return 'error';
    if (lower.includes('fixed') || lower.includes('solved') || lower.includes('solution')) return 'solution';
    if (lower.includes('learned') || lower.includes('pattern') || lower.includes('realized')) return 'learning';
    if (lower.includes('changed') || lower.includes('updated') || lower.includes('modified')) return 'code_change';
    return 'context';
  };

  const handleEdit = (insight: Insight) => {
    setEditingId(insight.id);
    setEditContent(insight.content);
  };

  const saveEdit = async () => {
    if (editingId && editContent.trim()) {
      await updateInsight(editingId, editContent);
      setEditingId(null);
      setEditContent('');
    }
  };

  const handleDelete = async (id: string) => {
    await deleteInsight(id);
  };

  const handleBulkDelete = async () => {
    if (selectedIds.size === 0) return;
    const ok = await confirm({
      title: `Delete ${selectedIds.size} ${selectedIds.size === 1 ? 'item' : 'items'}?`,
      description: 'The selected insights will be permanently removed.',
      confirmLabel: 'Delete',
      destructive: true,
    });
    if (ok) await bulkDelete(Array.from(selectedIds));
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      await importInsights(file);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleClearAll = async () => {
    const ok = await confirm({
      title: 'Clear all insights?',
      description: 'This will permanently delete every stored insight. This cannot be undone.',
      confirmLabel: 'Clear all',
      destructive: true,
    });
    if (ok) await clearAll();
  };

  const formatTime = (timestamp: string) => {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));

    if (days === 0) return 'Today';
    if (days === 1) return 'Yesterday';
    if (days < 7) return `${days} days ago`;
    return date.toLocaleDateString();
  };

  const totalCount = stats?.total_memories || 0;

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#fafafa', padding: '24px 32px' }}>
      <div style={{ maxWidth: '900px', margin: '0 auto' }}>

        <div style={{ marginBottom: '32px' }}>
          <h1 style={{ fontSize: '32px', fontWeight: 700, color: '#111', marginBottom: '8px' }}>
            Learning Memory
          </h1>
          <p style={{ fontSize: '16px', color: '#666' }}>
            {totalCount === 0
              ? 'Start by adding what the system should remember'
              : `${totalCount} items guiding system behavior`
            }
          </p>
        </div>

        <div style={{ backgroundColor: '#fff', borderRadius: '16px', padding: '20px', marginBottom: '24px', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
          <div style={{ display: 'flex', gap: '12px', marginBottom: '12px' }}>
            <button
              onClick={() => setSearchMode(false)}
              style={{
                padding: '8px 16px',
                borderRadius: '8px',
                border: 'none',
                backgroundColor: !searchMode ? '#111' : '#f0f0f0',
                color: !searchMode ? '#fff' : '#666',
                fontSize: '14px',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              Add Insight
            </button>
            <button
              onClick={() => setSearchMode(true)}
              style={{
                padding: '8px 16px',
                borderRadius: '8px',
                border: 'none',
                backgroundColor: searchMode ? '#111' : '#f0f0f0',
                color: searchMode ? '#fff' : '#666',
                fontSize: '14px',
                fontWeight: 500,
                cursor: 'pointer',
              }}
            >
              Search
            </button>
          </div>

          <div style={{ display: 'flex', gap: '12px' }}>
            <div style={{ flex: 1, position: 'relative' }}>
              <input
                type="text"
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
                placeholder={isListening
                  ? 'Listening...'
                  : searchMode
                    ? "What are you looking for?"
                    : "Tell me something to remember..."
                }
                style={{
                  width: '100%',
                  padding: '16px',
                  paddingRight: voiceSupported ? '52px' : '16px',
                  fontSize: '16px',
                  border: `2px solid ${isListening ? '#667eea' : '#e5e5e5'}`,
                  borderRadius: '12px',
                  outline: 'none',
                }}
              />
              {voiceSupported && (
                <button
                  onClick={toggleListening}
                  title={isListening ? 'Stop listening' : 'Voice input'}
                  style={{
                    position: 'absolute',
                    right: '10px',
                    top: '50%',
                    transform: 'translateY(-50%)',
                    width: '36px',
                    height: '36px',
                    borderRadius: '50%',
                    border: 'none',
                    backgroundColor: isListening ? '#dc2626' : '#f0f0f0',
                    color: isListening ? '#fff' : '#666',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
                    <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                    <line x1="12" x2="12" y1="19" y2="22" />
                  </svg>
                </button>
              )}
            </div>
            <button
              onClick={handleSubmit}
              disabled={!inputValue.trim()}
              style={{
                padding: '16px 32px',
                backgroundColor: inputValue.trim() ? '#111' : '#e5e5e5',
                color: inputValue.trim() ? '#fff' : '#999',
                border: 'none',
                borderRadius: '12px',
                fontSize: '16px',
                fontWeight: 600,
                cursor: inputValue.trim() ? 'pointer' : 'not-allowed',
              }}
            >
              {searchMode ? 'Find' : 'Save'}
            </button>
          </div>

          {!searchMode && (
            <p style={{ fontSize: '13px', color: '#999', marginTop: '12px' }}>
              Just type naturally. The system will understand context, preferences, decisions, and patterns.
            </p>
          )}
        </div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px' }}>
          {stats && Object.values(stats.by_type).some(v => v > 0) && (
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', flex: 1 }}>
              <button
                onClick={() => { setFilterType(''); fetchRecent(); }}
                style={{
                  padding: '8px 14px',
                  borderRadius: '20px',
                  border: filterType === '' ? '2px solid #111' : '1px solid #ddd',
                  backgroundColor: filterType === '' ? '#111' : '#fff',
                  color: filterType === '' ? '#fff' : '#666',
                  fontSize: '13px',
                  fontWeight: 500,
                  cursor: 'pointer',
                }}
              >
                All ({totalCount})
              </button>
              {Object.entries(stats.by_type).filter(([, count]) => count > 0).map(([type, count]) => (
                <button
                  key={type}
                  onClick={() => { setFilterType(type); fetchRecent(50, type); }}
                  style={{
                    padding: '8px 14px',
                    borderRadius: '20px',
                    border: filterType === type ? '2px solid #111' : '1px solid #ddd',
                    backgroundColor: filterType === type ? '#111' : '#fff',
                    color: filterType === type ? '#fff' : '#666',
                    fontSize: '13px',
                    fontWeight: 500,
                    cursor: 'pointer',
                  }}
                >
                  {categoryLabels[type]?.label || type} ({count})
                </button>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', gap: '4px', backgroundColor: '#f0f0f0', borderRadius: '8px', padding: '4px' }}>
            <button
              onClick={() => setViewMode('list')}
              style={{
                padding: '6px 12px',
                borderRadius: '6px',
                border: 'none',
                backgroundColor: viewMode === 'list' ? '#fff' : 'transparent',
                color: viewMode === 'list' ? '#111' : '#666',
                fontSize: '13px',
                fontWeight: 500,
                cursor: 'pointer',
                boxShadow: viewMode === 'list' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}
            >
              List
            </button>
            <button
              onClick={() => { setViewMode('grouped'); expandAllGroups(); }}
              style={{
                padding: '6px 12px',
                borderRadius: '6px',
                border: 'none',
                backgroundColor: viewMode === 'grouped' ? '#fff' : 'transparent',
                color: viewMode === 'grouped' ? '#111' : '#666',
                fontSize: '13px',
                fontWeight: 500,
                cursor: 'pointer',
                boxShadow: viewMode === 'grouped' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}
            >
              By Type
            </button>
            <button
              onClick={() => setViewMode('consolidated')}
              style={{
                padding: '6px 12px',
                borderRadius: '6px',
                border: 'none',
                backgroundColor: viewMode === 'consolidated' ? '#fff' : 'transparent',
                color: viewMode === 'consolidated' ? '#111' : '#666',
                fontSize: '13px',
                fontWeight: 500,
                cursor: 'pointer',
                boxShadow: viewMode === 'consolidated' ? '0 1px 3px rgba(0,0,0,0.1)' : 'none',
              }}
            >
              Similar
            </button>
          </div>
        </div>

        {suggestedTags.length > 0 && !searchMode && (
          <div style={{ padding: '12px 16px', backgroundColor: '#f0f9ff', borderRadius: '12px', marginBottom: '16px', border: '1px solid #bae6fd' }}>
            <div style={{ fontSize: '12px', color: '#0369a1', marginBottom: '8px', fontWeight: 500 }}>
              Suggested tags based on content:
            </div>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {suggestedTags.map(tag => (
                <span key={tag} style={{ padding: '4px 10px', backgroundColor: '#e0f2fe', color: '#0284c7', borderRadius: '12px', fontSize: '12px', fontWeight: 500 }}>
                  {tag}
                </span>
              ))}
            </div>
          </div>
        )}

        {insights.length > 0 && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
            <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
              <label style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={selectedIds.size === insights.length && insights.length > 0}
                  onChange={() => selectedIds.size === insights.length ? clearSelection() : selectAll()}
                  style={{ width: '18px', height: '18px', cursor: 'pointer' }}
                />
                <span style={{ fontSize: '14px', color: '#666' }}>
                  {selectedIds.size > 0 ? `${selectedIds.size} selected` : 'Select all'}
                </span>
              </label>

              {selectedIds.size > 0 && (
                <button
                  onClick={handleBulkDelete}
                  style={{ padding: '6px 12px', backgroundColor: '#fee2e2', color: '#dc2626', border: 'none', borderRadius: '6px', fontSize: '13px', cursor: 'pointer' }}
                >
                  Delete Selected
                </button>
              )}
            </div>

            <div style={{ position: 'relative' }}>
              <button
                onClick={() => setShowActions(!showActions)}
                style={{ padding: '8px 16px', backgroundColor: '#f5f5f5', border: '1px solid #e5e5e5', borderRadius: '8px', fontSize: '14px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                Actions
                <span style={{ fontSize: '10px' }}>&#9660;</span>
              </button>

              {showActions && (
                <div style={{ position: 'absolute', right: 0, top: '100%', marginTop: '4px', backgroundColor: '#fff', borderRadius: '8px', boxShadow: '0 4px 12px rgba(0,0,0,0.15)', padding: '8px 0', zIndex: 100, minWidth: '160px' }}>
                  <button
                    onClick={() => { exportInsights(); setShowActions(false); }}
                    style={{ display: 'block', width: '100%', padding: '10px 16px', textAlign: 'left', border: 'none', backgroundColor: 'transparent', fontSize: '14px', cursor: 'pointer' }}
                  >
                    Export All
                  </button>
                  <button
                    onClick={() => { fileInputRef.current?.click(); setShowActions(false); }}
                    style={{ display: 'block', width: '100%', padding: '10px 16px', textAlign: 'left', border: 'none', backgroundColor: 'transparent', fontSize: '14px', cursor: 'pointer' }}
                  >
                    Import
                  </button>
                  <div style={{ height: '1px', backgroundColor: '#eee', margin: '8px 0' }} />
                  <button
                    onClick={() => { handleClearAll(); setShowActions(false); }}
                    style={{ display: 'block', width: '100%', padding: '10px 16px', textAlign: 'left', border: 'none', backgroundColor: 'transparent', fontSize: '14px', color: '#dc2626', cursor: 'pointer' }}
                  >
                    Clear All
                  </button>
                </div>
              )}
            </div>
            <input ref={fileInputRef} type="file" accept=".json" onChange={handleImport} style={{ display: 'none' }} />
          </div>
        )}

        {error && (
          <div style={{ padding: '12px 16px', backgroundColor: '#fef2f2', color: '#dc2626', borderRadius: '8px', marginBottom: '16px', fontSize: '14px' }}>
            {error}
          </div>
        )}

        {viewMode === 'consolidated' && (
          <div style={{ backgroundColor: '#fff', borderRadius: '16px', overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.08)', marginBottom: '16px' }}>
            {consolidatedGroups.length === 0 ? (
              <div style={{ padding: '48px', textAlign: 'center', color: '#999' }}>
                <p style={{ fontSize: '16px', marginBottom: '8px' }}>No similar memories found</p>
                <p style={{ fontSize: '14px' }}>Add more memories to find patterns</p>
              </div>
            ) : (
              consolidatedGroups.map((group, idx) => (
                <div key={idx} style={{ borderBottom: idx < consolidatedGroups.length - 1 ? '1px solid #eee' : 'none' }}>
                  <div style={{ padding: '16px 20px', backgroundColor: '#f8fafc', borderBottom: '1px solid #e2e8f0' }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span style={{ padding: '4px 10px', backgroundColor: '#dbeafe', color: '#1d4ed8', borderRadius: '12px', fontSize: '12px', fontWeight: 600 }}>
                        {group.count} similar memories
                      </span>
                    </div>
                    <p style={{ margin: '12px 0 0', fontSize: '15px', color: '#111', lineHeight: 1.5 }}>
                      {group.primary.content}
                    </p>
                    <div style={{ display: 'flex', gap: '8px', marginTop: '8px', flexWrap: 'wrap' }}>
                      {(group.primary.metadata?.tags || []).map((tag: string) => (
                        <span key={tag} style={{ padding: '2px 8px', backgroundColor: '#f0fdf4', color: '#16a34a', borderRadius: '8px', fontSize: '11px' }}>
                          {tag}
                        </span>
                      ))}
                    </div>
                  </div>
                  {group.similar.length > 0 && (
                    <div style={{ padding: '12px 20px', backgroundColor: '#fafafa' }}>
                      <div style={{ fontSize: '12px', color: '#666', marginBottom: '8px' }}>
                        Similar entries (can be consolidated):
                      </div>
                      {group.similar.slice(0, 3).map((sim: any) => (
                        <div key={sim.id} style={{ padding: '8px 12px', backgroundColor: '#fff', borderRadius: '8px', marginBottom: '6px', fontSize: '13px', color: '#666', border: '1px solid #e5e5e5' }}>
                          {sim.content.length > 100 ? sim.content.slice(0, 100) + '...' : sim.content}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {viewMode !== 'consolidated' && (
          <div style={{ backgroundColor: '#fff', borderRadius: '16px', overflow: 'hidden', boxShadow: '0 1px 3px rgba(0,0,0,0.08)' }}>
            {isLoading ? (
              <div style={{ padding: '48px', textAlign: 'center', color: '#999' }}>
                Loading...
              </div>
            ) : insights.length === 0 ? (
              <div style={{ padding: '48px', textAlign: 'center', color: '#999' }}>
                <p style={{ fontSize: '16px', marginBottom: '8px' }}>No insights yet</p>
                <p style={{ fontSize: '14px' }}>Add your first one above</p>
              </div>
            ) : (
              insights.map((insight, index) => (
                <div
                  key={insight.id}
                  style={{ padding: '16px 20px', borderBottom: index < insights.length - 1 ? '1px solid #f0f0f0' : 'none', display: 'flex', gap: '12px', alignItems: 'flex-start' }}
                >
                  <input
                    type="checkbox"
                    checked={selectedIds.has(insight.id)}
                    onChange={() => toggleSelect(insight.id)}
                    style={{ marginTop: '4px', width: '16px', height: '16px', cursor: 'pointer' }}
                  />

                  <div style={{ flex: 1 }}>
                    {editingId === insight.id ? (
                      <div style={{ display: 'flex', gap: '8px' }}>
                        <input
                          type="text"
                          value={editContent}
                          onChange={(e) => setEditContent(e.target.value)}
                          onKeyDown={(e) => e.key === 'Enter' && saveEdit()}
                          style={{ flex: 1, padding: '8px 12px', fontSize: '15px', border: '2px solid #111', borderRadius: '8px', outline: 'none' }}
                          autoFocus
                        />
                        <button onClick={saveEdit} style={{ padding: '8px 16px', backgroundColor: '#111', color: '#fff', border: 'none', borderRadius: '8px', cursor: 'pointer' }}>
                          Save
                        </button>
                        <button onClick={() => setEditingId(null)} style={{ padding: '8px 16px', backgroundColor: '#f0f0f0', color: '#666', border: 'none', borderRadius: '8px', cursor: 'pointer' }}>
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <React.Fragment>
                        <p style={{ fontSize: '15px', color: '#111', lineHeight: 1.5, marginBottom: '8px' }}>
                          {insight.content}
                        </p>
                        <div style={{ display: 'flex', gap: '12px', alignItems: 'center', fontSize: '13px', color: '#999', marginBottom: '8px' }}>
                          <span style={{ padding: '2px 8px', backgroundColor: '#f5f5f5', borderRadius: '4px', fontSize: '12px' }}>
                            {categoryLabels[insight.metadata.memory_type]?.label || insight.metadata.memory_type}
                          </span>
                          <span>{formatTime(insight.metadata.timestamp)}</span>
                          {insight.relevance !== undefined && (
                            <span style={{ color: '#22c55e' }}>
                              {Math.round(insight.relevance * 100)}% match
                            </span>
                          )}
                        </div>
                        <TagEditor
                          tags={(insight.metadata.tags as string[]) || []}
                          onAddTag={(tag) => addTag(insight.id, tag)}
                          onRemoveTag={(tag) => removeTag(insight.id, tag)}
                          compact
                        />
                      </React.Fragment>
                    )}
                  </div>

                  {editingId !== insight.id && (
                    <div style={{ display: 'flex', gap: '4px' }}>
                      <button
                        onClick={() => setChatInsight(insight)}
                        title="Discuss with AI"
                        style={{ padding: '6px 10px', backgroundColor: 'transparent', border: 'none', borderRadius: '6px', cursor: 'pointer', color: '#667eea', fontSize: '14px', fontWeight: 500 }}
                      >
                        Discuss
                      </button>
                      <button
                        onClick={() => handleEdit(insight)}
                        title="Edit"
                        style={{ padding: '6px 10px', backgroundColor: 'transparent', border: 'none', borderRadius: '6px', cursor: 'pointer', color: '#999', fontSize: '14px' }}
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(insight.id)}
                        title="Delete"
                        style={{ padding: '6px 10px', backgroundColor: 'transparent', border: 'none', borderRadius: '6px', cursor: 'pointer', color: '#dc2626', fontSize: '14px' }}
                      >
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {chatInsight && (
          <MemoryChatPanel
            insight={chatInsight}
            onClose={() => setChatInsight(null)}
            onApplyEdit={handleApplyEdit}
          />
        )}
      </div>
      <ConfirmMount />
    </div>
  );
}
