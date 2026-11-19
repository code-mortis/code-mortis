"""Offline compatibility checks for the external Engine adapters."""

import asyncio
import json
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx
from test_llm_http import PROTOCOLS, request, result
from test_pr_engine import CONNECTION, CREDENTIALS, OPTIONS, final, plan, unit

from code_mortis.core import analysis
from code_mortis.core.errors import ReviewStopped
from code_mortis.core.security import digest
from code_mortis.integrations import llm
from code_mortis.reviews import engine, planning
from code_mortis.security_scans import engine as security_engine


class ExternalEngineAdapterTests(TestCase):
    def test_saved_requests_keep_their_original_identity(self):
        cases = [
            (None, None, "f477eb6dbfbc8ebb62c8cd9d84ee264f89d6580da4e36b069e877f8d315f857e"),
            (None, "bounded-reads/v2", "0370b632fb7a5e603579fc52ada5619d08b0c8a4dfb4bdfef3be2cb7a9d6d9e6"),
            ("mortis-completion/v1", None, "421ecae9c3ec23fb4bec59cac8b8a39fa3a0f514d7636ed9ba3bc8ebc91176e0"),
            ("mortis-completion/v1", "bounded-reads/v2", "e9b51636c288d2e64d8a3b20436f8e4c5f79f41e34979db0b133512778c10728"),
        ]
        for output_policy, tool_policy, fingerprint in cases:
            with self.subTest(output_policy=output_policy, tool_policy=tool_policy):
                batch = engine.prepare_batches(plan(), CONNECTION, OPTIONS, CREDENTIALS,
                    output_token_policy=output_policy, source_tool_policy=tool_policy)[0]
                self.assertEqual(llm.input_fingerprint(batch.request), fingerprint)
                recovered = engine._recovery_batches(batch, batch.request, batch.sources, batch.key)[0]
                self.assertEqual(recovered.request.max_output_tokens, 16384 if output_policy is None else 32768)

    def test_new_reviews_use_external_preparation_validation_and_unlimited_output(self):
        with patch.object(engine.native, "prepare_batches", wraps=engine.native.prepare_batches) as prepare:
            batch = engine.prepare_batches(plan(), CONNECTION, OPTIONS, CREDENTIALS,
                output_token_policy=analysis.WORKFLOW_OUTPUT_POLICY)[0]
        prepare.assert_called_once()
        self.assertEqual(batch.request.max_output_tokens, 0)
        with patch.object(engine.native, "validate_result", wraps=engine.native.validate_result) as validate:
            result = engine.validate_result(final(batch), batch, batch.sources, CREDENTIALS)
        validate.assert_called_once()
        self.assertEqual(result["completed_units"], [unit.key for unit in batch.units])
        recovered = engine._recovery_batches(batch, batch.request, batch.sources, batch.key)[0]
        self.assertEqual(recovered.request.max_output_tokens, 0)

    def test_saved_responses_cache_options_do_not_remove_review_coverage(self):
        connection = {**CONNECTION, "protocol": "openai-responses"}
        expected = engine.prepare_batches(plan(), connection, OPTIONS, dict(CREDENTIALS))[0]
        for key in ("prompt_cache_key", "prompt_cache_key.value"):
            with self.subTest(key=key):
                saved = {**connection, "options": {"extra_body": {key: "saved-affinity"}}}
                batches = engine.prepare_batches(plan(), saved, OPTIONS, dict(CREDENTIALS))
                self.assertEqual([(batch.spec, batch.error) for batch in batches], [(expected.spec, None)])
                self.assertEqual(batches[0].connection["options"]["extra_body"], {key: "saved-affinity"})
                self.assertEqual(saved["options"]["extra_body"], {key: "saved-affinity"})

    def test_saved_cache_key_preserves_replay_at_legacy_prompt_ceiling(self):
        value = plan([unit(), unit("other.py", key="u2")])
        policies = (analysis.OUTPUT_TOKEN_POLICY, analysis.WORKFLOW_OUTPUT_POLICY)
        for protocol, policy, cache_key in product(PROTOCOLS, policies, ("custom", None, "审" * 25)):
            with self.subTest(protocol=protocol, policy=policy, cache_key=cache_key):
                options = {"extra_body": {"prompt_cache_key": cache_key}}
                original = {**CONNECTION, "protocol": protocol}
                saved = {**original, "options": options}
                frozen = engine.prepare_batches(value, original, OPTIONS, dict(CREDENTIALS),
                    output_token_policy=policy)[0]
                body = llm.engine_http.encode_request(protocol, original, frozen.request)
                body["prompt_cache_key"] = cache_key
                wire_bytes = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode())
                ceiling = wire_bytes + 1024 + 64 * (len(frozen.request.messages) + len(frozen.request.tools))
                options["prompt_token_ceiling"] = ceiling
                replay = engine.prepare_batches(value, saved, OPTIONS, dict(CREDENTIALS), output_token_policy=policy)
                self.assertEqual([(batch.spec, batch.error) for batch in replay], [(frozen.spec, None)])
                self.assertEqual(replay[0].connection["options"], {"extra_body": {"prompt_cache_key": cache_key},
                                                                  "prompt_token_ceiling": ceiling})
                options["prompt_token_ceiling"] = ceiling - 1
                limited = engine.prepare_batches(value, saved, OPTIONS, dict(CREDENTIALS), output_token_policy=policy)
                self.assertEqual([len(batch.units) for batch in limited], [1, 1])

    def test_responses_cache_key_uses_session_affinity_for_engine_workflows(self):
        protocol = "openai-responses"
        task = request(session_id="saved-session", max_output_tokens=32)
        options = {"extra_body": {"prompt_cache_key": "saved-affinity", "metadata": {"source": "fixture"}}}
        connection = {**CONNECTION, "protocol": protocol, "base_url": "https://provider.example/v1",
                      "options": options}
        sent = []

        def handle(message):
            sent.append(json.loads(message.content))
            return httpx.Response(200, json=result(protocol))

        async def send(snapshot):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                return await llm.send(snapshot, CREDENTIALS, task, client=client)

        workflow = {**connection, "execution_limits": analysis.EXECUTION_LIMITS}
        config = SimpleNamespace(allow_private_endpoints=False, trusted_endpoint_hosts="")
        with (patch.object(llm, "settings", return_value=config),
              patch.object(llm, "safe_endpoint", return_value=connection["base_url"]),
              patch.object(llm.engine_http, "safe_endpoint", return_value=connection["base_url"]),
              patch.object(llm.llm_http, "safe_endpoint", return_value=connection["base_url"])):
            self.assertGreater(llm.reservation(workflow, task), 0)
            asyncio.run(send(workflow))
            asyncio.run(send(connection))
        self.assertEqual([body["prompt_cache_key"] for body in sent], [digest(task.session_id), "saved-affinity"])
        self.assertEqual([body["metadata"] for body in sent], [{"source": "fixture"}] * 2)
        self.assertEqual(options["extra_body"]["prompt_cache_key"], "saved-affinity")

    def test_external_split_retains_local_manifest_shape_and_cancellation_signal(self):
        with patch.object(planning.native, "split_units", wraps=planning.native.split_units) as split:
            units = planning.split_units("app.py", None, "M", "b" * 40, "value = 1\n",
                                         "@@ -1 +1 @@\n-value = 0\n+value = 1\n", "Check", 4096)
        split.assert_called_once()
        self.assertNotIn("source_only", vars(units[0]))
        with self.assertRaises(ReviewStopped):
            engine.prepare_batches(plan(), CONNECTION, OPTIONS, CREDENTIALS, stopped=lambda: True)

    def test_new_scanner_uses_one_remaining_workflow_deadline(self):
        for timeout in (None, 900):
            with self.subTest(timeout=timeout), TemporaryDirectory() as directory:
                root = Path(directory)
                config = SimpleNamespace(encryption_key="test-key", trivy_cache_dir=root / "cache",
                                         trivy_binary="trivy", trivy_version="0.76.0")
                clock, durations, cli_durations = [0], [], []

                def command(args, directory, env, *, timeout, stopped, durations=durations, clock=clock,
                            cli_durations=cli_durations):
                    durations.append(timeout)
                    clock[0] += 1
                    if "--version" in args:
                        return 0, "Version: 0.76.0", ""
                    cli_durations.append(args[args.index("--timeout") + 1])
                    return 0, json.dumps({"SchemaVersion": 2, "Results": []}), ""

                with patch.object(security_engine, "settings", return_value=config), \
                        patch.object(security_engine.time, "monotonic", side_effect=lambda clock=clock: clock[0]), \
                        patch.object(security_engine.process, "command", side_effect=command):
                    item = security_engine.Snapshot(root, "a" * 40, {}, lambda: False)
                    security_engine.trivy(item, timeout, lambda: False, execution_limits=True)
                self.assertEqual(durations, [None] * 4 if timeout is None else [900, 899, 898, 897])
                self.assertEqual(cli_durations, ["9223372036854775807ns"] * 3 if timeout is None
                                 else ["899000000000ns", "898000000000ns", "897000000000ns"])
