**Feature level ZF-7f01ff**

* [`POST /tasks`](/api/create-task),
  [`PATCH /tasks/{task_id}`](/api/update-task): Added the `priority` field
  (0 none, 1 low, 2 medium, 3 high). A value outside that range fails with
  a 400 error and changes nothing. Changing it needs the same permission as
  editing the card's title.
  [`GET /tasks`](/api/get-task-board) reports it for each card.
