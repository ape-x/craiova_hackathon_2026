import { Children, isValidElement } from 'react';
import type { ComponentProps, ReactElement, ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, TransportError } from './api';
import RecoveryCaseDialog from './RecoveryCaseDialog';
import RecoveryJobPanel from './RecoveryJobPanel';
import { groupRecoveryEntry } from './groupRecoveryEntry';
import {
  currentGroupRecoveryIntent,
  initialRecoverySelection,
  recoveryGroupSelectionIssue,
} from './recoveryNavigation';
import { recoveryDataReturnIssue } from './recoveryDataContext';
import type { RecoveryDataContext } from './recoveryDataContext';
import type { RecoveryFormDraft } from './recoveryFormDraft';
import { emptyHotelRecoveryForm } from './hotelRecovery';
import type { RecoveryIncident, RecoveryJobResponse, World } from './types';

const incident = (id: string, groups: string[]): RecoveryIncident => ({
  id,
  created_at: '2026-11-01T10:00:00Z',
  source: 'operator_report',
  kind: 'flight_disruption',
  description: 'Operator-reported disruption.',
  requested_flight_number: 'RO321',
  date_utc: '2026-10-31',
  flight_ref: { lookup_id: 'lookup-1', selection_id: 'leg-1' },
  unavailable_airport_code: null,
  group_ids: groups,
  status: 'open',
  flight: null,
  flight_context_state: 'expired',
});
const fixture = (): World =>
  ({
    mode: 'live',
    scenario_id: 'OPS',
    version: 5,
    epoch: '2026-11-01T00:00:00Z',
    sim_time: 600,
    currency: 'EUR',
    budget: 5000,
    airports: [],
    resources: [],
    offers: [],
    reservations: [],
    groups: ['G1', 'G2', 'G3'].map((id) => ({
      id,
      name: `Party ${id}`,
      incident_id: id === 'G3' ? 'CASE-2' : 'CASE-1',
      status: 'waiting',
      original_flight_id: 'RO321',
      origin: 'LHR',
      destination: 'CRA',
      size: 2,
      adult_count: 2,
      source: 'operator',
      ready_at: 600,
      deadline: 3000,
      budget: 1000,
      accessible: false,
    })),
    incidents: [incident('CASE-1', ['G1', 'G2']), incident('CASE-2', ['G3'])],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  }) as unknown as World;
const context = (groupIds = ['G1']): RecoveryDataContext => ({
  scenarioId: 'OPS',
  incidentId: 'CASE-1',
  groupIds,
});

function elements(node: ReactNode): ReactElement<Record<string, unknown>>[] {
  const result: ReactElement<Record<string, unknown>>[] = [];
  Children.forEach(node, (child) => {
    if (isValidElement<Record<string, unknown>>(child)) {
      result.push(child, ...elements(child.props.children as ReactNode));
    }
  });
  return result;
}

function captureCase(props: ComponentProps<typeof RecoveryCaseDialog>) {
  let tree: ReactNode;
  function Capture() {
    tree = RecoveryCaseDialog(props);
    return tree;
  }
  const html = renderToStaticMarkup(<Capture />);
  return { html, nodes: elements(tree) };
}

function capturePanel(props: ComponentProps<typeof RecoveryJobPanel>) {
  let tree: ReactNode;
  function Capture() {
    tree = RecoveryJobPanel(props);
    return tree;
  }
  const html = renderToStaticMarkup(<Capture />);
  return { html, nodes: elements(tree) };
}

function caseProps(world = fixture()): ComponentProps<typeof RecoveryCaseDialog> {
  return {
    world,
    incidentId: 'CASE-1',
    initialSelection: context(),
    onApplied: vi.fn(),
    onRefresh: vi.fn(async () => {}),
    onClose: vi.fn(),
    onManageData: vi.fn(),
  };
}

function panelHtml(world: World, selection = context(), disabled = false) {
  return renderToStaticMarkup(
    <RecoveryJobPanel
      world={world}
      incident={world.incidents![0]}
      disabled={disabled}
      initialSelection={selection}
      onRefresh={async () => {}}
      onMutationPending={() => {}}
    />,
  );
}

