import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { request, TransportError } from './api';
import { LiveVoiceClient, voiceCommandAcknowledgement } from './liveVoice';
import type { LiveVoiceOptions, VoiceState } from './liveVoice';
import type { MutationResult, RecoveryJob, World } from './types';

function reportReceipt(overrides: Partial<MutationResult> = {}): MutationResult {
  return {
    world: { version: 7, mode: 'live' } as World,
    applied: true,
    incident_id: 'CASE1',
    command_interpretation_calls: 1,
    report: {
      id: 'opr_arrival',
      source: 'operator_report',
      type: 'arrival_report',
      received_at: '2026-11-01T09:00:00Z',
      description: 'Operator-reported passenger position.',
      incident_id: 'CASE1',
      group_ids: ['G1', 'G2'],
      airport_code: 'OTP',
      ready_at: '2026-11-01T12:30:00+02:00',
      affected_group_ids: ['G1'],
      affected_offer_ids: ['O1'],
    },
    ...overrides,
  };
}
function queuedJob(overrides: Partial<RecoveryJob> = {}): RecoveryJob {
  return {
    id: 'JOB1',
    incident_id: 'CASE1',
    group_ids: ['G1', 'G2'],
    status: 'queued',
    max_provider_calls: 10,
    ...overrides,
  } as RecoveryJob;
}

