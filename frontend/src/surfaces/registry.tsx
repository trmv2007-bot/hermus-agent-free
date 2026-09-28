// Which component renders which kind of surface.
//
// The type is the enforcement, not a comment. `RENDERERS` is a total map over
// LAUNCHER_KINDS, so adding a kind to the launcher without writing a panel for
// it stops this file compiling. That is the §4 failure caught at the type level:
// "not implemented" has to be a build error on the way in, not something the
// operator discovers by clicking an icon.
//
// Kinds in the vocabulary that are not in the launcher (files, ide, diff,
// media) have no renderer by design. PRODUCT.md §3 lists Files and an embedded
// browser as surfaces the product still owes, so they stay typed and
// `unbuiltKinds` reports them when a plan asks for one. They are deliberately
// absent from the fan, so the launcher cannot advertise a feature that does not
// exist. Only /workspace/ops can still name one, and the answer there is
// UnbuiltPanel, which says so in words.

import type { SurfaceKind } from "../state/surfaces";
import { KIND_TITLES } from "../state/surfaces";
import { LAUNCHER_KINDS } from "../state/launcher";
import { ComputerPanel, EvidencePanel, LogsPanel, MemoryPanel, ModelPanel, MissionPanel, TelemetryPanel, TerminalPanel, WorkerPanel } from "./panels";
import { ChatPanel } from "./ChatPanel";
import { SettingsPanel } from "./SettingsPanel";
import { ModelsPanel } from "./ModelsPanel";
import { VoicePanel } from "./VoicePanel";

export interface SurfaceComponent {
  (props: { surfaceId: string }): JSX.Element;
}

/** Every kind the operator can open by hand. A panel is owed for each one. */
export type LauncherKind = (typeof LAUNCHER_KINDS)[number];

export const RENDERERS: Record<LauncherKind, SurfaceComponent> = {
  chat: ChatPanel,
  mission: MissionPanel,
  evidence: EvidencePanel,
  worker: WorkerPanel,
  telemetry: TelemetryPanel,
  logs: LogsPanel,
  // The diagnostics drawer became this. /control still exists and is still the
  // escape hatch, but the surface you open to change something is this.
  model: ModelSurface,
  diagnostics: SettingsPanel,
  computer: ComputerPanel,
  memory: MemoryPanel,
  terminal: TerminalPanel,
  voice: VoicePanel,
};

/**
 * The only reachable path to a kind with no panel, and it states what that is.
 *
 * §4 requires an unimplemented capability to name itself rather than sit behind
 * an empty frame. So this gives the four facts: what is missing, that nothing
 * backs it, that the launcher does not offer it, and the clause that forbids
 * dressing it up. An empty panel that looks healthy is the failure this rebuild
 * exists to remove, and it is worse when it is wearing a feature's name.
 */
export function UnbuiltPanel({ kind }: { kind: SurfaceKind }) {
  return (
    <div className="panel">
      <p className="probe" data-state="pending">
        <b>{KIND_TITLES[kind]} is not built yet</b>
        <span>No endpoint backs this surface, so there is nothing here to read. It is not offered in the launcher.</span>
        <em>PRODUCT.md §4: not implemented must not be dressed up as a pending feature.</em>
      </p>
    </div>
  );
}

/**
 * The Model surface answers two questions that were previously split apart:
 * "what is answering right now" and "what could I put on this machine".
 *
 * Status alone left the second question unanswerable -- you could see which
 * roles were loaded and had no way to add a vision model without editing .env.
 * The chooser alone would show options without saying what is already running,
 * which is the question you have first.
 */
function ModelSurface() {
  return (
    <div className="surface-stack">
      <ModelPanel />
      <ModelsPanel />
    </div>
  );
}

export function rendererFor(kind: SurfaceKind): SurfaceComponent {
  return RENDERERS[kind as LauncherKind] ?? (() => <UnbuiltPanel kind={kind} />);
}
