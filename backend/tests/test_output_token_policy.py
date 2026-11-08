"""Provider-free response allowance, immutable request and packing contracts."""


import pytest
from test_pr_engine import CONNECTION, CREDENTIALS, OPTIONS, final, plan, response, run
from test_security_latency import snapshot

from code_mortis.core import analysis
from code_mortis.integrations import llm
from code_mortis.reviews import engine
from code_mortis.security_scans import engine as security_engine


@pytest.mark.parametrize(("snapshot", "policy", "limit"), [
    (None, analysis.OUTPUT_TOKEN_POLICY, 16384),
    ({"versions": {}}, None, 8192),
    ({"versions": {"output_tokens": analysis.OUTPUT_TOKEN_POLICY}}, analysis.OUTPUT_TOKEN_POLICY, 16384),
])
def test_policy_is_new_only_for_new_plans(snapshot, policy, limit):
    assert analysis.output_token_policy(snapshot) == policy
    assert analysis.output_token_limit(policy) == limit


def test_unknown_policy_fails_closed():
    with pytest.raises(ValueError, match="unsupported"):
        analysis.output_token_policy({"versions": {"output_tokens": "future-policy"}})


@pytest.mark.parametrize("policy", [None, analysis.OUTPUT_TOKEN_POLICY])
def test_pr_request_and_continuation_keep_the_pinned_allowance(monkeypatch, policy):
    from test_pr_engine import install_source_reads, tool_response

    install_source_reads(monkeypatch)
    value = plan()
    batches = engine.prepare_batches(value, CONNECTION, OPTIONS, CREDENTIALS, output_token_policy=policy)
    legacy = engine.prepare_batches(value, CONNECTION, OPTIONS, CREDENTIALS, output_token_policy=None)[0]
    expected = analysis.output_token_limit(policy)
    assert batches[0].request.model_copy(update={"max_output_tokens": 8192}) == legacy.request
    assert llm.input_fingerprint(batches[0].request.model_copy(update={"max_output_tokens": 8192})) == legacy.spec.input_hash
    result, ledger = run(monkeypatch, value, batches, [tool_response(), response(final(batches[0]))])
    assert result[0] == "completed" and len(ledger.calls) == 2
    assert all(request.max_output_tokens == expected for request in ledger.calls)
    # Settled replay does not issue or charge another request.
    replay, _ = run(monkeypatch, value, batches, [], ledger=ledger)
    assert replay == result and len(ledger.calls) == 2


@pytest.mark.parametrize("policy", [None, analysis.OUTPUT_TOKEN_POLICY])
@pytest.mark.parametrize("packing", [None, True, False])
def test_security_packing_reservation_uses_pinned_output_policy(tmp_path, monkeypatch, policy, packing):
    item = snapshot(tmp_path, {"route.py": "return value", "guard.py": "pass"})
    supplied, reservations = [], []
    monkeypatch.setattr(llm, "reservation", lambda connection, request: reservations.append(request) or 1000)

    def infer(context, **kwargs):
        supplied.append(context)
        return kwargs["validator"]({"findings": [], "required_paths": []}, context), {"tokens": 4}

    # At 10k total, legacy 8,192 fits with 1k input; corrected 16,384 does not.
    security_engine.ask(item, CONNECTION, {}, "analysis", {
        "assigned_path": "route.py", "task_contract": security_engine.TASK_CONTRACT, "diff": "",
        "sources": {"current": {"route.py": "return value"}},
    }, 0 if packing is not None else 10_000, 60, lambda: False, "task", infer=infer,
        source_packing=packing, output_token_policy=policy)
    expected_packing = policy is None if packing is None else packing
    assert ("guard.py" in supplied[0]["sources"]["current"]) == expected_packing
    assert all(request.max_output_tokens == analysis.output_token_limit(policy) for request in reservations)
    assert len(reservations) == int(packing is not False)
