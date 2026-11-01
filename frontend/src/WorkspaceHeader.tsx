import {
  Activity,
  ChevronDown,
  HelpCircle,
  PanelLeft,
  Plus,
  Search,
  Settings2,
} from 'lucide-react';
import { Brand, Dot } from './ui';
import ThemeToggle from './ThemeToggle';
import type { World } from './types';

interface Props {
  locationLabel: string;
  mode: World['mode'];
  connected: boolean;
  sidebarOpen: boolean;
  showLog: boolean;
  onToggleSidebar: () => void;
  onSearch: () => void;
  onSettings: () => void;
  onLog: () => void;
  onHelp: () => void;
  onStartRecovery?: () => void;
  onNewScenario?: () => void;
}

export default function WorkspaceHeader(props: Props) {
  return (
    <header className="app-header">
      <div className="header-brand">
        <button
          className="icon-button sidebar-toggle"
          onClick={props.onToggleSidebar}
          aria-label={props.sidebarOpen ? 'Collapse resources' : 'Expand resources'}
          aria-expanded={props.sidebarOpen}
          title="Toggle resources"
        >
          <PanelLeft size={19} />
        </button>
        <Brand />
      </div>
      <div className="workspace-breadcrumb">
        <span>Workspace</span>
        <span>/</span>
        <strong>{props.locationLabel}</strong>
      </div>
      <div className="header-actions">
        {props.mode === 'simulation' && props.onNewScenario && (
          <button
            className="primary-button start-recovery-header"
            onClick={props.onNewScenario}
            aria-label="New scenario"
            title="New scenario"
          >
            <Plus size={15} />
            <span>New scenario</span>
          </button>
        )}
        {props.mode === 'live' && props.onStartRecovery && (
          <button
            className="primary-button start-recovery-header"
            onClick={props.onStartRecovery}
            aria-label="Start recovery"
            title="Start recovery"
          >
            <Plus size={15} />
            <span>Start recovery</span>
          </button>
        )}
        <ThemeToggle />
        <span className="sync-status" role="status">
          <Dot tone={props.connected ? 'mint' : 'amber'} />
          {props.connected ? 'Connected' : 'Reconnecting'}
        </span>
        <button className="mode-selector" onClick={props.onSettings}>
          {props.mode === 'live' ? 'Live · assisted' : 'Simulation'} <ChevronDown size={13} />
        </button>
        <button
          className="icon-button"
          onClick={props.onSearch}
          aria-label="Search the network"
          title="Search · ⌘K"
        >
          <Search size={18} />
        </button>
        <button
          className={`icon-button ${props.showLog ? 'active' : ''}`}
          onClick={props.onLog}
          aria-label="View decision log"
          aria-pressed={props.showLog}
          title="Decision log"
        >
          <Activity size={18} />
        </button>
        <button
          className="icon-button header-help"
          onClick={props.onHelp}
          aria-label={props.mode === 'live' ? 'Operation guide' : 'Simulation guide'}
          title="Guide"
        >
          <HelpCircle size={18} />
        </button>
        <button
          className="icon-button"
          onClick={props.onSettings}
          aria-label="Connection settings"
          title="Connections"
        >
          <Settings2 size={18} />
        </button>
      </div>
    </header>
  );
}
