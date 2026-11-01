import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApiError, SearchOutcomeUnknownError, TransportError, request } from './api';
import type { DelegationRequest } from './liveVoice';
import type { MutationResult, RecoveryJob, World } from './types';
import VoiceRequestReviewDialog from './VoiceRequestReviewDialog';
import {
  beginVoiceRequestReview,
  observeVoiceRequestOutcome,
  settleVoiceRequestReview,
  voiceReviewCanCorrect,
  voiceReviewDraftIssue,
  voiceReviewDraftText,
} from './voiceRequestReview';
import type { VoiceRequestReview, VoiceReviewDraftContext } from './voiceRequestReview';
import { resourceArrivalReceipt } from './resourceArrivalReceipt.testSupport';

const delegation: DelegationRequest = {
  text: '  Recalculează pentru Aeroportul Londra Heathrow.\nPăstrează grupul G1 împreună.  ',
  delegationId: 'session:first',
  context: 'Earlier cumulative transcript that must never enter the corrected draft.',
};
const world = { scenario_id: 'operation-1', version: 4, mode: 'live' } as World;
const context: VoiceReviewDraftContext = {
  scenarioId: world.scenario_id,
  draft: '',
  voiceActive: false,
  busy: false,
};
const reply = (overrides: Partial<MutationResult> = {}): MutationResult => ({
  world,
  applied: false,
  explanation: 'Please identify the passenger group.',
  ...overrides,
});
const begin = (sequence = 1) => beginVoiceRequestReview(delegation, world.scenario_id, sequence);
const completed = (result = reply()) => {
  const attempt = begin();
  return settleVoiceRequestReview(attempt, attempt, world.scenario_id, {
    kind: 'response',
    result,
  })!;
};
const failed = (cause: unknown) => {
  const attempt = begin();
  return settleVoiceRequestReview(attempt, attempt, world.scenario_id, { kind: 'failure', cause })!;
};
const unknownError = () => new TransportError(vi.fn());

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('exact voice delegation review', () => {
  it('observes one combined response without resubmitting or reducing it to the primary arrival', async () => {
    const result = resourceArrivalReceipt();
    const original = '  Autobuzul BUS1 este indisponibil.\nG1 este pregătit la OTP la 12:30.  ';
    const attempt = beginVoiceRequestReview({ ...delegation, text: original }, 'OP1', 1);
    let review: VoiceRequestReview | null = attempt;
    const submit = vi.fn().mockResolvedValue(result);
    await observeVoiceRequestOutcome(submit, (outcome) => {
      review = settleVoiceRequestReview(review, attempt, 'OP1', outcome);
    });
    expect(submit).toHaveBeenCalledTimes(1);
    expect(review!.text).toBe(original);
    expect(review!.status).toBe('reports_recorded');
    expect(review!.detail).toContain('serviciul BUS1 raportat indisponibil');
    expect(review!.detail).toContain('grupurile G1 raportate la OTP');
    expect(voiceReviewCanCorrect(review!)).toBe(false);
    expect(voiceReviewDraftText(review, attempt, { ...context, scenarioId: 'OP1' })).toBeNull();
    const html = renderReview(review!, { scenarioId: 'OP1' });
    expect(html).toContain('Operational reports recorded');
    expect(html).not.toContain('Copy to command draft');
    expect(submit).toHaveBeenCalledTimes(1);
  });
  it('keeps a malformed combined receipt unknown and leaves newer/scenario-scoped reviews intact', () => {
    const result = resourceArrivalReceipt();
    const attempt = beginVoiceRequestReview(delegation, 'OP1', 1);
    result.world.events[0].version -= 1;
    const review = settleVoiceRequestReview(attempt, attempt, 'OP1', { kind: 'response', result })!;
    expect(review.status).toBe('unknown');
    expect(voiceReviewCanCorrect(review)).toBe(false);
    const html = renderReview(review, { scenarioId: 'OP1' });
    expect(html).toContain('Outcome unconfirmed');
    expect(html).toContain('Recover request');
    expect(html).not.toContain('Copy to command draft');
    const newer = beginVoiceRequestReview({ ...delegation, delegationId: 'second' }, 'OP1', 2);
    expect(settleVoiceRequestReview(newer, attempt, 'OP1', { kind: 'response', result })).toBe(
      newer,
    );
    expect(
      settleVoiceRequestReview(attempt, attempt, 'NEW', { kind: 'response', result }),
    ).toBeNull();
  });
  it('freezes only the exact delegated text and original scope, without normalization or cumulative context', () => {
    const mutable = { ...delegation };
    const attempt = beginVoiceRequestReview(mutable, world.scenario_id, 7);
    mutable.text = 'A later transcript correction or a different utterance.';
    mutable.context = 'More transcript';
    expect(attempt.text).toBe(delegation.text);
    expect(attempt).toMatchObject({
      sequence: 7,
      scenarioId: 'operation-1',
      delegationId: 'session:first',
      status: 'pending',
    });
    expect(Object.isFrozen(attempt)).toBe(true);
    expect(attempt).not.toHaveProperty('context');
    expect(voiceReviewDraftText(attempt, attempt, context)).toBeNull();
  });

  it('copies an eligible request exactly, without mutating its record or submitting anything', () => {
    const review = completed();
    const original = { ...review };
    expect(review.status).toBe('not_applied');
    expect(review.detail).toContain('Please identify the passenger group.');
    expect(voiceReviewDraftText(review, review, context)).toBe(delegation.text);
    expect(review).toEqual(original);
    expect(Object.isFrozen(review)).toBe(true);
  });

  it.each([400, 409, 422, 503])(
    'allows correction after a definitive rejected ApiError (%s)',
    (status) => {
      // The shared command transport only emits ApiError for a definitive rejection;
      // ambiguous mutation failures are TransportError instead.
      const review = failed(new ApiError(status, 'Request was not applied.'));
      expect(review.status).toBe('rejected');
      expect(voiceReviewCanCorrect(review)).toBe(true);
      expect(voiceReviewDraftText(review, review, context)).toBe(delegation.text);
    },
  );

  it.each([
    [
      'queued recovery',
      reply({
        recovery_job: { id: 'JOB1', status: 'queued' } as RecoveryJob,
        incident_id: 'CASE1',
      }),
      'recovery_recorded',
    ],
    [
      'running recovery',
      reply({
        recovery_job: { id: 'JOB1', status: 'running' } as RecoveryJob,
        incident_id: 'CASE1',
      }),
      'recovery_recorded',
    ],
    [
      'completed recovery replay',
      reply({ recovery_job: { id: 'JOB1', status: 'completed' } as RecoveryJob, replayed: true }),
      'recovery_recorded',
    ],
    ['report', reply({ report: { id: 'REPORT1' } as MutationResult['report'] }), 'report_recorded'],
    ['applied change', reply({ applied: true }), 'applied'],
    ['unspecified applied state', reply({ applied: undefined }), 'response_received'],
  ] as const)(
    'does not enable a new copy for %s even when the receipt otherwise looks unapplied',
    (_, result, status) => {
      const review = completed(result);
      expect(review.status).toBe(status);
      expect(voiceReviewCanCorrect(review)).toBe(false);
      expect(voiceReviewDraftText(review, review, context)).toBeNull();
    },
  );

  it.each([
    ['transport uncertainty', unknownError()],
    ['unknown source outcome', new SearchOutcomeUnknownError('Unknown provider result')],
    ['unexpected error', new Error('Unexpected failure')],
    ['missing error details', null],
  ])('keeps %s ineligible and points to the existing same-request recovery', (_, cause) => {
    const review = failed(cause);
    expect(review.status).toBe('unknown');
    expect(voiceReviewDraftText(review, review, context)).toBeNull();
    expect(voiceReviewDraftIssue(review, context)).toContain(
      'If Recover request is available in the existing notification',
    );
    if (cause instanceof TransportError) expect(cause.retry).not.toHaveBeenCalled();
  });
});

