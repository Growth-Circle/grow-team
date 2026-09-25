from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views.room_meta import get_room_meta, get_room_topics_view, update_room_meta

api_patterns: list[URLPattern] = [
    rest_path("streams/<int:stream_id>/meta", GET=get_room_meta, PATCH=update_room_meta),
    rest_path("streams/<int:stream_id>/topics", GET=get_room_topics_view),
]
page_patterns: list[URLPattern] = []
