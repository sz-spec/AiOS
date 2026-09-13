// Provenance: 100% original VOS3 code, no external sources. Audited 2026-04-12.
'use client';

import { useState, useEffect } from 'react';
import { useKernel, KernelProcess } from '@/hooks/useKernel';

function formatUptime(ms: number): string {
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${h}h ${m}m ${sec}s`;
}

function formatMemory(kb: number): string {
  if (kb >= 1024) return `${(kb / 1024).toFixed(1)} MB`;
  return `${kb} KB`;
}

export default function KernelPage() {
  const {
    status,
    processes,
    filesystem,
    loading,
    error,
    fetchStatus,
    fetchProcesses,
    fetchFilesystem,
    // Desktop-only extensions
    isDesktop,
    startKernel,
    stopKernel,
    loadModel,
    kernelPath,
    setKernelPath,
    modelLoadProgress,
    qemuAlive,
    vbusConnected,
    vbusHmac,
    warpOpen,
    slots,
    fetchSlots,
    fetchSystemInfo,
  } = useKernel();

  const [autoRefresh, setAutoRefresh] = useState(true);
  const [currentPath, setCurrentPath] = useState('/disk');

  // Model load panel state
  const [modelPath, setModelPath] = useState('');
  const [slotId, setSlotId] = useState(0);

  // Initial load + auto-refresh
  useEffect(() => {
    fetchStatus();
    fetchProcesses();
    fetchFilesystem(currentPath);
    if (isDesktop) {
      fetchSystemInfo();
      fetchSlots();
    }

    if (!autoRefresh) return;
    const interval = setInterval(() => {
      fetchStatus();
      fetchProcesses();
      if (isDesktop) fetchSystemInfo();
    }, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, currentPath, fetchStatus, fetchProcesses, fetchFilesystem, isDesktop, fetchSlots, fetchSystemInfo]);

  const connected = status?.connected ?? false;
  const sysinfo = status?.sysinfo ?? {};

  return (
    <div style={{ padding: '24px', maxWidth: '1200px', margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <h1 style={{ fontSize: '24px', fontWeight: 700 }}>VOS3 Kernel Dashboard</h1>
        <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
          {isDesktop && (
            <span style={{
              padding: '2px 8px',
              borderRadius: '4px',
              fontSize: '11px',
              fontWeight: 600,
              background: '#3b82f622',
              color: '#3b82f6',
              textTransform: 'uppercase',
              letterSpacing: '0.05em',
            }}>
              Desktop
            </span>
          )}
          <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '14px', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            Auto-refresh
          </label>
          <button
            onClick={() => { fetchStatus(); fetchProcesses(); fetchFilesystem(currentPath); if (isDesktop) { fetchSystemInfo(); fetchSlots(); } }}
            style={{
              padding: '6px 16px',
              borderRadius: '6px',
              border: '1px solid var(--border-color, #333)',
              background: 'var(--bg-secondary, #1a1a1a)',
              color: 'var(--text-primary, #fff)',
              cursor: 'pointer',
              fontSize: '13px',
            }}
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div style={{ padding: '12px', background: '#3a1111', border: '1px solid #ff4444', borderRadius: '8px', marginBottom: '16px', color: '#ff8888', fontSize: '14px' }}>
          {error}
        </div>
      )}

      {/* Desktop: Start/Stop Kernel Panel */}
      {isDesktop && (
        <div style={{
          border: '1px solid var(--border-color, #333)',
          borderRadius: '8px',
          padding: '16px',
          marginBottom: '16px',
          background: 'var(--bg-secondary, #1a1a1a)',
        }}>
          <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '12px' }}>Kernel Lifecycle</h2>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <input
              type="text"
              value={kernelPath}
              onChange={(e) => setKernelPath(e.target.value)}
              placeholder="kernel/build/vos3.elf"
              style={{
                flex: 1,
                padding: '8px 12px',
                borderRadius: '6px',
                border: '1px solid var(--border-color, #333)',
                background: 'var(--bg-primary, #111)',
                color: 'var(--text-primary, #fff)',
                fontSize: '14px',
                fontFamily: 'monospace',
              }}
            />
            {!qemuAlive ? (
              <button
                onClick={() => startKernel(kernelPath)}
                disabled={loading}
                style={{
                  padding: '8px 20px',
                  borderRadius: '6px',
                  border: 'none',
                  background: '#22c55e',
                  color: '#fff',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  fontWeight: 600,
                  opacity: loading ? 0.6 : 1,
                }}
              >
                Start Kernel
              </button>
            ) : (
              <button
                onClick={() => stopKernel()}
                disabled={loading}
                style={{
                  padding: '8px 20px',
                  borderRadius: '6px',
                  border: 'none',
                  background: '#ef4444',
                  color: '#fff',
                  cursor: loading ? 'not-allowed' : 'pointer',
                  fontSize: '14px',
                  fontWeight: 600,
                  opacity: loading ? 0.6 : 1,
                }}
              >
                Stop Kernel
              </button>
            )}
          </div>
        </div>
      )}

      {/* System Status Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginBottom: '24px' }}>
        <StatusCard
          title="Connection"
          value={connected ? 'Online' : 'Offline'}
          color={connected ? '#22c55e' : '#ef4444'}
        />
        <StatusCard
          title="Uptime"
          value={sysinfo.uptime_ms ? formatUptime(sysinfo.uptime_ms) : '--'}
          color="#3b82f6"
        />
        <StatusCard
          title="Memory"
          value={sysinfo.mem_total_kb
            ? `${formatMemory(sysinfo.mem_free_kb ?? 0)} / ${formatMemory(sysinfo.mem_total_kb)}`
            : '--'}
          color="#a855f7"
        />
        <StatusCard
          title="Tasks"
          value={sysinfo.tasks != null ? String(sysinfo.tasks) : '--'}
          color="#f59e0b"
        />
      </div>

      {/* Desktop Status Cards */}
      {isDesktop && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '16px', marginBottom: '24px' }}>
          <StatusCard
            title="QEMU"
            value={qemuAlive ? 'Running' : 'Stopped'}
            color={qemuAlive ? '#22c55e' : '#ef4444'}
          />
          <StatusCard
            title="VBus"
            value={vbusHmac ? 'Secured' : vbusConnected ? 'Connected' : 'Disconnected'}
            color={vbusHmac ? '#22c55e' : vbusConnected ? '#f59e0b' : '#ef4444'}
          />
          <StatusCard
            title="Warp Drive"
            value={warpOpen ? 'Open' : 'Closed'}
            color={warpOpen ? '#22c55e' : '#6b7280'}
          />
        </div>
      )}

      {/* Process List */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '12px' }}>Running Processes</h2>
        <div style={{
          border: '1px solid var(--border-color, #333)',
          borderRadius: '8px',
          overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '14px' }}>
            <thead>
              <tr style={{ background: 'var(--bg-secondary, #1a1a1a)' }}>
                <th style={thStyle}>PID</th>
                <th style={thStyle}>Name</th>
                <th style={thStyle}>State</th>
                <th style={thStyle}>PPID</th>
              </tr>
            </thead>
            <tbody>
              {processes.length === 0 ? (
                <tr><td colSpan={4} style={{ padding: '16px', textAlign: 'center', color: '#888' }}>
                  {loading ? 'Loading...' : 'No processes'}
                </td></tr>
              ) : (
                processes.map((p: KernelProcess) => (
                  <tr key={p.pid} style={{ borderTop: '1px solid var(--border-color, #333)' }}>
                    <td style={tdStyle}>{p.pid}</td>
                    <td style={tdStyle}>{p.name}</td>
                    <td style={tdStyle}>
                      <span style={{
                        padding: '2px 8px',
                        borderRadius: '4px',
                        fontSize: '12px',
                        background: p.state === 'RUNNING' ? '#22c55e22' : p.state === 'READY' ? '#3b82f622' : '#f59e0b22',
                        color: p.state === 'RUNNING' ? '#22c55e' : p.state === 'READY' ? '#3b82f6' : '#f59e0b',
                      }}>
                        {p.state}
                      </span>
                    </td>
                    <td style={tdStyle}>{p.ppid}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Filesystem Browser */}
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '18px', fontWeight: 600, marginBottom: '12px' }}>Filesystem Browser</h2>
        <div style={{
          border: '1px solid var(--border-color, #333)',
          borderRadius: '8px',
          padding: '16px',
          background: 'var(--bg-secondary, #1a1a1a)',
        }}>
          <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
            <input
              type="text"
              value={currentPath}
              onChange={(e) => setCurrentPath(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && fetchFilesystem(currentPath)}
              style={{
                flex: 1,
                padding: '8px 12px',
                borderRadius: '6px',
                border: '1px solid var(--border-color, #333)',
                background: 'var(--bg-primary, #111)',
                color: 'var(--text-primary, #fff)',
                fontSize: '14px',
                fontFamily: 'monospace',
              }}
            />
            <button
              onClick={() => fetchFilesystem(currentPath)}
              style={{
                padding: '8px 16px',
                borderRadius: '6px',
                border: '1px solid var(--border-color, #333)',
                background: '#3b82f6',
                color: '#fff',
                cursor: 'pointer',
                fontSize: '13px',
              }}
            >
              Browse
            </button>
          </div>

          {filesystem && (
            <div>
              <div style={{ fontSize: '12px', color: '#888', marginBottom: '8px' }}>
                {filesystem.count} items in {filesystem.path}
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                {filesystem.files.map((f) => (
                  <div
                    key={f}
                    style={{
                      padding: '6px 12px',
                      borderRadius: '6px',
                      background: 'var(--bg-primary, #111)',
                      border: '1px solid var(--border-color, #333)',
                      fontSize: '13px',
                      fontFamily: 'monospace',
                      cursor: 'pointer',
                    }}
                    onClick={() => {
                      const newPath = `${currentPath}/${f}`.replace(/\/+/g, '/');
                      setCurrentPath(newPath);
                      fetchFilesystem(newPath);
                    }}
                  >
                    {f}
                  </div>
                ))}
                {filesystem.files.length === 0 && (
                  <span style={{ color: '#888', fontSize: '13px' }}>Empty directory</span>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Desktop: Model Load Panel */}
      {isDesktop && (
        <div style={{
          border: '1px solid var(--border-color, #333)',
          borderRadius: '8px',
          padding: '16px',
          marginBottom: '24px',
          background: 'var(--bg-secondary, #1a1a1a)',
        }}>
          <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '12px' }}>Model Loader</h2>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginBottom: '12px' }}>
            <input
              type="text"
              value={modelPath}
              onChange={(e) => setModelPath(e.target.value)}
              placeholder="/path/to/model.gguf"
              style={{
                flex: 1,
                padding: '8px 12px',
                borderRadius: '6px',
                border: '1px solid var(--border-color, #333)',
                background: 'var(--bg-primary, #111)',
                color: 'var(--text-primary, #fff)',
                fontSize: '14px',
                fontFamily: 'monospace',
              }}
            />
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
              <label style={{ fontSize: '13px', color: '#888' }}>Slot:</label>
              <input
                type="number"
                min={0}
                max={7}
                value={slotId}
                onChange={(e) => setSlotId(Math.min(7, Math.max(0, parseInt(e.target.value) || 0)))}
                style={{
                  width: '50px',
                  padding: '8px',
                  borderRadius: '6px',
                  border: '1px solid var(--border-color, #333)',
                  background: 'var(--bg-primary, #111)',
                  color: 'var(--text-primary, #fff)',
                  fontSize: '14px',
                  textAlign: 'center',
                }}
              />
            </div>
            <button
              onClick={() => loadModel(modelPath, slotId)}
              disabled={loading || !modelPath}
              style={{
                padding: '8px 20px',
                borderRadius: '6px',
                border: 'none',
                background: '#a855f7',
                color: '#fff',
                cursor: loading || !modelPath ? 'not-allowed' : 'pointer',
                fontSize: '14px',
                fontWeight: 600,
                opacity: loading || !modelPath ? 0.6 : 1,
              }}
            >
              Load Model
            </button>
          </div>

          {/* Progress bar */}
          {modelLoadProgress !== null && (
            <div style={{
              height: '8px',
              borderRadius: '4px',
              background: 'var(--bg-primary, #111)',
              overflow: 'hidden',
              marginBottom: '8px',
            }}>
              <div
                style={{
                  height: '100%',
                  width: `${modelLoadProgress}%`,
                  background: 'linear-gradient(90deg, #a855f7, #3b82f6)',
                  borderRadius: '4px',
                  transition: 'width 0.15s ease-out',
                }}
              />
            </div>
          )}

          {/* Slot enumeration */}
          {slots && (
            <div style={{ fontSize: '12px', color: '#888', fontFamily: 'monospace', whiteSpace: 'pre-wrap' }}>
              {slots}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// -- Sub-components --

function StatusCard({ title, value, color }: { title: string; value: string; color: string }) {
  return (
    <div style={{
      padding: '16px',
      borderRadius: '8px',
      border: '1px solid var(--border-color, #333)',
      background: 'var(--bg-secondary, #1a1a1a)',
    }}>
      <div style={{ fontSize: '12px', color: '#888', marginBottom: '4px' }}>{title}</div>
      <div style={{ fontSize: '20px', fontWeight: 700, color }}>{value}</div>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  padding: '10px 16px',
  textAlign: 'left',
  fontWeight: 600,
  fontSize: '12px',
  textTransform: 'uppercase',
  letterSpacing: '0.05em',
  color: '#888',
};

const tdStyle: React.CSSProperties = {
  padding: '10px 16px',
};
