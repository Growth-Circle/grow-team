# Grow Team P0/S0: keputusan runtime dan adapter

Tanggal: 2026-09-21. Penyelidikan ini read-only terhadap repository dan tidak memakai credential, produksi, atau model berbayar. Artifact sementara berada di `/tmp/grow-team-p0-research/`.

Status: **digantikan sebagian 2026-09-24.** [Keputusan SDK agent](agent-sdk-decision.md)
memindahkan job `answer` dan `manage` ke jalur cepat dengan `@anthropic-ai/sdk`.
Keputusan pada halaman ini tetap berlaku untuk jalur code. Lihat
[spesifikasi jalur cepat](spec/2026-09-24-agent-fast-lane.md).

**[jalur code]** **Rekomendasi: gunakan runtime endpoint TypeScript terbatas dengan tool broker milik Grow Runner. Gunakan SDK ACP 1.5.0 dan sertifikasi `@agentclientprotocol/codex-acp` 1.12.0 sebagai adapter awal.** Ini merupakan keputusan rekayasa berdasarkan source dan handshake nyata yang terbatas. Seluruh P0 belum lulus: browser integration, coding fixture, sandbox, provider nyata, dan recovery belum diuji pada laporan ini.

## Bukti yang sudah diperoleh

- Source Buzz commit `5079c770fe30bb3d8204822ce6c2431eacac6d4b` tersedia dalam object Git lokal `/home/ramaaditya/Project/rd-team`. Semua pembacaan Buzz memakai `git show` commit tersebut; HEAD rd-team berbeda (`c21049cac42e1d90978bd096cfa2490c802fd848`).
- Paket npm diperiksa pada registry dan dibandingkan dengan source tag resmi.
- SDK 1.5.0 dan adapter Codex 1.12.0 diinstal lokal di `/tmp`, memakai `--ignore-scripts`, tanpa instalasi global.
- Dependency Codex dipatok tepat `0.154.0`, bukan mengikuti range paket adapter. Zod dipatok `4.6.5`.
- Handshake real subprocess ACP berhasil dengan Node `v24.18.0`. Tidak ada prompt atau auth request. Child mendapat environment allowlist, HOME/CODEX_HOME sementara, dan NO_BROWSER=1.
- `initialize` mengembalikan protocol 1, agent version 1.12.0, `loadSession: true`, HTTP MCP true, SSE false, dan steering supported.
- Metode auth yang diumumkan pada lingkungan ini hanya `api-key`; itu bukan bukti login berhasil atau coding siap.
- stderr handshake kosong. Process group probe dihentikan; tidak ada proses adapter/Codex probe yang tertinggal saat pemeriksaan berikutnya.
- Host mempunyai `codex-cli 0.155.1` dan Claude Code `2.1.276`, tetapi probe menggunakan Codex 0.154.0 terpisah. Tidak ada adapter ACP global ditemukan saat pemeriksaan PATH.

Artifact bukti:

- `/tmp/grow-team-p0-research/handshake.mjs`: program probe SDK→adapter, tanpa prompt.
- `/tmp/grow-team-p0-research/handshake-result.json`: respons initialize dan versi persis.
- `/tmp/grow-team-p0-research/handshake-stderr.txt`: stderr kosong.
- `/tmp/grow-team-p0-research/package-lock.json`: versi dan integrity seluruh dependency probe.
- `buzz-crates__buzz-agent__*`: salinan source commit pinned untuk review.
- `src__AgentMode.ts`, `src__CodexJsonRpcConnection.ts`: source tag adapter untuk temuan policy.

## Perbandingan endpoint runtime

Sejarah 2026-09-21, sebelum keputusan SDK agent. Perbandingan ini menilai runtime
endpoint TypeScript yang sekarang menjadi jalur code. Jalur cepat (`answer`,
`manage`) tidak dibahas di sini; lihat
[keputusan SDK agent](agent-sdk-decision.md).

