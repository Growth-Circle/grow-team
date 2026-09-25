import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.translation import gettext as _

from zerver.lib.oauth_callback import OAuthStateError, get_exchanger, verify_state
from zerver.models import Realm, UserProfile

# The browser lands here after approving (or denying) access on the
# provider's own site. There is no Zulip session on this host (see
# zerver/lib/oauth_callback.py for why), so identity travels entirely in
# the signed `state` value instead.
LINK_INVALID_MESSAGE = _(
    "This connection link is no longer valid. Start again from your workspace."
)


def oauth_callback_page(request: HttpRequest, provider: str) -> HttpResponse:
    if not settings.OAUTH_CALLBACK_HOST or request.get_host() != settings.OAUTH_CALLBACK_HOST:
        return HttpResponse(status=404)

    try:
        oauth_state = verify_state(request.GET.get("state", ""), provider=provider)
    except OAuthStateError:
        return HttpResponse(LINK_INVALID_MESSAGE, status=400)

    try:
        realm = Realm.objects.get(id=oauth_state.realm_id)
        UserProfile.objects.get(id=oauth_state.user_id, realm=realm)
    except (Realm.DoesNotExist, UserProfile.DoesNotExist):
        return HttpResponse(LINK_INVALID_MESSAGE, status=400)

    # Whatever happens next, send the browser back to where the connect
    # flow started; the destination page reads the resulting status from
    # its own API rather than from this URL.
    destination = realm.url + oauth_state.return_hash

    code = request.GET.get("code")
    if request.GET.get("error") or not code:
        return HttpResponseRedirect(destination)

    exchange = get_exchanger(provider)
    if exchange is not None:
        try:
            exchange(request, code, oauth_state)
        except Exception:
            logging.exception("OAuth code exchange failed for provider %s", provider)

    return HttpResponseRedirect(destination)
