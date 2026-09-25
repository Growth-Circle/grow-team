# Riset 03: Tipe agen sanji.space (Agen Kerja vs Agen Coding)

Status: riset, bukan keputusan.

Catatan konflik: isi riset ini bertentangan dengan keputusan pemilik di [`agent-sdk-decision.md`](../agent-sdk-decision.md) (24 September 2026). Keputusan pemilik yang berlaku sampai ada keputusan baru.

**Tanggal:** 24 September 2026
**Pertanyaan dari owner:**

1. Apakah ide dua tipe agen (**coding** dan **kerja**) sudah tepat?
2. Untuk agen coding: minta pengguna meng-install Claude Code / Codex CLI di server, lalu sanji menyambung lewat **ACP** dan men-spawn container. Benar?
3. Untuk agen kerja: pakai **Claude Agent SDK** karena "bisa kerja cepat". Benar?

**Konteks repo:** `grow-team` sudah memutuskan runner memakai **ACP** (`codex-acp` 1.12.0) + runtime endpoint OpenAI-compatible, dengan container rootless, lease, dan journal di PostgreSQL (basi, lihat catatan konflik di atas; [agent-harness-decision.md](../agent-harness-decision.md), [agent-runtime-decision.md](../agent-runtime-decision.md); basi sejak [agent-sdk-decision.md](../agent-sdk-decision.md), hari yang sama, lihat catatan konflik di atas).

---

## Jawaban singkat

| Ide                                                        | Penilaian                      | Catatan                                                                                                                                                             |
| ---------------------------------------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Dua tipe agen (Kerja & Coding)                             | ✅ **Tepat**                   | Kebutuhan lingkungan, izin, biaya, dan UX-nya memang berbeda jauh                                                                                                   |
| Coding lewat ACP ke Claude Code / Codex di server pengguna | ✅ **Tepat**, dengan 2 koreksi | (a) Claude Code **tidak** bicara ACP secara native, perlu adapter. (b) **Wajib API key**, bukan login langganan Pro/Max                                             |
| Agen kerja pakai Claude Agent SDK karena lebih cepat       | ⚠️ **Setengah tepat**          | Agent SDK adalah **loop yang sama dengan Claude Code**, jadi tidak otomatis lebih cepat. Kecepatan datang dari cara menjalankannya. SDK juga hanya mendukung Claude |

---

## 1. Temuan riset

### 1.1 ACP (Agent Client Protocol)

- ACP adalah standar terbuka dari Zed untuk komunikasi agen coding ↔ editor/klien, berbasis JSON-RPC 2.0 lewat stdin/stdout. Perannya berbeda dengan MCP: **MCP untuk akses tool dan data, ACP untuk integrasi agen ↔ klien**. Saat sesi ACP dimulai, klien bisa menyerahkan endpoint MCP ke agen. _(Morph, Mar 2026)_
- **Claude Code belum bicara ACP secara native.** Ia berjalan lewat adapter buatan Zed. Codex CLI juga lewat adapter. _(Morph)_
- Adapter resmi di organisasi `agentclientprotocol`: **`claude-agent-acp`** (dibangun di atas Claude Agent SDK) dan **`codex-acp`**. Keduanya aktif diperbarui (per 23 Sep 2026). _(GitHub agentclientprotocol, npm)_
- Registry ACP sudah berisi puluhan agen: Gemini CLI, Copilot, Goose, OpenCode, Cursor, dan lain-lain. Artinya klien ACP bisa menjalankan banyak vendor dengan satu integrasi. _(agentclientprotocol.com)_

➡️ **Implikasi:** keputusan grow-team memakai ACP sudah tepat. Runner cukup menjadi **ACP client**. Untuk Claude, install `@agentclientprotocol/claude-agent-acp`. Untuk Codex, `codex-acp` (sudah ada di runner).

### 1.2 Claude Agent SDK

