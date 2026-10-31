import { useEffect, useMemo, useRef, useState } from 'react';
import { AlertCircle, Building2, Globe2, Loader2, Plane, RotateCcw, Users, X } from 'lucide-react';
import type { SceneMode, World } from '../types';
import type { GoogleMapElement, LoadedMaps } from './contracts';
import { getMapsLoader } from './loader';
import { buildOverlays, cameraFor, observedPoint, updateObservationTracks } from './overlays';
import type { ObservationTracks } from './overlays';
import { ALL_MODELS, buildModelDescriptors } from './models';
import type { ModelVisibility } from './models';
import { createModelLayer } from './modelLayer';
import type { ModelLayer } from './modelLayer';
import { moveCamera } from './camera';
import { createMapInitialization, overlayFailureMessage } from './initialization';
import type { MapInitialization, MapLoadingState, OverlayStage } from './initialization';
import { transferReview } from '../resourceLabels';
import { privateTransfer } from '../priceEvidence';
import './maps.css';

export interface GoogleMaps3DProps {
  world: World;
  mode: SceneMode;
  selectedId: string | null;
  onSelect: (id: string) => void;
  apiKey: string;
  onClose?: () => void;
}

type Runtime = LoadedMaps & { map: GoogleMapElement; initialization: MapInitialization };

export function GeographicLegend({
  mode,
  selectedReview,
  roadGeometry,
  hasObservations,
}: {
  mode: World['mode'];
  selectedReview: ReturnType<typeof transferReview> | null;
  roadGeometry: { sourced: boolean; connectionOnly: boolean };
  hasObservations: boolean;
}) {
  return (
    <div className={`geographic-map-legend${selectedReview ? ' is-transfer-review' : ''}`}>
      {selectedReview && (
        <div className="geographic-route-review" role="status">
          <strong>{selectedReview.title}</strong>
          <span>{selectedReview.status}</span>
          <small>
            {selectedReview.sourceTime}.{' '}
            {roadGeometry.sourced
              ? 'Dated prediction, not a vehicle position.'
              : 'Endpoints only; no driving path shown.'}
          </small>
        </div>
      )}
      {hasObservations && (
        <span>
          <i className="geo-observed" />
          Received observations
        </span>
      )}
      {roadGeometry.sourced && (
        <span>
          <i className="geo-road" />
          Google Routes · predicted road
        </span>
      )}
      {roadGeometry.connectionOnly && <span>Connection only · road geometry unavailable</span>}
      {mode !== 'live' && (
        <>
          <span>
            <i className="geo-scenario" />
            Simulated network
          </span>
          <span>
            <i className="geo-confirmed" />
            Confirmed in simulator
          </span>
        </>
      )}
      {mode !== 'live' ? (
        <small>
          Authored terminal, fleet and families · simulated layout and positions. Links are
          schematic. Observed aircraft enlarged 2.5×; Google terrain is real.
        </small>
      ) : hasObservations ? (
        <small>
          Observed aircraft at provider positions, enlarged 2.5×. Google terrain is real; airport
          status requires verification.
        </small>
      ) : !selectedReview ? (
        <small>Google terrain is real; airport status requires verification.</small>
      ) : null}
    </div>
  );
}

