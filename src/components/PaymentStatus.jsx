import { useEffect, useState } from 'react';
import { getPaymentStatus } from '../services/api.js';

export default function PaymentStatus({ paymentId }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!paymentId) return;
    const controller = new AbortController();
    const fetchStatus = async () => {
      setLoading(true);
      setError(null);
      setStatus(null);
      try {
        const data = await getPaymentStatus(paymentId, { signal: controller.signal });
        if (!controller.signal.aborted) setStatus(data.status);
      } catch (err) {
        if (!controller.signal.aborted) setError(err.message);
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    fetchStatus();
    return () => controller.abort();
  }, [paymentId]);

  if (!paymentId) return null;

  return (
    <div>
      {loading && <p>Loading...</p>}
      {error && <p>Error: {error}</p>}
      {status && <p>Status: {status}</p>}
    </div>
  );
}
