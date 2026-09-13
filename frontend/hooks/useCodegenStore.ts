/**
 * Bridge hook for gradual migration from useCodegen (React hooks + fetch)
 * to the Zustand codegen store.
 *
 * Usage: Replace `useCodegen()` with `useCodegenStoreBridge()` in components.
 * The returned API shape matches the original useCodegen hook.
 */
import { useCallback } from 'react';
import { useAuth } from '@clerk/nextjs';
import {
  useCodegenStore,
  type GeneratedFile,
  type GenerationProgress,
  type ValidationResult,
} from '@/lib/stores/codegen-store';

export function useCodegenStoreBridge() {
  const { getToken } = useAuth();

  const generatedFiles = useCodegenStore((s) => s.generatedFiles);
  const isGenerating = useCodegenStore((s) => s.isGenerating);
  const progress = useCodegenStore((s) => s.progress);
  const error = useCodegenStore((s) => s.error);

  const storeGenerateCode = useCodegenStore((s) => s.generateCode);
  const storeGenerateProject = useCodegenStore((s) => s.generateProject);
  const storeValidateCode = useCodegenStore((s) => s.validateCode);
  const storeClearFiles = useCodegenStore((s) => s.clearFiles);

  const generateCode = useCallback(
    (request: { prompt: string; language: string }) =>
      storeGenerateCode(request, getToken),
    [storeGenerateCode, getToken],
  );

  const generateProject = useCallback(
    (request: { prompt: string }) =>
      storeGenerateProject(request, getToken),
    [storeGenerateProject, getToken],
  );

  const validateCode = useCallback(
    (code: string, language: string): Promise<ValidationResult> =>
      storeValidateCode(code, language, getToken),
    [storeValidateCode, getToken],
  );

  const clearFiles = storeClearFiles;

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

export type { GeneratedFile, GenerationProgress, ValidationResult };
