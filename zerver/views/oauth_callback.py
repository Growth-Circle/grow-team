import logging

from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.utils.translation import gettext_lazy as _

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
    # With a fixed callback host configured, every provider redirects
    # here, so a mismatched Host is rejected up front, the same way an
    # undefined route would be. With no fixed host configured (a
    # single-realm dev setup), each realm is its own callback host
    # instead; that can only be checked once `state` reveals which realm
    # this request belongs to, further down.
    configured_host = settings.OAUTH_CALLBACK_HOST
    if configured_host and request.get_host().lower() != configured_host.lower():
        return HttpResponse(status=404)

    try:
        oauth_state = verify_state(request.GET.get("state", ""), provider=provider)
    except OAuthStateError:
        return HttpResponse(LINK_INVALID_MESSAGE, status=400)

    try:
        realm = Realm.objects.get(id=oauth_state.realm_id)
        user_profile = UserProfile.objects.get(id=oauth_state.user_id, realm=realm)
    except (Realm.DoesNotExist, UserProfile.DoesNotExist):
        return HttpResponse(LINK_INVALID_MESSAGE, status=400)

    if not configured_host and request.get_host().lower() != realm.host:
        return HttpResponse(status=404)

    if realm.deactivated or not user_profile.is_active:
        return HttpResponse(LINK_INVALID_MESSAGE, status=400)

    # sign_state refuses to sign anything else, but a defense-in-depth
    # check here means a redirect is never built from an unchecked value,
    # regardless of how `state` was produced.
    if not oauth_state.return_hash.startswith("#"):
        return HttpResponse(LINK_INVALID_MESSAGE, status=400)

    # Whatever happens next, send the browser back to where the connect
    # flow started; the destination page reads the resulting status from
    # its own API rather than from this URL.
    destination = realm.url + oauth_state.return_hash

    code = request.GET.get("code")
    if request.GET.get("error") or not code:
        return HttpResponseRedirect(destination)

    exchange = get_exchanger(provider)
    if exchange is None:
        # A provider only reaches this view after registering an
        # exchanger for it at import time; reaching here with none
        # registered means that registration was missed.
        logging.error("No OAuth code exchanger registered for provider %s", provider)
        return HttpResponseRedirect(destination)

    try:
        exchange(request, code, user_profile, oauth_state)
    except Exception:
        logging.exception("OAuth code exchange failed for provider %s", provider)

    return HttpResponseRedirect(destination)
