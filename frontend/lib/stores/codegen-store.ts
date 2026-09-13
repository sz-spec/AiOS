import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';

// ---------------------------------------------------------------------------
// Types (mirrored from hooks/useCodegen.ts)
// ---------------------------------------------------------------------------

export interface GeneratedFile {
  path: string;
  content: string;
  language: string;
}

export interface GenerationProgress {
  stage: string;
  percent: number;
  message: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

interface CodegenState {
  generatedFiles: GeneratedFile[];
  isGenerating: boolean;
  progress: GenerationProgress | null;
  error: string | null;

  generateCode: (
    request: { prompt: string; language: string },
    getToken: () => Promise<string | null>,
  ) => Promise<void>;
  generateProject: (
    request: { prompt: string },
    getToken: () => Promise<string | null>,
  ) => Promise<void>;
  validateCode: (
    code: string,
    language: string,
    getToken: () => Promise<string | null>,
  ) => Promise<ValidationResult>;
  clearFiles: () => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const authHeader = (token: string | null): Record<string, string> =>
  token ? { Authorization: `Bearer ${token}` } : {};

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useCodegenStore = create<CodegenState>()(
  immer((set) => ({
    generatedFiles: [],
    isGenerating: false,
    progress: null,
    error: null,

    generateCode: async (request, getToken) => {
      set((s) => {
        s.isGenerating = true;
        s.error = null;
        s.progress = { stage: 'analyzing', percent: 10, message: 'Analyzing request...' };
      });

      try {
        set((s) => {
          s.progress = { stage: 'generating', percent: 50, message: 'Generating code...' };
        });

        const token = await getToken();
        const response = await fetch('/api/codegen/generate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeader(token) },
          body: JSON.stringify(request),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const data = await response.json();

        set((s) => {
          s.generatedFiles = [
            {
              path: `generated.${request.language}`,
              content: data.code,
              language: request.language,
            },
          ];
          s.progress = { stage: 'complete', percent: 100, message: 'Generation complete!' };
        });
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Generation failed';
        set((s) => {
          s.error = errorMessage;
          s.progress = null;
        });
      } finally {
        set((s) => {
          s.isGenerating = false;
        });
      }
    },

    generateProject: async (request, getToken) => {
      set((s) => {
        s.isGenerating = true;
        s.error = null;
        s.progress = { stage: 'planning', percent: 5, message: 'Planning project structure...' };
      });

      try {
        set((s) => {
          s.progress = { stage: 'generating', percent: 30, message: 'Generating files...' };
        });

        const token = await getToken();
        const response = await fetch('/api/codegen/generate/project', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeader(token) },
          body: JSON.stringify(request),
        });

        if (!response.ok) {
          throw new Error(`HTTP error: ${response.status}`);
        }

        const data = await response.json();

        set((s) => {
          s.generatedFiles = data.files;
          s.progress = { stage: 'complete', percent: 100, message: 'Project generated!' };
        });
      } catch (err) {
        const errorMessage = err instanceof Error ? err.message : 'Generation failed';
        set((s) => {
          s.error = errorMessage;
          s.progress = null;
        });
      } finally {
        set((s) => {
          s.isGenerating = false;
        });
      }
    },

    validateCode: async (code, language, getToken) => {
      try {
        const token = await getToken();
        const response = await fetch('/api/codegen/validate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeader(token) },
          body: JSON.stringify({ code, language }),
        });
        return response.json();
      } catch {
        return { valid: false, errors: ['Validation request failed'], warnings: [] };
      }
    },

    clearFiles: () => {
      set((s) => {
        s.generatedFiles = [];
        s.progress = null;
        s.error = null;
      });
    },
  })),
);
