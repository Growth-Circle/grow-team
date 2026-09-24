# Spesifikasi Latensi dan Keandalan Agent

Tanggal: 2026-09-24, Asia/Jakarta.

Status: **kontrak baru; belum diimplementasikan. Semua baris LR belum diuji.**

Spesifikasi induk: [jalur cepat](2026-09-24-agent-fast-lane.md).

## 1. Tujuan

Spesifikasi ini menetapkan target waktu, batas kegagalan, cara mengukur, dan cara
menjaga agent tetap tersedia saat rilis.

## 2. Bukti kegagalan (produksi, 22–24 September 2026)

Dari 15 job, 8 selesai dan 7 gagal:

| Pola                                   | Jumlah | Linimasa (detik dari `job.queued`)                                   | Temuan |
| -------------------------------------- | ------ | -------------------------------------------------------------------- | ------ |
| `start_failed`                         | 3      | `attempt.starting` 1–168 s, lalu `attempt.stopped` 13–142 s kemudian | Container model gagal mulai |
| `runtime_stopped` sesudah `context.read` | 3    | `tool.finished` 16–32 s, lalu diam, lalu `attempt.stopped` 20–391 s kemudian | Jurnal runner tidak mencatat reservasi model. Loop berhenti sebelum memanggil model |
| Dibatalkan sesudah menunggu            | 2      | Belum mulai sesudah 113 s, atau diam 391 s sesudah membaca konteks    | Pemberi perintah menyerah |

Kejadian lain:

- 2026-09-24 15:13 WIB: runner dihentikan untuk rilis 25 dan tetap mati lebih dari
  1 jam. Selama itu semua mention tidak dijawab dan tidak ada pesan ke pengguna.

Kesimpulan: kegagalan datang dari start container, dari loop adapter yang berhenti
sebelum memanggil model, dan dari runner yang tidak tersedia. Tidak satu pun berasal
dari model.

## 3. Target layanan

Ukur di produksi selama 7 hari bergulir, hanya untuk job jalur cepat.

| Ukuran                                             | p50      | p95      |
| -------------------------------------------------- | -------- | -------- |
| Pesan sampai indikator mengetik                    | ≤ 1 s    | ≤ 2 s    |
| Pesan sampai teks jawaban pertama tampil           | ≤ 5 s    | ≤ 10 s   |
| Pesan sampai jawaban final, `answer` tanpa alat    | ≤ 12 s   | ≤ 25 s   |
| Pesan sampai jawaban final, `answer` dengan alat   | ≤ 20 s   | ≤ 45 s   |
| `result.prepared` sampai hasil final tampil        | ≤ 1 s    | ≤ 3 s    |

Batas kegagalan:

| Ukuran                                                 | Batas            |
| ------------------------------------------------------ | ---------------- |
| Job jalur cepat yang gagal karena sistem               | ≤ 2 %            |
| Job yang gagal tanpa pesan ke pemberi perintah         | 0                |
| Menit runner tidak tersedia per rilis                  | ≤ 5 menit        |
| Mention saat runner offline tanpa balasan dalam 5 s    | 0                |

"Gagal karena sistem" tidak menghitung batal oleh pengguna, penolakan model, dan
penolakan izin.

## 4. Pengukuran

### 4.1 Titik waktu per job

Server menyimpan titik waktu ini pada `AgentAttempt`:

| Field                | Diisi saat                                         |
| -------------------- | -------------------------------------------------- |
| `message_sent_at`    | Pesan pemicu tersimpan                             |
| `queued_at`          | Job masuk antrean                                  |
| `typing_sent_at`     | Indikator mengetik pertama dikirim                 |
| `claimed_at`         | Claim berhasil                                     |
| `model_request_at`   | Runner mengirim request model pertama              |
| `first_delta_at`     | Runner menerima delta teks pertama                 |
| `first_draft_at`     | Pesan draft pertama dibuat                         |
| `prepared_at`        | `result.prepared` diterima                         |
| `published_at`       | Hasil final tampil                                 |
| `stopped_at`         | `attempt.stopped` diterima                         |

Runner mengirim `model_request_at` dan `first_delta_at` sebagai field event. Server
mengisi field lain dari jamnya sendiri.

### 4.2 Pemakaian

Per attempt: token input, token output, token baca cache, token tulis cache, jumlah
panggilan alat, total waktu alat, jumlah retry, dan `failure_code`. Tidak ada isi prompt,
isi jawaban, atau argumen alat.

### 4.3 Dasbor dan laporan

1. Halaman admin "Agent performance" menampilkan p50 dan p95 per ukuran bagian 3,
   jumlah job, persentase gagal, dan rasio cache.
2. Detail job di panel tugas menampilkan waktu per tahap untuk pemilik profil dan admin.
3. Laporan mingguan otomatis ke admin realm berisi ukuran yang melewati target.

### 4.4 Probe sintetis

