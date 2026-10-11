"""Strict, read-only AMP 1.0.0 LLM model-binding classifier.

Derived from AMP's Go DTOs, not from amctl's tabular output:
  AgentModelConfigListResponse: {configs: [{uuid, name, type, ...}], pagination}
  AgentModelConfigResponse: {uuid, name, type, envMappings OR envModelConfig, environmentVariables, ...}
  Observed installed runtime: envMappings[env].configuration.{providerName, providerUuid, proxyUuid, status}
  Legacy DTO variant: envModelConfig[env].llmProxy.{providerName, proxyUuid, policies}
  EnvModelConfigRequest: {providerName, configuration}

The 'llmProxy.proxyUuid' is NOT the organization LLM provider UUID. A model
binding's generated name ('openai', 'openai-2') is NOT the provider handle.

This module does not mutate a server or silently reinterpret unknown shapes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ContractError(ValueError):
    """An AMP response is incomplete, ambiguous, or unexpectedly shaped."""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label}: expected JSON object, got {type(value).__name__}")
    return value


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{label}: expected non-empty string")
    return value


def parse_collection(raw: Any) -> list[dict]:
    """Parse only the HTTP GET collection response, not an amctl --json envelope.

    Individual bindings MUST subsequently be fetched by UUID. Summaries do
    not carry environment mappings, so never classify them as missing environments.
    """
    obj = _object(raw, "model-config collection")
    items = obj.get("configs")
    if not isinstance(items, list):
        raise ContractError("model-config collection: missing configs[]; inspect unmodified API response")
    seen: set[str] = set()
    parsed = []
    for idx, item in enumerate(items):
        item = _object(item, f"configs[{idx}]")
        uuid = _nonempty(item.get("uuid"), f"configs[{idx}].uuid")
        _nonempty(item.get("type"), f"configs[{idx}].type")
        if uuid in seen:
            raise ContractError(f"model-config collection: duplicate uuid {uuid}")
        seen.add(uuid)
        parsed.append(dict(item))
    # Do not assume a full result if server has paginated it.
    pagination = obj.get("pagination")
    if isinstance(pagination, dict):
        # AMP 1.0.0 PaginationInfo: {count, offset, limit}; never guess
        # an unreturned page is empty. Treat nonzero offsets as partial.
        offset, count = pagination.get("offset"), pagination.get("count")
        if isinstance(offset, int) and offset != 0:
            raise ContractError(f"model-config collection starts at offset {offset}; fetch complete collection")
        if isinstance(count, int) and count > len(items):
            raise ContractError(f"model-config collection is paginated ({len(items)}/{count}); fetch remaining pages")
    return parsed


def parse_provider_registry(raw: Any) -> dict[str, str | None]:
    """Parse 'amctl llm-provider list --json', documented .data.providers[].id.

    Returns handle -> optional provider UUID. Handle is the stable reference;
    UUID enables extra cross-checks when returned in both org/provider and
    agent/model-config responses.
    """
    root = _object(raw, "amctl provider output")
    # amctl 1.0.0 has both raw-like success envelopes without `status`
    # ({"instance":..., "org":..., "data":...}) and documented envelopes
    # containing "status": "ok".  Fail on an explicit error but accept
    # its absence.  Observed on amctl 1.0.0 commit 21b1adb2e.
    if "status" in root and root["status"] != "ok":
        raise ContractError("amctl provider list: explicit non-ok status")
    data = _object(root.get("data"), "amctl provider output.data")
    providers = data.get("providers")
    if not isinstance(providers, list):
        raise ContractError("amctl provider list: expected data.providers[]")
    registry: dict[str, str | None] = {}
    for i, provider in enumerate(providers):
        obj = _object(provider, f"provider[{i}]")
        handle = _nonempty(obj.get("id"), f"provider[{i}].id")
        if handle in registry:
            raise ContractError(f"duplicate organization provider handle {handle!r}")
        provider_uuid = obj.get("uuid")
        if provider_uuid is not None:
            _nonempty(provider_uuid, f"provider[{i}].uuid")
        registry[handle] = provider_uuid
    return registry


def parse_provider_ids(raw: Any) -> set[str]:
    return set(parse_provider_registry(raw))


@dataclass(frozen=True)
class BindingFinding:
    code: str  # verified | absent | conflict | incomplete | ambiguous
    explanation: str
    uuid: str | None = None
    binding_name: str | None = None
    provider_handle: str | None = None
    environment: str = "default"

    @property
    def healthy(self) -> bool:
        return self.code == "verified"


def classify_binding(
    collection: Any,
    details_by_uuid: Mapping[str, Any],
    expected_provider: str = "aurora-openai",
    environment: str = "default",
    expected_provider_uuid: str | None = None,
) -> BindingFinding:
    """Classify existing bindings; NEVER apply changes from a summary object.

    A correct, unambiguous binding is verified. All potentially destructive or
    insufficiently understood cases fail closed with a diagnostic.
    """
    _nonempty(expected_provider, "expected_provider")
    _nonempty(environment, "environment")
    summaries = [s for s in parse_collection(collection) if s["type"] == "llm"]
    if not summaries:
        return BindingFinding("absent", "No LLM model-config exists; creation requires validated creation path", environment=environment)
    if len(summaries) != 1:
        uuids = ", ".join(s["uuid"] for s in summaries)
        return BindingFinding("ambiguous", f"{len(summaries)} LLM bindings exist ({uuids}); do not silently choose or delete", environment=environment)
    summary = summaries[0]
    uuid = summary["uuid"]
    if uuid not in details_by_uuid:
        raise ContractError(f"Missing individual GET detail for model-config UUID {uuid}; summary is insufficient")
    detail = _object(details_by_uuid[uuid], f"model-config/{uuid}")
    if detail.get("uuid") != uuid or detail.get("type") != "llm":
        raise ContractError(f"model-config/{uuid}: UUID/type differs from collection summary")
    name = detail.get("name")
    if name is not None and not isinstance(name, str):
        raise ContractError(f"model-config/{uuid}: invalid binding name")
    modern = detail.get("envMappings")
    legacy = detail.get("envModelConfig")
    if modern is not None and not isinstance(modern, dict):
        raise ContractError(f"model-config/{uuid}: envMappings must be an object")
    if legacy is not None and not isinstance(legacy, dict):
        raise ContractError(f"model-config/{uuid}: envModelConfig must be an object")
    if modern and legacy:
        raise ContractError(f"model-config/{uuid}: competing envMappings and envModelConfig responses")
    field = "envMappings" if modern is not None else "envModelConfig"
    envs = modern if modern is not None else legacy
    if not isinstance(envs, dict):
        return BindingFinding("incomplete", "Individual detail contains no environment mappings in either supported response format", uuid, name, environment=environment)
    if environment not in envs:
        others = ", ".join(sorted(envs)) or "none"
        return BindingFinding("incomplete", f"No mapping for environment {environment!r}; found: {others}. Do not replace other environment mappings", uuid, name, environment=environment)
    mapping = _object(envs[environment], f"{field}[{environment!r}]")
    reported = mapping.get("environmentName")
    if reported is not None and reported != environment:
        raise ContractError(f"model-config/{uuid}: environment key {environment!r} disagrees with environmentName {reported!r}")
    entry_field = "configuration" if field == "envMappings" else "llmProxy"
    proxy = mapping.get(entry_field)
    if not isinstance(proxy, dict):
        return BindingFinding("incomplete", f"Environment {environment!r} lacks {entry_field} detail", uuid, name, environment=environment)
    provider = proxy.get("providerName")
    if not isinstance(provider, str) or not provider.strip():
        return BindingFinding("incomplete", f"Environment {environment!r} lacks {entry_field}.providerName", uuid, name, environment=environment)
    if provider != expected_provider:
        return BindingFinding("conflict", f"Existing environment {environment!r} references provider {provider!r}, expected {expected_provider!r}. No automatic replacement", uuid, name, provider, environment)
    actual_provider_uuid = proxy.get("providerUuid")
    if expected_provider_uuid and actual_provider_uuid is not None and actual_provider_uuid != expected_provider_uuid:
        return BindingFinding("conflict", f"Provider UUID {actual_provider_uuid!r} differs from organization's {expected_provider_uuid!r}; no automatic replacement", uuid, name, provider, environment)
    if not isinstance(proxy.get("proxyUuid"), str) or not proxy["proxyUuid"].strip():
        return BindingFinding("incomplete", f"Provider {provider!r} is referenced, but no proxyUuid was returned; verify gateway association", uuid, name, provider, environment)
    status = proxy.get("status")
    if status is not None and str(status).lower() != "active":
        return BindingFinding("incomplete", f"Provider {provider!r} mapping is {status!r}, not active", uuid, name, provider, environment)
    envvars = detail.get("environmentVariables")
    if envvars is not None:
        if not isinstance(envvars, list):
            raise ContractError(f"model-config/{uuid}: environmentVariables must be an array")
        by_key: dict[str, str] = {}
        seen_names: set[str] = set()
        for i, entry in enumerate(envvars):
            entry = _object(entry, f"environmentVariables[{i}]")
            key = _nonempty(entry.get("key"), f"environmentVariables[{i}].key")
            variable_name = _nonempty(entry.get("name"), f"environmentVariables[{i}].name")
            if key in by_key or variable_name in seen_names:
                return BindingFinding("ambiguous", "Duplicate environment variable key or name", uuid, name, provider, environment)
            by_key[key] = variable_name
            seen_names.add(variable_name)
        if not {"url", "apikey"}.issubset(by_key):
            return BindingFinding("incomplete", "Missing injected url/apikey variable mapping; do not rotate credentials", uuid, name, provider, environment)
    return BindingFinding("verified", f"Environment {environment!r} references organization provider {provider!r} through LLM proxy; binding name irrelevant", uuid, name, provider, environment)
