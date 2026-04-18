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
        instance.applications.get = AsyncMock(return_value=_make_apps_list([_make_app()]))
        instance.applications.by_application_id.return_value.get = AsyncMock(return_value=app)

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
        instance.applications.get = AsyncMock(return_value=_make_apps_list([_make_app()]))
        instance.applications.by_application_id.return_value.add_password.post = AsyncMock(return_value=new_cred)

        client = GraphClient(credential=MagicMock())
        result = client.add_password_credential(APP_ID, display_name="test", validity_days=30)

    assert result.key_id == "new-key"
    assert result.secret_text == "s3cr3t"


# ---------------------------------------------------------------------------
# remove_password_credential
# ---------------------------------------------------------------------------

def test_remove_password_credential(monkeypatch):
    with patch("srf.graph.client.GraphServiceClient") as MockGraph:
        instance = MockGraph.return_value
        instance.applications.get = AsyncMock(return_value=_make_apps_list([_make_app()]))
        remove_mock = AsyncMock(return_value=None)
        instance.applications.by_application_id.return_value.remove_password.post = remove_mock

        client = GraphClient(credential=MagicMock())
        client.remove_password_credential(APP_ID, KEY_ID)

    remove_mock.assert_awaited_once()
