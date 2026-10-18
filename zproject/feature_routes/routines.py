from django.urls.resolvers import URLPattern

from zerver.lib.rest import rest_path
from zerver.views import routines as routine_views

api_patterns: list[URLPattern] = [
    rest_path("routines", GET=routine_views.list_routines, POST=routine_views.create_routine),
    rest_path("routines/templates", GET=routine_views.list_routine_templates),
    rest_path("routines/agents", GET=routine_views.list_routine_agents),
    rest_path(
        "routines/<uuid:routine_id>",
        GET=routine_views.get_routine,
        PATCH=routine_views.update_routine,
        DELETE=routine_views.delete_routine,
    ),
    rest_path("routines/<uuid:routine_id>/pause", POST=routine_views.pause_routine),
    rest_path("routines/<uuid:routine_id>/resume", POST=routine_views.resume_routine),
    rest_path("routines/<uuid:routine_id>/run", POST=routine_views.run_routine),
    rest_path("routines/<uuid:routine_id>/runs", GET=routine_views.list_routine_runs),
]
page_patterns: list[URLPattern] = []
