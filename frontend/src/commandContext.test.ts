import { describe, expect, it, vi } from 'vitest';
import { ApiError, TransportError } from './api';
import {
  commandContext,
  commandNeedsRegionalView,
  keepCommandReplyVisible,
  recoveryCaseForResult,
  rememberCommand,
  rememberFailedCommand,
  verifiedCommandResult,
} from './commandContext';
import type { CommandExchange } from './commandContext';
import type { AuditEvent, MutationResult, RecoveryJob, World } from './types';
import { resourceArrivalReceipt } from './resourceArrivalReceipt.testSupport';
import { UNCONFIRMED_COMBINED_REPORT } from './combinedReports';

describe('conversation context for scenario authoring', () => {
  it('remembers both recorded service/arrival facts once, with the exact arrival case and blocked search', () => {
    const result = resourceArrivalReceipt();
    result.recovery_blocked = {
      code: 'airport_transfer_setup_required',
      explanation: 'Review the explicit pickup and luggage inputs.',
    };
    const detail = verifiedCommandResult(result);
    expect(detail).toContain('serviciul BUS1 raportat indisponibil pentru planificare');
    expect(detail).toContain('grupurile G1 raportate la OTP');
    expect(detail).toContain('pentru 2 grupuri, posibil din alte cazuri');
    expect(detail).toContain(
      'Căutarea nu a pornit: Review the explicit pickup and luggage inputs.',
    );
    const history = rememberCommand([], 'BUS1 unavailable; G1 ready at OTP; recalculate.', detail);
    const saved = commandContext(history, 'BUS1');
    expect(history).toHaveLength(1);
    expect(saved.match(/verified_backend/g)).toHaveLength(1);
    expect(saved).toContain('serviciul BUS1 raportat indisponibil');
    expect(saved).toContain('grupurile G1 raportate la OTP');
    expect(keepCommandReplyVisible(result)).toBe(true);
    expect(recoveryCaseForResult(result, result.world)).toBe('CASE1');
    expect(recoveryCaseForResult(result, { ...result.world, scenario_id: 'NEW' })).toBeNull();
  });
  it('keeps a replayed queued search historical and a malformed plural result unconfirmed in later context', () => {
    const result = resourceArrivalReceipt();
    result.replayed = true;
    result.recovery_job = {
      id: 'JOB1',
      incident_id: 'CASE1',
      group_ids: ['G1'],
      status: 'queued',
    } as RecoveryJob;
    expect(verifiedCommandResult(result)).toContain('era deja înregistrată în acest răspuns');
    result.world.resources[0].available = true;
    const detail = verifiedCommandResult(result);
    expect(detail).toBe(UNCONFIRMED_COMBINED_REPORT);
    const history = rememberCommand([], 'A single combined request.', detail);
    expect(history[0].outcome).toBe('unconfirmed');
    expect(commandContext(history, null)).toContain('unconfirmed_outcome');
    expect(commandContext(history, null)).not.toContain('verified_backend');
    expect(recoveryCaseForResult(result, result.world)).toBeNull();
  });
  it('retains typed clarification details after a rejected request without labeling them applied', () => {
    const pending = rememberCommand(
      [],
      'Adaugă 12 pasageri spre Lisabona.',
      'Lipsesc termenul de sosire și bugetul.',
    );
    const history = rememberFailedCommand(
      pending,
      'Termenul este 22:00 și bugetul este 300 EUR de persoană.',
      new ApiError(503, 'Decisions did not approve the plan'),
    );
    const context = commandContext(history, null);
    const entries = context
      .split('\n')
      .filter((line) => line.startsWith('{'))
      .map((line) => JSON.parse(line));
    expect(entries).toHaveLength(2);
    expect(entries[0].verified_backend).toContain('Lipsesc termenul');
    expect(entries[1].user).toContain('22:00');
    expect(entries[1].user).toContain('300 EUR');
    expect(entries[1].failed_request_outcome).toContain('Serverul a respins cererea');
    expect(entries[1].failed_request_outcome).toContain('nu reexecuta automat');
    expect(entries[1]).not.toHaveProperty('verified_backend');
  });

  it('marks uncertain transport outcomes separately and leaves exact retries under operator control', () => {
    const retry = vi.fn();
    const history = rememberFailedCommand(
      [],
      'Creează un charter cu 12 locuri.',
      new TransportError(async <T>() => {
        retry();
        return { replayed: true } as T;
      }),
    );
    const context = commandContext(history, null);
    expect(context).toContain('unconfirmed_outcome');
    expect(context).not.toContain('verified_backend');
    expect(context).toContain('cererea poate fi deja aplicată');
    expect(context).toContain('Starea actuală a serverului are prioritate');
    expect(context).toContain('același ID');
    expect(context).toContain('Recover request');
    expect(context).toContain('Nu repeta automat cererea cu un ID nou');
    expect(retry).not.toHaveBeenCalled();
  });

  it('does not turn an unexpected client error into a confirmed backend rejection', () => {
    const context = commandContext(
      rememberFailedCommand([], 'Schimbă bugetul.', new Error('Unexpected failure')),
      null,
    );
    expect(context).toContain('unconfirmed_outcome');
    expect(context).not.toContain('failed_request_outcome');
    expect(context).not.toContain('verified_backend');
  });

  it('preserves a pending clarification and its original request for a short follow-up', () => {
    const result = verifiedCommandResult({
      world: { version: 12 },
      applied: false,
      explanation: 'Câte locuri are noul autocar și la ce oră poate pleca?',
    } as MutationResult);
    const history = rememberCommand([], 'Adaugă un autocar spre Sibiu.', result);
    const context = commandContext(history, 'CRA', 'assistant: Pot să te ajut cu capacitatea.');
    expect(context).toContain('Adaugă un autocar spre Sibiu.');
    expect(context).toContain('Câte locuri are noul autocar');
    expect(context).toContain('Nicio modificare operațională aplicată.');
    expect(context).toContain('verified_backend');
    expect(context).toContain('Nu reexecuta cereri anterioare');
    expect(context).toContain('Entitate selectată în UI: "CRA"');
    expect(context).toContain('nu dovedește executarea acțiunilor');
  });

  it('keeps the latest verified pairs when long voice transcripts exceed the API context limit', () => {
    let history: CommandExchange[] = [];
    for (let index = 0; index < 12; index++) {
      history = rememberCommand(
        history,
        `Cererea ${index}: ${'condiție '.repeat(200)}`,
        `Rezultatul ${index}: ${'clarificare '.repeat(100)}`,
      );
    }
    const context = commandContext(history, 'B12', 'transcriere veche '.repeat(1000));
    expect(history).toHaveLength(8);
    expect(context.length).toBeLessThanOrEqual(6000);
    expect(context).toContain('Cererea 11:');
    expect(context).toContain('Rezultatul 11:');
    expect(context).not.toContain('Cererea 0:');
  });

  it('retains a structurally valid newest exchange even when JSON escaping expands the request', () => {
    const history = rememberCommand([], 'Cerere: ' + '\n\t"'.repeat(1500), 'Lipsește destinația.');
    const context = commandContext(history, null, 'istoric '.repeat(3000));
    expect(context.length).toBeLessThanOrEqual(6000);
    const line = context.split('\n').find((part) => part.startsWith('{'))!;
    expect(JSON.parse(line)).toMatchObject({ verified_backend: 'Lipsește destinația.' });
  });

  it('does not infer successful mutations from a no-change result without an explanation', () => {
    expect(verifiedCommandResult({ world: { version: 2 }, applied: false } as MutationResult)).toBe(
      'Nicio modificare operațională aplicată. Versiunea 2.',
    );
  });

  it('keeps a clarification visible while allowing completed focus-only replies to fade', () => {
    const clarification = {
      world: { version: 12 },
      applied: false,
      explanation: 'La ce oră pleacă autocarul?',
    } as MutationResult;
    expect(keepCommandReplyVisible(clarification)).toBe(true);
    expect(keepCommandReplyVisible({ ...clarification, focus_id: 'B12' })).toBe(false);
    expect(
      keepCommandReplyVisible({
        ...clarification,
        decision: { focus_id: 'B12' },
      } as MutationResult),
    ).toBe(false);
    expect(keepCommandReplyVisible({ ...clarification, applied: true })).toBe(false);
  });

  it('retains the backend authoring explanation ahead of the planning-only explanation', () => {
    const reply = verifiedCommandResult({
      world: { version: 13 },
      applied: true,
      explanation: 'Am adăugat un autocar cu 52 de locuri spre Salonic. Pleacă la 18:00.',
      decision: { explanation: 'Am recalculat transferurile.' },
    } as MutationResult);
    expect(reply).toContain('52 de locuri spre Salonic');
    expect(reply).toContain('Pleacă la 18:00.');
    expect(reply).toContain('Versiunea 13.');
  });
});

