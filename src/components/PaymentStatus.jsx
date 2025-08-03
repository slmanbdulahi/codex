import { useEffect, useState } from 'react';
import { getPaymentStatus } from '../services/api';

export default function PaymentStatus({ paymentId }) {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!paymentId) return;
    const fetchStatus = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await getPaymentStatus(paymentId);
        setStatus(data.status);
      } catch (err) {
        setError(err.message);
      } finally {
        setLoading(false);
      }
    };
    fetchStatus();
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
