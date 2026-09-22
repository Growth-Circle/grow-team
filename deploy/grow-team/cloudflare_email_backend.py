from email.utils import formataddr, parseaddr
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend


class EmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        endpoint = settings.EMAIL_HOST
        token = settings.EMAIL_HOST_PASSWORD
        if urlsplit(endpoint).scheme != "https" or not token:
            raise ImproperlyConfigured("Configure the HTTPS email relay and its secret.")
        sent = 0
        for message in email_messages:
            recipients = message.recipients()
            if not recipients:
                continue
            mime = message.message()
            name, sender = parseaddr(message.from_email)
            if sender == parseaddr(settings.ZULIP_ADMINISTRATOR)[1]:
                if not mime.get("Reply-To"):
                    mime["Reply-To"] = message.from_email
                sender = parseaddr(settings.NOREPLY_EMAIL_ADDRESS)[1]
                mime.replace_header("From", formataddr((name, sender)))
            raw = mime.as_string(linesep="\r\n")
            try:
                for recipient in recipients:
                    response = requests.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {token}", "User-Agent": "Grow-Team/12.2"},
                        json={"from": sender, "to": parseaddr(recipient)[1], "raw": raw},
                        timeout=30,
                        allow_redirects=False,
                    )
                    try:
                        body = response.json()
                    except (ValueError, AttributeError):
                        body = {}
                    if response.status_code != 200 or body.get("success") is not True:
                        raise OSError(
                            f"Email relay returned HTTP {response.status_code} "
                            f"({body.get('error', 'no detail')})."
                        )
            except OSError:
                if not self.fail_silently:
                    raise
                continue
            sent += 1
        return sent
