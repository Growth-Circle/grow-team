**Feature level ZF-48ffae**

* [`GET /needs`](/api/list-needs): With `status=open`, team-wide items
  (`broadcast` set to `true`) now leave the list after 48 hours instead of
  7 days, and of the team-wide `mention` items from one topic of a
  channel only the newest is listed; the older ones are resolved by the
  server and do not list as done. An item you reacted to, or replied to
  or marked done, is not affected.
