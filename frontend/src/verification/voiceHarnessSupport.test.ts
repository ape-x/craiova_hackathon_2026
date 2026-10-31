import { describe, expect, it, vi } from 'vitest';
import type { AppConfig, Decision, MutationResult, World } from '../types';
import {
  assertIsolatedTarget,
  HARNESS_ORIGIN,
  installSyntheticInput,
  SingleCommandGate,
  summarizeResult,
} from './voiceHarnessSupport';

function fixture(): { config: AppConfig; world: World } {
  return {
    config: {
      environment: 'test',
      public_base_url: HARNESS_ORIGIN,
      ai_configured: true,
      voice_configured: true,
      traffic_running: false,
      models: { reasoning: 'gpt-6.1-sol', decision: 'gpt-6-luna', voice: 'gpt-live-1' },
    },
    world: {
      scenario_id: 'voice-check-fixture',
      version: 1,
      epoch: '2026-10-31T00:00:00+02:00',
      sim_time: 600,
      mode: 'simulation',
      currency: 'EUR',
      budget: 45000,
      airports: [],
      resources: [],
      offers: [],
      reservations: [],
      events: [],
      decisions: [],
      observations: [],
      stats: {},
      provider_status: {},
      groups: [
        {
          id: 'G001',
          name: 'Synthetic test group',
          members: ['Synthetic passenger'],
          size: 1,
          origin: 'CRA',
          destination: 'LTN',
          deadline: 2220,
          accessible: false,
          budget: 340,
          original_flight_id: 'F101',
          status: 'waiting',
          token: 'private-passenger-token-not-for-export',
          priority: 1,
        },
      ],
    },
  };
}

describe('isolated spoken harness boundary', () => {
  it('accepts only the fresh isolated simulator and exact configured models', () => {
    const { config, world } = fixture();
    expect(() => assertIsolatedTarget(config, world)).not.toThrow();
    expect(() =>
      assertIsolatedTarget(
        { ...config, models: { ...(config.models as object), voice: 'substitute-model' } },
        world,
      ),
    ).toThrow(/exact requested/);
  });

  it.each([
    [
      'production environment',
      (config: AppConfig, _world: World) => {
        config.environment = 'production';
      },
    ],
    [
      'normal app origin',
      (config: AppConfig, _world: World) => {
        config.public_base_url = 'http://127.0.0.1:5173';
      },
    ],
    [
      'normal scenario',
      (_config: AppConfig, world: World) => {
        world.scenario_id = 'normal-world';
      },
    ],
    [
      'external observations',
      (_config: AppConfig, world: World) => {
        world.observations = [{ source: 'FR24' }];
      },
    ],
    [
      'active flight polling',
      (config: AppConfig, _world: World) => {
        config.traffic_running = true;
      },
    ],
    [
      'already-applied expected budget',
      (_config: AppConfig, world: World) => {
        world.budget = 20000;
      },
    ],
    [
      'oversized scenario',
      (_config: AppConfig, world: World) => {
        world.groups = Array.from({ length: 21 }, (_, i) => ({ ...world.groups[0], id: `G${i}` }));
      },
    ],
  ] as const)('rejects %s before the session', (_name, change) => {
    const { config, world } = fixture();
    change(config, world);
    expect(() => assertIsolatedTarget(config, world)).toThrow();
  });

  it('reserves the single command even when a provider call would fail', () => {
    const gate = new SingleCommandGate();
    gate.take();
    expect(() => gate.take()).toThrow(/single command/);
  });

  it('uses only the synthetic track and restores an inherited media API without calling it', async () => {
    const ambientCapture = vi.fn();
    const devices = Object.create({ getUserMedia: ambientCapture }) as MediaDevices;
    const stream = {} as MediaStream;
    const acquired = vi.fn();
    const restore = installSyntheticInput(devices, stream, acquired);
    const installed = devices.getUserMedia.bind(devices);
    expect(await devices.getUserMedia({ audio: true })).toBe(stream);
    await expect(installed({ audio: true })).rejects.toThrow(/only be acquired once/);
    expect(acquired).toHaveBeenCalledTimes(1);
    expect(ambientCapture).not.toHaveBeenCalled();
    restore();
    restore();
    expect(Object.hasOwn(devices, 'getUserMedia')).toBe(false);
    expect(devices.getUserMedia).toBe(ambientCapture);
    await expect(installed({ audio: true })).rejects.toThrow();
    expect(ambientCapture).not.toHaveBeenCalled();
  });

  it('restores an own property descriptor exactly, including early cancellation', async () => {
    const original = vi.fn();
    const devices = {} as MediaDevices;
    Object.defineProperty(devices, 'getUserMedia', {
      value: original,
      configurable: true,
      writable: false,
    });
    const before = Object.getOwnPropertyDescriptor(devices, 'getUserMedia');
    const restore = installSyntheticInput(devices, {} as MediaStream, vi.fn());
    const installed = devices.getUserMedia.bind(devices);
    restore();
    expect(Object.getOwnPropertyDescriptor(devices, 'getUserMedia')).toEqual(before);
    await expect(installed({ audio: true })).rejects.toThrow();
    expect(original).not.toHaveBeenCalled();
  });

  it('exports only measured outcomes and never passenger tokens or whole worlds', () => {
    const { world } = fixture();
    const decision: Decision = {
      id: 'decision-new',
      timestamp: '2026-10-31T12:00:00Z',
      source_version: 1,
      applied_version: 2,
      mode: 'ai',
      provider: 'OpenAI Decisions',
      request: 'Synthetic request',
      explanation: 'Verified synthetic outcome',
      selected_plan_id: 'p1',
      latency_ms: 1200,
      timings_ms: { interpretation_ms: 700, decisions_ms: 400 },
      focus_id: null,
      candidates: [
        {
          id: 'p1',
          strategy: {
            id: 's1',
            label: 'Balanced',
            objective: 'balanced',
            preferred_airport: null,
            weights: {},
          },
          allocations: [],
          exceptions: [],
          recovered_passengers: 1,
          total_cost: 100,
          average_arrival: 1200,
          validation: { valid: true },
          solver: 'bounded',
          optimality: 'not globally proven',
          latency_ms: 100,
        },
      ],
    };
    const result: MutationResult = {
      world: { ...world, budget: 20000, version: 2, decisions: [decision] },
      decision,
    };
    const summary = summarizeResult(result, world);
    expect(summary).toMatchObject({
      expected_budget_applied: true,
      applied: true,
      new_ai_decision: true,
      selected_candidate_valid: true,
    });
    const exported = JSON.stringify(summary);
    expect(exported).not.toContain('private-passenger-token');
    expect(exported).not.toContain('groups');
    expect(summarizeResult({ world, applied: false }, world)).toMatchObject({
      applied: false,
      new_ai_decision: false,
      selected_candidate_valid: false,
    });
    expect(summarizeResult(result, { ...world, decisions: [decision] }).new_ai_decision).toBe(
      false,
    );
  });
});
