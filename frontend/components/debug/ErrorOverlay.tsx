/**
 * ErrorOverlay — Phase 3.0 Subsystem 3.2
 * ========================================
 * Provides Monaco editor decorations for runtime errors captured from
 * the Sandpack preview. Shows red underlines at error locations with
 * hover tooltips containing error messages and "Send to AI" actions.
 *
 * Usage:
 *   const decorations = buildErrorDecorations(errors);
 *   // Apply to Monaco editor via deltaDecorations
 */

'use client';

import { useMemo } from 'react';
import type { ConsoleEntry } from '@/hooks/useConsoleCapture';

// ─── Types ───────────────────────────────────────────────────────────────────

export interface ErrorDecoration {
  /** The file this error belongs to (project-relative path). */
  file: string;
  /** 1-based line number in the file. */
  line: number;
  /** The error message for the hover tooltip. */
  message: string;
  /** The full stack trace (if available). */
  stackTrace?: string;
  /** The original console entry for "Send to AI" action. */
  entry: ConsoleEntry;
}

export interface MonacoDecoration {
  range: {
    startLineNumber: number;
    startColumn: number;
    endLineNumber: number;
    endColumn: number;
  };
  options: {
    isWholeLine: boolean;
    className: string;
    glyphMarginClassName: string;
    hoverMessage: { value: string };
    overviewRuler: {
      color: string;
      position: number;
    };
  };
}

// ─── Decoration Builder ──────────────────────────────────────────────────────

/**
 * Convert captured console errors into Monaco editor decorations.
 *
 * Only includes errors that have a resolvable file + line.
 * Returns decorations grouped by file for efficient application.
 */
export function buildErrorDecorations(errors: ConsoleEntry[]): ErrorDecoration[] {
  return errors
    .filter((e) => e.file && e.line && e.line > 0)
    .map((e) => ({
      file: e.file!,
      line: e.line!,
      message: e.message,
      stackTrace: e.stackTrace,
      entry: e,
    }));
}

/**
 * Convert ErrorDecorations into Monaco-compatible decoration objects
 * for a specific file. Call editor.deltaDecorations(oldIds, newDecorations).
 */
export function toMonacoDecorations(
  decorations: ErrorDecoration[],
  activeFile: string,
): MonacoDecoration[] {
  return decorations
    .filter((d) => d.file === activeFile)
    .map((d) => ({
      range: {
        startLineNumber: d.line,
        startColumn: 1,
        endLineNumber: d.line,
        endColumn: 1,
      },
      options: {
        isWholeLine: true,
        className: 'error-line-decoration',
        glyphMarginClassName: 'error-glyph',
        hoverMessage: {
          value: `**Error**: ${d.message}\n\n*Click "AI Fix" in the console to analyze this error.*`,
        },
        overviewRuler: {
          color: '#ef4444',
          position: 4, // OverviewRulerLane.Full
        },
      },
    }));
}

// ─── CSS Injection ───────────────────────────────────────────────────────────

/**
 * CSS styles for error decorations. Inject once into the document.
 * This uses Monaco's built-in decoration class system.
 */
export const ERROR_DECORATION_CSS = `
.error-line-decoration {
  background-color: rgba(239, 68, 68, 0.12) !important;
  border-bottom: 2px wavy #ef4444;
}
.error-glyph {
  background-color: #ef4444;
  border-radius: 50%;
  width: 8px !important;
  height: 8px !important;
  margin-left: 4px;
  margin-top: 6px;
}
`;

// ─── React Hook ──────────────────────────────────────────────────────────────

/**
 * Hook that computes error decorations for the currently active file.
 *
 * @param errors - Console error entries from useConsoleCapture
 * @param activeFile - The currently open file path in the editor
 * @returns Monaco decorations to apply via deltaDecorations
 */
export function useErrorDecorations(
  errors: ConsoleEntry[],
  activeFile: string,
) {
  const decorations = useMemo(
    () => buildErrorDecorations(errors),
    [errors],
  );

  const monacoDecorations = useMemo(
    () => toMonacoDecorations(decorations, activeFile),
    [decorations, activeFile],
  );

  const errorFiles = useMemo(
    () => [...new Set(decorations.map((d) => d.file))],
    [decorations],
  );

  return {
    decorations,
    monacoDecorations,
    errorFiles,
    errorCount: decorations.length,
  };
}
