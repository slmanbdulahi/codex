import { useState } from 'react';
import { createPayment } from '../services/api';

export default function PaymentForm() {
  const [amount, setAmount] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const data = await createPayment({ amount: parseFloat(amount) });
      setResult(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <form onSubmit={handleSubmit}>
      <input
        type="number"
        value={amount}
        onChange={(e) => setAmount(e.target.value)}
        placeholder="Amount"
      />
      <button type="submit" disabled={loading}>Pay</button>
      {loading && <p>Processing...</p>}
      {error && <p>Error: {error}</p>}
      {result && <p>Payment ID: {result.id}</p>}
    </form>
  );
}
