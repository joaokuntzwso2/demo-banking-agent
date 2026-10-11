"""DTO-driven regression fixtures. No synthetic assumption that lists contain envModelConfig."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from aurora_llm_contract import ContractError, classify_binding, parse_collection, parse_provider_ids, parse_provider_registry

UUID = "11111111-2222-3333-4444-555555555555"
PROXY_UUID = "aaaa1111-bbbb-2222-cccc-333333333333"


def collection(items=None):
    return {"configs": [{"uuid": UUID, "name": "openai-2", "type": "llm", "agentId": "agent-id"}] if items is None else items,
            "pagination": {"count": 1, "offset": 0, "limit": 50}}


def detail(provider="aurora-openai"):
    return {"uuid": UUID, "name": "openai-2", "type": "llm", "agentId": "agent-id",
            "envModelConfig": {"default": {"environmentName": "default", "llmProxy": {
                "proxyUuid": PROXY_UUID, "providerUuid": "provider-uuid-123", "providerName": provider, "policies": []}}},
            "environmentVariables": [{"name": "OPENAI_URL", "key": "url"},
                                     {"name": "OPENAI_API_KEY", "key": "apikey"}]}


class ObservedRuntimeTests(unittest.TestCase):
    """Grounded fixture captured from the user's installed amctl 1.0.0 runtime."""
    def test_live_account_card_are_verified(self):
        import json
        fixture=json.loads((Path(__file__).resolve().parents[0]/"fixtures/amp_100_llm_live.json").read_text())
        provider_uuid=parse_provider_registry(fixture["provider_list"])["aurora-openai"]
        for name in ("aurora-account", "aurora-card"):
            data=fixture[name]
            detail=data["detail"]
            self.assertNotIn("envModelConfig", detail)
            finding=classify_binding(data["list"], {detail["uuid"]: detail},
                                     expected_provider_uuid=provider_uuid)
            self.assertTrue(finding.healthy, finding.explanation)

    def test_live_wrong_provider_uuid_detected(self):
        import json
        from copy import deepcopy
        fixture=json.loads((Path(__file__).resolve().parents[0]/"fixtures/amp_100_llm_live.json").read_text())
        obj=deepcopy(fixture["aurora-card"])
        obj['detail']['envMappings']['default']['configuration']['providerUuid']='wrong'
        result=classify_binding(obj['list'],{obj['detail']['uuid']:obj['detail']},
                                expected_provider_uuid=fixture['provider_list']['data']['providers'][0]['uuid'])
        self.assertEqual(result.code,'conflict')

    def test_live_inactive_mapping_does_not_pass(self):
        import json
        from copy import deepcopy
        fixture=json.loads((Path(__file__).resolve().parents[0]/"fixtures/amp_100_llm_live.json").read_text())
        obj=deepcopy(fixture["aurora-card"])
        obj['detail']['envMappings']['default']['configuration']['status']='inactive'
        result=classify_binding(obj['list'],{obj['detail']['uuid']:obj['detail']})
        self.assertEqual(result.code,'incomplete')

