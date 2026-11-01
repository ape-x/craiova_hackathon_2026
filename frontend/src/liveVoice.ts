/** GPT-Live WebRTC, not the older Realtime protocol.
 * Official contract: https://developers.openai.com/api/docs/guides/voice-webrtc
 * No project key or provider configuration reaches the browser.
 */
import { TransportError } from './api';
import { verifiedCommandResult } from './commandContext';
import type { MutationResult } from './types';
import {
  combinedReports,
  UNCONFIRMED_COMBINED_REPORT,
  type CombinedReportCheck,
} from './combinedReports';

export type VoiceState = 'idle' | 'connecting' | 'listening' | 'working' | 'closing' | 'error';
export type DelegationRequest = { text: string; delegationId: string; context: string };
export interface VoiceStartupScope {
  incident_id?: string;
  group_id?: string;
}
export interface LiveVoiceOptions {
  sessionUrl?: string;
  /** Selected IDs are reference context only; the server resolves facts and execution remains explicit. */
  scope?: VoiceStartupScope;
  onState: (state: VoiceState, message: string) => void;
  onTranscript: (text: string, role: 'user' | 'assistant') => void;
  onAudioLevel?: (level: number) => void;
  /** Render a user-gesture Play control calling client.audio.play(). */
  onPlaybackBlocked?: () => void;
  /** Must execute through the versioned backend, then return verified facts. */
  onDelegation: (request: DelegationRequest) => Promise<string | MutationResult>;
  onEvent?: (event: Record<string, unknown>) => void;
}

type Fragment = { text: string; start: number; end: number; receivedAt: number };
type DelegationWindow = { start: number; end: number };
type ConversationFragment = Fragment & { role: 'user' | 'assistant' };

/** Context appends allow 500 tokens. A smaller UTF-8 byte budget is a
 * conservative bound without shipping a tokenizer or guessing chars/token.
 * Iterate code points so truncation never splits a surrogate pair.
 */
function boundedContext(content: string, limit = 480): string {
  const encoder = new TextEncoder();
  if (encoder.encode(content).byteLength <= limit) return content;
  let result = '',
    bytes = 0;
  for (const character of content) {
    const length = encoder.encode(character).byteLength;
    if (bytes + length > limit - 3) break;
    bytes += length;
    result += character;
  }
  return result.trimEnd() + '…';
}

const ROMANIAN_MONTHS = [
  'ianuarie',
  'februarie',
  'martie',
  'aprilie',
  'mai',
  'iunie',
  'iulie',
  'august',
  'septembrie',
  'octombrie',
  'noiembrie',
  'decembrie',
];

function reportedReadinessForSpeech(value: string): string {
  const ready = Date.parse(value);
  if (!Number.isFinite(ready) || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(value))
    return 'data și ora declarate în panou';
  const instant = new Date(ready);
  // One verified instant supplies both calendar date and time. Neither the
  // case's flight lookup date nor the browser/replay clock can fill a gap.
  return `${instant.getUTCDate()} ${ROMANIAN_MONTHS[instant.getUTCMonth()]} ${instant.getUTCFullYear()}, ora ${instant.toISOString().slice(11, 16)} UTC`;
}

/** Only facts from this backend receipt may describe the outcome or allowance.
 * Reserve room for the qualification; long scope/reason text cannot remove it.
 * Full details remain in the command reply and case panel, without another call.
 */