1. Server membuat satu job `answer` uji setiap 15 menit untuk setiap profil yang aktif.
2. Job uji memakai topik privat milik sistem dan tidak mengirim notifikasi.
3. Probe mengukur waktu end-to-end dan mencatatnya terpisah dari job nyata.
4. Dua probe gagal berturut-turut mengirim peringatan ke pemilik profil.

## 5. Ketersediaan runner

### 5.1 Status runner

1. Runner mengirim heartbeat setiap 15 s. Server menandai runner `offline` sesudah
   45 s tanpa heartbeat.
2. Admission memeriksa status runner. Bila `offline`, server tetap membuat job dan
   langsung mengirim teks "offline" (spesifikasi streaming bagian 5).
3. Status yang tidak dapat diperiksa tampil sebagai `unknown`, bukan `offline`.
4. Runner yang offline lebih dari 5 menit mengirim peringatan ke pemilik profil.

### 5.2 Drain dan restart

1. Runner punya perintah `drain`: berhenti mengklaim, menyelesaikan job jalur cepat yang
   aktif, lalu keluar. Batas drain adalah 3 menit.
2. Job jalur code yang aktif saat drain berhenti dengan aturan batal yang ada.
3. Systemd memulai ulang runner segera sesudah keluar, kecuali operator menghentikannya
   dengan sengaja.

### 5.3 Prosedur rilis

1. Rilis yang tidak mengubah skema protokol tidak menghentikan runner.
2. Rilis yang mengubah skema protokol memakai urutan: drain runner, swap server, pasang
   runner baru, start runner, cek status `online` dan kesiapan profil.
3. Semua langkah pada nomor 2 berjalan dalam satu jendela kerja. Runner tidak boleh
   tetap mati sesudah server swap selesai.
4. Bila runner baru gagal start, operator memasang lagi runner lama yang kompatibel
   atau mengembalikan server dalam 10 menit.
5. Prosedur rilis mencatat waktu runner mati. Nilai di atas 5 menit adalah insiden.

## 6. Retry dan batas penyedia

1. Runner melakukan retry hanya untuk 408, 409, 429, 5xx, dan error koneksi, maks 2 kali,
   dengan jeda eksponensial dan `retry-after`.
2. Runner tidak melakukan retry sesudah delta pertama tampil di chat.
3. Sesudah retry habis, job berakhir dengan teks "busy" atau "stopped responding".
4. Penolakan model (`refusal`) tidak di-retry dengan model lain secara diam-diam.

## 7. Akar masalah yang wajib ditutup sebelum F5

| Nomor | Masalah                                                        | Tindakan                                                              |
| ----- | -------------------------------------------------------------- | --------------------------------------------------------------------- |
| 1     | Loop adapter berhenti sesudah `context.read` tanpa memanggil model | Tulis tes regresi dari 4 job dengan pola ini: 3 `runtime_stopped` dan `a6ee760d`. Jalur cepat tidak memakai loop ini, tetapi jalur code masih memakainya |
| 2     | Container model gagal mulai (`start_failed`)                   | Catat penyebab start di jurnal. Jalur cepat tidak memakai container   |
| 3     | Job tidak mulai selama 113–168 s                               | Long-poll `/runner/wake` dan kapasitas per jalur                     |
| 4     | Runner mati lebih dari 1 jam saat rilis                        | Prosedur rilis bagian 5.3 dan peringatan bagian 5.1                   |
| 5     | Gagal tanpa pesan ke pengguna                                   | Teks kegagalan spesifikasi streaming bagian 5                         |

## 8. Kriteria penerimaan (LR)

| ID    | Kriteria                                                                                  |
| ----- | ----------------------------------------------------------------------------------------- |
| LR-01 | Setiap job menyimpan semua titik waktu bagian 4.1 yang berlaku untuk jalurnya.            |
| LR-02 | Dasbor menampilkan p50 dan p95 per ukuran bagian 3 dari data 7 hari.                      |
| LR-03 | Probe sintetis berjalan setiap 15 menit dan tidak mengirim notifikasi.                    |
| LR-04 | Dua probe gagal berturut-turut mengirim peringatan ke pemilik profil.                     |
| LR-05 | Runner tanpa heartbeat 45 s tampil `offline`. Status yang gagal diperiksa tampil `unknown`. |
| LR-06 | Mention saat runner offline mendapat teks dalam 5 s, dan job berjalan saat runner kembali. |
| LR-07 | `drain` menyelesaikan job jalur cepat aktif dalam 3 menit tanpa klaim baru.               |
| LR-08 | Rilis tanpa perubahan skema protokol tidak menghentikan runner.                           |
| LR-09 | Retry tidak terjadi sesudah delta pertama tampil.                                        |
| LR-10 | Metrik tidak memuat isi prompt, isi jawaban, argumen alat, atau kunci.                    |
| LR-11 | Target bagian 3 tercapai 7 hari berturut-turut sebelum fase F5.                          |
