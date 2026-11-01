import { useRef, useState } from 'react';
import { ArrowRight, FlaskConical, Loader2, Users } from 'lucide-react';
import type { DemoCaseInput } from './types';
import { Modal, Tag } from './ui';
import './demoSetup.css';

export type DemoCaseDraft = {
  origin: string;
  destination: string;
  passengers: string;
  group_size: string;
  group_budget_eur: string;
  ready_at: string;
  deadline: string;
  description: string;
};

export function initialDemoCaseDraft(now = new Date()): DemoCaseDraft {
  const ready = new Date(now);
  ready.setUTCDate(ready.getUTCDate() + 1);
  ready.setUTCHours(8, 0, 0, 0);
  const deadline = new Date(ready);
  deadline.setUTCDate(deadline.getUTCDate() + 1);
  deadline.setUTCHours(22, 0, 0, 0);
  return {
    origin: 'LHR',
    destination: 'OTP',
    passengers: '2',
    group_size: '2',
    group_budget_eur: '2000',
    ready_at: ready.toISOString().slice(0, 16),
    deadline: deadline.toISOString().slice(0, 16),
    description:
      'Two fictional adults need an alternative journey from London Heathrow to Bucharest.',
  };
}

export function demoCaseInput(draft: DemoCaseDraft, now = Date.now()): DemoCaseInput {
  const origin = draft.origin.trim().toUpperCase();
  const destination = draft.destination.trim().toUpperCase();
  if (!/^[A-Z]{3,4}$/.test(origin) || !/^[A-Z]{3,4}$/.test(destination))
    throw new Error('Enter a real IATA or ICAO airport code for both locations.');
  if (origin === destination) throw new Error('Choose different origin and destination airports.');
  const passengers = Number(draft.passengers);
  const groupSize = Number(draft.group_size);
  const budget = Number(draft.group_budget_eur);
  if (!Number.isInteger(passengers) || passengers < 1 || passengers > 500)
    throw new Error('Choose between 1 and 500 fictional adults.');
  if (!Number.isInteger(groupSize) || groupSize < 1 || groupSize > 12 || groupSize > passengers)
    throw new Error('People per group must be between 1 and 12, up to the passenger count.');
  if (!draft.group_budget_eur.trim() || !Number.isFinite(budget) || budget < 0 || budget > 1000000)
    throw new Error('Enter a group budget between 0 and 1,000,000 EUR.');
  const ready = new Date(`${draft.ready_at}Z`);
  const deadline = new Date(`${draft.deadline}Z`);
  if (
    !Number.isFinite(ready.getTime()) ||
    !Number.isFinite(deadline.getTime()) ||
    ready.toISOString().slice(0, 16) !== draft.ready_at ||
    deadline.toISOString().slice(0, 16) !== draft.deadline
  )
    throw new Error('Enter valid readiness and arrival times in UTC.');
  if (ready.getTime() <= now) throw new Error('Passenger readiness must be in the future.');
  if (deadline <= ready) throw new Error('The arrival deadline must be after passenger readiness.');
  if (!draft.description.trim()) throw new Error('Add a short description for this test case.');
  return {
    origin,
    destination,
    passengers,
    group_size: groupSize,
    group_budget_eur: budget,
    ready_at: ready.toISOString(),
    deadline: deadline.toISOString(),
    description: draft.description.trim(),
  };
}

export function DemoNotice({ onSetup }: { onSetup: () => void }) {
  return (
    <section className="demo-notice" aria-label="Simulation with real supplier data">
      <FlaskConical size={18} aria-hidden="true" />
      <div>
        <strong>Fictional passengers · real supplier data</strong>
        <p>
          Test recovery with real flight, hotel and transport offers. Searches use connected
          providers; no bookings or passenger messages are sent.
        </p>
      </div>
      <button type="button" className="secondary-button" onClick={onSetup}>
        New test case <ArrowRight size={14} aria-hidden="true" />
      </button>
    </section>
  );
}

