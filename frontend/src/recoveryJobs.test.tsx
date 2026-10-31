import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { api, ApiError, TransportError } from './api';
import { RecoveryJobDetails } from './RecoveryJobPanel';
import {
  classifyRecoveryJobGaps,
  mergeRecoveryJobs,
  recoveryJobActive,
  recoveryJobAnchor,
  recoveryJobWaiting,
} from './recoveryJobs';
import type { RecoveryJob, RecoveryJobInput, RecoveryJobResponse, World } from './types';

const input: RecoveryJobInput = {
  group_ids: ['G-1'],
  objective: 'balanced',
  modes: ['flight'],
  max_provider_calls: 8,
  instructions: 'Keep this family together.',
};
const job: RecoveryJob = {
  id: 'JOB-1',
  incident_id: 'CASE-1',
  group_ids: ['G-1'],
  objective: 'balanced',
  modes: ['flight'],
  max_provider_calls: 8,
  calls_dispatched: 2,
  status: 'running',
  stage: 'searching_flights',
  message: 'Searching the selected flight alternatives.',
  created_at: '2026-11-01T10:00:00Z',
  updated_at: '2026-11-01T10:01:00Z',
  cancel_requested: false,
  source_version: 4,
  applied_version: null,
  steps: [
    {
      id: 'STEP-1',
      stage: 'searching_flights',
      provider: 'Supplier A',
      status: 'succeeded',
      started_at: '2026-11-01T10:00:10Z',
      finished_at: '2026-11-01T10:01:00Z',
      group_ids: ['G-1'],
      message: 'One quoted flight returned.',
      quote_count: 1,
    },
  ],
  gaps: [
    {
      code: 'ground_link_missing',
      message: 'No verified transfer connects the hotel to the airport.',
      group_ids: ['G-1'],
    },
  ],
  result: null,
};
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('durable recovery request boundaries', () => {
  it('recovers an unknown start using the exact request and does not silently start a new provider attempt', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response('upstream failed', { status: 502 }))
      .mockResolvedValueOnce(Response.json({ job, replayed: true }));
    vi.stubGlobal('fetch', fetcher);
    const mutable = { ...input, group_ids: ['G-1'] };
    let unknown: TransportError | null = null;
    try {
      await api.startRecoveryJob({ version: 4 } as World, 'CASE/1', mutable);
    } catch (cause) {
      expect(cause).toBeInstanceOf(TransportError);
      unknown = cause as TransportError;
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
    mutable.group_ids.push('G-2');
    const replay = await unknown!.retry<RecoveryJobResponse>();
    expect(replay.replayed).toBe(true);
    const [first, second] = fetcher.mock.calls;
    expect(first[0]).toBe('/api/incidents/CASE%2F1/recovery-jobs');
    expect(second[1].body).toBe(first[1].body);
    expect(JSON.parse(second[1].body)).toMatchObject({ group_ids: ['G-1'], expected_version: 4 });
  });
  it('keeps cancellation separate from a new search and reuses its key after an unknown outcome', async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Connection lost'))
      .mockResolvedValueOnce(Response.json({ job: { ...job, cancel_requested: true } }));
    vi.stubGlobal('fetch', fetcher);
    let unknown: TransportError | null = null;
    try {
      await api.cancelRecoveryJob('JOB/1');
    } catch (cause) {
      unknown = cause as TransportError;
    }
    const response = await unknown!.retry<RecoveryJobResponse>();
    expect(response.job.cancel_requested).toBe(true);
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      '/api/recovery-jobs/JOB%2F1/cancel',
      '/api/recovery-jobs/JOB%2F1/cancel',
    ]);
    expect(fetcher.mock.calls[1][1].body).toBe(fetcher.mock.calls[0][1].body);
    expect(Object.keys(JSON.parse(fetcher.mock.calls[0][1].body))).toEqual(['idempotency_key']);
  });
  it('exposes definitive conflicts and gives explicit new attempts new request identities', async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({ detail: 'Operation changed', applied: false }, { status: 409 }),
      )
      .mockResolvedValue(Response.json({ job }));
    vi.stubGlobal('fetch', fetcher);
    await expect(
      api.startRecoveryJob({ version: 4 } as World, 'CASE-1', input),
    ).rejects.toBeInstanceOf(ApiError);
    expect(fetcher).toHaveBeenCalledTimes(1);
    await api.startRecoveryJob({ version: 5 } as World, 'CASE-1', input);
    const bodies = fetcher.mock.calls.map(([, options]) => JSON.parse(options.body));
    expect(bodies[0].idempotency_key).not.toBe(bodies[1].idempotency_key);
    expect(bodies[1].expected_version).toBe(5);
  });
  it('polls the incident endpoint as a read, without a provider dispatch body', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ jobs: [job] }));
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    expect((await api.recoveryJobs('CASE-1', controller.signal)).jobs).toHaveLength(1);
    expect(fetcher.mock.calls[0][1]).toMatchObject({ method: 'GET', body: undefined });
  });
});