describe('bounded voice acknowledgements from authoritative receipts', () => {
  it('separates the recorded arrival/readiness from clearance, journey completion and a search', () => {
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        explanation: 'Long explanation '.repeat(200),
        recovery_total_call_limit: 27,
      }),
    );
    expect(content).toContain('Raport înregistrat: 2 grupuri la OTP');
    expect(content).toContain('2026-11-01 10:30 UTC');
    expect(content).toContain('Nu am pornit o căutare.');
    expect(content).toContain(
      'nu confirmare de aterizare, autorizare aeroportuară, itinerar sau rezervare',
    );
    expect(content).not.toContain('27');
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
  });

  it.each([
    ['insufficient_call_allowance', 'limita de apeluri este insuficientă'],
    ['hotel_setup_required', 'configurația hotelului trebuie completată sau revizuită'],
    ['ai_not_configured', 'serviciul AI necesar nu este disponibil'],
    ['recovery_service_unavailable', 'serviciul de recuperare nu este disponibil'],
    ['recovery_prerequisite_failed', 'o condiție necesară nu este îndeplinită'],
    ['recovery_queue_unavailable', 'coada de recuperare nu este disponibilă'],
  ] as const)('keeps a persisted report distinct from blocked search: %s', (code, reason) => {
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        recovery_blocked: { code, explanation: 'Detailed prerequisite explanation '.repeat(200) },
        recovery_total_call_limit: 8,
      }),
    );
    expect(content).toContain('Raport înregistrat');
    expect(content).toContain(`Căutarea nu a pornit: ${reason}`);
    expect(content).toContain('Limită totală: 8 apeluri');
    expect(content).not.toContain('în coadă');
    expect(content).toContain('nu confirmare de aterizare');
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
  });

  it('announces a queued job and only its returned total allowance, without claiming a recovered itinerary', () => {
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        recovery_job: queuedJob(),
        recovery_total_call_limit: 11,
      }),
    );
    expect(content).toContain('Raport înregistrat');
    expect(content).toContain('Cererea de căutare a fost înregistrată pentru 2 grupuri');
    expect(content).toContain('Limită totală: 11 apeluri, inclusiv interpretarea.');
    expect(content).toContain(
      'nu confirmare de aterizare, autorizare aeroportuară, itinerar sau rezervare',
    );
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
  });

  it('keeps the exact affected service ahead of optional long text and never calls an outage a supplier cancellation', () => {
    const serviceId = 'APL-' + 'a'.repeat(100);
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        report: {
          id: 'opr_outage',
          source: 'operator_report',
          type: 'resource_unavailability',
          resource_id: serviceId,
          previous_available: true,
          received_at: '2026-11-01T09:00:00Z',
          description: 'Reported breakdown',
          affected_group_ids: ['G1'],
          affected_offer_ids: ['O1'],
        },
        recovery_job: queuedJob({ group_ids: ['G1'] }),
        recovery_total_call_limit: 8,
        explanation: 'An unbounded explanation '.repeat(200),
      }),
    );
    expect(content).toContain(`serviciul ${serviceId} indisponibil pentru planificare`);
    expect(content).toContain('Cererea de căutare a fost înregistrată pentru 1 grup');
    expect(content).toContain('Nu este anulare la furnizor, itinerar confirmat sau rezervare.');
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
  });

  it('does not call an old completed job newly queued or a proposal still valid', () => {
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        applied: false,
        report: undefined,
        recovery_job: queuedJob({ status: 'completed' }),
      }),
    );
    expect(content).toContain('Starea consemnată a căutării: completed.');
    expect(content).toContain('Verifică propunerea actuală');
    expect(content).not.toMatch(/Raport înregistrat|în coadă/);
    expect(content).toContain('Nu confirmă un itinerar sau o rezervare.');
  });

  it('describes a historical queued replay as the same recorded request, never a new or current job', () => {
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        replayed: true,
        recovery_job: queuedJob(),
        recovery_total_call_limit: 11,
        explanation: 'This historical job may now have finished.',
      }),
    );
    expect(content).toContain('Raport deja înregistrat');
    expect(content).toContain('Cererea de căutare a fost deja înregistrată');
    expect(content).toContain('Vezi progresul curent în panoul cazului');
    expect(content).not.toMatch(/în coadă|în curs|am pornit|finalizată/);
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
  });

  it.each([false, true])(
    'describes a waiting receipt historically without announcing a finished handoff (replay=%s)',
    (replayed) => {
      const content = voiceCommandAcknowledgement(
        reportReceipt({
          replayed,
          recovery_job: queuedJob({
            stage: 'waiting_for_previous',
            calls_dispatched: 0,
            supersedes_job_ids: ['PREVIOUS-1', 'PREVIOUS-2'],
          }),
          recovery_total_call_limit: 11,
        }),
      );
      expect(content).toContain(replayed ? 'Raport deja înregistrat' : 'Raport înregistrat');
      expect(content).toContain(
        'La înregistrare, cererea aștepta încheierea încercărilor anterioare; 0 apeluri proprii.',
      );
      expect(content).toContain('Limită totală: 11 apeluri');
      expect(content).toContain('Vezi progresul curent în panoul cazului');
      expect(content).toContain('nu confirmare de aterizare');
      expect(content).not.toMatch(/am oprit|am anulat|finalizată|căutarea a pornit|caut acum/);
      expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
    },
  );

  it('does not call the old superseded request a new search or a completed cancellation', () => {
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        replayed: true,
        recovery_job: queuedJob({
          status: 'running',
          stage: 'searching_flights',
          calls_dispatched: 2,
          cancel_requested: true,
          superseded_by_job_id: 'NEW-ATTEMPT',
        }),
      }),
    );
    expect(content).toContain('are o înlocuire consemnată');
    expect(content).toContain('nu confirmă oprirea ei');
    expect(content).not.toMatch(/Cererea de căutare a fost|am oprit|0 apeluri proprii/);
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
  });

  it.each(['finished', 'waiting_for_previous'] as const)(
    'keeps unknown dependency review distinct from a queued search at stage %s',
    (stage) => {
      const content = voiceCommandAcknowledgement(
        reportReceipt({
          recovery_job: queuedJob({
            status: 'needs_review',
            stage,
            calls_dispatched: 0,
            supersedes_job_ids: ['UNKNOWN-ATTEMPT'],
          }),
        }),
      );
      expect(content).toContain('Starea consemnată cere revizuirea încercărilor anterioare');
      expect(content).toContain('0 apeluri proprii');
      expect(content).not.toMatch(
        /aștepta încheierea|Cererea de căutare a fost|este contradictorie/,
      );
    },
  );

  it('never invents zero usage or waiting success for inconsistent status receipts', () => {
    for (const overrides of [
      { status: 'queued', calls_dispatched: 1 },
      { status: 'running', calls_dispatched: 0 },
    ] as const) {
      const content = voiceCommandAcknowledgement(
        reportReceipt({
          recovery_job: queuedJob({ stage: 'waiting_for_previous', ...overrides }),
        }),
      );
      expect(content).toContain('Starea așteptării este contradictorie');
      expect(content).not.toMatch(/0 apeluri proprii|aștepta încheierea/);
    }
  });

  it('acknowledges review for an inherited blocker even without a direct predecessor', () => {
    const content = voiceCommandAcknowledgement(
      reportReceipt({
        recovery_job: queuedJob({
          status: 'needs_review',
          stage: 'finished',
          calls_dispatched: 0,
          blocking_job_ids: ['UNKNOWN-ANCESTOR'],
        }),
      }),
    );
    expect(content).toContain('Starea consemnată cere revizuirea încercărilor anterioare');
    expect(content).toContain('0 apeluri proprii');
    expect(content).not.toContain('Cererea de căutare a fost');
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
  });

  it('speaks the server-resolved relative readiness without reanchoring it to the browser or retry time', () => {
    const receipt = reportReceipt({ replayed: true });
    if (receipt.report?.type !== 'arrival_report') throw new Error('Invalid arrival fixture');
    receipt.report.readiness_basis = {
      kind: 'command_relative_minutes',
      reference: 'în 30 de minute',
      command_received_at: '2026-11-01T09:59:50Z',
      anchor_at: '2026-11-01T10:00:00Z',
      rounding: 'ceil_minute',
      minutes: 30,
    };
    vi.setSystemTime(new Date('2026-11-02T12:00:00Z'));
    const content = voiceCommandAcknowledgement(receipt);
    expect(content).toContain('Raport deja înregistrat');
    expect(content).toContain('pregătite de la 2026-11-01 10:30 UTC');
    expect(content).not.toContain('2026-11-02');
    expect(content).not.toContain('în 30 de minute');
  });

  it('does not announce queue success for a foreign case or contradictory receipt', () => {
    const foreign = voiceCommandAcknowledgement(
      reportReceipt({
        recovery_job: queuedJob({ incident_id: 'CASE-OTHER' }),
      }),
    );
    expect(foreign).toContain('Raport înregistrat');
    expect(foreign).toContain('Nu pot confirma cazul căutării');
    expect(foreign).not.toContain('în coadă');
    const contradictory = voiceCommandAcknowledgement(
      reportReceipt({
        recovery_job: queuedJob(),
        recovery_blocked: { code: 'hotel_setup_required', explanation: 'Missing setup' },
      }),
    );
    expect(contradictory).toContain('Starea căutării este contradictorie');
    expect(contradictory).not.toMatch(/în coadă|Căutarea nu a pornit/);
  });
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => {
    resolve = yes;
  });
  return { promise, resolve };
}

