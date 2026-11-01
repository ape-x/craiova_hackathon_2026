import { ArrowRight, FileText } from 'lucide-react';
import { Modal, Tag } from './ui';
import {
  voiceReviewCanCorrect,
  voiceReviewDraftIssue,
  voiceReviewStatusLabel,
} from './voiceRequestReview';
import type { VoiceRequestReview, VoiceReviewDraftContext } from './voiceRequestReview';
import './voiceRequestReview.css';

export default function VoiceRequestReviewDialog({
  review,
  context,
  onClose,
  onCopyToDraft,
}: {
  review: VoiceRequestReview;
  context: VoiceReviewDraftContext;
  onClose: () => void;
  onCopyToDraft: () => void;
}) {
  const issue = voiceReviewDraftIssue(review, context);
  const canCorrect = voiceReviewCanCorrect(review) && review.scenarioId === context.scenarioId;
  return (
    <Modal title="Review voice request" onClose={onClose}>
      <div className="voice-review-intro">
        <FileText size={18} aria-hidden="true" />
        <p>The latest request sent by voice, exactly as received by the application.</p>
      </div>
      <section className="voice-review-section" aria-label="Original voice request">
        <h3>Original request</h3>
        <p className="voice-review-original">{review.text}</p>
      </section>
      <section className="voice-review-section" aria-label="Recorded voice request outcome">
        <div className="voice-review-result-heading">
          <h3>Recorded outcome</h3>
          <Tag tone={review.status === 'unknown' || review.status === 'pending' ? 'amber' : ''}>
            {voiceReviewStatusLabel[review.status]}
          </Tag>
        </div>
        <p className="voice-review-result" role="status">
          {review.detail}
        </p>
      </section>
      {canCorrect && (
        <p className="field-hint">
          Copy the original into the command draft, edit it, then explicitly send a new request.
          Copying does not submit anything or change the original record.
        </p>
      )}
      {issue && (
        <p className="voice-review-reason" id="voice-review-draft-issue">
          {issue}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" className="secondary-button" onClick={onClose}>
          Back to workspace
        </button>
        {canCorrect && (
          <button
            type="button"
            className="primary-button"
            disabled={Boolean(issue)}
            aria-describedby={issue ? 'voice-review-draft-issue' : undefined}
            onClick={onCopyToDraft}
          >
            Copy to command draft
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        )}
      </div>
    </Modal>
  );
}
