**Feature level ZF-bcb7f9**

* `PATCH /realm`, [`POST /register`](/api/register-queue),
  [`GET /events`](/api/get-events): Added `can_command_administrator_agents_group`
  realm setting, which is a [group-setting value](/api/group-setting-values)
  describing the set of users with permission to give tasks to administrator
  agents.
* `GET /agent/profiles`, `GET /agent/profiles/{profile_id}`: Added
  `command_allowed`, which is `false` when the profile is an administrator
  agent the reader may not command.
* Added a `manage` agent task kind for administrator agent tasks.
* Added `POST /api/v1/agent/runner/operations/execute`, which runs a
  `team.manage` operation and stores its server-written receipt. Operation
  data returned by the runner propose, consume, execute, and list endpoints
  gained `server_receipt`.
* Added `team_tools` to the runner capability report. A `manage` agent
  profile needs `tool_calling` and `team_tools` set to `passed` before it
  becomes ready.