/** Mount only after the operator explicitly chooses the geographic view. */
export default function GoogleMaps3D({
  world,
  mode,
  selectedId,
  onSelect,
  apiKey,
  onClose,
}: GoogleMaps3DProps) {
  const host = useRef<HTMLDivElement>(null);
  const current = useRef({ world, mode, selectedId, onSelect });
  current.current = { world, mode, selectedId, onSelect };
  const tracks = useRef<ObservationTracks>(new Map());
  const [runtime, setRuntime] = useState<Runtime | null>(null);
  const [state, setState] = useState<MapLoadingState>('loading');
  const [error, setError] = useState('');
  const [clock, setClock] = useState(Date.now());
  const [cameraError, setCameraError] = useState('');
  const [modelError, setModelError] = useState('');
  const [overlayError, setOverlayError] = useState('');
  const [roadGeometry, setRoadGeometry] = useState({ sourced: false, connectionOnly: false });
  const [visibility, setVisibility] = useState<ModelVisibility>(ALL_MODELS);
  const modelLayer = useRef<ModelLayer | null>(null);

  useEffect(() => {
    const interval = setInterval(() => setClock(Date.now()), 15000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    let map: GoogleMapElement | undefined;
    let libraryLoaded = false;
    setState('loading');
    setError('');
    setRuntime(null);
    setCameraError('');
    setModelError('');
    setOverlayError('');
    const loader = getMapsLoader();
    const initialization = createMapInitialization((next) => {
      setError(next.error);
      setState(next.state);
    });
    const offAuth = loader.onAuthenticationFailure(() => initialization.fail('authentication'));
    void loader
      .load(apiKey)
      .then((libraries) => {
        if (!initialization.active || !host.current) return;
        libraryLoaded = true;
        const { altitudeMode: _altitudeMode, ...initialCamera } = cameraFor(
          current.current.world,
          current.current.mode,
          current.current.selectedId,
        );
        map = new libraries.maps3d.Map3DElement({
          ...initialCamera,
          // Constructor centers use MSL. Resolve terrain-relative closeups with
          // the documented CameraOptions after Google reports a steady map.
          range: Math.max(6500, initialCamera.range),
          mode: 'SATELLITE',
          defaultUIHidden: false,
        });
        map.style.width = '100%';
        map.style.height = '100%';
        map.setAttribute(
          'aria-label',
          'Google geographic 3D map with source-labelled aircraft observations',
        );
        initialization.watch(map);
        host.current.append(map);
        if (initialization.active) setRuntime({ ...libraries, map, initialization });
      })
      .catch(() =>
        initialization.fail(
          !apiKey.trim() ? 'configuration' : libraryLoaded ? 'initialization' : 'load',
        ),
      );
    return () => {
      initialization.dispose();
      offAuth();
      if (map) {
        try {
          Promise.resolve(map.stopCameraAnimation()).catch(() => undefined);
        } catch {
          /* Map may have failed initialization. */
        }
        map.replaceChildren();
        map.remove();
      }
    };
  }, [apiKey]);

  useEffect(() => {
    updateObservationTracks(tracks.current, world.observations);
  }, [world.observations]);

  useEffect(() => {
    if (!runtime) return;
    return runtime.initialization.runIfReady(() => {
      setOverlayError('');
      const elements: HTMLElement[] = [];
      let stage: OverlayStage = 'derive overlays';
      try {
        const { markers, routes } = buildOverlays(world, selectedId, tracks.current, clock);
        const geometry = {
          sourced: routes.some((route) => route.geometry === 'sourced_road'),
          connectionOnly: routes.some((route) => route.geometry === 'connection_only'),
        };
        setRoadGeometry((previous) =>
          previous.sourced === geometry.sourced &&
          previous.connectionOnly === geometry.connectionOnly
            ? previous
            : geometry,
        );
        for (const route of routes) {
          // Google 3D polylines have no documented dash pattern. Separate segments
          // make simulated proposals visibly dashed without undocumented options.
          const paths = route.dashed
            ? route.path
                .slice(0, -1)
                .flatMap((point, index) =>
                  index % 2 === 0 ? [[point, route.path[index + 1]]] : [],
                )
            : [route.path];
          for (const path of paths) {
            stage = 'create route';
            const line = new runtime.maps3d.Polyline3DElement({
              path,
              strokeColor: route.color,
              strokeWidth: route.selected ? 7 : route.observed ? 4 : 2.5,
              altitudeMode: route.geometry
                ? 'CLAMP_TO_GROUND'
                : route.observed
                  ? 'ABSOLUTE'
                  : 'RELATIVE_TO_GROUND',
              geodesic: route.geometry !== 'sourced_road',
              drawsOccludedSegments: true,
              zIndex: route.selected ? 20 : route.observed ? 10 : 1,
            });
            elements.push(line);
            stage = 'attach route';
            runtime.map.append(line);
          }
        }
        for (const marker of markers) {
          stage = 'create marker';
          const element = new runtime.maps3d.Marker3DInteractiveElement({
            position: marker.point,
            label: marker.label,
            title: marker.title,
            altitudeMode:
              marker.observed && marker.point.altitude !== undefined
                ? 'ABSOLUTE'
                : marker.point.altitude
                  ? 'RELATIVE_TO_GROUND'
                  : 'CLAMP_TO_GROUND',
            extruded: marker.observed,
            drawsWhenOccluded: true,
            sizePreserved: true,
            zIndex: marker.selected ? 30 : marker.observed ? 20 : 10,
          });
          elements.push(element);
          stage = 'create marker pin';
          element.append(
            new runtime.marker.PinElement({
              background: marker.color,
              borderColor: '#112532',
              glyphColor: '#112532',
              scale: marker.selected ? 1.25 : marker.observed ? 0.8 : 0.95,
            }),
          );
          element.addEventListener('gmp-click', (event) => {
            event.stopPropagation();
            current.current.onSelect(marker.selectionId ?? marker.id);
          });
          stage = 'attach marker';
          runtime.map.append(element);
        }
      } catch (cause) {
        elements.forEach((element) => element.remove());
        if (runtime.initialization.ready) setOverlayError(overlayFailureMessage(stage, cause));
      }
      return () => elements.forEach((element) => element.remove());
    });
  }, [runtime, world, selectedId, clock, state]);

  const modelDescriptors = useMemo(
    () => buildModelDescriptors(world, selectedId, visibility),
    [world, selectedId, visibility],
  );
  const currentModels = useRef(modelDescriptors);
  currentModels.current = modelDescriptors;
  const modelSignature = JSON.stringify(modelDescriptors);
  useEffect(() => {
    if (!runtime || state !== 'ready' || !runtime.initialization.ready) return;
    setModelError('');
    const layer = createModelLayer(runtime.map, runtime.maps3d, setModelError, undefined, (id) =>
      current.current.onSelect(id),
    );
    modelLayer.current = layer;
    layer.update(currentModels.current);
    return () => {
      layer.dispose();
      if (modelLayer.current === layer) modelLayer.current = null;
    };
  }, [runtime, state]);
  useEffect(() => {
    modelLayer.current?.update(currentModels.current);
  }, [modelSignature]);

  const camera = useMemo(() => cameraFor(world, mode, selectedId), [world, mode, selectedId]);
  const cameraSignature = JSON.stringify(camera);
  useEffect(() => {
    if (!runtime) return;
    return runtime.initialization.runIfReady(() => {
      setCameraError('');
      const target = cameraFor(
        current.current.world,
        current.current.mode,
        current.current.selectedId,
      );
      let cancelled = false;
      void moveCamera(
        runtime.map,
        target,
        window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 1200,
      ).then((moved) => {
        if (!moved && !cancelled && runtime.initialization.ready)
          setCameraError('Automatic camera movement failed. Use the map controls.');
      });
      return () => {
        cancelled = true;
      };
    });
  }, [runtime, cameraSignature, state]);

  const fresh = world.observations.filter((observation) => {
    const stamp = observation.observed_at ?? observation.timestamp;
    return (
      observation.mode !== 'SANDBOX' &&
      observation.mode !== 'REPLAY' &&
      stamp &&
      Number.isFinite(Date.parse(stamp)) &&
      clock - Date.parse(stamp) >= -60000 &&
      clock - Date.parse(stamp) <= 90000
    );
  }).length;
  const selectedTransfer =
    world.mode === 'live'
      ? world.resources.find((item) => item.id === selectedId && privateTransfer(item))
      : undefined;
  const selectedReview = selectedTransfer ? transferReview(world, selectedTransfer, clock) : null;
  const hasObservations = world.observations.some(
    (observation) =>
      Boolean(observation.id ?? observation.fr24_id) && observedPoint(observation) !== null,
  );

  return (
    <section className="geographic-map" aria-label="Geographic 3D operations view">
      <div className="geographic-map-canvas" ref={host} />
      {(state === 'loading' || state === 'rendering') && (
        <div className="geographic-map-state" role="status">
          <Loader2 size={25} className="spin" />
          <strong>
            {state === 'loading' ? 'Connecting geographic 3D' : 'Rendering geographic terrain'}
          </strong>
          <span>Google Maps · provider access and local detail are being checked</span>
        </div>
      )}
      {state === 'error' && (
        <div className="geographic-map-state geographic-map-error" role="alert">
          <AlertCircle size={28} />
          <strong>Geographic view unavailable</strong>
          <p>{error}</p>
          {onClose && (
            <button type="button" onClick={onClose}>
              <X size={15} />
              Return to simulation view
            </button>
          )}
          <span>No replacement imagery has been loaded.</span>
        </div>
      )}
      {state === 'ready' && (
        <>
          <div className="geographic-map-source">
            <Globe2 size={14} />
            <span>GOOGLE GEOGRAPHIC 3D</span>
            {(!selectedReview || hasObservations) && <b>{fresh} fresh aircraft observations</b>}
          </div>
          <GeographicLegend
            mode={world.mode}
            selectedReview={selectedReview}
            roadGeometry={roadGeometry}
            hasObservations={hasObservations}
          />
          <div className="geographic-model-controls" role="group" aria-label="3D object layers">
            <span>3D OBJECTS</span>
            {(
              [
                ['vehicles', 'Vehicles', Plane],
                ['passengers', 'Passengers', Users],
                ['terminal', 'Terminal', Building2],
              ] as const
            )
              .filter(([key]) => world.mode !== 'live' || key === 'vehicles')
              .map(([key, label, Icon]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={visibility[key]}
                  onClick={() =>
                    setVisibility((previous) => ({ ...previous, [key]: !previous[key] }))
                  }
                >
                  <Icon size={13} />
                  {world.mode === 'live' ? 'Observed aircraft' : label}
                </button>
              ))}
          </div>
          <button
            type="button"
            className="geographic-map-recenter"
            aria-label="Recenter geographic map"
            onClick={() => {
              if (runtime?.initialization.ready)
                void moveCamera(runtime.map, camera, 900).then((moved) => {
                  if (!moved && runtime.map.isConnected && runtime.initialization.ready)
                    setCameraError('Could not recenter the map.');
                });
            }}
          >
            <RotateCcw size={16} />
          </button>
        </>
      )}
      {state === 'ready' && cameraError && (
        <div className="geographic-map-warning" role="status">
          {cameraError}
        </div>
      )}
      {state === 'ready' && modelError && (
        <div className="geographic-map-warning geographic-model-warning" role="status">
          {modelError}
        </div>
      )}
      {state === 'ready' && overlayError && (
        <div className="geographic-map-warning geographic-overlay-warning" role="status">
          {overlayError}
        </div>
      )}
    </section>
  );
}
