from django.urls import path
from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import mcp_access, mcp_server

api_patterns: list[URLPattern] = [
    rest_path("mcp/access", GET=mcp_access.list_access, POST=mcp_access.create_access),
    rest_path("mcp/access/<uuid:grant_id>/revoke", POST=mcp_access.revoke_access),
]
page_patterns: list[URLPattern] = [
    path("mcp", mcp_server.mcp),
    path("mcp/", mcp_server.mcp),
    path(".well-known/oauth-protected-resource", mcp_access.protected_metadata),
    path(".well-known/oauth-protected-resource/mcp", mcp_access.protected_metadata),
    path(".well-known/oauth-authorization-server", mcp_access.authorization_metadata),
    path("mcp/authorize", mcp_access.authorize),
    path("mcp/register", mcp_access.register_client),
    path("mcp/token", mcp_access.token),
    path("mcp/revoke", mcp_access.revoke_token),
]
