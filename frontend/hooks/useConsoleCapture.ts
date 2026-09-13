/**
 * useConsoleCapture — Phase 3.0 Subsystem 3.2
 * =============================================
 * Captures console.log/error/warn/info from the Sandpack preview iframe
 * via useSandpackConsole(). Parses stack traces, maps Sandpack virtual
 * paths to project paths, and maintains a circular buffer of entries.
 */

import { useState, useCallback, useMemo, useEffect, useRef } from 'react';

// Sandpack console hook — imported only when inside SandpackProvider
let useSandpackConsole: (() => { logs: SandpackLog[] }) | null = null;
try {
  const sandpack = require('@codesandbox/sandpack-react');
  useSandpackConsole = sandpack.useSandpackConsole;
} catch {
  // Not inside SandpackProvider or package not available
}

interface SandpackLog {
  data?: string[];
  method?: string;
  id?: string;
}

// ─── Public Types ────────────────────────────────────────────────────────────

export interface ConsoleEntry {
  id: string;
  type: 'log' | 'error' | 'warn' | 'info';
  message: string;
  timestamp: number;
  file?: string;
  line?: number;
  column?: number;
  stackTrace?: string;
}

export interface UseConsoleCaptureResult {
  logs: ConsoleEntry[];
  errors: ConsoleEntry[];
  warnings: ConsoleEntry[];
  clear: () => void;
  entryCount: number;
}

// ─── Constants ───────────────────────────────────────────────────────────────

const MAX_ENTRIES = 500;

// ─── Stack Trace Parsing ─────────────────────────────────────────────────────

const FRAME_PATTERNS = [
  // Chrome/V8: "at fn (file.tsx:10:5)"
  /^\s*at\s+(.+?)\s*\((.+?):(\d+):(\d+)\)/,
  // Chrome/V8 anonymous: "at file.tsx:10:5"
  /^\s*at\s+(.+?):(\d+):(\d+)/,
  // Firefox: "fn@file.tsx:10:5"
  /^(.+?)@(.+?):(\d+):(\d+)/,
];

function parseFirstFrame(stackTrace: string): {
  file?: string;
  line?: number;
  column?: number;
} {
  if (!stackTrace) return {};

  for (const line of stackTrace.split('\n')) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    for (const pattern of FRAME_PATTERNS) {
      const match = trimmed.match(pattern);
      if (match) {
        // Pattern 1 & 3 have fn at group(1), file at group(2)
        // Pattern 2 has file at group(1), no fn
        const groups = match.length === 5
          ? { file: match[2], line: match[3], col: match[4] }
          : { file: match[1], line: match[2], col: match[3] };

        return {
          file: mapSandpackPath(groups.file),
          line: parseInt(groups.line, 10),
          column: parseInt(groups.col, 10),
        };
      }
    }
  }

  return {};
}

