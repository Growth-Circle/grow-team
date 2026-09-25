# 05 · Tugas (papan)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat [keputusan rebrand](../sanji-rebrand-decision.md#konflik-halaman-dan-keputusan).

## Tujuan

Semua tugas manusia **dan** agen dari seluruh ruang dalam satu papan, supaya pertanyaan "siapa ngerjain apa, sudah sampai mana?" terjawab tanpa bertanya di chat.

## Kenapa

Update status adalah bagian terbesar dari "work about work". Jika tugas manusia dan agen berada di papan yang sama, tim tidak perlu memakai tracker terpisah.

## Isi layar

- Header "Tugas · Semua ruang · Minggu ini".
- Filter: Semua / Punyaku / Agen.
- 4 kolom: **Belum → Dikerjakan → Review → Selesai**, dengan jumlah di tiap kolom.
- Kartu: judul, `# ruang`, deadline (merah muda jika hari ini atau berupa jam), bentuk/inisial PIC + nama, dan tombol maju ("Mulai →", "Ke review →", "Selesai ✓").
- Badge "Tugas" di sidebar = tugas milik pengguna yang belum Selesai.

## Kebutuhan backend

### Data: `Task` (baru)

| Field                                           | Keterangan                                            |
| ----------------------------------------------- | ----------------------------------------------------- |
| `id`, `realm_id`, `room_id`, `topic` (opsional) | Lokasi                                                |
| `title`, `description`                          |                                                       |
| `assignee_type` (`user`/`agent`), `assignee_id` | PIC                                                   |
| `status`                                        | `todo` / `doing` / `review` / `done`                  |
| `due_at`                                        | Nullable                                              |
| `source`                                        | `manual` / `brief` / `meeting` / `whatsapp` / `agent` |
| `source_ref`                                    | ID brief/meeting/pesan                                |
| `agent_job_id`                                  | Jika dikerjakan agen                                  |
| `created_by`, `created_at`, `updated_at`        | Audit                                                 |

### Aturan status

- Tugas agen: status **mengikuti job**. `doing` saat attempt berjalan, `review` saat `awaiting_approval`, `done` setelah approval diterima dan hasil terbit. Pengguna tidak bisa menggeser tugas agen ke Selesai tanpa approval.
- Tugas manusia: bebas digeser oleh PIC atau pemilik ruang.
- Setiap perubahan status mengirim pesan singkat ke topik ruang terkait (bisa dimatikan).

### API

| Method | Endpoint                                                  | Fungsi                                               |
| ------ | --------------------------------------------------------- | ---------------------------------------------------- |
| GET    | `/api/v1/sanji/tasks?filter=all\|mine\|agents&range=week` | Daftar per kolom                                     |
| POST   | `/api/v1/sanji/tasks`                                     | Buat manual                                          |
| PATCH  | `/api/v1/sanji/tasks/{id}`                                | Ubah status/PIC/deadline (divalidasi aturan di atas) |

### Event realtime

`sanji_task_created`, `sanji_task_updated`.

## Pertanyaan terbuka

- Drag-and-drop: keduanya. Desktop memakai drag HTML5, layar sentuh memakai tombol maju (Q-08).
- Sinkronisasi dua arah dengan tracker eksternal (Linear/Jira)? Belum masuk MVP.
