import datetime
from unittest import mock

import time_machine
from django.core.signing import dumps
from django.utils.timezone import now as timezone_now
from typing_extensions import override

from zerver.actions.users import do_deactivate_user
from zerver.lib.oauth_callback import (
    STATE_SALT,
    OAuthState,
    OAuthStateError,
    callback_url,
    get_exchanger,
    register,
    sign_state,
    verify_state,
)
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import UserProfile
from zerver.models.realms import get_realm

OAUTH_CALLBACK_HOST = "auth.testserver"


class OAuthCallbackTestCase(ZulipTestCase):
    """A registered exchanger leaks into every other test in the process
    unless each test starts from, and restores, an empty registry."""

    @override
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.dict("zerver.lib.oauth_callback._exchangers", clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class OAuthCallbackHelperTest(OAuthCallbackTestCase):
    """Unit tests for the sign/verify contract in zerver/lib/oauth_callback.py."""

    def test_sign_and_verify_round_trip(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "google", "#workspace-settings/integrations")
        oauth_state = verify_state(state, provider="google")
        self.assertEqual(oauth_state.realm_id, hamlet.realm_id)
        self.assertEqual(oauth_state.user_id, hamlet.id)
        self.assertEqual(oauth_state.provider, "google")
        self.assertEqual(oauth_state.return_hash, "#workspace-settings/integrations")
        self.assertIsNone(oauth_state.code_verifier)

    def test_sign_state_rejects_non_fragment_return_hash(self) -> None:
        hamlet = self.example_user("hamlet")
        for bad_hash in ["", "drive", "/drive", ".evil.example", "@evil.example"]:
            with self.assertRaises(ValueError):
                sign_state(hamlet.realm, hamlet, "google", bad_hash)

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
        register("sanji-test-duplicate", lambda request, code, user_profile, state: None)
        with self.assertRaises(AssertionError):
            register("sanji-test-duplicate", lambda request, code, user_profile, state: None)
        self.assertIsNotNone(get_exchanger("sanji-test-duplicate"))

    def test_pkce_code_verifier_round_trip(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(
            hamlet.realm, hamlet, "openrouter", "#drive", code_verifier="a-secret-verifier"
        )
        oauth_state = verify_state(state, provider="openrouter")
        self.assertEqual(oauth_state.code_verifier, "a-secret-verifier")

    def test_callback_url_uses_configured_host(self) -> None:
        hamlet = self.example_user("hamlet")
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST, EXTERNAL_URI_SCHEME="https://"):
            self.assertEqual(
                callback_url(hamlet.realm, "google"),
                f"https://{OAUTH_CALLBACK_HOST}/oauth/callback/google",
            )

    def test_callback_url_falls_back_to_realm_host(self) -> None:
        hamlet = self.example_user("hamlet")
        with self.settings(OAUTH_CALLBACK_HOST=None, EXTERNAL_URI_SCHEME="https://"):
            self.assertEqual(
                callback_url(hamlet.realm, "google"),
                f"https://{hamlet.realm.host}/oauth/callback/google",
            )

    def test_verify_state_rejects_payload_missing_nonce(self) -> None:
        # Exercises the same rejection path as a missing/blank nonce field,
        # independent of how the signed token was produced.
        hamlet = self.example_user("hamlet")
        state = dumps(
            {
                "realm_id": hamlet.realm_id,
                "user_id": hamlet.id,
                "provider": "google",
                "return_hash": "#drive",
            },
            salt=STATE_SALT,
        )
        with self.assertRaises(OAuthStateError):
            verify_state(state, provider="google")

    def test_verify_state_rejects_payload_missing_realm_id(self) -> None:
        # A well-formed, correctly-signed token that is nonetheless missing
        # a required field hits the trailing KeyError branch, not the
        # nonce check above.
        hamlet = self.example_user("hamlet")
        state = dumps(
            {
                "user_id": hamlet.id,
                "provider": "google",
                "return_hash": "#drive",
                "nonce": "some-nonce",
            },
            salt=STATE_SALT,
        )
        with self.assertRaises(OAuthStateError):
            verify_state(state, provider="google")


class OAuthCallbackViewTest(OAuthCallbackTestCase):
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

    def test_configured_host_is_matched_case_insensitively(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-case", "#drive")
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-case",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST.upper(),
            )
        self.assertEqual(result.status_code, 302)

    def test_unset_callback_host_falls_back_to_realm_host(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-unset-host", "#drive")
        with self.settings(OAUTH_CALLBACK_HOST=None):
            result = self.client_get(
                "/oauth/callback/sanji-test-unset-host",
                {"state": state, "code": "abc123"},
                HTTP_HOST=hamlet.realm.host,
            )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], hamlet.realm.url + "#drive")

    def test_unset_callback_host_rejects_other_realms_host(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-unset-host-mismatch", "#drive")
        other_realm = get_realm("zephyr")
        with self.settings(OAUTH_CALLBACK_HOST=None):
            result = self.client_get(
                "/oauth/callback/sanji-test-unset-host-mismatch",
                {"state": state, "code": "abc123"},
                HTTP_HOST=other_realm.host,
            )
        self.assertEqual(result.status_code, 404)

    def test_deactivated_realm_is_rejected(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-deactivated-realm", "#drive")
        hamlet.realm.deactivated = True
        hamlet.realm.save(update_fields=["deactivated"])
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-deactivated-realm",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 400)

    def test_deactivated_user_is_rejected(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-deactivated-user", "#drive")
        do_deactivate_user(hamlet, acting_user=None)
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-deactivated-user",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 400)

    def test_provider_denial_redirects_without_exchange(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-denial", "#drive")
        called: list[str] = []
        register(
            "sanji-test-denial",
            lambda request, code, user_profile, oauth_state: called.append(code),
        )
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
        received: list[tuple[str, UserProfile, OAuthState]] = []
        register(
            "sanji-test-success",
            lambda request, code, user_profile, oauth_state: received.append(
                (code, user_profile, oauth_state)
            ),
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
        code, user_profile, oauth_state = received[0]
        self.assertEqual(code, "abc123")
        self.assertEqual(user_profile.id, hamlet.id)
        self.assertEqual(oauth_state.user_id, hamlet.id)

    def test_missing_exchanger_is_logged(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-unregistered", "#drive")
        with (
            self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST),
            self.assertLogs(level="ERROR") as logs,
        ):
            result = self.client_get(
                "/oauth/callback/sanji-test-unregistered",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 302)
        self.assertTrue(
            any("sanji-test-unregistered" in line for line in logs.output),
            logs.output,
        )

    def test_exchange_failure_still_redirects(self) -> None:
        hamlet = self.example_user("hamlet")
        state = sign_state(hamlet.realm, hamlet, "sanji-test-failure", "#drive")

        def failing_exchange(
            request: object, code: str, user_profile: UserProfile, oauth_state: OAuthState
        ) -> None:
            raise RuntimeError("provider is unreachable")

        register("sanji-test-failure", failing_exchange)
        with (
            self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST),
            self.assertLogs(level="ERROR") as logs,
        ):
            result = self.client_get(
                "/oauth/callback/sanji-test-failure",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], hamlet.realm.url + "#drive")
        self.assertTrue(
            any("OAuth code exchange failed" in line for line in logs.output), logs.output
        )

    def test_nonexistent_user_returns_friendly_error(self) -> None:
        hamlet = self.example_user("hamlet")
        state = dumps(
            {
                "realm_id": hamlet.realm_id,
                "user_id": 999999999,
                "provider": "sanji-test-missing-user",
                "return_hash": "#drive",
                "nonce": "test-nonce-for-missing-user",
            },
            salt=STATE_SALT,
        )
        with self.settings(OAUTH_CALLBACK_HOST=OAUTH_CALLBACK_HOST):
            result = self.client_get(
                "/oauth/callback/sanji-test-missing-user",
                {"state": state, "code": "abc123"},
                HTTP_HOST=OAUTH_CALLBACK_HOST,
            )
        self.assertEqual(result.status_code, 400)