describe('recovery progress and provenance', () => {
  it.each([
    ['searching_hotels', 'Searching hotel rates', 'Nuitee / LiteAPI'],
    ['locating_hotel', 'Checking hotel location and published policy', 'Nuitee / LiteAPI'],
    [
      'quoting_initial_airport_to_hotel',
      'Getting airport-to-hotel transfer quotes',
      'Airport Pickups London',
    ],
    [
      'routing_initial_airport_to_hotel',
      'Checking the airport-to-hotel driving route',
      'Google Routes',
    ],
    ['reference_fx', 'Retrieving the ECB reference exchange rate', 'European Central Bank'],
    [
      'quoting_hotel_to_airport',
      'Getting hotel-to-airport transfer quotes',
      'Airport Pickups London',
    ],
    ['routing_hotel_to_airport', 'Checking the hotel-to-airport driving route', 'Google Routes'],
  ] as const)(
    'renders %s in both live progress and the source ledger',
    (stage, label, provider) => {
      const running: RecoveryJob = {
        ...job,
        stage,
        message: 'Source work in progress.',
        steps: [{ ...job.steps[0], stage, provider, message: 'Recorded source response.' }],
      };
      const html = renderToStaticMarkup(
        <RecoveryJobDetails job={running} world={{ offers: [] } as unknown as World} />,
      );
      const ledger = html.split('class="case-job-ledger"')[1];
      expect(html.split(label)).toHaveLength(3);
      expect(ledger).toContain(`<strong>${label}</strong>`);
      expect(ledger).toContain(provider);
      expect(html).not.toContain(stage);
      expect(html).not.toContain('<strong></strong>');
    },
  );

  it('preserves acknowledged jobs across an older list response and never regresses completed work', () => {
    const completed: RecoveryJob = {
      ...job,
      status: 'completed',
      stage: 'finished',
      updated_at: '2026-11-01T10:02:00Z',
    };
    expect(mergeRecoveryJobs([completed], [job])[0]).toEqual(completed);
    expect(mergeRecoveryJobs([completed], [])[0]).toEqual(completed);
    expect(
      mergeRecoveryJobs([completed], [{ ...job, updated_at: completed.updated_at }])[0],
    ).toEqual(completed);
    expect(recoveryJobActive(completed)).toBe(false);
    for (const status of [
      'needs_review',
      'unknown_outcome',
      'blocked',
      'cancelled',
      'failed',
    ] as const)
      expect(recoveryJobActive({ ...job, status })).toBe(false);
  });
  it('shows actual provider counts and gaps without a percentage or capacity claim', () => {
    const html = renderToStaticMarkup(
      <RecoveryJobDetails job={job} world={{ offers: [] } as unknown as World} />,
    );
    expect(html).toContain('2 provider calls dispatched');
    expect(html).toContain('limit 8');
    expect(html).toContain('Supplier A');
    expect(html).toContain('No verified transfer connects the hotel to the airport');
    expect(html).not.toContain('25%');
    expect(html).not.toContain('seats available');
  });
  it('separates historical proposal results from expired offers and actual supplier confirmation', () => {
    const completed: RecoveryJob = {
      ...job,
      status: 'completed',
      stage: 'finished',
      applied_version: 5,
      result: {
        decision_id: 'D-1',
        plan_id: 'P-1',
        recovered_passengers: 3,
        exception_passengers: 0,
        offer_ids: ['O-1'],
        resource_ids: ['F-1'],
        source_counts: { 'Supplier A': 1 },
      },
    };
    const expiredWorld = {
      offers: [{ id: 'O-1', status: 'proposed', expires_at: '2020-01-01T00:00:00Z' }],
    } as unknown as World;
    const html = renderToStaticMarkup(<RecoveryJobDetails job={completed} world={expiredWorld} />);
    expect(html).toContain('passengers proposed');
    expect(html).toContain('0 of 1');
    expect(html).toContain('Proposals are not supplier bookings');
    expect(html).not.toContain('3 passengers confirmed');
  });
  it('does not describe requested cancellation or an unknown provider outcome as safely finished', () => {
    const html = renderToStaticMarkup(
      <RecoveryJobDetails
        job={{ ...job, status: 'unknown_outcome', cancel_requested: true }}
        world={{ offers: [] } as unknown as World}
      />,
    );
    expect(html).toContain('in-flight provider call may still finish and incur a charge');
    expect(html).toContain('A new attempt can incur additional provider charges');
    expect(html).toContain('UNKNOWN OUTCOME');
  });
});

