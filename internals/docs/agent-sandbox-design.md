# Grow Runner: rancangan containment Linux

Audit 2026-09-21. Read-only terhadap source aplikasi. Tidak membaca credential, menghubungi model berbayar, atau mengubah produksi.

## Keputusan konkret

Gunakan **rootless Docker per attempt dengan network namespace `none`, ditambah broker model host melalui Unix socket khusus attempt**. Adapter ACP 1.12.0 dan Codex 0.154.0 berada seluruhnya di container. SDK 1.5.0 dan supervisor Grow berada di luar container. Provider/device/Git credential hanya berada pada supervisor/broker luar.

Alur:

```text
Grow control plane <-- TLS + device identity --> supervisor + lease watchdog
                                                   |
                           typed ACP stdio --------+------> codex-acp -> Codex
                                                   |        [container attempt]
provider HTTPS <-- credential injection <-- model broker <-- Unix socket <-- HTTP loopback relay
                                                   |
                                  external Git broker (separate approval ledger)
```

Container tidak mendapat akses ke Docker socket, host network, host home, SSH agent, credential files, atau API context/Titen/Git umum. Satu Unix socket dipasang sebagai file, bukan direktori host. Relay hanya mengubah transport HTTP loopback ke socket tersebut. Relay tidak menerima URL upstream, tidak memegang provider key, dan bukan HTTP CONNECT/SOCKS proxy.

## Konfigurasi containment

- Image pin digest berisi Node 24.18.0, lockfile ACP/Codex/Zod, relay kecil, dan toolchain profil repository yang disetujui.
- Container non-root UID/GID tetap; `--network=none --read-only --cap-drop=ALL --security-opt=no-new-privileges`.
- PID namespace, IPC namespace, dan cgroup namespace privat; seccomp default tetap aktif. Jangan menambahkan privileged, host PID, atau CAP_SYS_ADMIN untuk membuat Codex bekerja.
- Wajib resource limit CPU, RAM, PID, ukuran output, temporary disk, dan waktu. Profil awal contoh: 2 CPU, 2 GiB RAM, 256 PID; sesuaikan kebutuhan tes sebelum grant.
- Writable mounts hanya checkout disposable `/workspace` dan tmpfs terbatas `/tmp`, `/home/agent`, `/home/agent/.codex`. Untuk read-only, `/workspace` dipasang read-only.
- Checkout harus copy/clone mandiri dengan `.git` mandiri yang sudah dibersihkan. Jangan mount worktree pengguna beserta common Git directory. Origin harus bebas credential; tidak ada helper, hooks, filters, atau git config host.
- Siapkan dependency secara offline di image atau cache read-only. Dependency install jaringan menjadi operasi broker/profil terpisah; tidak mengubah network namespace saat runtime.
- Environment allowlist: PATH, HOME container, CODEX_HOME container, LANG, NO_BROWSER, konfigurasi non-secret. Jangan mewarisi process.env supervisor.
- ACP client tidak mengiklankan host fs/terminal. Semua tool native tetap di container. Session/new hanya menerima `/workspace`; mode/config/provider changes dari UI/model ditolak di supervisor.
- Gunakan mode adapter `read-only` sebagai preset `workspaceWrite/on-request/user`, bukan sebagai jaminan filesystem read-only. Jangan gunakan default `agent/auto_review` atau full access sebagai jalan pintas.
- Matikan subagent/MCP eksternal/extensions/hooks yang tidak disertifikasi. Container tetap membatasi dampak bila konfigurasi native termuat dari repository.

## Broker model dan autentikasi lokal

Setiap attempt mendapat Unix socket baru di direktori host mode 0700. Hanya file socket itu dipasang ke container. Kebijakan broker diikat ke socket saat dibuat: runner/job/attempt/epoch, config version, approved origin/base path, model allowlist, data scope, budget, dan expiry. Payload tidak boleh memilih identitas kebijakan.

Autentikasi utama adalah kemampuan membuka socket yang khusus dipasang ke attempt. Pemeriksaan SO_PEERCRED dan cgroup merupakan pertahanan tambahan bila transport mendukungnya. Node 24.18.0 tidak menyediakan API net.Socket publik untuk SO_PEERCRED. Jangan menambahkan native addon hanya untuk pemeriksaan ini. UID saja tidak cukup: beberapa container rootless memakai mapping UID yang sama. Pemilik host dipercaya karena ia sudah mengendalikan runner; inode socket khusus attempt dan parent host 0700 menjadi batas utama. Jangan memakai token bearer berisi credential di env agent. Jika token lokal tambahan diperlukan, anggap token tersebut bisa dibaca tool; token hanya mengizinkan model attempt yang sama dan habis bersama lease.

