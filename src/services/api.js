const API_ROOT = '/api/payments';

function resourceId(value) {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9_-]{1,128}$/.test(value)) {
    throw new Error('Invalid payment or invoice ID');
  }
  return value;
}

async function request(path, options) {
  const response = await fetch(path, {
    credentials: 'same-origin',
    redirect: 'error',
    cache: 'no-store',
    ...options
  });
  if (!response.ok) {
    throw new Error('The payment service could not complete this request');
  }
  return response;
}

function mutation(csrfToken, requestId, body) {
  if (!csrfToken || !requestId) {
    throw new Error('Payment actions require a server session and request ID');
  }
  return {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRF-Token': csrfToken,
      'Idempotency-Key': requestId
    },
    body: JSON.stringify(body)
  };
}

export async function createPayment({ invoiceId, amount }, { csrfToken, requestId } = {}) {
  const id = resourceId(invoiceId);
  if (typeof amount !== 'string' || !/^[0-9]{1,10}(?:\.[0-9]{1,2})?$/.test(amount) || !/[1-9]/.test(amount)) {
    throw new Error('Enter a positive amount with at most two decimal places');
  }
  const response = await request(API_ROOT, mutation(csrfToken, requestId, { invoiceId: id, amount }));
  return response.json();
}

export async function getPaymentStatus(paymentId, { signal } = {}) {
  const response = await request(`${API_ROOT}/${resourceId(paymentId)}`, { signal });
  return response.json();
}

export async function refundPayment(paymentId, { csrfToken, requestId } = {}) {
  await request(`${API_ROOT}/${resourceId(paymentId)}/refund`, mutation(csrfToken, requestId, {}));
}
