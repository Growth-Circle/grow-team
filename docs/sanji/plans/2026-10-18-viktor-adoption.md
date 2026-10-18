# Plan: Mengadopsi alur Viktor ke Sanji (aman & bertahap)

Status: **DRAF untuk dibaca Ari — belum ada yang dikerjakan.** Sumber: paket `viktor-ui-docs.zip` (snapshot app.viktor.com 18 Okt 2026, 14 dokumen + 76 screenshot) + audit kode Sanji `a3abb3cabb`/`eed41c862e`.

## 1. Apa itu Viktor (ringkas)
Viktor = **"karyawan AI"** yang jadi pusat aplikasi. Alurnya:
1. **Chat dengan agen** sebagai layar utama + panel kanan **Status** ("yang sedang dikerjakan, yang menunggu kamu, keputusan yang diambil").
2. **Integrasi** (9.800 app + MCP custom) dengan **izin per tool**: *Nonaktif / Jalankan otomatis / Minta konfirmasi*, akses *Anggota workspace / Hanya saya*, dan **kunci** oleh admin.
3. **Terjadwal**: tugas berulang dari **template** (Daily morning briefing, End of day summary, Weekly team digest…) atau manual (judul, deskripsi, pola jadwal, jam, model).
4. **Skill**: playbook `SKILL.md` — *Milik Anda* vs *Dibagikan*; diajari lewat chat, rekaman, atau unggah; bisa diedit; "terakhir dibaca".
5. **Memory**: bisa ditanya "apa yang Viktor tahu" (disusun ulang tiap malam).
6. **Pengaturan workspace**: model default + preset (Ultra/Smart/Balanced/Cheap) + tingkat penalaran, **instruksi workspace** (4.000 karakter), izin anggota, tim.
7. **Penggunaan & penagihan**: kredit per pengguna/thread/task, grafik harian, batas pengeluaran bulanan, isi ulang otomatis.
8. Shell: sidebar sederhana, **command palette Ctrl K**, pintasan, kartu "starting points" untuk onboarding.

## 2. Pemetaan ke Sanji (yang sudah ada vs gap)
| Viktor | Sanji sekarang (bukti) | Gap |
|---|---|---|
| Chat agen + panel Status | Agen di ruang (`AgentConversation`, `AgentJob`, job card + `agent_job_drawer.ts`), **Needs you** (approval/decision/mention), **Today**, Task board | Belum ada satu "panel Status" per agen/percakapan yang merangkum *sedang dikerjakan / menunggu kamu / keputusan* |
| Izin per tool integrasi | **`McpToolPolicy`** per koneksi (`zerver/models/mcp.py:120`), **`McpJobPlan`** (rencana tool-call untuk di-approve, `:163`), cakupan koneksi workspace/ruang/pribadi | UI izin per tool belum ada/terbatas (`mcp_ui.ts`); MCP server untuk klien luar (`mcp_tools.py`) belum memakai kebijakan per tool (temuan audit H1) |
| Terjadwal + template | Rutinitas bot (Cek pagi, Evaluasi 4 jam, Rekap harian) jalan lewat runner atas nama **Rama**; pengingat pagi via systemd timer | Tidak ada halaman "Terjadwal": rutinitas tak terlihat, tak bisa dijeda/diedit, tak ada template, tak ada riwayat run, identitas bot = Rama |
| Skill | `AgentProfile.work_skills` (JSON), `instructions`, `AgentRealmSettings.team_instructions` (+revision) | Belum ada pustaka playbook bersama (mis. "SOP Essential UMKM", "Format evaluasi 4 jam") yang bisa dilihat/diedit/diversikan |
| Memory | `AgentContextRef`, digest ruang; Titen di luar app | Tidak ada layar "apa yang agen tahu" — **opsional/nanti** |
| Model default & preset | `AgentProvider`, halaman 17 "Model & API key" (spec) | Belum ada preset & level penalaran yang jelas per workspace |
| Penggunaan & budget | `monthly_budget_microunits` di realm & profil; **`model_budget.py` stub selalu "ok"** (audit) | Tidak ada halaman penggunaan; budget tidak ditegakkan |
| Command palette | `command_palette.ts` sudah ada | Perlu dicek kelengkapan aksi |
| Design system | Token `--sj-*`, sudah satu sistem | Komponen standar PageHeader/EmptyState/kartu pengaturan belum dibakukan; format angka & i18n belum ada lint |

