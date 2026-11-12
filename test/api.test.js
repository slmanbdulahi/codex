import assert from 'node:assert/strict';
import { test } from 'node:test';

import { createPayment, getPaymentStatus, refundPayment } from '../src/services/api.js';

test('payment uses a same-origin session, decimal string, CSRF, and retry key', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ id: 'p_1' }));
  const input = { invoiceId: 'invoice_1', amount: '0.10' };
  const options = { csrfToken: 'test-csrf', requestId: 'test-request' };
  assert.deepEqual(await createPayment(input, options), { id: 'p_1' });
  await createPayment(input, options);
  assert.deepEqual(fetch.mock.calls[0].arguments, fetch.mock.calls[1].arguments);
  const [url, init] = fetch.mock.calls[0].arguments;
  assert.equal(url, '/api/payments');
  assert.equal(init.credentials, 'same-origin');
  assert.equal(init.redirect, 'error');
  assert.equal(init.headers.Authorization, undefined);
  assert.equal(init.headers['X-CSRF-Token'], options.csrfToken);
  assert.equal(init.headers['Idempotency-Key'], options.requestId);
  assert.deepEqual(JSON.parse(init.body), input);
});

test('mutations without CSRF and invalid identifiers fail before issuing a request', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch');
  await assert.rejects(refundPayment('p_1'), /server session/);
  await assert.rejects(getPaymentStatus('../outside'), /Invalid/);
  await assert.rejects(createPayment({ invoiceId: 'invoice_1', amount: '0.001' }), /positive amount/);
  assert.equal(fetch.mock.callCount(), 0);
});

test('refund uses a POST and surfaces a generic server authorization failure', async (t) => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response('private provider response', { status: 403 }));
  const options = { csrfToken: 'test-csrf', requestId: 'test-refund' };
  await assert.rejects(refundPayment('p_1', options), { message: 'The payment service could not complete this request' });
  const [url, init] = fetch.mock.calls[0].arguments;
  assert.equal(url, '/api/payments/p_1/refund');
  assert.equal(init.method, 'POST');
  assert.equal(init.headers['Idempotency-Key'], options.requestId);
});
