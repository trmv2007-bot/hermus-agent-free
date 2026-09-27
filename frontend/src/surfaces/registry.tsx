// Which component renders which kind of surface. A kind with no renderer shows a
// panel that says it is not built, rather than an empty frame implying a feature
// that exists.

import type { SurfaceKind } from "../state/surfaces";
import { ChatPanel, ComputerPanel, DiagnosticsPanel, EvidencePanel, LogsPanel, MemoryPanel, ModelPanel, MissionPanel, PendingPanel, TelemetryPanel, TerminalPanel, WorkerPanel } from "./panels";
import { VoicePanel } from "./VoicePanel";
import { SettingsPanel } from "./SettingsPanel";

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
  diagnostics: DiagnosticsPanel,
  computer: ComputerPanel,
  memory: MemoryPanel,
  terminal: TerminalPanel,
  voice: VoicePanel,
  settings: SettingsPanel,
};

export function rendererFor(kind: SurfaceKind): SurfaceComponent {
  return RENDERERS[kind] ?? (() => <PendingPanel label={kind} />);
}
