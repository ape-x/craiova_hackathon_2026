import { useEffect, useId, useState, useSyncExternalStore } from 'react';
import { Mail, RefreshCw, Send } from 'lucide-react';
import { key, request, TransportError } from './api';
import type { AppConfig } from './types';
import './PassengerNotifications.css';

export type NotificationChannel = 'email' | 'sms';
export type NotificationDelivery = {
  id: string;
  group_id: string;
  offer_id: string;
  channel: NotificationChannel;
  recipient_masked: string;
  status: 'sending' | 'accepted' | 'failed' | 'unknown';
  provider: 'resend' | 'brevo';
  provider_message_id: string | null;
  created_at: string;
  updated_at: string;
  error_code: string | null;
  error_message: string | null;
};
type NotificationState = {
  channel: NotificationChannel;
  recipient: string;
  pending: boolean;
  historyPending: boolean;
  history: NotificationDelivery[];
  error: string;
  historyError: string;
  result: NotificationDelivery | null;
  uncertain: TransportError | null;
};

export function notificationUnavailableReason(
  config: AppConfig,
  channel: NotificationChannel,
  offerId: string | null,
): string | null {
  const provider = notificationProviderLabel(
    channel === 'sms' || config.notification_email_provider === 'brevo' ? 'brevo' : 'resend',
  );
  if (config.notification_available !== true) {
    return typeof config.notification_unavailable_reason === 'string' &&
      config.notification_unavailable_reason
      ? config.notification_unavailable_reason
      : `Notifications are unavailable. Configure ${provider} ${channel === 'email' ? 'email' : 'SMS'} and a public passenger URL first.`;
  }
  if (
    config[
      channel === 'email' ? 'notification_email_configured' : 'notification_sms_configured'
    ] !== true
  )
    return `${channel === 'email' ? 'Email' : 'SMS'} is not configured in ${provider}.`;
  if (!offerId) return 'An active passenger offer is required before sending its link.';
  return null;
}

export function notificationProviderLabel(provider: NotificationDelivery['provider']) {
  return provider === 'resend' ? 'Resend' : 'Brevo';
}

export function notificationUnknownInstruction(provider: NotificationDelivery['provider']) {
  return `Check ${notificationProviderLabel(provider)} before trying again.`;
}

export function notificationStatusLabel(
  status: NotificationDelivery['status'],
  provider: NotificationDelivery['provider'],
) {
  const name = notificationProviderLabel(provider);
  return {
    sending: 'Submission in progress',
    accepted: `Accepted by ${name}`,
    failed: `Not accepted by ${name}`,
    unknown: 'Delivery unconfirmed',
  }[status];
}

function recipientIssue(channel: NotificationChannel, recipient: string) {
  if (channel === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(recipient))
    return 'Enter a valid email address.';
  if (channel === 'sms' && !/^\+[1-9]\d{5,14}$/.test(recipient))
    return 'Use an international phone number, starting with + and the country code.';
  return null;
}

