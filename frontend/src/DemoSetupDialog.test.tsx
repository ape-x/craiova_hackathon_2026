import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import DemoSetupDialog, {
  DemoNotice,
  demoCaseInput,
  initialDemoCaseDraft,
} from './DemoSetupDialog';
import WorkspaceHeader from './WorkspaceHeader';
import ResourceRail from './ResourceRail';
import type { World } from './types';

const now = new Date('2026-11-01T23:30:00Z');

describe('test case draft', () => {
  it('defaults to two fictional adults and an explicitly UTC future travel window', () => {
    const draft = initialDemoCaseDraft(now);
    expect(draft).toMatchObject({
      origin: 'LHR',
      destination: 'OTP',
      passengers: '2',
      group_size: '2',
      group_budget_eur: '2000',
      ready_at: '2026-11-02T08:00',
      deadline: '2026-11-03T22:00',
    });
    expect(demoCaseInput(draft, now.getTime())).toMatchObject({
      passengers: 2,
      group_size: 2,
      group_budget_eur: 2000,
      ready_at: '2026-11-02T08:00:00.000Z',
      deadline: '2026-11-03T22:00:00.000Z',
    });
  });

  it('normalizes airport codes without adding source inventory or request identities', () => {
    const payload = demoCaseInput(
      {
        ...initialDemoCaseDraft(now),
        origin: ' lhr ',
        destination: 'otp',
        description: ' A test case. ',
      },
      now.getTime(),
    );
    expect(payload).toMatchObject({
      origin: 'LHR',
      destination: 'OTP',
      description: 'A test case.',
    });
    expect(Object.keys(payload).sort()).toEqual(
      [
        'origin',
        'destination',
        'passengers',
        'group_size',
        'group_budget_eur',
        'ready_at',
        'deadline',
        'description',
      ].sort(),
    );
  });

  it.each([
    ['origin', 'London'],
    ['destination', 'LHR'],
    ['passengers', '0'],
    ['passengers', '2.5'],
    ['group_size', '3'],
    ['group_size', '0'],
    ['group_budget_eur', ''],
    ['group_budget_eur', '-1'],
    ['ready_at', '2026-02-30T08:00'],
    ['ready_at', '2026-10-31T08:00'],
    ['deadline', '2026-11-02T07:00'],
    ['description', ' '],
  ])('rejects invalid %s value %s before submission', (field, value) => {
    expect(() =>
      demoCaseInput({ ...initialDemoCaseDraft(now), [field]: value }, now.getTime()),
    ).toThrow();
  });
});

describe('demo source labels', () => {
  it('describes fictional passengers and real suppliers without executing a search or creating a case on render', () => {
    const create = vi.fn();
    const close = vi.fn();
    const html = renderToStaticMarkup(
      <DemoSetupDialog busy={false} onCreate={create} onClose={close} />,
    );
    expect(html).toContain('Create fictional adult passengers at real airports');
    expect(html).toContain('Creating a case does not search suppliers');
    expect(html).toContain('Ready to travel · UTC');
    expect(html).toContain('Create test passengers');
    expect(create).not.toHaveBeenCalled();
    expect(close).not.toHaveBeenCalled();
    expect(
      renderToStaticMarkup(<DemoSetupDialog busy onCreate={create} onClose={close} />),
    ).toContain('disabled=""');
  });

  it('shows a visible source boundary and labels the header without changing regular live mode', () => {
    const action = vi.fn();
    const banner = renderToStaticMarkup(<DemoNotice onSetup={action} />);
    expect(banner).toContain('Fictional passengers · real supplier data');
    expect(banner).toContain('no bookings or passenger messages are sent');
    const props = {
      locationLabel: 'LHR',
      mode: 'live' as const,
      connected: true,
      sidebarOpen: true,
      showLog: false,
      onToggleSidebar: action,
      onSearch: action,
      onSettings: action,
      onLog: action,
      onHelp: action,
      onStartRecovery: action,
      onDemoSetup: action,
    };
    const demo = renderToStaticMarkup(<WorkspaceHeader {...props} demo />);
    expect(demo).toContain('Simulation · live data');
    expect(demo).toContain('New test case');
    expect(demo).not.toContain('Start recovery');
    const live = renderToStaticMarkup(<WorkspaceHeader {...props} />);
    expect(live).toContain('Live · assisted');
    expect(live).not.toContain('New test case');
    expect(action).not.toHaveBeenCalled();
  });

  it('labels airport reference data without implying an outage or operational clearance', () => {
    const action = vi.fn();
    const world: World = {
      scenario_id: 'test-case-ui',
      version: 1,
      epoch: now.toISOString(),
      sim_time: 0,
      mode: 'live',
      demo: true,
      currency: 'EUR',
      budget: 0,
      airports: [
        {
          id: 'LHR',
          code: 'LHR',
          name: 'London Heathrow',
          lat: 51.47,
          lon: -0.46,
          available: false,
          diversion_eligible: false,
          operational_status: 'unknown',
        },
      ],
      resources: [],
      groups: [],
      offers: [],
      reservations: [],
      events: [],
      decisions: [],
      observations: [],
      stats: {},
      provider_status: {},
    };
    const html = renderToStaticMarkup(
      <ResourceRail
        world={world}
        selectedId={null}
        onSelect={action}
        onMode={action}
        onScenario={action}
        onLiveData={action}
        onDemoSetup={action}
      />,
    );
    expect(html).toContain('REFERENCE');
    expect(html).toContain('operational status not verified');
    expect(html).not.toContain('UNKNOWN');
    expect(html).toContain('Create test passengers');
    expect(html).toContain('fictional passengers');
  });
});
