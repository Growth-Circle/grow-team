from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import needs as needs_views

api_patterns: list[URLPattern] = [
    rest_path("needs", GET=needs_views.list_needs),
    rest_path(
        "needs/mentions/<int:message_id>/resolve",
        POST=needs_views.resolve_mention,
        DELETE=needs_views.resolve_mention,
    ),
]
page_patterns: list[URLPattern] = []
