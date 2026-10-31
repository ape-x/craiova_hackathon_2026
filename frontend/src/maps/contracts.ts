/** Typed subset of Google Maps JS weekly 3D API, verified 2026-10-31. */
export interface GeoPoint {
  lat: number;
  lng: number;
  altitude?: number;
}
export interface Camera {
  center: GeoPoint;
  range: number;
  tilt: number;
  heading: number;
}
export interface GoogleMapElement extends HTMLElement {
  center: GeoPoint;
  range: number;
  tilt: number;
  heading: number;
  flyCameraTo(options: { endCamera: Camera; durationMillis: number }): void | Promise<void>;
  stopCameraAnimation(): void | Promise<void>;
}
export interface MarkerOptions {
  position: GeoPoint;
  label: string;
  title: string;
  altitudeMode: 'ABSOLUTE' | 'CLAMP_TO_GROUND' | 'RELATIVE_TO_GROUND';
  extruded?: boolean;
  drawsWhenOccluded?: boolean;
  sizePreserved?: boolean;
  zIndex?: number;
}
export interface LineOptions {
  path: GeoPoint[];
  strokeColor: string;
  strokeWidth: number;
  altitudeMode: 'ABSOLUTE' | 'RELATIVE_TO_GROUND' | 'CLAMP_TO_GROUND';
  drawsOccludedSegments?: boolean;
  geodesic?: boolean;
  zIndex?: number;
}
export interface Maps3DLibrary {
  Map3DElement: new (
    options: Camera & {
      mode: 'SATELLITE' | 'HYBRID';
      defaultUIHidden?: boolean;
      gestureHandling?: string;
    },
  ) => GoogleMapElement;
  Marker3DInteractiveElement: new (options: MarkerOptions) => HTMLElement;
  Polyline3DElement: new (options: LineOptions) => HTMLElement;
  Model3DElement: new (options: ModelOptions) => HTMLElement;
}
export interface ModelOptions {
  src: string;
  position: GeoPoint;
  orientation: { heading: number; tilt: number; roll: number };
  scale: number;
  altitudeMode: 'ABSOLUTE' | 'CLAMP_TO_GROUND';
}
export interface MarkerLibrary {
  PinElement: new (options: {
    background: string;
    borderColor: string;
    glyphColor: string;
    scale: number;
  }) => HTMLElement;
}
export interface LoadedMaps {
  maps3d: Maps3DLibrary;
  marker: MarkerLibrary;
}