- Agent SDK men-spawn dan mengawasi **subprocess `claude` CLI** yang punya shell, working directory, dan file sesi di disk. Setiap agen yang berjalan adalah proses panjang yang terikat pada state lokal. _(Claude Code Docs: Hosting)_
- SDK disebut menjalankan **loop agen yang sama dengan Claude Code** di dalam proses dan infrastruktur kita sendiri. _(TrueFoundry)_
- **Wajib dijalankan di container sandbox:** isolasi proses, batas resource, kontrol jaringan, filesystem sementara. Biaya container minimum sekitar 5 sen/jam. Biaya dominan tetap token. _(Claude Docs: Hosting)_
- Sandbox per-perintah bawaan **tidak cukup untuk run tanpa pengawasan**. Seluruh proses harus dibungkus container atau VM. _(OpenComputer)_
- SDK dan Managed Agents **hanya mendukung model Claude**. _(TrueFoundry)_
- Ada alternatif **Claude Managed Agents** (sejak April 2026): Anthropic yang menjalankan harness, sandbox, dan log sesi, lewat REST API. _(HatchWorks)_

### 1.3 Autentikasi & lisensi, penting untuk produk multi-tenant

- Anthropic **tidak mengizinkan developer pihak ketiga menawarkan login claude.ai** atau rate limit langganan untuk produk mereka, termasuk agen berbasis Agent SDK, kecuali sudah disetujui. **Wajib API key.** _(Agent SDK overview)_
- Token OAuth dari akun Free/Pro/Max tidak boleh dipakai di produk lain, termasuk Agent SDK. _(Winbuzzer, Feb 2026)_
- Produk kita harus tetap memakai **branding sendiri** dan tidak boleh tampak seperti Claude Code. _(Agent SDK overview)_

➡️ **Implikasi:** alur "pengguna login Claude Max di servernya lalu sanji memakai sesinya" **berisiko melanggar ToS**, karena sanji adalah produk pihak ketiga yang mengorkestrasi. Minta **API key Anthropic Console / Bedrock / Vertex**. Untuk Codex (login ChatGPT vs API key), kebijakan OpenAI harus dicek terpisah. Riset ini belum memverifikasinya.

---

## 2. Rekomendasi arsitektur

Bedakan tipe agen berdasarkan **beban kerja dan lingkungan**, bukan berdasarkan SDK.

|                | **Agen Kerja** (Presentasi, Riset, Laporan, Notulen, Email)                                                                                                   | **Agen Coding** (kode, PR, test)                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| Contoh         | Kaki, Matcha, agen buatan pengguna non-teknis                                                                                                                 | Ayame (mode coding), agen repo                                                         |
| Lingkungan     | **Cloud sanji** (multi-tenant), sandbox ringan per sesi, tanpa repo                                                                                           | **Runner milik pengguna** (laptop/server), container per job berisi checkout repo      |
| Harness        | Claude Agent SDK **atau** `claude-agent-acp` di sandbox cloud                                                                                                 | Claude Code (via `claude-agent-acp`) atau Codex CLI (via `codex-acp`) lewat ACP        |
| Tool           | MCP sanji: Drive, Sheets, Slides, Docs, Fathom, WhatsApp, Kalender, pencarian web, **team-tools** (buat tugas, minta approval). Shell dimatikan atau dibatasi | File, shell, git, test runner di dalam container + team-tools sanji via MCP            |
| Model          | Claude (Sonnet untuk kerja, Haiku untuk ringkasan/routing), bisa diganti endpoint lain                                                                        | Pilihan pengguna: Claude, Codex, atau endpoint OpenAI-compatible (sudah ada di runner) |
| Kredensial     | API key milik **sanji** (ditagih ke workspace) atau BYO key workspace                                                                                         | API key milik **pengguna** di runner (BYO), tidak pernah lewat server sanji            |
| Setup pengguna | **Nol.** Langsung jalan saat workspace dibuat                                                                                                                 | Install `sanji-runner` + CLI agen + API key, lalu pairing di browser                   |
| Target waktu   | Mulai < 3 detik (warm pool)                                                                                                                                   | Mulai 10–60 detik (container + checkout)                                               |
| Approval       | Aksi keluar (kirim ke klien, tulis ke Drive klien)                                                                                                            | Merge, push, deploy, perintah berbahaya                                                |