describe('operational update recovery dependencies', () => {
  const previous: RecoveryJob = {
    ...job,
    cancel_requested: true,
    superseded_by_job_id: 'JOB-2',
  };
  const waiting: RecoveryJob = {
    ...job,
    id: 'JOB-2',
    status: 'queued',
    stage: 'waiting_for_previous',
    calls_dispatched: 0,
    supersedes_job_ids: ['JOB-1'],
    steps: [],
    gaps: [],
    message: 'Waiting for the earlier attempt to finish.',
    created_at: '2026-11-01T10:02:00Z',
    updated_at: '2026-11-01T10:02:00Z',
  };
  const world = { offers: [] } as unknown as World;

  it('keeps waiting work active with zero calls and navigable recorded dependencies', () => {
    const fetcher = vi.fn();
    vi.stubGlobal('fetch', fetcher);
    const html = renderToStaticMarkup(
      <RecoveryJobDetails job={waiting} world={world} relatedJobs={[waiting, previous]} />,
    );
    expect(recoveryJobActive(waiting)).toBe(true);
    expect(recoveryJobWaiting(waiting)).toBe(true);
    expect(html).toContain('Waiting for earlier recovery attempts');
    expect(html).toContain('Waiting before the new search');
    expect(html).toContain('0 provider calls dispatched');
    expect(html).toContain('unknown outcome requires review');
    expect(html).toContain('href="#recovery-job-JOB-1"');
    expect(html).toContain('JOB-1 · running');
    expect(html).not.toMatch(/Searching flight suppliers|passengers proposed|handoff complete/);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('shows the replacement link without calling a requested cancellation complete', () => {
    const html = renderToStaticMarkup(
      <RecoveryJobDetails job={previous} world={world} relatedJobs={[previous, waiting]} />,
    );
    expect(html).toContain('Superseded by request');
    expect(html).toContain('href="#recovery-job-JOB-2"');
    expect(html).toContain('JOB-2 · queued');
    expect(html).toContain('does not confirm that earlier work has stopped');
    expect(html).toContain('Cancellation requested');
    expect(html).toContain('no cancellation is complete until the recorded status confirms it');
    expect(html).toContain('2 provider calls dispatched');
    expect(html).not.toContain('CANCELLED');
  });

  it('does not invent navigable history or dependency outcomes when a linked attempt is not loaded', () => {
    const html = renderToStaticMarkup(<RecoveryJobDetails job={waiting} world={world} />);
    expect(html).toContain('JOB-1 · not in loaded case history');
    expect(html).not.toContain('href="#recovery-job-JOB-1"');
    expect(html).not.toContain('JOB-1 · cancelled');
    const withoutRelationships = renderToStaticMarkup(
      <RecoveryJobDetails job={job} world={world} />,
    );
    expect(withoutRelationships).not.toContain('Recovery attempt dependencies');
    expect(withoutRelationships).not.toContain('Superseded by request');
  });

  it('keeps the dependency record after review is required without claiming a search or recovery', () => {
    const needsReview: RecoveryJob = {
      ...waiting,
      status: 'needs_review',
      stage: 'finished',
      message: 'An earlier provider outcome is unknown. Review the call ledger.',
    };
    const html = renderToStaticMarkup(
      <RecoveryJobDetails job={needsReview} world={world} relatedJobs={[previous, needsReview]} />,
    );
    expect(recoveryJobActive(needsReview)).toBe(false);
    expect(recoveryJobWaiting(needsReview)).toBe(false);
    expect(html).toContain('NEEDS REVIEW');
    expect(html).toContain('No new provider calls were dispatched by this attempt');
    expect(html).toContain('href="#recovery-job-JOB-1"');
    expect(html).not.toMatch(/Waiting before the new search|passengers proposed/);
  });

  it('preserves replacement/cancellation facts against older receipts and equal-time polls', () => {
    expect(mergeRecoveryJobs([previous, waiting], [job])).toEqual([waiting, previous]);
    expect(mergeRecoveryJobs([waiting], [{ ...waiting, supersedes_job_ids: undefined }])).toEqual([
      waiting,
    ]);
    const running: RecoveryJob = {
      ...waiting,
      status: 'running',
      stage: 'searching_flights',
      calls_dispatched: 1,
    };
    expect(mergeRecoveryJobs([waiting], [running])).toEqual([running]);
    expect(mergeRecoveryJobs([running], [waiting])).toEqual([running]);
    const cancelled: RecoveryJob = { ...previous, status: 'cancelled', stage: 'finished' };
    expect(mergeRecoveryJobs([cancelled], [previous])).toEqual([cancelled]);
  });

  it('identifies inherited blockers separately from the directly superseded attempts', () => {
    const ancestor: RecoveryJob = {
      ...job,
      id: 'JOB-ANCESTOR',
      status: 'unknown_outcome',
      stage: 'finished',
    };
    const review: RecoveryJob = {
      ...waiting,
      status: 'needs_review',
      stage: 'finished',
      blocking_job_ids: [ancestor.id, 'JOB-MISSING'],
    };
    const html = renderToStaticMarkup(
      <RecoveryJobDetails job={review} world={world} relatedJobs={[previous, ancestor, review]} />,
    );
    const blockers = html.split('aria-label="Attempts requiring review"')[1].split('</section>')[0];
    const direct = html
      .split('aria-label="Recovery attempt dependencies"')[1]
      .split('</section>')[0];
    expect(blockers).toContain('href="#recovery-job-JOB-ANCESTOR"');
    expect(blockers).toContain('JOB-ANCESTOR · unknown outcome');
    expect(blockers).toContain('JOB-MISSING · not in loaded case history');
    expect(blockers).not.toContain('href="#recovery-job-JOB-MISSING"');
    expect(blockers).not.toContain('href="#recovery-job-JOB-1"');
    expect(direct).toContain('href="#recovery-job-JOB-1"');
    expect(direct).not.toContain('JOB-ANCESTOR');
    expect(html).toContain('0 provider calls dispatched');
    expect(html).not.toContain('passengers proposed');
  });

  it('preserves inherited blockers on equal-time stale receipts and shows them without direct predecessors', () => {
    const review: RecoveryJob = {
      ...waiting,
      status: 'needs_review',
      stage: 'finished',
      supersedes_job_ids: undefined,
      blocking_job_ids: ['JOB-ANCESTOR', 'JOB-MISSING'],
    };
    for (const blocking_job_ids of [undefined, [], ['JOB-ANCESTOR']]) {
      expect(mergeRecoveryJobs([review], [{ ...review, blocking_job_ids }])).toEqual([review]);
    }
    const html = renderToStaticMarkup(<RecoveryJobDetails job={review} world={world} />);
    expect(html).toContain('aria-label="Attempts requiring review"');
    expect(html).toContain('JOB-ANCESTOR · not in loaded case history');
    expect(html).not.toContain('aria-label="Recovery attempt dependencies"');
  });

  it('uses literal safe fragment targets for recorded IDs', () => {
    const odd = { ...previous, id: 'JOB/1?x=#quoted' };
    const html = renderToStaticMarkup(
      <RecoveryJobDetails
        job={{ ...waiting, supersedes_job_ids: [odd.id] }}
        world={world}
        relatedJobs={[odd]}
      />,
    );
    expect(recoveryJobAnchor(odd.id)).toBe('recovery-job-JOB%2F1%3Fx%3D%23quoted');
    expect(html).toContain('href="#recovery-job-JOB%252F1%253Fx%253D%2523quoted"');
    expect(renderToStaticMarkup(<RecoveryJobDetails job={odd} world={world} />)).toContain(
      'id="recovery-job-JOB%2F1%3Fx%3D%23quoted"',
    );
  });
});

type RecordedWait = NonNullable<NonNullable<RecoveryJob['result']>['waits']>[number];
const waitWorld = {
  offers: [],
  groups: [{ id: 'G-1', name: 'Planning family' }],
  airports: [{ id: 'JFK', name: 'John F. Kennedy International Airport' }],
} as unknown as World;
function completedWithWait(overrides: Partial<RecordedWait> = {}): RecoveryJob {
  return {
    ...job,
    status: 'completed',
    stage: 'finished',
    gaps: [],
    applied_version: 5,
    result: {
      decision_id: 'D-1',
      plan_id: 'P-1',
      recovered_passengers: 2,
      exception_passengers: 0,
      offer_ids: [],
      resource_ids: ['F-1'],
      source_counts: { 'Supplier A': 1 },
      waits: [
        {
          group_id: 'G-1',
          location_id: 'JFK',
          from_at: '2027-01-02T03:30:00Z',
          to_at: '2027-01-02T05:00:00Z',
          duration_minutes: 90,
          timezone: 'America/New_York',
          overnight: true,
          accommodation_included: false,
          ...overrides,
        },
      ],
    },
  };
}

describe('search exclusions versus unresolved passenger requirements', () => {
  const exclusion = {
    code: 'quote_not_usable',
    message: 'Sourced offer does not meet this incident’s route or timing constraints.',
    group_ids: ['G-1'],
  };
  const emptySearch = {
    code: 'no_flight_offers',
    message: 'No offers returned for one selected dated search.',
    group_ids: ['G-1'],
  };

  it('keeps a successful two-passenger proposal successful when other supplier alternatives were rejected', () => {
    const completed = completedWithWait();
    completed.result!.waits = [];
    completed.gaps = [exclusion, emptySearch];
    const html = renderToStaticMarkup(<RecoveryJobDetails job={completed} world={waitWorld} />);
    expect(html).toContain('<strong>2</strong> passengers proposed');
    expect(html).toContain('<strong>0</strong> with unresolved requirements');
    expect(html).toContain('Source exclusions and search outcomes');
    expect(html).toContain(exclusion.message);
    expect(html).toContain(emptySearch.message);
    expect(html).toContain('Groups: G-1');
    expect(html).not.toContain('Requirements still unresolved');
    expect(html).not.toContain('aria-label="Recovery gaps"');
  });

  it('preserves real missing requirements separately when the attempt has no complete journey', () => {
    const missing = {
      code: 'no_complete_journey',
      message: 'No complete sourced itinerary passed the required checks.',
      group_ids: ['G-1'],
    };
    const blocked: RecoveryJob = {
      ...job,
      status: 'blocked',
      stage: 'finished',
      gaps: [exclusion, missing],
    };
    const html = renderToStaticMarkup(<RecoveryJobDetails job={blocked} world={waitWorld} />);
    const requirements = html.split('aria-label="Recovery gaps"')[1].split('</section>')[0];
    expect(requirements).toContain('Requirements still unresolved');
    expect(requirements).toContain(missing.message);
    expect(requirements).not.toContain(exclusion.message);
    const sources = html
      .split('aria-label="Source exclusions and search outcomes"')[1]
      .split('</section>')[0];
    expect(sources).toContain(exclusion.message);
    expect(sources).not.toContain(missing.message);
  });

  it('retains every recorded reason and does not downgrade new or explicit requirements', () => {
    const wait = {
      code: 'overnight_wait_without_accommodation',
      message: 'Review the overnight wait.',
      group_ids: ['G-1'],
    };
    const unknown = {
      code: 'future_required_constraint',
      message: 'A newly supported mandatory condition is not satisfied.',
      group_ids: ['G-2'],
    };
    expect(classifyRecoveryJobGaps([exclusion, wait, unknown, emptySearch])).toEqual({
      requirements: [unknown],
      sourceExclusions: [exclusion, emptySearch],
      waitingNotes: [wait],
    });
  });
});

describe('recorded waits and local time', () => {
  it('shows elapsed duration and source-location local times without making an overnight wait a failure', () => {
    const completed = completedWithWait();
    completed.gaps = [
      {
        code: 'overnight_wait_without_accommodation',
        message: 'Review the recorded overnight waiting interval.',
        group_ids: ['G-1'],
      },
    ];
    const html = renderToStaticMarkup(<RecoveryJobDetails job={completed} world={waitWorld} />);
    expect(html).toContain('90 min (1 h 30 min)');
    expect(html).toContain('John F. Kennedy International Airport (JFK)');
    expect(html).toContain('Planning family');
    expect(html).toContain('01 Jan 2027, 22:30 GMT-5');
    expect(html).toContain('02 Jan 2027, 00:00 GMT-5');
    expect(html).toContain('America/New_York');
    expect(html).toContain('Wait crosses local midnight');
    expect(html).toContain('Accommodation is not included during this wait');
    expect(html).toContain('no mandatory rest requirement was assumed');
    expect(html).toContain('Informational waiting considerations');
    expect(html).not.toContain('Requirements still unresolved');
    expect(html).toContain('COMPLETED');
  });

  it('retains actual elapsed duration across the London clock change rather than subtracting local clock labels', () => {
    const completed = completedWithWait({
      location_id: 'LHR',
      timezone: 'Europe/London',
      from_at: '2027-10-31T00:30:00Z',
      to_at: '2027-10-31T03:30:00Z',
      duration_minutes: 180,
      overnight: false,
    });
    const html = renderToStaticMarkup(<RecoveryJobDetails job={completed} world={waitWorld} />);
    expect(html).toContain('180 min (3 h)');
    expect(html).toContain('01:30 GMT+1');
    expect(html).toContain('03:30 GMT');
    expect(html).toContain('Europe/London');
    expect(html).not.toContain('Wait crosses local midnight');
  });

  it('labels unknown or unsupported local zones and explicitly uses UTC, preserving fractional minutes', () => {
    for (const timezone of [null, 'Unsupported/Timezone']) {
      const completed = completedWithWait({
        timezone,
        overnight: null,
        from_at: '2027-01-02T03:30:00Z',
        to_at: '2027-01-02T03:35:30Z',
        duration_minutes: 5.5,
      });
      const html = renderToStaticMarkup(<RecoveryJobDetails job={completed} world={waitWorld} />);
      expect(html).toContain('5.5 min');
      expect(html).toContain('03:30 GMT');
      expect(html).toContain('UTC · local timezone unavailable');
      expect(html).toContain('Local overnight status unknown');
    }
  });

  it('keeps a separate explicit operator requirement unresolved while presenting the overnight disclosure as informational', () => {
    const completed = completedWithWait();
    completed.gaps = [
      {
        code: 'overnight_wait_without_accommodation',
        message: 'Informational overnight wait.',
        group_ids: ['G-1'],
      },
      {
        code: 'rest_requirement_unmet',
        message: 'The operator-required accessible rest facility is missing.',
        group_ids: ['G-1'],
      },
    ];
    const html = renderToStaticMarkup(<RecoveryJobDetails job={completed} world={waitWorld} />);
    expect(html).toContain('Waiting considerations');
    const requirements = html.split('aria-label="Recovery gaps"')[1].split('</section>')[0];
    expect(requirements).toContain('Requirements still unresolved');
    expect(requirements).toContain('operator-required accessible rest facility');
    expect(requirements).not.toContain('Informational overnight wait');
  });
});
