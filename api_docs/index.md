# The Grow Team API

Grow Team uses the compatible Grow Team API protocol. These APIs allow you to
integrate other services with Grow Team. This
guide should help you find the API you need:

* First, check if the tool you'd like to integrate with Grow Team
  [already has a native integration](/integrations/).
* Next, check if [Zapier](https://zapier.com/apps) or
  [IFTTT](https://ifttt.com/search) has an integration.
  native integrations can often connect a service without writing code.
* If you'd like to send content into Grow Team, you can
  [write a native incoming webhook integration][incoming-webhooks-overview]
  or use the [API for sending messages](/api/send-message).
* If you're building an interactive bot that reacts to activity inside
  Grow Team, see the
  [Python framework for interactive bots](/help/running-bots) or
  [real-time events API](/api/get-events).

To build your own Grow Team integration, check out
the full [REST API](/api/rest), generally starting with
[installing the API client bindings](/api/installation-instructions).

In case you already know how you want to build your integration and you're
just looking for an API key, we've got you covered [here](/api/api-keys).

[incoming-webhooks-overview]: https://zulip.readthedocs.io/en/latest/webhooks/incoming-webhooks-overview.html