| Sumbu | Buzz agent subprocess pada commit pinned | Runtime TypeScript terbatas |
| --- | --- | --- |
| Loop provider | Sudah memiliki Chat Completions dan Responses, usage/error parsing, tool pairing, compaction, bounded outputs. | Perlu implementasi dua dialect, validation, retries, usage, budgets, compaction, dan tests. |
| Approval | Source pinned memiliki `PermissionBroker`: setiap LLM-issued MCP call meminta izin ACP sebelum eksekusi; deny/timeout/cancel fail closed. | Broker memeriksa scope/lease dan durable operation sebelum dispatch tool. Tidak ada gap protokol tambahan. |
| Durability | Histori/session in-memory; tidak ada `session/load`. Grow tetap perlu local journal, checkpoint, replay rules, dan adapter rehydration. | Journal menyimpan state tepat sebelum/setelah tool; dapat membuat recovery bagian dari loop. |
| Secret | Model key berada pada proses Rust; MCP env disaring, tetapi passthrough aktual mencakup SSH/Git/Buzz identity dan HOME. | Supervisor memegang key; sandbox tools tidak menerima secret environment. Boundary tetap perlu tes OS. |
| Endpoint policy | HTTP client yang diperiksa tidak memasang policy DNS/IP/redirect khusus Grow. Perlu proxy/broker egress atau patch Rust. | Transport milik runner dapat menerapkan allowlist, per-connect DNS, credential origin, time/size bounds secara langsung. |
| Streaming | Non-streaming HTTP. Agent emits ACP message chunks sesudah response; bukan streaming token provider. | Dapat memenuhi capability stream per provider, dengan buffering argumen dan eksekusi hanya setelah complete frame. |
| Tools | Stdio MCP; custom Grow MCP broker bisa mengganti buzz-dev-mcp. | Tool broker dapat dipanggil langsung; MCP hanya untuk integrasi yang perlu. |
| Packaging | Binary Rust tambahan; compile dari workspace pinned dan supply-chain provenance. No binary di target lokal yang diperiksa. | Satu stack Node dengan ACP package dan runner. Perlu pin Node/dependency dan lockfile. |
| Cakupan dependency | buzz-agent sendiri tidak bergantung pada relay; tetap membawa provider/OAuth/catalog yang tak dibutuhkan awal. buzz-dev-mcp tergantung Buzz CLI/Git/Nostr. | Batasi dependency ke SDK ACP + validator + library transport yang diperlukan. |
| Risiko | Runtime matang, tetapi kontrol Grow masih harus ditambahkan pada beberapa seam. Jangan menganggap README sebagai security proof. | Lebih banyak kode loop baru; conformance fixtures dan fault injection wajib sebelum code_ready. |

Rekomendasi TS berasal dari kesesuaian kebutuhan durable operation journal, policy jaringan pemilik, streaming capability, dan pemisahan secret/tool. Ini **bukan benchmark** bahwa TS lebih cepat atau lebih aman. Hindari dua runtime default. Pertahankan Buzz sebagai referensi perilaku dan regression cases; jangan fork seluruh harness atau membawa `buzz-dev-mcp` tanpa alasan.

Alternatif Buzz tetap layak jika runtime baru terlalu mahal: jalankan `buzz-agent` langsung melalui ACP milik Grow, MCP hanya broker Grow, environment kosong/allowlist, fixed API mode, max rounds=40, max sessions=1, external network proxy, dan checkpoint Grow. Jangan memakai `buzz-acp` relay harness sebagai pasangan client: source harness yang diperiksa masih memiliki jalur auto-approve `allow_once`.

Sumber primer pinned:

- [Buzz agent manifest](https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/Cargo.toml)
- [Permission broker](https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/src/permission.rs)
- [MCP environment/process control](https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/src/mcp.rs)
- [Provider HTTP client](https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/src/llm.rs)
- [Runtime capability declarations](https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/src/lib.rs)
- [Buzz ACP permission path](https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/acp.rs)
- [Dev MCP dependencies](https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-dev-mcp/Cargo.toml)

## Pin package dan lisensi

| Package/runtime | Pin rekomendasi untuk conformance awal | Dasar |
| --- | --- | --- |
| Node | `24.18.0` | Versi runtime probe berhasil; jangan mengklaim semua Node 24 tanpa suite. |
| `@agentclientprotocol/sdk` | `1.5.0` | Registry dan package.json tag sama; Apache-2.0. |
| `@agentclientprotocol/codex-acp` | `1.12.0` | Official release 2026-09-15; Apache-2.0. |
| `@openai/codex` | `0.154.0` | Basis update adapter 1.12.0; pin dependency override/lock agar `^0.154.0` tidak menggeser test target. Apache-2.0. |
| `zod` | `4.6.5` | SDK accepts 3.25/4.x; installed conformance pin 4.6.5. MIT. |
| Claude alternative | `@agentclientprotocol/claude-agent-acp@0.79.0` | Registry current; Node >=22, SDK exact 1.4.0, Claude Agent SDK exact 0.3.274. Belum diprobe; bukan supported release. |
| `@anthropic-ai/sdk` **[jalur cepat]** | `0.128.0` (versi dikunci persis di `package-lock.json`; nilai pasti ditetapkan saat implementasi) | Dipilih pada [keputusan SDK agent](agent-sdk-decision.md) untuk loop model jalur cepat. |

