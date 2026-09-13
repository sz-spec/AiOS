const { test } = require('node:test');
const assert = require('node:assert/strict');
const { createHmac } = require('node:crypto');
const { spawnSync } = require('node:child_process');
const { requireWebhookSecret, verifyGitHubSignature } = require('../dist/webhook_auth.js');
const payload = Buffer.from('{"action":"opened"}');
const secret = 'test-only-webhook-secret';
const signature = 'sha256=' + createHmac('sha256', secret).update(payload).digest('hex');
test('accepts exact signed payload and rejects changed bytes or key', () => {
  assert.equal(verifyGitHubSignature(payload, signature, secret), true);
  assert.equal(verifyGitHubSignature(Buffer.concat([payload, Buffer.from(' ')]), signature, secret), false);
  assert.equal(verifyGitHubSignature(payload, signature, 'wrong-key'), false);
});
test('rejects missing secret and malformed signatures without exceptions', () => {
  for (const value of [undefined, '', 'sha256=a', 'sha256='+'z'.repeat(64), [], 'sha1='+'a'.repeat(64), signature+'a']) {
    assert.equal(verifyGitHubSignature(payload, value, secret), false);
  }
  assert.equal(verifyGitHubSignature(payload, signature, ''), false);
  assert.throws(() => requireWebhookSecret('  '), /required/);
});
test('actual server fails before opening services when secret is missing', () => {
  const result = spawnSync(process.execPath, ['dist/webhook_server.js'], {
    cwd: require('node:path').join(__dirname, '..'), env: { ...process.env, GITHUB_WEBHOOK_SECRET: '' }, encoding: 'utf8', timeout: 5000,
  });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /GITHUB_WEBHOOK_SECRET is required/);
});
test('Bull timer UUID generation remains compatible with the patched dependency', () => {
  const TimerManager = require('bull/lib/timer-manager');
  const manager = new TimerManager();
  const id = manager.set('migration-probe', 60000, () => {});
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  manager.clear(id);
  assert.equal(manager.idle, true);
});
