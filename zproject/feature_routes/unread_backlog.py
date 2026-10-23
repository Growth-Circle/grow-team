from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import unread_backlog as unread_backlog_views

api_patterns: list[URLPattern] = [
    rest_path(
        "messages/stale_unread",
        GET=unread_backlog_views.get_stale_unread,
        POST=unread_backlog_views.mark_stale_unread,
    ),
]
page_patterns: list[URLPattern] = []
