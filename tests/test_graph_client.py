"""Tests for GraphClient — all Azure SDK calls are monkeypatched."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from srf.graph.client import GraphClient


APP_ID = "app-0001"
OBJ_ID = "obj-0001"
KEY_ID = "key-0001"


def _make_app(obj_id=OBJ_ID, app_id=APP_ID, creds=None):
    app = MagicMock()
    app.id = obj_id
    app.app_id = app_id
    app.password_credentials = creds or []
    return app


def _make_apps_list(apps):
    result = MagicMock()
    result.value = apps
    return result


def _make_cred(key_id=KEY_ID):
    cred = MagicMock()
    cred.key_id = key_id
    return cred


# ---------------------------------------------------------------------------
# list_password_credentials
# ---------------------------------------------------------------------------


def test_list_password_credentials(monkeypatch):
    cred1 = _make_cred("k1")
    cred2 = _make_cred("k2")
    app = _make_app(creds=[cred1, cred2])

    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(
            return_value=_make_apps_list([_make_app()])
        )
        instance.applications.by_application_id.return_value.get = AsyncMock(
            return_value=app
        )

        client = GraphClient(credential=MagicMock())
        result = client.list_password_credentials(APP_ID)

    assert len(result) == 2
    assert result[0].key_id == "k1"


def test_list_password_credentials_no_app_raises(monkeypatch):
    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(return_value=_make_apps_list([]))

        client = GraphClient(credential=MagicMock())
        with pytest.raises(ValueError, match="not found"):
            client.list_password_credentials(APP_ID)


# ---------------------------------------------------------------------------
# add_password_credential
# ---------------------------------------------------------------------------


def test_add_password_credential(monkeypatch):
    new_cred = _make_cred("new-key")
    new_cred.secret_text = "s3cr3t"

    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(
            return_value=_make_apps_list([_make_app()])
        )
        instance.applications.by_application_id.return_value.add_password.post = (
            AsyncMock(return_value=new_cred)
        )

        client = GraphClient(credential=MagicMock())
        result = client.add_password_credential(
            APP_ID, display_name="test", validity_days=30
        )

    assert result.key_id == "new-key"
    assert result.secret_text == "s3cr3t"


# ---------------------------------------------------------------------------
# remove_password_credential
# ---------------------------------------------------------------------------


def test_remove_password_credential(monkeypatch):
    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(
            return_value=_make_apps_list([_make_app()])
        )
        remove_mock = AsyncMock(return_value=None)
        instance.applications.by_application_id.return_value.remove_password.post = (
            remove_mock
        )

        client = GraphClient(credential=MagicMock())
        client.remove_password_credential(APP_ID, KEY_ID)

    remove_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# test_object_id_used_directly
# ---------------------------------------------------------------------------


def test_object_id_used_directly(monkeypatch):
    # Build a fake app where .id is the correct object ID
    correct_app = _make_app(obj_id=OBJ_ID, app_id=APP_ID)

    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value

        # First call  → id filter    → returns a match (it IS the object ID)
        # Second call never happens — resolution returns early
        instance.applications.get = AsyncMock(
            side_effect=[
                _make_apps_list([correct_app]),  # 1st call: id eq → match
            ]
        )
        instance.applications.by_application_id.return_value.get = AsyncMock(
            return_value=correct_app
        )

        client = GraphClient(credential=MagicMock())
        result = client.list_password_credentials(OBJ_ID)

    assert result == []


# ---------------------------------------------------------------------------
# test_object_id_not_found
# ---------------------------------------------------------------------------


def test_object_id_not_found(monkeypatch):

    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value

        # Both lookups return empty
        instance.applications.get = AsyncMock(
            side_effect=[
                _make_apps_list([]),  # 1st call: id eq    → no match
                _make_apps_list([]),  # 2nd call: appId eq → no match
            ]
        )

        client = GraphClient(credential=MagicMock())
        with pytest.raises(ValueError, match="not found"):
            client.list_password_credentials("unknow-guid")


# ---------------------------------------------------------------------------
# list_owners
# ---------------------------------------------------------------------------


def _make_dir_obj(obj_id, odata_type):
    obj = MagicMock()
    obj.id = obj_id
    obj.odata_type = odata_type
    return obj


def _make_owners_result(entries):
    result = MagicMock()
    result.value = entries
    return result


def test_list_owners_returns_user_ids():
    user1 = _make_dir_obj("user-001", "#microsoft.graph.user")
    user2 = _make_dir_obj("user-002", "#microsoft.graph.user")
    sp = _make_dir_obj("sp-001", "#microsoft.graph.servicePrincipal")

    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(
            return_value=_make_apps_list([_make_app()])
        )
        instance.applications.by_application_id.return_value.owners.get = AsyncMock(
            return_value=_make_owners_result([user1, user2, sp])
        )

        client = GraphClient(credential=MagicMock())
        result = client.list_owners(APP_ID)

    assert result == ["user-001", "user-002"]


def test_list_owners_empty():
    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(
            return_value=_make_apps_list([_make_app()])
        )
        instance.applications.by_application_id.return_value.owners.get = AsyncMock(
            return_value=_make_owners_result([])
        )

        client = GraphClient(credential=MagicMock())
        result = client.list_owners(APP_ID)

    assert result == []


# ---------------------------------------------------------------------------
# add_owner
# ---------------------------------------------------------------------------


def test_add_owner_calls_ref_post():
    from msgraph.generated.models.reference_create import ReferenceCreate

    user_oid = "user-999"

    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(
            return_value=_make_apps_list([_make_app()])
        )
        ref_post_mock = AsyncMock(return_value=None)
        instance.applications.by_application_id.return_value.owners.ref.post = (
            ref_post_mock
        )

        client = GraphClient(credential=MagicMock())
        client.add_owner(APP_ID, user_oid)

    ref_post_mock.assert_awaited_once()
    body = ref_post_mock.call_args[0][0]
    assert isinstance(body, ReferenceCreate)
    assert (
        body.odata_id == f"https://graph.microsoft.com/v1.0/directoryObjects/{user_oid}"
    )


# ---------------------------------------------------------------------------
# objectId cache
# ---------------------------------------------------------------------------


def test_object_id_cached_after_first_call():
    """Second call for the same sp_id must not hit the Graph API again."""
    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        app = _make_app()
        instance.applications.get = AsyncMock(return_value=_make_apps_list([app]))
        instance.applications.by_application_id.return_value.get = AsyncMock(
            return_value=app
        )

        client = GraphClient(credential=MagicMock())
        client.list_password_credentials(APP_ID)
        client.list_password_credentials(APP_ID)

    # _get_object_id resolves via applications.get — it should only be called once
    assert instance.applications.get.call_count == 1
