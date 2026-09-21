# Grow Runner: rancangan containment Linux

Audit 2026-09-21, diperbarui 2026-09-22. Tidak memakai credential nyata, menghubungi model berbayar, atau mengubah produksi.

## Keputusan konkret

Gunakan **rootless Docker dengan network namespace `none` dan broker Grow untuk seluruh operasi project**. Adapter ACP 1.12.0 dan Codex 0.154.0 berada dalam container model tanpa mount repository. SDK 1.5.0 dan supervisor Grow berada di luar container. Operasi project berjalan dalam container tool terpisah. Provider/device/Git credential hanya berada pada supervisor/broker luar.

Loop native tetap memakai Codex. Patch adapter mematikan environment native dengan `environments: []` dan menghubungkan dynamic tools ke broker Grow. Probe ACP sintetis telah membuktikan call/result serta penolakan tool native pada dua turn. Integrasi broker produksi, izin, recovery, dan coding nyata masih menjadi gate sebelum adapter dapat dinyatakan siap.

Alur:

```text
Grow control plane <-- TLS + device identity --> supervisor + lease watchdog
                                                   |
                           typed ACP stdio --------+------> codex-acp -> Codex
                                                   |        [container model]
provider HTTPS <-- credential injection <-- model broker <-- Unix socket <-- HTTP loopback relay
                                                   |
                                  typed tool broker --------> [container tool]
                                                   |
                                  external Git broker (separate approval ledger)
```

Container tidak mendapat akses ke Docker socket, host network, host home, SSH agent, credential files, atau API context/Titen/Git umum. Satu Unix socket dipasang sebagai file, bukan direktori host. Relay hanya mengubah transport HTTP loopback ke socket tersebut. Relay tidak menerima URL upstream, tidak memegang provider key, dan bukan HTTP CONNECT/SOCKS proxy.

## Konfigurasi containment

- Image pin digest berisi Node 24.18.0, lockfile ACP/Codex/Zod, relay kecil, dan toolchain profil repository yang disetujui.
- Container non-root UID/GID tetap; `--network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges`.
- PID namespace, IPC namespace, dan cgroup namespace privat; seccomp default tetap aktif. Jangan menambahkan privileged, host PID, atau CAP_SYS_ADMIN untuk membuat Codex bekerja.
- Wajib resource limit CPU, RAM, PID, ukuran output, temporary disk, dan waktu. Profil awal contoh: 2 CPU, 2 GiB RAM, 256 PID; sesuaikan kebutuhan tes sebelum grant.
- Container model memakai working directory netral serta tmpfs terbatas untuk home dan state native. Repository tidak dipasang di container ini.
- Container tool hanya memperoleh checkout disposable `/workspace` dan tmpfs yang dibatasi. Untuk baca dan pemeriksaan tanpa izin edit, `/workspace` dipasang read-only. Output pemeriksaan memakai direktori writable yang disetujui.
- Checkout harus copy/clone mandiri dengan `.git` mandiri yang sudah dibersihkan. Jangan mount worktree pengguna beserta common Git directory. Origin harus bebas credential; tidak ada helper, hooks, filters, atau git config host.
- Siapkan dependency secara offline di image atau cache read-only. Dependency install jaringan menjadi operasi broker/profil terpisah; tidak mengubah network namespace saat runtime.
- Environment allowlist: PATH, HOME container, CODEX_HOME container, LANG, NO_BROWSER, konfigurasi non-secret. Jangan mewarisi process.env supervisor.
- ACP client tidak mengiklankan host fs/terminal. Session/new hanya menerima working directory netral; mode/config/provider changes dari UI/model ditolak di supervisor.
- Kirim `environments: []` pada thread/start dan setiap turn/start. Daftarkan hanya dynamic tools yang sesuai izin attempt. Jangan memakai preset native sebagai pengganti pemeriksaan izin Grow.
- Matikan subagent, MCP eksternal, plugin, browser, hosted search, code mode, review/fork/goal, serta extension yang tidak disertifikasi. Periksa katalog tool pada setiap request model terhadap allowlist.
- Jangan memuat konfigurasi, hook, skill, plugin, atau MCP dari repository ke proses native. Broker menyampaikan aturan project sebagai data sesuai scope; aturan itu tidak dapat memperluas izin.

