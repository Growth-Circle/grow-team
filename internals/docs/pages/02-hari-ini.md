# 02 · Hari ini (home)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

## Tujuan
Layar pertama setelah login. Menjawab tiga pertanyaan dalam 10 detik: **apa yang butuh aku, apa yang sedang dikerjakan agen, dan apa jadwal hari ini.**

## Kenapa
- Riset menunjukkan sekitar 60% waktu kerja habis untuk "work about work" (mengejar status, pindah aplikasi). Halaman ini mengumpulkan semuanya di satu tempat. Lihat [riset 01](../research/01-kebutuhan-kerja-pekerja.md).
- Kotak "Mau bikin apa?" menghapus langkah "pilih kanal dulu". Pengguna cukup menulis brief, dan Kaki yang menaruhnya di ruang yang tepat.

## Isi layar
1. **Header:** tanggal, sapaan, jumlah item yang perlu ditangani ("3 hal perlu kamu" / "Semua beres"), chip integrasi aktif (Drive, Fathom, WhatsApp).
2. **Kotak "Mau bikin apa?":**
   - Input brief bebas.
   - Pilihan jenis kerja: Presentasi / Riset / Laporan / Notulen / Lainnya.
   - Petunjuk tujuan ("→ # riset-churn" atau "ruang baru: proyek-deck").
   - Tombol "Serahkan ke Kaki".
3. **Perlu kamu (ringkas):** daftar yang sama dengan [03](03-perlu-kamu.md), tanpa filter.
4. **Ringkasan ruang aktif:** kartu per ruang berisi 3 baris (Keputusan, Blocker, Menunggu siapa) plus info meta dan link "Buka ruang".
5. **Agen sedang kerja:** job aktif dengan bentuk agen, nama tugas, progress, langkah, dan ruang.
6. **Rapat hari ini:** dari kalender + Fathom. Rapat yang sudah selesai menampilkan "Notulen siap → N tugas".
7. **Rapikan ruang:** jumlah ruang sepi + tombol "Arsipkan N ruang" / "Lihat dulu".
8. **Toast konfirmasi** setelah setiap aksi.

## Interaksi
| Aksi | Hasil |
|---|---|
| Kirim brief | Job baru untuk Kaki muncul di "Agen sedang kerja" + toast yang menyebut ruang tujuan |
| Approve / Kirim / Pilih A | Item hilang dari daftar, pemberi tugas dikabari, badge berkurang |
| Lihat / Balas | Membuka ruang terkait |
| Buka ruang (kartu ringkasan) | Membuka [04 Ruang](04-ruang.md) |
| Arsipkan ruang sepi | Ruang diarsip, sidebar mengecil |

## Kebutuhan backend

### Data
| Entitas | Field | Catatan |
|---|---|---|
| `Brief` (baru) | `id`, `realm`, `author`, `text`, `kind` (deck/research/report/notes/other), `target_room_id` (nullable), `created_job_id` | Input awal sebelum menjadi job |
| `RoomDigest` (baru) | `room_id`, `date`, `decided`, `blocker`, `waiting_on_user_ids[]`, `message_count`, `generated_by_job_id` | Satu baris per ruang per hari |
| `AgentJob` | Mengikuti spec lifecycle. Butuh field tampilan: `title`, `step_label`, `progress` (0–1), `room_id` | Progress dilaporkan runner |
| `Meeting` (baru, dari integrasi) | `external_id`, `provider` (fathom), `title`, `start`, `platform` (meet/zoom), `summary`, `action_items[]`, `created_task_ids[]` | Lihat bagian Integrasi Fathom |

### API
| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/api/v1/sanji/home` | Agregat: needs (5 teratas), digests, jobs aktif, meetings hari ini, jumlah ruang sepi |
| POST | `/api/v1/sanji/briefs` | Buat brief → routing oleh Kaki → buat job. Response berisi ruang tujuan untuk toast |
| POST | `/api/v1/sanji/briefs/route-preview` | Opsional: tebakan ruang tujuan saat pengguna mengetik/memilih jenis |

### Routing brief (logika Kaki)
1. Jika brief menyebut ruang/klien yang ada, arahkan ke ruang itu.
2. Jika jenisnya Presentasi/Laporan untuk proyek baru, usulkan **ruang Proyek baru** dengan `due_date`.
3. Jika ragu, kirim ke DM pengguna dan minta konfirmasi. Jangan menebak diam-diam.

### Event realtime
- `sanji_job_progress`, `sanji_need_created`, `sanji_need_resolved`, `sanji_digest_ready`, `sanji_meeting_processed`.

### Integrasi Fathom
- Webhook "meeting selesai" → simpan `Meeting` → job Kaki: ubah action item jadi `Task` (PIC, deadline, ruang) → arsipkan notulen ke folder Drive ruang.
- ⚠️ Endpoint dan payload webhook Fathom harus divalidasi di dokumentasi developer resmi sebelum dibangun.

### Izin
- **Ringkasan ruang wajib opt-in per ruang** (`RoomMeta.summary_enabled`), karena PRD Grow Team menyatakan pilot tidak melakukan pemantauan umum. Hanya pemilik ruang yang boleh menyalakannya.
- Brief hanya boleh diarahkan ke ruang yang bisa diakses penulisnya.

## Pertanyaan terbuka
- Jam berapa digest dibuat? Usulan: 07.00 waktu realm.
- Batas biaya model untuk digest harian per workspace?