export function voiceCommandAcknowledgement(result: MutationResult): string {
  const bundle = combinedReports(result);
  if (bundle.state === 'invalid') return UNCONFIRMED_COMBINED_REPORT;
  if (bundle.state === 'combined') return combinedReportAcknowledgement(result, bundle);
  const report =
    result.applied === true && result.report?.source === 'operator_report'
      ? result.report
      : undefined;
  const job = result.recovery_job;
  const blocked = result.recovery_blocked;
  if (!report && !job && !blocked) return boundedContext(verifiedCommandResult(result));

  const parts: string[] = [];
  const recorded = result.replayed ? 'deja înregistrat' : 'înregistrat';
  const groups = (count: number) => `${count} ${count === 1 ? 'grup' : 'grupuri'}`;
  if (report?.type === 'arrival_report') {
    const time = reportedReadinessForSpeech(report.ready_at);
    parts.push(
      `Raport ${recorded}: ${groups(report.group_ids.length)} la ${report.airport_code}, ${report.group_ids.length === 1 ? 'pregătit' : 'pregătite'} de la ${time}.`,
    );
  } else if (report?.type === 'resource_unavailability') {
    parts.push(
      `Raport ${recorded}: serviciul ${report.resource_id} indisponibil pentru planificare.`,
    );
  } else if (report?.type === 'airport_restriction') {
    const operations = report.operations
      .map((operation) => (operation === 'arrivals' ? 'aterizări' : 'decolări'))
      .join(' și ');
    parts.push(
      `Raport ${recorded}: restricția cazului pentru ${report.airport_code}, ${operations}, ${report.action === 'restrict' ? 'aplicată' : 'eliminată'}.`,
    );
  }

  if (job && blocked) {
    parts.push('Starea căutării este contradictorie în răspuns; verifică panoul cazului.');
  } else if (job) {
    if (!result.incident_id || result.incident_id !== job.incident_id) {
      parts.push('Nu pot confirma cazul căutării din acest răspuns; verifică panoul cazului.');
    } else if (job.superseded_by_job_id) {
      parts.push(
        'Cererea din răspuns are o înlocuire consemnată; această legătură nu confirmă oprirea ei.',
      );
    } else if (
      job.stage === 'waiting_for_previous' &&
      (job.status === 'queued' || job.status === 'running')
    ) {
      if (job.status === 'queued' && job.calls_dispatched === 0) {
        // This is the immutable receipt, including on an exact replay. Current
        // dependency outcomes are fetched by the case panel, never inferred here.
        parts.push(
          'La înregistrare, cererea aștepta încheierea încercărilor anterioare; 0 apeluri proprii.',
        );
      } else {
        parts.push('Starea așteptării este contradictorie; verifică panoul cazului.');
      }
    } else if (
      job.status === 'needs_review' &&
      (job.supersedes_job_ids?.length || job.blocking_job_ids?.length) &&
      job.calls_dispatched === 0
    ) {
      parts.push('Starea consemnată cere revizuirea încercărilor anterioare; 0 apeluri proprii.');
    } else if (job.status === 'queued' || job.status === 'running') {
      parts.push(
        `Cererea de căutare a fost ${result.replayed ? 'deja ' : ''}înregistrată pentru ${groups(job.group_ids.length)}${report ? '' : `, cazul ${result.incident_id}`}.`,
      );
    } else {
      // A status read or replay may include an old completed job. It is not a
      // fresh queue receipt or proof that its proposal is still usable.
      parts.push(
        `Starea consemnată a căutării: ${job.status}. Verifică propunerea actuală în panoul cazului.`,
      );
    }
  } else if (blocked) {
    const reasons = {
      insufficient_call_allowance: 'limita de apeluri este insuficientă',
      hotel_setup_required: 'configurația hotelului trebuie completată sau revizuită',
      romanian_hotel_setup_required: 'completează formularul pentru hotel și transport în România',
      airport_transfer_setup_required: 'configurația transferului trebuie completată sau revizuită',
      ai_not_configured: 'serviciul AI necesar nu este disponibil',
      recovery_service_unavailable: 'serviciul de recuperare nu este disponibil',
      recovery_prerequisite_failed:
        'o condiție necesară nu este îndeplinită; verifică panoul cazului',
      recovery_queue_unavailable:
        'coada de recuperare nu este disponibilă; verifică panoul cazului',
    };
    parts.push(
      `Căutarea nu a pornit: ${reasons[blocked.code] ?? 'verifică motivul în panoul cazului'}.`,
    );
  } else {
    parts.push('Nu am pornit o căutare.');
  }

  if (
    (job || blocked) &&
    Number.isSafeInteger(result.recovery_total_call_limit) &&
    result.recovery_total_call_limit! >= 0 &&
    result.recovery_total_call_limit! <= 27
  ) {
    parts.push(
      `Limită totală: ${result.recovery_total_call_limit} apeluri, inclusiv interpretarea.`,
    );
  }
  if (
    job &&
    !blocked &&
    result.incident_id === job.incident_id &&
    (job.status === 'queued' || job.status === 'running')
  ) {
    parts.push('Vezi progresul curent în panoul cazului.');
  }
  const qualification =
    report?.type === 'airport_restriction'
      ? `${result.manual_review_required || report.manual_review_required ? 'Rezervări confirmate în conflict; intervenție necesară. ' : ''}Condiție a cazului; nu confirmă permisiunea de zbor, sosirea pasagerilor sau rezervarea.`
      : report?.type === 'arrival_report'
        ? 'Date raportate, nu confirmare de aterizare, autorizare aeroportuară, itinerar sau rezervare.'
        : report
          ? 'Nu este anulare la furnizor, itinerar confirmat sau rezervare.'
          : 'Nu confirmă un itinerar sau o rezervare.';
  const available = 480 - new TextEncoder().encode(qualification).byteLength - 1;
  return `${boundedContext(parts.join(' '), available)} ${qualification}`;
}

