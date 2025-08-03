import { useState } from 'react';
import { refundPayment } from '../services/api';

export default function RefundButton({ paymentId }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [success, setSuccess] = useState(false);

  const handleRefund = async () => {
    setLoading(true);
    setError(null);
    setSuccess(false);
    try {
      await refundPayment(paymentId);
      setSuccess(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <button onClick={handleRefund} disabled={loading}>
        Refund
      </button>
      {loading && <p>Processing...</p>}
      {error && <p>Error: {error}</p>}
      {success && <p>Refunded</p>}
    </div>
  );
}
