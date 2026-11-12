import { useRef, useState } from 'react';
import { createPayment } from '../services/api.js';

export default function PaymentForm({ invoiceId, csrfToken, canCreate = false }) {
  const [amount, setAmount] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const pendingRequest = useRef(null);
  const submitting = useRef(false);
  const enabled = canCreate && Boolean(csrfToken && invoiceId);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!enabled || submitting.current || result) return;
    submitting.current = true;
    setLoading(true);
    setError(null);
    try {
      if (!pendingRequest.current) pendingRequest.current = crypto.randomUUID();
      const data = await createPayment({ invoiceId, amount }, { csrfToken, requestId: pendingRequest.current });
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      submitting.current = false;
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <input
        type="number"
        min="0.01"
        step="0.01"
        max="1000000000"
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        placeholder="Amount"
        disabled={!enabled || Boolean(pendingRequest.current)}
        required
      />
      <button type="submit" disabled={!enabled || loading || Boolean(result)}>Pay</button>
      {loading && <p>Processing...</p>}
      {error && <p>Error: {error}</p>}
      {result && <p>Payment ID: {result.id}</p>}
    </form>
  );
}
