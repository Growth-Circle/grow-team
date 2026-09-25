**Feature level ZF-98ffbd**

* [`GET /streams/{stream_id}/meta`](/api/get-room-meta),
  [`PATCH /streams/{stream_id}/meta`](/api/update-room-meta): New endpoints
  read and edit a room's type folder, owner, due date, and summary
  opt-in. The PATCH endpoint can also ask the room's built-in assistant to
  post its first message.
* [`GET /streams/{stream_id}/topics`](/api/get-room-topics): New endpoint
  lists a room's 50 most recently active topics, each with its message
  count.
* [`GET /channels/quiet`](/api/get-quiet-channels): New endpoint lists
  channels with no message in the last 30 days.
* [`POST /channels/archive`](/api/archive-channels): New endpoint
  archives several channels in one request.
