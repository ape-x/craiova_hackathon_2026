import { describe, expect, it, vi } from 'vitest';
import { ApiError, TransportError } from './api';
import {
  commandContext,
  commandNeedsRegionalView,
  keepCommandReplyVisible,
  rememberCommand,
  rememberFailedCommand,
  verifiedCommandResult,
} from './commandContext';
import type { CommandExchange } from './commandContext';
import type { AuditEvent, MutationResult, World } from './types';

describe('conversation context for scenario authoring', () => {
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
});
