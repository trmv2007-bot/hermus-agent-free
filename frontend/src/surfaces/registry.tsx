// Which component renders which kind of surface. A kind with no renderer shows a
// panel that says it is not built, rather than an empty frame implying a feature
// that exists.

import type { SurfaceKind } from "../state/surfaces";
import { ChatPanel, ComputerPanel, DiagnosticsPanel, EvidencePanel, LogsPanel, MemoryPanel, ModelPanel, MissionPanel, PendingPanel, TelemetryPanel, TerminalPanel, WorkerPanel } from "./panels";

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
};

export function rendererFor(kind: SurfaceKind): SurfaceComponent {
  return RENDERERS[kind] ?? (() => <PendingPanel label={kind} />);
}
