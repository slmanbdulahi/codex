const API_BASE_URL = process.env.REACT_APP_API_BASE_URL;
const API_KEY = process.env.REACT_APP_API_KEY;

async function handleResponse(response) {
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || 'API request failed');
  }
  return response.json();
}

export async function createPayment(paymentData) {
  const response = await fetch(`${API_BASE_URL}/payments`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${API_KEY}`
    },
    body: JSON.stringify(paymentData)
  });
  return handleResponse(response);
}

export async function getPaymentStatus(paymentId) {
  const response = await fetch(`${API_BASE_URL}/payments/${paymentId}`, {
    headers: {
      'Authorization': `Bearer ${API_KEY}`
    }
  });
  return handleResponse(response);
}

export async function refundPayment(paymentId) {
  const response = await fetch(`${API_BASE_URL}/payments/${paymentId}/refund`, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${API_KEY}`
    }
  });
  return handleResponse(response);
}
