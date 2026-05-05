from __future__ import annotations

import asyncio
import logging
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
logger = logging.getLogger(__name__)


class GraphClient:
    """Thin wrapper around msgraph-sdk for SP password credential operations."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential
        self._object_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, coro_factory):
        """Run an async Graph SDK coroutine synchronously in a thread-safe way.

        A fresh ``GraphServiceClient`` (and its underlying httpx transport) is
        created for every call so that the async HTTP client is always local to
        the event loop it runs in.  This makes concurrent calls from different
        threads safe without requiring any locking.
        """
        graph = GraphServiceClient(self._credential, scopes=_GRAPH_SCOPES)
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro_factory(graph))
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
            finally:
                loop.close()

    async def _get_object_id(self, sp_id: str, graph: GraphServiceClient) -> str:
        """Resolve a service-principal identifier to its application object ID (cached).

        Resolution order:
        1. Try ``id eq '{sp_id}'``  — caller already provided an object ID.
        2. Fall back to ``appId eq '{sp_id}'`` — caller provided a client/app ID.
        """

        if sp_id in self._object_id_cache:
            logger.debug("srf.graph.client: object_id cache hit for sp_id=%s", sp_id)
            return self._object_id_cache[sp_id]

        from msgraph.generated.applications.applications_request_builder import (
            ApplicationsRequestBuilder,
        )
        from kiota_abstractions.base_request_configuration import RequestConfiguration

        # --- Step 1: treat sp_id as an object ID ---
        logger.debug(
            "srf.graph.client: resolving object_id for sp_id=%s via Graph API (obj_id first)",
            sp_id,
        )
        query_params_obj_id = (
            ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
                filter=f"id eq '{sp_id}'", select=["id", "appId"]
            )
        )
        config_obj_id = RequestConfiguration(query_parameters=query_params_obj_id)
        result_obj_id = await graph.applications.get(
            request_configuration=config_obj_id
        )
        apps_obj_id = (
            result_obj_id.value if result_obj_id and result_obj_id.value else []
        )
        if apps_obj_id:
            logger.debug(
                "srf.graph.client: sp_id=%s is already an object_id, using directly",
                sp_id,
            )
            self._object_id_cache[sp_id] = sp_id
            return sp_id

        # --- Step 2: fall back — treat sp_id as an app (client) ID ---
        logger.debug(
            "srf.graph.client: sp_id=%s not found as object_id, trying appId lookup",
            sp_id,
        )
        query_params_app_id = (
            ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
                filter=f"appId eq '{sp_id}'",
                select=["id", "appId"],
            )
        )
        config_app_id = RequestConfiguration(query_parameters=query_params_app_id)
        result_app_id = await graph.applications.get(
            request_configuration=config_app_id
        )
        apps = result_app_id.value if result_app_id and result_app_id.value else []
        if not apps:
            raise ValueError(
                f"Application with obj_id or app_id '{sp_id}' not found in the directory."
            )

        obj_id: str = apps[0].id  # type: ignore[assignment]
        self._object_id_cache[sp_id] = obj_id
        logger.debug(
            "srf.graph.client: resolved app_id=%s -> object_id=%s", sp_id, obj_id
        )
        return obj_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_password_credentials(self, sp_id: str) -> list[PasswordCredential]:
        logger.debug(
            "srf.graph.client: listing password credentials for sp_id=%s", sp_id
        )

        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(sp_id, graph)
            app = await graph.applications.by_application_id(obj_id).get()
            return app.password_credentials or []  # type: ignore[union-attr]

        return self._run(_call)

    def add_password_credential(
        self, sp_id: str, display_name: str, validity_days: int = 365
    ) -> PasswordCredential:
        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(sp_id, graph)
            end_dt = datetime.now(tz=timezone.utc) + timedelta(days=validity_days)
            body = AddPasswordPostRequestBody()
            cred = PasswordCredential()
            cred.display_name = display_name
            cred.end_date_time = end_dt
            body.password_credential = cred
            return await graph.applications.by_application_id(obj_id).add_password.post(
                body
            )

        return self._run(_call)

    def remove_password_credential(self, sp_id: str, key_id: str) -> None:
        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(sp_id, graph)
            body = RemovePasswordPostRequestBody()
            body.key_id = key_id
            await graph.applications.by_application_id(obj_id).remove_password.post(
                body
            )

        self._run(_call)

    def list_owners(self, sp_id: str) -> list[str]:
        logger.debug("srf.graph.client: listing owners for sp_id=%s", sp_id)

        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(sp_id, graph)
            result = await graph.applications.by_application_id(obj_id).owners.get()
            entries = result.value if result and result.value else []
            return [
                e.id
                for e in entries
                if getattr(e, "odata_type", None) == "#microsoft.graph.user"
            ]

        return self._run(_call)

    def add_owner(self, sp_id: str, user_object_id: str) -> None:
        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(sp_id, graph)
            body = ReferenceCreate()
            body.odata_id = (
                f"https://graph.microsoft.com/v1.0/directoryObjects/{user_object_id}"
            )
            await graph.applications.by_application_id(obj_id).owners.ref.post(body)

        self._run(_call)

    # def get_user_properties_by_email(self, email: str) -> None:
    #     async def _call(graph: GraphServiceClient):
    #         user_properties = await self.
