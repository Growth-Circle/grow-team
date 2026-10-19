from unittest import mock

from zerver.actions.realm_settings import do_set_realm_property
from zerver.lib.exceptions import JsonableError
from zerver.lib.push_notifications import FailedToConnectBouncerError
from zerver.lib.remote_server import PushNotificationBouncerError
from zerver.lib.test_classes import ZulipTestCase
from zerver.models import Device, PushDeviceToken


class MobilePushCheckTest(ZulipTestCase):
    URL = "/json/mobile_push/check"

    def _set_push_service(self, enabled: bool) -> None:
        do_set_realm_property(
            self.example_user("hamlet").realm,
            "push_notifications_enabled",
            enabled,
            acting_user=None,
        )

    def test_reports_a_server_without_a_push_service(self) -> None:
        user = self.example_user("hamlet")
        self._set_push_service(False)
        PushDeviceToken.objects.create(user=user, token="ab" * 16, kind=PushDeviceToken.FCM)
        self.login_user(user)
        with (
            mock.patch("zerver.views.notification_check.send_test_push_notification") as send,
            mock.patch("zerver.views.notification_check.send_e2ee_test_push_notification") as e2ee,
        ):
            result = self.assert_json_success(self.client_post(self.URL))
        self.assertEqual(result["push_service_configured"], False)
        self.assertFalse(result["sent"])
        send.assert_not_called()
        e2ee.assert_not_called()

    def test_reports_no_registered_device(self) -> None:
        self._set_push_service(True)
        self.login("hamlet")
        with mock.patch("zerver.views.notification_check.send_test_push_notification") as send:
            result = self.assert_json_success(self.client_post(self.URL))
        self.assertEqual(
            (result["push_service_configured"], result["device_count"], result["sent"]),
            (True, 0, False),
        )
        self.assertIsNone(result["error"])
        send.assert_not_called()

    def test_sends_to_both_kinds_of_device(self) -> None:
        user = self.example_user("hamlet")
        self._set_push_service(True)
        PushDeviceToken.objects.create(user=user, token="ab" * 16, kind=PushDeviceToken.FCM)
        Device.objects.create(user=user, push_token_id=7)
        # A device that has not finished registering with the bouncer gets nothing.
        Device.objects.create(user=user, pending_push_token_id=8)
        self.login_user(user)
        with (
            mock.patch("zerver.views.notification_check.send_test_push_notification") as send,
            mock.patch("zerver.views.notification_check.send_e2ee_test_push_notification") as e2ee,
        ):
            result = self.assert_json_success(self.client_post(self.URL))
        self.assertEqual(
            (result["push_service_configured"], result["device_count"], result["sent"]),
            (True, 2, True),
        )
        self.assertIsNone(result["error"])
        send.assert_called_once()
        e2ee.assert_called_once()

    def test_says_why_the_push_could_not_be_sent(self) -> None:
        user = self.example_user("hamlet")
        self._set_push_service(True)
        PushDeviceToken.objects.create(user=user, token="ab" * 16, kind=PushDeviceToken.FCM)
        self.login_user(user)
        with mock.patch(
            "zerver.views.notification_check.send_test_push_notification",
            side_effect=FailedToConnectBouncerError,
        ):
            result = self.assert_json_success(self.client_post(self.URL))
        self.assertFalse(result["sent"])
        self.assertEqual(
            result["error"], "Network error while connecting to Zulip push notification service."
        )

        with (
            mock.patch(
                "zerver.views.notification_check.send_test_push_notification",
                side_effect=PushNotificationBouncerError("bad key"),
            ),
            self.assertLogs("zerver.views.notification_check", level="ERROR"),
        ):
            result = self.assert_json_success(self.client_post(self.URL))
        self.assertFalse(result["sent"])
        self.assertEqual(result["error"], "The push service refused the request.")

    def test_a_json_error_of_sending_is_reported(self) -> None:
        user = self.example_user("hamlet")
        self._set_push_service(True)
        PushDeviceToken.objects.create(user=user, token="ab" * 16, kind=PushDeviceToken.FCM)
        self.login_user(user)
        with mock.patch(
            "zerver.views.notification_check.send_test_push_notification",
            side_effect=JsonableError("Boom"),
        ):
            result = self.assert_json_success(self.client_post(self.URL))
        self.assertEqual(result["error"], "Boom")

    def test_only_a_human_can_check(self) -> None:
        bot = self.example_user("default_bot")
        result = self.api_post(bot, "/api/v1/mobile_push/check")
        self.assert_json_error(result, "This endpoint does not accept bot requests.")