class FakeAudio {
  autoplay = false;
  controls = false;
  srcObject: unknown = null;
  play = vi.fn(async () => undefined);
  pause = vi.fn();
  setAttribute() {}
}
class FakeTrack {
  enabled = true;
  stop = vi.fn();
}
class FakeStream {
  track = new FakeTrack();
  getTracks() {
    return [this.track];
  }
  getAudioTracks() {
    return [this.track];
  }
}
class FakeChannel extends EventTarget {
  readyState = 'open';
  sent: Record<string, unknown>[] = [];
  send = vi.fn((value: string) => {
    this.sent.push(JSON.parse(value));
  });
  close = vi.fn(() => {
    this.readyState = 'closed';
    this.dispatchEvent(new Event('close'));
  });
  emit(event: Record<string, unknown>) {
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify(event) }));
  }
}
class FakePeer extends EventTarget {
  static instances: FakePeer[] = [];
  channel = new FakeChannel();
  connectionState = 'new';
  iceGatheringState = 'complete';
  localDescription: { sdp: string } | null = null;
  createOffer = vi.fn(async () => ({ type: 'offer', sdp: 'v=0\r\no=offer' }));
  setLocalDescription = vi.fn(async (value: { sdp: string }) => {
    this.localDescription = value;
  });
  setRemoteDescription = vi.fn(async (_value: unknown): Promise<void> => undefined);
  createDataChannel = vi.fn(() => this.channel);
  addTrack = vi.fn();
  close = vi.fn(() => {
    this.connectionState = 'closed';
  });
  constructor() {
    super();
    FakePeer.instances.push(this);
  }
}
class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  close = vi.fn(async () => undefined);
  resume = vi.fn(async () => undefined);
  constructor() {
    FakeAudioContext.instances.push(this);
  }
  createAnalyser() {
    return { fftSize: 256, getByteTimeDomainData: (array: Uint8Array) => array.fill(140) };
  }
  createMediaStreamSource() {
    return { connect: vi.fn() };
  }
}

let stream: FakeStream;
let permission: ReturnType<typeof vi.fn>;
let fetchMock: ReturnType<typeof vi.fn>;
const answer = () => ({
  ok: true,
  json: async () => ({ session_id: 'live_test', sdp: 'v=0\r\no=answer', max_seconds: 180 }),
});