Relay mendengar pada `127.0.0.1:PORT` dalam network namespace container. Konfigurasi provider Codex menunjuk `http://127.0.0.1:PORT/v1`, `wire_api=responses`, tanpa provider `env_key` atau Authorization nyata. Adapter 1.12.0 menyediakan custom gateway `apiType=openai` yang dipetakan ke Responses. Supervisor memakai gateway extension atau CODEX_CONFIG/MODEL_PROVIDER yang tetap. Pilihan mekanisme harus dibuktikan dengan session/new+prompt sintetis sebelum rilis; source mendukungnya, probe ini belum menjalankannya.

Broker menerima hanya method/path yang benar-benar diperlukan, awalnya POST `/v1/responses`. Tolak CONNECT, absolute-form URLs, Upgrade/WebSocket, forwarding headers, path traversal, query tujuan, dan API provider lainnya. WebSocket Responses belum disertifikasi; gunakan HTTP SSE. Jika native meminta endpoint tambahan, deny dan audit dahulu.

Broker memvalidasi schema, model, ukuran request, output token, concurrency, jumlah request, budget biaya, dan lease sebelum meneruskan. Native request tidak boleh mengubah model atau memakai server-side web search, remote MCP, computer use, ataupun URL fetch melalui tool provider. Untuk coding awal, allowlist katalog function tools native yang sudah ditinjau. Tolak media URL eksternal bila data scope belum mengizinkannya.

Broker membangun tujuan dari konfigurasi host, bukan dari Host/header/body. Ia membuang seluruh credential/header client dan menyuntik provider credential miliknya. DNS diselesaikan dan IP diperiksa pada setiap koneksi; IP koneksi dipatok setelah validasi, dengan TLS SNI/certificate memakai hostname yang disetujui. Tolak metadata, redirect, dan alamat di luar policy. LAN/Tailscale hanya per host/port yang disetujui. Tidak ada fallback provider otomatis.

Streaming SSE dibatasi ukuran, waktu idle, total waktu, dan schema. Broker menghentikan upstream ketika lease/cancel/budget habis. Jangan log header/body mentah. Sanitasi error dan known-secret echoes sebelum keluar. Provider yang sengaja menyandikan ulang secret adalah batas kepercayaan eksternal; redaction bukan bukti bahwa provider berbahaya tidak bisa membocorkannya.

**Batas penting:** native agent dan shell dalam container yang sama dapat memanggil relay model. Tanpa isolasi tool tambahan, tidak ada bukti bahwa setiap request berasal dari loop model yang sah. Desain ini menolak jaringan arbitrer dan melindungi credential; bukan melarang shell menggunakan kemampuan model terbatas. Semua pemanggilan dibebankan ke budget attempt. Jika persyaratan dimaksudkan melarang shell memanggil model sama sekali, desain ini belum cukup: perlu tool execution namespace/UID terpisah yang benar-benar diwajibkan native adapter, atau runtime broker-tool TS.

Sumber pemeriksaan API Node: [net.Socket pada Node 24.18.0](https://github.com/nodejs/node/blob/v24.18.0/doc/api/net.md). Probe Python membuktikan dukungan kernel Linux, bukan ketersediaan API Node.

## Stop, lease, dan durability

PID/process-group kill saja tidak cukup. Supervisor menguasai container ID immutable dan cgroup attempt. Watchdog luar terpisah dari koneksi ACP menyimpan expiry monotonik. Control disconnect membekukan dispatch baru dan menghentikan seluruh container segera; jangan menunggu native tool berikutnya karena tool native dapat mutasi lokal tanpa broker.

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

1. Image/runtime/toolchain pinned tersedia dan dapat menjalankan Codex dalam seccomp default tanpa elevated capabilities. Kegagalan sandbox native harus fail closed.
2. Fake provider SSE melalui relay: initialize/new/prompt, tool call fixture edit/test, permission deny/cancel, resume, output diff, dan penghentian semuanya diuji dengan package exact.
3. Model auth tidak membutuhkan key vendor nyata pada agent. Cari synthetic credential canary di env, /proc, files, stdout/stderr, ACP, artifacts, errors, dan config logs.
4. Direct IPv4/IPv6, DNS, UDP, metadata, loopback host, bridge, proxy env, alternate hostnames, redirect, DNS rebinding, URL userinfo, encoded paths, CONNECT dan websocket semuanya ditolak.
5. Socket attempt lain, epoch lama, revoked lease, expiry selama stream, model berbeda, forged Host/Authorization, concurrent budget race, dan oversized bodies ditolak.
6. Shell yang memanggil relay langsung tetap tidak memperoleh key, tujuan bebas, remote tools, atau budget tambahan. Uji endpoint yang mengembalikan synthetic secret canary.
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
