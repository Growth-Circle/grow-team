**Feature level ZF-98ffbd**

* [`GET /streams/{stream_id}/meta`](/api/get-room-meta),
  [`PATCH /streams/{stream_id}/meta`](/api/update-room-meta): New endpoints
  read and edit a room's type folder, owner, due date, and summary
  opt-in. The PATCH endpoint can also ask the room's built-in assistant to
  post its first message.
