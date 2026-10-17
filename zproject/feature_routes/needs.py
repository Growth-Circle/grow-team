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
    rest_path("needs/resolve", POST=needs_views.resolve_needs),
    rest_path("needs/unresolve", POST=needs_views.unresolve_needs),
    rest_path("needs/snooze", POST=needs_views.snooze_needs),
    rest_path("needs/unsnooze", POST=needs_views.unsnooze_needs),
    rest_path(
        "needs/card_notifications/<int:notification_id>/resolve",
        POST=needs_views.resolve_card_notification,
    ),
]
page_patterns: list[URLPattern] = []
