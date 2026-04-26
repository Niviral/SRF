from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from azure.core.credentials import TokenCredential
from msgraph.graph_service_client import GraphServiceClient
from msgraph.generated.applications.item.add_password.add_password_post_request_body import (
    AddPasswordPostRequestBody,
)
from msgraph.generated.applications.item.remove_password.remove_password_post_request_body import (
    RemovePasswordPostRequestBody,
)
from msgraph.generated.models.password_credential import PasswordCredential
from msgraph.generated.models.reference_create import ReferenceCreate
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from kiota_abstractions.base_request_configuration import RequestConfiguration
from msgraph.generated.applications.applications_request_builder import (
    ApplicationsRequestBuilder,
)
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

_GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]
logger = logging.getLogger(__name__)


class GraphClient:
    """Thin wrapper around msgraph-sdk for SP password credential operations."""

    def __init__(self, credential: TokenCredential) -> None:
        self._credential = credential
        self._object_id_cache: dict[str, str] = {}
        self._email_resolution_disabled = False

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

    async def _get_object_id(self, app_id: str, graph: GraphServiceClient) -> str:
        """Resolve appId (client ID) to the application's object ID (cached)."""

        if app_id in self._object_id_cache:
            logger.debug("srf.graph.client: object_id cache hit for app_id=%s", app_id)
            return self._object_id_cache[app_id]

        logger.debug(
            "srf.graph.client: resolving object_id for app_id=%s via Graph API", app_id
        )
        query_params = (
            ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
                filter=f"appId eq '{app_id}'",
                select=["id", "appId"],
            )
        )
        config = RequestConfiguration(query_parameters=query_params)
        result = await graph.applications.get(request_configuration=config)
        apps = result.value if result and result.value else []
        if not apps:
            logger.debug(
                "srf.graph.client: resolving if provide value=%s is object_id via Graph API",
                app_id,
            )
            query_params_obj_id = (
                ApplicationsRequestBuilder.ApplicationsRequestBuilderGetQueryParameters(
                    filter=f"id eq '{app_id}'", select=["id", "appId"]
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
                    "srf.graph.client: value=%s is already an object_id, using directly",
                    app_id,
                )
                self._object_id_cache[app_id] = app_id
                return app_id
            else:
                raise ValueError(
                    f"Application with appId or objID'{app_id}' not found in the directory."
                )
        obj_id: str = apps[0].id  # type: ignore[assignment]
        self._object_id_cache[app_id] = obj_id
        logger.debug(
            "srf.graph.client: resolved app_id=%s -> object_id=%s", app_id, obj_id
        )
        return obj_id

    async def _get_email_obj_id(self, email: str, graph: GraphServiceClient) -> str:

        if email in self._object_id_cache:
            logger.debug("srf.graph.client: email cache hit for email=%s", email)
            return self._object_id_cache[email]

        logger.debug(
            "srf.graph.client: resolving object_id for email=%s via Graph API", email
        )
        query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
            filter=f"mail eq '{email}'or userPrincipalName eq '{email}'",
            select=["id", "mail", "userPrincipalName"],
        )
        config = RequestConfiguration(query_parameters=query_params)
        try:
            result = await graph.users.get(request_configuration=config)
        except ODataError as e:
            if e.response_status_code == 403:
                logger.error(
                    "srf.graph.client: code:%s, message:%s",
                    e.response_status_code,
                    e.primary_message,
                )
                self._email_resolution_disabled = True
                raise PermissionError
        else:
            emails = result.value if result and result.value else []
            if not emails:
                raise ValueError(f"Owner with email: {email} not found in directory")
            obj_id: str = emails[0].id  # type: ignore[assigment]
            self._object_id_cache[email] = obj_id
            logger.debug(
                "srf.graph.client: resolved email=%s -> object_id=%s", email, obj_id
            )
            return obj_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_password_credentials(self, app_id: str) -> list[PasswordCredential]:
        logger.debug(
            "srf.graph.client: listing password credentials for app_id=%s", app_id
        )

        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(app_id, graph)
            app = await graph.applications.by_application_id(obj_id).get()
            return app.password_credentials or []  # type: ignore[union-attr]

        return self._run(_call)

    def add_password_credential(
        self, app_id: str, display_name: str, validity_days: int = 365
    ) -> PasswordCredential:
        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(app_id, graph)
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

    def remove_password_credential(self, app_id: str, key_id: str) -> None:
        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(app_id, graph)
            body = RemovePasswordPostRequestBody()
            body.key_id = key_id
            await graph.applications.by_application_id(obj_id).remove_password.post(
                body
            )

        self._run(_call)

    def list_owners(self, app_id: str) -> list[str]:
        logger.debug("srf.graph.client: listing owners for app_id=%s", app_id)

        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(app_id, graph)
            result = await graph.applications.by_application_id(obj_id).owners.get()
            entries = result.value if result and result.value else []
            return [
                e.id
                for e in entries
                if getattr(e, "odata_type", None) == "#microsoft.graph.user"
            ]

        return self._run(_call)

    def add_owner(self, app_id: str, user_object_id: str) -> None:
        async def _call(graph: GraphServiceClient):
            obj_id = await self._get_object_id(app_id, graph)
            body = ReferenceCreate()
            body.odata_id = (
                f"https://graph.microsoft.com/v1.0/directoryObjects/{user_object_id}"
            )
            await graph.applications.by_application_id(obj_id).owners.ref.post(body)

        self._run(_call)

    def get_user_by_email(self, email: str) -> str:
        async def _call(graph: GraphServiceClient):
            if not self._email_resolution_disabled:
                user_obj_id = await self._get_email_obj_id(email, graph)
                return user_obj_id
            else:
                logger.warning(
                    "srf.graph.client: Email resolution disabled, operator missing not allowed to query `User.ReadBasic.All`"
                )
                raise PermissionError

        return self._run(_call)