export default function DemoSetupDialog({
  busy,
  onCreate,
  onClose,
}: {
  busy: boolean;
  onCreate: (input: DemoCaseInput) => Promise<unknown>;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(initialDemoCaseDraft);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const inFlight = useRef(false);
  const locked = busy || submitting;
  function update<K extends keyof DemoCaseDraft>(key: K, value: DemoCaseDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setError('');
  }
  return (
    <Modal
      title="New test case"
      onClose={() => {
        if (!locked) onClose();
      }}
    >
      <Tag tone="cyan">SIMULATION · LIVE DATA</Tag>
      <p className="modal-copy">
        Create fictional adult passengers at real airports. Their dates, group size and budget
        define the search; flight seats, hotel rooms and transport prices come from real suppliers.
      </p>
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (locked || inFlight.current) return;
          setError('');
          try {
            const input = demoCaseInput(draft);
            inFlight.current = true;
            setSubmitting(true);
            await onCreate(input);
          } catch (cause) {
            setError(
              cause instanceof Error ? cause.message : 'The test case could not be created.',
            );
          } finally {
            inFlight.current = false;
            setSubmitting(false);
          }
        }}
      >
        <fieldset className="demo-case-fields" disabled={locked}>
          <div className="demo-case-grid">
            <label className="field">
              Origin airport
              <input
                data-autofocus
                value={draft.origin}
                onChange={(event) => update('origin', event.target.value)}
                pattern="[A-Za-z]{3,4}"
                minLength={3}
                maxLength={4}
                required
                autoComplete="off"
              />
            </label>
            <label className="field">
              Destination airport
              <input
                value={draft.destination}
                onChange={(event) => update('destination', event.target.value)}
                pattern="[A-Za-z]{3,4}"
                minLength={3}
                maxLength={4}
                required
                autoComplete="off"
              />
            </label>
            <label className="field">
              Fictional adults
              <input
                type="number"
                min={1}
                max={500}
                step={1}
                value={draft.passengers}
                onChange={(event) => update('passengers', event.target.value)}
                required
              />
            </label>
            <label className="field">
              People per group
              <input
                type="number"
                min={1}
                max={12}
                step={1}
                value={draft.group_size}
                onChange={(event) => update('group_size', event.target.value)}
                required
              />
            </label>
            <label className="field">
              Ready to travel · UTC
              <input
                type="datetime-local"
                value={draft.ready_at}
                onChange={(event) => update('ready_at', event.target.value)}
                required
              />
            </label>
            <label className="field">
              Arrival deadline · UTC
              <input
                type="datetime-local"
                value={draft.deadline}
                onChange={(event) => update('deadline', event.target.value)}
                required
              />
            </label>
          </div>
          <label className="field">
            Budget per group · EUR
            <input
              type="number"
              min={0}
              max={1000000}
              step="0.01"
              value={draft.group_budget_eur}
              onChange={(event) => update('group_budget_eur', event.target.value)}
              required
            />
          </label>
          <label className="field">
            Scenario description
            <textarea
              rows={3}
              value={draft.description}
              onChange={(event) => update('description', event.target.value)}
              maxLength={1000}
              required
            />
          </label>
          <p className="field-hint">
            Times are UTC. All passengers are adults with standard accessibility requirements.
            Creating a case does not search suppliers; you choose when to start the search.
          </p>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <div className="modal-actions">
            <button type="button" className="secondary-button" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="primary-button">
              {submitting ? (
                <Loader2 size={16} className="spin" aria-hidden="true" />
              ) : (
                <Users size={16} aria-hidden="true" />
              )}
              {submitting ? 'Creating test case…' : 'Create test passengers'}
              <ArrowRight size={15} aria-hidden="true" />
            </button>
          </div>
        </fieldset>
      </form>
    </Modal>
  );
}
