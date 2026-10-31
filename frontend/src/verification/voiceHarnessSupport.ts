import type { AppConfig, MutationResult, World } from '../types';

export const EXPECTED_BUDGET = 20000;
export const HARNESS_ORIGIN = 'http://127.0.0.1:5177';

/** A mistaken proxy must fail before a paid session or simulator mutation. */
export function assertIsolatedTarget(config: AppConfig, world: World): void {
  if (
    !['test', 'development'].includes(config.environment ?? '') ||
    config.public_base_url !== HARNESS_ORIGIN ||
    !world.scenario_id.startsWith('voice-check-') ||
    world.mode !== 'simulation' ||
    world.groups.length > 20 ||
    world.groups.length === 0 ||
    world.observations.length > 0 ||
    world.offers.length > 0 ||
    world.reservations.length > 0 ||
    world.decisions.length > 0 ||
    world.budget === EXPECTED_BUDGET ||
    config.traffic_running === true
  )
    throw new Error('Target is not a fresh, isolated voice-check scenario. No session started.');
  const models = config.models as Record<string, unknown> | undefined;
  if (
    !config.ai_configured ||
    !config.voice_configured ||
    models?.reasoning !== 'gpt-6.1-sol' ||
    models?.decision !== 'gpt-6-luna' ||
    models?.voice !== 'gpt-live-1'
  )
    throw new Error('The exact requested OpenAI providers are not configured.');
  if (config.auth_required && !config.authenticated)
    throw new Error('The isolated operator is not authenticated.');
}

/** One paid command per run, including failed/unconfirmed attempts. */
export class SingleCommandGate {
  private used = false;

  take(): void {
    if (this.used) throw new Error('This bounded check already used its single command.');
    this.used = true;
  }
}

/** Local harness override only. The real capture function is never invoked. */
export function installSyntheticInput(
  mediaDevices: MediaDevices,
  stream: MediaStream,
  onAcquire: () => void,
): () => void {
  const descriptor = Object.getOwnPropertyDescriptor(mediaDevices, 'getUserMedia');
  let used = false;
  let restored = false;
  Object.defineProperty(mediaDevices, 'getUserMedia', {
    configurable: true,
    value: async () => {
      if (used || restored) throw new Error('Synthetic input can only be acquired once.');
      used = true;
      onAcquire();
      return stream;
    },
  });
  return () => {
    if (restored) return;
    restored = true;
    if (descriptor) Object.defineProperty(mediaDevices, 'getUserMedia', descriptor);
    else Reflect.deleteProperty(mediaDevices, 'getUserMedia');
  };
}

/** Whitelist only synthetic operational evidence; never export bearer links. */
export function summarizeResult(result: MutationResult, before: World) {
  const decision = result.decision;
  const selected = decision?.candidates.find((plan) => plan.id === decision.selected_plan_id);
  return {
    scenario_matches: result.world.scenario_id === before.scenario_id,
    before_version: before.version,
    after_version: result.world.version,
    before_budget: before.budget,
    after_budget: result.world.budget,
    expected_budget_applied: result.world.budget === EXPECTED_BUDGET,
    applied: result.applied !== false && result.world.version > before.version,
    new_ai_decision: Boolean(
      decision &&
      decision.mode === 'ai' &&
      !before.decisions.some((old) => old.id === decision.id) &&
      decision.source_version === before.version &&
      decision.applied_version === result.world.version,
    ),
    provider: decision?.provider ?? null,
    timings_ms: decision?.timings_ms ?? {},
    selected_candidate_valid: selected?.validation.valid === true,
    candidate_count: decision?.candidates.length ?? 0,
    recovered_passengers: selected?.recovered_passengers ?? null,
    explanation: result.explanation ?? decision?.explanation ?? '',
  };
}
