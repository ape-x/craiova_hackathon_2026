import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import {
  createPassengerNotificationSession,
  notificationStatusLabel,
  notificationUnknownInstruction,
  notificationUnavailableReason,
  PassengerNotifications,
  type NotificationDelivery,
} from './PassengerNotifications';
import type { AppConfig } from './types';

const config: AppConfig = {
  notification_available: true,
  notification_email_configured: true,
  notification_sms_configured: true,
};
const delivery = (overrides: Partial<NotificationDelivery> = {}): NotificationDelivery => ({
  id: 'N1',
  group_id: 'G1',
  offer_id: 'O1',
  channel: 'email',
  recipient_masked: 'p***@example.com',
  status: 'accepted',
  provider: 'resend',
  provider_message_id: 'provider-1',
  created_at: '2026-11-01T11:00:00Z',
  updated_at: '2026-11-01T11:00:00Z',
  error_code: null,
  error_message: null,
  ...overrides,
});
const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('passenger notification flow', () => {
  it('defaults email configuration guidance to Resend while keeping SMS on Brevo', () => {
    expect(notificationUnavailableReason({}, 'email', 'O1')).toContain('Configure Resend email');
    expect(notificationUnavailableReason({}, 'sms', 'O1')).toContain('Configure Brevo SMS');
    expect(
      notificationUnavailableReason(
        { ...config, notification_email_configured: false },
        'email',
        'O1',
      ),
    ).toBe('Email is not configured in Resend.');
    expect(
      notificationUnavailableReason(
        { ...config, notification_email_configured: false, notification_email_provider: 'brevo' },
        'email',
        'O1',
      ),
    ).toBe('Email is not configured in Brevo.');
    expect(
      notificationUnavailableReason(
        { ...config, notification_sms_configured: false, notification_email_provider: 'resend' },
        'sms',
        'O1',
      ),
    ).toBe('SMS is not configured in Brevo.');
  });

  it.each([
    ['email', 'resend', 'Resend', 'passenger@example.com'],
    ['sms', 'brevo', 'Brevo', '+40722123456'],
    ['email', 'brevo', 'Brevo', 'passenger@example.com'],
  ] as const)(
    'labels %s receipts with their actual %s provider',
    async (channel, provider, name, recipient) => {
      const fetcher = vi.fn().mockResolvedValue(Response.json(delivery({ channel, provider })));
      vi.stubGlobal('fetch', fetcher);
      const session = createPassengerNotificationSession('G1', channel);
      session.setRecipient(recipient);
      await session.send('O1', config);
      const result = session.getSnapshot().result!;
      expect(result.provider).toBe(provider);
      expect(notificationStatusLabel(result.status, result.provider)).toBe(`Accepted by ${name}`);
      expect(notificationStatusLabel('failed', result.provider)).toBe(`Not accepted by ${name}`);
      expect(notificationUnknownInstruction(result.provider)).toBe(
        `Check ${name} before trying again.`,
      );
      expect(JSON.parse(fetcher.mock.calls[0][1].body)).not.toHaveProperty('provider');
    },
  );

  it('keeps mixed-provider history labels independent from the current email configuration', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        Response.json({
          notifications: [
            delivery({ id: 'new-email', provider: 'resend' }),
            delivery({ id: 'old-email', provider: 'brevo' }),
            delivery({
              id: 'sms',
              channel: 'sms',
              provider: 'brevo',
              status: 'unknown',
              recipient_masked: '+40***456',
            }),
          ],
        }),
      ),
    );
    const session = createPassengerNotificationSession('G1');
    await session.refresh();
    expect(
      session
        .getSnapshot()
        .history.map((item) => notificationStatusLabel(item.status, item.provider)),
    ).toEqual(['Accepted by Resend', 'Accepted by Brevo', 'Delivery unconfirmed']);
    expect(notificationUnknownInstruction(session.getSnapshot().history[2].provider)).toBe(
      'Check Brevo before trying again.',
    );
  });

  it('never sends on render or when history loads; the email recipient label is explicit', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ notifications: [] }));
    vi.stubGlobal('fetch', fetcher);
    const html = renderToStaticMarkup(
      <PassengerNotifications groupId="G1" offerId="O1" config={config} />,
    );
    expect(fetcher).not.toHaveBeenCalled();
    expect(html).toContain('Recipient email');
    expect(html).toContain('Send offer link');
    expect(html).toContain('Notification history');
    expect(html).not.toContain('textarea');
    expect(html).toContain('Send offer by email');
    expect(html).toContain('type="email"');
    expect(html).not.toContain('type="radio"');
    expect(html).not.toContain('type="tel"');
    expect(html).not.toContain('SMS');
    expect(html).not.toContain('Recipient phone');
    const session = createPassengerNotificationSession('G1');
    await session.refresh();
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/groups/G1/notifications');
    expect(fetcher.mock.calls[0][1].method).toBe('GET');
  });

  it.each([
    [{}, 'email', 'O1'],
    [{ ...config, notification_available: false }, 'email', 'O1'],
    [{ ...config, notification_email_configured: false }, 'email', 'O1'],
    [{ ...config, notification_sms_configured: false }, 'sms', 'O1'],
    [config, 'email', null],
  ] as const)(
    'blocks unavailable sending without making a request',
    async (settings, channel, offerId) => {
      const fetcher = vi.fn();
      vi.stubGlobal('fetch', fetcher);
      const session = createPassengerNotificationSession('G1', channel);
      session.setRecipient(channel === 'email' ? 'person@example.com' : '+40712345678');
      await session.send(offerId, settings);
      expect(fetcher).not.toHaveBeenCalled();
      expect(session.getSnapshot().error).toBeTruthy();
      expect(notificationUnavailableReason(settings, channel, offerId)).toBeTruthy();
    },
  );

  it('stays email-only when SMS is configured and email is unavailable', () => {
    const html = renderToStaticMarkup(
      <PassengerNotifications
        groupId="G1"
        offerId={null}
        config={{ ...config, notification_email_configured: false }}
      />,
    );
    expect(html).toContain('Recipient email');
    expect(html).toContain('Email is not configured in Resend.');
    expect(html).toContain('type="submit" disabled=""');
    expect(html).not.toContain('type="radio"');
    expect(html).not.toContain('type="tel"');
    expect(html).not.toContain('Recipient phone');
    expect(html).not.toContain('SMS');
    expect(
      notificationUnavailableReason(
        {
          notification_unavailable_reason: 'Use a public HTTPS passenger URL.',
        },
        'email',
        'O1',
      ),
    ).toBe('Use a public HTTPS passenger URL.');
  });

  it('submits only the chosen channel, recipient, offer and request identity, preventing a concurrent second click', async () => {
    const gate = deferred<Response>();
    const fetcher = vi.fn().mockReturnValue(gate.promise);
    vi.stubGlobal('fetch', fetcher);
    const session = createPassengerNotificationSession('G1');
    session.setRecipient('  person@example.com  ');
    const pending = session.send('O1', config);
    expect(session.getSnapshot().pending).toBe(true);
    await session.send('O1', config);
    session.setChannel('sms');
    session.setRecipient('+40700000000');
    expect(session.getSnapshot().channel).toBe('email');
    expect(fetcher).toHaveBeenCalledTimes(1);
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body).toEqual({
      channel: 'email',
      recipient: 'person@example.com',
      offer_id: 'O1',
      idempotency_key: expect.any(String),
    });
    gate.resolve(Response.json(delivery()));
    await pending;
    expect(session.getSnapshot().result?.status).toBe('accepted');
    expect(notificationStatusLabel('accepted', session.getSnapshot().result!.provider)).toBe(
      'Accepted by Resend',
    );
    expect(session.getSnapshot().pending).toBe(false);
  });

  it('preserves the exact request identity and payload after a transport failure, only recovering on explicit action', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Connection lost'))
      .mockResolvedValueOnce(Response.json(delivery()));
    vi.stubGlobal('fetch', fetcher);
    const session = createPassengerNotificationSession('G1');
    session.setRecipient('person@example.com');
    await session.send('O1', config);
    expect(session.getSnapshot().uncertain).not.toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    session.setRecipient('different@example.com');
    await session.send('O2', config);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await session.recover();
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
    expect(session.getSnapshot().uncertain).toBeNull();
    expect(session.getSnapshot().result?.recipient_masked).toBe('p***@example.com');
  });

  it('keeps a provider-unknown result blocked until the operator explicitly checks the provider', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(Response.json(delivery({ status: 'unknown', provider_message_id: null })));
    vi.stubGlobal('fetch', fetcher);
    const session = createPassengerNotificationSession('G1');
    session.setRecipient('person@example.com');
    await session.send('O1', config);
    await session.recover();
    await session.send('O1', config);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(
      notificationStatusLabel(
        session.getSnapshot().result!.status,
        session.getSnapshot().result!.provider,
      ),
    ).toBe('Delivery unconfirmed');
    session.prepareAfterProviderCheck();
    await session.send('O1', config);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(JSON.parse(fetcher.mock.calls[1][1].body).idempotency_key).not.toBe(
      JSON.parse(fetcher.mock.calls[0][1].body).idempotency_key,
    );
  });

  it('discards stale history and submission results when the selected group changes', async () => {
    const history = deferred<Response>();
    const sent = deferred<Response>();
    const fetcher = vi
      .fn()
      .mockReturnValueOnce(history.promise)
      .mockReturnValueOnce(sent.promise)
      .mockResolvedValueOnce(Response.json({ notifications: [] }));
    vi.stubGlobal('fetch', fetcher);
    const previous = createPassengerNotificationSession('G1');
    const historyPending = previous.refresh();
    previous.setRecipient('old@example.com');
    const sendPending = previous.send('O1', config);
    const updates = vi.fn();
    previous.subscribe(updates);
    previous.dispose();
    const next = createPassengerNotificationSession('G2');
    await next.refresh();
    history.resolve(Response.json({ notifications: [delivery()] }));
    sent.resolve(Response.json(delivery()));
    await Promise.all([historyPending, sendPending]);
    expect(updates).not.toHaveBeenCalled();
    expect(next.getSnapshot()).toMatchObject({
      recipient: '',
      error: '',
      history: [],
      result: null,
      uncertain: null,
      pending: false,
    });
    expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true);
    expect(fetcher.mock.calls[2][0]).toBe('/api/groups/G2/notifications');
  });

  it('filters history to the current group and recovers an in-progress receipt through a read without resending', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(Response.json(delivery({ status: 'sending' })))
      .mockResolvedValueOnce(
        Response.json({
          notifications: [delivery(), delivery({ id: 'OTHER', group_id: 'G2' })],
        }),
      );
    vi.stubGlobal('fetch', fetcher);
    const session = createPassengerNotificationSession('G1');
    session.setRecipient('person@example.com');
    await session.send('O1', config);
    await session.send('O1', config);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await session.refresh();
    expect(fetcher.mock.calls[1][1].method).toBe('GET');
    expect(session.getSnapshot().history).toHaveLength(1);
    expect(session.getSnapshot().result?.status).toBe('accepted');
  });

  it.each([{}, delivery({ group_id: 'G2' }), { ...delivery(), status: 'delivered' }])(
    'keeps malformed or wrong-group responses uncertain without a new send',
    async (response) => {
      const fetcher = vi.fn().mockResolvedValue(Response.json(response));
      vi.stubGlobal('fetch', fetcher);
      const session = createPassengerNotificationSession('G1');
      session.setRecipient('person@example.com');
      await session.send('O1', config);
      expect(session.getSnapshot().uncertain).not.toBeNull();
      expect(session.getSnapshot().result).toBeNull();
      await session.send('O1', config);
      expect(fetcher).toHaveBeenCalledTimes(1);
    },
  );

  it.each([
    ['email', 'not-an-email'],
    ['sms', '0712345678'],
    ['sms', '+40 712 345 678'],
  ] as const)('validates %s recipient before submitting', async (channel, recipient) => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const session = createPassengerNotificationSession('G1', channel);
    session.setRecipient(recipient);
    await session.send('O1', config);
    expect(fetcher).not.toHaveBeenCalled();
    expect(session.getSnapshot().error).toBeTruthy();
  });
});
