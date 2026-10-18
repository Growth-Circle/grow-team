# Plan V1: Izin per tool & persetujuan ("Off / Auto / Ask first")

Status: **DRAF untuk review Rama. Belum ada kode, commit, atau perubahan server.** Induk: `2026-10-18-viktor-adoption.md` fase V1. Dasar kode: `feature/team-mcp` `a3abb3cabb` (file yang dikutip identik di `eed41c862e`). Migrasi berikutnya bernomor `0845`.

## Bukti: apa yang sudah ada
Ada **dua permukaan MCP berbeda**. Keduanya belum memakai kebijakan per tool.
- **Keluar (agen memakai server luar).** Tabel sudah ada: `McpServer`, `McpConnection`, `McpToolPolicy` (`allow/approve/block`, default `block`), `McpAgentGrant`, `McpJobPlan` (`zerver/models/mcp.py:24-188`; docstring `:4` "WP44 builds the gateway"). Tapi **gateway belum ada**: tidak ada view untuk membuat koneksi, tidak ada penegak; satu-satunya pemakaian model di kode produksi hanyalah chip `McpAgentGrant` (`zerver/views/agents.py:125`), selebihnya cuma test (`test_workspace_models.py:428-461`). Runner mengirim `mcpServers: []` ke sesi ACP (`services/grow-agent-runner/src/acp-runtime.ts:227`). Spec 16 (`internals/docs/pages/16-koneksi-mcp.md:45-54`) sudah mendefinisikan aturan `boleh_pakai` dan kartu Rencana; spec 13.3 (`spec/2026-09-22-...skills-and-mcp.md:1063-1066`) minta mutasi MCP lewat **ledger approval yang ada** (`McpCallArguments`), bukan modal baru.
- **Masuk (klien luar memakai MCP Sanji).** `zerver/views/mcp_server.py`: satu-satunya gerbang tulis adalah scope `team:write` (`:137-139`). Tidak ada kebijakan per tool. `tool_list` memberi `destructiveHint: False` untuk semua tool (`zerver/lib/mcp_tools.py:223`), padahal `delete_task` dan `edit_message` merusak. Rate limit hanya umum `api_by_user` 200/menit (`mcp_server.py:71`; `default_settings.py:296`). Audit `MCPAccessAudit` hanya `grant, tool_name, outcome, created_at` (`zerver/models/mcp_access.py:51-55`), tanpa argumen dan tanpa layar baca. UI `web/src/mcp_ui.ts` hanya token/revoke (`/json/mcp/access`), tidak ada daftar tool. Ini temuan H1/M4 audit (`2026-10-15-sanji-system-audit-plan.md:17,74-77`: AC-SEC-001..003 diserap V1).
- **Approval agen.** `AgentApproval` terikat `AgentOperation` (`models/agents.py:760`). Kelas operasi yang butuh approval hanya `git.push, git.draft_pr, dependencies.install, shell.run, team.manage` (`actions/agent_approvals.py:33`); `OperationArguments` tidak punya `mcp.call` (`agent_protocol.py:891,930`). Item Needs untuk approval hanya menampilkan `job.request[:80]` dan komentar "Every approval gets the same action today" (`zerver/lib/needs.py:267-274`): **tidak ada preview tindakan yang tepat**. Hanya requester yang boleh memutuskan `team.manage` (`agent_approvals.py:246`, `needs.py:528`).
- **Peringatan perilaku.** Rutinitas Rama (Cek pagi dll.) memakai grant OAuth `team:write` lewat tool tulis ini (lihat plan V2). Jika kebijakan baru langsung dipaksakan, rutinitas itu berhenti menunggu approval.

## Gap
(1) Tidak ada UI/API kebijakan per tool. (2) Server masuk tidak punya Auto/Ask/Off, hint salah, rate limit tulis, audit argumen. (3) Tidak ada jalur "Ask first" untuk klien luar (tidak ada job) maupun untuk tool MCP keluar. (4) Needs tidak punya preview eksak. (5) Gateway keluar belum ada (milik Rama).

## Pendekatan
Pemetaan label: `allow` = **Auto**, `approve` = **Ask first**, `block` = **Off**. Satu evaluator murni `zerver/lib/mcp_policy.py::evaluate(...) -> allow|ask|deny` dipakai dua penegak: server masuk (sekarang) dan gateway keluar (saat WP44 mendarat). Fitur dikirim **gelap** di belakang `AgentRealmSettings.mcp_policy_enforced` (bool, `db_default=False`); grant lama berstatus `legacy` dan tetap seperti hari ini sampai Rama mengubahnya. Test hanya di WSL, tidak di server-gteam (memori `sanji-dev-env`).