Integrity utama dari registry:

```text
@agentclientprotocol/sdk@1.5.0
sha512-524jwbB2iYWA+kWWyv9fhKbhU89dH/lu9u5EXwVNmfYzopV8BujCDxByBDZhxRUkB7RWIJzISCnwivdDk+bdVg==
@agentclientprotocol/codex-acp@1.12.0
sha512-au6YcgvZmoUMuFrJlSYfJrHEB9SW4YHwUUS8fchYBIY2uwq/lJXwebgP4di9ANJulmr+mv2FE0CraY1agi5YYg==
@openai/codex@0.154.0
sha512-FV/x1OHXYv/ifjf3mXj9ThTTAWcUZN6cGIRQRhRxkKNOPuImu1WW0c8ev1vUkE9XGH90dEnYG1tBjIkxRikg0w==
zod@4.6.5
sha512-v5l/aFXZQeai4awLbOpSoHecE9UiMrnfx75tEXLjNonXVARxQ5mOeipTjROUchszUNCqnE+hqAMujRsRHsut2Q==
```

Sumber: [SDK tag package](https://github.com/agentclientprotocol/typescript-sdk/blob/v1.5.0/package.json), [SDK registry](https://registry.npmjs.org/@agentclientprotocol%2fsdk/1.5.0), [adapter release](https://github.com/agentclientprotocol/codex-acp/releases/tag/v1.12.0), [adapter manifest](https://github.com/agentclientprotocol/codex-acp/blob/v1.12.0/package.json), [adapter registry](https://registry.npmjs.org/@agentclientprotocol%2fcodex-acp/1.12.0), [Claude adapter registry](https://registry.npmjs.org/@agentclientprotocol%2fclaude-agent-acp/0.79.0).

## SDK API yang benar untuk implementasi baru

Gunakan API app SDK v1, bukan experimental v2 atau transport HTTP. ACP tetap stdio lokal. `ClientSideConnection` masih tersedia sebagai compatibility wrapper yang deprecated.

```ts
const stream = acp.ndJsonStream(
    Writable.toWeb(child.stdin),
    Readable.toWeb(child.stdout),
);
const connection = acp.client({name: "grow-runner", version: RUNNER_VERSION})
    .onRequest("session/request_permission", handlePermission)
    .onNotification("session/update", handleUpdate)
    .connect(stream);
const initialized = await connection.agent.request("initialize", {
    protocolVersion: acp.PROTOCOL_VERSION,
    clientCapabilities: {},
});
const session = await connection.agent.request("session/new", {
    cwd: attemptWorkspace,
    mcpServers: [],
});
await connection.agent.request("session/prompt", {
    sessionId: session.sessionId,
    prompt: [{type: "text", text: task}],
});
await connection.agent.notify("session/cancel", {sessionId: session.sessionId});
connection.close();
await connection.closed;
```

Contoh session/prompt di atas adalah kontrak implementasi, **belum dijalankan pada adapter nyata dalam riset ini**. Handshake di `/tmp` hanya mengirim initialize. Capabilities `fs`/terminal jangan diiklankan sebelum handlers benar-benar tersedia. Permission handler harus memilih ID dari opsi adapter, dengan scope aktual; cancel/deny boleh mengembalikan `{outcome:{outcome:"cancelled"}}`. Jangan forward `agent_thought_chunk` ke panel/audit.

Sumber: [SDK migration guide pinned](https://github.com/agentclientprotocol/typescript-sdk/blob/v1.5.0/MIGRATION_0.26_0.27.md).

## Temuan kritis adapter Codex

Mode ID `read-only` dalam source adapter 1.12.0 **bukan sandbox read-only**. Ia menggunakan `workspaceWrite`, approval `on-request`, reviewer `user`, network false. Mode default `agent` menggunakan `auto_review`. Mode `agent-full-access` memakai `dangerFullAccess` dan approval `never`.

Implikasi:

1. Jangan memakai label mode sebagai bukti izin Grow.
2. Jangan menawarkan full-access kepada pengguna Grow.
3. Lock konfigurasi adapter ke policy Grow; jangan terima mode bebas dari prompt.
4. P2 read-only membutuhkan outer mount/broker read-only, walaupun adapter mode bernama read-only.
5. P3 coding hanya enabled setelah sandbox/kebocoran/process-tree probe lulus.
6. Adapter menurunkan environment ke Codex App Server. Buat environment allowlist, isolated config root, dan vendor credential path yang spesifik.
7. Jangan mount seluruh direktori `~/.codex` host: config/MCP/skills/auth milik pengguna dapat memperluas context/tool/credential.
8. Session load/steering advertised bukan lulus conformance; uji resume dan permission saat reconnect.
9. Disable subagent execution awal, termasuk extension native, agar invariant satu executor tetap benar.
10. Background task capability membutuhkan supervisor yang mencatat dan menghentikan task lama sebelum attempt terminal.

Sumber: [AgentMode.ts pinned](https://github.com/agentclientprotocol/codex-acp/blob/v1.12.0/src/AgentMode.ts), [App Server process env](https://github.com/agentclientprotocol/codex-acp/blob/v1.12.0/src/CodexJsonRpcConnection.ts), [README adapter](https://github.com/agentclientprotocol/codex-acp/blob/v1.12.0/README.md).

## Boundary yang tetap wajib pada dua mode (sejarah; sekarang jalur code)

- Supervisor luar sandbox memiliki credential runner/provider dan mengelola lease heartbeat. Tool sandbox tidak memiliki keduanya.
- **[jalur code]** Rootless container per attempt, non-root, drop capabilities, no host socket/home/SSH, limits CPU/RAM/PID/output/time, mount checkout terisolasi.
- Seluruh tools native ACP berada dalam containment setara bila tidak dapat diarahkan lewat broker.
- Model egress menggunakan transport/broker terpisah; shell tidak mendapat network atau write credential Git dari akses model.
- Egress proxy memvalidasi DNS/IP pada koneksi dan menolak metadata; public TLS diverifikasi; private endpoint owner allowlist explicit.
- Lease deadline berlaku lokal walau server/control connection mati; stop entire container/process tree, bukan hanya parent PID.
- Durable operation state sebelum mutation; operation ID/hash terikat attempt/epoch/policy; outcome unknown tidak dieksekusi ulang otomatis.
- Approval per remote effect melalui backend ledger + CAS, di luar model/native mode.
- Secret redaction di error/header/stdout/event/artifact; test synthetic canary dari shell env/filesystem dan model error echo.
- **[jalur code]** Final diff/tree/check artifacts dari actual workspace/broker, bukan klaim native model.

## Gate P0/S0 yang masih tersisa

- [ ] Runner package/browser/control-plane integration dengan satu fixture.
- [ ] Real installed ACP session/prompt/progress/permission/cancel dan coding di sandbox.
- [ ] Satu endpoint nyata yang disetujui, dengan biaya dibatasi, probe dan fixture coding; laporan ini tidak memakai credential.
- [ ] Fake HTTP conformance dua mode: Chat Completions/Responses, fragmented streams, malformed schema, retries, secret echoes, context recovery. Dipensiunkan untuk jalur cepat 2026-09-24: jalur cepat memakai `anthropic_messages` lewat `@anthropic-ai/sdk`, bukan codec dua mode ini. Gerbang ini tetap berlaku untuk jalur code.
- [ ] Budget/error/restore journal tests pada runtime TS pilihan.
- [ ] Sandbox process-grandchild, no-new-tools-after-disconnect, owner-WIP, symlink/Git metadata escape, secret filesystem/env tests.
- [ ] Browser-only evidence dengan tab close/reopen; tidak cukup dari ACP handshake.
- [ ] Pin runner image digest dan package lock ke hasil conformance yang benar-benar dirilis.

P0 comparison saat ini berupa source comparison plus adapter handshake. Tidak ada benchmark kedua runtime, build Buzz pinned, atau klaim provider/coding lulus. Semua kode probe berada di `/tmp`; repository Grow Team tetap tidak diubah oleh pekerjaan ini.


## Base Linux untuk paket native

Base implementasi adalah `node:24.18.0-trixie-slim` pada digest
`sha256:ae91dcc111a68c9d2d81ff2a17bda61be126426176fde6fe7d08ab13b7f50573`.
Probe lokal mengonfirmasi Node dan Zsh dari payload yang dipatok dapat berjalan.
Bookworm awal gagal memenuhi kebutuhan `GLIBC_2.38` dari binary Zsh.
Rincian batas dan bukti terdapat pada [desain sandbox](agent-sandbox-design.md).
Sertifikasi runtime penuh tetap merupakan gate terpisah.
