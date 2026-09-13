import { useState, useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import { apiFetch } from '@/lib/api-client';

interface GeneratedFile {
  path: string;
  content: string;
  language: string;
}

interface GenerationProgress {
  stage: string;
  percent: number;
  message: string;
}

export function useCodegen() {
  // Phase v17 (F-H4): Clerk auth token for all API requests
  const { getToken } = useAuth();

  const [generatedFiles, setGeneratedFiles] = useState<GeneratedFile[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [progress, setProgress] = useState<GenerationProgress | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generateCode = useCallback(async (request: { prompt: string; language: string }) => {
    setIsGenerating(true);
    setError(null);
    setProgress({ stage: 'analyzing', percent: 10, message: 'Analyzing request...' });

    try {
      setProgress({ stage: 'generating', percent: 50, message: 'Generating code...' });

      const response = await apiFetch(getToken, `/api/codegen/generate`, {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      const data = await response.json();

      setGeneratedFiles([
        {
          path: `generated.${request.language}`,
          content: data.code,
          language: request.language,
        },
      ]);

      setProgress({ stage: 'complete', percent: 100, message: 'Generation complete!' });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Generation failed';
      setError(errorMessage);
      setProgress(null);
    } finally {
      setIsGenerating(false);
    }
  }, [getToken]);

  const generateProject = useCallback(async (request: { prompt: string }) => {
    setIsGenerating(true);
    setError(null);
    setProgress({ stage: 'planning', percent: 5, message: 'Planning project structure...' });

    try {
      setProgress({ stage: 'generating', percent: 30, message: 'Generating files...' });

      const response = await apiFetch(getToken, `/api/codegen/generate/project`, {
        method: 'POST',
        body: JSON.stringify(request),
      });

      if (!response.ok) {
        throw new Error(`HTTP error: ${response.status}`);
      }

      const data = await response.json();
      setGeneratedFiles(data.files);
      setProgress({ stage: 'complete', percent: 100, message: 'Project generated!' });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Generation failed';
      setError(errorMessage);
      setProgress(null);
    } finally {
      setIsGenerating(false);
    }
  }, [getToken]);

  const validateCode = useCallback(async (code: string, language: string) => {
    try {
      const response = await apiFetch(getToken, `/api/codegen/validate`, {
        method: 'POST',
        body: JSON.stringify({ code, language }),
      });
      return response.json();
    } catch (err) {
      return { valid: false, errors: ['Validation request failed'], warnings: [] };
    }
  }, [getToken]);

  const clearFiles = useCallback(() => {
    setGeneratedFiles([]);
    setProgress(null);
    setError(null);
  }, []);

  return {
    generatedFiles,
    isGenerating,
    progress,
    error,
    generateCode,
    generateProject,
    validateCode,
    clearFiles,
  };
}