function checkboxFor(html: string, group: string) {
  const label = html
    .match(/<label class="case-group">[\s\S]*?<\/label>/g)
    ?.find((item) => item.includes(`Party ${group}`));
  expect(label).toBeDefined();
  return label!.match(/<input[^>]*>/)![0];
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('explicit recovery navigation scope', () => {
  it('preselects exactly one requested group while another linked group remains unselected', () => {
    const world = fixture();
    const requested = context();
    const initial = initialRecoverySelection(world, world.incidents![0], requested);
    expect(initial).toEqual({ groupIds: ['G1'], issue: null });
    initial.groupIds.push('G2');
    expect(requested.groupIds).toEqual(['G1']);
    const html = panelHtml(world, requested);
    expect(checkboxFor(html, 'G1')).toContain('checked=""');
    expect(checkboxFor(html, 'G2')).not.toContain('checked=""');
    expect(html).not.toContain('Party G3');
    expect(html).not.toContain('Requested selection could not be restored');
  });

  it('preserves an exact group order and requires explicit selection when no context was supplied', () => {
    const world = fixture();
    expect(initialRecoverySelection(world, world.incidents![0], context(['G2', 'G1']))).toEqual({
      groupIds: ['G2', 'G1'],
      issue: null,
    });
    expect(initialRecoverySelection(world, world.incidents![0])).toEqual({
      groupIds: [],
      issue: null,
    });
  });

  it.each([
    ['wrong operation', { ...context(), scenarioId: 'OLD' }],
    ['wrong case', { ...context(), incidentId: 'CASE-2', groupIds: ['G3'] }],
    ['missing group', context(['G1', 'MISSING'])],
    ['mixed cases', context(['G1', 'G3'])],
    ['duplicate group', context(['G1', 'G1'])],
    ['normalized lookalike', context(['G1', 'g2'])],
  ])('refuses %s as a whole instead of silently keeping a valid subset', (_, requested) => {
    const world = fixture();
    expect(initialRecoverySelection(world, world.incidents![0], requested)).toEqual({
      groupIds: [],
      issue: expect.any(String),
    });
    const html = panelHtml(world, requested);
    expect(checkboxFor(html, 'G1')).not.toContain('checked=""');
    expect(checkboxFor(html, 'G2')).not.toContain('checked=""');
    expect(html).toContain('Requested selection could not be restored');
  });

  it('rejects the entire selection when one group has a confirmed offer despite a stale waiting status', () => {
    const world = fixture();
    world.offers.push({
      id: 'CONFIRMED-1',
      group_id: 'G2',
      status: 'confirmed',
      itinerary: { legs: [], arrival: 900, total_cost: 100 },
      expires_at: '2026-11-02T00:00:00Z',
      created_version: 4,
      confirmed_at: '2026-11-01T10:00:00Z',
      reason: null,
    });
    expect(recoveryGroupSelectionIssue(world, world.incidents![0], ['G1', 'G2'])).toContain(
      'recorded bookings',
    );
    expect(
      initialRecoverySelection(world, world.incidents![0], context(['G1', 'G2'])).groupIds,
    ).toEqual([]);
    const html = panelHtml(world, context(['G1', 'G2']));
    expect(checkboxFor(html, 'G1')).not.toContain('checked=""');
    expect(checkboxFor(html, 'G2')).toContain('disabled=""');
  });

  it('rejects conflicting reciprocal membership rather than repairing it during navigation', () => {
    const world = fixture();
    world.groups[1].incident_id = 'CASE-2';
    expect(
      initialRecoverySelection(world, world.incidents![0], context(['G1', 'G2'])).groupIds,
    ).toEqual([]);
    expect(recoveryGroupSelectionIssue(world, world.incidents![0], ['G2'])).toContain(
      'conflicting',
    );
    expect(world.groups[1].incident_id).toBe('CASE-2');
    expect(world.incidents![0].group_ids).toEqual(['G1', 'G2']);
  });

  it('does not trim an over-limit selection to a smaller valid scope', () => {
    const world = fixture();
    expect(
      recoveryGroupSelectionIssue(
        world,
        world.incidents![0],
        Array.from({ length: 25 }, (_, i) => `G${i}`),
      ),
    ).toContain('no subset');
  });

  it('rechecks the clicked intent using the latest world without treating expired FR24 or a version change as lost scope', () => {
    const world = fixture();
    const clicked = groupRecoveryEntry(world, 'G1')!.intent;
    world.version += 1;
    expect(currentGroupRecoveryIntent(world, clicked)).toEqual({
      ...clicked,
      sourceVersion: world.version,
    });
    expect(world.incidents![0].flight_context_state).toBe('expired');
  });

  it.each(['case', 'kind', 'operation', 'missing'] as const)(
    'does not reroute a stale clicked intent after its %s changes',
    (change) => {
      const world = fixture();
      const clicked = groupRecoveryEntry(world, 'G1')!.intent;
      if (change === 'case') {
        world.groups[0].incident_id = 'CASE-2';
        world.incidents![0].group_ids = ['G2'];
        world.incidents![1].group_ids.push('G1');
      } else if (change === 'kind') world.groups[0].status = 'confirmed';
      else if (change === 'operation') world.scenario_id = 'OTHER';
      else world.groups.shift();
      expect(currentGroupRecoveryIntent(world, clicked)).toBeNull();
    },
  );

  it('does not turn an earlier setup click into a newly bound recovery request', () => {
    const world = fixture();
    world.groups[0].incident_id = null;
    world.incidents![0].group_ids = ['G2'];
    const clicked = groupRecoveryEntry(world, 'G1')!.intent;
    expect(clicked.kind).toBe('setup');
    world.groups[0].incident_id = 'CASE-1';
    world.incidents![0].group_ids.push('G1');
    expect(currentGroupRecoveryIntent(world, clicked)).toBeNull();
  });
});

describe('case selection handoff and pending requests', () => {
  it.each(['3', '9'])(
    'preserves a changed %s-call allowance and the full unsent form through the actual data handoff',
    (limit) => {
      const fetch = vi.fn();
      vi.stubGlobal('fetch', fetch);
      const form: RecoveryFormDraft = {
        groupIds: ['G1'],
        objective: 'fastest',
        modes: ['bus', 'flight'],
        limit: '8',
        instructions: '  Keep the requested limits.\nNo changes to baggage.  ',
        hotelMode: false,
        hotelFields: {
          ...emptyHotelRecoveryForm(),
          checkin_at: '2026-11-02T14:00',
          rooms: '02',
          initial_point: {
            label: 'Original declared point',
            latitude: 'invalid latitude',
            longitude: '',
            confirmed: true,
          },
        },
        airportTransfers: [
          {
            group_id: 'G1',
            pickup_at: '2026-11-02T15:00',
            suitcases: '0',
            handbags: '',
            scope: 'original-initial-scope',
          },
        ],
        afterFlightTransfer: {
          group_id: 'G1',
          scope: 'original-after-flight-scope',
          pickup_window_start: '2026-11-03T12:00',
          pickup_window_end: 'unfinished time',
          airport_processing_minutes: '90',
          suitcases: '0',
          handbags: '2',
          max_inbound_options: '2',
        },
      };
      const props = { ...caseProps(), initialSelection: { ...context(), searchDraft: form } };
      const savedCase = captureCase(props);
      const child = savedCase.nodes.find((node) => node.type === RecoveryJobPanel)!;
      const rendered = capturePanel(child.props as ComponentProps<typeof RecoveryJobPanel>);
      const allowance = rendered.nodes.find(
        (node) =>
          node.type === 'label' && renderToStaticMarkup(node).includes('Maximum provider calls'),
      )!;
      const input = elements(allowance).find((node) => node.type === 'input')!;
      (input.props.onChange as (event: { target: { value: string } }) => void)({
        target: { value: limit },
      });
      const manage = savedCase.nodes.find(
        (node) =>
          node.type === 'button' &&
          renderToStaticMarkup(node).includes('Manage passenger requirements'),
      )!;
      (manage.props.onClick as () => void)();
      const handedOff = (props.onManageData as ReturnType<typeof vi.fn>).mock
        .calls[0][0] as RecoveryDataContext;
      expect(handedOff).toEqual({ ...context(), searchDraft: { ...form, limit } });
      expect(handedOff.searchDraft).not.toBe(form);
      expect(form.limit).toBe('8');
      const onDraftChange = vi.fn();
      const returned = capturePanel({
        world: props.world,
        incident: props.world.incidents![0],
        initialSelection: handedOff,
        disabled: false,
        onRefresh: async () => {},
        onMutationPending: () => {},
        onDraftChange,
      });
      expect(returned.html).toMatch(
        new RegExp(`Maximum provider calls<input[^>]*value="${limit}"`),
      );
      expect(returned.html).toContain('<option value="fastest" selected="">');
      const instructions = returned.nodes.find((node) => node.type === 'textarea')!;
      expect(instructions.props.value).toBe(form.instructions);
      (instructions.props.onChange as (event: { target: { value: string } }) => void)({
        target: { value: 'Next explicit edit' },
      });
      expect(onDraftChange).toHaveBeenCalledWith({
        ...form,
        limit,
        instructions: 'Next explicit edit',
      });
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it('reports explicit checkbox changes without sourcing or losing the previous selection', () => {
    const world = fixture();
    const onSelectionChange = vi.fn();
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    let tree: ReactNode;
    function Capture() {
      tree = RecoveryJobPanel({
        world,
        incident: world.incidents![0],
        initialSelection: context(),
        disabled: false,
        onRefresh: async () => {},
        onMutationPending: () => {},
        onSelectionChange,
      });
      return tree;
    }
    renderToStaticMarkup(<Capture />);
    const labels = elements(tree).filter(
      (node) => node.type === 'label' && node.props.className === 'case-group',
    );
    const change = (id: string, checked: boolean) => {
      const label = labels.find((node) => renderToStaticMarkup(node).includes(`Party ${id}`))!;
      const input = elements(label).find((node) => node.type === 'input')!;
      (input.props.onChange as (event: { target: { checked: boolean } }) => void)({
        target: { checked },
      });
    };
    change('G2', true);
    expect(onSelectionChange).toHaveBeenLastCalledWith(['G1', 'G2']);
    change('G1', false);
    expect(onSelectionChange).toHaveBeenLastCalledWith(['G2']);
    expect(fetch).not.toHaveBeenCalled();
  });

  it('hands off the source selection, separately from the case group-linking controls, with no source calls', () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    const props = caseProps();
    const captured = captureCase(props);
    const manage = captured.nodes.find(
      (node) =>
        node.type === 'button' &&
        renderToStaticMarkup(node).includes('Manage passenger requirements'),
    )!;
    const panel = captured.nodes.find((node) => node.type === RecoveryJobPanel)!;
    expect(panel.props.initialSelection).toEqual(context());
    expect(props.onManageData).not.toHaveBeenCalled();
    (manage.props.onClick as () => void)();
    expect(props.onManageData).toHaveBeenLastCalledWith(context());
    const selected = ['G2'];
    (panel.props.onSelectionChange as (ids: string[]) => void)(selected);
    (manage.props.onClick as () => void)();
    expect(props.onManageData).toHaveBeenLastCalledWith(context(['G2']));
    expect((props.onManageData as ReturnType<typeof vi.fn>).mock.calls[1][0].groupIds).not.toBe(
      selected,
    );
    expect(fetch).not.toHaveBeenCalled();
    expect(props.onApplied).not.toHaveBeenCalled();
  });

  it('does not hand off a valid subset from a mixed stale initial selection', () => {
    const props = { ...caseProps(), initialSelection: context(['G1', 'G3']) };
    const captured = captureCase(props);
    const manage = captured.nodes.find(
      (node) =>
        node.type === 'button' &&
        renderToStaticMarkup(node).includes('Manage passenger requirements'),
    )!;
    (manage.props.onClick as () => void)();
    expect(props.onManageData).toHaveBeenCalledWith(context([]));
  });

  it.each([
    { ...context(), scenarioId: 'OLD' },
    { ...context(), incidentId: 'CASE-2' },
  ])('renders only a stale-context guard for a mismatched case entry', (initialSelection) => {
    const captured = captureCase({ ...caseProps(), initialSelection });
    expect(captured.html).toContain('The operation or recovery case changed');
    expect(captured.html).not.toContain('Search provider alternatives');
    expect(captured.html).not.toContain('Manage passenger requirements');
    expect(captured.nodes.some((node) => node.type === RecoveryJobPanel)).toBe(false);
  });

  it('keeps a pending panel disabled while preserving its explicit selection', () => {
    const html = panelHtml(fixture(), context(), true);
    expect(html).toContain('<fieldset class="case-fieldset" disabled="">');
    expect(checkboxFor(html, 'G1')).toContain('checked=""');
    expect(checkboxFor(html, 'G2')).not.toContain('checked=""');
    expect(recoveryDataReturnIssue(fixture(), context(), true)).toContain('pending submission');
  });

  it('keeps the original unknown request key, exact group scope and allowance after another navigation selection', async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(new Response('upstream unavailable', { status: 502 }))
      .mockResolvedValueOnce(Response.json({ replayed: true, job: { id: 'JOB1' } }));
    vi.stubGlobal('fetch', fetch);
    const world = fixture();
    const selected = initialRecoverySelection(world, world.incidents![0], context());
    let unknown: TransportError | undefined;
    try {
      await api.startRecoveryJob(world, 'CASE-1', {
        group_ids: selected.groupIds,
        objective: 'balanced',
        modes: ['flight'],
        max_provider_calls: 8,
        instructions: '',
      });
    } catch (error) {
      expect(error).toBeInstanceOf(TransportError);
      unknown = error as TransportError;
    }
    selected.groupIds.push('G2');
    world.version += 1;
    expect(
      initialRecoverySelection(world, world.incidents![1], {
        ...context(['G3']),
        incidentId: 'CASE-2',
      }).groupIds,
    ).toEqual(['G3']);
    expect(recoveryDataReturnIssue(world, context(), true)).toContain('pending submission');
    const replay = await unknown!.retry<RecoveryJobResponse>();
    expect(replay.replayed).toBe(true);
    expect(fetch).toHaveBeenCalledTimes(2);
    const [first, second] = fetch.mock.calls;
    expect(second[0]).toBe(first[0]);
    expect(second[1].body).toBe(first[1].body);
    expect(JSON.parse(second[1].body)).toMatchObject({
      group_ids: ['G1'],
      expected_version: 5,
      max_provider_calls: 8,
    });
  });
});
