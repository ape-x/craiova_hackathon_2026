import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { LiveVoiceClient } from './liveVoice';
import type { LiveVoiceOptions, VoiceState } from './liveVoice';

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
  const onDelegation = vi.fn(async () => 'Nu am aplicat nicio modificare.');
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
function command(channel: FakeChannel, id = 'delegation-1') {
  channel.emit({
    type: 'session.input_transcript.delta',
    event_id: 'transcript-1',
    delta: 'Arată autocarul B12.',
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
    ).toContain('nu am modificat simulatorul');
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