/** One selected group owns its form, request identity and asynchronous results. */
export function createPassengerNotificationSession(
  groupId: string,
  initialChannel: NotificationChannel = 'email',
) {
  let state: NotificationState = {
    channel: initialChannel,
    recipient: '',
    pending: false,
    historyPending: false,
    history: [],
    error: '',
    historyError: '',
    result: null,
    uncertain: null,
  };
  const listeners = new Set<() => void>();
  const endpoint = `/groups/${encodeURIComponent(groupId)}/notifications`;
  let active = true;
  let generation = 0;
  let historyGeneration = 0;
  let historyAbort: AbortController | null = null;
  const locked = () =>
    state.pending ||
    Boolean(state.uncertain) ||
    ['unknown', 'sending'].includes(state.result?.status ?? '');
  const publish = (patch: Partial<NotificationState>) => {
    if (!active) return;
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  };
  async function refresh() {
    if (!active || state.pending) return;
    historyAbort?.abort();
    const controller = new AbortController();
    historyAbort = controller;
    const scope = generation;
    const sequence = ++historyGeneration;
    publish({ historyPending: true, historyError: '' });
    try {
      const result = await request<{ notifications: NotificationDelivery[] }>(
        endpoint,
        undefined,
        controller.signal,
      );
      if (!active || scope !== generation || sequence !== historyGeneration) return;
      const history = result.notifications.filter((item) => item.group_id === groupId);
      const current = state.result && history.find((item) => item.id === state.result!.id);
      publish({ history, ...(current ? { result: current } : {}) });
    } catch (cause) {
      if (
        !controller.signal.aborted &&
        active &&
        scope === generation &&
        sequence === historyGeneration
      )
        publish({
          historyError:
            cause instanceof Error ? cause.message : 'Notification history is unavailable.',
        });
    } finally {
      if (active && scope === generation && sequence === historyGeneration)
        publish({ historyPending: false });
    }
  }
  async function run(action: () => Promise<NotificationDelivery>) {
    if (!active || state.pending) return;
    const scope = generation;
    historyAbort?.abort();
    historyGeneration++;
    publish({ pending: true, historyPending: false, error: '', result: null });
    try {
      const result = await action();
      if (!active || scope !== generation) return;
      if (
        !result ||
        typeof result !== 'object' ||
        result.group_id !== groupId ||
        typeof result.id !== 'string' ||
        typeof result.recipient_masked !== 'string' ||
        !['resend', 'brevo'].includes(result.provider) ||
        !['sending', 'accepted', 'failed', 'unknown'].includes(result.status)
      )
        throw new TransportError(<T,>() => action() as Promise<T>);
      publish({
        result,
        uncertain: null,
        history: [result, ...state.history.filter((item) => item.id !== result.id)],
      });
    } catch (cause) {
      if (!active || scope !== generation) return;
      publish({
        uncertain: cause instanceof TransportError ? cause : null,
        error:
          cause instanceof TransportError
            ? 'The outcome is unconfirmed. Recover the original request to reuse its request ID.'
            : cause instanceof Error
              ? cause.message
              : 'The notification could not be submitted.',
      });
    } finally {
      if (active && scope === generation) publish({ pending: false });
    }
  }
  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    activate() {
      active = true;
    },
    dispose() {
      active = false;
      generation++;
      historyAbort?.abort();
      listeners.clear();
    },
    refresh,
    setChannel(channel: NotificationChannel) {
      if (!locked()) publish({ channel, recipient: '', error: '', result: null });
    },
    setRecipient(recipient: string) {
      if (!locked()) publish({ recipient, error: '', result: null });
    },
    async send(offerId: string | null, config: AppConfig) {
      if (!active || locked()) return;
      const recipient = state.recipient.trim();
      const issue =
        notificationUnavailableReason(config, state.channel, offerId) ||
        recipientIssue(state.channel, recipient);
      if (issue) {
        publish({ error: issue });
        return;
      }
      const body = {
        channel: state.channel,
        recipient,
        offer_id: offerId!,
        idempotency_key: key(),
      };
      await run(() => request<NotificationDelivery>(endpoint, body));
    },
    async recover() {
      const uncertain = state.uncertain;
      if (!uncertain || state.pending || !active) return;
      await run(() => uncertain.retry<NotificationDelivery>());
    },
    prepareAfterProviderCheck() {
      if (state.pending || state.uncertain || state.result?.status !== 'unknown') return;
      publish({ result: null, error: '' });
    },
  };
}

export type PassengerNotificationsProps = {
  groupId: string;
  offerId: string | null;
  config: AppConfig;
};

export function PassengerNotifications(props: PassengerNotificationsProps) {
  return <NotificationForm key={props.groupId} {...props} />;
}

