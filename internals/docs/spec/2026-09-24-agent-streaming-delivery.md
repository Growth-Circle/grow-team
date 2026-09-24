# Spesifikasi Pengiriman Jawaban Agent secara Streaming

Tanggal: 2026-09-24, Asia/Jakarta.

Status: **kontrak baru; belum diimplementasikan. Semua baris SD belum diuji.**

Spesifikasi induk: [jalur cepat](2026-09-24-agent-fast-lane.md). Spesifikasi ini
mengatur apa yang orang lihat di chat dan di panel tugas selama agent bekerja.

## 1. Tujuan

1. Orang tahu dalam 1 detik bahwa agent sudah menerima mention.
2. Jawaban muncul sedikit demi sedikit di satu pesan, bukan sesudah semuanya selesai.
3. Setiap kegagalan menghasilkan pesan yang jelas di topik yang sama.
4. Streaming tidak menambah pesan, notifikasi, atau riwayat edit yang mengganggu.

## 2. Urutan tampilan

| Waktu                          | Yang orang lihat di topik                         | Yang terjadi di server                          |
| ------------------------------ | ------------------------------------------------- | ----------------------------------------------- |
| Admission menerima job         | Indikator "Agent sedang mengetik"                 | Server mengirim notifikasi typing atas nama bot |
| Setiap 10 s sebelum draft ada  | Indikator tetap tampil                            | Server memperbarui notifikasi typing            |
| Snapshot draft pertama         | Satu pesan bot baru dengan teks awal dan penanda menulis | Server membuat pesan draft                |
| Snapshot berikutnya            | Teks pesan bertambah                              | Server mengedit pesan draft, maks 1 kali per detik |
| `result.prepared`              | Teks final, penanda menulis hilang                | Server mengedit pesan draft menjadi hasil final |
| Gagal atau batal               | Teks penjelasan di pesan yang sama, atau pesan baru bila draft belum ada | Server menulis teks kegagalan |

## 3. Pesan draft

### 3.1 Pembuatan

1. Server membuat pesan draft saat menerima snapshot pertama yang lolos cek audiens.
2. Pengirim adalah bot profil. Tujuan adalah topik asal, atau DM asal.
3. Isi pesan adalah teks snapshot, lalu penanda menulis di baris terakhir.
4. Server mencatat `result_message_id` pada job saat pesan draft dibuat. Kunci
   pengiriman tetap `result:{job_id}`. Tidak ada pesan hasil kedua.
5. Bila `result.prepared` datang sebelum snapshot mana pun, server langsung mengirim
   hasil final sebagai pesan biasa.

### 3.2 Edit

1. Server mengedit pesan draft dengan jalur internal publisher. Edit ini bukan edit
   pengguna.
2. Edit draft tidak menambah riwayat edit dan tidak menampilkan label "EDITED".
3. Server membatasi edit maks 1 kali per detik per pesan. Snapshot yang datang lebih
   cepat menggantikan snapshot yang menunggu.
4. Server tidak mengedit dengan snapshot yang `draft_seq`-nya lebih kecil dari yang
   sudah tampil.
5. Server menyaring rahasia dan merender Markdown sebelum setiap edit.
6. Markdown yang belum lengkap (misalnya blok kode tanpa penutup) ditutup sementara
   untuk render. Teks aslinya tidak diubah.

### 3.3 Notifikasi dan unread

1. Pesan draft masuk ke unread seperti pesan bot biasa.
2. Notifikasi push dan email untuk pesan draft dikirim sesudah hasil final, satu kali.
3. Mention di teks draft dirender sebagai mention diam. Mention biasa berlaku pada hasil
   final, dan notifikasinya dikirim satu kali.
4. Pesan draft dan edit-nya tidak memicu agent lain.

### 3.4 Hasil final

1. Server mengedit pesan draft dengan teks final dan menghapus penanda menulis.
2. Server menjalankan cek audiens, cek rahasia, dan batas panjang pesan sebelum edit final.
3. Bila teks final lebih panjang dari batas pesan, server memotong dengan penanda
   potong dan menaruh teks lengkap sebagai artefak di panel tugas.
4. Sesudah edit final, job menjadi `completed`.

## 4. Panel tugas

1. Panel tugas menerima status lewat event server secara real-time, bukan polling 5 s.
2. Panel menampilkan fase: menunggu, membaca konteks, menulis, memakai alat, selesai.
   Fase berasal dari event, bukan dari timer.