/** Both atomic report facts and the recorded search outcome fit before optional detail. */
function combinedReportAcknowledgement(
  result: MutationResult,
  bundle: Extract<CombinedReportCheck, { state: 'combined' }>,
): string {
  const { arrival } = bundle;
  let operation: string;
  if (bundle.kind === 'resource_and_arrival') {
    // Keep a short ID whole. Long IDs remain exact in the visible receipt;
    // truncating one in speech could identify a different service.
    const id = bundle.resource.resource_id;
    const service =
      new TextEncoder().encode(id).byteLength <= 40 ? `serviciul ${id}` : 'serviciul indicat';
    operation = `${service} raportat indisponibil pentru planificare`;
  } else {
    const { restriction } = bundle;
    const operations = restriction.operations
      .map((item) => (item === 'arrivals' ? 'aterizări' : 'decolări'))
      .join(' și ');
    operation = `${restriction.airport_code}, ${operations}, restricție ${restriction.action === 'restrict' ? 'aplicată' : 'eliminată'} în caz`;
  }
  const count = arrival.group_ids.length;
  const facts = `Rapoarte ${result.replayed ? 'deja ' : ''}înregistrate: ${operation}; ${count} ${count === 1 ? 'grup' : 'grupuri'} la ${arrival.airport_code}, ${count === 1 ? 'pregătit' : 'pregătite'} de la ${reportedReadinessForSpeech(arrival.ready_at)}.`;
  const job = result.recovery_job;
  const blocked = result.recovery_blocked;
  let status: string;
  if (job && blocked) status = 'Stare de căutare contradictorie; verifică cazul.';
  else if (job) {
    if (
      job.incident_id !== result.incident_id ||
      job.group_ids.length !== count ||
      job.group_ids.some((id) => !arrival.group_ids.includes(id))
    )
      status = 'Cazul sau grupurile căutării diferă; verifică panoul.';
    else if (job.superseded_by_job_id)
      status = 'Cerere înlocuită în evidență; oprirea nu e confirmată.';
    else if (job.stage === 'waiting_for_previous' && ['queued', 'running'].includes(job.status))
      status =
        job.status === 'queued' && job.calls_dispatched === 0
          ? 'La înregistrare: aștepta încercările anterioare; 0 apeluri proprii.'
          : 'Așteptare contradictorie; verifică panoul.';
    else if (
      job.status === 'needs_review' &&
      (job.supersedes_job_ids?.length || job.blocking_job_ids?.length) &&
      job.calls_dispatched === 0
    )
      status = 'Cere revizuirea încercărilor anterioare; 0 apeluri proprii.';
    else if (job.status === 'queued' || job.status === 'running')
      status = `Căutare ${result.replayed ? 'deja ' : ''}înregistrată; verifică progresul actual.`;
    else {
      const labels: Record<string, string> = {
        completed: 'încheiată',
        blocked: 'blocată',
        failed: 'eșuată',
        cancelled: 'anulată',
        needs_review: 'necesită revizuire',
        unknown_outcome: 'rezultat incert',
      };
      status = `Căutare consemnată: ${labels[job.status] ?? 'neconfirmată'}; verifică panoul.`;
    }
  } else if (blocked) {
    const reasons: Record<string, string> = {
      insufficient_call_allowance: 'limită insuficientă',
      hotel_setup_required: 'revizuiește hotelul',
      romanian_hotel_setup_required: 'completează pachetul hotel din România',
      airport_transfer_setup_required: 'revizuiește transferul',
      recovery_queue_unavailable: 'coadă indisponibilă',
      ai_not_configured: 'AI indisponibil',
      recovery_service_unavailable: 'serviciu indisponibil',
    };
    status = `Căutarea nu a pornit: ${reasons[blocked.code] ?? 'verifică motivul în panou'}.`;
  } else status = 'Nu am pornit o căutare.';
  const conflict =
    (bundle.kind === 'airport_and_arrival' && bundle.restriction.manual_review_required) ||
    result.manual_review_required
      ? 'Rezervări în conflict; intervenție necesară.'
      : '';
  const qualification =
    bundle.kind === 'resource_and_arrival'
      ? 'Date raportate; nu confirmă aterizarea, disponibilitatea la furnizor sau rezervări.'
      : 'Date raportate; nu confirmă aterizarea, permisiuni de zbor sau rezervări.';
  const required = [facts, status, conflict, qualification].filter(Boolean).join(' ');
  // Bounded whole service IDs/airport codes/group counts and fixed vocabulary preserve
  // both reports. The optional limit never displaces either report or status.
  const limit =
    (job || blocked) &&
    Number.isSafeInteger(result.recovery_total_call_limit) &&
    result.recovery_total_call_limit! >= 0 &&
    result.recovery_total_call_limit! <= 27
      ? ` Limită: ${result.recovery_total_call_limit} apeluri cu interpretarea.`
      : '';
  return new TextEncoder().encode(required + limit).byteLength <= 480 ? required + limit : required;
}

