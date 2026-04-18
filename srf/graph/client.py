from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from azure.core.credentials import TokenCredential
from msgraph import GraphServiceClient
from msgraph.generated.applications.item.add_password.add_password_post_request_body import (
    AddPasswordPostRequestBody,
)
from msgraph.generated.applications.item.remove_password.remove_password_post_request_body import (
    RemovePasswordPostRequestBody,
)
from msgraph.generated.models.password_credential import PasswordCredential
from msgraph.generated.models.reference_create import ReferenceCreate


_GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]


class GraphClient:
    """Thin wrapper around msgraph-sdk for SP password credential operations."""

    def __init__(self, credential: TokenCredential) -> None:
        self._graph = GraphServiceClient(credential, scopes=_GRAPH_SCOPES)
        self._object_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, coro):
        """Run an async Graph SDK coroutine synchronously in a thread-safe way.

        ``asyncio.run()`` is not safe to call concurrently from multiple threads
        because it modifies the running event loop at the process level.
        We create an isolated event loop per call and tear it down cleanly,
        including shutting down async generators and the default executor so
        that aiohttp connection pools are not leaked across threads.
        """
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    async def _get_object_id(self, app_id: str) -> str:
        """Resolve appId (client ID) to the application's object ID (cached)."""
        if app_id in self._object_id_cache:
            return self._object_id_cache[app_id]

        from msgraph.generated.applications.applications_request_builder import (
            ApplicationsRequestBuilder,
        )
        from kiota_abstractions.base_request_configuration import RequestConfiguration

        query_params = ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
            filter=f"appId eq '{app_id}'",
            select=["id", "appId"],
        )
        config = RequestConfiguration(query_parameters=query_params)
        result = await self._graph.applications.get(request_configuration=config)
        apps = result.value if result and result.value else []
        if not apps:
            raise ValueError(f"Application with appId '{app_id}' not found in the directory.")
        obj_id: str = apps[0].id  # type: ignore[assignment]
        self._object_id_cache[app_id] = obj_id
        return obj_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_password_credentials(self, app_id: str) -> list[PasswordCredential]:
        async def _call():
            obj_id = await self._get_object_id(app_id)
            app = await self._graph.applications.by_application_id(obj_id).get()
            return app.password_credentials or []  # type: ignore[union-attr]

        return self._run(_call())

    def add_password_credential(
        self, app_id: str, display_name: str, validity_days: int = 365
    ) -> PasswordCredential:
        async def _call():
            obj_id = await self._get_object_id(app_id)
            end_dt = datetime.now(tz=timezone.utc) + timedelta(days=validity_days)
            body = AddPasswordPostRequestBody()
            cred = PasswordCredential()
            cred.display_name = display_name
            cred.end_date_time = end_dt
            body.password_credential = cred
            return await self._graph.applications.by_application_id(obj_id).add_password.post(body)

        return self._run(_call())

    def remove_password_credential(self, app_id: str, key_id: str) -> None:
        async def _call():
            obj_id = await self._get_object_id(app_id)
            body = RemovePasswordPostRequestBody()
            body.key_id = key_id
            await self._graph.applications.by_application_id(obj_id).remove_password.post(body)

        self._run(_call())

    def list_owners(self, app_id: str) -> list[str]:
        async def _call():
            obj_id = await self._get_object_id(app_id)
            result = await self._graph.applications.by_application_id(obj_id).owners.get()
            entries = result.value if result and result.value else []
            return [
                e.id
                for e in entries
                if getattr(e, "odata_type", None) == "#microsoft.graph.user"
            ]

        return self._run(_call())

    def add_owner(self, app_id: str, user_object_id: str) -> None:
        async def _call():
            obj_id = await self._get_object_id(app_id)
            body = ReferenceCreate()
            body.odata_id = f"https://graph.microsoft.com/v1.0/directoryObjects/{user_object_id}"
            await self._graph.applications.by_application_id(obj_id).owners.ref.post(body)

        self._run(_call())