describe('regional view after a verified command', () => {
  const event = (id: string, type: string, target_id: string | null = null) =>
    ({ id, type, target_id }) as AuditEvent;
  const world = (overrides: Partial<World> = {}) =>
    ({
      scenario_id: 'test',
      version: 1,
      events: [],
      airports: [],
      resources: [],
      ...overrides,
    }) as World;

  it('does not react to a diversion left in the previous audit tail after a budget-only change', () => {
    const old = event('old-diversion', 'flight_diverted', 'F101');
    const before = world({ events: [old] });
    const result = {
      world: world({ events: [old, event('budget', 'budget_changed')] }),
      applied: true,
    };
    expect(commandNeedsRegionalView(before, result)).toBe(false);
    expect(commandNeedsRegionalView(before, { ...result, applied: false })).toBe(false);
  });

  it('reveals a newly authored airport or a new resource serving a regional destination', () => {
    const before = world();
    const airportWorld = world({
      events: [event('new-airport', 'airport_added', 'SKG')],
      airports: [{ id: 'SKG' } as World['airports'][number]],
    });
    expect(commandNeedsRegionalView(before, { world: airportWorld, applied: true })).toBe(true);
    const resourceWorld = world({
      events: [event('new-bus', 'resource_added', 'B-SKG')],
      resources: [{ id: 'B-SKG', origin: 'CRA', destination: 'SKG' } as World['resources'][number]],
    });
    expect(commandNeedsRegionalView(before, { world: resourceWorld, applied: true })).toBe(true);
  });

  it('keeps a new local resource in airport view and ignores nonexistent event targets', () => {
    const local = world({
      events: [event('new-hotel', 'resource_added', 'H-NEW')],
      resources: [{ id: 'H-NEW', origin: 'CRA', destination: 'CRA' } as World['resources'][number]],
    });
    expect(commandNeedsRegionalView(world(), { world: local, applied: true })).toBe(false);
    expect(
      commandNeedsRegionalView(world(), {
        world: world({ events: [event('missing-target', 'resource_added', 'B-MISSING')] }),
        applied: true,
      }),
    ).toBe(false);
  });

  it('switches for a new diversion but never from an unrelated replacement scenario', () => {
    const next = world({ events: [event('new-diversion', 'flight_diverted', 'F101')] });
    expect(commandNeedsRegionalView(world(), { world: next, applied: true })).toBe(true);
    expect(
      commandNeedsRegionalView(world(), {
        world: { ...next, scenario_id: 'replacement' },
        applied: true,
      }),
    ).toBe(false);
  });

  it('reveals a freshly authored network when a command resets and creates the scenario atomically', () => {
    const next = world({
      scenario_id: 'fresh',
      events: [event('reset', 'scenario_reset'), event('weather', 'airport_weather', 'LIS')],
      airports: [{ id: 'LIS' } as World['airports'][number]],
    });
    expect(commandNeedsRegionalView(world(), { world: next, applied: true })).toBe(true);
    expect(commandNeedsRegionalView(world(), { world: next, applied: false })).toBe(false);
    expect(
      commandNeedsRegionalView(world(), {
        world: world({ scenario_id: 'empty', events: [event('reset', 'scenario_reset')] }),
        applied: true,
      }),
    ).toBe(false);
  });
});

