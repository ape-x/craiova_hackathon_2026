export type CameraFocus = { request: number; context: string; following: boolean };

/** Keep effect replays on the same target; manual context changes end following. */
export function nextCameraFocus(
  previous: CameraFocus | null,
  request: number,
  context: string,
): CameraFocus {
  return {
    request,
    context,
    following:
      request > 0 &&
      (previous?.request !== request || (previous.context === context && previous.following)),
  };
}
