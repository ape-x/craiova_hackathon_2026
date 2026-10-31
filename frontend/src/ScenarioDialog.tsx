import { useState } from 'react';
import { ArrowRight, BusFront, Plane, SlidersHorizontal, TriangleAlert } from 'lucide-react';
import type { EventType, ScenarioChange, World } from './types';
import { Modal, Tag } from './ui';
import { diversionChoice } from './scenarioSelection';
import { isAirportNode } from './locationNodes';
const options: {
  type: EventType;
  name: string;
  hint: string;
  scope: 'resource' | 'flight' | 'airport' | 'group' | 'world';
  input?: 'number' | 'boolean' | 'airport';
}[] = [
  {
    type: 'resource_unavailable',
    name: 'Resource unavailable',
    hint: 'Invalidate only the itineraries that depend on this resource.',
    scope: 'resource',
  },
  {
    type: 'flight_cancelled',
    name: 'Cancel a flight',
    hint: 'Remove a flight from the scenario and recover affected passengers.',
    scope: 'flight',
  },
  {
    type: 'airport_closed',
    name: 'Airport unavailable',
    hint: 'Close a scenario airport. Inbound passengers need an eligible alternate.',
    scope: 'airport',
  },
  {
    type: 'flight_diverted',
    name: 'Divert a flight',
    hint: 'Simulate an arrival at an eligible alternate; downstream transfers are recalculated.',
    scope: 'flight',
    input: 'airport',
  },
  {
    type: 'resource_capacity',
    name: 'Change capacity',
    hint: 'Set a new capacity; hotel capacity is measured in rooms.',
    scope: 'resource',
    input: 'number',
  },
  {
    type: 'budget_changed',
    name: 'Change total budget',
    hint: 'Set the hard global budget for this scenario, in euros.',
    scope: 'world',
    input: 'number',
  },
  {
    type: 'group_deadline',
    name: 'Arrival deadline',
    hint: 'Minutes from midnight of the scenario day. 1320 = 22:00.',
    scope: 'group',
    input: 'number',
  },
  {
    type: 'group_budget',
    name: 'Passenger group budget',
    hint: 'Set the maximum total itinerary cost for this whole group.',
    scope: 'group',
    input: 'number',
  },
  {
    type: 'group_accessibility',
    name: 'Accessibility requirement',
    hint: 'All legs must support the group’s accessibility requirement.',
    scope: 'group',
    input: 'boolean',
  },
  {
    type: 'resource_accessibility',
    name: 'Resource accessibility',
    hint: 'Change whether this resource supports accessible travel.',
    scope: 'resource',
    input: 'boolean',
  },
  {
    type: 'resource_restored',
    name: 'Restore a resource',
    hint: 'Make the resource available for future offers again.',
    scope: 'resource',
  },
];
export default function ScenarioDialog({
  world,
  selectedId,
  busy,
  onClose,
  onApply,
}: {
  world: World;
  selectedId: string | null;
  busy: boolean;
  onClose: () => void;
  onApply: (change: ScenarioChange, replan: boolean) => Promise<void>;
}) {
  const [type, setType] = useState<EventType>('resource_unavailable');
  const [target, setTarget] = useState(selectedId ?? 'B12');
  const [value, setValue] = useState('10');
  const [replan, setReplan] = useState(true);
  const [error, setError] = useState('');
  const option = options.find((item) => item.type === type)!;
  const targets =
    option.scope === 'airport'
      ? world.airports
          .filter((item) => isAirportNode(item) && ['CRA', 'OTP', 'SBZ'].includes(item.code))
          .map((item) => ({ id: item.id, label: `${item.code} · ${item.name}` }))
      : option.scope === 'group'
        ? world.groups.map((item) => ({
            id: item.id,
            label: `${item.id} · ${item.name} (${item.size})`,
          }))
        : world.resources
            .filter((item) => option.scope !== 'flight' || item.kind === 'flight')
            .map((item) => ({ id: item.id, label: `${item.id} · ${item.name}` }));
  const resolvedTarget = targets.some((item) => item.id === target)
    ? target
    : (targets[0]?.id ?? '');
  const alternate = diversionChoice(world.airports, value);
  const noAlternate = option.input === 'airport' && !alternate.selectedId;
  const changeType = (next: EventType) => {
    setType(next);
    setValue(
      next === 'group_deadline'
        ? '1320'
        : next === 'budget_changed'
          ? '15000'
          : next === 'group_budget'
            ? '800'
            : next === 'flight_diverted'
              ? 'OTP'
              : next.endsWith('accessibility')
                ? 'true'
                : '10',
    );
  };
  return (
    <Modal title="Scenario controls" onClose={onClose}>
      <div className="scenario-intro">
        <SlidersHorizontal size={22} />
        <div>
          <p>Change the conditions. Keep the world consistent.</p>
          <span>Any value you choose is validated against the current state.</span>
        </div>
        <Tag tone="amber">SIMULATION</Tag>
      </div>
      <div className="scenario-presets">
        <button
          onClick={() => {
            changeType('resource_unavailable');
            setTarget('B12');
          }}
        >
          <BusFront size={18} />
          <span>Bus breakdown</span>
        </button>
        <button
          onClick={() => {
            changeType('airport_closed');
            setTarget('CRA');
          }}
        >
          <Plane size={18} />
          <span>Airport closure</span>
        </button>
        <button onClick={() => changeType('resource_capacity')}>
          <UsersIcon />
          <span>Capacity change</span>
        </button>
      </div>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (noAlternate) return;
          setError('');
          try {
            await onApply(
              {
                type,
                target_id: option.scope === 'world' ? null : resolvedTarget,
                value:
                  option.input === 'number'
                    ? Number(value)
                    : option.input === 'boolean'
                      ? value === 'true'
                      : option.input === 'airport'
                        ? alternate.selectedId
                        : null,
              },
              replan,
            );
            onClose();
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : 'Change failed');
          }
        }}
      >
        <label className="field">
          Change type
          <select value={type} onChange={(event) => changeType(event.target.value as EventType)}>
            {options.map((item) => (
              <option value={item.type} key={item.type}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        {option.scope !== 'world' && (
          <label className="field">
            Target
            <select value={resolvedTarget} onChange={(event) => setTarget(event.target.value)}>
              {targets.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
        )}
        {option.input === 'number' && (
          <label className="field">
            New value
            <input
              type="number"
              required
              min="0"
              max={type === 'budget_changed' || type === 'group_budget' ? 1000000 : 10080}
              step={type.includes('budget') ? '0.01' : '1'}
              value={value}
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
        )}
        {option.input === 'boolean' && (
          <label className="field">
            Accessibility
            <select value={value} onChange={(event) => setValue(event.target.value)}>
              <option value="true">Required / supported</option>
              <option value="false">Not required / not supported</option>
            </select>
          </label>
        )}
        {option.input === 'airport' && (
          <label className="field">
            Alternate airport
            <select
              value={alternate.selectedId}
              onChange={(event) => setValue(event.target.value)}
              disabled={noAlternate}
            >
              {noAlternate && <option value="">No eligible airport available</option>}
              {alternate.airports.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.code}
                </option>
              ))}
            </select>
            {noAlternate && (
              <span className="field-hint">
                Make an eligible airport available before diverting this flight.
              </span>
            )}
          </label>
        )}
        <p className="field-hint">{option.hint}</p>
        <label className="check-field">
          <input
            type="checkbox"
            checked={replan}
            onChange={(event) => setReplan(event.target.checked)}
          />{' '}
          Recalculate affected journeys after applying
        </label>
        {error && (
          <div className="form-error" role="alert">
            <TriangleAlert size={16} />
            {error}
          </div>
        )}
        <div className="modal-actions">
          <span>Applies to version {world.version}</span>
          <button className="primary-button" disabled={busy || noAlternate} type="submit">
            {busy ? 'Applying…' : 'Apply condition'}
            <ArrowRight size={15} />
          </button>
        </div>
      </form>
    </Modal>
  );
}
function UsersIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <circle cx="9" cy="7" r="3" />
      <path d="M3 21v-3a6 6 0 0 1 12 0v3M17 4a3 3 0 0 1 0 6M20 21v-3a6 6 0 0 0-3-5" />
    </svg>
  );
}
