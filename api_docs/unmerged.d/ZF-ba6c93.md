**Feature level ZF-ba6c93**

* [`GET /needs`](/api/list-needs): New `reply` item kind, for unread
  messages that other people wrote in a topic you follow since you last
  wrote there, one item per topic, with a matching count. Items of this
  kind carry a new `reply` object; the field is absent on every other kind.
  A `reply` item is never listed with `status=resolved`.
