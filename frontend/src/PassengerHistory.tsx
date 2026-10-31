import { ItineraryView } from './Inspector';
import { costPresentation, EstimatedCostNote } from './priceEvidence';
import type { Airport, Offer, Resource } from './types';
import './inventoryHistory.css';

export default function PassengerHistory({
  offer,
  epoch,
  resources = [],
  airports = [],
  archivedResourceIds = [],
}: {
  offer: Offer | null;
  epoch: string;
  resources?: Resource[];
  airports?: Airport[];
  archivedResourceIds?: string[];
}) {
  if (!offer || ['held', 'proposed', 'accepted', 'confirmed'].includes(offer.status)) return null;
  const references = new Set(offer.itinerary.legs.map((leg) => leg.resource_id));
  // Defence in depth: only source details belonging to this recorded itinerary.
  const sources = resources.filter((resource) => references.has(resource.id));
  const missingSources = references.size - new Set(sources.map((resource) => resource.id)).size;
  const archiveCount = new Set(archivedResourceIds.filter((id) => references.has(id))).size;
  return (
    <details className="passenger-journey-history">
      <summary>Previous itinerary · historical record</summary>
      <p>
        This {offer.status} proposal is kept for reference. It is not a current offer or travel
        confirmation and cannot be accepted here.
      </p>
      {offer.reason && <p>{offer.reason}</p>}
      {missingSources > 0 && (
        <p className="passenger-history-warning" role="note">
          Source evidence is unavailable for {missingSources} recorded service
          {missingSources === 1 ? '' : 's'}. The saved itinerary is shown below; missing supplier
          details have not been reconstructed.
        </p>
      )}
      {archiveCount > 0 && (
        <p>
          {archiveCount} service record{archiveCount === 1 ? '' : 's'} preserved in the source
          archive.
        </p>
      )}
      <ItineraryView offer={offer} world={{ mode: 'live', epoch, resources: sources, airports }} />
      {sources.length > 0 && (
        <ul className="passenger-history-sources" aria-label="Recorded itinerary sources">
          {sources.map((resource) => (
            <li key={resource.id}>
              {resource.name} · {resource.source}
            </li>
          ))}
        </ul>
      )}
      <p>
        Recorded {costPresentation(offer.itinerary).estimated ? 'estimated total' : 'total'}:{' '}
        {costPresentation(offer.itinerary).amount}
      </p>
      <EstimatedCostNote value={offer.itinerary} />
    </details>
  );
}
