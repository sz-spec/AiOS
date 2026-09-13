import { vi } from 'vitest';

export const useQuery = vi.fn(() => undefined);
export const useMutation = vi.fn(() => vi.fn());
export const useAction = vi.fn(() => vi.fn());
export const useConvex = vi.fn();