## 3. Yang AMAN diadopsi vs yang TIDAK
**Adopsi (pola, bukan kode):**
- Panel **Status** per agen (dibangun dari data yang sudah ada).
- **Izin per tool 3 tingkat** + "Minta konfirmasi" yang masuk ke **Needs you → Approvals** — sekaligus menutup temuan keamanan H1/M4 dan menggantikan "tunggu OK Rama" lewat chat.
- **Halaman Terjadwal** dengan template + identitas bot sendiri.
- **Playbook bersama** (skill) di atas `work_skills`/instruksi yang ada, dengan riwayat versi.
- **Penggunaan & budget** yang benar-benar ditegakkan.
- Disiplin desain: PageHeader/EmptyState standar, satu formatter angka/tanggal (id-ID, 24 jam, WIB), lint terjemahan, glosarium satu-istilah-satu-nama.

**Jangan ditiru (pelajaran dari backlog Viktor sendiri, 32 temuan):** ID mentah sebagai nama (UX-01), Markdown mentah (UX-02), aksi berbiaya tanpa konfirmasi (UX-05 "Infinity" langsung menelepon), bahasa campur (UX-10), satu fitur banyak nama (UX-11), modal melebihi layar (UX-07), aksi destruktif ganda (UX-23).

**Di luar scope:** katalog 9.800 integrasi, Spaces (app builder), Docs/Pages publik, Referral/Hadiah, iMessage/ponsel/suara. Sanji tetap **chat tim + ruang + topik (Zulip)**; agen adalah anggota tim, bukan pengganti chat.

## 4. Fase (setiap fase = rilis sendiri, test di WSL, deploy bergerbang + rollback)

### Fase V0 — Fondasi desain & bahasa · Risiko LOW
- **AC-V0-001:** The web app shall have shared `PageHeader`, `EmptyState`, and settings-card components used by Needs, Task board, Agents, and new pages.
- **AC-V0-002:** All numbers, money, dates and times shall go through one formatter using the user locale (id-ID: "6,9 rb", "14 Okt", 24-jam WIB).
- **AC-V0-003:** CI/lint shall fail when a UI string has no Indonesian translation (pending files counted), and a glossary file shall fix one name per feature (Ruang/Channel, Kartu/Task, Perlu kamu/Needs you…).

### Fase V1 — Izin per tool & persetujuan · Risiko HIGH (auth) · bersama Rama
- **AC-V1-001:** Each MCP connection shall show its tools with a per-tool policy *Off / Auto / Ask first*, using `McpToolPolicy`; only owners/admins can change them; a "Lock" switch prevents member edits.
- **AC-V1-002:** When an agent wants to run an "Ask first" tool, the system shall create a Needs-you **Approval** with the exact action (tool, target, preview) and run it only after Approve.
- **AC-V1-003:** Sanji's own MCP server write tools (send/DM/edit/delete/create/update) shall follow the same policies for external clients and be advertised with correct destructive hints.
- **AC-V1-004 — Unwanted:** If a tool's policy is Off, then the agent shall not see or call it, and an attempt shall be logged in the audit feed.

