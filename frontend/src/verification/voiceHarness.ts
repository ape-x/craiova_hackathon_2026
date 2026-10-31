/** Dev-only verification entry. Never imported by the application entry point. */
import { request, ApiError } from '../api';
import { commandContext, verifiedCommandResult } from '../commandContext';
import { LiveVoiceClient } from '../liveVoice';
import type { AppConfig, MutationResult, World } from '../types';
import {
  assertIsolatedTarget,
  HARNESS_ORIGIN,
  installSyntheticInput,
  SingleCommandGate,
  summarizeResult,
} from './voiceHarnessSupport';

const element = <T extends HTMLElement>(id: string) => {
  const value = document.getElementById(id);
  if (!value) throw new Error('Verification page is incomplete.');
  return value as T;
};
const start = element<HTMLButtonElement>('start');
const stop = element<HTMLButtonElement>('stop');
const play = element<HTMLButtonElement>('play');
const download = element<HTMLButtonElement>('download');
const preflight = element<HTMLButtonElement>('preflight');
const confirm = element<HTMLInputElement>('confirm');
const status = element('status');
const evidence = element('evidence');
let ready = false;
let running = false;
let attempts = 0;
let client: LiveVoiceClient | undefined;
let closeRun: (() => Promise<void>) | undefined;
let displayedReport: Record<string, unknown> = {};
const outputURLs: string[] = [];

function render() {
  evidence.textContent = JSON.stringify(displayedReport, null, 2);
  start.disabled = !ready || !confirm.checked || running || attempts >= 2;
  stop.disabled = !running;
  preflight.disabled = running;
}

async function checkTarget() {
  if (location.origin !== HARNESS_ORIGIN)
    throw new Error('Use the dedicated loopback verification origin.');
  const [config, world] = await Promise.all([
    request<AppConfig>('/config'),
    request<World>('/world'),
  ]);
  assertIsolatedTarget(config, world);
  return world;
}

confirm.addEventListener('change', render);
preflight.addEventListener('click', () => {
  ready = false;
  render();
  void checkTarget()
    .then((world) => {
      ready = true;
      status.textContent = `Isolated target checked: ${world.groups.length} synthetic groups; budget €${world.budget}.`;
      displayedReport = {
        state: 'prepared_not_run',
        provider_calls_made: false,
        synthetic_group_count: world.groups.length,
      };
    })
    .catch(() => {
      status.textContent =
        'Preflight failed. Verify the isolated API, exact model configuration and fresh voice-check scenario.';
      displayedReport = { state: 'preflight_failed', provider_calls_made: false };
    })
    .finally(render);
});

