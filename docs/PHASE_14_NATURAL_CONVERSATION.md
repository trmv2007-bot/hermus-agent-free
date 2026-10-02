# Phase 14 — Natural Conversation + Interruption

Status: **Complete**

Phase 14 adds a conversational control plane above the canonical Mission Runtime. It does not create a second execution engine.

## Capabilities
- Conversation sessions with bounded recent-turn context.
- Explicit session/run correlation for queued and WebSocket work.
- Mid-run steering through the same RunBus control path used by the runtime.
- Cooperative interruption/cancellation at runtime step boundaries.
- HTTP controls for session steering, interruption, run steering and run interruption.
- WebSocket steer / redirect actions.
- Background run notifications for completion, errors and cancellation.
- Voice output interruption state using generation tokens.
- A voice interruption endpoint that signals clients to stop current playback and can cancel an associated run.
- Existing SSE/WebSocket run events remain the live progress channel.

## Interfaces
- POST /conversation/session
- GET /conversation/{session_id}
- POST /conversation/{session_id}/steer
- POST /conversation/{session_id}/interrupt
- GET /conversation/{session_id}/notifications
- POST /runs/{run_id}/steer
- POST /runs/{run_id}/interrupt
- POST /voice/interrupt
- WebSocket actions: steer, redirect, cancel

A queued job can include session_id; the gateway then associates the run with that conversation and records the user turn.

## Architecture

```text
User / Voice / WebSocket
        |
        v
Conversation Manager
   |          |
   |          +--> bounded turn context
   |
   +--> RunBus.steer / RunBus.cancel
                 |
                 v
          Canonical Runtime
          Mission / Agent loop
                 |
                 +--> SSE / WS events
                 |
                 +--> conversation notifications
```

Voice output interruption uses a generation token. A client can stop currently playing audio when /voice/interrupt returns client_action=stop_current_audio. The server-side generation invalidates stale output state. If a blocking TTS backend is already inside a single synthesis/playback call, the endpoint cannot physically terminate that external process; it provides the authoritative interrupt signal for the voice client and prevents stale output from being accepted as the current generation.

## Safety and runtime invariants
- Steering is an instruction to the existing agent loop; it does not grant tools or permissions.
- Cancellation remains cooperative and is polled at runtime step boundaries.
- Mission verification, approvals, red-lines, sandboxing and emergency controls remain authoritative.
- Conversation context is bounded; it is not an unbounded transcript injection.
- Background notifications observe existing run events rather than executing work.

## Regression coverage
`tests/test_phase14_conversation.py` covers session context, steering, interruption and notification lifecycle. `tests/test_voice_executive_bridge.py` covers output-generation invalidation.

The GitHub build session added the regression tests but did not execute the full repository test suite locally.