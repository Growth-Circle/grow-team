from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import agent_status as agent_status_views

api_patterns: list[URLPattern] = [
    rest_path("agent/profiles/<uuid:profile_id>/status", GET=agent_status_views.get_agent_status),
]
page_patterns: list[URLPattern] = []
