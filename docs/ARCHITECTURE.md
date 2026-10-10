# Architecture

The service has one `SessionManager` and one `SafetyController`. API handlers call those authorities rather than keeping route-local state. Providers are replaceable interfaces: speech, vision, cursor, and browser adapters can be unavailable without disabling health, session, telemetry, or safety APIs.

The normal command path is:

`provider -> normalized transcript -> CommandRouter -> SafetyController -> adapter -> CommandResult + telemetry`

Telemetry is an in-memory publisher with bounded per-client queues. Evaluation stores bounded monotonic timing samples and labels metrics as measured or not measured. No raw audio/video or secrets are persisted or published. FastAPI lifespan starts the session and shuts down providers; real camera activation is explicit through `/api/v1/vision/start`.

