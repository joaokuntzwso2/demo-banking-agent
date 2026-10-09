# Operational Observability and Evidence

## Two different evidence streams

- **Business audit**: Core `events` table records `approval_requested`, `approval_decision`, `card_blocked`, `dispute_created`, `pix_completed`. Each event includes an `X-Correlation-ID`. The portal **Agent Activity** page displays these events. They are NOT OpenTelemetry spans.
- **Agent orchestration events**: Concierge response `data.steps` records `understand`, `plan`, specialist calls and compose, plus action completion after approval. The portal **Coordinated execution** panel displays these, NOT chain-of-thought and NOT traces.
- **Actual distributed traces**: When configured in the runtime, OTLP HTTP exporter and WSO2 Agent Manager instrumentation can show HTTP edges and gateway activity. Confirm exact spans and gateway policies **in your own installation**; never present mocked graph steps as real Agent Manager traces.

## Local optional OTLP export

All agents, BFF, MCP upstream and Core accept `AURORA_ENABLE_OTEL=true`. With a reachable collector, set:

```dotenv
AURORA_ENABLE_OTEL=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://REACHABLE_COLLECTOR:4318
```

Ensure the chosen OpenTelemetry SDK/instrumentation versions work with the target WSO2 instrumentation profile; avoid double-instrumenting platform-managed workloads. Services propagate `X-Correlation-ID` and HTTPX instrumentation propagates trace context when OTel is enabled. Correlation is helpful but does not itself prove a single contiguous trace across a managed gateway.

## Presenter evidence checklist

1. Six agent instances shown in the actual Agent Manager console.
2. Registered banking MCP Proxy and seven discovered tools.
3. Scope-to-tool mapping includes all seven tools.
4. One customer message; capture a real trace matching the operation time.
5. Account specialist read succeeds; prohibited Account->execute Pix request is **denied by gateway** and visible in its logs/traces.
6. Show payment reference in portal and matching Core event ID; capture the same business correlation ID at each traced hop where supported.
7. Show an evaluation suite built against real runs: routing correctness, forbidden tool invocation, Spanish fidelity, grounded policy citation, Pix no-double-debit.

**Do not invent latency, automation savings or ROI.** Capture metrics during dry runs and report only measured values, specifying sample sizes and environment.