function mapSandpackPath(virtualPath: string): string {
  if (!virtualPath) return virtualPath;
  // Skip node_modules paths
  if (virtualPath.includes('node_modules')) return virtualPath;
  // Strip leading slash for project files
  return virtualPath.replace(/^\//, '');
}

// ─── Secret Scrubbing — SECURITY-CRITICAL ────────────────────────────────────

const SECRET_PATTERNS: [RegExp, string][] = [
  // Stripe keys: sk_live_*, sk_test_*, pk_live_*, pk_test_*
  [/(sk|pk|rk)_(live|test)_[A-Za-z0-9]{10,}/g, '[REDACTED_STRIPE_KEY]'],
  // AWS Access Key IDs
  [/AKIA[A-Z0-9]{16}/g, '[REDACTED_AWS_KEY]'],
  // GitHub PATs
  [/gh[pousr]_[A-Za-z0-9]{36,}/g, '[REDACTED_GITHUB_TOKEN]'],
  // Slack tokens
  [/xox[bpar]-[A-Za-z0-9\-]{10,}/g, '[REDACTED_SLACK_TOKEN]'],
  // JWT/Bearer tokens in Authorization headers
  [/(Authorization:\s*Bearer\s+)([A-Za-z0-9._\-]{20,})/g, '$1[REDACTED_BEARER_TOKEN]'],
  // Standalone Bearer + JWT
  [/(Bearer\s+)(eyJ[A-Za-z0-9._\-]{20,})/g, '$1[REDACTED_JWT]'],
  // Generic password/secret/api_key assignments
  [/((?:password|secret|api_key|apikey|api_secret|access_token|refresh_token)\s*[=:]\s*)['"]?([A-Za-z0-9/+=\-_.]{8,})['"]?/gi, '$1[REDACTED]'],
  // PEM private keys
  [/-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----/g, '[REDACTED_PRIVATE_KEY]'],
];

/**
 * Remove sensitive secrets from text before storing or sending to AI.
 * Scrubs: Stripe keys, AWS keys, GitHub PATs, Slack tokens, JWT/Bearer tokens,
 * password assignments, PEM private keys.
 */
export function scrubSecrets(text: string): string {
  if (!text) return text;
  let result = text;
  for (const [pattern, replacement] of SECRET_PATTERNS) {
    // Reset lastIndex for global regexes
    pattern.lastIndex = 0;
    result = result.replace(pattern, replacement);
  }
  return result;
}

// ─── Hook ────────────────────────────────────────────────────────────────────

export function useConsoleCapture(): UseConsoleCaptureResult {
  const [entries, setEntries] = useState<ConsoleEntry[]>([]);
  const idCounter = useRef(0);

  // Try to use Sandpack console if available
  const sandpackLogs = useSandpackConsole?.()?.logs;

  // Process new Sandpack logs
  useEffect(() => {
    if (!sandpackLogs || sandpackLogs.length === 0) return;

    const newEntries: ConsoleEntry[] = sandpackLogs.map((log) => {
      const method = (log.method || 'log') as ConsoleEntry['type'];
      const message = Array.isArray(log.data)
        ? log.data.map((d) => (typeof d === 'string' ? d : JSON.stringify(d))).join(' ')
        : String(log.data ?? '');

      // Extract stack trace from error messages
      const stackTrace = method === 'error' ? extractStackTrace(message) : undefined;
      const frameInfo = stackTrace ? parseFirstFrame(stackTrace) : {};

      idCounter.current += 1;

      return {
        id: `console-${idCounter.current}`,
        type: method === 'error' || method === 'warn' || method === 'info' ? method : 'log',
        message: scrubSecrets(cleanMessage(message)),
        timestamp: Date.now(),
        stackTrace: stackTrace ? scrubSecrets(stackTrace) : undefined,
        ...frameInfo,
      };
    });

    setEntries((prev) => {
      const combined = [...prev, ...newEntries];
      // Circular buffer: keep last MAX_ENTRIES
      return combined.length > MAX_ENTRIES
        ? combined.slice(combined.length - MAX_ENTRIES)
        : combined;
    });
  }, [sandpackLogs]);

  const clear = useCallback(() => {
    setEntries([]);
  }, []);

  const errors = useMemo(
    () => entries.filter((e) => e.type === 'error'),
    [entries],
  );

  const warnings = useMemo(
    () => entries.filter((e) => e.type === 'warn'),
    [entries],
  );

  return {
    logs: entries,
    errors,
    warnings,
    clear,
    entryCount: entries.length,
  };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function extractStackTrace(message: string): string | undefined {
  const stackIndex = message.indexOf('\n    at ');
  if (stackIndex === -1) {
    // Try Firefox format
    const firefoxIndex = message.indexOf('\n@');
    if (firefoxIndex === -1) return undefined;
    return message.slice(firefoxIndex + 1);
  }
  return message.slice(stackIndex + 1);
}

function cleanMessage(message: string): string {
  // Remove stack trace from display message (keep just the error line)
  const stackIndex = message.indexOf('\n    at ');
  if (stackIndex !== -1) return message.slice(0, stackIndex);
  return message;
}
