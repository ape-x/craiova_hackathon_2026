# Passenger email with Resend

AIR uses **Resend** for passenger-offer emails. The operator opens a passenger group, enters the recipient, and explicitly sends the current offer link. Creating a plan, opening the form or accepting an itinerary never sends an email automatically.

## Configure

Add the following to private `backend/.env` for local Compose, or `.env.production` for production Compose:

```dotenv
NOTIFICATION_EMAIL_PROVIDER=resend
RESEND_API_KEY=
NOTIFICATION_EMAIL_FROM=
NOTIFICATION_EMAIL_FROM_NAME=AIR
NOTIFICATIONS_ALLOW_SIMULATION=false
```

Use a Resend API key with **Sending access**, restricted to the sending domain when possible. The `from` address must belong to a verified domain. For an initial test to the Resend account owner's own address, Resend permits `onboarding@resend.dev`; this sender does not enable arbitrary passenger recipients. Keep keys on the backend.

`PUBLIC_BASE_URL` must be the publicly reachable HTTPS origin serving AIR. Localhost links cannot work on a passenger's phone. Production Compose derives this origin from `APP_DOMAIN`. Recreate the API container after changing settings. Connections & session shows configuration status, not a guarantee of provider delivery.

Sending from simulation requires explicit `NOTIFICATIONS_ALLOW_SIMULATION=true` and marks the email as a simulation. Demo mode never sends real notifications. Automated tests replace the HTTP transport and never contact real recipients.

## API and outcomes

Existing operator authentication protects both routes:

- `GET /api/groups/{group_id}/notifications`: current-scenario history with masked recipients.
- `POST /api/groups/{group_id}/notifications`: send the current offer.

```json
{
  "channel": "email",
  "recipient": "passenger@example.com",
  "offer_id": "CURRENT_OFFER_ID",
  "idempotency_key": "a-unique-request-id"
}
```

The example address is a placeholder. The backend builds the message from the current, authoritative offer. Expired, invalidated, superseded or mismatched offers are rejected. The caller cannot choose a provider or supply an arbitrary body.

**Accepted** means that Resend accepted the request, not that it was delivered. **Failed** means an explicit provider rejection. **Unknown** means a timeout, connection loss, interrupted send or malformed response: check the provider before a new send. There are no automatic retries for an uncertain result. The same request key returns its saved outcome; different input under the same key is rejected. Resend also receives the stable delivery ID in its `Idempotency-Key` header. Local duplicate prevention survives process restarts and outlives the provider's 24-hour idempotency window.

Raw recipients, email bodies, private passenger links and API keys are not added to the world or audit exports. The private SQLite ledger stores masked recipients and hashes. Historical rows retain the actual provider, including earlier Brevo sends.

## Legacy compatibility

Brevo email remains available only when explicitly selected with `NOTIFICATION_EMAIL_PROVIDER=brevo` and `BREVO_API_KEY`. There is no fallback between providers. The existing SMS adapter is disabled in the current configuration (`NOTIFICATION_SMS_SENDER=`) and the passenger form offers email only.

## Provider references

- [Resend sending API](https://resend.com/docs/api-reference/emails/send-email)
- [Resend idempotency](https://resend.com/docs/dashboard/emails/idempotency-keys)
- [Sending-domain requirement and testing restriction](https://resend.com/docs/knowledge-base/403-error-resend-dev-domain)
