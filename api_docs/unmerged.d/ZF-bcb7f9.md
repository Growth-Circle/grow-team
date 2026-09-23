**Feature level ZF-bcb7f9**

* `PATCH /realm`, [`POST /register`](/api/register-queue),
  [`GET /events`](/api/get-events): Added `can_command_administrator_agents_group`
  realm setting, which is a [group-setting value](/api/group-setting-values)
  describing the set of users with permission to give tasks to administrator
  agents.