## Broker model dan autentikasi lokal

Setiap attempt mendapat Unix socket baru di direktori host mode 0700. Hanya file socket itu dipasang ke container. Kebijakan broker diikat ke socket saat dibuat: runner/job/attempt/epoch, config version, approved origin/base path, model allowlist, data scope, budget, dan expiry. Payload tidak boleh memilih identitas kebijakan.

Autentikasi utama adalah kemampuan membuka socket yang khusus dipasang ke attempt. Pemeriksaan SO_PEERCRED dan cgroup merupakan pertahanan tambahan bila transport mendukungnya. Node 24.18.0 tidak menyediakan API net.Socket publik untuk SO_PEERCRED. Jangan menambahkan native addon hanya untuk pemeriksaan ini. UID saja tidak cukup: beberapa container rootless memakai mapping UID yang sama. Pemilik host dipercaya karena ia sudah mengendalikan runner; inode socket khusus attempt dan parent host 0700 menjadi batas utama. Jangan memakai token bearer berisi credential di env agent. Jika token lokal tambahan diperlukan, anggap token tersebut bisa dibaca tool; token hanya mengizinkan model attempt yang sama dan habis bersama lease.

Relay mendengar pada `127.0.0.1:PORT` dalam network namespace container. Konfigurasi provider Codex menunjuk `http://127.0.0.1:PORT/v1`, `wire_api=responses`, tanpa provider `env_key` atau Authorization nyata. Adapter 1.12.0 menyediakan custom gateway `apiType=openai` yang dipetakan ke Responses. Probe memakai gateway extension melalui session/new dan dua prompt; semua request fixture tidak membawa Authorization. Relay Unix socket produksi masih harus diuji bersama jalur tersebut.

Broker menerima hanya method/path yang benar-benar diperlukan, awalnya POST `/v1/responses`. Tolak CONNECT, absolute-form URLs, Upgrade/WebSocket, forwarding headers, path traversal, query tujuan, dan API provider lainnya. WebSocket Responses belum disertifikasi; gunakan HTTP SSE. Jika native meminta endpoint tambahan, deny dan audit dahulu.

Broker memvalidasi schema, model, ukuran request, output token, concurrency, jumlah request, budget biaya, dan lease sebelum meneruskan. Native request tidak boleh mengubah model atau memakai server-side web search, remote MCP, computer use, ataupun URL fetch melalui tool provider. Allowlist hanya memuat dynamic tools Grow yang diuji dan tool internal tanpa efek eksternal yang secara eksplisit disetujui. Tolak media URL eksternal bila data scope belum mengizinkannya.

Broker membangun tujuan dari konfigurasi host, bukan dari Host/header/body. Ia membuang seluruh credential/header client dan menyuntik provider credential miliknya. DNS diselesaikan dan IP diperiksa pada setiap koneksi; IP koneksi dipatok setelah validasi, dengan TLS SNI/certificate memakai hostname yang disetujui. Tolak metadata, redirect, dan alamat di luar policy. LAN/Tailscale hanya per host/port yang disetujui. Tidak ada fallback provider otomatis.

Streaming SSE dibatasi ukuran, waktu idle, total waktu, dan schema. Broker menghentikan upstream ketika lease/cancel/budget habis. Jangan log header/body mentah. Sanitasi error dan known-secret echoes sebelum keluar. Provider yang sengaja menyandikan ulang secret adalah batas kepercayaan eksternal; redaction bukan bukti bahwa provider berbahaya tidak bisa membocorkannya.

Container tool tidak mendapat socket atau relay model. Proses project tidak dapat memanggil model melalui kemampuan milik container native. Pemisahan ini harus diuji, termasuk namespace jaringan, mount, environment, dan file descriptor yang diwariskan.