export class LiveVoiceClient {
  readonly audio = new Audio();
  private peer?: RTCPeerConnection;
  private channel?: RTCDataChannel;
  private microphone?: MediaStream;
  private audioContext?: AudioContext;
  private meterFrame?: number;
  private abort?: AbortController;
  private sessionId?: string;
  private ready = false;
  private closing = false;
  private finalized = false;
  private generation = 0;
  private durationTimer?: ReturnType<typeof setTimeout>;
  private startupTimer?: ReturnType<typeof setTimeout>;
  private handshakeTimer?: ReturnType<typeof setTimeout>;
  private closeTimer?: ReturnType<typeof setTimeout>;
  private fragments: Fragment[] = [];
  private conversation: ConversationFragment[] = [];
  private retiredThrough = -1;
  private delegatedThrough = -1;
  private transcript = { user: '', assistant: '' };
  private seenDelegations = new Set<string>();
  private seenEvents = new Set<string>();
  private delegationQueue: Promise<void> = Promise.resolve();
  private closeResolve?: () => void;
  private stopPromise?: Promise<void>;

  constructor(private options: LiveVoiceOptions) {
    this.audio.autoplay = true;
    this.audio.controls = true;
    this.audio.setAttribute('playsinline', '');
  }

  async start(): Promise<void> {
    if (this.peer || this.closing || this.stopPromise) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.isSecureContext) {
      this.options.onState('error', 'Microfonul necesită HTTPS sau localhost.');
      return;
    }
    // Snapshot only allowed IDs before microphone permission/SDP can yield.
    // Do not forward caller-authored facts or let selection changes retarget startup.
    const scope: VoiceStartupScope = {};
    if (
      typeof this.options.scope?.incident_id === 'string' &&
      this.options.scope.incident_id.trim()
    )
      scope.incident_id = this.options.scope.incident_id;
    if (typeof this.options.scope?.group_id === 'string' && this.options.scope.group_id.trim())
      scope.group_id = this.options.scope.group_id;
    const generation = ++this.generation;
    this.finalized = false;
    this.closing = false;
    this.fragments = [];
    this.conversation = [];
    this.retiredThrough = -1;
    this.delegatedThrough = -1;
    this.transcript = { user: '', assistant: '' };
    this.seenDelegations.clear();
    this.seenEvents.clear();
    this.delegationQueue = Promise.resolve();
    const abort = new AbortController();
    this.abort = abort;
    this.options.onState('connecting', 'Conectare la GPT-Live…');
    try {
      const peer = new RTCPeerConnection();
      this.peer = peer;
      peer.addEventListener('track', (event) => {
        if (this.peer !== peer) return;
        this.audio.srcObject = event.streams[0] ?? new MediaStream([event.track]);
        void this.audio.play().catch(() => {
          if (this.peer === peer && generation === this.generation && !this.closing)
            this.options.onPlaybackBlocked?.();
        });
      });
      peer.addEventListener('connectionstatechange', () => {
        if (this.peer === peer && peer.connectionState === 'failed')
          this.fail('Conexiunea vocală a eșuat; consumul final nu este confirmat.');
      });
      const microphone = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      if (generation !== this.generation || this.closing) {
        microphone.getTracks().forEach((track) => track.stop());
        return;
      }
      this.microphone = microphone;
      // Bound microphone capture while HTTP/SDP negotiation is still pending.
      // The provider's server watchdog remains responsible for remote cleanup
      // if a session was created before this client aborts the handshake.
      this.handshakeTimer = setTimeout(() => {
        if (this.isCurrent(generation, peer))
          this.fail(
            'Negocierea vocală a depășit 60 de secunde. Microfonul local a fost oprit; consumul final al furnizorului nu este confirmat.',
          );
      }, 60000);
      microphone.getAudioTracks().forEach((track) => peer.addTrack(track, microphone));
      this.startMeter(microphone);
      const channel = peer.createDataChannel('oai-events');
      this.channel = channel;
      channel.addEventListener('message', ({ data }) => {
        if (this.channel !== channel) return;
        try {
          this.handleEvent(JSON.parse(String(data)) as Record<string, any>);
        } catch {
          this.fail('Eveniment vocal invalid; sesiunea a fost oprită.');
        }
      });
      channel.addEventListener('close', () => {
        if (this.channel === channel && !this.finalized)
          this.fail('Vocea s-a deconectat fără confirmarea consumului final.');
      });
      const offer = await peer.createOffer();
      if (!this.isCurrent(generation, peer)) return;
      await peer.setLocalDescription(offer);
      if (!this.isCurrent(generation, peer)) return;
      await this.gatherIce(peer, abort.signal);
      if (!this.isCurrent(generation, peer)) return;
      const sdp = peer.localDescription?.sdp;
      if (!sdp) throw new Error('Lipsește oferta WebRTC.');
      const response = await fetch(this.options.sessionUrl ?? '/api/voice/session', {
        method: 'POST',
        credentials: 'same-origin',
        signal: abort.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sdp, ...(Object.keys(scope).length ? { scope } : {}) }),
      });
      if (!this.isCurrent(generation, peer)) return;
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        if (!this.isCurrent(generation, peer)) return;
        throw new Error(
          typeof body.detail === 'string'
            ? body.detail
            : `GPT-Live indisponibil (HTTP ${response.status}).`,
        );
      }
      const result = (await response.json()) as {
        session_id: string;
        sdp: string;
        max_seconds?: number;
      };
      if (!this.isCurrent(generation, peer)) return;
      if (!result.session_id || !result.sdp?.startsWith('v=0'))
        throw new Error('Răspuns WebRTC invalid.');
      this.sessionId = result.session_id;
      await peer.setRemoteDescription({ type: 'answer', sdp: result.sdp });
      if (!this.isCurrent(generation, peer)) return;
      clearTimeout(this.handshakeTimer);
      // HTTP already started the session. Never send session.start here.
      this.durationTimer = setTimeout(
        () => {
          void this.stop();
        },
        Math.min(600, Math.max(30, result.max_seconds ?? 180)) * 1000,
      );
      this.startupTimer = setTimeout(() => {
        if (!this.ready) this.fail('GPT-Live nu a confirmat pornirea sesiunii.');
      }, 20000);
    } catch (error) {
      if (generation === this.generation)
        this.fail(error instanceof Error ? error.message : 'Conectarea vocală a eșuat.');
    }
  }

  private isCurrent(generation: number, peer: RTCPeerConnection): boolean {
    return generation === this.generation && this.peer === peer && !this.closing;
  }

  setMuted(muted: boolean): void {
    this.microphone?.getAudioTracks().forEach((track) => {
      track.enabled = !muted;
    });
  }

  private startMeter(stream: MediaStream): void {
    if (!this.options.onAudioLevel) return;
    const context = new AudioContext();
    this.audioContext = context;
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    context.createMediaStreamSource(stream).connect(analyser);
    const samples = new Uint8Array(analyser.fftSize);
    const update = () => {
      if (this.audioContext !== context) return;
      analyser.getByteTimeDomainData(samples);
      const energy =
        samples.reduce((sum, sample) => sum + ((sample - 128) / 128) ** 2, 0) / samples.length;
      this.options.onAudioLevel?.(Math.min(1, Math.sqrt(energy) * 5));
      this.meterFrame = requestAnimationFrame(update);
    };
    void context.resume().catch(() => undefined);
    update();
  }

  private gatherIce(peer: RTCPeerConnection, signal: AbortSignal): Promise<void> {
    if (peer.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        clearTimeout(timer);
        peer.removeEventListener('icegatheringstatechange', changed);
        signal.removeEventListener('abort', cancelled);
      };
      const cancelled = () => {
        cleanup();
        reject(new DOMException('Voice startup was cancelled.', 'AbortError'));
      };
      const changed = () => {
        if (peer.iceGatheringState === 'complete') {
          cleanup();
          resolve();
        }
      };
      const timer = setTimeout(() => {
        cleanup();
        reject(new Error('Colectarea ICE a expirat.'));
      }, 10000);
      peer.addEventListener('icegatheringstatechange', changed);
      signal.addEventListener('abort', cancelled, { once: true });
      if (signal.aborted) {
        cancelled();
        return;
      }
      changed();
    });
  }

  private handleEvent(event: Record<string, any>): void {
    if (typeof event.type !== 'string') throw new Error('missing type');
    if (typeof event.event_id === 'string') {
      if (this.seenEvents.has(event.event_id)) return;
      this.seenEvents.add(event.event_id);
    }
    this.options.onEvent?.(event);
    if (event.type === 'session.started') {
      if (this.closing) return;
      this.ready = true;
      clearTimeout(this.startupTimer);
      this.options.onState(
        'listening',
        'GPT-Live conectat · cererile sunt verificate de aplicație.',
      );
    } else if (event.type === 'session.closed') {
      this.finalized = true;
      this.cleanup();
      this.options.onState('idle', 'Sesiune vocală încheiată; consum final primit.');
    } else if (event.type === 'error' || event.type === 'session.error') {
      this.fail(
        typeof event.error?.message === 'string'
          ? event.error.message
          : 'GPT-Live a raportat o eroare.',
      );
    } else if (
      event.type === 'session.input_transcript.delta' ||
      event.type === 'session.output_transcript.delta'
    ) {
      const role = event.type === 'session.input_transcript.delta' ? 'user' : 'assistant';
      if (typeof event.delta !== 'string') return;
      this.transcript[role] = (this.transcript[role] + event.delta).slice(-6000);
      this.options.onTranscript(this.transcript[role], role);
      if (
        Number.isFinite(event.start_ms) &&
        Number.isFinite(event.end_ms) &&
        event.start_ms >= 0 &&
        event.end_ms >= event.start_ms
      ) {
        // The interval is half-open: speech exactly at the retired boundary
        // belongs to the next turn. Earlier late speech is neither a command
        // nor newly admitted reference context.
        if (role === 'user' && event.start_ms < this.retiredThrough) return;
        const fragment = {
          text: event.delta,
          start: event.start_ms,
          end: event.end_ms,
          receivedAt: Date.now(),
        };
        this.conversation.push({ ...fragment, role });
        this.conversation = this.conversation.slice(-300);
        if (role === 'user') {
          this.fragments.push(fragment);
          this.fragments = this.fragments.slice(-200);
        }
      }
    } else if (
      event.type === 'session.delegation.created' &&
      event.delegation?.target === 'client'
    ) {
      const id = event.delegation.id;
      if (typeof id !== 'string' || !id || this.seenDelegations.has(id) || this.closing) return;
      this.seenDelegations.add(id);
      // Reserve the window immediately, before earlier queued work finishes.
      // Retiring it does not depend on receiving text or successfully executing
      // a command: late words from a timed-out turn must never be replayed.
      if (
        !Number.isSafeInteger(event.offset_ms) ||
        event.offset_ms < 0 ||
        event.offset_ms <= this.delegatedThrough
      ) {
        this.send({
          type: 'session.commentary.append',
          event_id: crypto.randomUUID(),
          delegation_id: id,
          content:
            'Nu pot delimita sigur această cerere vocală. Te rog repetă explicit comanda; nu am trimis-o aplicației.',
        });
        return;
      }
      const window = { start: this.delegatedThrough, end: event.offset_ms };
      this.delegatedThrough = window.end;
      const generation = this.generation;
      this.delegationQueue = this.delegationQueue
        .then(() => this.delegate(id, window, generation))
        .catch(() => {
          if (generation === this.generation)
            this.fail('Procesarea comenzii vocale a eșuat; sesiunea a fost oprită.');
        });
    }
  }

  private async waitForTranscript(
    window: DelegationWindow,
    generation: number,
  ): Promise<Fragment[]> {
    const deadline = Date.now() + 2000;
    // GPT-Live has no transcript item ID or turn-complete event. Timeline
    // windows plus a bounded settling period are a grouping heuristic, not
    // proof an utterance is complete. Ambiguous requests still need clarification.
    // https://developers.openai.com/api/docs/guides/live-conversations
    while (generation === this.generation && !this.closing) {
      const now = Date.now();
      const relevant = this.fragments.filter(
        (part) => part.start >= window.start && part.start < window.end,
      );
      const lastArrival = Math.max(...relevant.map((part) => part.receivedAt));
      if (relevant.length && now - lastArrival >= 250) return relevant;
      if (now >= deadline) return [];
      await new Promise((resolve) => setTimeout(resolve, Math.min(50, deadline - now)));
    }
    return [];
  }

  private async delegate(id: string, window: DelegationWindow, generation: number): Promise<void> {
    if (generation !== this.generation || this.closing) return;
    this.send({
      type: 'session.thinking.append',
      event_id: crypto.randomUUID(),
      delegation_id: id,
      content: 'Cererea este verificată de aplicație. Încă nu există un rezultat confirmat.',
    });
    // A delegation may arrive before its transcript. Wait for that turn only,
    // never borrow later speech to complete an older operational command.
    const relevant = await this.waitForTranscript(window, generation);
    if (generation !== this.generation || this.closing) return;
    const text = relevant
      .map((part) => part.text)
      .join('')
      .trim();
    const earlier: Array<{ role: 'user' | 'assistant'; text: string }> = [];
    for (const part of this.conversation) {
      if (part.start >= window.end || (part.role === 'user' && part.start >= window.start))
        continue;
      const last = earlier.at(-1);
      if (last?.role === part.role) last.text += part.text;
      else earlier.push({ role: part.role, text: part.text });
    }
    // Assistant speech can clarify a reference, but is not proof of execution.
    // App separately adds authoritative backend request/result history.
    const context = earlier
      .map((part) => `${part.role}: ${part.text}`)
      .join('\n')
      .slice(-2000);
    this.retiredThrough = Math.max(this.retiredThrough, window.end);
    if (!text) {
      this.send({
        type: 'session.commentary.append',
        event_id: crypto.randomUUID(),
        delegation_id: id,
        content:
          'Nu am primit o transcriere completă pentru această cerere. Te rog repetă explicit comanda; nu am trimis-o aplicației.',
      });
      return;
    }
    this.options.onState('working', 'Interpretez cererea și verific datele de planificare…');
    try {
      const result = await this.options.onDelegation({
        text,
        delegationId: `${this.sessionId}:${id}`,
        context,
      });
      if (generation !== this.generation || this.closing) return;
      this.send({
        type: 'session.commentary.append',
        event_id: crypto.randomUUID(),
        delegation_id: id,
        content: typeof result === 'string' ? result : voiceCommandAcknowledgement(result),
      });
      this.options.onState('listening', 'Rezultatul verificat a revenit în conversație.');
    } catch (error) {
      if (generation !== this.generation || this.closing) return;
      const message = error instanceof Error ? error.message : 'Cererea a eșuat.';
      this.send({
        type: 'session.commentary.append',
        event_id: crypto.randomUUID(),
        delegation_id: id,
        content:
          error instanceof TransportError
            ? 'Rezultatul cererii este neconfirmat; raportul sau căutarea pot fi deja înregistrate. Folosește Recover request cu același ID. Nu retrimite automat comanda și nu afirma succesul.'
            : `Nu afirma că planul a fost aplicat. Aplicația a raportat o eroare: ${message.slice(0, 700)}. Operatorul poate verifica starea curentă.`,
      });
      // A failed planning command is recoverable. Keep the same session and
      // microphone under UI control; the application already displays its error.
      this.options.onState('listening', '');
    }
  }

  private send(event: Record<string, unknown>): void {
    if (this.ready && this.channel?.readyState === 'open') {
      const bounded =
        (event.type === 'session.commentary.append' || event.type === 'session.thinking.append') &&
        typeof event.content === 'string'
          ? { ...event, content: boundedContext(event.content) }
          : event;
      this.channel.send(JSON.stringify(bounded));
    }
  }

  stop(): Promise<void> {
    if (this.stopPromise) return this.stopPromise;
    if (!this.peer) return Promise.resolve();
    this.closing = true;
    clearTimeout(this.durationTimer);
    this.setMuted(true);
    this.options.onState('closing', 'Închid sesiunea și aștept consumul final…');
    if (!this.ready || this.channel?.readyState !== 'open') {
      this.cleanup();
      this.options.onState(
        'idle',
        'Conexiune închisă; finalizarea furnizorului nu este confirmată.',
      );
      return Promise.resolve();
    }
    const stopped = new Promise<void>((resolve) => {
      this.closeResolve = resolve;
    });
    this.stopPromise = stopped;
    // Set the promise before sending: a duplicate stop or even a synchronous
    // session.closed event must observe the same in-flight finalization.
    try {
      this.closeTimer = setTimeout(() => {
        this.cleanup();
        this.options.onState(
          'error',
          'Lipsește session.closed; consumul final al furnizorului nu este confirmat.',
        );
      }, 15000);
      this.send({ type: 'session.close' });
    } catch {
      this.cleanup();
      this.options.onState(
        'error',
        'Nu am putut confirma închiderea furnizorului. Microfonul local a fost oprit.',
      );
    }
    return stopped;
  }

  /** Call on unmount; graceful close continues asynchronously for at most 15s. */
  dispose(): void {
    void this.stop();
  }

  private fail(message: string): void {
    try {
      if (this.ready && this.channel?.readyState === 'open') this.send({ type: 'session.close' });
    } catch {
      // A broken transport cannot acknowledge closure; local capture must stop.
    } finally {
      this.cleanup();
    }
    this.options.onState('error', message);
  }

  private cleanup(): void {
    ++this.generation;
    clearTimeout(this.durationTimer);
    clearTimeout(this.startupTimer);
    clearTimeout(this.handshakeTimer);
    clearTimeout(this.closeTimer);
    this.abort?.abort();
    this.microphone?.getTracks().forEach((track) => track.stop());
    this.microphone = undefined;
    if (this.meterFrame !== undefined) cancelAnimationFrame(this.meterFrame);
    const context = this.audioContext;
    this.audioContext = undefined;
    if (context) void context.close().catch(() => undefined);
    this.options.onAudioLevel?.(0);
    const channel = this.channel;
    this.channel = undefined;
    channel?.close();
    const peer = this.peer;
    this.peer = undefined;
    peer?.close();
    this.audio.srcObject = null;
    this.audio.pause();
    this.ready = false;
    this.closing = false;
    this.sessionId = undefined;
    this.closeResolve?.();
    this.closeResolve = undefined;
    this.stopPromise = undefined;
  }
}
