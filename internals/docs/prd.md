# PRD Grow Team

Status produk: internal, web-first, nama sementara **Grow Team**.

## Masalah dan sasaran

Tim kecil membutuhkan percakapan kerja yang dapat dibaca kembali menurut kanal dan topik, bukan arus chat tunggal. Sasaran awal adalah komunikasi harian yang dapat dipakai lima sampai enam anggota dari browser. Sasaran jangka lanjut adalah fondasi produk B2B, tanpa klaim harga, revenue, ROI, atau tanggal rilis.

## Persona

- **Anggota tim:** membaca, mencari, membalas, dan mengunggah konteks kerja.
- **Administrator organisasi:** mengundang anggota, mengatur peran, kanal, dan kebijakan.
- **Operator platform:** menjaga backup, runtime, ingress, dan pemulihan.

## Ruang lingkup saat ini

Chat, kanal, topik, DM, pencarian, unggah, peran, undangan, reset password, dan email transaksi telah diverifikasi. Aplikasi browser adalah jalur utama. Deploy image fork berbranding Grow Team untuk `team.growc.id` termasuk ruang lingkup aktif dan sedang berjalan; branding runtime belum boleh diklaim selesai.

## Target pilot AI

Sesudah stabilisasi internal dan rilis image fork, Grow Team menargetkan pilot
agent yang terus memantau kanal terkonfigurasi dan menjalankan tugas yang diminta.
Pilot memakai FR-08–FR-18. Ini target inti yang belum diimplementasikan, bukan
fitur yang sudah tersedia atau komitmen komersial.

## Di luar ruang lingkup saat ini

Landing/marketing, billing, paket komersial, aplikasi Grow Team desktop/mobile,
dan migrasi Buzz ke chat aktif. Buzz adalah arsip pemulihan, bukan target produk.

## Metrik provisional

Metrik belum menjadi KPI komersial. Untuk pilot, ukur keberhasilan login/undangan, pengiriman pesan realtime, pencarian, upload-download, delivery email, keberhasilan backup, dan jumlah insiden akses. Tetapkan baseline dan target setelah penggunaan internal stabil.

## Kebutuhan terkait

Traceability: BR-01/BR-02 di [BRD](brd.md) membatasi PR-01 workspace internal,
PR-02 pilot AI, dan PR-03 kesiapan B2B ini. PR-01 memakai FR-01–FR-07 dan FR-20; PR-02
memakai FR-08–FR-18; PR-03 memakai FR-19. Risiko ada di [security](security.md).
