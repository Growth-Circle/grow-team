# Route registry for Sanji feature areas (see PLAN.md Sanji WP04, section
# 4.2). Each module below owns its own API and page routes, so adding a
# new area never requires editing zproject/urls.py again. A module that
# has not grown its own routes yet exports two empty lists.
from django.urls.resolvers import URLPattern

from zproject.feature_routes import (
    agent_devices,
    agent_extras,
    agent_runner_extras,
    cloud_runners,
    device_page,
    drive,
    home,
    integrations,
    mcp,
    meetings,
    models,
    needs,
    oauth_callback,
    push,
    pwa,
    rooms,
    runner_install,
    whatsapp,
    workspace,
    workspace_members,
)

_MODULES = (
    rooms,
    needs,
    home,
    workspace,
    workspace_members,
    agent_extras,
    agent_devices,
    agent_runner_extras,
    runner_install,
    push,
    pwa,
    device_page,
    integrations,
    meetings,
    drive,
    whatsapp,
    mcp,
    models,
    cloud_runners,
    oauth_callback,
)

API_PATTERNS: list[URLPattern] = [pattern for module in _MODULES for pattern in module.api_patterns]
PAGE_PATTERNS: list[URLPattern] = [
    pattern for module in _MODULES for pattern in module.page_patterns
]
