from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views.room_meta import get_room_meta, update_room_meta

api_patterns: list[URLPattern] = [
    rest_path("streams/<int:stream_id>/meta", GET=get_room_meta, PATCH=update_room_meta),
]
page_patterns: list[URLPattern] = []
