import { createHmac, timingSafeEqual } from 'node:crypto';

export function requireWebhookSecret(secret: string): void {
  if (!secret.trim()) throw new Error('GITHUB_WEBHOOK_SECRET is required');
}

/** Verify the exact raw request bytes; malformed public input never throws. */
export function verifyGitHubSignature(payload: string | Buffer, signature: unknown, secret: string): boolean {
  if (!secret.trim() || typeof signature !== 'string' || !/^sha256=[0-9a-f]{64}$/.test(signature)) return false;
  const expected = createHmac('sha256', secret).update(payload).digest();
  const supplied = Buffer.from(signature.slice(7), 'hex');
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}
