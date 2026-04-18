"""Tests for OwnershipChecker."""
from __future__ import annotations

from unittest.mock import MagicMock

from srf.config.models import SecretConfig
from srf.graph.client import GraphClient
from srf.ownership.checker import OwnershipChecker


KV_ID = "/subscriptions/s/resourceGroups/r/providers/Microsoft.KeyVault/vaults/kv"


def _cfg(required_owners=None):
    return SecretConfig(
        name="test-sp",
        app_id="app-id",
        keyvault_id=KV_ID,
        keyvault_secret_name="test-secret",
        required_owners=required_owners or [],
    )


def test_skip_when_no_required_owners():
    graph = MagicMock(spec=GraphClient)
    checker = OwnershipChecker(graph_client=graph)

    result = checker.check_and_update(_cfg())

    assert result.checked is False
    graph.add_owner.assert_not_called()


def test_all_owners_already_present():
    graph = MagicMock(spec=GraphClient)
    graph.list_owners.return_value = ["u1", "u2"]
    checker = OwnershipChecker(graph_client=graph)

    result = checker.check_and_update(_cfg(required_owners=["u1", "u2"]))

    assert result.checked is True
    assert result.owners_added == []
    assert result.owners_already_present == ["u1", "u2"]
    graph.add_owner.assert_not_called()


def test_adds_missing_owners():
    graph = MagicMock(spec=GraphClient)
    graph.list_owners.return_value = ["u1"]
    checker = OwnershipChecker(graph_client=graph)

    result = checker.check_and_update(_cfg(required_owners=["u1", "u2"]))

    assert result.checked is True
    assert result.owners_added == ["u2"]
    assert result.owners_already_present == ["u1"]
    graph.add_owner.assert_called_once_with("app-id", "u2")


def test_adds_all_when_none_present():
    graph = MagicMock(spec=GraphClient)
    graph.list_owners.return_value = []
    checker = OwnershipChecker(graph_client=graph)

    result = checker.check_and_update(_cfg(required_owners=["u1", "u2"]))

    assert result.checked is True
    assert set(result.owners_added) == {"u1", "u2"}
    assert result.owners_already_present == []
    assert graph.add_owner.call_count == 2


def test_error_returns_error_result():
    graph = MagicMock(spec=GraphClient)
    graph.list_owners.side_effect = RuntimeError("graph down")
    checker = OwnershipChecker(graph_client=graph)

    result = checker.check_and_update(_cfg(required_owners=["u1"]))

    assert result.checked is True
    assert result.error is not None
    assert "graph down" in result.error


def test_master_owners_applied_to_all():
    graph = MagicMock(spec=GraphClient)
    graph.list_owners.return_value = []
    checker = OwnershipChecker(graph_client=graph, master_owners=["master-u1"])

    result = checker.check_and_update(_cfg(required_owners=[]))

    assert result.checked is True
    assert result.owners_added == ["master-u1"]
    graph.add_owner.assert_called_once_with("app-id", "master-u1")


def test_master_and_sp_owners_merged():
    graph = MagicMock(spec=GraphClient)
    graph.list_owners.return_value = []
    checker = OwnershipChecker(graph_client=graph, master_owners=["m1"])

    result = checker.check_and_update(_cfg(required_owners=["sp1"]))

    assert result.checked is True
    assert result.owners_added == ["m1", "sp1"]
    assert graph.add_owner.call_count == 2
    graph.add_owner.assert_any_call("app-id", "m1")
    graph.add_owner.assert_any_call("app-id", "sp1")


def test_master_owners_deduplication():
    graph = MagicMock(spec=GraphClient)
    graph.list_owners.return_value = []
    checker = OwnershipChecker(graph_client=graph, master_owners=["u1"])

    result = checker.check_and_update(_cfg(required_owners=["u1", "u2"]))

    assert result.checked is True
    assert result.owners_added == ["u1", "u2"]
    assert graph.add_owner.call_count == 2


def test_skip_when_both_empty():
    graph = MagicMock(spec=GraphClient)
    checker = OwnershipChecker(graph_client=graph, master_owners=[])

    result = checker.check_and_update(_cfg(required_owners=[]))

    assert result.checked is False
    graph.add_owner.assert_not_called()
