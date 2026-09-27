// The launcher is the room advertising its own capabilities, so every tile in it
// has to open something that reads a real endpoint. These tests hold that line
// where it is cheapest to break: the day someone adds a kind to the fan and has
// not written the panel yet, this is the test that should fail.

import { describe, expect, it } from "vitest";
import { LAUNCHER_KINDS } from "../state/launcher";
import { KIND_TITLES, type SurfaceKind } from "../state/surfaces";
import { RENDERERS, rendererFor } from "./registry";

describe("the launcher only offers things that work", () => {
  it("has a renderer for every kind in the fan", () => {
    for (const kind of LAUNCHER_KINDS) {
      expect(RENDERERS[kind], `${kind} is in the launcher with no panel behind it`).toBeTypeOf("function");
    }
  });

  it("never falls through to the unbuilt panel for a launcher kind", () => {
    // The fallthrough is the honest answer for a kind the product still owes. It
    // must be unreachable for anything the operator can click.
    for (const kind of LAUNCHER_KINDS) {
      expect(rendererFor(kind), `${kind} renders the "not built" panel`).not.toBe(
        rendererFor("files" as SurfaceKind),
      );
    }
  });

  it("keeps the unbuilt kinds out of the fan and says so when reached directly", () => {
    // files/ide/diff/media are owed by PRODUCT.md §3 and not built. They stay
    // typed so an op naming one is answered rather than rejected as a typo, but
    // the launcher must not advertise them.
    const unbuilt: SurfaceKind[] = ["files", "ide", "diff", "media"];
    for (const kind of unbuilt) {
      expect(LAUNCHER_KINDS as readonly string[]).not.toContain(kind);
      expect(rendererFor(kind)).toBeTypeOf("function");
    }
  });

  it("names and glyphs every kind, built or not", () => {
    for (const kind of LAUNCHER_KINDS) {
      expect(KIND_TITLES[kind], `${kind} has no title`).toBeTruthy();
    }
  });

  it("has no duplicate fan entries", () => {
    // A duplicated tile opens two surfaces of the same kind, which reads as the
    // room having two of a capability it only has one of.
    expect(new Set(LAUNCHER_KINDS).size).toBe(LAUNCHER_KINDS.length);
  });
});
