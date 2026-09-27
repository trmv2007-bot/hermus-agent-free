// Which component renders which kind of surface. A kind with no renderer shows a
// panel that says it is not built, rather than an empty frame implying a feature
// that exists.

import type { SurfaceKind } from "../state/surfaces";
import { ComputerPanel, EvidencePanel, LogsPanel, MemoryPanel, ModelPanel, MissionPanel, PendingPanel, TelemetryPanel, TerminalPanel, WorkerPanel } from "./panels";
import { ChatPanel } from "./ChatPanel";
import { SettingsPanel } from "./SettingsPanel";
import { VoicePanel } from "./VoicePanel";

export interface SurfaceComponent {
  (props: { surfaceId: string }): JSX.Element;
}

export const RENDERERS: Partial<Record<SurfaceKind, SurfaceComponent>> = {
  chat: ChatPanel,
  mission: MissionPanel,
  evidence: EvidencePanel,
  worker: WorkerPanel,
  model: ModelPanel,
  telemetry: TelemetryPanel,
  logs: LogsPanel,
  // The diagnostics drawer became this. /control still exists and is still the
  // escape hatch, but the surface you open to change something is this.
  diagnostics: SettingsPanel,
  computer: ComputerPanel,
  memory: MemoryPanel,
  terminal: TerminalPanel,
  voice: VoicePanel,
};

export function rendererFor(kind: SurfaceKind): SurfaceComponent {
  return RENDERERS[kind] ?? (() => <PendingPanel label={kind} />);
}