start.addEventListener('click', () => {
  void run();
});
stop.addEventListener('click', () => {
  void closeRun?.();
});
play.addEventListener('click', () => {
  void client?.audio
    .play()
    .then(() => {
      play.disabled = true;
    })
    .catch(() => {
      status.textContent = 'Playback is still blocked. Check browser sound permissions.';
    });
});
download.addEventListener('click', () => {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(displayedReport, null, 2)], { type: 'application/json' }),
  );
  const link = document.createElement('a');
  link.href = url;
  link.download = 'voice-spoken-check.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function run() {
  if (running || !ready || !confirm.checked || attempts >= 2) return;
  if (typeof AudioContext === 'undefined' || !navigator.mediaDevices) {
    status.textContent = 'Secure browser audio APIs are unavailable; no provider call made.';
    return;
  }
  running = true;
  client = undefined;
  ready = false;
  attempts += 1;
  download.disabled = true;
  play.disabled = true;
  element('user-transcript').textContent = 'Input: —';
  element('assistant-transcript').textContent = 'Assistant: —';
  const gate = new SingleCommandGate();
  const began = performance.now();
  const elapsed = () => Math.round(performance.now() - began);
  const timeline: Array<Record<string, unknown>> = [];
  const counts: Record<string, number> = {};
  // A pending backend completion belongs to this run even if the operator
  // later prepares another one. Never braid evidence across sessions.
  const report: Record<string, unknown> = {
    state: 'preparing',
    started_at: new Date().toISOString(),
    attempt: attempts,
    input: 'macOS Ioana synthetic Romanian WAV, mono 24000 Hz, 5.534708 seconds',
    microphone_accessed: false,
    ambient_audio_recorded: false,
    requested_models: {
      voice: 'gpt-live-1',
      interpretation: 'gpt-6.1-sol',
      service_tier: 'ultrafast',
      decision: 'gpt-6-luna',
    },
    max_backend_commands: 1,
    client_close_deadline_seconds: 35,
    live_session_started: false,
    finalization_confirmed: false,
    command_attempts: 0,
    timeline,
    event_counts: counts,
    limitations: [
      'Synthetic speech does not verify a physical microphone or room acoustics.',
      'Playback activity and transcript require semantic review; an acknowledgement alone is not proof of execution.',
    ],
  };
  displayedReport = report;
  render();
  const context = new AudioContext();
  const destination = context.createMediaStreamDestination();
  const mediaDevices = navigator.mediaDevices;
  let restoreInput: (() => void) | undefined;
  let source: AudioBufferSourceNode | undefined;
  let inputPlayed = false;
  let disposed = false;
  let closed = false;
  let sessionTimer: ReturnType<typeof setTimeout> | undefined;
  let absoluteTimer: ReturnType<typeof setTimeout> | undefined;
  let speechTimer: ReturnType<typeof setTimeout> | undefined;
  let frame: number | undefined;
  let outputSource: MediaStreamAudioSourceNode | undefined;
  let outputRecorder: MediaRecorder | undefined;
  let resultAt: number | undefined;
  let outputAfterResult = '';
  let maxOutputRmsAfterResult = 0;
  const audioChunks: Blob[] = [];

  const mark = (event: string) => {
    timeline.push({ event, elapsed_ms: elapsed() });
    render();
  };
  const releaseLocal = () => {
    if (disposed) return;
    disposed = true;
    clearTimeout(sessionTimer);
    clearTimeout(absoluteTimer);
    clearTimeout(speechTimer);
    if (frame !== undefined) cancelAnimationFrame(frame);
    source?.disconnect();
    try {
      source?.stop();
    } catch {
      /* Already ended or never started. */
    }
    destination.stream.getTracks().forEach((track) => track.stop());
    outputSource?.disconnect();
    if (outputRecorder?.state === 'recording') outputRecorder.stop();
    restoreInput?.();
    void context.close().catch(() => undefined);
    running = false;
    closeRun = undefined;
    download.disabled = false;
    report.elapsed_ms = elapsed();
    report.output_transcript_after_backend_result = outputAfterResult;
    report.max_remote_audio_rms_after_backend_result = maxOutputRmsAfterResult;
    report.state = report.finalization_confirmed
      ? 'closed_review_evidence'
      : 'ended_finalization_unconfirmed';
    render();
  };
  const close = async () => {
    if (closed) return;
    closed = true;
    mark('close_requested');
    await client?.stop();
    releaseLocal();
  };
  closeRun = close;

  try {
    // The context resumes inside this actual button gesture; no microphone
    // permission is requested anywhere in the harness.
    await context.resume();
    const before = await checkTarget();
    const response = await fetch('/verification/budget-command.wav');
    if (!response.ok) throw new Error('Synthetic speech asset unavailable.');
    const buffer = await context.decodeAudioData(await response.arrayBuffer());
    if (disposed || closed) return;
    if (buffer.duration < 1 || buffer.duration > 10)
      throw new Error('Invalid test audio duration.');
    report.audio_duration_seconds = buffer.duration;
    source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(destination);
    source.addEventListener('ended', () => mark('synthetic_speech_ended'), { once: true });
    if (!mediaDevices) throw new Error('Secure browser media APIs unavailable.');
    restoreInput = installSyntheticInput(mediaDevices, destination.stream, () =>
      mark('synthetic_media_track_acquired'),
    );
    client = new LiveVoiceClient({
      onState: (state, message) => {
        report.client_state = state;
        status.textContent =
          state === 'error'
            ? 'Voice check failed. See measured evidence; no automatic retry.'
            : message;
        if (state === 'listening' && !inputPlayed && !closed) {
          inputPlayed = true;
          speechTimer = setTimeout(() => {
            if (closed || disposed) return;
            source?.start();
            mark('synthetic_speech_started');
          }, 500);
        }
        if (state === 'error' || state === 'idle') releaseLocal();
        render();
      },
      onPlaybackBlocked: () => {
        play.disabled = false;
        report.playback_blocked = true;
        render();
      },
      onTranscript: (text, role) => {
        element(`${role === 'user' ? 'user' : 'assistant'}-transcript`).textContent =
          `${role}: ${text}`;
        report[`${role}_transcript`] = text;
        render();
      },
      onEvent: (event) => {
        const type = typeof event.type === 'string' ? event.type : 'unknown';
        counts[type] = (counts[type] ?? 0) + 1;
        if (type === 'session.started') {
          report.live_session_started = true;
          mark('session.started');
          sessionTimer = setTimeout(() => {
            void close();
          }, 30000);
        } else if (type === 'session.delegation.created') {
          mark('session.delegation.created');
        } else if (
          type === 'session.output_transcript.delta' &&
          resultAt !== undefined &&
          typeof event.delta === 'string'
        ) {
          outputAfterResult += event.delta;
        } else if (type === 'session.closed') {
          report.finalization_confirmed = true;
          const usage = event.usage as Record<string, unknown> | undefined;
          if (typeof usage?.seconds === 'number') report.final_usage_seconds = usage.seconds;
          if (typeof event.reason === 'string') report.close_reason = event.reason;
          mark('session.closed');
        }
      },
      onDelegation: async ({ text, delegationId, context: voiceContext }) => {
        gate.take();
        report.command_attempts = 1;
        report.delegated_text = text;
        mark('backend_command_requested');
        try {
          const result = await request<MutationResult>('/commands', {
            text,
            context: commandContext([], null, voiceContext),
            expected_version: before.version,
            idempotency_key: `voice-check-${delegationId}`,
          });
          report.backend = summarizeResult(result, before);
          resultAt = performance.now();
          report.verified_result_returned_to_voice_client = verifiedCommandResult(result);
          mark('backend_result_received');
          return verifiedCommandResult(result);
        } catch (error) {
          report.backend_error =
            error instanceof ApiError
              ? { http_status: error.status }
              : { status: 'unconfirmed_transport_or_client_error' };
          mark('backend_command_failed_or_unconfirmed');
          throw new Error('Verificarea nu confirmă executarea comenzii; nu anunța succesul.');
        }
      },
    });
    const outputAudio = client.audio;
    outputAudio.setAttribute('aria-label', 'Actual GPT-Live remote audio');
    element('output-audio').replaceChildren(outputAudio);
    outputAudio.addEventListener(
      'loadedmetadata',
      () => {
        if (disposed || outputSource || !(outputAudio.srcObject instanceof MediaStream)) return;
        const stream = outputAudio.srcObject;
        outputSource = context.createMediaStreamSource(stream);
        const analyser = context.createAnalyser();
        analyser.fftSize = 512;
        const silent = context.createGain();
        silent.gain.value = 0;
        outputSource.connect(analyser).connect(silent).connect(context.destination);
        const samples = new Float32Array(analyser.fftSize);
        const meter = () => {
          if (disposed) return;
          analyser.getFloatTimeDomainData(samples);
          if (resultAt !== undefined) {
            const rms = Math.sqrt(
              samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length,
            );
            maxOutputRmsAfterResult = Math.max(maxOutputRmsAfterResult, rms);
          }
          frame = requestAnimationFrame(meter);
        };
        meter();
        // This recorder receives only the provider's remote track, never input.
        if (typeof MediaRecorder !== 'undefined') {
          try {
            outputRecorder = new MediaRecorder(stream);
            outputRecorder.addEventListener('dataavailable', ({ data }) => {
              if (data.size) audioChunks.push(data);
            });
            outputRecorder.addEventListener('stop', () => {
              if (!audioChunks.length) return;
              const url = URL.createObjectURL(
                new Blob(audioChunks, { type: outputRecorder?.mimeType }),
              );
              outputURLs.push(url);
              const recording = document.createElement('audio');
              recording.controls = true;
              recording.src = url;
              recording.setAttribute('aria-label', 'Recorded provider output for review');
              if (displayedReport === report) element('output-audio').append(recording);
              report.remote_audio_recording_bytes = audioChunks.reduce(
                (sum, chunk) => sum + chunk.size,
                0,
              );
              render();
            });
            outputRecorder.start();
          } catch {
            report.remote_recording_available = false;
          }
        }
      },
      { once: true },
    );
    report.state = 'connecting';
    mark('session_start_requested');
    // Covers slow startup as well as an active session. The isolated server
    // must also be configured with VOICE_MAX_SECONDS=35 (see README).
    absoluteTimer = setTimeout(() => {
      void close();
    }, 35000);
    await client.start();
  } catch {
    status.textContent =
      'Preparation or startup failed. No automatic retries; inspect the isolated setup.';
    report.preparation_or_startup_failed = true;
    await close();
  }
}

window.addEventListener('pagehide', () => {
  void closeRun?.();
  outputURLs.forEach((url) => URL.revokeObjectURL(url));
});
render();