describe('draft and asynchronous scope gates', () => {
  it('observes the existing same-key recovery without submitting a second command identity or changing a typed draft', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('upstream failure', { status: 502 }))
      .mockResolvedValueOnce(Response.json(reply({ replayed: true })));
    vi.stubGlobal('fetch', fetcher);
    const attempt = begin();
    let review: VoiceRequestReview | null = attempt;
    const draft = 'A separate typed request already being edited.';
    const observer = vi.fn((outcome: Parameters<typeof settleVoiceRequestReview>[3]) => {
      review = settleVoiceRequestReview(review, attempt, world.scenario_id, outcome);
    });
    let uncertainty: TransportError | null = null;
    try {
      await observeVoiceRequestOutcome(
        () =>
          request<MutationResult>('/commands', {
            text: attempt.text,
            context: delegation.context,
            expected_version: world.version,
            idempotency_key: `voice-${attempt.delegationId}`,
          }),
        observer,
      );
    } catch (cause) {
      expect(cause).toBeInstanceOf(TransportError);
      uncertainty = cause as TransportError;
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(review!.status).toBe('unknown');
    expect(voiceReviewDraftText(review, attempt, { ...context, draft })).toBeNull();
    expect(observer.mock.calls[0][0].kind).toBe('failure');
    // The workspace's existing Recover request action invokes this same retry.
    await observeVoiceRequestOutcome(() => uncertainty!.retry<MutationResult>(), observer);
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toMatchObject({
      text: delegation.text,
      idempotency_key: 'voice-session:first',
    });
    expect(observer.mock.calls[1][0].kind).toBe('response');
    expect(review!.status).toBe('not_applied');
    expect(voiceReviewDraftText(review, attempt, { ...context, draft })).toBeNull();
    expect(draft).toBe('A separate typed request already being edited.');
    expect(voiceReviewDraftText(review, attempt, context)).toBe(delegation.text);
  });

  it.each([
    [{ draft: 'Existing draft' }, 'already contains text'],
    [{ draft: '  ' }, 'already contains text'],
    [{ voiceActive: true }, 'End the voice session'],
    [{ busy: true }, 'current operation'],
    [{ scenarioId: 'another-operation' }, 'previous operation'],
    [{ scenarioId: undefined }, 'previous operation'],
  ])('preserves the current context before allowing copy: %j', (override, reason) => {
    const review = completed();
    const scoped = { ...context, ...override };
    expect(voiceReviewDraftIssue(review, scoped)).toContain(reason);
    expect(voiceReviewDraftText(review, review, scoped)).toBeNull();
  });

  it('does not copy an older displayed delegation after a newer delegation starts, even if IDs are reused', () => {
    const displayed = completed();
    const latest = begin(2);
    expect(voiceReviewDraftText(latest, displayed, context)).toBeNull();
    expect(voiceReviewDraftText(null, displayed, context)).toBeNull();
    const nowUnknown = settleVoiceRequestReview(displayed, displayed, world.scenario_id, {
      kind: 'failure',
      cause: unknownError(),
    })!;
    expect(voiceReviewDraftText(nowUnknown, displayed, context)).toBeNull();
  });

  it('ignores late results/errors from an older delegation without overwriting the latest review', () => {
    const old = begin(1);
    const latest = beginVoiceRequestReview(
      { ...delegation, text: 'The latest command.', delegationId: 'session:second' },
      world.scenario_id,
      2,
    );
    expect(
      settleVoiceRequestReview(latest, old, world.scenario_id, {
        kind: 'response',
        result: reply(),
      }),
    ).toBe(latest);
    expect(
      settleVoiceRequestReview(latest, old, world.scenario_id, {
        kind: 'failure',
        cause: new ApiError(400, 'Late rejection'),
      }),
    ).toBe(latest);
    expect(latest.text).toBe('The latest command.');
    expect(latest.status).toBe('pending');
  });

  it('discards outcomes outside the original scenario and never resurrects a cleared review', () => {
    const attempt = begin();
    expect(
      settleVoiceRequestReview(attempt, attempt, 'operation-2', {
        kind: 'response',
        result: reply(),
      }),
    ).toBeNull();
    expect(
      settleVoiceRequestReview(attempt, attempt, world.scenario_id, {
        kind: 'response',
        result: reply({ world: { ...world, scenario_id: 'operation-2' } }),
      }),
    ).toBeNull();
    expect(
      settleVoiceRequestReview(null, attempt, world.scenario_id, {
        kind: 'response',
        result: reply(),
      }),
    ).toBeNull();
    const latest = beginVoiceRequestReview(delegation, 'operation-2', 2);
    expect(
      settleVoiceRequestReview(latest, attempt, 'operation-2', {
        kind: 'failure',
        cause: unknownError(),
      }),
    ).toBe(latest);
  });

  it('accepts the existing same-key recovery outcome only for the matching latest attempt', () => {
    const attempt = begin();
    const uncertain = settleVoiceRequestReview(attempt, attempt, world.scenario_id, {
      kind: 'failure',
      cause: unknownError(),
    })!;
    const resolved = settleVoiceRequestReview(uncertain, attempt, world.scenario_id, {
      kind: 'response',
      result: reply({ replayed: true }),
    })!;
    expect(resolved.status).toBe('not_applied');
    expect(voiceReviewDraftText(resolved, resolved, context)).toBe(delegation.text);
    const newer = begin(2);
    expect(
      settleVoiceRequestReview(newer, attempt, world.scenario_id, {
        kind: 'response',
        result: reply({ replayed: true }),
      }),
    ).toBe(newer);
  });
});

