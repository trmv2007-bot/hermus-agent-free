// Which component renders which kind of surface. A kind with no renderer shows a
// panel that says it is not built, rather than an empty frame implying a feature
// that exists.

import type { SurfaceKind } from "../state/surfaces";
import { ChatPanel, EvidencePanel, ModelPanel, MissionPanel, PendingPanel, WorkerPanel } from "./panels";

export interface SurfaceComponent {
  (props: { surfaceId: string }): JSX.Element;
}

export const RENDERERS: Partial<Record<SurfaceKind, SurfaceComponent>> = {
  chat: ChatPanel,
  mission: MissionPanel,
  evidence: EvidencePanel,
  worker: WorkerPanel,
  model: ModelPanel,
};

export function rendererFor(kind: SurfaceKind): SurfaceComponent {
  return RENDERERS[kind] ?? (() => <PendingPanel label={kind} />);
}
