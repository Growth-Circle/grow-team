**Feature level ZF-63e6db**

* [`PATCH /tasks/{task_id}`](/api/update-task): Any non-guest user who can
  read a card can now change only its column and position. A request that
  also sets any other field still requires the card's assignee, creator,
  reviewer, a channel administrator, or an organization administrator, and
  fails as a whole. A guest can move a card only when they already hold one
  of those roles on it.
