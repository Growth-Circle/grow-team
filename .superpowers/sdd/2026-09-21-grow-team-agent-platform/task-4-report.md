# Task 4 report

Base: `4031aab26659beb11d6cbb1023b4c8fb146583f6`.

## Scope

Added renderer-backed personal mention provenance and message-transaction admission.
Admission creates durable receipts, jobs, and outbox records through Task 3 lifecycle APIs.
It excludes bot authors, silent mentions, code, blockquotes, groups, and wildcards.
It supports one-human and one-agent implicit direct messages.
Group direct messages require a personal mention.

Added optional sender-scoped `agent_send_key` handling with canonical payload digest conflicts and replay.
Added advisory preflight, message receipt, and send-intent routes.
Follow-up remains limited to the existing exact job-ID input route.
Coding automatic mentions queue when one approved default ref is available.
They create `draft` jobs with `needs_input` receipts and no wake outbox when choices are incomplete.

## Red and green evidence

Red command:

```text
tools/grow-team/test-environment/run.sh tools/test-backend --parallel=1 zerver.tests.test_agent_message_admission
```

Initial result: one failure. A personal agent mention created zero jobs.

Green command:

```text
tools/grow-team/test-environment/run.sh tools/test-backend --parallel=1 zerver.tests.test_agent_message_admission zerver.tests.test_message_send.StreamMessagesTest.test_not_too_many_queries zerver.tests.test_message_send.PersonalMessageSendTest.test_personal_message zerver.tests.test_message_send.PersonalMessageSendTest.test_direct_message_permission_group_setting zerver.tests.test_message_send.PersonalMessageSendTest.test_direct_message_initiator_group_setting
```

Result: 7 tests passed.
The tests cover personal provenance, silent/code/blockquote exclusions, one-human/one-agent implicit direct-message admission, job/outbox creation, same-key replay, changed-payload conflict, and legacy query counts.

Static command:

```text
tools/grow-team/test-environment/run.sh .venv/bin/python -m mypy zerver/actions/agent_dispatch.py zerver/actions/agent_jobs.py zerver/actions/message_send.py zerver/lib/agent_context.py zerver/lib/markdown/__init__.py zerver/lib/message.py zerver/lib/agent_job_requests.py zerver/views/agent_jobs.py zerver/views/message_send.py zerver/tests/test_agent_message_admission.py
```

Result: success with no issues in 10 source files.
Ruff passed. `git diff --check` passed.

## Self-review

The dispatcher reads only the renderer's pre-group personal IDs.
It deduplicates profile targets and preserves a rejected receipt after lifecycle admission rolls back.
The message transaction reserves send keys before message insertion, updates the intent with the stored ID, and replays that ID after a lost response.
No dispatch path calls a model, runner, provider, or network service.
The nested guard retains its stricter database timeout settings to the outer chat commit.

## Limitations

Controller review found remaining gaps after this checkpoint: deleted send-intent tombstones, mixed replay/new batch result ordering, receipt job-resource rechecks, and their regressions require completion before freeze.

`zerver.tests.test_markdown.MarkdownFixtureTest.test_markdown_fixtures` fails on this checkout.
The exact failures are `normal_quote_message`, `user_mention_with_no_message_link`, and `user_mention_with_no_narrow_link`.
Each expects a rendered silent mention span and receives raw `@_**Zoe|7**` markup.
The Task 4 provenance diff only adds a separate set and does not modify rendered HTML.
The test and renderer paths for those fixtures are unchanged from reviewed base except for that additive set.
Black `--check` reports formatting differences in existing large upstream files with the available formatter version.
Ruff is clean for the Task 4 paths.
