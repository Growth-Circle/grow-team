# 13 · Pesan agen di ruang (chat UX)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat [keputusan rebrand](../sanji-rebrand-decision.md#konflik-halaman-dan-keputusan).

Tanggal: 24 September 2026 · Prototipe: Dashboard → buka ruang `# rilis-v2`, lalu kirim pesan dengan `@kaki` / `@ayame` / `@matcha`.

## Masalah di versi lama (Grow Team, screenshot 21 Sep)

| Gejala                                                                                                                           | Dampak                                                                        |
| -------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Agen mengirim **pesan terpisah untuk tiap status**: "This task is queued.", "The task stopped before it finished.", lalu jawaban | Ruang penuh pesan sistem. 1 permintaan = 2–3 pesan                            |
| **URL mentah** `https://team.growc.id/#agent-jobs/…` di setiap pesan                                                             | Berisik, tidak bisa dipahami, dan membingungkan orang yang tidak paham teknis |
| Status gagal ("stopped") **tanpa alasan dan tanpa aksi**                                                                         | Pengguna tidak tahu harus apa                                                 |
| Balasan dalam bahasa Inggris, padahal pengguna menulis dalam bahasa Indonesia                                                    | Tidak konsisten                                                               |
| Avatar agen berupa ikon robot generik yang sama                                                                                  | Sulit membedakan agen                                                         |

## Prinsip baru

1. **Satu permintaan = satu pesan agen.** Pesan itu berisi **kartu job** yang statusnya _diperbarui di tempat_ (queued → bekerja → selesai), bukan pesan baru.
2. **Pengakuan tanpa pesan:** begitu agen menerima mention, pesan pengguna diberi reaksi **👀 Nama** (≤ 1 detik). Tidak ada pesan "queued".
3. **Tanpa URL mentah.** Link job diganti tombol **Detail →** yang membuka drawer Job (09 §E2).
4. **Gagal harus punya alasan + aksi:** "Berhenti: runner LAPTOP-DITA tidur" + **↻ Coba lagi**.
5. **Hasil = chip artefak** (PR #214, 5 tugas, Draf siap, Lolos ✓), bukan kalimat panjang.
6. Bahasa balasan mengikuti **Pengaturan → Umum → Bahasa agen** (default Indonesia).
7. Avatar agen = **bentuk + warna** unik (lingkaran/cincin/kotak), plus label `AGEN`.

## Anatomi kartu job

```
┌──────────────────────────────────────────────────────┐
│ (●) BEKERJA   Screenshot mobile PR #214     Detail → │  ← status pill · judul · tombol
│ ▓▓▓▓▓▓▓▓▓▓▓░░░░░░░                                    │  ← progress (hanya antre/bekerja)
│ Mengambil screenshot 3/5            [5 gambar] [↻]    │  ← langkah sekarang · artefak/aksi
└──────────────────────────────────────────────────────┘
```

| Status    | Pill                              | Border | Progress                | Footer                                                                        |
| --------- | --------------------------------- | ------ | ----------------------- | ----------------------------------------------------------------------------- |
| `queued`  | ANTRE (abu)                       | abu    | ya, 5%                  | "Antre · posisi N"                                                            |
| `working` | BEKERJA (kuning, titik berdenyut) | tinta  | ya, bergaris beranimasi | nama langkah dari runner                                                      |
| `review`  | MENUNGGU APPROVAL (merah muda)    | tinta  | tidak                   | "Menunggu approval {nama}" + chip artefak. Item juga muncul di **Perlu kamu** |
| `done`    | SELESAI (hijau)                   | abu    | tidak                   | durasi + chip artefak                                                         |
| `failed`  | BERHENTI (merah)                  | merah  | tidak                   | alasan jelas + **↻ Coba lagi**                                                |

## Alur di prototipe

1. Pengguna kirim "@ayame tolong …".
2. **0,3 dtk:** reaksi `👀 Ayame` muncul di pesan pengguna.
3. **0,6 dtk:** pesan Ayame muncul berisi kartu **ANTRE**.
4. **1,5 dtk:** kartu berubah **BEKERJA**, "Membaca konteks ruang", 30%.
5. **2,7 dtk:** "Mengerjakan", 72%.
6. **4 dtk:** teks balasan muncul di atas kartu, kartu **SELESAI** + chip "Draf siap".
7. Job juga masuk ke **Agen sedang kerja** di Hari ini.

- Klik **↻ Coba lagi** pada kartu BERHENTI → kartu kembali bekerja lalu selesai (tanpa pesan baru).

## Kebutuhan backend

| Kebutuhan          | Detail                                                                                                                                                                                                                                            |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Satu pesan per job | Saat admission berhasil, broker membuat **satu** pesan bot dengan `sanji_job_id` di metadata pesan. Update status memakai **edit pesan** Zulip (`PATCH /messages/{id}`) atau event khusus `sanji_job_card`, **bukan** pesan baru                  |
| Reaksi pengakuan   | Setelah admission: `POST /messages/{id}/reactions` dengan emoji `eyes` atas nama bot. Jika admission ditolak: reaksi 🚫 + pesan ephemeral ke pengirim saja, berisi alasan                                                                         |
| Render kartu       | Frontend mendeteksi `sanji_job_id` dan merender kartu dari state job (realtime `sanji_job_progress`), sehingga teks pesan tidak perlu diedit tiap detik. Fallback untuk klien lain (email, mobile lama): teks singkat "Ayame · Selesai · PR #214" |
| Alasan gagal       | Runner mengirim `failure_code` + pesan manusiawi: `runner_offline`, `budget_exceeded`, `grant_revoked`, `timeout`, `tool_error`. Mapping teks Indonesia ada di frontend                                                                           |
| Coba lagi          | `POST /jobs/{id}/retry` → attempt baru pada job yang sama ([spec lifecycle §12.3](../spec/2026-09-21-agent-lifecycle-and-mention-flow.md#123-resume)), kartu yang sama diperbarui                                                                 |
| Artefak            | `JobArtifact` (`type`: pr/file/task/check/draft, `label`, `url`), ditampilkan sebagai chip. Chip PR/file membuka tujuan, chip tugas membuka drawer Tugas                                                                                          |
| Balasan teks       | Hanya satu balasan akhir di pesan yang sama. Pertanyaan lanjutan dari agen memakai `request_decision` (masuk **Perlu kamu**), bukan pesan baru                                                                                                    |
| Anti-loop          | Pesan bot tidak memicu agen lain ([PRD](../prd.md)). Kartu job tidak mengandung mention                                                                                                                                                           |

## Pertanyaan terbuka

- Edit pesan Zulip memicu notifikasi "edited". Perlu flag agar edit oleh broker tidak menampilkan label "diedit"?
- Balasan panjang (> 1.500 karakter) sebaiknya jadi file Drive/doc + ringkasan 3 baris di pesan?
