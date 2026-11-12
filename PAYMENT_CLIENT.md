# Payment client integration

These React components require an authenticated, same-origin backend. The payment and refund controls start disabled. Mount each component with a stable React key for its invoice or payment. Pass the server-issued CSRF token and the corresponding server-computed capability to enable a control.

The host must implement the following endpoints before enabling the controls:

| Endpoint | Required server checks |
| --- | --- |
| `POST /api/payments` | Authenticate the session, validate CSRF, authorize the invoice, and check the submitted decimal amount against the authoritative invoice and currency. |
| `GET /api/payments/{id}` | Authenticate the session and authorize access to the specific payment. |
| `POST /api/payments/{id}/refund` | Authenticate the session, validate CSRF, require explicit refund permission for the payment, and validate the refundable balance. |

The server must scope idempotency keys to the caller and operation, persist attempts, handle provider retries, and keep provider credentials in server-only configuration. The browser sends a decimal string for amounts and sends mutation idempotency keys with `Idempotency-Key`. Server authorization applies independently of component visibility or props.

This branch contains client components and transport tests. Keep payment and refund actions disabled until a backend has been integrated and authorization, provider failures, and duplicate requests have passed end-to-end tests. Use `npm ci`, `npm test`, and `npm run build` to verify the client and generate its bundles.
