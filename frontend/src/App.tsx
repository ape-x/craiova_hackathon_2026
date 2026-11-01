import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDownToLine,
  ArrowRight,
  Check,
  FileText,
  Globe2,
  Loader2,
  LogOut,
  Mic,
  MicOff,
  Radio,
  RefreshCw,
  Search,
  ArrowUp,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Square,
  TriangleAlert,
  Volume2,
  X,
  Zap,
} from 'lucide-react';
import { api, ApiError, TransportError, downloadSession, request } from './api';
import type { AppConfig, MutationResult, SceneMode, World } from './types';
import { useWorld } from './useWorld';
import ResourceRail from './ResourceRail';
import Inspector from './Inspector';
import Timeline from './Timeline';
import WorkspaceHeader from './WorkspaceHeader';
import WorkspaceSummary from './WorkspaceSummary';
import ScenarioDialog from './ScenarioDialog';
import NewScenarioDialog from './NewScenarioDialog';
import LiveDataDialog from './LiveDataDialog';
import RecoveryCaseDialog from './RecoveryCaseDialog';
import RecoveryCases from './RecoveryCases';
import { groupRecoveryEntry, type GroupRecoveryIntent } from './groupRecoveryEntry';
import { currentGroupRecoveryIntent } from './recoveryNavigation';
import { resolveRecoveryDataContext, type RecoveryDataContext } from './recoveryDataContext';
import {
  prepareRecoveryItineraryNavigation,
  preservesItineraryReviewOnClose,
  recoveryItineraryMapSelection,
  type RecoveryItineraryIntent,
} from './recoveryItineraryReview';
import { useFreshnessClock } from './resourceFreshness';
import { locationKindLabel } from './locationNodes';
import { geographicContext } from './resourceLabels';
import { WeatherNetworkOverlay } from './WeatherStatus';
import { Brand, Dot, Modal, Tag } from './ui';
import ThemeToggle from './ThemeToggle';
import { LiveVoiceClient } from './liveVoice';
import VoiceRequestReviewDialog from './VoiceRequestReviewDialog';
import CommandDraft from './CommandDraft';
import {
  beginVoiceRequestReview,
  observeVoiceRequestOutcome,
  reconcileVoiceRequestReview,
  settleVoiceRequestReview,
  voiceReviewDraftText,
} from './voiceRequestReview';
import type { VoiceRequestReview, VoiceReviewOutcome } from './voiceRequestReview';
import { voiceStartupScope } from './voiceScope';
import { reportedArrivalFocus } from './arrivalFocus';
import {
  commandContext,
  recoveryCaseForResult,
  commandNeedsRegionalView,
  keepCommandReplyVisible,
  rememberCommand,
  rememberFailedCommand,
  verifiedCommandResult,
} from './commandContext';
import type { CommandExchange } from './commandContext';
const Scene = lazy(() => import('./scene/Scene'));
const GoogleMaps3D = lazy(() => import('./maps/GoogleMaps3D'));
const Passenger = lazy(() => import('./Passenger'));

export default function App() {
  return location.pathname.startsWith('/boarding') ? (
    <Suspense fallback={<LoadingPage />}>
      <Passenger />
    </Suspense>
  ) : (
    <Operations />
  );
}

function LoadingPage() {
  return (
    <main className="loading-page">
      <Brand />
      <span className="loading-line" />
      <p>Connecting to the operations network</p>
    </main>
  );
}
function Login({ onSuccess }: { onSuccess: () => void }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  return (
    <main className="login-page">
      <div className="page-theme-toggle">
        <ThemeToggle />
      </div>
      <Brand />
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          setError('');
          try {
            await api.login(token);
            setToken('');
            onSuccess();
          } catch (cause) {
            setError(cause instanceof Error ? cause.message : 'Login failed');
          } finally {
            setBusy(false);
          }
        }}
      >
        <Tag tone="cyan">OPERATOR ACCESS</Tag>
        <h1>
          Your operations.
          <br />
          One clear picture.
        </h1>
        <p>Enter the operator credential configured for this deployment.</p>
        <label className="field">
          Operator access token
          <input
            type="password"
            autoComplete="current-password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            required
          />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="primary-button" disabled={busy}>
          {busy ? 'Connecting…' : 'Enter operations'}
          <ArrowRight size={16} />
        </button>
      </form>
      <small>Airport Recovery Live · Operations workspace</small>
    </main>
  );
}

