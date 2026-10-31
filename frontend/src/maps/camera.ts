import type { Camera, GoogleMapElement } from './contracts';

/** Google camera methods may throw before returning, as well as reject later. */
export async function moveCamera(
  map: Pick<GoogleMapElement, 'flyCameraTo'>,
  target: Camera,
  durationMillis: number,
): Promise<boolean> {
  try {
    await map.flyCameraTo({ endCamera: target, durationMillis });
    return true;
  } catch {
    return false;
  }
}