### Kenapa agen kerja bisa terasa "cepat" (bukan karena SDK-nya)

1. **Warm pool:** sandbox sudah siap sebelum ada tugas, jadi tidak ada cold start container.
2. **Tanpa repo & tanpa shell:** tidak ada clone atau install dependensi.
3. **Model tepat guna:** Haiku untuk routing/ringkasan, Sonnet untuk dokumen, Opus hanya bila perlu.
4. **Tool sempit via MCP:** agen tidak "mengeksplorasi" filesystem.
5. **Streaming ke UI:** progres langsung terlihat di drawer Job.

### Opsi A vs B untuk agen kerja

|           | **A. Satu protokol (ACP di mana-mana)**                                                                                  | **B. Agent SDK langsung di control plane**                                              |
| --------- | ------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------- |
| Cara      | Agen kerja juga dijalankan lewat `claude-agent-acp` di sandbox cloud. Runner cloud = runner biasa milik sanji            | Worker Python/TS memanggil Agent SDK, di luar protokol runner                           |
| +         | Satu jalur untuk lifecycle, approval, journal, recovery, yang **sudah dibangun** di grow-agent-runner. Bisa ganti vendor | Lebih sedikit lapisan, akses fitur SDK terbaru (hooks, subagents) lebih cepat           |
| −         | Satu lapisan adapter tambahan                                                                                            | Jalur kedua yang harus diberi approval, audit, dan recovery sendiri. Terkunci ke Claude |
| **Saran** | ✅ **Pilih A untuk MVP**                                                                                                 | Pertimbangkan jika A terbukti membatasi                                                 |

> Alternatif tanpa operasi infrastruktur: **Claude Managed Agents** untuk agen kerja. Cocok untuk tim kecil, tapi data dan sandbox berada di infrastruktur Anthropic, jadi perlu dicek terhadap `security.md` dan klaim privasi di landing.

---

## 3. Alur agen coding (usulan)

```
Pengguna (browser)                 sanji control plane             Runner pengguna (server)
  │ Pengaturan → Agen & runner          │                                 │
  │ "+ Pasangkan perangkat" ──────────▶ │ buat kode pairing               │
  │                                     │ ◀──── sanji runner pair SNJ-4829│ (CLI)
  │ setujui di browser ───────────────▶ │ simpan credential runner (hash) │
  │                                     │                                 │ cek: docker/podman,
  │                                     │                                 │ claude-agent-acp / codex-acp,
  │                                     │                                 │ API key di env lokal
  │ @ayame "perbaiki bug login"         │                                 │
  │ ──────────────────────────────────▶ │ admission + grant + job         │
  │                                     │ ── lease job ─────────────────▶ │ spawn container rootless
  │                                     │                                 │ checkout repo → workspace terpisah
  │                                     │                                 │ start ACP agent (stdio)
  │ drawer Job: log langsung ◀───────── │ ◀── event progres / tool call ─ │ ACP session/update
  │ approval "push branch?" ◀────────── │ ◀── request_permission ──────── │ (policy runner, BUKAN allow_once)
  │ Approve ──────────────────────────▶ │ ── consume approval ──────────▶ │ push → PR
  │                                     │                                 │ container dihapus, checkpoint disimpan
```

**Yang perlu dicek wizard pairing** (tampilkan sebagai checklist di UI):

1. Docker/Podman rootless tersedia.
2. Minimal satu agen ACP terpasang: `claude-agent-acp` (butuh Node) dan/atau `codex-acp`.
3. API key tersedia di environment lokal runner (`ANTHROPIC_API_KEY` / Bedrock / Vertex / `OPENAI_API_KEY`). **Key tidak pernah dikirim ke sanji.**
4. Akses git ke repo (deploy key / token dengan scope repo tertentu).
5. Kapasitas: maksimum job paralel.