beforeEach(() => {
  vi.useFakeTimers();
  FakePeer.instances = [];
  FakeAudioContext.instances = [];
  stream = new FakeStream();
  permission = vi.fn(async () => stream);
  fetchMock = vi.fn(async () => answer());
  vi.stubGlobal('Audio', FakeAudio);
  vi.stubGlobal('RTCPeerConnection', FakePeer);
  vi.stubGlobal('MediaStream', FakeStream);
  vi.stubGlobal('AudioContext', FakeAudioContext);
  vi.stubGlobal('window', { isSecureContext: true });
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: permission } });
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn(() => 1),
  );
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});
afterEach(() => {
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function clientWith(overrides: Partial<LiveVoiceOptions> = {}) {
  const states: Array<[VoiceState, string]> = [];
  const onDelegation = vi.fn<LiveVoiceOptions['onDelegation']>(
    async () => 'Nu am aplicat nicio modificare.',
  );
  const client = new LiveVoiceClient({
    onState: (state, message) => states.push([state, message]),
    onTranscript: vi.fn(),
    onDelegation,
    ...overrides,
  });
  return { client, states, onDelegation };
}
async function connected(overrides: Partial<LiveVoiceOptions> = {}) {
  const result = clientWith(overrides);
  await result.client.start();
  const peer = FakePeer.instances.at(-1)!;
  peer.channel.emit({ type: 'session.started', session: { id: 'live_test' } });
  return { ...result, peer, channel: peer.channel };
}
async function end(client: LiveVoiceClient, channel: FakeChannel) {
  const done = client.stop();
  channel.emit({ type: 'session.closed', usage: { seconds: 1 } });
  await done;
}
function command(channel: FakeChannel, id = 'delegation-1', text = 'Arată autocarul B12.') {
  channel.emit({
    type: 'session.input_transcript.delta',
    event_id: 'transcript-1',
    delta: text,
    start_ms: 0,
    end_ms: 900,
  });
  channel.emit({
    type: 'session.delegation.created',
    event_id: `event-${id}`,
    offset_ms: 1000,
    delegation: { id, target: 'client' },
  });
}

describe('GPT-Live lifecycle with mocked browser resources', () => {
  it('speaks the report outcome once and never borrows earlier search scope or a call cap for the next report', async () => {
    const onDelegation = vi
      .fn<LiveVoiceOptions['onDelegation']>()
      .mockResolvedValueOnce(
        reportReceipt({ recovery_job: queuedJob(), recovery_total_call_limit: 11 }),
      )
      .mockResolvedValueOnce(reportReceipt());
    const { client, channel } = await connected({ onDelegation, scope: { incident_id: 'CASE1' } });
    const first =
      'Grupurile G1 și G2 din CASE1 sunt pregătite la OTP la 2026-11-01 10:30 UTC. Înregistrează și recalculează cazul CASE1. Maximum 11 apeluri.';
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: first,
      start_ms: 0,
      end_ms: 900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1000,
      delegation: { id: 'report-search', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1000,
      delegation: { id: 'report-search', target: 'client' },
    });
    const second =
      'Doar înregistrează: grupurile G1 și G2 din CASE1 sunt pregătite la OTP la 2026-11-01 10:30 UTC.';
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: second,
      start_ms: 1001,
      end_ms: 1900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2000,
      delegation: { id: 'report-only', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledTimes(2);
    expect(onDelegation.mock.calls[1][0]).toEqual({
      text: second,
      context: `user: ${first}`,
      delegationId: 'live_test:report-only',
    });
    const spoken = channel.sent.filter((event) => event.type === 'session.commentary.append');
    expect(spoken).toHaveLength(2);
    expect(spoken[0].content).toContain('Limită totală: 11');
    expect(spoken[1].content).toContain('Raport înregistrat');
    expect(spoken[1].content).toContain('Nu am pornit o căutare.');
    expect(spoken[1].content).not.toMatch(/11 apeluri|CASE1|Cererea de căutare|Limită totală/);
    expect(fetchMock).toHaveBeenCalledTimes(1); // Only the mocked session handshake.
    await end(client, channel);
  });

  it('keeps an uncertain report outcome unknown and recovers the identical request without another delegation', async () => {
    let uncertain: TransportError | undefined;
    let submitted: Record<string, unknown> | undefined;
    const onDelegation = vi.fn<LiveVoiceOptions['onDelegation']>(
      async ({ text, context, delegationId }) => {
        submitted = {
          text,
          context,
          expected_version: 6,
          idempotency_key: `voice-${delegationId}`,
        };
        try {
          return await request<MutationResult>('/commands', submitted);
        } catch (error) {
          if (error instanceof TransportError) uncertain = error;
          throw error;
        }
      },
    );
    const { client, channel } = await connected({ onDelegation });
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 503,
      json: async () => ({ applied: null, detail: 'Request outcome unknown' }),
    });
    command(
      channel,
      'report-unknown',
      'Înregistrează: grupurile G1 și G2 din CASE1 sunt pregătite la OTP la 2026-11-01 10:30 UTC.',
    );
    await vi.advanceTimersByTimeAsync(250);
    expect(uncertain).toBeInstanceOf(TransportError);
    const spoken = channel.sent.filter((event) => event.type === 'session.commentary.append');
    expect(spoken).toHaveLength(1);
    expect(spoken[0].content).toContain('raportul sau căutarea pot fi deja înregistrate');
    expect(spoken[0].content).toContain('Recover request cu același ID');
    expect(spoken[0].content).not.toMatch(/Raport înregistrat:|Nu am aplicat|în coadă/);
    expect(onDelegation).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const originalBody = fetchMock.mock.calls[1][1].body;
    // Changing current UI/input objects cannot change the serialized retry.
    Object.assign(submitted!, {
      text: 'A different request with 27 calls',
      context: 'New history',
      expected_version: 99,
      idempotency_key: 'new-key',
    });
    const replay = reportReceipt({ replayed: true });
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => replay });
    expect(await uncertain!.retry<MutationResult>()).toEqual(replay);
    expect(fetchMock.mock.calls[2][1].body).toBe(originalBody);
    expect(onDelegation).toHaveBeenCalledOnce();
    expect(channel.sent.filter((event) => event.type === 'session.commentary.append')).toHaveLength(
      1,
    );
    await end(client, channel);
  });

  it('sends only a snapshot of selected scope IDs after permission without executing a command', async () => {
    const delayed = deferred<FakeStream>();
    permission.mockReturnValue(delayed.promise);
    const scope = {
      incident_id: 'CASE1',
      group_id: 'G1',
      label: 'Do not send',
      context: 'Do not send',
      model: 'Do not send',
      max_provider_calls: 26,
    };
    const { client, onDelegation } = clientWith({ scope });
    const starting = client.start();
    expect(fetchMock).not.toHaveBeenCalled();
    scope.incident_id = 'CASE2';
    scope.group_id = 'G2';
    delayed.resolve(stream);
    await starting;
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/voice/session');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      sdp: 'v=0\r\no=offer',
      scope: { incident_id: 'CASE1', group_id: 'G1' },
    });
    expect(onDelegation).not.toHaveBeenCalled();
    await end(client, FakePeer.instances.at(-1)!.channel);
  });

  it.each([undefined, {}, { incident_id: '', group_id: '  ' }])(
    'omits an empty startup scope and retains the existing SDP-only request',
    async (scope) => {
      const { client, channel, onDelegation } = await connected({ scope });
      expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ sdp: 'v=0\r\no=offer' });
      expect(onDelegation).not.toHaveBeenCalled();
      await end(client, channel);
    },
  );

  it('delegates a complete free-form compound request once, never each transcript fragment', async () => {
    const { client, channel, onDelegation } = await connected();
    [
      'Adaugă un autocar nou cu 52 de locuri. ',
      'Pleacă din Craiova spre Sibiu la 18:00, ',
      'iar B12 este defect.',
    ].forEach((delta, index) => {
      channel.emit({
        type: 'session.input_transcript.delta',
        event_id: `compound-${index}`,
        delta,
        start_ms: index * 500,
        end_ms: (index + 1) * 500,
      });
    });
    await vi.advanceTimersByTimeAsync(1000);
    expect(onDelegation).not.toHaveBeenCalled();
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1600,
      delegation: { id: 'compound', target: 'client' },
    });
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Și bugetul să fie 9000 de euro.',
      start_ms: 2000,
      end_ms: 2400,
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledOnce();
    expect(onDelegation).toHaveBeenCalledWith(
      expect.objectContaining({
        text: 'Adaugă un autocar nou cu 52 de locuri. Pleacă din Craiova spre Sibiu la 18:00, iar B12 este defect.',
        delegationId: 'live_test:compound',
      }),
    );
    await end(client, channel);
  });

  it('passes prior dialogue as reference context while executing only the new clarification reply', async () => {
    const { client, channel, onDelegation } = await connected();
    command(channel);
    await vi.advanceTimersByTimeAsync(250);
    channel.emit({
      type: 'session.output_transcript.delta',
      delta: 'Te referi la B12 din Craiova?',
      start_ms: 1100,
      end_ms: 1500,
    });
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Da, din Craiova.',
      start_ms: 1600,
      end_ms: 1900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2000,
      delegation: { id: 'clarification', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledTimes(2);
    expect(onDelegation).toHaveBeenLastCalledWith({
      text: 'Da, din Craiova.',
      delegationId: 'live_test:clarification',
      context: 'user: Arată autocarul B12.\nassistant: Te referi la B12 din Craiova?',
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2100,
      delegation: { id: 'empty-followup', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledTimes(2);
    await end(client, channel);
  });

  it('keeps recoverable command failures controllable and listening, then closes every local resource', async () => {
    const onAudioLevel = vi.fn();
    const { client, states, peer, channel } = await connected({
      onAudioLevel,
      onDelegation: async () => {
        throw new Error('World version changed');
      },
    });
    command(channel);
    await vi.advanceTimersByTimeAsync(250);
    expect(states.at(-1)).toEqual(['listening', '']);
    expect(peer.close).not.toHaveBeenCalled();
    expect(stream.track.stop).not.toHaveBeenCalled();
    expect(
      channel.sent.find((event) => event.type === 'session.commentary.append')?.content,
    ).toContain('Nu afirma că planul a fost aplicat');
    await end(client, channel);
    expect(stream.track.stop).toHaveBeenCalledOnce();
    expect(peer.close).toHaveBeenCalledOnce();
    expect(FakeAudioContext.instances[0].close).toHaveBeenCalledOnce();
    expect(onAudioLevel).toHaveBeenLastCalledWith(0);
    expect(client.audio.srcObject).toBeNull();
  });

  it('shares repeated stop completion and refuses a second start until close is confirmed', async () => {
    const { client, channel } = await connected();
    const first = client.stop(),
      second = client.stop();
    expect(second).toBe(first);
    await client.start();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(FakePeer.instances).toHaveLength(1);
    expect(channel.sent.filter((event) => event.type === 'session.close')).toHaveLength(1);
    channel.emit({ type: 'session.closed' });
    await Promise.all([first, second]);
    await client.start();
    expect(FakePeer.instances).toHaveLength(2);
    await client.stop(); // This second peer never received session.started.
  });

  it('stops tracks returned after permission was cancelled and never starts a provider session', async () => {
    const delayed = deferred<FakeStream>();
    permission.mockReturnValue(delayed.promise);
    const { client, states } = clientWith();
    const starting = client.start();
    await client.stop();
    delayed.resolve(stream);
    await starting;
    expect(stream.track.stop).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(FakePeer.instances[0].close).toHaveBeenCalledOnce();
    expect(states.at(-1)?.[0]).toBe('idle');
  });

  it('does not let a stale session HTTP response resurrect a closed connection', async () => {
    const delayed = deferred<ReturnType<typeof answer>>();
    fetchMock.mockReturnValue(delayed.promise);
    const { client, states } = clientWith();
    const starting = client.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetchMock).toHaveBeenCalledOnce();
    const requestSignal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    await client.stop();
    const afterStop = states.length;
    delayed.resolve(answer());
    await starting;
    expect(requestSignal.aborted).toBe(true);
    expect(FakePeer.instances[0].setRemoteDescription).not.toHaveBeenCalled();
    expect(stream.track.stop).toHaveBeenCalledOnce();
    await vi.advanceTimersByTimeAsync(25000);
    expect(states).toHaveLength(afterStop);
  });

  it('ignores remote SDP completion after stop and does not arm stale timers', async () => {
    const { client, states } = clientWith();
    const delayed = deferred<void>();
    const starting = client.start();
    const peer = FakePeer.instances[0];
    peer.setRemoteDescription.mockReturnValue(delayed.promise);
    await vi.advanceTimersByTimeAsync(0);
    expect(peer.setRemoteDescription).toHaveBeenCalledOnce();
    await client.stop();
    const count = states.length;
    delayed.resolve();
    await starting;
    await vi.advanceTimersByTimeAsync(25000);
    expect(states).toHaveLength(count);
    expect(peer.close).toHaveBeenCalledOnce();
  });

  it('bounds a stalled handshake, aborts HTTP and stops the microphone without claiming remote closure', async () => {
    const delayed = deferred<ReturnType<typeof answer>>();
    fetchMock.mockReturnValue(delayed.promise);
    const { client, states } = clientWith();
    const starting = client.start();
    await vi.advanceTimersByTimeAsync(0);
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal;
    await vi.advanceTimersByTimeAsync(60000);
    expect(states.at(-1)?.[0]).toBe('error');
    expect(states.at(-1)?.[1]).toContain('nu este confirmat');
    expect(signal.aborted).toBe(true);
    expect(stream.track.stop).toHaveBeenCalledOnce();
    expect(FakePeer.instances[0].close).toHaveBeenCalledOnce();
    delayed.resolve(answer());
    await starting;
    expect(FakePeer.instances[0].setRemoteDescription).not.toHaveBeenCalled();
  });

  it('cancels the handshake deadline after successful connection', async () => {
    const { client, peer, channel, states } = await connected();
    await vi.advanceTimersByTimeAsync(60000);
    expect(peer.close).not.toHaveBeenCalled();
    expect(states.at(-1)?.[0]).toBe('listening');
    await end(client, channel);
  });

  it('reports playback blocking through a callback without corrupting voice state', async () => {
    const onPlaybackBlocked = vi.fn();
    const { client, peer, channel, states } = await connected({ onPlaybackBlocked });
    (client.audio.play as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('Autoplay blocked'),
    );
    const before = states.length;
    peer.dispatchEvent(
      Object.assign(new Event('track'), { streams: [stream], track: stream.track }),
    );
    await vi.advanceTimersByTimeAsync(0);
    expect(onPlaybackBlocked).toHaveBeenCalledOnce();
    expect(states).toHaveLength(before);
    await end(client, channel);
  });

  it('deduplicates delegation events and returns the verified no-change response verbatim', async () => {
    const { client, channel, onDelegation } = await connected();
    command(channel);
    channel.emit({
      type: 'session.delegation.created',
      event_id: 'new-event-same-delegation',
      offset_ms: 1000,
      delegation: { id: 'delegation-1', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledOnce();
    expect(channel.sent.filter((event) => event.type === 'session.commentary.append')).toEqual([
      expect.objectContaining({
        content: 'Nu am aplicat nicio modificare.',
        delegation_id: 'delegation-1',
      }),
    ]);
    await end(client, channel);
  });

  it('keeps the full date clarification audible and forwards a complete correction unchanged', async () => {
    const { client, channel, onDelegation, states } = await connected();
    const clarification =
      'Nicio modificare operațională aplicată. Data asociată cazului sau zborului este ambiguă ori invalidă. Repetă comanda completă corectată: ID-ul exact al cazului/zborului, o singură dată YYYY-MM-DD, grupul/grupurile, serviciile cerute și limita totală explicită de apeluri pentru noua cerere. Nu am pornit căutarea. Interpretarea a folosit 1 apel. Versiunea 49.';
    onDelegation.mockResolvedValueOnce(clarification);
    const malformed =
      'Recalculează pentru Planning group 1, EK5 din 30 și 1 octombrie 2020 și 6, hotel, ambele transferuri și zbor. Maximum 11 apeluri în total.';
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: malformed,
      start_ms: 0,
      end_ms: 900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1000,
      delegation: { id: 'bad-date', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledExactlyOnceWith({
      text: malformed,
      context: '',
      delegationId: 'live_test:bad-date',
    });
    expect(channel.sent.find((event) => event.type === 'session.commentary.append')?.content).toBe(
      clarification,
    );
    expect(states.at(-1)?.[0]).toBe('listening');
    const messages = states
      .map(([, message]) => message)
      .concat(channel.sent.map((event) => String(event.content ?? '')));
    expect(messages.join('\n')).not.toMatch(/simulat/i);
    expect(messages.join('\n')).toContain('Încă nu există un rezultat confirmat');

    const corrected =
      'Recalculează pentru Planning group 1, EK5 din 2026-10-31, hotel, ambele transferuri și zbor. Maximum 11 apeluri în total.';
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: corrected,
      start_ms: 1200,
      end_ms: 1900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2000,
      delegation: { id: 'corrected-date', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledTimes(2);
    expect(onDelegation).toHaveBeenLastCalledWith({
      text: corrected,
      context: `user: ${malformed}`,
      delegationId: 'live_test:corrected-date',
    });
    await end(client, channel);
  });

  it('retains a delegation when its transcript arrives after the old 250ms deadline', async () => {
    const { client, channel, onDelegation } = await connected();
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1000,
      delegation: { id: 'late-text', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(500);
    expect(onDelegation).not.toHaveBeenCalled();
    expect(channel.sent.filter((event) => event.type === 'session.commentary.append')).toHaveLength(
      0,
    );
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Arată autocarul B12.',
      start_ms: 0,
      end_ms: 900,
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({
        text: 'Arată autocarul B12.',
        delegationId: 'live_test:late-text',
      }),
    );
    await end(client, channel);
  });

  it('lets a compound request finish arriving before dispatching its accumulated text', async () => {
    const { client, channel, onDelegation } = await connected();
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Adaugă un autocar nou ',
      start_ms: 0,
      end_ms: 400,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1000,
      delegation: { id: 'settling-text', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(200);
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'cu 52 de locuri spre Sibiu.',
      start_ms: 400,
      end_ms: 900,
    });
    await vi.advanceTimersByTimeAsync(50);
    expect(onDelegation).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ text: 'Adaugă un autocar nou cu 52 de locuri spre Sibiu.' }),
    );
    await end(client, channel);
  });

  it('retires an expired turn so its late words never join a later complete command', async () => {
    const { client, channel, onDelegation } = await connected();
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1000,
      delegation: { id: 'expired-text', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(2100);
    expect(onDelegation).not.toHaveBeenCalled();
    expect(
      channel.sent.find((event) => event.type === 'session.commentary.append')?.content,
    ).toContain('nu am trimis-o aplicației');
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Anulează zborul F101. ',
      start_ms: 0,
      end_ms: 900,
    });
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Arată autocarul B12.',
      start_ms: 1100,
      end_ms: 1900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2000,
      delegation: { id: 'new-complete-text', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ text: 'Arată autocarul B12.' }),
    );
    await end(client, channel);
  });

  it('keeps queued command windows separate even when an earlier command is still running', async () => {
    const result = deferred<string>();
    const onDelegation = vi
      .fn()
      .mockReturnValueOnce(result.promise)
      .mockResolvedValue('Noua cerere verificată.');
    const { client, channel } = await connected({ onDelegation });
    command(channel);
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledOnce();
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: ' Fragment vechi întârziat.',
      start_ms: 800,
      end_ms: 950,
    });
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Arată aeroportul OTP.',
      start_ms: 1100,
      end_ms: 1900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2000,
      delegation: { id: 'queued-command', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(500);
    expect(onDelegation).toHaveBeenCalledOnce();
    result.resolve('Prima cerere verificată.');
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledTimes(2);
    expect(onDelegation).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ text: 'Arată aeroportul OTP.' }),
    );
    await end(client, channel);
  });

  it('keeps speech on the exact next-window boundary and excludes retired late words from reference context', async () => {
    const { client, channel, onDelegation } = await connected();
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 1000,
      delegation: { id: 'expired-boundary', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(2100);
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Anulează zborul F101.',
      start_ms: 0,
      end_ms: 900,
    });
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Arată autocarul B12.',
      start_ms: 1000,
      end_ms: 1900,
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2000,
      delegation: { id: 'next-boundary', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledExactlyOnceWith({
      text: 'Arată autocarul B12.',
      delegationId: 'live_test:next-boundary',
      context: '',
    });
    await end(client, channel);
  });

  it('never executes a delegation with missing or backwards transcript boundaries', async () => {
    const { client, channel, onDelegation } = await connected();
    command(channel);
    await vi.advanceTimersByTimeAsync(250);
    channel.emit({
      type: 'session.input_transcript.delta',
      delta: 'Arată aeroportul OTP.',
      start_ms: 1100,
      end_ms: 1900,
    });
    channel.emit({
      type: 'session.delegation.created',
      delegation: { id: 'missing-offset', target: 'client' },
    });
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 900,
      delegation: { id: 'old-offset', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledOnce();
    channel.emit({
      type: 'session.delegation.created',
      offset_ms: 2000,
      delegation: { id: 'valid-offset', target: 'client' },
    });
    await vi.advanceTimersByTimeAsync(250);
    expect(onDelegation).toHaveBeenCalledTimes(2);
    expect(onDelegation).toHaveBeenNthCalledWith(
      2,
      expect.objectContaining({ text: 'Arată aeroportul OTP.' }),
    );
    await end(client, channel);
  });

  it('bounds Unicode commentary conservatively below the 500-token append limit without splitting code points', async () => {
    const { client, channel } = await connected({ onDelegation: async () => '🛬ș漢'.repeat(300) });
    command(channel);
    await vi.advanceTimersByTimeAsync(250);
    const content = channel.sent.find((event) => event.type === 'session.commentary.append')
      ?.content as string;
    expect(new TextEncoder().encode(content).byteLength).toBeLessThanOrEqual(480);
    expect(content.endsWith('…')).toBe(true);
    expect(new TextDecoder().decode(new TextEncoder().encode(content))).toBe(content);
    expect(content).not.toContain('�');
    await end(client, channel);
  });

  it('cleans up after an unexpected queue error instead of exposing an orphan error state', async () => {
    const states: VoiceState[] = [];
    const { client, peer, channel } = await connected({
      onState: (state) => {
        if (state === 'working') throw new Error('Unexpected rendering failure');
        states.push(state);
      },
    });
    command(channel);
    await vi.advanceTimersByTimeAsync(250);
    expect(states.at(-1)).toBe('error');
    expect(peer.close).toHaveBeenCalledOnce();
    expect(stream.track.stop).toHaveBeenCalledOnce();
    await client.stop();
  });

  it('ends local capture when final server acknowledgement never arrives', async () => {
    const { client, peer, states } = await connected();
    const stopping = client.stop();
    await vi.advanceTimersByTimeAsync(15000);
    await stopping;
    expect(states.at(-1)?.[0]).toBe('error');
    expect(states.at(-1)?.[1]).toContain('nu este confirmat');
    expect(peer.close).toHaveBeenCalledOnce();
    expect(stream.track.stop).toHaveBeenCalledOnce();
  });
});
