# Banco Aurora — classify Concierge chat HTTP 500

The previous diagnostic established Portal login=200, Portal Core state=200,
Portal chat=500 and a direct Gateway→Concierge chat=500.

The likely failing area is Concierge or one of its upstreams, not the Portal
frontend. This additive tool reads only the current Kubernetes pod inventory
and recent logs. It **never** prints raw logs, request or response bodies,
customer identifiers, secret values, URLs or environment variables. It outputs
exception types and allowlisted source-file line numbers to identify which
operation failed. It does not modify the existing reconciler.

From repo root:

```sh
unzip -o ~/Downloads/banco-aurora-chat-upstream-diagnostics-v2.zip -d .
python3 -m py_compile scripts/diagnose-aurora-upstream.py
python3 -m unittest discover -s tests -p 'test_upstream_diagnostic_v2.py' -v
python3 scripts/diagnose-aurora-upstream.py --since 30m --include-core --include-portal
```

If 30 minutes has elapsed since the failed request, reproduce **only the
non-transactional** `/api/chat` balance diagnostic (not `--s01`), then rerun
this log classifier with `--since 5m`.

- `httpx.ConnectError` around `agents/concierge.py:129`: Core workflow
  connection or DNS/network reachability; verify cluster service routing.
- `httpx.HTTPStatusError` around `agents/concierge.py:129` or `:164`:
  Core workflow read/write rejected an HTTP request; verify capability mapping
  and the corresponding Core pod logs without exposing key values.
- `openai.*Error` or an `HTTPException` on `agents/concierge.py:61-69`:
  governed model connection, model identity, API header or gateway policy.
  The published application converts planning errors to HTTP 503; verify the
  status propagation through Agent Manager if the caller sees HTTP 500.
- `KeyError`, `TypeError`, `ValidationError` inside `agents/concierge.py`:
  code or response-schema issue. Use the reported source line to isolate.
- No exception in Concierge logs: inspect Agent Manager gateway routing and
  logs around the same request time; a proxy-side HTTP 500 may precede the
  application handler.

The classifier is intentionally narrower than a log viewer. Unknown error
messages may need private inspection on your workstation for exact diagnosis.
Do not share raw logs, Kubernetes Secret YAML, pod environment dumps, or key
values; they can contain credentials and banking demo data.
