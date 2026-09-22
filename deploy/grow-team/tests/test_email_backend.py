import unittest
from unittest.mock import patch

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

if not settings.configured:
    settings.configure(
        DEFAULT_CHARSET="utf-8",
        EMAIL_HOST="https://mail.example/send",
        EMAIL_HOST_PASSWORD="test-token",
        ZULIP_ADMINISTRATOR="rama.aditya@growthcircle.id",
        NOREPLY_EMAIL_ADDRESS="noreply@growc.id",
    )

from cloudflare_email_backend import EmailBackend


class Response:
    status_code = 200

    def json(self):
        return {"success": True}


class EmailBackendTest(unittest.TestCase):
    def message(self):
        message = EmailMultiAlternatives(
            "Verifikasi", "Buka tautan", "Grow Team <noreply@growc.id>",
            ["owner@example.com"], bcc=["hidden@example.com"],
            headers={"List-Unsubscribe": "<https://team.growc.id/unsubscribe>"},
        )
        message.attach_alternative("<p>Buka tautan</p>", "text/html")
        return message

    @patch("cloudflare_email_backend.requests.post", return_value=Response())
    def test_preserves_multipart_headers_and_private_bcc(self, post):
        self.assertEqual(EmailBackend().send_messages([self.message()]), 1)
        payloads = [call.kwargs["json"] for call in post.call_args_list]
        self.assertEqual([p["to"] for p in payloads], ["owner@example.com", "hidden@example.com"])
        self.assertIn("multipart/alternative", payloads[0]["raw"])
        self.assertIn("List-Unsubscribe:", payloads[0]["raw"])
        self.assertNotIn("Bcc:", payloads[0]["raw"])
        self.assertEqual(payloads[0]["from"], "noreply@growc.id")
        self.assertFalse(post.call_args.kwargs["allow_redirects"])

    @patch("cloudflare_email_backend.requests.post")
    def test_provider_failure_is_not_reported_as_sent(self, post):
        post.return_value.status_code = 502
        post.return_value.json.return_value = {"success": False, "error": "E_DAILY_LIMIT_EXCEEDED"}
        with self.assertRaisesRegex(OSError, r"HTTP 502 \(E_DAILY_LIMIT_EXCEEDED\)"):
            EmailBackend().send_messages([self.message()])
        self.assertEqual(EmailBackend(fail_silently=True).send_messages([self.message()]), 0)

    @patch("cloudflare_email_backend.requests.post")
    def test_unconfirmed_delivery_is_not_reported_as_sent(self, post):
        post.return_value.status_code = 200
        post.return_value.json.return_value = {}
        with self.assertRaisesRegex(OSError, r"HTTP 200 \(no detail\)"):
            EmailBackend().send_messages([self.message()])

    @patch("cloudflare_email_backend.requests.post")
    def test_empty_message_list_sends_nothing(self, post):
        self.assertEqual(EmailBackend().send_messages([]), 0)
        post.assert_not_called()

    @patch("cloudflare_email_backend.requests.post", return_value=Response())
    def test_support_mail_uses_verified_sender_and_keeps_reply_address(self, post):
        message = self.message()
        message.from_email = "Grow Team support <rama.aditya@growthcircle.id>"
        self.assertEqual(EmailBackend().send_messages([message]), 1)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["from"], "noreply@growc.id")
        self.assertIn("From: Grow Team support <noreply@growc.id>", payload["raw"])
        self.assertIn("Reply-To: Grow Team support <rama.aditya@growthcircle.id>", payload["raw"])


if __name__ == "__main__":
    unittest.main()
