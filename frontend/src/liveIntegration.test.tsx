import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import Inspector from './Inspector';
import type { Offer, World } from './types';

function operation(mode: World['mode'], status: Offer['status']): World {
  return {
    scenario_id: 'integration-fixture',
    version: 3,
    epoch: '2026-10-31T00:00:00+02:00',
    sim_time: 600,
    mode,
    currency: 'EUR',
    budget: 1000,
    airports: [],
    resources: [
      {
        id: 'BUS-1',
        kind: 'bus',
        name: 'Recovery coach',
        origin: 'CRA',
        destination: 'OTP',
        departure: 620,
        arrival: 800,
        capacity: 40,
        accessible: true,
        available: true,
        cost_per_person: 20,
        room_capacity: 1,
        vehicle_id: null,
        status: 'available',
        source: mode === 'live' ? 'operator_verified' : 'synthetic',
      },
    ],
    groups: [
      {
        id: 'FAMILY-1',
        name: 'Test family',
        size: 4,
        members: ['A', 'B', 'C', 'D'],
        origin: 'CRA',
        destination: 'OTP',
        deadline: 900,
        accessible: false,
        budget: 1000,
        original_flight_id: 'ORIGINAL',
        status: status === 'confirmed' ? 'confirmed' : 'offered',
        token: 'fixture-only',
        priority: 0,
        source: mode === 'live' ? 'manifest_import' : 'synthetic',
      },
    ],
    offers: [
      {
        id: 'OFFER-1',
        group_id: 'FAMILY-1',
        status,
        expires_at: '2099-01-01T00:00:00Z',
        created_version: 2,
        confirmed_at: null,
        reason: null,
        itinerary: {
          arrival: 800,
          total_cost: 80,
          legs: [
            {
              resource_id: 'BUS-1',
              kind: 'bus',
              origin: 'CRA',
              destination: 'OTP',
              departure: 620,
              arrival: 800,
              cost: 80,
            },
          ],
        },
      },
    ],
    reservations: [],
    events: [],
    decisions: [],
    observations: [],
    stats: {},
    provider_status: {},
  };
}
function render(world: World, selectedId: string) {
  return renderToStaticMarkup(
    <Inspector
      world={world}
      selectedId={selectedId}
      onSelect={() => {}}
      onClose={() => {}}
      onReplan={() => {}}
      onScenario={() => {}}
      busy={false}
      showLog={false}
      config={{ public_base_url: 'https://example.test' }}
    />,
  );
}

describe('integrated live and simulation presentation', () => {
  it('never promotes a simulated confirmation or resource to a supplier-verified booking', () => {
    const world = operation('simulation', 'confirmed');
    const journey = render(world, 'FAMILY-1');
    expect(journey).toContain('Itinerary confirmed in simulation');
    expect(journey).not.toContain('Supplier bookings confirmed');
    const coach = render(world, 'BUS-1');
    expect(coach).toContain('SIMULATED RESOURCE');
    expect(coach).toContain('Available');
    expect(coach).not.toContain('Verified');
  });
  it('shows accepted live journeys as pending bookings, and only confirmed journeys as supplier-confirmed', () => {
    const accepted = render(operation('live', 'accepted'), 'FAMILY-1');
    expect(accepted).toContain('Passenger accepted · bookings pending');
    expect(accepted).not.toContain('Supplier bookings confirmed');
    expect(render(operation('live', 'confirmed'), 'FAMILY-1')).toContain(
      'Supplier bookings confirmed',
    );
  });
  it('retains live proposed and accepted parties in resource dependencies', () => {
    for (const status of ['proposed', 'accepted'] as const) {
      const html = render(operation('live', status), 'BUS-1');
      expect(html).toContain('Dependent groups');
      expect(html).toContain('Test family');
      expect(html).toContain('4 pax');
    }
    expect(render(operation('live', 'declined'), 'BUS-1')).not.toContain('Dependent groups');
  });
});
