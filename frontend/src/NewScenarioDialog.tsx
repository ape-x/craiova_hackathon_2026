import { ArrowRight, FileText, Mic, Sparkles } from 'lucide-react';
import { Modal, Tag } from './ui';

export default function NewScenarioDialog({
  voiceState,
  voiceAvailable,
  busy,
  onClose,
  onVoice,
  onText,
}: {
  voiceState: string;
  voiceAvailable: boolean;
  busy: boolean;
  onClose: () => void;
  onVoice: () => void;
  onText: () => void;
}) {
  const waiting = busy || ['connecting', 'working', 'closing'].includes(voiceState);
  const listening = voiceState === 'listening';
  return (
    <Modal title="Create a new scenario" onClose={onClose}>
      <Tag tone="cyan">BUILD IT BY VOICE</Tag>
      <p className="modal-copy">
        Describe a fresh situation in Romanian or English. The AI asks for missing details, then
        builds the scene and compares recovery options.
      </p>
      <div className="demo-steps">
        <div>
          <span>01</span>
          <h3>Ask for a new scenario</h3>
          <p>Begin with “Creează un scenariu nou…” or “Create a new scenario…”</p>
        </div>
        <div>
          <span>02</span>
          <h3>Build the story together</h3>
          <p>
            Name the airport and what goes wrong. Answer the agent’s questions, add constraints, or
            say “Completează tu restul” to let it choose and explain simulated values.
          </p>
        </div>
        <div>
          <span>03</span>
          <h3>Keep changing it live</h3>
          <p>
            Once the scene is ready, keep speaking to change the weather, a service or a deadline.
          </p>
        </div>
      </div>
      <div className="help-note">
        <Sparkles size={18} aria-hidden="true" />
        <p>
          Questions leave the current scene intact. It is replaced when the new scenario is ready.
        </p>
      </div>
      {waiting && (
        <p className="field-hint" role="status">
          Finish the current operation before describing a new scenario.
        </p>
      )}
      {!voiceAvailable && (
        <p className="field-hint">Voice needs OpenAI access configured on the server.</p>
      )}
      <div className="modal-actions">
        <button type="button" className="secondary-button" disabled={waiting} onClick={onText}>
          <FileText size={15} aria-hidden="true" /> Write instead
        </button>
        <button
          type="button"
          className="primary-button"
          disabled={waiting || !voiceAvailable}
          onClick={onVoice}
        >
          <Mic size={16} aria-hidden="true" />
          {listening ? 'Continue by voice' : 'Start voice'}
          <ArrowRight size={15} aria-hidden="true" />
        </button>
      </div>
    </Modal>
  );
}