### Fase V2 — Halaman Terjadwal + identitas bot · Risiko MEDIUM · bersama Rama
- **AC-V2-001:** A "Scheduled" page shall list every recurring routine (Cek pagi, Evaluasi 4 jam, Rekap harian, pengingat pagi) with owner agent, pattern, next run, last result, and Pause/Resume.
- **AC-V2-002:** Users shall create a routine from templates (Morning brief, End-of-day summary, Weekly digest, Client check) or manually (title, instructions, pattern presets, time 24-h WIB, target room/topic, model).
- **AC-V2-003:** Routine posts shall be sent by a bot identity (e.g. "Sanji Agent"/the agent's own user), never as a human.
- **AC-V2-004:** Each run shall keep a history (start, end, cost, result, link to the post); a failed run shall notify the owner.
- **AC-V2-005 — Unwanted:** If a routine would exceed the budget, then it shall pause and notify instead of running.

### Fase V3 — Panel Status per agen · Risiko MEDIUM
- **AC-V3-001:** Opening an agent (DM or its room) shall show a side panel "Status" with three sections: In progress (running jobs), Waiting on you (approvals/decisions), Decided (recent decisions), built from AgentJob/AgentApproval/Needs.
- **AC-V3-002:** Items shall link to the job drawer, card, or message; the panel can be hidden and remembers the choice.
- **AC-V3-003:** Works at 390px as a sheet, light/dark, keyboard.

### Fase V4 — Playbook bersama (Skill) · Risiko MEDIUM
- **AC-V4-001:** A "Playbooks" page shall list workspace playbooks (Shared) and personal ones (Mine), each a Markdown document rendered safely (no raw Markdown/HTML comments).
- **AC-V4-002:** Editing a shared playbook shall create a new revision with author and diff; agents use the latest revision.
- **AC-V4-003:** Agents' `work_skills` shall reference playbooks by id, and the playbook shows which agents use it and when it was last read.

### Fase V5 — Penggunaan & budget · Risiko MEDIUM
- **AC-V5-001:** A "Usage" page shall show spend per agent, person, routine and model for a period, with names (never raw ids) and id-ID number format.
- **AC-V5-002:** `monthly_budget_microunits` (workspace & agent) shall be enforced: at 80% notify owner, at 100% pause non-urgent work with a clear message.
- **AC-V5-003 — Unwanted:** If usage data is missing for a run, then the UI shall show "unknown" rather than zero.

### Fase V6 — Model default & preset · Risiko LOW–MEDIUM
- **AC-V6-001:** Workspace settings shall offer presets (e.g. Hemat / Seimbang / Cerdas) mapping to provider+model+reasoning, plus an advanced manual choice; the default applies to agents and routines unless overridden.

## 5. Urutan yang disarankan
V0 → V1 → V2 → V3 → V4 → V5 → V6. Alasan: V0 mencegah utang desain/i18n (kesalahan terbesar Viktor); V1 menutup risiko keamanan & bottleneck approval; V2 membuat rutinitas yang paling sering dipakai tim terlihat & bisa dikendalikan; V3–V6 menambah transparansi.

## 6. Risiko & syarat
- Agent platform, runner, dan MCP gateway (WP44) = wilayah Rama → V1, V2, V3, V5 butuh keputusan & review Rama.
- V1 & V5 menyentuh auth/biaya → HIGH; wajib test akses, audit log, dan uji di dev sebelum prod.
- Semua migrasi additive dengan `db_default`; deploy satu fase per rilis.

## 7. Pertanyaan untuk Ari (default = rekomendasi)
1. Urutan fase: **A (rek.)** V0 → V1 → V2 → V3 → V4 → V5 → V6 · B mulai dari V2 (Terjadwal) karena paling terasa · C pilih sendiri.
2. Peran Rama: **A (rek.)** Rama review & ikut memutuskan V1/V2/V3/V5 (aku siapkan plan detail + draf PR per fase) · B aku kerjakan langsung, Rama diberi tahu.
3. Identitas bot rutinitas: **A (rek.)** satu bot "Sanji Agent" · B pakai user agen yang sudah ada (mis. "Opus").
4. Halaman Memory ala Viktor: **A (rek.)** nanti (setelah V5) · B masuk sekarang.
