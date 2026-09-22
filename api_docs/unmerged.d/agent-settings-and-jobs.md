* New Grow Team agent APIs manage profiles, runners, providers, repositories,
  grants, team defaults, and channel attachments. User requests use standard
  authentication and a versioned `payload` form field. Resource lists return
  caller-specific projections and bounded pages.
* New Grow Team job APIs create and control jobs, recover send intents, show
  events and evidence, and decide approvals. Job detail includes selected
  attempt checks and separate operation and artifact cursors.
  Each artifact includes its attempt ID.
* New Grow Team runner APIs claim work, poll controls, report events and setup
  results, transfer bounded artifacts, and reconcile uncertain operations.
  Runner requests use a separate bearer credential. A runner can read and
  update its own metadata with a revision check.
* Authorized users can read a safe runner catalog summary.
  The summary includes adapter IDs, versions, auth states, and sandbox aliases.
  Only the owner can read the full catalog.
