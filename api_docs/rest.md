# The Grow Team REST API

The Grow Team REST API powers the Grow Team web app. It remains compatible with
the Grow Team API protocol. To use this API:

* You'll need to [get an API key](/api/api-keys).  You will likely
  want to [create a bot](/help/add-a-bot-or-integration), unless you're
  using the API to interact with
  your own account (e.g., exporting your personal message history).
* Choose what language you'd like to use.  You can download the
  [Python or JavaScript bindings](/api/installation-instructions), projects in
  [other languages](/api/client-libraries), or
  just make HTTP requests with your favorite programming language.
* If you're making your own HTTP requests, you'll want to send the
  appropriate [HTTP basic authentication headers](/api/http-headers).
* The API has a standard
  [system for reporting errors](/api/rest-error-handling).

Most other details are covered in the documentation for the individual
endpoints:

!!! tip ""

    You may use the `client.call_endpoint` method of our Python API
    bindings to call an endpoint that isn't documented here. For an
    example, see [Upload a custom emoji](/api/upload-custom-emoji).

{!rest-endpoints.md!}

For implementation details that are not documented here, consult the
[Grow Team source code](https://github.com/Growth-Circle/grow-team).
