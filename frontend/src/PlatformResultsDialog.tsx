import type { PlatformActionResult } from './types';
import { Modal, Tag } from './ui';

const hiddenField = /(^|_)(token|secret|password|authorization|api_key)(_|$)/i;
export function visibleResultEntries(value: Record<string, unknown>) {
  return Object.entries(value).filter(([key]) => key !== 'world' && !hiddenField.test(key));
}
function label(key: string) {
  return key.replaceAll('_', ' ').replaceAll('.', ' ');
}
function ResultData({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) return <span>—</span>;
  if (typeof value !== 'object')
    return <span style={{ overflowWrap: 'anywhere' }}>{String(value)}</span>;
  if (depth >= 5) return <span>Additional details are available in the operation record.</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ol>
        {value.slice(0, 50).map((item, index) => (
          <li key={index}>
            <ResultData value={item} depth={depth + 1} />
          </li>
        ))}
        {value.length > 50 && <li>{value.length - 50} additional records.</li>}
      </ol>
    ) : (
      <span>No records returned.</span>
    );
  return (
    <dl>
      {visibleResultEntries(value as Record<string, unknown>).map(([key, item]) => (
        <div key={key} style={{ marginBottom: 8 }}>
          <dt>
            <strong>{label(key)}</strong>
          </dt>
          <dd style={{ marginLeft: 12 }}>
            <ResultData value={item} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

export default function PlatformResultsDialog({
  results,
  onClose,
}: {
  results: PlatformActionResult[];
  onClose: () => void;
}) {
  return (
    <Modal title="Operation details" onClose={onClose} wide>
      {results.map((item, index) => (
        <section className="settings-section" key={`${item.name}-${index}`}>
          <div className="section-label">
            <strong>{label(item.name)}</strong>
            <Tag tone={item.status === 'succeeded' ? 'mint' : 'amber'}>
              {item.status === 'needs_input' ? 'More details needed' : item.status}
            </Tag>
          </div>
          {item.summary && <p>{item.summary}</p>}
          {item.error && <p className="form-error">{item.error}</p>}
          {!!item.missing_fields?.length && (
            <p>Provide: {item.missing_fields.map(label).join(', ')}.</p>
          )}
          {item.result !== undefined && <ResultData value={item.result} />}
        </section>
      ))}
      {!results.length && <p>No action results are available.</p>}
    </Modal>
  );
}