Semua dynamic tool masuk melalui request `item/tool/call` yang diteruskan adapter ke supervisor. Broker mengikat identitas call ke attempt aktif, memeriksa lease/izin/scope/argumen/budget, lalu mencatat intent lokal secara durable sebelum eksekusi. Hasil dicatat sebelum dikembalikan. ID yang sama dengan argumen berbeda ditolak; hasil tidak pasti harus direkonsiliasi. Kontrak backend yang dipakai runner mewajibkan proposal dan konsumsi operasi server sebelum setiap efek tool, termasuk read, edit, dan check. Journal lokal melengkapi otorisasi tersebut; respons consume yang hilang tidak memberi izin eksekusi.

Sumber pemeriksaan API Node: [net.Socket pada Node 24.18.0](https://github.com/nodejs/node/blob/v24.18.0/doc/api/net.md). Probe Python membuktikan dukungan kernel Linux, bukan ketersediaan API Node.

## Stop, lease, dan durability

PID/process-group kill saja tidak cukup. Supervisor menguasai seluruh container ID immutable dan cgroup attempt. Watchdog luar terpisah dari koneksi ACP menyimpan expiry monotonik. Control disconnect membekukan dispatch baru dan menghentikan seluruh container segera, termasuk operasi tool yang masih berjalan.

Saat stop: cabut socket grant dahulu, tutup upstream request, kirim session/cancel best effort, beri grace period pendek, lalu `docker kill --signal=KILL <container-id>`. Verifikasi container stopped dan cgroup populated=0 atau proses teramati sudah hilang sebelum status cancelled/completed. Container kill membunuh init PID namespace dan turunannya, termasuk setsid/double-fork. Background task tidak boleh melewati attempt.

Watchdog wajib tetap bekerja bila supervisor crash. Jalankan pengawasan melalui layanan user systemd terpisah dengan timer expiry dan cleanup handler. Kill watchdog/supervisor bersama, engine failure, atau cgroup accounting tidak tersedia harus diuji; kegagalan memverifikasi stop berarti interrupted, bukan cancelled. Saat startup, identifikasi attempt lama sebelum menerima lease baru. Broker tetap fail-closed berdasarkan expiry walaupun cleanup OS gagal.

Tidak ada credential Git di container. Push/PR hanya melalui external Git broker dengan ledger, approval hash commit/diff, target/ref, epoch, expiry, dan CAS. Broker menolak hooks/filter/repository config yang dapat mengeksekusi kode di luar containment. Hasil unknown direkonsiliasi sebelum retry. Final diff/test/artifact berasal dari checkout aktual; jangan percaya klaim agent.

## Bukti lokal

Source yang dibaca:

- `/home/ramaaditya/Project/.worktrees/grow-team-agents/internals/docs/agent-runtime-decision.md`.
- `internals/docs/spec/2026-09-21-agent-connections-and-coding-harness.md`, bagian 10–16.
- `/tmp/grow-team-p0-research/src__CodexJsonRpcConnection.ts:15-27`: child env diteruskan ke Codex App Server.
- `/tmp/grow-team-p0-research/src__AgentMode.ts:37-78`: mode, approval, sandbox policy.
- `/tmp/grow-team-p0-research/node_modules/@agentclientprotocol/codex-acp/dist/index.js:28166`: gateway hanya `openai: responses`.
- File yang sama `:28376-28412`: gateway memakai base_url/http_headers, tanpa kewajiban key nyata.
- File yang sama `:35150-35178`: CODEX_CONFIG/MODEL_PROVIDER dan startup logging. Jangan menaruh key pada config karena logger dapat merekamnya.
- `/tmp/grow-team-p0-research/package-lock.json`: package pins dari penelitian awal.

Host yang diperiksa: rootless Docker, builtin seccomp, cgroup v2, systemd driver, user Docker service Delegate=yes, unprivileged user namespaces aktif. Ini kemampuan host yang diamati, bukan bukti seluruh batas resource efektif.

Probe baru tanpa secret:

- Program `/tmp/grow-team-sandbox-probe/probe.py`.
- Hasil `/tmp/grow-team-sandbox-probe/result.json`.
- Image lokal `python@sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de`; tidak menarik image.
- Container UID 65532, network none, read-only rootfs, cap-drop ALL, no-new-privileges, RAM/PID/CPU flags.
- Koneksi Unix socket host berhasil dengan request sintetis; broker menerima peer host UID/GID 165531 dan PID yang dapat dicocokkan.
- Koneksi TCP ke 1.1.1.1, 169.254.169.254, 172.17.0.1 masing-masing gagal ENETUNREACH.
- Child menjalankan setsid; dua PID host tercatat sebelum kill. Sesudah docker kill, keduanya tidak ada dan container auto-removed.
- Tidak ada API key, model prompt, model berbayar, mount project, atau aktivitas produksi pada probe.

Probe hanya membuktikan primitive UDS + network namespace + penghentian child teramati. Ia belum membuktikan relay HTTP, Codex prompt/coding, policy broker, resource limits, atau proteksi kernel.

## Asumsi dan gate negatif wajib

1. Image/runtime/toolchain pinned tersedia dan dapat menjalankan loop Codex dalam seccomp default tanpa elevated capabilities. Native environments harus tetap kosong dan katalog tool sesuai allowlist. Kegagalan containment atau protokol harus fail closed.
2. Fake provider SSE melalui relay: initialize/new/prompt, tool call fixture edit/test, permission deny/cancel, resume, output diff, dan penghentian semuanya diuji dengan package exact.
3. Model auth tidak membutuhkan key vendor nyata pada agent. Cari synthetic credential canary di env, /proc, files, stdout/stderr, ACP, artifacts, errors, dan config logs.
4. Direct IPv4/IPv6, DNS, UDP, metadata, loopback host, bridge, proxy env, alternate hostnames, redirect, DNS rebinding, URL userinfo, encoded paths, CONNECT dan websocket semuanya ditolak.
5. Socket attempt lain, epoch lama, revoked lease, expiry selama stream, model berbeda, forged Host/Authorization, concurrent budget race, dan oversized bodies ditolak.
6. Proses tool tidak dapat mengakses relay/socket model. Native tidak memperoleh key, tujuan bebas, remote tools, atau budget tambahan. Uji endpoint yang mengembalikan synthetic secret canary.
7. Host HOME/SSH/Docker/cloud files tidak tersedia. Uji symlink, /proc/root/fd, mount attempts, setuid binaries, devices, Git common-dir escape, hooks, submodules/LFS/filter, dan poisoned shared cache.
8. Fork bomb, RAM/CPU/disk/output flood, setsid/double-fork, child ignores TERM, supervisor SIGKILL, ACP crash, control disconnect, broker crash, dan reboot recovery. Verifikasi cgroup kosong, bukan exit parent saja.
9. Owner WIP tetap identik; mutation hanya checkout attempt. Read-only mode menolak writes. Final tests terikat final tree hash.
10. Push/PR tanpa approval, approval changed diff/ref/epoch, double use, dan remote outcome unknown ditolak atau direkonsiliasi.

Kesimpulan: primitive Linux yang dibutuhkan tersedia dan satu probe sempit berhasil. Desain layak untuk implementasi dan conformance berikutnya; **belum tersertifikasi dan belum code_ready**.


## Base image Node yang tersedia

Image `node:24.18.0-bookworm-slim` telah ditarik dan dipatok ke
`sha256:6f7b03f7c2c8e2e784dcf9295400527b9b1270fd37b7e9a7285cf83b6951452d`.
Probe container tanpa jaringan mengembalikan Node `v24.18.0` dan UID `65532`.
Nilai cgroup sesuai flag probe: RAM `268435456`, PID `32`, CPU `25000 100000`.

Ini memeriksa versi dan pengaturan cgroup. Image runner lengkap, toolchain,
dan tes pelanggaran batas resource masih harus dibangun dan diuji.

### Base image untuk payload native lengkap

Pemeriksaan tambahan 22 September 2026 menemukan bahwa Zsh pada paket Codex
memerlukan `GLIBC_2.38`. Binary tersebut gagal pada image Bookworm di atas.
Node tetap berjalan, sehingga pemeriksaan Node saja tidak cukup.

Gunakan base `node:24.18.0-trixie-slim` dengan digest berikut untuk implementasi:

```text
sha256:ae91dcc111a68c9d2d81ff2a17bda61be126426176fde6fe7d08ab13b7f50573
```

Probe image ini mengembalikan Node `v24.18.0`, Zsh `5.9.0.3-test`, dan
Debian GLIBC `2.41-12+deb13u3`. Probe memakai UID `65532`, network `none`,
root filesystem read-only, serta batas CPU, RAM, dan jumlah proses.
Kedua container probe telah berhenti dan bukti tetap tersimpan.

Hasil tersebut membuktikan kompatibilitas binary yang diuji. Image runner akhir,
seluruh toolchain, watchdog, dan batas akses tetap harus disertifikasi.

## Kompatibilitas sandbox native pada container pilot

Probe Codex `0.154.0` menemukan bahwa `workspaceWrite` gagal membuat namespace
`bwrap` di dalam container dengan konfigurasi di atas. Konfigurasi container dan
host tidak diubah untuk mengatasi kegagalan tersebut.

Schema yang diekspor binary yang sama menyediakan kebijakan turn berikut:

```json
{"type":"externalSandbox","networkAccess":"restricted"}
```

Pada probe `command/exec`, kebijakan ini berhasil menjalankan proses sintetis.
UID tetap `65532`. Penulisan `/workspace/probe.txt` berhasil; penulisan ke `/etc`
dan mount paket read-only ditolak dengan `EROFS`. Home SSH pengguna dan Docker
socket tidak tersedia. Koneksi ke IP publik, metadata, dan bridge ditolak dengan
`ENETUNREACH`. Tidak ada credential, request model, atau repository pengguna.

Bukti sementara: `/tmp/grow-team-native-probe/app-server-result.json` dan
`schemas.json`. Probe ini belum membuktikan turn ACP, izin, resume, atau coding.

[Dokumentasi resmi](https://learn.chatgpt.com/docs/agent-approvals-security)
menjelaskan bahwa container dapat menjadi batas isolasi ketika sandbox internal
tidak dapat dijalankan. Untuk versi pilot, kontrak App Server menyediakan mode
external secara eksplisit. Grow tetap memakai seluruh pembatasan container,
broker model, dan watchdog yang ditetapkan pada dokumen ini.

Adapter ACP `1.12.0` mengirim kebijakan dari `AgentMode` pada setiap prompt.
Karena itu override `CODEX_CONFIG` saja tidak cukup. Integrasi memerlukan patch
sempit yang dipatok, diperiksa, dan diuji pada pembentukan turn. Pertahankan
`approvalPolicy: on-request` serta `approvalsReviewer: user`. Larang perubahan
mode, provider, root, atau extension yang dapat melewati descriptor aktif.

`thread/start` memakai enum sandbox yang berbeda dan tidak menerima string
`externalSandbox`. Jangan mengirim nilai yang tidak didukung atau memulai tool
sebelum kebijakan turn yang benar terpasang.

Mode external menyerahkan pembatasan file kepada container. Mount `.git` dan
konfigurasi `.codex` harus read-only secara eksplisit. Commit, push, dan draft PR
tetap melalui broker dengan record operasi serta izin yang sesuai. Untuk tugas
diskusi, seluruh mount repository harus read-only.

`on-request/user` mempertahankan jalur approval ketika native agent meminta izin.
Pengaturan tersebut tidak memeriksa setiap edit atau shell melalui Grow. Profil
dengan izin lebih sempit hanya boleh aktif jika batas itu benar-benar dapat
ditegakkan. Receipt progres setelah eksekusi bukan bukti journal sebelum eksekusi.
Jalur yang diuji tetap mengirim `externalSandbox/restricted` pada setiap turn
container model. Namun, native environments harus kosong: kebijakan external
tidak memberi akses tool project. Dynamic tools Grow memberikan pemeriksaan
sebelum eksekusi untuk setiap operasi project di container tool terpisah.

Jangan mengaktifkan proxy jaringan native yang memerlukan sandbox bertingkat.
Model memakai relay loopback ke socket broker Grow dengan policy tetap.

## Kontrak dynamic tools pada versi yang dipatok

Tag Codex `rust-v0.154.0` menunjuk commit
`6b9826e3aa83b1a5947db50f4332cb9c65f1b340`. Schema binary yang sama mengonfirmasi
field experimental `environments` dan `dynamicTools`. Adapter 1.12.0 sudah
mengaktifkan `experimentalApi`, tetapi generated types-nya belum memuat field ini.
Patch wajib memakai kontrak versi yang dipatok dan gagal jika target patch berubah.

`environments: []` menghilangkan registrasi exec, write_stdin, apply_patch, dan
view_image. Dynamic tools tetap terdaftar dan menunggu hasil dari client. Nama
tool yang tidak terdaftar ditolak sebelum dispatch. Ini bukti source; sertifikasi
wajib memaksa call palsu melalui provider fixture untuk mengonfirmasi perilaku.

PreToolUse bawaan tidak dipakai sebagai batas otorisasi. Pada versi ini timeout,
spawn error, dan output rusak dapat meneruskan eksekusi. `write_stdin` juga tidak
melewati pre-hook. Pemeriksaan setelah eksekusi tidak memperbaiki celah tersebut.

Referensi versi yang diperiksa:

- [Thread environments dan dynamic tools](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/app-server-protocol/src/protocol/v2/thread.rs).
- [Turn environments](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/app-server-protocol/src/protocol/v2/turn.rs).
- [Registrasi tool](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/core/src/tools/spec_plan.rs).
- [Request dan hasil dynamic tool](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/app-server-protocol/src/protocol/v2/item.rs).
- [Perilaku gagal PreToolUse](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/hooks/src/events/pre_tool_use.rs).

Sebelum rilis, uji ACP aktual untuk call/result, penolakan, dua turn, call native
palsu, cancel, reconnect, dan pemulihan hasil tidak pasti. Raw App Server probe
tidak menggantikan pembuktian jalur ACP dan broker yang akan dipasang.

## Probe ACP sintetis 2026-09-22

Probe `/tmp/grow-team-native-dynamic-probe/probe.mjs` menjalankan paket ACP dan
Codex asli dengan patch yang diperiksa per target. Bundle asli tidak diubah.
Probe kelayakan ini memakai image Bookworm awal, UID 65532, network none, read-only root,
cap-drop ALL, no-new-privileges, 512 MiB RAM, 128 PID, dan 0,5 CPU.

Verifier independen `/tmp/grow-team-native-dynamic-probe/verify.py` lulus:

- Tujuh request Responses lokal hanya memuat dua dynamic fixture tools.
- Tiga call menunggu jawaban broker yang sengaja ditunda 350 ms.
- Call yang ditolak menghasilkan status gagal tanpa marker efek.
- Call palsu `exec_command` dan `apply_patch` ditolak tanpa efek.
- Dua prompt ACP selesai dengan `end_turn` setelah hasil tool yang sesuai.
- Satu thread/start dan dua turn/start seluruhnya memakai environments kosong.
- Tidak ada Authorization, credential nyata, request model eksternal, atau perubahan cache paket.

Probe pertama menolak katalog karena `request_user_input` serta `skills.list/read`
masih muncul. Kontrol efektif adalah `tools.experimental_request_user_input.enabled=false`
dan `orchestrator.skills.enabled=false`. Flag lain untuk shell, subagent, browser,
plugin, hook, search, dan code mode juga dimatikan; katalog aktual tetap diperiksa.

Adapter juga membuat thread judul otomatis di luar jalur prompt. Patch mematikan
`TitleGenerator.onTurnCompleted`. Implementasi produksi harus menegakkan kebijakan
pada jalur request bersama dan menolak seluruh jalur sesi yang belum diuji.

SHA-256 bundle asli:
`f45a64dc3a994556ebdb688dc8d59b86945a9b2f940a3e3e545739dd265a7cc5`.
SHA-256 patch probe:
`a45f842ed9a5511f142c57c17cf1252a8ee04d26a1e7e91d812db78e21490ea1`.

Provider dan broker fixture berada di loopback container yang sama. Probe ini
membuktikan kompatibilitas protokol; ia belum menguji batas broker produksi,
relay Unix socket, edit repository, proses tes, cancel/reconnect, atau publikasi.
Readiness `code_ready` tetap memerlukan sertifikasi integrasi lengkap.