function NotificationForm({ groupId, offerId, config }: PassengerNotificationsProps) {
  const [session] = useState(() => createPassengerNotificationSession(groupId, 'email'));
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
  const id = useId();
  useEffect(() => {
    session.activate();
    void session.refresh();
    return () => session.dispose();
  }, [session]);
  const unavailable = notificationUnavailableReason(config, 'email', offerId);
  const locked =
    state.pending ||
    Boolean(state.uncertain) ||
    ['unknown', 'sending'].includes(state.result?.status ?? '');
  return (
    <details className="passenger-notifications">
      <summary>
        <Mail size={15} aria-hidden="true" /> Send offer by email
      </summary>
      <div className="passenger-notifications-body">
        <p>Email the current itinerary link to this passenger group.</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void session.send(offerId, config);
          }}
        >
          <fieldset disabled={locked}>
            <label className="passenger-notifications-recipient" htmlFor={`${id}-recipient`}>
              Recipient email
            </label>
            <input
              id={`${id}-recipient`}
              type="email"
              inputMode="email"
              autoComplete="off"
              spellCheck={false}
              required
              maxLength={254}
              value={state.recipient}
              placeholder="passenger@example.com"
              aria-describedby={`${id}-help`}
              onChange={(event) => session.setRecipient(event.target.value)}
            />
            <p id={`${id}-help`} className="passenger-notifications-help">
              {unavailable || 'The recipient receives a private link to this group’s offer.'}
            </p>
          </fieldset>
          <button
            className="primary-button"
            type="submit"
            disabled={locked || Boolean(unavailable) || !state.recipient.trim()}
          >
            <Send size={14} aria-hidden="true" />
            {state.pending ? 'Submitting…' : 'Send offer link'}
          </button>
        </form>
        {state.error && (
          <p className="passenger-notifications-error" role="alert">
            {state.error}
          </p>
        )}
        {state.uncertain && (
          <button
            className="secondary-button"
            type="button"
            disabled={state.pending || config.notification_available !== true}
            onClick={() => void session.recover()}
          >
            <RefreshCw size={14} aria-hidden="true" /> Recover original request
          </button>
        )}
        {state.result && (
          <div
            className={`passenger-notifications-receipt status-${state.result.status}`}
            role="status"
          >
            <strong>{notificationStatusLabel(state.result.status, state.result.provider)}</strong>
            <span>{state.result.recipient_masked}</span>
            {state.result.status === 'accepted' && (
              <p>Provider acceptance does not confirm delivery.</p>
            )}
            {state.result.status === 'sending' && (
              <p>
                Refresh history to check the original submission. Do not send another message yet.
              </p>
            )}
            {state.result.status === 'failed' && (
              <p>{state.result.error_message || 'The provider did not accept this message.'}</p>
            )}
            {state.result.status === 'unknown' && (
              <>
                <p>
                  Delivery is unconfirmed. {notificationUnknownInstruction(state.result.provider)}
                </p>
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => session.prepareAfterProviderCheck()}
                >
                  I checked {notificationProviderLabel(state.result.provider)}; prepare another
                  message
                </button>
              </>
            )}
          </div>
        )}
        <div className="passenger-notifications-history-heading">
          <h4>Notification history</h4>
          <button
            type="button"
            className="secondary-button"
            disabled={state.historyPending || state.pending}
            onClick={() => void session.refresh()}
            aria-label="Refresh notification history"
          >
            <RefreshCw size={13} aria-hidden="true" />
            {state.historyPending ? 'Loading…' : 'Refresh'}
          </button>
        </div>
        {state.historyError && (
          <p className="passenger-notifications-error" role="alert">
            {state.historyError}
          </p>
        )}
        {!state.history.length && !state.historyPending && !state.historyError && (
          <p className="passenger-notifications-help">No notifications recorded for this group.</p>
        )}
        <ul className="passenger-notifications-history">
          {state.history.map((item) => (
            <li key={item.id}>
              <div>
                <strong>{item.channel === 'email' ? 'Email' : 'SMS'}</strong>
                <span>{notificationStatusLabel(item.status, item.provider)}</span>
              </div>
              <span>{item.recipient_masked}</span>
              {item.status === 'unknown' && (
                <span>{notificationUnknownInstruction(item.provider)}</span>
              )}
              <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

export default PassengerNotifications;
