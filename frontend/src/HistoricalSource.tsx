import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, FileClock, Loader2 } from 'lucide-react';
import { ApiError, formatCost } from './api';
import { archiveReasonLabel, loadInventoryRecord } from './inventoryHistory';
import { PriceEvidenceDetails, privateTransfer } from './priceEvidence';
import { freshnessTime, sourceInstant } from './resourceFreshness';
import { locationName } from './resourceLabels';
import type { InventoryResourceRecord } from './types';
import './inventoryHistory.css';

function timestamp(value: unknown): string {
  const instant = sourceInstant(value);
  return instant === null ? 'Not supplied' : freshnessTime(instant);
}

export function SourceRecordDetails({
  record,
  epoch,
}: {
  record: InventoryResourceRecord;
  epoch: string;
}) {
  const { resource } = record;
  const names = { airports: record.locations };
  const base = sourceInstant(epoch);
  const time = (minutes: number) =>
    base === null ? 'Time unavailable' : freshnessTime(base + minutes * 60_000);
  const provenance = resource.provenance ?? {};
  const quoteTime = provenance.quoted_at ?? provenance.source_quoted_at;
  const total = resource.budget_total_cost ?? resource.quote_total_cost;
  return (
    <section className="source-record" aria-label={`Saved source for ${resource.id}`}>
      <div className="source-record-heading">
        <FileClock size={16} aria-hidden="true" />
        <strong>{resource.name}</strong>
        <span>{record.archived ? 'Archived' : 'Recorded service'}</span>
      </div>
      <p className="source-record-notice">
        {record.archived
          ? 'Historical evidence only. This service is outside active inventory and cannot be used for a new plan.'
          : 'Saved service data. This lookup does not check current supplier availability or create a booking.'}
      </p>
      <dl>
        <div>
          <dt>Source</dt>
          <dd>{resource.source}</dd>
        </div>
        <div>
          <dt>Service reference</dt>
          <dd>{resource.id}</dd>
        </div>
        {resource.external_offer_id && (
          <div>
            <dt>Supplier reference</dt>
            <dd>{resource.external_offer_id}</dd>
          </div>
        )}
        <div>
          <dt>Recorded route</dt>
          <dd>
            {locationName(names, resource.origin)} → {locationName(names, resource.destination)}
          </dd>
        </div>
        <div>
          <dt>{resource.kind === 'hotel' ? 'Recorded stay' : 'Recorded schedule'}</dt>
          <dd>
            {time(resource.departure)} – {time(resource.arrival)}
          </dd>
        </div>
        <div>
          <dt>Source quoted at</dt>
          <dd>{timestamp(quoteTime)}</dd>
        </div>
        <div>
          <dt>Supplier validity</dt>
          <dd>{timestamp(resource.valid_until)}</dd>
        </div>
        {resource.quote_freshness && (
          <div>
            <dt>Received by the application</dt>
            <dd>
              {timestamp(resource.quote_freshness.received_at)} · the local freshness window is not
              a supplier hold.
            </dd>
          </div>
        )}
        {!privateTransfer(resource) && total != null && Number.isFinite(total) && (
          <div>
            <dt>Recorded planning total</dt>
            <dd>{formatCost(total)}</dd>
          </div>
        )}
        {record.archived && (
          <div>
            <dt>Archived</dt>
            <dd>
              {timestamp(record.archived_at)}
              {record.archived_version !== null
                ? ` · operation version ${record.archived_version}`
                : ''}
            </dd>
          </div>
        )}
        {record.archive_reason && (
          <div>
            <dt>Archive reason</dt>
            <dd>{archiveReasonLabel(record.archive_reason)}</dd>
          </div>
        )}
      </dl>
      <PriceEvidenceDetails evidence={resource.price_evidence} />
    </section>
  );
}

export default function HistoricalSource({
  scenarioId,
  resourceId,
  epoch,
}: {
  scenarioId: string;
  resourceId: string;
  epoch: string;
}) {
  // A changed operation/reference gets fresh state before paint, not an effect reset.
  return (
    <SourceLookup
      key={`${scenarioId}:${resourceId}`}
      scenarioId={scenarioId}
      resourceId={resourceId}
      epoch={epoch}
    />
  );
}

function SourceLookup({
  scenarioId,
  resourceId,
  epoch,
}: {
  scenarioId: string;
  resourceId: string;
  epoch: string;
}) {
  const contentId = useId();
  const [open, setOpen] = useState(false);
  const [record, setRecord] = useState<InventoryResourceRecord | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const current = useRef<AbortController | null>(null);
  useEffect(() => () => current.current?.abort(), []);
  const toggle = async () => {
    current.current?.abort();
    current.current = null;
    setRecord(null);
    setError('');
    setLoading(false);
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    setLoading(true);
    const controller = new AbortController();
    current.current = controller;
    try {
      const next = await loadInventoryRecord(scenarioId, resourceId, controller.signal);
      if (!controller.signal.aborted && current.current === controller) setRecord(next);
    } catch (cause) {
      if (!controller.signal.aborted && current.current === controller) {
        setError(
          cause instanceof ApiError && cause.status === 404
            ? 'No saved source record is available for this service.'
            : cause instanceof ApiError && cause.status === 409
              ? 'The operation has changed. Reopen its recovery case to inspect the current references.'
              : 'The saved source record could not be loaded. Close and reopen this reference to retry.',
        );
      }
    } finally {
      if (!controller.signal.aborted && current.current === controller) setLoading(false);
    }
  };
  return (
    <div className="historical-source">
      <button
        type="button"
        className="source-record-toggle"
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => void toggle()}
      >
        <ChevronDown size={14} aria-hidden="true" />
        <span>{resourceId}</span>
        <span className="source-record-action">{open ? 'Close source' : 'View source'}</span>
      </button>
      <div id={contentId} hidden={!open}>
        {loading && (
          <p role="status">
            <Loader2 size={14} className="spin" aria-hidden="true" /> Loading saved source…
          </p>
        )}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {record && <SourceRecordDetails record={record} epoch={epoch} />}
      </div>
    </div>
  );
}
