# Perbaikan identitas bot dan Linkifiers Grow Team

## Permintaan dan batas

Alamat bot sistem dan contoh Linkifiers masih menampilkan identitas upstream.
Ganti alamat bot dengan domain `team.growc.id` dan contoh repo dengan `Growth-Circle/grow-team`.
Pertahankan ID bot, pemilik, API key, izin, riwayat pesan, dan data anggota.
Jangan mengubah layanan Hermes, lisensi upstream, atau identifier protokol.
Commit dan push tetap termasuk cakupan yang sudah diminta pengguna.

## Tugas

1. Periksa contoh Linkifiers pada template, katalog bahasa, dan runtime browser.
   Perbaiki sumber contoh serta terjemahan aktif yang terkait.
2. Selaraskan konfigurasi domain bot dengan pembuatan dan lookup bot lintas realm.
   Buat prosedur perubahan alamat terjaga yang menolak benturan atau akun yang tidak sesuai.
   Sediakan verifikasi tanpa perubahan serta pemulihan dengan ID akun yang sama.
3. Tinjau perubahan, jalankan pengujian relevan, dan bangun image baru.
4. Backup, terapkan hanya ke Grow Team, lalu periksa bot dan Linkifiers melalui browser serta API.
5. Catat bukti, commit, push, dan cocokkan SHA lokal dengan remote.

## Kriteria penerimaan

- Ketiga bot yang terlihat memakai `@team.growc.id`.
- Avatar Notification Bot memakai simbol Grow Team.
- Daftar bot sistem, lookup notifikasi/welcome/email, serta alamat tersimpan konsisten.
- ID bot, API key, izin, dan hash pesan sama dengan baseline sebelum perbaikan.
- Linkifiers tidak menampilkan contoh repo `zulip/zulip`.
- Perubahan konfigurasi tidak membuat bot duplikat saat startup ulang.
- Login, tampilan pesan, event queue, dan akses tanpa autentikasi tetap sesuai.
- Backup dan langkah pemulihan tersedia; Hermes tidak berubah.
