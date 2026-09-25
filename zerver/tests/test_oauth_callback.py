import datetime

import time_machine
from django.utils.timezone import now as timezone_now

from zerver.lib.oauth_callback import (
    OAuthState,
    OAuthStateError,
    get_exchanger,
    register,
    sign_state,
    verify_state,
)
from zerver.lib.test_classes import ZulipTestCase

OAUTH_CALLBACK_HOST = "auth.testserver"


class OAuthCallbackHelperTest(ZulipTestCase):
    """Unit tests for the sign/verify contract in zerver/lib/oauth_callback.py."""

    def test_sign_and_verify_round_trip(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "google", "#workspace-settings/integrations")
        oauth_state = verify_state(state, provider="google")
        self.assertEqual(oauth_state.realm_id, hamlet.realm_id)
        self.assertEqual(oauth_state.user_id, hamlet.id)
        self.assertEqual(oauth_state.provider, "google")
        self.assertEqual(oauth_state.return_hash, "#workspace-settings/integrations")

    def test_garbage_state_is_rejected(self) -> None:
        with self.assertRaises(OAuthStateError):
            verify_state("not-a-real-token", provider="google")

    def test_provider_mismatch_is_rejected(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "google", "#drive")
        with self.assertRaises(OAuthStateError):
            verify_state(state, provider="github")

    def test_expired_state_is_rejected(self) -> None:
        hamlet = self.example_user("hamlet")
        start = timezone_now()
        with time_machine.travel(start, tick=False):
            state = sign_state(hamlet.realm, hamlet, "google", "#drive")
        with (
            time_machine.travel(start + datetime.timedelta(minutes=11), tick=False),
            self.assertRaises(OAuthStateError),
        ):
            verify_state(state, provider="google")

    def test_state_cannot_be_used_twice(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "google", "#drive")
        verify_state(state, provider="google")
        with self.assertRaises(OAuthStateError):
            verify_state(state, provider="google")

    def test_register_rejects_duplicate_provider(self) -> None:
        register("sanji-test-duplicate", lambda request, code, state: None)
        with self.assertRaises(AssertionError):
            register("sanji-test-duplicate", lambda request, code, state: None)
        self.assertIsNotNone(get_exchanger("sanji-test-duplicate"))


class OAuthCallbackViewTest(ZulipTestCase):
    """Integration tests for GET /oauth/callback/<provider>."""

    def test_wrong_host_is_not_found(self) -> None:
        hamlet = self.example_user("hamlet")
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/google",
                {"state": "irrelevant", "code": "abc"},
                HTTP_HOST=hamlet.realm.host,
            )
        self.assertEqual(result.status_code, 404)

    def test_invalid_state_returns_friendly_error(self) -> None:
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/google",
                {"state": "not-a-real-token", "code": "abc"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 400)
        content = result.content.decode()
        self.assertIn("Start again from your workspace", content)
        # Never leak the internal rejection reason.
        self.assertNotIn("BadSignature", content)
        self.assertNotIn("nonce", content)

    def test_cross_host_redirects_back_to_originating_realm(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(
            hamlet.realm, hamlet, "sanji-test-crosshost", "#workspace-settings/integrations"
        )
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-crosshost",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(
            result["Location"],
            hamlet.realm.url + "#workspace-settings/integrations",
        )

    def test_provider_denial_redirects_without_exchange(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-denial", "#drive")
        called: list[str] = []
        register("sanji-test-denial", lambda request, code, oauth_state: called.append(code))
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-denial",
                {"state": state, "error": "access_denied"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], hamlet.realm.url + "#drive")
        self.assertEqual(called, [])

    def test_successful_callback_invokes_registered_exchange(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-success", "#drive")
        received: list[tuple[str, OAuthState]] = []
        register(
            "sanji-test-success",
            lambda request, code, oauth_state: received.append((code, oauth_state)),
        )
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-success",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], hamlet.realm.url + "#drive")
        self.assertEqual(len(received), 1)
        code, oauth_state = received[0]
        self.assertEqual(code, "abc123")
        self.assertEqual(oauth_state.user_id, hamlet.id)

    def test_exchange_failure_still_redirects(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-failure", "#drive")

        def failing_exchange(request: object, code: str, oauth_state: OAuthState) -> None:
            raise RuntimeError("provider is unreachable")

        register("sanji-test-failure", failing_exchange)
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-failure",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], hamlet.realm.url + "#drive")
