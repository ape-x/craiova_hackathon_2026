import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  Check,
  ChevronDown,
  Globe2,
  HelpCircle,
  Loader2,
  LogOut,
  Mic,
  MicOff,
  Radio,
  RefreshCw,
  Search,
  Send,
  Settings2,
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
import ScenarioDialog from './ScenarioDialog';
import { WeatherNetworkOverlay } from './WeatherStatus';
import { Brand, Dot, Modal, Tag } from './ui';
import { LiveVoiceClient } from './liveVoice';
import {
  commandContext,
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
      <small>Airport Recovery Live · Simulation environment</small>
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
  const [modal, setModal] = useState<'scenario' | 'settings' | 'search' | 'help' | 'reset' | null>(
    null,
  );
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
  const [lastCommand, setLastCommand] = useState('');
  const [commandReply, setCommandReply] = useState<{
    scenarioId: string;
    text: string;
  } | null>(null);
  const lastReply =
    commandReply?.scenarioId === world?.scenario_id ? (commandReply?.text ?? '') : '';
  const [voiceState, setVoiceState] = useState<string>('idle');
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
  const commandInput = useRef<HTMLInputElement>(null);
  const [mobilePanel, setMobilePanel] = useState<'map' | 'resources' | 'inspector'>('map');
  const [plannerMode, setPlannerMode] = useState<'ai' | 'solver' | null>(null);
  const closeModal = useCallback(() => setModal(null), []);
  const chosenMode = plannerMode ?? (config.ai_configured ? 'ai' : 'solver');
  const select = useCallback((id: string) => {
    setSelectedId(id);
    setShowLog(false);
  }, []);
  const showScenario = useCallback(() => setModal('scenario'), []);
  const applyResult = useCallback(
    (result: MutationResult) => {
      const replaced = worldRef.current?.scenario_id !== result.world.scenario_id;
      receive(result.world);
      const focus = result.focus_id ?? result.decision?.focus_id;
      if (replaced && !focus) setSelectedId(null);
      if (focus) {
        setSelectedId(focus);
        if (result.world.airports.some((airport) => airport.id === focus)) setMode('region');
        if (result.world.groups.some((group) => group.id === focus)) setMode('passenger');
      }
      return result;
    },
    [receive, worldRef],
  );
  const execute = useCallback(
    async (label: string, action: () => Promise<MutationResult>) => {
      if (running.current) throw new Error('An operation is already in progress.');
      running.current = true;
      setBusy(true);
      setOperation(label);
      setNotice(null);
      const started = performance.now();
      try {
        const result = applyResult(await action());
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
              ? () => execute(label, () => cause.retry<MutationResult>())
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
    if (!worldRef.current) return;
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
    async (text: string, delegationId?: string, voiceContext = '') => {
      if (!text.trim() || !worldRef.current) return null;
      setLastCommand(text);
      setCommand('');
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
        result = await execute('Interpreting request and evaluating plans', () =>
          delegationId
            ? request<MutationResult>('/commands', {
                text,
                context,
                expected_version: previousWorld.version,
                idempotency_key: `voice-${delegationId}`.slice(0, 120),
              })
            : api.command(previousWorld, text, context),
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
          commandHistory.current = {
            scenarioId: result.world.scenario_id,
            exchanges: [],
          };
        commandHistory.current.exchanges = rememberCommand(
          commandHistory.current.exchanges,
          text,
          reply,
        );
      }
      if (commandNeedsRegionalView(previousWorld, result)) setMode('region');
      return result;
    },
    [execute, worldRef],
  );
  const toggleVoice = async () => {
    if (voiceState === 'closing') return;
    if (voice.current) {
      await voice.current.stop();
      voice.current = null;
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
      onState: (state, message) => {
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
      onDelegation: async ({ text, delegationId, context }) => {
        const result = await submitCommand(text, delegationId, context);
        if (!result) return 'Nu am aplicat nicio modificare.';
        return verifiedCommandResult(result);
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
      setVoiceState('error');
    }
  };
  useEffect(
    () => () => {
      voice.current?.dispose();
    },
    [],
  );
  useEffect(() => {
    if (authRequired) voice.current?.dispose();
  }, [authRequired]);
  useEffect(() => {
    const keydown = (event: KeyboardEvent) => {
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
  const homeAirport =
    world.airports.find((airport) => airport.id === selectedId) ?? world.airports[0];
  const lastDecision = world.decisions.at(-1);
  const isVoiceActive = !['idle', 'closed', 'error', 'disconnected'].includes(voiceState);
  return (
    <div className={`operations-app panel-${mobilePanel}`}>
      <header className="app-header">
        <Brand />
        <div className="operation-location">
          <span>
            {homeAirport ? `${homeAirport.code} recovery operations` : 'Simulation workspace'}
          </span>
          <Tag>{homeAirport?.code ?? 'NETWORK'}</Tag>
        </div>
        <div className="header-center">
          <Dot tone={connection === 'connected' ? 'mint' : 'amber'} />
          <span>{connection === 'connected' ? 'NETWORK SYNCED' : 'RECONNECTING'}</span>
        </div>
        <div className="header-actions">
          <button className="mode-selector" onClick={() => setModal('settings')}>
            <span className="simulation-mark" />
            SIMULATION
            <ChevronDown size={13} />
          </button>
          <button
            className="icon-button header-search"
            onClick={() => setModal('search')}
            aria-label="Search the network"
            title="Search · ⌘K"
          >
            <Search size={18} />
          </button>
          <button
            className="icon-button"
            onClick={() => {
              setShowLog((value) => !value);
              setMobilePanel('inspector');
            }}
            aria-label="View decision log"
            title="Decision log"
          >
            <Activity size={18} />
            {world.decisions.length > 0 && <span className="notification-dot" />}
          </button>
          <button
            className="icon-button"
            onClick={() => setModal('settings')}
            aria-label="Connection settings"
            title="Connections"
          >
            <Settings2 size={18} />
          </button>
          <span className="header-divider" />
          <button
            className={`log-button ${showLog ? 'active' : ''}`}
            onClick={() => {
              setShowLog((value) => !value);
              setMobilePanel('inspector');
            }}
          >
            <ClipboardIcon />
            <span>Decision log</span>
          </button>
        </div>
      </header>
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
          Inspector
        </button>
      </nav>
      <div className="operations-workspace">
        <ResourceRail
          world={world}
          selectedId={selectedId}
          onSelect={select}
          onMode={setMode}
          onScenario={showScenario}
        />
        <main className="map-workspace" aria-label="Operational 3D view">
          <div className={`scene-wrap ${mapStyle === 'geographic' ? 'geographic-view' : ''}`}>
            <Suspense
              fallback={
                <div className="scene-loading">
                  <Loader2 className="spin" size={24} />
                  <span>Building the operational view</span>
                </div>
              }
            >
              {world.airports.length === 0 ? (
                <div className="scenario-empty-map">
                  <div>
                    <Tag tone="cyan">NEW SIMULATION</Tag>
                    <h2>Your next scenario starts here</h2>
                    <p>
                      Describe an airport, flight and disruption by voice or text. The agent will
                      create the simulation and compare recovery options.
                    </p>
                    <button
                      className="primary-button"
                      onClick={() => commandInput.current?.focus()}
                    >
                      Describe a scenario <ArrowRight size={14} />
                    </button>
                  </div>
                </div>
              ) : mapStyle === 'geographic' ? (
                <GoogleMaps3D
                  world={world}
                  mode={mode}
                  selectedId={selectedId}
                  onSelect={select}
                  apiKey={config.google_maps_api_key ?? ''}
                  onClose={() => setMapStyle('architectural')}
                />
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
                    {view === 'airport'
                      ? 'Airport'
                      : view === 'region'
                        ? 'Regional network'
                        : 'Passenger'}
                  </button>
                ))}
              </div>
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
              <div className={`planner-badge ${chosenMode === 'ai' ? 'cyan' : 'slate'}`}>
                <Zap size={12} />
                <span>
                  {chosenMode === 'ai'
                    ? world.decisions.some((item) => item.mode === 'ai')
                      ? 'AI VERIFIED'
                      : 'AI CONFIGURED'
                    : 'LOCAL SOLVER'}
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
            {!busy &&
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
                  {mapStyle !== 'geographic' && mode === 'region'
                    ? 'SCHEMATIC NETWORK'
                    : homeAirport
                      ? `${Math.abs(homeAirport.lat).toFixed(3)}° ${homeAirport.lat >= 0 ? 'N' : 'S'} · ${Math.abs(homeAirport.lon).toFixed(3)}° ${homeAirport.lon >= 0 ? 'E' : 'W'}`
                      : 'No airport selected'}
                </span>
                <span className="map-site-code">
                  {mode === 'region' ? 'NETWORK' : (homeAirport?.code ?? 'NETWORK')}
                </span>
              </div>
              <div className="map-legend">
                <span>
                  <i className="legend-line cyan" />
                  Observed traffic
                </span>
                <span>
                  <i className="legend-line amber dashed" />
                  Scenario change
                </span>
                <span>
                  <i className="legend-line mint" />
                  Recovery plan
                </span>
              </div>
            </div>
            <div className="map-disclaimer">
              {mapStyle === 'geographic'
                ? 'GEOGRAPHIC TERRAIN · SIMULATED OPERATIONS'
                : 'CUSTOM 3D MODEL · SYNTHETIC OPERATIONS'}
            </div>
            <div className="map-action">
              <button
                className="secondary-button"
                onClick={showScenario}
                title="Exemple de condiții. Descrie un scenariu propriu prin voce sau text."
              >
                <SlidersHorizontal size={14} />
                Exemple rapide
              </button>
              <button
                className="primary-button"
                disabled={busy}
                onClick={() => {
                  void replan();
                }}
              >
                {busy ? <Loader2 size={14} className="spin" /> : <Sparkles size={14} />}
                Replan <span>{chosenMode === 'ai' ? 'AI' : 'SOLVER'}</span>
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
          onScenario={showScenario}
          busy={busy}
          config={config}
          showLog={showLog}
        />
      </div>
      <footer className={`voice-console ${isVoiceActive ? 'listening' : ''}`}>
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
            Describe a scenario in Romanian or English
          </label>
          <input
            id="scenario-command"
            aria-describedby={lastReply ? 'command-reply' : undefined}
            ref={commandInput}
            placeholder={
              isVoiceActive
                ? 'Ascult. Descrie scenariul prin voce…'
                : 'Descrie un scenariu, resursele și condițiile…'
            }
            value={command}
            onChange={(event) => setCommand(event.target.value)}
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
              : lastReply
                ? `Ultimul răspuns verificat: ${lastReply}`
                : isVoiceActive
                  ? voiceTranscript || 'OpenAI voice session active'
                  : lastCommand ||
                    (config.ai_configured
                      ? 'Voice + Decisions + Ultrafast · live reasoning, validated actions'
                      : 'Text & voice need OpenAI access. Scenario controls and solver are available.')}
          </output>
          <button
            className="send-command"
            type="submit"
            disabled={busy || !command.trim()}
            aria-label="Send scenario command"
          >
            <Send size={21} />
          </button>
        </form>
        <div className="console-meta">
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
          <button onClick={() => setModal('help')} className="icon-button" aria-label="Demo guide">
            <HelpCircle size={17} />
          </button>
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
      {modal === 'scenario' && (
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
      {modal === 'reset' && (
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
      {modal === 'help' && (
        <Modal title="Put the network to the test" onClose={closeModal}>
          <div className="demo-steps">
            <div>
              <span>01</span>
              <h3>Start with an incident</h3>
              <p>“Autocarul B12 s-a defectat. Păstrează familiile împreună.”</p>
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
            <button className="primary-button" onClick={closeModal}>
              Let’s begin
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
      type: 'Airport',
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
          autoFocus
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
            caption: 'Observed traffic · separate from simulation',
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
          <span>Current simulation</span>
          <code>v{world.version}</code>
        </div>
        <div className="session-actions">
          <button className="secondary-button" onClick={() => downloadSession(world)}>
            <ArrowDownToLine size={15} />
            Export audit
          </button>
          <button className="secondary-button" onClick={onReset}>
            <RefreshCw size={15} />
            New simulation
          </button>
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
function ClipboardIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
    >
      <rect x="4" y="2" width="12" height="16" rx="1" />
      <path d="M7 6h6M7 10h6M7 14h4" />
    </svg>
  );
}