class ContractTests(unittest.TestCase):
    def test_list_summary_does_not_contain_env_model_config(self):
        self.assertNotIn("envModelConfig", parse_collection(collection())[0])
        with self.assertRaisesRegex(ContractError, "individual GET"):
            classify_binding(collection(), {})

    def test_correct_binding_uses_provider_reference_not_generated_name(self):
        result = classify_binding(collection(), {UUID: detail()})
        self.assertTrue(result.healthy)
        self.assertEqual(result.binding_name, "openai-2")
        self.assertEqual(result.provider_handle, "aurora-openai")

    def test_absent_binding(self):
        self.assertEqual(classify_binding({"configs": [], "pagination": {"count": 0, "offset": 0, "limit": 50}}, {}).code, "absent")

    def test_other_types_not_llm(self):
        data = collection([{"uuid": UUID, "name": "mcp", "type": "mcp"}])
        self.assertEqual(classify_binding(data, {}).code, "absent")

    def test_missing_environment_never_silently_replaced(self):
        obj = detail()
        obj["envModelConfig"] = {"staging": {"environmentName": "staging", "llmProxy": {"providerName": "aurora-openai"}}}
        finding = classify_binding(collection(), {UUID: obj})
        self.assertEqual(finding.code, "incomplete")
        self.assertIn("staging", finding.explanation)

    def test_wrong_provider_ref_is_conflict(self):
        self.assertEqual(classify_binding(collection(), {UUID: detail("prod-openai")}).code, "conflict")

    def test_mismatched_provider_uuid_is_conflict(self):
        result = classify_binding(collection(), {UUID: detail()}, expected_provider_uuid="DIFFERENT-UUID")
        self.assertEqual(result.code, "conflict")

    def test_provider_uuid_matches_org_provider_record(self):
        result = classify_binding(collection(), {UUID: detail()}, expected_provider_uuid="provider-uuid-123")
        self.assertTrue(result.healthy)

    def test_missing_proxy_uuid_does_not_pass(self):
        obj = detail()
        obj["envModelConfig"]["default"]["llmProxy"].pop("proxyUuid")
        self.assertEqual(classify_binding(collection(), {UUID: obj}).code, "incomplete")

    def test_missing_proxy_data_does_not_pass(self):
        obj = detail()
        obj["envModelConfig"]["default"].pop("llmProxy")
        self.assertEqual(classify_binding(collection(), {UUID: obj}).code, "incomplete")

    def test_missing_envvar_mapping_is_blocked(self):
        obj = detail()
        obj["environmentVariables"] = [{"name": "OPENAI_URL", "key": "url"}]
        self.assertEqual(classify_binding(collection(), {UUID: obj}).code, "incomplete")

    def test_duplicate_envvar_mapping_is_blocked(self):
        obj = detail()
        obj["environmentVariables"].append({"name": "OPENAI_URL", "key": "apikey"})
        self.assertEqual(classify_binding(collection(), {UUID: obj}).code, "ambiguous")

    def test_missing_env_model_config_is_incomplete_not_absent(self):
        obj = detail()
        del obj["envModelConfig"]
        self.assertEqual(classify_binding(collection(), {UUID: obj}).code, "incomplete")

    def test_multiple_bindings_refuses_automatic_selection(self):
        data = collection([{"uuid": UUID, "name": "openai", "type": "llm"},
                           {"uuid": "some-other-uuid", "name": "openai-2", "type": "llm"}])
        data["pagination"]["count"] = 2
        self.assertEqual(classify_binding(data, {}).code, "ambiguous")

    def test_env_name_mismatch_fails_closed(self):
        obj = detail()
        obj["envModelConfig"]["default"]["environmentName"] = "prod"
        with self.assertRaisesRegex(ContractError, "disagrees"):
            classify_binding(collection(), {UUID: obj})

    def test_unexpected_http_shape_fails_closed(self):
        with self.assertRaisesRegex(ContractError, "configs"):
            parse_collection({"data": {"configs": []}})

    def test_collection_pagination_not_ignored(self):
        data = collection()
        data["pagination"]["count"] = 7
        with self.assertRaisesRegex(ContractError, "paginated"):
            parse_collection(data)

    def test_actual_amctl_100_provider_envelope_without_status(self):
        # Captured from `amctl version 1.0.0 (commit 21b1adb2e)`;
        # provider credential is not part of this response.
        raw = {
            "instance": "default", "org": "default", "data": {
                "limit": 50, "offset": 0, "providers": [{
                    "createdAt": "2026-10-10T16:55:31.343384Z", "createdBy": "system",
                    "id": "aurora-openai", "name": "Banco Aurora OpenAI",
                    "status": "CREATED", "template": "openai",
                    "updatedAt": "2026-10-10T16:55:31.343384Z",
                    "uuid": "6aaa1fb6-58dd-402d-82bd-ff4b828e1a24",
                }], "total": 1,
            },
        }
        self.assertEqual(parse_provider_registry(raw)["aurora-openai"],
                         "6aaa1fb6-58dd-402d-82bd-ff4b828e1a24")

    def test_actual_account_card_collection_summaries(self):
        # Captured from amctl api GET, not fabricated implementation output.
        samples = [
            ("aurora-account", "43752396-6f76-4f3b-89f0-550b9b1fac6c", "aurora-openai"),
            ("aurora-card", "d8e10bfe-f9c9-4e48-9e24-d6602c9f623e", "aurora-card-llm-config"),
        ]
        for agent, uuid, name in samples:
            raw = {"configs": [{"agentId": agent, "createdAt": "2026-10-10T16:55:31Z",
                                "name": name, "projectName": "banco-aurora",
                                "type": "llm", "uuid": uuid}],
                   "pagination": {"count": 1, "limit": 20, "offset": 0}}
            result = parse_collection(raw)
            self.assertEqual(result[0]["uuid"], uuid)
            self.assertNotIn("envModelConfig", result[0])
            with self.assertRaisesRegex(ContractError, "individual GET"):
                classify_binding(raw, {})

    def test_explicit_error_envelope_is_rejected(self):
        with self.assertRaisesRegex(ContractError, "non-ok status"):
            parse_provider_registry({"status": "error", "data": {"providers": []}})

    def test_provider_handle_from_amctl_envelope(self):
        handles = parse_provider_ids({"status": "ok", "scope": {"org": "default"},
                                      "data": {"providers": [{"id": "aurora-openai", "name": "OpenAI Production", "uuid": "uuid"}]}})
        self.assertEqual(handles, {"aurora-openai"})

    def test_provider_registry_tracks_uuid_if_exposed(self):
        registry = parse_provider_registry({"status": "ok", "data": {"providers": [{"id": "aurora-openai", "uuid": "provider-uuid-123"}]}})
        self.assertEqual(registry["aurora-openai"], "provider-uuid-123")

    def test_provider_name_is_not_an_id(self):
        handles = parse_provider_ids({"status": "ok", "data": {"providers": [{"id": "prod-openai", "name": "aurora-openai"}]}})
        self.assertNotIn("aurora-openai", handles)


if __name__ == "__main__":
    unittest.main()
