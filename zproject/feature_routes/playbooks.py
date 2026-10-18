from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import playbooks as playbook_views

api_patterns: list[URLPattern] = [
    rest_path("playbooks", GET=playbook_views.list_playbooks, POST=playbook_views.create_playbook),
    rest_path("playbooks/render", POST=playbook_views.render_playbook_preview),
    rest_path(
        "playbooks/<int:playbook_id>",
        GET=playbook_views.get_playbook,
        PATCH=playbook_views.update_playbook,
    ),
    rest_path("playbooks/<int:playbook_id>/archive", POST=playbook_views.archive_playbook),
    rest_path("playbooks/<int:playbook_id>/unarchive", POST=playbook_views.unarchive_playbook),
    rest_path("playbooks/<int:playbook_id>/diff", GET=playbook_views.get_playbook_diff),
    rest_path(
        "playbooks/<int:playbook_id>/revisions/<int:revision_number>",
        GET=playbook_views.get_playbook_revision,
    ),
]
page_patterns: list[URLPattern] = []
