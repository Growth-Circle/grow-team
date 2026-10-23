**Feature level ZF-b1a4c0**

* [`GET /messages/stale_unread`](/api/get-stale-unread-count),
  [`POST /messages/stale_unread`](/api/mark-stale-unread-as-read): New
  endpoints to count the requesting user's unread messages that are older
  than a week, and to mark them as read.
