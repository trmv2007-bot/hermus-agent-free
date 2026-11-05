# HERMUS Production & Real-World Integration

The seven post-roadmap goals are now represented in the repository.

## 1. Control Room
The Control Room is the single UI projection. It includes a unified command center,
attention state, distributed topology, integration/profile/voice summaries and cross-system search.

## 2. Distributed execution
Phase 17 remains the coordination authority. core/distributed_transport.py adds signed,
expiring job envelopes. A production deployment should place an mTLS-capable transport
adapter around these envelopes. Envelopes never grant local permissions.

## 3. Integrations
core/integrations.py now contains an explicit external-service catalog for calendar,
email, messaging, files, browser, smart-home and development adapters. Credentials must
remain in environment/secret stores and are never persisted by the registry.

## 4. Voice
core/voice_stream.py adds stream lifecycle, chunk accounting and authoritative interruption
tokens. Existing STT/TTS and VoiceExecutiveBridge remain the execution path.

## 5. Reliability
CI, smoke tests, lint/typecheck and deployment health checks are first-class entry points.
Run make test-full, make lint, make typecheck and make doctor before deployment.

## 6. Deployment
Docker and Compose provide a reproducible gateway deployment with persistent data storage
and a health check. Native setup.sh remains supported.

## 7. Personalization
core/personal_profile.py provides an explicit durable profile for assistant name,
communication style, language, voice preferences, routines, trusted devices and allowed capabilities.

### Security boundary
None of these layers bypasses approval gates, red lines, sandboxing, ToolGateway, ModelGateway,
MemoryFacade, JobQueue, verification or emergency-stop controls.

### What remains environment-specific
Actual Google/Microsoft calendar, email, messaging, smart-home and remote-node accounts
must be connected by their provider-specific adapters and credentials. The repository now
has the common catalog, gateway surface and safety boundary for those adapters; it does not
invent or silently obtain third-party credentials.