describe('scoped live recovery receipts', () => {
  const world = {
    mode: 'live',
    scenario_id: 'live-operation',
    version: 10,
    incidents: [{ id: 'RC-ONE' }],
  } as World;
  const job = {
    id: 'JOB-ONE',
    incident_id: 'RC-ONE',
    status: 'queued',
    calls_dispatched: 0,
    max_provider_calls: 7,
  } as RecoveryJob;
  const result: MutationResult = {
    world,
    applied: false,
    incident_id: 'RC-ONE',
    recovery_job: job,
    command_interpretation_calls: 1,
    recovery_total_call_limit: 8,
    explanation:
      'Cererea este înregistrată: 1 interpretare și cel mult 7 apeluri ale jobului. Nu confirmă un itinerar sau o rezervare.',
  };

  it('reads the verified queue receipt without claiming a plan or simulator mutation', () => {
    const text = verifiedCommandResult(result);
    expect(text).toContain('1 interpretare');
    expect(text).toContain('7 apeluri');
    expect(text).toContain('Nu confirmă un itinerar');
    expect(text).not.toContain('Rezultat confirmat');
    expect(text).not.toContain('simulator');
    expect(keepCommandReplyVisible(result)).toBe(true);
  });

  it('opens the matching current case for both typed/voice receipts and exact retries', () => {
    expect(recoveryCaseForResult(result, world)).toBe('RC-ONE');
    expect(recoveryCaseForResult({ ...result, replayed: true }, { ...world, version: 12 })).toBe(
      'RC-ONE',
    );
    expect(recoveryCaseForResult({ ...result, recovery_job: null }, world)).toBe('RC-ONE');
  });

  it('never opens a missing, replaced or mismatched case from a replay', () => {
    expect(recoveryCaseForResult(result, { ...world, incidents: [] })).toBeNull();
    expect(recoveryCaseForResult(result, { ...world, scenario_id: 'replacement' })).toBeNull();
    expect(recoveryCaseForResult(result, { ...world, mode: 'simulation' })).toBeNull();
    expect(
      recoveryCaseForResult(
        { ...result, recovery_job: { ...job, incident_id: 'RC-OTHER' } },
        world,
      ),
    ).toBeNull();
    expect(recoveryCaseForResult({ world, applied: false }, world)).toBeNull();
  });

  it('keeps queued history as reference and does not call a live applied result a simulator', () => {
    const text = commandContext(
      rememberCommand([], 'Recover RO321', verifiedCommandResult(result)),
      null,
    );
    expect(text).toContain('Cererea este înregistrată');
    expect(text).toContain('Nu reexecuta cereri anterioare');
    expect(
      verifiedCommandResult({ world, applied: true, explanation: 'Cerința a fost actualizată.' }),
    ).toContain('Rezultat confirmat de aplicație.');
  });
});

