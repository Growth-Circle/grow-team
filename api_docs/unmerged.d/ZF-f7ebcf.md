**Feature level ZF-f7ebcf**

* Added `POST /agent/jobs/{job_id}/deliver-privately`, which sends a held
  task result to the requester as a direct message when the conversation's
  audience changed after the task finished.
* Added `POST /agent/profiles/{profile_id}/test-task`, which sends a bot
  direct message to the caller and creates a one-off answer task from it.
* `POST /agent/jobs` gains optional `follows_job_id`, which links a new
  task to the ended task it follows.
* `GET /agent/jobs/{job_id}`: job detail gains `repository`, `base_ref`,
  `budget`, `instructions`, and `follows_job_id`. `allowed_actions` gains
  `deliver_privately` and `follow_up`. `job_kind` documents the existing
  `manage` value. `reason_code` gains `audience_changed`.
* Dispatch receipts report `admission_denied` and `configuration_needed`
  as stable reason codes instead of English sentences.
