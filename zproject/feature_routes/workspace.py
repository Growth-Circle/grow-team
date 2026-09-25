from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import workspace_settings as workspace_settings_views

# workspace owns its own API and page routes. Every route here is a
# human-connection route: normal Zulip session/API-key authentication,
# reachable at both /json/... and /api/v1/....
api_patterns: list[URLPattern] = [
    rest_path(
        "agent/realm-settings",
        GET=workspace_settings_views.get_realm_settings,
        PATCH=workspace_settings_views.patch_realm_settings,
    ),
]
page_patterns: list[URLPattern] = []