describe('platform action acknowledgements', () => {
  it('speaks actual UI results rather than a proposed success message', () => {
    const result: MutationResult = {
      world: { mode: 'live', version: 3 } as World,
      explanation: 'Opened hotels successfully',
      ui_actions: [{ name: 'ui.open', arguments: { screen: 'live-data', tab: 'hotels' } }],
      ui_action_results: [
        {
          name: 'ui.open',
          status: 'failed',
          message: 'Panoul nu a fost deschis: o cerere este încă în curs.',
        },
      ],
    };
    expect(verifiedCommandResult(result)).toBe(
      'Panoul nu a fost deschis: o cerere este încă în curs.',
    );
  });
  it('does not confirm a UI action that has not executed in the browser', () => {
    const result: MutationResult = {
      world: { mode: 'live', version: 3 } as World,
      ui_actions: [{ name: 'ui.export', arguments: {} }],
    };
    expect(verifiedCommandResult(result)).toContain('nu au un rezultat confirmat');
  });
  it('retains the backend partial result and the actual navigation outcome', () => {
    const result: MutationResult = {
      world: { mode: 'live', version: 4 } as World,
      explanation: 'Caz creat. Căutarea nu a pornit: limita este insuficientă.',
      platform_results: [
        { name: 'demo.create_case', status: 'succeeded' },
        { name: 'recovery.start', status: 'failed' },
      ],
      ui_action_results: [{ name: 'ui.open', status: 'succeeded', message: 'Cazul este deschis.' }],
    };
    expect(verifiedCommandResult(result)).toContain('Căutarea nu a pornit');
    expect(verifiedCommandResult(result)).toContain('Cazul este deschis.');
  });
});
