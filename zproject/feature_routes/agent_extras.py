from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views.agents import get_agent_profile_stats

# agent_extras owns its own API and page routes. This module starts empty;
# whichever feature is built in this area fills it in without needing
# to touch zproject/urls.py again.
api_patterns: list[URLPattern] = [
    rest_path("agent/profiles/stats", GET=get_agent_profile_stats),
]
page_patterns: list[URLPattern] = []
