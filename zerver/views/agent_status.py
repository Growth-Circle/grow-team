from uuid import UUID

from django.http import HttpRequest, HttpResponse

from zerver.lib import agent_status
from zerver.models import UserProfile, agents
from zerver.views.agents import _directory_profiles, _success, safe_agent_endpoint


@safe_agent_endpoint
def get_agent_status(
    request: HttpRequest, user_profile: UserProfile, profile_id: UUID
) -> HttpResponse:
    visible, _complete = _directory_profiles(user_profile)
    # An agent the user cannot see answers like an id that is not there.
    if profile_id not in visible:
        raise ValueError("Profile is unavailable.")
    profile = agents.AgentProfile.objects.get(id=profile_id, realm=user_profile.realm)
    return _success(request, dict(agent_status.get_agent_status(user_profile, profile)))