---

## 4. Dampak ke produk & UI

| Area                       | Perubahan                                                                                                                                                                                  |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Modal **Buat agen**        | Langkah pertama pilih **Tipe**: _Agen kerja_ (default, langsung jalan) / _Agen coding_ (butuh runner). Tipe coding menampilkan pilihan harness (Claude Code / Codex / endpoint) dan runner |
| Kartu agen                 | Badge tipe: `KERJA` / `CODING`, plus nama runner untuk tipe coding                                                                                                                         |
| Pengaturan → Agen & runner | Wizard pairing dengan checklist §3. Status per runner: agen ACP yang terdeteksi + versinya                                                                                                 |
| Tugas                      | Tugas coding menampilkan tautan PR dan status CI. Tugas kerja menampilkan tautan file Drive                                                                                                |
| Tagihan                    | Agen kerja memakai kuota model workspace (ditagih sanji). Agen coding memakai key pengguna, jadi tidak masuk tagihan sanji                                                                 |
| Landing                    | Tambah kalimat: "Agen kerja langsung aktif. Agen coding berjalan di server kamu sendiri."                                                                                                  |

## 5. Risiko & pertanyaan terbuka

| #   | Risiko / pertanyaan                                      | Mitigasi / langkah                                                                                               |
| --- | -------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| 1   | Pengguna ingin memakai langganan Claude Max/ChatGPT Plus | Claude: **tidak boleh**. Tampilkan penjelasan di wizard. ChatGPT/Codex: cek kebijakan OpenAI sebelum menjanjikan |
| 2   | Adapter ACP pihak ketiga berubah cepat                   | Pin versi di image runner (sudah dilakukan untuk `codex-acp`), jalankan conformance test tiap upgrade            |
| 3   | ACP client default memilih `allow_once`                  | Sudah diputuskan di spec: runner memakai policy dan approval sanji sendiri                                       |
| 4   | Agen kerja di cloud memegang data klien                  | Sandbox per workspace, tanpa shell, egress dibatasi ke Google/Fathom/WA API, audit setiap tool call              |
| 5   | Biaya token agen kerja                                   | Batas biaya per workspace (Pengaturan), routing model murah untuk tugas ringan                                   |
| 6   | Managed Agents vs self-host                              | Mulai self-host (opsi A). Evaluasi Managed Agents setelah pilot                                                  |

## 6. Sumber

- Morph: ACP explained: https://www.morphllm.com/agent-client-protocol
- ACP GitHub org (claude-agent-acp, codex-acp): https://github.com/agentclientprotocol
- ACP agents registry: https://agentclientprotocol.com/get-started/agents
- npm `@agentclientprotocol/claude-agent-acp`: https://www.npmjs.com/package/@agentclientprotocol/claude-agent-acp
- Claude Code Docs: Hosting the Agent SDK: https://code.claude.com/docs/en/agent-sdk/hosting
- Claude Docs: Hosting the Agent SDK: https://platform.claude.com/docs/en/agent-sdk/hosting
- Claude Code Docs: Agent SDK overview (auth & branding): https://code.claude.com/docs/en/agent-sdk/overview
- TrueFoundry: Agent SDK vs Managed Agents: https://www.truefoundry.com/blog/claude-agent-sdk-vs-claude-managed-agents
- HatchWorks: Agent SDK & Managed Agents: https://hatchworks.com/blog/claude/claude-agent-sdk-and-managed-agents/
- OpenComputer: Where does the agent run: https://opencomputer.dev/guides/claude-agent-sdk-sandbox/
- Winbuzzer: subscription OAuth ban: https://winbuzzer.com/2026/02/19/anthropic-bans-claude-subscription-oauth-in-third-party-apps-xcxwbn/
