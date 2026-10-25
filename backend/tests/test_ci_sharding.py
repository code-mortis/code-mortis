from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import pytest_collection_modifyitems


def test_ci_shards_cover_every_case_once_with_consistent_order(monkeypatch):
    original = [SimpleNamespace(nodeid=f"test.py::test_case[{i}]") for i in range(17)]
    assigned, counts = [], []
    monkeypatch.setenv("PYTEST_SHARD_COUNT", "4")
    for index in range(4):
        monkeypatch.setenv("PYTEST_SHARD_INDEX", str(index))
        config = SimpleNamespace(hook=SimpleNamespace(pytest_deselected=Mock()))
        items = list(reversed(original)) if index % 2 else original[:]
        pytest_collection_modifyitems(config, items)
        deselected = config.hook.pytest_deselected.call_args.kwargs["items"]
        assert {item.nodeid for item in items}.isdisjoint(item.nodeid for item in deselected)
        assert len(items) + len(deselected) == len(original)
        assert items == sorted(original, key=lambda item: item.nodeid)[index::4]
        counts.append(len(items))
        assigned.extend(item.nodeid for item in items)
    assert sorted(assigned) == sorted(item.nodeid for item in original)
    assert counts == [5, 4, 4, 4]


def test_local_pytest_keeps_every_case_in_order(monkeypatch):
    monkeypatch.delenv("PYTEST_SHARD_INDEX", raising=False)
    monkeypatch.delenv("PYTEST_SHARD_COUNT", raising=False)
    items = [SimpleNamespace(nodeid="b"), SimpleNamespace(nodeid="a")]
    original = items[:]
    pytest_collection_modifyitems(None, items)
    assert items == original


@pytest.mark.parametrize(("index", "count"), [
    ("-1", "4"), ("4", "4"), ("0", "0"), ("0", "-1"), ("bad", "4"),
    ("0", "bad"), ("0", None), (None, "4"),
])
def test_invalid_ci_shard_fails_instead_of_silently_skipping_tests(monkeypatch, index, count):
    for name, value in [("PYTEST_SHARD_INDEX", index), ("PYTEST_SHARD_COUNT", count)]:
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    with pytest.raises(pytest.UsageError):
        pytest_collection_modifyitems(None, [])
