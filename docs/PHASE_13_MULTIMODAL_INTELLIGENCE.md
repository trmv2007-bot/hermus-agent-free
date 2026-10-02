# Phase 13 — Multimodal Intelligence

Status: Complete

HERMUS now has a unified multimodal evidence layer above the existing vision, document-ingestion and browser capabilities.

## Capabilities

- Image analysis through the existing `vision_analyze` tool and canonical ModelGateway.
- Structured multimodal evidence with modality, source, confidence, model and artifacts.
- Workspace-scoped file access for multimodal APIs; paths outside the workspace are rejected.
- Document understanding through the existing canonical `document_ingest` extraction pipeline.
- Browser screenshot + visual analysis through the existing Playwright/browser tool.
- Multimodal observations are recorded in the canonical WorldModel with provenance and permission scope.
- Multimodal observation events are emitted for Control Room/runtime visibility.
- Gateway status endpoint exposes recent multimodal evidence.

## Gateway

- POST `/multimodal/image`
- POST `/multimodal/document`
- POST `/multimodal/browser`
- GET `/multimodal/status`

## Architecture

Attachment / Browser → MultimodalIntelligence → Vision / Document Ingest → Evidence → WorldModel → Executive reasoning / verification

The multimodal layer does not create a second model gateway or execution engine. Vision inference continues through ModelGateway, document extraction uses `document_ingest`, and browser interaction uses the existing browser/tool permission path.

## Safety and privacy

- File analysis is restricted to the configured workspace.
- Multimodal analysis is observational; it does not grant write, shell, browser navigation or external-service permissions.
- Browser screenshots use the existing browser capability and its permission boundary.
- WorldModel provenance, confidence and permission metadata remain attached to observations.
- Existing approval, red-line, sandbox and emergency-stop controls remain authoritative for any subsequent action based on visual evidence.

## Regression coverage

Tests cover image evidence recording, workspace path confinement, canonical document ingestion and structured multimodal observation events.

A full repository test-suite run was not performed as part of the GitHub-only build session.