### U1. Data & evaluator
**Files:** `zerver/models/mcp.py`, `zerver/models/mcp_access.py`, `zerver/models/agents.py` (flag), migrasi `0845_mcp_policy_v1.py`, `zerver/lib/mcp_policy.py`, `zerver/tests/test_mcp_policy.py`.
- Tabel baru `MCPAccessToolPolicy(grant, tool_name, policy)` meniru `McpToolPolicy` (unique grant+tool, check policy valid).
- `MCPAccessGrant.policy_mode` (`default="managed", db_default="legacy"`): baris lama `legacy` (baca Auto, tulis Auto bila `team:write`), grant baru `managed`.
- `McpConnection.policy_locked` (bool, `db_default=False`); `McpToolPolicy.updated_by` (FK null, `db_constraint=False`).
- Default `managed` (usulan, lihat Q4): baca Auto; `add_reaction/remove_reaction/create_task/update_task/upload_file` Auto; `send_message/send_direct_message/edit_message/delete_task` Ask first.
- **AC-V1-001 (State):** While a grant is `managed`, the evaluator shall return the stored policy for a tool, else the class default.
- **AC-V1-002 (State):** While a grant is `legacy`, the evaluator shall allow exactly the tools its scopes allowed before this change.
- **AC-V1-003 (Unwanted):** If the tool name is unknown, or a connection is `needs_review` and the tool `is_new`, then the evaluator shall deny.
- Test: `test_mcp_policy` (matriks scope x policy x mode).

### U2. Penegakan di server MCP masuk
**Files:** `zerver/views/mcp_server.py`, `zerver/lib/mcp_tools.py`, `zproject/default_settings.py` (+ `zerver/lib/rate_limiter.py` kelas per grant), test di `zerver/tests/test_mcp_access.py`.
Urutan di `tools/call`: scope, kebijakan, rate limit, validasi, ask atau eksekusi.
- **AC-V1-004 (Unwanted):** If a tool's policy is Off, then `tools/list` shall omit it, and `tools/call` shall return the same error as an unknown tool and write an audit row `blocked`.
- **AC-V1-005 (Event):** When `tools/list` is served, the system shall advertise `destructiveHint: true` for `delete_task` and `edit_message`, and `idempotentHint` per tool rather than `not write`.
- **AC-V1-006 (Unwanted):** If a grant makes more than 20 write calls per minute or 300 per hour, then the system shall reject the call with a clear error and audit `rate_limited` (Q8).
- **AC-V1-007 (Event):** When the policy is Auto, the system shall execute the tool as today and audit `success` or `rejected` with an argument digest.
- Test: tambahan `test_mcp_access` (list, call Off, hint, limit; regresi 26 test lama).

### U3. "Ask first" menjadi approval Needs dengan preview eksak
**Files:** model baru `McpPendingCall` (grant, user, tool_name, `arguments`, `argument_digest`, `preview`, status `pending/approved/rejected/expired/executed`, `expires_at`, `nonce`, `decided_at`, unique digest aktif), `zerver/lib/mcp_preview.py`, `zerver/views/mcp_access.py` (`POST /json/mcp/calls/<id>/decision`), `zerver/lib/needs.py` (item `kind: "approval"`, id `mcp_call:<id>`; `KINDS` tidak bertambah), `web/src/needs_ui.ts`, `needs_data.ts`, test `test_needs.py` + `test_mcp_access.py`.
Alur: `tools/call` dengan kebijakan Ask membuat `McpPendingCall` dan menjawab `isError` "Menunggu persetujuan di Sanji" (JSON-RPC stateless, tidak bisa menunggu). Pemilik grant menyetujui di Perlu kamu; **tindakan dijalankan saat Approve** (di request pemilik, sebagai dirinya), hasil disimpan; retry klien dengan argumen sama menerima resi (Q2). Preview dibuat dari argumen tervalidasi: tool, target dengan nama (channel/topik, nama penerima DM, judul kartu), isi penuh (Needs memotong 600 karakter dengan "Lihat semua"), mention terurai menjadi nama, digest 12 karakter.
- **AC-V1-008 (Event):** When a call to an Ask-first tool arrives, the system shall create one pending approval per distinct argument digest and shall not execute the tool.
- **AC-V1-009 (Event):** When the grant owner approves, the system shall execute exactly the stored arguments once and record the receipt.
- **AC-V1-010 (Unwanted):** If the stored digest does not match the recomputed digest, the approval has expired (TTL `approval_ttl_minutes`), or a user other than the grant owner decides, then the system shall not execute and shall return 403 or 409.
- **AC-V1-011 (Unwanted):** If the same call is retried while pending or after execution, then the system shall return the pending or executed receipt and shall not create a second execution.
- **AC-V1-012 (State):** While an approval is pending, the Needs item shall show the exact tool, target, full content and expiry, and a Reject action.

