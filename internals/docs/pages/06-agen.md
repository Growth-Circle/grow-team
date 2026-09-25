# 06 · Agen

Status: spesifikasi halaman, belum kontrak. Konflik: lihat [keputusan rebrand](../sanji-rebrand-decision.md#konflik-halaman-dan-keputusan).

## Tujuan

Mengelola agen di workspace: melihat siapa saja agennya, apa perannya, aksesnya, performanya, dan **semua aksi yang sudah dilakukan**.

## Kenapa

Kepercayaan pada agen datang dari transparansi. Pengguna harus bisa menjeda agen dan membaca log tanpa bertanya ke admin.

## Isi layar

- Header: nama workspace, runner aktif ("LAPTOP-DITA · ONLINE"), tombol "+ Buat agen".
- Kartu agen (Kaki, Ayame, Matcha, dan agen buatan pengguna):
  - Bentuk + warna, nama, peran (PERENCANA/PEMBANGUN/PENINJAU), deskripsi.
  - Statistik: tugas per minggu, % di-approve, model.
  - Chip akses (ruang, Drive, GitHub, Kalender, Fathom).
  - Toggle **Aktif / Jeda**, dan status saat ini.
- **Log aktivitas:** waktu, agen, aksi, hasil (DITULIS/REVIEW/MENUNGGU/SELESAI/DIBACA).

## Kebutuhan backend

### Data

| Entitas                                      | Field                                                                                                                                                                                                                    | Catatan                                                          |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| `AgentProfile`                               | Mengikuti [spec settings & connections](../spec/2026-09-22-agent-settings-connections-and-team-defaults.md). Tambahan: `role` (planner/builder/reviewer/custom), `avatar_shape`, `avatar_color`, `description`, `paused` | Bentuk & warna bisa diedit pengguna                              |
| `AgentGrant`                                 | Akses ruang, koneksi (Drive, GitHub, Kalender, Fathom), mode baca/tulis                                                                                                                                                  | Sumber chip akses                                                |
| `AgentAuditEvent` (perluasan journal runner) | `agent_id`, `job_id`, `action`, `target` (file/PR/ruang), `result`, `at`                                                                                                                                                 | **Semua** tool call yang menyentuh data eksternal wajib tercatat |

### Statistik

- `tasks_per_week` = jumlah `Task` dengan `assignee=agent` yang selesai dalam 7 hari.
- `approve_rate` = approval diterima / total approval yang diminta (30 hari).
- Dihitung dengan job agregasi (bisa memakai modul `analytics` Zulip).

### API

| Method | Endpoint                                   | Fungsi                                                 |
| ------ | ------------------------------------------ | ------------------------------------------------------ |
| GET    | `/api/v1/sanji/agents`                     | Daftar + statistik + status sekarang + runner          |
| POST   | `/api/v1/sanji/agents`                     | Buat agen (form: nama, peran, instruksi, model, akses) |
| PATCH  | `/api/v1/sanji/agents/{id}`                | Ubah, termasuk `paused`                                |
| GET    | `/api/v1/sanji/agents/audit?agent=&limit=` | Log aktivitas                                          |

### Perilaku "Jeda"

- Agen yang dijeda **tidak menerima trigger baru**. Mention ke agen tersebut dibalas oleh supervisor: "Agen sedang dijeda".
- Job yang sedang berjalan: selesaikan attempt saat ini lalu berhenti, atau batalkan dengan aman. Ikuti [spec lifecycle §12.4](../spec/2026-09-21-agent-lifecycle-and-mention-flow.md#124-edit-pause-dan-arsip-profil) lewat `POST /agent/profiles/{id}/pause`.

### Izin

- Hanya pemilik agen atau Admin yang boleh mengubah/menjeda agen.
- Log hanya menampilkan aksi pada ruang yang bisa diakses oleh orang yang melihat.

## Pertanyaan terbuka

- Model yang tampil ("Claude", "Codex") berasal dari koneksi model atau harness ACP? Perlu satu label yang konsisten.