function renderReview(review: VoiceRequestReview, override: Partial<VoiceReviewDraftContext> = {}) {
  const copy = vi.fn();
  const close = vi.fn();
  const html = renderToStaticMarkup(
    <VoiceRequestReviewDialog
      review={review}
      context={{ ...context, ...override }}
      onCopyToDraft={copy}
      onClose={close}
    />,
  );
  expect(copy).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
  return html;
}

describe('optional review presentation', () => {
  it('shows exact original text and a recorded non-applied result with a copy-only action', () => {
    const html = renderReview(completed());
    expect(html).toContain('aria-label="Review voice request"');
    expect(html).toContain('Original request');
    expect(html).toContain(delegation.text);
    expect(html).not.toContain(delegation.context);
    expect(html).toContain('No operational change applied');
    expect(html).toContain('Please identify the passenger group.');
    expect(html).toContain('Copy to command draft');
    expect(html).toContain('Copying does not submit anything');
    expect(html).toContain('explicitly send a new request');
    expect(html).not.toMatch(/type="submit"|Resend|Confirm request/);
  });

  it.each([
    [begin(), 'Awaiting backend result'],
    [failed(unknownError()), 'Outcome unconfirmed'],
    [completed(reply({ applied: true })), 'Operational change recorded'],
    [
      completed(reply({ recovery_job: { id: 'J1', status: 'queued' } as RecoveryJob })),
      'Recovery request recorded',
    ],
    [
      completed(reply({ report: { id: 'R1' } as MutationResult['report'] })),
      'Operational report recorded',
    ],
  ])('renders the recorded state without offering fresh resubmission: %s', (review, label) => {
    const html = renderReview(review);
    expect(html).toContain(label);
    expect(html).not.toContain('Copy to command draft');
    expect(html).not.toContain('type="submit"');
  });

  it('explains the stopped-voice and existing-draft gates and retains a way back to the workspace', () => {
    for (const [override, reason] of [
      [{ voiceActive: true }, 'End the voice session'],
      [{ draft: 'Do not overwrite this.' }, 'draft already contains text'],
    ] as const) {
      const html = renderReview(completed(), override);
      expect(html).toContain(reason);
      expect(html).toContain('disabled=""');
      expect(html).toContain('aria-describedby="voice-review-draft-issue"');
      expect(html).toContain('Back to workspace');
    }
  });

  it('keeps untrusted original text and failed-result text as escaped content', () => {
    const attempt = beginVoiceRequestReview(
      { ...delegation, text: '<script>rewrite()</script>' },
      world.scenario_id,
      9,
    );
    const review = settleVoiceRequestReview(attempt, attempt, world.scenario_id, {
      kind: 'failure',
      cause: new ApiError(422, '<img src=x onerror=send()>'),
    })!;
    const html = renderReview(review);
    expect(html).toContain('&lt;script&gt;rewrite()&lt;/script&gt;');
    expect(html).toContain('&lt;img src=x onerror=send()&gt;');
    expect(html).not.toContain('<script>');
    expect(html).toContain('Request rejected');
  });
});
