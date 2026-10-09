"""Correlated business and agent events; optional OpenTelemetry when installed.

No model prompts, financial account identifiers or authorization secrets are
attached as telemetry attributes. For managed AMP use its documented OTel setup.
"""
from contextvars import ContextVar
import re,uuid,os
from fastapi import Request
_correlation=ContextVar('aurora_correlation',default=None)

def correlation_id():return _correlation.get() or uuid.uuid4().hex

def outbound_headers():return {'X-Correlation-ID':correlation_id()}

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
            provider=TracerProvider(resource=Resource.create({'service.name':f'aurora-{service}'}))
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(provider)
            FastAPIInstrumentor.instrument_app(app)
            HTTPXClientInstrumentor().instrument()
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
