'use client';

import { useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { AlertTriangle, X, Send, Loader2 } from 'lucide-react';

/**
 * SOSButton — Expert-in-the-Loop trigger (IE-2, Phase 2.0)
 *
 * Renders a pulsing red SOS button when `stuckCount >= 3`.
 * On click, opens a modal where the user describes the problem.
 * Submits a POST /api/v1/expert/request with the build state.
 */

interface SOSButtonProps {
  stuckCount: number;
  projectId: string;
  buildId?: string;
  /** Full build state dict — sent to backend for context packaging. */
  buildState?: Record<string, unknown>;
}

export function SOSButton({
  stuckCount,
  projectId,
  buildId,
  buildState,
}: SOSButtonProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [description, setDescription] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [result, setResult] = useState<{ id: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const shouldShow = stuckCount >= 3;

  const handleSubmit = useCallback(async () => {
    setIsSubmitting(true);
    setError(null);

    try {
      const res = await fetch('/api/v1/expert/request', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_id: projectId,
          build_id: buildId,
          description,
          build_state: buildState,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `Request failed (${res.status})`);
      }

      const data = await res.json();
      setResult({ id: data.request?.id });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create request');
    } finally {
      setIsSubmitting(false);
    }
  }, [projectId, buildId, description, buildState]);

  if (!shouldShow) return null;

  return (
    <>
      {/* Pulsing SOS button */}
      <motion.button
        onClick={() => setIsOpen(true)}
        initial={{ scale: 0 }}
        animate={{ scale: 1 }}
        whileHover={{ scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: '8px',
          padding: '10px 20px',
          backgroundColor: '#dc2626',
          color: '#fff',
          border: 'none',
          borderRadius: '8px',
          fontSize: '14px',
          fontWeight: 700,
          cursor: 'pointer',
          boxShadow: '0 0 0 0 rgba(220, 38, 38, 0.7)',
          animation: 'sos-pulse 2s ease-in-out infinite',
        }}
      >
        <AlertTriangle size={18} />
        SOS — Get Expert Help
      </motion.button>

      {/* Modal overlay */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => !isSubmitting && setIsOpen(false)}
            style={{
              position: 'fixed',
              inset: 0,
              backgroundColor: 'rgba(0, 0, 0, 0.5)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              zIndex: 9999,
            }}
          >
            <motion.div
              initial={{ scale: 0.9, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.9, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              style={{
                backgroundColor: 'var(--bg-primary, #fff)',
                borderRadius: '12px',
                border: '1px solid var(--border-light, #e5e7eb)',
                width: '100%',
                maxWidth: '480px',
                padding: '24px',
                boxShadow: '0 20px 60px rgba(0, 0, 0, 0.15)',
              }}
            >
              {/* Header */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginBottom: '16px',
                }}
              >
                <div
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                  }}
                >
                  <div
                    style={{
                      width: '36px',
                      height: '36px',
                      borderRadius: '8px',
                      backgroundColor: '#fee2e2',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                    }}
                  >
                    <AlertTriangle size={20} color="#dc2626" />
                  </div>
                  <div>
                    <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>
                      Request Expert Help
                    </h3>
                    <p
                      style={{
                        margin: 0,
                        fontSize: '12px',
                        color: 'var(--text-tertiary, #9ca3af)',
                      }}
                    >
                      AI stuck after {stuckCount} attempts
                    </p>
                  </div>
                </div>
                <button
                  onClick={() => setIsOpen(false)}
                  disabled={isSubmitting}
                  style={{
                    background: 'none',
                    border: 'none',
                    cursor: 'pointer',
                    padding: '4px',
                    color: 'var(--text-tertiary, #9ca3af)',
                  }}
                >
                  <X size={20} />
                </button>
              </div>

              {/* Result state */}
              {result ? (
                <div
                  style={{
                    padding: '16px',
                    backgroundColor: '#d1fae5',
                    borderRadius: '8px',
                    fontSize: '14px',
                    color: '#065f46',
                    textAlign: 'center',
                  }}
                >
                  <p style={{ margin: '0 0 8px', fontWeight: 600 }}>
                    Help request submitted!
                  </p>
                  <p style={{ margin: 0, fontSize: '12px' }}>
                    Request ID: {result.id?.slice(0, 8)}... — An expert will be
                    assigned shortly.
                  </p>
                </div>
              ) : (
                <>
                  {/* Description */}
                  <textarea
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Describe the problem you're experiencing..."
                    disabled={isSubmitting}
                    rows={4}
                    style={{
                      width: '100%',
                      padding: '12px',
                      borderRadius: '8px',
                      border: '1px solid var(--border-light, #e5e7eb)',
                      fontSize: '14px',
                      resize: 'vertical',
                      fontFamily: 'inherit',
                      backgroundColor: 'var(--bg-secondary, #f9fafb)',
                      boxSizing: 'border-box',
                    }}
                  />

                  {/* Info */}
                  <p
                    style={{
                      margin: '12px 0',
                      fontSize: '12px',
                      color: 'var(--text-tertiary, #9ca3af)',
                    }}
                  >
                    Your code, errors, and build history will be shared with the
                    expert for context.
                  </p>

                  {/* Error message */}
                  {error && (
                    <p
                      style={{
                        margin: '0 0 12px',
                        fontSize: '13px',
                        color: '#dc2626',
                      }}
                    >
                      {error}
                    </p>
                  )}

                  {/* Submit button */}
                  <button
                    onClick={handleSubmit}
                    disabled={isSubmitting}
                    style={{
                      width: '100%',
                      padding: '10px',
                      backgroundColor: isSubmitting ? '#9ca3af' : '#dc2626',
                      color: '#fff',
                      border: 'none',
                      borderRadius: '8px',
                      fontSize: '14px',
                      fontWeight: 600,
                      cursor: isSubmitting ? 'not-allowed' : 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '8px',
                    }}
                  >
                    {isSubmitting ? (
                      <>
                        <Loader2
                          size={16}
                          style={{ animation: 'spin 1.5s linear infinite' }}
                        />
                        Submitting...
                      </>
                    ) : (
                      <>
                        <Send size={16} />
                        Send SOS Request
                      </>
                    )}
                  </button>
                </>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      <style>{`
        @keyframes sos-pulse {
          0% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.7); }
          70% { box-shadow: 0 0 0 12px rgba(220, 38, 38, 0); }
          100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0); }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </>
  );
}
