import { ArrowUpRight, MapPin } from 'lucide-react';
import type { Airport, SceneMode, World } from './types';

export default function WorkspaceSummary({
  world,
  mode,
  airport,
  onReview,
}: {
  world: World;
  mode: SceneMode;
  airport?: Airport;
  onReview: () => void;
}) {
  const waiting = world.groups
    .filter((group) => ['waiting', 'affected', 'exception'].includes(group.status))
    .reduce((sum, group) => sum + group.size, 0);
  const offered = world.groups
    .filter((group) => group.status === 'offered')
    .reduce((sum, group) => sum + group.size, 0);
  const confirmed = world.groups
    .filter((group) => group.status === 'confirmed')
    .reduce((sum, group) => sum + group.size, 0);
  return (
    <div className="workspace-summary">
      <div className="workspace-title">
        <span className="eyebrow">
          <MapPin size={12} />{' '}
          {mode === 'region' ? 'Connected airports' : (airport?.name ?? 'Simulation workspace')}
        </span>
        <h1>
          {!world.airports.length
            ? 'What happens next?'
            : mode === 'region'
              ? 'One connected network.'
              : mode === 'passenger'
                ? 'Every journey matters.'
                : 'A way forward for everyone.'}
        </h1>
      </div>
      {world.groups.length > 0 && (
        <button
          className="recovery-snapshot"
          onClick={onReview}
          aria-label="Review recovery progress"
        >
          <span>
            <strong>{waiting}</strong>
            <small>Need a plan</small>
          </span>
          <span>
            <strong>{offered}</strong>
            <small>Offered</small>
          </span>
          <span>
            <strong className="text-mint">{confirmed}</strong>
            <small>Confirmed</small>
          </span>
          <ArrowUpRight size={15} />
        </button>
      )}
    </div>
  );
}