function Operations() {
  const { world, worldRef, config, receive, refresh, connection, error, authRequired, reconnect } =
    useWorld();
  const [mode, setMode] = useState<SceneMode>('airport');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [mapStyle, setMapStyle] = useState<'architectural' | 'geographic'>('architectural');
  const selectedRef = useRef(selectedId);
  selectedRef.current = selectedId;
  const [showLog, setShowLog] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [modal, setModal] = useState<
    | 'scenario'
    | 'new-scenario'
    | 'settings'
    | 'search'
    | 'help'
    | 'reset'
    | 'live-data'
    | 'recovery-case'
    | 'recovery-picker'
    | 'voice-review'
    | null
  >(null);
  const [recoveryCaseId, setRecoveryCaseId] = useState<string | null>(null);
  const [recoverySelection, setRecoverySelection] = useState<RecoveryDataContext>();
  const [recoveryDataContext, setRecoveryDataContext] = useState<RecoveryDataContext>();
  const [itineraryReview, setItineraryReview] = useState<RecoveryItineraryIntent>();
  const itineraryReviewClock = useFreshnessClock(Boolean(itineraryReview && recoveryDataContext));
  const [recoverySetup, setRecoverySetup] = useState<GroupRecoveryIntent | null>(null);
  const [busy, setBusy] = useState(false);
  const running = useRef(false);
  const [operation, setOperation] = useState('');
  const [notice, setNotice] = useState<{
    text: string;
    error?: boolean;
    persistent?: boolean;
    retry?: () => Promise<unknown>;
  } | null>(null);
  const [command, setCommand] = useState('');
  const commandDraft = useRef(command);
  const updateCommandDraft = useCallback((text: string) => {
    commandDraft.current = text;
    setCommand(text);
  }, []);
  const [lastCommand, setLastCommand] = useState('');
  const [newScenarioGuidance, setNewScenarioGuidance] = useState(false);
  const [commandReply, setCommandReply] = useState<{ scenarioId: string; text: string } | null>(
    null,
  );
  const lastReply =
    commandReply?.scenarioId === world?.scenario_id ? (commandReply?.text ?? '') : '';
  const [voiceState, setVoiceState] = useState<string>('idle');
  const voiceStateRef = useRef(voiceState);
  const [voiceReview, setVoiceReview] = useState<VoiceRequestReview | null>(null);
  const latestVoiceReview = useRef<VoiceRequestReview | null>(null);
  const voiceReviewSequence = useRef(0);
  const recordVoiceOutcome = useCallback(
    (attempt: VoiceRequestReview, outcome: VoiceReviewOutcome) => {
      const next = settleVoiceRequestReview(
        latestVoiceReview.current,
        attempt,
        worldRef.current?.scenario_id,
        outcome,
      );
      if (next !== latestVoiceReview.current) {
        latestVoiceReview.current = next;
        setVoiceReview(next);
      }
    },
    [worldRef],
  );
  const [voiceTranscript, setVoiceTranscript] = useState('');
  const [audioBlocked, setAudioBlocked] = useState(false);
  const [voiceMuted, setVoiceMuted] = useState(false);
  const waveform = useRef<HTMLDivElement>(null);
  const voice = useRef<LiveVoiceClient | null>(null);
  const commandHistory = useRef<{
    scenarioId: string;
    exchanges: CommandExchange[];
  }>({
    scenarioId: '',
    exchanges: [],
  });
  const commandInput = useRef<HTMLTextAreaElement>(null);
  const [mobilePanel, setMobilePanel] = useState<'map' | 'resources' | 'inspector'>('map');
  const [plannerMode, setPlannerMode] = useState<'ai' | 'solver' | null>(null);
  const closeModal = useCallback(() => {
    setModal(null);
    if (!itineraryReview || !preservesItineraryReviewOnClose(modal)) {
      setRecoveryDataContext(undefined);
      setItineraryReview(undefined);
    }
  }, [itineraryReview, modal]);
  const openRecoveryCase = useCallback(
    (id: string | null) => {
      setItineraryReview(undefined);
      setRecoveryCaseId(id);
      setRecoverySelection(
        id && worldRef.current
          ? { scenarioId: worldRef.current.scenario_id, incidentId: id, groupIds: [] }
          : undefined,
      );
      setRecoveryDataContext(undefined);
      setRecoverySetup(null);
      setModal('recovery-case');
    },
    [worldRef],
  );
  const openGroupRecovery = useCallback(
    (requested: GroupRecoveryIntent) => {
      const current = worldRef.current;
      const intent = current && currentGroupRecoveryIntent(current, requested);
      if (!current || !intent) {
        setNotice({
          text: 'The passenger or case context changed. Select the group again to review its current recovery options.',
          error: true,
        });
        return;
      }
      setRecoveryDataContext(undefined);
      setItineraryReview(undefined);
      if (intent.kind === 'setup') {
        setRecoverySetup(intent);
        setModal('recovery-picker');
        return;
      }
      setRecoverySetup(null);
      setRecoveryCaseId(intent.incidentId);
      setRecoverySelection({
        scenarioId: intent.scenarioId,
        incidentId: intent.incidentId,
        groupIds: intent.kind === 'recover' ? [intent.groupId] : [],
      });
      setModal('recovery-case');
    },
    [worldRef],
  );
  const chosenMode = plannerMode ?? (config.ai_configured ? 'ai' : 'solver');
  const select = useCallback((id: string) => {
    setSelectedId(id);
    setShowLog(false);
    setMobilePanel('inspector');
  }, []);
  function returnToRecoveryCase() {
    const current = worldRef.current;
    const context = current && resolveRecoveryDataContext(current, recoveryDataContext);
    if (!recoveryDataContext || context?.kind !== 'ready') {
      setNotice({
        text: 'The original case context changed. Reopen the intended case from the workspace.',
        error: true,
      });
      return;
    }
    setRecoveryCaseId(recoveryDataContext.incidentId);
    setRecoverySelection(structuredClone(recoveryDataContext));
    setRecoverySetup(null);
    setItineraryReview(undefined);
    setModal('recovery-case');
  }
  const showScenario = useCallback(() => {
    setRecoveryDataContext(undefined);
    setItineraryReview(undefined);
    setModal(worldRef.current?.mode === 'live' ? 'live-data' : 'scenario');
  }, [worldRef]);
  const applyResult = useCallback(
    (result: MutationResult) => {
      const replaced = worldRef.current?.scenario_id !== result.world.scenario_id;
      receive(result.world);
      const reportedAirport = reportedArrivalFocus(result, worldRef.current ?? result.world);
      const focus =
        result.reports !== undefined ||
        result.report?.type === 'arrival_report' ||
        result.report?.type === 'airport_restriction'
          ? reportedAirport
          : (result.focus_id ?? result.decision?.focus_id);
      if (replaced && !focus) setSelectedId(null);
      if (focus) {
        setSelectedId(focus);
        if (result.world.airports.some((airport) => airport.id === focus))
          setMode(reportedAirport ? 'airport' : 'region');
        if (result.world.groups.some((group) => group.id === focus)) setMode('passenger');
      }
      const recoveryCase = recoveryCaseForResult(result, worldRef.current ?? result.world);
      if (recoveryCase) {
        setRecoveryCaseId(recoveryCase);
        setRecoverySelection((previous) =>
          previous?.scenarioId === result.world.scenario_id && previous.incidentId === recoveryCase
            ? previous
            : { scenarioId: result.world.scenario_id, incidentId: recoveryCase, groupIds: [] },
        );
        setRecoveryDataContext(undefined);
        setItineraryReview(undefined);
        setRecoverySetup(null);
        setModal('recovery-case');
      }
      return result;
    },
    [receive, worldRef],
  );
  const execute = useCallback(
    async (
      label: string,
      action: () => Promise<MutationResult>,
      onOutcome?: (outcome: VoiceReviewOutcome) => void,
    ) => {
      if (running.current) throw new Error('An operation is already in progress.');
      running.current = true;
      setBusy(true);
      setOperation(label);
      setNotice(null);
      const started = performance.now();
      try {
        const result = applyResult(await observeVoiceRequestOutcome(action, onOutcome));
        setNotice({
          persistent: keepCommandReplyVisible(result),
          text:
            result.applied === false
              ? (result.explanation ?? 'No operational change applied.')
              : `State updated · v${result.world.version} · ${((performance.now() - started) / 1000).toFixed(2)}s round trip`,
        });
        return result;
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 409) await refresh().catch(() => {});
        const message = cause instanceof Error ? cause.message : 'Operation failed';
        setNotice({
          text: message,
          error: true,
          retry:
            cause instanceof TransportError
              ? () => execute(label, () => cause.retry<MutationResult>(), onOutcome)
              : undefined,
        });
        throw cause;
      } finally {
        running.current = false;
        setBusy(false);
        setOperation('');
      }
    },
    [applyResult, refresh],
  );
  const replan = useCallback(async () => {
    if (!worldRef.current || running.current) return;
    if (worldRef.current.mode === 'live') {
      setRecoverySetup(null);
      setModal('recovery-picker');
      return;
    }
    try {
      await execute(
        chosenMode === 'ai'
          ? 'AI is evaluating recovery alternatives'
          : 'Solver is evaluating recovery alternatives',
        () => api.replan(worldRef.current!, chosenMode),
      );
    } catch {
      /* Error appears in the operation notice. */
    }
  }, [chosenMode, execute, worldRef]);
  const submitCommand = useCallback(
    async (
      text: string,
      delegationId?: string,
      voiceContext = '',
      onOutcome?: (outcome: VoiceReviewOutcome) => void,
    ) => {
      if (!text.trim() || !worldRef.current) return null;
      setNewScenarioGuidance(false);
      setLastCommand(text);
      if (!delegationId) updateCommandDraft('');
      const previousWorld = worldRef.current;
      const scenarioId = previousWorld.scenario_id;
      if (commandHistory.current.scenarioId !== scenarioId)
        commandHistory.current = { scenarioId, exchanges: [] };
      const context = commandContext(
        commandHistory.current.exchanges,
        selectedRef.current,
        voiceContext,
      );
      let result: MutationResult;
      try {
        result = await execute(
          'Interpreting request and evaluating plans',
          () =>
            delegationId
              ? request<MutationResult>('/commands', {
                  text,
                  context,
                  expected_version: previousWorld.version,
                  idempotency_key: `voice-${delegationId}`.slice(0, 120),
                })
              : api.command(previousWorld, text, context),
          onOutcome,
        );
      } catch (cause) {
        if (worldRef.current?.scenario_id === scenarioId)
          commandHistory.current.exchanges = rememberFailedCommand(
            commandHistory.current.exchanges,
            text,
            cause,
          );
        // execute owns the existing error notice and exact-payload retry.
        throw cause;
      }
      if (worldRef.current?.scenario_id === result.world.scenario_id) {
        const reply = verifiedCommandResult(result);
        setCommandReply({ scenarioId: result.world.scenario_id, text: reply });
        if (commandHistory.current.scenarioId !== result.world.scenario_id)
          commandHistory.current = { scenarioId: result.world.scenario_id, exchanges: [] };
        commandHistory.current.exchanges = rememberCommand(
          commandHistory.current.exchanges,
          text,
          reply,
        );
      }
      if (
        result.report?.type !== 'arrival_report' &&
        commandNeedsRegionalView(previousWorld, result)
      )
        setMode('region');
      return result;
    },
    [execute, updateCommandDraft, worldRef],
  );
  const toggleVoice = async () => {
    if (voiceState === 'closing') return;
    if (voice.current) {
      await voice.current.stop();
      voice.current = null;
      voiceStateRef.current = 'idle';
      setVoiceState('idle');
      return;
    }
    if (!config.voice_configured) {
      setNotice({
        text: 'Voice is not configured. Add the OpenAI key on the server, then restart the API.',
        error: true,
      });
      setModal('settings');
      return;
    }
    setAudioBlocked(false);
    setVoiceMuted(false);
    const client = new LiveVoiceClient({
      scope: voiceStartupScope(
        worldRef.current,
        selectedRef.current,
        recoveryCaseId,
        modal === 'recovery-case',
      ),
      onState: (state, message) => {
        voiceStateRef.current = state;
        setVoiceState(state);
        if (state === 'idle' || state === 'error') voice.current = null;
        if (message)
          setNotice((previous) =>
            previous?.persistent && state !== 'error'
              ? previous
              : { text: message, error: state === 'error' },
          );
      },
      onTranscript: (text, role) => {
        if (role === 'user') setVoiceTranscript(text);
      },
      onAudioLevel: (level) => waveform.current?.style.setProperty('--audio-level', String(level)),
      onPlaybackBlocked: () => setAudioBlocked(true),
      onDelegation: async (delegation) => {
        const scenarioId = worldRef.current?.scenario_id;
        if (!scenarioId) return 'Nu am aplicat nicio modificare.';
        const attempt = beginVoiceRequestReview(
          delegation,
          scenarioId,
          ++voiceReviewSequence.current,
          worldRef.current ?? undefined,
        );
        latestVoiceReview.current = attempt;
        setVoiceReview(attempt);
        const observe = (outcome: VoiceReviewOutcome) => recordVoiceOutcome(attempt, outcome);
        try {
          const result = await submitCommand(
            attempt.text,
            attempt.delegationId,
            delegation.context,
            observe,
          );
          if (!result) {
            observe({
              kind: 'failure',
              cause: new Error('No backend result was recorded for this request.'),
            });
            return 'Nu am aplicat nicio modificare.';
          }
          return result;
        } catch (cause) {
          // execute reports dispatched outcomes and its existing same-key retry.
          // A local rejection before execute must also leave an honest record.
          if (
            latestVoiceReview.current?.sequence === attempt.sequence &&
            latestVoiceReview.current.status === 'pending'
          )
            observe({ kind: 'failure', cause });
          throw cause;
        }
      },
    });
    voice.current = client;
    try {
      await client.start();
    } catch (cause) {
      setNotice({
        text: cause instanceof Error ? cause.message : 'Unable to start voice',
        error: true,
      });
      client.dispose();
      voice.current = null;
      voiceStateRef.current = 'error';
      setVoiceState('error');
    }
  };
  const describeNewScenario = (input: 'voice' | 'text') => {
    if (
      worldRef.current?.mode !== 'simulation' ||
      running.current ||
      ['connecting', 'working', 'closing'].includes(voiceStateRef.current)
    )
      return;
    setNewScenarioGuidance(true);
    closeModal();
    if (input === 'text') {
      requestAnimationFrame(() => commandInput.current?.focus());
    } else if (voice.current) {
      // The user is continuing the current session, not toggling it off.
      voice.current.setMuted(false);
      setVoiceMuted(false);
    } else {
      void toggleVoice();
    }
  };
  useEffect(
    () => () => {
      latestVoiceReview.current = null;
      voice.current?.dispose();
    },
    [],
  );
  useEffect(() => {
    if (authRequired) voice.current?.dispose();
  }, [authRequired]);
  useEffect(() => {
    const next = reconcileVoiceRequestReview(latestVoiceReview.current, world?.scenario_id);
    if (next !== latestVoiceReview.current) {
      latestVoiceReview.current = next;
      setVoiceReview(next);
      if (!next) setModal((current) => (current === 'voice-review' ? null : current));
    }
  }, [world?.scenario_id]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
      if (modal || (event.target instanceof Element && event.target.closest('[role="dialog"]')))
        return;
      const typing =
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLTextAreaElement ||
        event.target instanceof HTMLSelectElement;
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setModal('search');
      }
      if (event.key === '/' && !typing) {
        event.preventDefault();
        commandInput.current?.focus();
      }
      if (event.key === 'Escape' && !modal) {
        setSelectedId(null);
        setShowLog(false);
      }
    };
    window.addEventListener('keydown', keydown);
    return () => window.removeEventListener('keydown', keydown);
  }, [modal]);
  useEffect(() => {
    if (!notice || notice.error || notice.persistent) return;
    const timer = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(timer);
  }, [notice]);
  if (authRequired) return <Login onSuccess={reconnect} />;
  if (!world)
    return error ? (
      <main className="error-page">
        <Brand />
        <h1>Operations network unavailable</h1>
        <p>{error}</p>
        <button className="primary-button" onClick={reconnect}>
          <RefreshCw size={16} />
          Reconnect
        </button>
        <small>Start the FastAPI server and check its health endpoint.</small>
      </main>
    ) : (
      <LoadingPage />
    );
  const live = world.mode === 'live';
  const pinnedItinerary = recoveryDataContext ? itineraryReview : undefined;
  const mapContext = geographicContext(
    world,
    recoveryItineraryMapSelection(world, selectedId, pinnedItinerary, itineraryReviewClock),
  );
  const mapSelectionId = mapContext.selectionId;
  const homeAirport = mapContext.home;
  const geographic = live || mapStyle === 'geographic';
  const lastDecision = world.decisions.at(-1);
  const isVoiceActive = !['idle', 'closed', 'error', 'disconnected'].includes(voiceState);
  const currentVoiceReview = voiceReview?.scenarioId === world.scenario_id ? voiceReview : null;
  return (
    <div
      className={`operations-app panel-${mobilePanel} ${sidebarOpen ? '' : 'sidebar-collapsed'}`}
    >
      <WorkspaceHeader
        mode={world.mode}
        locationLabel={mapContext.locationLabel}
        connected={connection === 'connected'}
        sidebarOpen={sidebarOpen}
        showLog={showLog}
        onToggleSidebar={() => setSidebarOpen((value) => !value)}
        onSearch={() => setModal('search')}
        onSettings={() => setModal('settings')}
        onHelp={() => setModal('help')}
        onStartRecovery={() => openRecoveryCase(null)}
        onNewScenario={() => setModal('new-scenario')}
        onLog={() => {
          setShowLog((value) => !value);
          setMobilePanel('inspector');
        }}
      />
      <nav className="mobile-nav" aria-label="Operations panels">
        <button
          className={mobilePanel === 'resources' ? 'active' : ''}
          onClick={() => setMobilePanel('resources')}
        >
          Resources
        </button>
        <button
          className={mobilePanel === 'map' ? 'active' : ''}
          onClick={() => setMobilePanel('map')}
        >
          3D view
        </button>
        <button
          className={mobilePanel === 'inspector' ? 'active' : ''}
          onClick={() => setMobilePanel('inspector')}
        >
          Overview
        </button>
      </nav>
      <div className="operations-workspace">
        <ResourceRail
          world={world}
          selectedId={selectedId}
          onSelect={select}
          onMode={setMode}
          onScenario={showScenario}
          onLiveData={() => setModal('live-data')}
          onRecoveryCase={openRecoveryCase}
          onApplied={applyResult}
        />
        <main className="map-workspace" aria-label="Operational 3D view">
          <WorkspaceSummary
            world={world}
            mode={mode}
            airport={homeAirport}
            onReview={() => {
              setSelectedId(null);
              setShowLog(false);
              setMobilePanel('inspector');
            }}
          />
          <div className={`scene-wrap ${geographic ? 'geographic-view' : ''}`}>
            <Suspense
              fallback={
                <div className="scene-loading">
                  <Loader2 className="spin" size={24} />
                  <span>Building the operational view</span>
                </div>
              }
            >
              {!live && world.airports.length === 0 ? (
                <div className="scenario-empty-map">
                  <div>
                    <Tag tone="cyan">NEW SIMULATION</Tag>
                    <h2>Your next scenario starts here</h2>
                    <p>
                      Describe an airport, flight and disruption by voice or text. The agent will
                      create the simulation and compare recovery options.
                    </p>
                    <button className="primary-button" onClick={() => setModal('new-scenario')}>
                      Create a new scenario <ArrowRight size={14} />
                    </button>
                  </div>
                </div>
              ) : geographic && (!live || config.google_maps_api_key) ? (
                <GoogleMaps3D
                  world={mapContext.world}
                  mode={mode}
                  selectedId={mapSelectionId}
                  onSelect={select}
                  apiKey={config.google_maps_api_key ?? ''}
                  onClose={live ? undefined : () => setMapStyle('architectural')}
                />
              ) : live ? (
                <div className="live-empty-map">
                  <div>
                    <Tag tone="cyan">LIVE OPERATION</Tag>
                    <h2>Connect real geography and supplier data</h2>
                    <p>
                      The geographic map needs a configured browser Maps key. Your verified
                      inventory and passengers remain available in the side panels.
                    </p>
                    <button className="primary-button" onClick={() => setModal('live-data')}>
                      Manage operation data
                    </button>
                  </div>
                </div>
              ) : (
                <Scene world={world} mode={mode} selectedId={selectedId} onSelect={select} />
              )}
            </Suspense>
            <div className="map-topbar">
              <div className="view-switcher" role="group" aria-label="Map view">
                {(['airport', 'region', 'passenger'] as const).map((view) => (
                  <button
                    key={view}
                    aria-pressed={mode === view}
                    className={mode === view ? 'active' : ''}
                    onClick={() => {
                      setMode(view);
                      if (
                        view === 'passenger' &&
                        !world.groups.some((group) => group.id === selectedId)
                      )
                        setSelectedId(world.groups[0]?.id ?? null);
                    }}
                  >
                    {view === 'airport' ? 'Airport' : view === 'region' ? 'Network' : 'Passengers'}
                  </button>
                ))}
              </div>
              {!live && (
                <button
                  className="geographic-toggle"
                  aria-pressed={mapStyle === 'geographic'}
                  onClick={() =>
                    setMapStyle((value) =>
                      value === 'architectural' ? 'geographic' : 'architectural',
                    )
                  }
                  title="Switch between the architectural model and Google geographic 3D"
                >
                  <Globe2 size={13} />
                  {mapStyle === 'geographic' ? 'Airport model' : 'Geographic 3D'}
                </button>
              )}
              <div className={`planner-badge ${chosenMode === 'ai' ? 'cyan' : 'slate'}`}>
                <Zap size={12} />
                <span>
                  {chosenMode === 'ai'
                    ? world.decisions.some((item) => item.mode === 'ai')
                      ? 'AI planner'
                      : 'AI configured'
                    : 'Local solver'}
                </span>
                <Dot tone={chosenMode === 'ai' ? 'cyan' : 'slate'} />
              </div>
            </div>
            {!busy && <WeatherNetworkOverlay world={world} onSelect={select} />}
            {busy && (
              <div className="calculation-overlay" role="status">
                <Loader2 size={17} className="spin" />
                <div>
                  <strong>{operation}</strong>
                  <span>Validating the current inventory and constraints</span>
                </div>
              </div>
            )}
            {!live &&
              !busy &&
              world.airports.some((airport) => airport.id === 'CRA' && !airport.available) && (
                <div className="incident-overlay">
                  <TriangleAlert size={17} />
                  <span>SIMULATED AIRPORT CLOSURE</span>
                  <b>CRA</b>
                </div>
              )}
            <div className="map-bottom-left">
              <div className="map-coordinate">
                <span className="crosshair-mark">+</span>
                <span>
                  {!geographic && mode === 'region'
                    ? 'SCHEMATIC NETWORK'
                    : homeAirport
                      ? `${Math.abs(homeAirport.lat).toFixed(3)}° ${homeAirport.lat >= 0 ? 'N' : 'S'} · ${Math.abs(homeAirport.lon).toFixed(3)}° ${homeAirport.lon >= 0 ? 'E' : 'W'}`
                      : 'No airport selected'}
                </span>
                <span className="map-site-code">
                  {mode === 'region' ? 'NETWORK' : mapContext.siteLabel}
                </span>
              </div>
              <div className="map-legend">
                <span>
                  <i className="legend-line cyan" />
                  Observations
                </span>
                <span>
                  <i className="legend-line amber dashed" />
                  {live ? 'Operational data' : 'Scenario change'}
                </span>
                <span>
                  <i className="legend-line mint" />
                  Recovery
                </span>
              </div>
            </div>
            <div className="map-disclaimer">
              {live
                ? 'Real geography · Observed aircraft · No simulated vehicle positions'
                : mapStyle === 'geographic'
                  ? 'Google terrain · Simulated operations'
                  : mode === 'region'
                    ? 'Schematic network · Simulated operations'
                    : 'Illustrative model · Simulated operations'}
            </div>
            <div className="map-action">
              <button
                className="secondary-button"
                onClick={showScenario}
                title={
                  live
                    ? 'Manage passenger requirements and sourced services'
                    : 'Change the simulation conditions or describe a scenario by voice or text'
                }
              >
                <SlidersHorizontal size={14} />
                {live ? 'Operation data' : 'Add a condition'}
              </button>
              <button
                className="primary-button"
                disabled={busy}
                onClick={() => {
                  void replan();
                }}
              >
                {busy ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}
                {live ? 'Find sourced alternatives' : 'Find recovery'}{' '}
                <span>{live ? 'LIVE SOURCES' : chosenMode === 'ai' ? 'AI' : 'SOLVER'}</span>
              </button>
            </div>
          </div>
          <Timeline world={world} onSelect={select} />
        </main>
        <Inspector
          world={world}
          selectedId={selectedId}
          onSelect={select}
          onClose={() => setSelectedId(null)}
          onReplan={() => {
            void replan();
          }}
          onGroupRecovery={openGroupRecovery}
          itineraryReview={pinnedItinerary}
          onReturnToRecoveryCase={
            pinnedItinerary
              ? () => {
                  if (!running.current && !notice?.retry) returnToRecoveryCase();
                }
              : undefined
          }
          reviewReturnDisabled={busy || Boolean(notice?.retry)}
          onScenario={showScenario}
          onApplied={applyResult}
          onRefresh={refresh}
          busy={busy}
          config={config}
          showLog={showLog}
        />
      </div>
      <footer
        aria-label={live ? 'Recovery assistant' : 'Scenario assistant'}
        className={`voice-console ${isVoiceActive ? 'listening' : ''}`}
      >
        <button
          className={`mic-button ${isVoiceActive ? 'active' : ''}`}
          onClick={() => {
            void toggleVoice();
          }}
          disabled={voiceState === 'closing'}
          aria-label={isVoiceActive ? 'End voice session' : 'Start voice session'}
          title={isVoiceActive ? 'End voice session' : 'Talk to Recovery Live'}
        >
          {voiceState === 'connecting' ? (
            <Loader2 size={22} className="spin" />
          ) : isVoiceActive ? (
            <Square size={17} fill="currentColor" />
          ) : (
            <Mic size={22} />
          )}
        </button>
        <div className="voice-wave" ref={waveform} aria-hidden="true">
          {Array.from({ length: 38 }, (_, index) => (
            <i
              key={index}
              style={{
                height: isVoiceActive
                  ? `calc(4px + var(--audio-level, 0) * ${34 * Math.sin(index * 1.7) ** 2 * Math.sin(((index + 1) / 39) * Math.PI)}px)`
                  : `${4 + 14 * Math.sin(index * 1.7) ** 2 * Math.sin(((index + 1) / 39) * Math.PI)}px`,
              }}
            />
          ))}
        </div>
        <form
          className="command-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submitCommand(command).catch(() => {});
          }}
        >
          <label className="sr-only" htmlFor="scenario-command">
            {live
              ? 'Describe a recovery planning request in Romanian or English'
              : 'Describe a scenario in Romanian or English'}
          </label>
          <CommandDraft
            id="scenario-command"
            aria-describedby={lastReply ? 'command-reply' : undefined}
            ref={commandInput}
            placeholder={
              !live && newScenarioGuidance
                ? 'Creează un scenariu nou în care…'
                : isVoiceActive
                  ? live
                    ? 'Listening. Describe a recovery request…'
                    : 'Listening. Tell me what has changed…'
                  : live
                    ? 'Review passengers and verified offers…'
                    : 'What changed? Describe a scenario…'
            }
            value={command}
            onChange={(event) => updateCommandDraft(event.target.value)}
            maxLength={4000}
            disabled={busy}
          />
          <output
            id="command-reply"
            className="command-subtitle"
            aria-live={lastReply && !busy ? 'polite' : 'off'}
            aria-atomic="true"
            title={lastReply || undefined}
          >
            {busy
              ? operation
              : !live && newScenarioGuidance
                ? 'Începe cu „Creează un scenariu nou…”, apoi descrie aeroportul, zborul și problema.'
                : lastReply
                  ? lastReply
                  : isVoiceActive
                    ? voiceTranscript || 'OpenAI voice session active'
                    : lastCommand ||
                      (config.ai_configured
                        ? 'Describe it in English or Romanian. Every plan is validated before it is applied.'
                        : live
                          ? 'Text & voice need OpenAI access. Operation data and solver are available.'
                          : 'Text & voice need OpenAI access. Scenario controls and solver are available.')}
          </output>
          <button
            className="send-command"
            type="submit"
            disabled={busy || !command.trim()}
            aria-label={live ? 'Send planning request' : 'Send scenario command'}
          >
            <ArrowUp size={20} />
          </button>
        </form>
        <div className="console-meta">
          {currentVoiceReview && (
            <button
              type="button"
              className="voice-review-trigger"
              aria-label="Review voice request"
              title="Review voice request"
              onClick={() => setModal('voice-review')}
            >
              <FileText size={16} aria-hidden="true" />
              <span>Review voice request</span>
            </button>
          )}
          {isVoiceActive && (
            <button
              className="icon-button"
              aria-label={voiceMuted ? 'Unmute microphone' : 'Mute microphone'}
              onClick={() => {
                voice.current?.setMuted(!voiceMuted);
                setVoiceMuted((value) => !value);
              }}
            >
              {voiceMuted ? <MicOff size={17} /> : <Mic size={17} />}
            </button>
          )}
          {isVoiceActive && audioBlocked && (
            <button
              className="secondary-button"
              onClick={() => {
                void voice.current?.audio
                  .play()
                  .then(() => setAudioBlocked(false))
                  .catch(() =>
                    setNotice({
                      text: 'Audio playback is blocked. Check this browser’s sound permissions.',
                      error: true,
                    }),
                  );
              }}
            >
              <Volume2 size={15} />
              Play voice
            </button>
          )}
          {lastDecision && (
            <span className="last-latency">
              <Zap size={12} />
              {(lastDecision.latency_ms / 1000).toFixed(2)}s <span>last decision</span>
            </span>
          )}
        </div>
      </footer>
      {notice && (
        <div
          className={`toast ${notice.error ? 'error' : ''}`}
          role={notice.error ? 'alert' : 'status'}
        >
          {notice.error ? <TriangleAlert size={17} /> : <Check size={16} />}
          <span>{notice.text}</span>
          {notice.retry && (
            <button
              className="secondary-button"
              disabled={busy}
              onClick={() => {
                void notice.retry?.().catch(() => {});
              }}
            >
              Recover request
            </button>
          )}
          <button
            className="icon-button"
            onClick={() => setNotice(null)}
            aria-label="Dismiss notification"
          >
            <X size={14} />
          </button>
        </div>
      )}
      {modal === 'live-data' && (
        <LiveDataDialog
          world={world}
          onClose={closeModal}
          onApplied={applyResult}
          recoveryContext={recoveryDataContext}
          onReturnToCase={returnToRecoveryCase}
        />
      )}
      {modal === 'recovery-picker' && live && (
        <Modal title="Choose recovery case" onClose={closeModal} wide>
          <p className="field-hint">
            Select the incident to review its passenger groups and search current flight, hotel and
            transfer offers.
          </p>
          {recoverySetup?.scenarioId === world.scenario_id && (
            <p className="case-source-warning">
              {groupRecoveryEntry(world, recoverySetup.groupId)?.detail}
            </p>
          )}
          <RecoveryCases
            world={world}
            onOpen={(id) => {
              const setup = recoverySetup;
              openRecoveryCase(id);
              if (setup?.scenarioId === worldRef.current?.scenario_id) setRecoverySetup(setup);
            }}
          />
        </Modal>
      )}
      {modal === 'voice-review' && currentVoiceReview && (
        <VoiceRequestReviewDialog
          review={currentVoiceReview}
          context={{
            scenarioId: world.scenario_id,
            draft: command,
            voiceActive: isVoiceActive || Boolean(voice.current),
            busy,
          }}
          onClose={closeModal}
          onCopyToDraft={() => {
            const text = voiceReviewDraftText(latestVoiceReview.current, currentVoiceReview, {
              scenarioId: worldRef.current?.scenario_id,
              draft: commandDraft.current,
              voiceActive:
                Boolean(voice.current) ||
                !['idle', 'closed', 'error', 'disconnected'].includes(voiceStateRef.current),
              busy: running.current,
            });
            if (text === null) return;
            updateCommandDraft(text);
            closeModal();
            requestAnimationFrame(() => commandInput.current?.focus());
          }}
        />
      )}
      {modal === 'recovery-case' && live && (
        <RecoveryCaseDialog
          key={`${world.scenario_id}:${recoveryCaseId ?? 'new'}`}
          world={world}
          incidentId={recoveryCaseId}
          initialSelection={recoverySelection}
          setupGroupId={
            recoverySetup?.scenarioId === world.scenario_id ? recoverySetup.groupId : undefined
          }
          onApplied={applyResult}
          onRefresh={refresh}
          onClose={closeModal}
          reviewDisabled={busy || Boolean(notice?.retry)}
          onReviewItinerary={(intent, context) => {
            const current = worldRef.current;
            if (!current || running.current || notice?.retry) return;
            const target = prepareRecoveryItineraryNavigation(current, intent, context, false);
            if (target.kind !== 'ready') {
              setNotice({ text: target.reason, error: true });
              return;
            }
            setRecoveryDataContext(target.context);
            setItineraryReview(target.intent);
            setSelectedId(target.group.id);
            setShowLog(false);
            setMobilePanel('inspector');
            setMode('passenger');
            setModal(null);
            requestAnimationFrame(() =>
              document.querySelector<HTMLElement>('.inspector')?.focus({ preventScroll: true }),
            );
          }}
          onManageData={(context) => {
            setItineraryReview(undefined);
            setRecoveryCaseId(context.incidentId);
            setRecoveryDataContext({ ...context, groupIds: [...context.groupIds] });
            setModal('live-data');
          }}
        />
      )}
      {modal === 'scenario' && !live && (
        <ScenarioDialog
          world={world}
          selectedId={selectedId}
          busy={busy}
          onClose={closeModal}
          onApply={async (change, shouldReplan) => {
            const result = await execute('Applying scenario condition', () =>
              api.event(worldRef.current!, change),
            );
            if (change.type === 'airport_closed' || change.type === 'flight_diverted')
              setMode('region');
            if (shouldReplan) {
              try {
                await execute(
                  chosenMode === 'ai'
                    ? 'AI is evaluating recovery alternatives'
                    : 'Solver is evaluating recovery alternatives',
                  () => api.replan(result.world, chosenMode),
                );
              } catch (cause) {
                const message = `Condition saved at v${result.world.version}. Replanning was not confirmed: ${cause instanceof Error ? cause.message : 'Check the current state.'}`;
                setNotice((previous) => ({
                  text: message,
                  error: true,
                  retry: previous?.retry,
                }));
                throw new Error(message);
              }
            }
          }}
        />
      )}
      {modal === 'search' && (
        <SearchDialog
          world={world}
          onClose={closeModal}
          onSelect={(id) => {
            select(id);
            if (world.groups.some((group) => group.id === id)) setMode('passenger');
            closeModal();
          }}
        />
      )}
      {modal === 'settings' && (
        <SettingsDialog
          world={world}
          config={config}
          onClose={closeModal}
          plannerMode={chosenMode}
          setPlannerMode={setPlannerMode}
          onReset={() => setModal('reset')}
          onRefresh={refresh}
          onLogout={async () => {
            await voice.current?.stop();
            voice.current = null;
            await api.logout();
            reconnect();
          }}
        />
      )}
      {modal === 'new-scenario' && !live && (
        <NewScenarioDialog
          voiceState={voiceState}
          voiceAvailable={Boolean(config.voice_configured)}
          busy={busy}
          onClose={closeModal}
          onVoice={() => describeNewScenario('voice')}
          onText={() => describeNewScenario('text')}
        />
      )}
      {modal === 'reset' && !live && (
        <Modal title="Start a new simulation" onClose={closeModal}>
          <p className="modal-copy">
            This replaces the current scenario, offers and reservations with a fresh synthetic
            network. Export the session first if you want to keep its decision log.
          </p>
          <div className="modal-actions">
            <button className="secondary-button" onClick={() => downloadSession(world)}>
              <ArrowDownToLine size={15} />
              Export current session
            </button>
            <button
              className="primary-button"
              disabled={busy}
              onClick={() => {
                void execute('Resetting simulation', () => api.reset(worldRef.current!))
                  .then(() => {
                    setSelectedId(null);
                    setMode('airport');
                    setLastCommand('');
                    closeModal();
                  })
                  .catch(() => {});
              }}
            >
              Start fresh
              <ArrowRight size={15} />
            </button>
          </div>
        </Modal>
      )}
      {modal === 'help' && live && (
        <Modal title="Operation guide" onClose={closeModal}>
          <p className="modal-copy">
            Import the affected passenger manifest, or enter clearly labeled planning assumptions
            while passenger details are unavailable. Search connected suppliers and add current
            quotes or documented operator-verified services to the operation. Evaluate complete
            journeys using those resources and the passengers’ requirements.
          </p>
          <p className="modal-copy">
            Flight observations establish aircraft context; they do not establish a disruption or
            bookable seats. Passenger acceptance records a preference. Record supplier confirmations
            only after every service has actually been booked for verified passengers.
          </p>
          <button className="primary-button" onClick={() => setModal('live-data')}>
            Manage operation data
          </button>
        </Modal>
      )}
      {modal === 'help' && !live && (
        <Modal title="Put the network to the test" onClose={closeModal}>
          <div className="demo-steps">
            <div>
              <span>01</span>
              <h3>Create your own scenario</h3>
              <p>
                Choose New scenario, start voice and say “Creează un scenariu nou…” followed by the
                situation you want to test.
              </p>
            </div>
            <div>
              <span>02</span>
              <h3>Change the conditions again</h3>
              <p>“Aeroportul Craiova nu mai este disponibil. Compară alternativele.”</p>
            </div>
            <div>
              <span>03</span>
              <h3>Make it personal</h3>
              <p>
                Select a passenger group, scan their QR, then accept the offer on another device.
              </p>
            </div>
            <div>
              <span>04</span>
              <h3>Test an unexpected constraint</h3>
              <p>
                Change any capacity, budget or deadline. The planner evaluates the current shared
                inventory.
              </p>
            </div>
          </div>
          <div className="help-note">
            <ShieldCheck size={17} />
            <p>
              All operational changes and bookings are simulated. Live observations, AI responses
              and measured timings are labeled independently.
            </p>
          </div>
          <div className="modal-actions">
            <span>
              <kbd>⌘ K</kbd> Search <kbd>/</kbd> Command <kbd>Esc</kbd> Clear
            </span>
            <button className="primary-button" onClick={() => setModal('new-scenario')}>
              Create a new scenario
              <ArrowRight size={15} />
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function SearchDialog({
  world,
  onClose,
  onSelect,
}: {
  world: World;
  onClose: () => void;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState('');
  const entities = [
    ...world.airports.map((item) => ({
      id: item.id,
      name: item.name,
      type: locationKindLabel(item),
    })),
    ...world.resources.map((item) => ({
      id: item.id,
      name: item.name,
      type: item.kind,
    })),
    ...world.groups.map((item) => ({
      id: item.id,
      name: item.name,
      type: `${item.size} passengers`,
    })),
  ];
  const results = entities
    .filter((item) =>
      `${item.id} ${item.name} ${item.type}`.toLowerCase().includes(query.toLowerCase()),
    )
    .slice(0, 30);
  return (
    <Modal title="Search the network" onClose={onClose}>
      <label className="global-search">
        <Search size={19} />
        <input
          placeholder="Airport, vehicle, flight or passenger…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          data-autofocus
          aria-label="Search all entities"
        />
      </label>
      <div className="search-results">
        {results.map((item) => (
          <button key={item.id} onClick={() => onSelect(item.id)}>
            <strong>{item.id}</strong>
            <span>{item.name}</span>
            <Tag>{item.type}</Tag>
            <ArrowRight size={14} />
          </button>
        ))}
        {!results.length && <p>No matching entities.</p>}
      </div>
    </Modal>
  );
}

function SettingsDialog({
  world,
  config,
  onClose,
  plannerMode,
  setPlannerMode,
  onReset,
  onRefresh,
  onLogout,
}: {
  world: World;
  config: AppConfig;
  onClose: () => void;
  plannerMode: 'ai' | 'solver';
  setPlannerMode: (mode: 'ai' | 'solver') => void;
  onReset: () => void;
  onRefresh: () => Promise<World>;
  onLogout: () => Promise<void>;
}) {
  const [trafficBusy, setTrafficBusy] = useState(false);
  const [error, setError] = useState('');
  const trafficEnabled = Boolean(
    world.provider_status.fr24_polling ?? world.provider_status.traffic_running,
  );
  return (
    <Modal title="Connections & session" onClose={onClose}>
      <div className="connection-list">
        {[
          {
            name: 'OpenAI Decisions + Ultrafast',
            caption: 'Live interpretation and plan selection',
            ready: config.ai_configured,
          },
          {
            name: 'OpenAI Voice',
            caption: 'Full-duplex voice with server-validated actions',
            ready: config.voice_configured,
          },
          {
            name: 'Flightradar24',
            caption: 'Observed aircraft positions · no bookable seat inventory',
            ready: config.fr24_configured,
          },
          {
            name: 'Google Maps 3D',
            caption: 'Geographic 3D · requires an allowed browser key',
            ready: Boolean(config.google_maps_api_key),
          },
        ].map((item) => (
          <div className="connection-row" key={item.name}>
            <Dot tone={item.ready ? 'mint' : 'slate'} />
            <div>
              <strong>{item.name}</strong>
              <span>{item.caption}</span>
            </div>
            <Tag tone={item.ready ? 'mint' : ''}>{item.ready ? 'CONFIGURED' : 'NOT CONNECTED'}</Tag>
          </div>
        ))}
      </div>
      <p className="field-hint">
        Configured means a credential is present. A successful request verifies provider access.
        Server secrets are never entered in this browser.
      </p>
      <div className="settings-section">
        <label className="field">
          Planner
          <select
            value={plannerMode}
            onChange={(event) => setPlannerMode(event.target.value as 'ai' | 'solver')}
          >
            <option value="ai" disabled={!config.ai_configured}>
              Live AI · OpenAI Decisions
            </option>
            <option value="solver">Local solver · deterministic, no AI</option>
          </select>
        </label>
        <p className="field-hint">
          The local solver validates and allocates inventory. Natural-language interpretation
          requires live AI.
        </p>
      </div>
      {config.fr24_configured && (
        <div className="settings-section">
          <strong>Observed traffic polling</strong>
          <p className="field-hint">
            Starting the feed consumes your Flightradar24 API credits. All clients share one feed.
          </p>
          <button
            className="secondary-button"
            disabled={trafficBusy}
            onClick={async () => {
              setTrafficBusy(true);
              setError('');
              try {
                await request(`/traffic/${trafficEnabled ? 'stop' : 'start'}`, {});
                await onRefresh();
              } catch (cause) {
                setError(cause instanceof Error ? cause.message : 'Traffic request failed');
              } finally {
                setTrafficBusy(false);
              }
            }}
          >
            {trafficEnabled ? <Square size={14} /> : <Radio size={14} />}{' '}
            {trafficEnabled ? 'Pause live feed' : 'Start live feed'}
          </button>
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="settings-section">
        <div className="section-label">
          <span>{world.mode === 'live' ? 'Current live operation' : 'Current simulation'}</span>
          <code>v{world.version}</code>
        </div>
        <div className="session-actions">
          <button className="secondary-button" onClick={() => downloadSession(world)}>
            <ArrowDownToLine size={15} />
            Export audit
          </button>
          {world.mode === 'simulation' && (
            <button className="secondary-button" onClick={onReset}>
              <RefreshCw size={15} />
              New simulation
            </button>
          )}
          {config.auth_required && (
            <button
              className="secondary-button"
              onClick={() => {
                void onLogout();
              }}
            >
              <LogOut size={15} />
              Sign out
            </button>
          )}
        </div>
      </div>
      <div className="deployment-note">
        <ShieldCheck size={15} />
        <span>
          {config.environment === 'production'
            ? 'Protected production deployment'
            : 'Local development session'}{' '}
          · durable server state
        </span>
      </div>
    </Modal>
  );
}
