from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.lib.room_digests import get_room_digest_view, summarize_room
from zerver.views.room_meta import (
    archive_channels,
    get_quiet_channels_view,
    get_room_meta,
    get_room_topics_view,
    update_room_meta,
)

api_patterns: list[URLPattern] = [
    rest_path("streams/<int:stream_id>/meta", GET=get_room_meta, PATCH=update_room_meta),
    rest_path("streams/<int:stream_id>/topics", GET=get_room_topics_view),
    rest_path("streams/<int:stream_id>/summarize", POST=summarize_room),
    rest_path("streams/<int:stream_id>/digest", GET=get_room_digest_view),
    rest_path("channels/quiet", GET=get_quiet_channels_view),
    rest_path("channels/archive", POST=archive_channels),
]
page_patterns: list[URLPattern] = []