### U4. UI izin per tool, kunci, cakupan akses
**Files:** `web/src/mcp_ui.ts`, `web/templates/mcp.hbs`, CSS, `web/tests/mcp_ui.test.cjs`, string i18n id; endpoint `GET/PUT /json/mcp/access/<id>/tools`.
Tiap koneksi punya tab Tools, Akses, Pengaturan (rujukan Viktor `screenshots/08b,09,10`). Baris tool: nama, deskripsi **teks biasa** (tanpa Markdown mentah, UX-02), pilihan 3 tingkat dengan label penuh yang tidak terpotong (UX-20), hint "membaca/menulis/merusak". Aksi massal: "Semua baca = Auto". Akses: Anggota workspace / Hanya saya (= `scope` workspace/personal; ruang tetap lewat pemilik ruang). Kunci: hanya admin yang mengubah saat terkunci. Satu tombol "Putuskan" (UX-23).
- **AC-V1-013 (Event):** When an owner or admin changes a tool's policy, the system shall persist it, audit it with the actor, and show it without reload.
- **AC-V1-014 (Unwanted):** If a connection is locked and the actor is not an admin, then the change shall be rejected and the control disabled with an explanation.
- **AC-V1-015 (State):** While viewed at 390px, light or dark, the tool list shall fit without clipped labels and be keyboard operable.

### U5. Siap-gateway untuk koneksi keluar (belum aktif)
**Files:** `zerver/lib/agent_protocol.py` (`McpCallArguments`), `zerver/actions/agent_approvals.py` (`mcp.call` di `APPROVAL_ACTIONS`), `zerver/views/mcp_connections.py` (PUT policy/agents/review per spec 16), `needs.py` preview untuk `mcp.call`, `services/grow-agent-runner/protocol/` + `conformance-digests.json`. **Seluruh unit ini butuh keputusan Rama (Q1, Q3).**
- **AC-V1-016 (Event):** When an agent proposes `mcp.call` for an Ask-first tool, the system shall create an `AgentApproval` whose preview shows connection, tool and validated arguments, and consume it once.
- **AC-V1-017 (Unwanted):** If the tool is Off, not granted to the agent, or answer-mode and not read-only, then the proposal shall be rejected and `AgentAuditEvent` `mcp.blocked` recorded.
- **AC-V1-018 (Event):** When a "Rencana" (`McpJobPlan`) is approved, the listed tools shall run Auto up to their call limits and no others.

### U6. Audit feed
**Files:** `MCPAccessAudit` (+`argument_digest`, `detail` JSON, `db_default`), `zerver/views/mcp_access.py` (`GET /json/mcp/audit`), `web/src/mcp_ui.ts` ("Aktivitas"), `agent_protocol.parse_authority_event` (tipe baru).
- **AC-V1-019 (Event):** When any MCP call is blocked, asked, approved, rejected, executed or rate-limited, the system shall add an audit entry with tool name, outcome, digest and actor, never the content or token.
- **AC-V1-020 (State):** While viewing Aktivitas, a member shall see only their own grants and an admin all of them, with names, not raw ids.

## Migrasi, rollback, test
Semua additive dengan `db_default`; `0845` tanpa data-migration berisiko. **Rollback:** matikan `mcp_policy_enforced` (tanpa deploy), lalu revert kode bila perlu; tabel tetap, tidak dipakai image lama. Gate rilis: `./tools/test-backend zerver.tests.test_mcp_policy zerver.tests.test_mcp_access zerver.tests.test_needs zerver.tests.test_agents zerver.tests.test_workspace_models zerver.tests.test_openapi`, `./tools/test-js-with-node` penuh, `./tools/lint` file berubah, mypy bersih, cek browser (terang/gelap, 1280 dan 390, keyboard), lalu deploy bergerbang dan smoke baca-saja.

## Risiko (HIGH, auth)
Bypass lewat jalur selain `tools/call` (mis. `fetch`, `upload_file`); TOCTOU antara preview dan eksekusi (ditutup digest ulang di dalam transaksi `select_for_update`); eksekusi oleh orang yang salah; grant lama jadi macet (ditutup mode `legacy`); approval kedaluwarsa menumpuk di Needs; perubahan protokol runner (U5) memutus kompatibilitas conformance.

## Pertanyaan untuk Rama (rekomendasi di depan)
1. Cakupan: **A (rek.)** V1 = U1-U4+U6 (server masuk, bisa dibangun sekarang) dan U5 menunggu WP44 · B semuanya sekaligus.
2. Eksekusi setelah Approve untuk klien luar: **A (rek.)** langsung saat Approve, retry menerima resi · B saat klien retry.
3. Tool MCP keluar: **A (rek.)** `mcp.call` di ledger approval yang ada (spec 13.3), protokol dinaikkan · B tabel approval terpisah.
4. Default grant baru: **A (rek.)** baca dan reaksi/kartu/upload Auto, send/DM/edit/delete Ask · B semua tulis Ask · C delete Off (spec 16).
5. Needs: **A (rek.)** pakai `kind: "approval"` dengan id `mcp_call:` · B kind baru.
6. Siapa memutuskan: **A (rek.)** hanya pemilik grant · B pemilik atau admin.
7. Kunci: **A (rek.)** hanya koneksi keluar; grant masuk cukup revoke admin · B keduanya.
8. Rate limit tulis: **A (rek.)** 20/menit, 300/jam per grant · B lain.
9. Rutinitas Rama sekarang: **A (rek.)** tetap `legacy` Auto sampai V2 memindahkannya · B langsung Ask first.
