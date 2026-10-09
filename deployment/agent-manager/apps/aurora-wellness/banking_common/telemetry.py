"""Correlated business and agent events; optional OpenTelemetry when installed.

No model prompts, financial account identifiers or authorization secrets are
attached as telemetry attributes. For managed AMP use its documented OTel setup.
"""
from contextvars import ContextVar
import re,uuid,os
from contextlib import contextmanager
from fastapi import Request
_correlation=ContextVar('aurora_correlation',default=None)

def correlation_id():return _correlation.get() or uuid.uuid4().hex

def outbound_headers():
    headers={'X-Correlation-ID':correlation_id()}
    # Propagate W3C traceparent/tracestate and baggage, when an active context
    # exists. This lets gateway, BFF, hosted agents and MCP share a real trace.
    try:
        from opentelemetry.propagate import inject
        inject(headers)
    except ImportError:
        pass
    return headers

@contextmanager
def business_span(operation, **safe_attributes):
    """Custom child span; never attach customer records, prompt, tool args, or secrets.

    Works with Agent Manager's existing SDK provider as well as local OTLP.
    With no active OpenTelemetry tracer provider it becomes a no-op span.
    """
    try:
        from opentelemetry import trace
    except ImportError:
        yield None
        return
    tracer=trace.get_tracer('banco-aurora.workflows', '4.0')
    with tracer.start_as_current_span(operation) as span:
        for name,value in safe_attributes.items():
            if value is not None and isinstance(value,(str,int,float,bool)):
                span.set_attribute(name,value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            from opentelemetry.trace import Status,StatusCode
            span.set_status(Status(StatusCode.ERROR,type(exc).__name__))
            raise


def install_telemetry(app,service):
    @app.middleware('http')
    async def correlate(request:Request,call_next):
        supplied=request.headers.get('x-correlation-id','')
        cid=supplied if re.fullmatch(r'[A-Za-z0-9_-]{8,64}',supplied) else uuid.uuid4().hex
        token=_correlation.set(cid)
        try:
            response=await call_next(request)
            response.headers['X-Correlation-ID']=cid
            response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api/') else 'no-cache'
            return response
        finally:_correlation.reset(token)
    # Optional official OTLP exporter; platform-level auto-instrumentation may
    # already be active. Avoid double-instrumenting the same application.
    if os.getenv('AURORA_ENABLE_OTEL','false').lower()=='true':
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry import trace
            # Never replace a provider already installed by the hosting platform.
            existing=trace.get_tracer_provider()
            if type(existing).__name__ in ('ProxyTracerProvider','NoOpTracerProvider'):
                provider=TracerProvider(resource=Resource.create({'service.name':f'aurora-{service}'}))
                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
                trace.set_tracer_provider(provider)
            if not getattr(app.state,'_aurora_otel_installed',False):
                FastAPIInstrumentor.instrument_app(app)
                app.state._aurora_otel_installed=True
            HTTPXClientInstrumentor().instrument()  # instrumentation library guards repeats
        except ImportError:
            raise RuntimeError('AURORA_ENABLE_OTEL requires the optional telemetry dependencies')


def wrap_asgi(app):
    """Capture correlation in the MCP Starlette/ASGI upstream."""
    class CorrelatedApp:
        async def __call__(self,scope,receive,send):
            if scope.get('type')!='http':return await app(scope,receive,send)
            headers=dict(scope.get('headers',[]))
            raw=headers.get(b'x-correlation-id',b'').decode('ascii','ignore')
            cid=raw if re.fullmatch(r'[A-Za-z0-9_-]{8,64}',raw) else uuid.uuid4().hex
            token=_correlation.set(cid)
            try:return await app(scope,receive,send)
            finally:_correlation.reset(token)
    result=CorrelatedApp()
    if os.getenv('AURORA_ENABLE_OTEL','false').lower()=='true':
        from opentelemetry.instrumentation.asgi import OpenTelemetryMiddleware
        return OpenTelemetryMiddleware(result)
    return result
