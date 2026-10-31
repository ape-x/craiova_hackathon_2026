/** GPT-Live WebRTC, not the older Realtime protocol.
 * Official contract: https://developers.openai.com/api/docs/guides/voice-webrtc
 * No project key or provider configuration reaches the browser.
 */
export type VoiceState = 'idle' | 'connecting' | 'listening' | 'working' | 'closing' | 'error';
export type DelegationRequest = { text: string; delegationId: string; context: string };
export interface LiveVoiceOptions {
  sessionUrl?: string;
  onState: (state: VoiceState, message: string) => void;
  onTranscript: (text: string, role: 'user' | 'assistant') => void;
  onAudioLevel?: (level: number) => void;
  /** Render a user-gesture Play control calling client.audio.play(). */
  onPlaybackBlocked?: () => void;
  /** Must execute through the versioned backend, then return verified facts. */
  onDelegation: (request: DelegationRequest) => Promise<string>;
  onEvent?: (event: Record<string, unknown>) => void;
}

type Fragment = { text: string; start: number; end: number; receivedAt: number };
type ConversationFragment = Fragment & { role: 'user' | 'assistant' };

/** Context appends allow 500 tokens. A smaller UTF-8 byte budget is a
 * conservative bound without shipping a tokenizer or guessing chars/token.
 * Iterate code points so truncation never splits a surrogate pair.
 */
function boundedContext(content: string): string {
  const encoder = new TextEncoder();
  if (encoder.encode(content).byteLength <= 480) return content;
  let result = '',
    bytes = 0;
  for (const character of content) {
    const length = encoder.encode(character).byteLength;
    if (bytes + length > 477) break;
    bytes += length;
    result += character;
  }
  return result.trimEnd() + '…';
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
  private consumedUntil = -1;
  private delegatedUntil = -1;
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
    const generation = ++this.generation;
    this.finalized = false;
    this.closing = false;
    this.fragments = [];
    this.conversation = [];
    this.consumedUntil = -1;
    this.delegatedUntil = -1;
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
        body: JSON.stringify({ sdp }),
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
      this.options.onState('listening', 'GPT-Live conectat · cererile modifică numai simulatorul.');
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
        // A retired turn must not leak a late transcription into a newer task.
        if (role === 'user' && event.start_ms <= this.consumedUntil) return;
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
      if (typeof id !== 'string' || this.seenDelegations.has(id) || this.closing) return;
      this.seenDelegations.add(id);
      const offset = event.offset_ms;
      if (!Number.isFinite(offset) || offset < 0 || offset <= this.delegatedUntil) {
        this.send({
          type: 'session.commentary.append',
          event_id: crypto.randomUUID(),
          delegation_id: id,
          content:
            'Această cerere nu are un interval vocal valid. Repetă comanda explicit; nu am modificat simulatorul pentru ea.',
        });
        return;
      }
      // Reserve the interval now, not when the preceding backend call finishes.
      const after = this.delegatedUntil;
      this.delegatedUntil = offset;
      const generation = this.generation;
      this.delegationQueue = this.delegationQueue
        .then(() => this.delegate(id, after, offset, generation))
        .catch(() => {
          if (generation === this.generation)
            this.fail('Procesarea comenzii vocale a eșuat; sesiunea a fost oprită.');
        });
    }
  }

  private async delegate(
    id: string,
    after: number,
    offset: number,
    generation: number,
  ): Promise<void> {
    if (generation !== this.generation || this.closing) return;
    this.send({
      type: 'session.thinking.append',
      event_id: crypto.randomUUID(),
      delegation_id: id,
      content: 'Cererea este verificată în simulator. Încă nu există un rezultat confirmat.',
    });
    // Delegation and transcript delivery may arrive out of order. Allow up to
    // two seconds for this interval, then require a short quiet period before
    // sending its accumulated text once. A transcript delta is not a user turn.
    let relevant: Fragment[] = [];
    let settled = false;
    for (let elapsed = 0; elapsed < 2000; elapsed += 250) {
      await new Promise((resolve) => setTimeout(resolve, 250));
      if (generation !== this.generation || this.closing) return;
      relevant = this.fragments.filter((part) => part.start > after && part.start <= offset);
      if (
        relevant.length &&
        Date.now() - Math.max(...relevant.map((part) => part.receivedAt)) >= 250
      ) {
        settled = true;
        break;
      }
    }
    const text = relevant
      .map((part) => part.text)
      .join('')
      .trim();
    const earlier: Array<{ role: 'user' | 'assistant'; text: string }> = [];
    for (const part of this.conversation) {
      if (part.start > offset || (part.role === 'user' && part.start > after)) continue;
      const last = earlier.at(-1);
      if (last?.role === part.role) last.text += part.text;
      else earlier.push({ role: part.role, text: part.text });
    }
    // Spoken assistant text may contain a clarification, but is never proof of
    // execution. App adds the authoritative backend request/result history.
    const context = earlier
      .map((part) => `${part.role}: ${part.text}`)
      .join('\n')
      .slice(-2000);
    this.consumedUntil = offset;
    if (!text || !settled) {
      this.send({
        type: 'session.commentary.append',
        event_id: crypto.randomUUID(),
        delegation_id: id,
        content:
          'Nu am primit o transcriere completă pentru această cerere. Te rog repetă explicit comanda; nu am modificat simulatorul.',
      });
      return;
    }
    this.options.onState('working', 'Interpretez cererea și verific starea simulatorului…');
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
        content: result.slice(0, 1200),
      });
      this.options.onState('listening', 'Rezultatul verificat a revenit în conversație.');
    } catch (error) {
      if (generation !== this.generation || this.closing) return;
      const message = error instanceof Error ? error.message : 'Cererea a eșuat.';
      this.send({
        type: 'session.commentary.append',
        event_id: crypto.randomUUID(),
        delegation_id: id,
        content: `Aplicația a raportat o eroare: ${message.slice(0, 700)}. Nu afirma că planul a fost aplicat. Operatorul poate verifica starea curentă.`,
      });
      // A failed simulator command is recoverable. Keep the same session and
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