3. Panel menampilkan teks draft terakhir untuk orang yang boleh melihat job.
4. Panel tidak menampilkan thinking, argumen alat mentah, atau isi paket konteks.
5. Untuk admin, panel menampilkan waktu per tahap (spesifikasi latensi bagian 4).

## 5. Kegagalan, batal, dan agent tidak siap

Teks mengikuti aturan salinan: tanpa detail internal, satu kalimat, lalu jalan keluar.
Teks bawaan berbahasa Inggris dan diterjemahkan.

| Keadaan                                   | Teks untuk orang di topik                                                   | Aksi             |
| ----------------------------------------- | --------------------------------------------------------------------------- | ---------------- |
| Runner pemilik offline saat mention       | "{agent} is offline right now. Your request will start when it is back."     | Job tetap antre  |
| Job belum mulai sesudah 60 s              | "{agent} has not started yet. It will reply here when it does."              | Job tetap antre  |
| Antrean penuh                             | "{agent} has too many requests right now. Try again in a few minutes."       | Job ditolak      |
| Model tidak menjawab (idle timeout)       | "{agent} stopped responding. Ask again to retry."                            | Tombol Retry     |
| Batas penyedia model (429 setelah retry)  | "{agent} is busy right now. Ask again in a minute."                          | Tombol Retry     |
| Model menolak (`refusal`)                 | "{agent} can't help with this request."                                      | —                |
| Jawaban terpotong (`max_tokens`)          | Teks draft tetap, lalu "The answer was cut short. Ask for the rest."         | —                |
| Batal oleh pemberi perintah               | Teks draft tetap, lalu "Stopped."                                            | —                |
| Audiens berubah di tengah jawaban         | Pesan draft disembunyikan. Pemberi perintah menerima hasil secara privat.    | AT-23            |
| Kegagalan lain                            | "{agent} couldn't finish this. Ask again to retry."                          | Tombol Retry     |

Aturan:

1. Setiap kegagalan menghasilkan teks dalam 30 s sesudah kejadian (FL-30).
2. Bila pesan draft sudah ada, teks kegagalan menjadi baris terakhir pesan itu.
   Bila belum ada, server mengirim satu pesan bot baru.
3. Teks tidak menyebut endpoint, kode error, model, runner, container, atau kunci.
4. Detail teknis hanya ada di panel tugas untuk pemilik profil dan admin.
5. Tombol Retry membuat job baru dengan `follows_job_id`. Tombol hanya tampil untuk
   pemberi perintah.

## 6. Penanda menulis

1. Penanda menulis adalah satu baris miring di akhir pesan: "*{agent} is writing…*".
2. Penanda tidak masuk ke teks hasil, salinan, atau kutipan.
3. Klien tanpa frontend Grow tetap melihat penanda sebagai teks biasa. Hasil final
   menghapusnya.

## 7. Kriteria penerimaan (SD)

| ID    | Kriteria                                                                                     |
| ----- | -------------------------------------------------------------------------------------------- |
| SD-01 | Indikator mengetik tampil paling lambat 1 s sesudah admission menerima job.                   |
| SD-02 | Indikator mengetik tetap tampil sampai pesan draft ada atau job berakhir.                     |
| SD-03 | Satu job menghasilkan tepat satu pesan bot di topik, termasuk saat snapshot diulang.          |
| SD-04 | Edit draft maks 1 kali per detik per pesan.                                                  |
| SD-05 | Edit draft tidak menambah riwayat edit dan tidak menampilkan "EDITED".                       |
| SD-06 | Notifikasi push dan email untuk jawaban agent dikirim satu kali, sesudah hasil final.        |
| SD-07 | Mention di teks draft tidak mengirim notifikasi. Mention di hasil final mengirim satu kali.   |
| SD-08 | Markdown yang belum lengkap di draft tidak merusak tampilan pesan lain.                      |
| SD-09 | Setiap keadaan pada tabel bagian 5 menghasilkan teksnya dalam 30 s.                          |
| SD-10 | Teks kegagalan tidak memuat detail internal (tes grep salinan).                              |
| SD-11 | Panel tugas memperbarui fase tanpa polling.                                                  |
| SD-12 | Panel tugas tidak menampilkan thinking, argumen alat mentah, atau isi paket konteks.         |
| SD-13 | Mention saat runner offline langsung mendapat teks "offline", dan job berjalan saat runner kembali. |
