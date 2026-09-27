// The floor's cell size.
//
// This is pure arithmetic, and it used to live inside Backdrop.tsx — which
// meant the test that covers it had to import a React component module. In the
// node test environment that import took the whole component tree (React,
// zustand, the pan store) along with it, and the worker died before a single
// assertion ran. The suite reported a crashed worker rather than a failing
// test, which is the worst of both: it looks like infrastructure noise and it
// is easy to miss.
//
// A number that is worth testing belongs somewhere a test can reach without
// dragging a framework in with it.

const BASE_CELL = 52;

/**
 * The floor's cell size, in screen pixels.
 *
 * It scales with the zoom, then steps by 4x when it would fall under 12px or
 * rise over 96px. That step is what keeps the floor reading as a floor: below
 * 12px the 1px lines overlap into a solid wash, and above 96px it is too sparse
 * to feel like ground under the room. Without it, zooming out slowly turns the
 * grid into a grey fog.
 */
export function gridCell(zoom: number): number {
  // Clamp BEFORE the stepping loops, because the loops do not terminate on
  // every input. `gridCell(0)` is `0 * 4` forever, and `gridCell(-1)` walks off
  // to -Infinity — both spin until the tab is killed. A NaN zoom escapes both
  // loops but returns NaN, which renders as no floor at all. So the value is
  // sanitised first and the loops can assume a positive finite number.
  const z = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  let cell = BASE_CELL * z;
  while (cell < 12) cell *= 4;
  while (cell > 96) cell /= 4;
  return cell;
}
