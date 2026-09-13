const { test } = require('node:test');
const assert = require('node:assert/strict');
const { VOS3Client, VOS3App } = require('../dist/index.js');

test('published client serializes authenticated requests and reports denied responses', async () => {
  const original = global.fetch;
  try {
    global.fetch = async (url, init) => {
      assert.equal(url, 'https://example.invalid/api/apps/v1/records');
      assert.equal(init.headers.Authorization, 'Bearer test-only-token');
      assert.equal(init.headers['X-VOS3-App-Id'], 'test-app');
      assert.deepEqual(JSON.parse(init.body), { entityId: 'entity', data: { value: 7 } });
      return new Response(JSON.stringify({ detail: 'denied' }), { status: 403 });
    };
    const client = new VOS3Client({ appId: 'test-app', apiKey: 'test-only-token', baseUrl: 'https://example.invalid' });
    assert.deepEqual(await client.createRecord('entity', { value: 7 }), { data: { detail: 'denied' }, ok: false, error: 'denied' });
  } finally { global.fetch = original; }
});
test('app awaits registered event handlers and keeps scope declarations', async () => {
  const app = new VOS3App({ appId: 'test-app', apiKey: 'test-only-token', scopes: ['vos3:records:read'] });
  const order = [];
  app.on('event', async () => { await Promise.resolve(); order.push(1); });
  app.on('event', () => { order.push(2); });
  await app.emit('event');
  assert.deepEqual(order, [1, 2]);
  assert.equal(app.hasScope('vos3:records:read'), true);
  assert.equal(app.hasScope('vos3:records:write'), false);
});
