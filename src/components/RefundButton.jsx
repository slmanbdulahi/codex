import { useRef, useState } from 'react';
import { refundPayment } from '../services/api.js';

export default function RefundButton({ paymentId, csrfToken, canRefund = false }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);
  const pendingRequest = useRef(null);
  const submitting = useRef(false);
  const enabled = canRefund && Boolean(csrfToken && paymentId);

  const handleRefund = async () => {
    if (!enabled || submitting.current || success) return;
    submitting.current = true;
    setLoading(true);
    setError(null);
    setSuccess(false);
    try {
      if (!pendingRequest.current) pendingRequest.current = crypto.randomUUID();
      await refundPayment(paymentId, { csrfToken, requestId: pendingRequest.current });
      setSuccess(true);
    } catch (err) {
      setError(err.message);
    } finally {
      submitting.current = false;
      setLoading(false);
    }
  };

  return (
    <div>
      <button type="button" onClick={handleRefund} disabled={!enabled || loading || success}>
        Refund
      </button>
      {loading && <p>Processing...</p>}
      {error && <p>Error: {error}</p>}
      {success && <p>Refunded</p>}
    </div>
  );
}
