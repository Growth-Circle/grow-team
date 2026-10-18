**Feature level ZF-3ee9ff**

* [`GET /routines`](/api/list-routines),
  [`POST /routines`](/api/create-routine),
  [`GET /routines/templates`](/api/list-routine-templates),
  [`GET /routines/agents`](/api/list-routine-agents),
  [`GET /routines/{routine_id}`](/api/get-routine),
  [`PATCH /routines/{routine_id}`](/api/update-routine),
  [`DELETE /routines/{routine_id}`](/api/delete-routine),
  [`POST /routines/{routine_id}/pause`](/api/pause-routine),
  [`POST /routines/{routine_id}/resume`](/api/resume-routine),
  [`POST /routines/{routine_id}/run`](/api/run-routine),
  [`GET /routines/{routine_id}/runs`](/api/list-routine-runs): New
  endpoints for scheduled routines, work that an agent repeats on a
  schedule and posts under its own name. A guest cannot use them. A
  routine that runs outside Sanji is listed with `executor` set to
  `external` and cannot be changed through the API.
* [`GET /events`](/api/get-events): New `routine` events report routines
  that are added, changed, paused, resumed, run, or deleted. They reach
  the active members who can read the routine's channel, and never a
  guest.
