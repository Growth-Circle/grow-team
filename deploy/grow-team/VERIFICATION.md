# Verifikasi deployment Grow Team

Tanggal: 21 September 2026.

## Hasil

- Domain publik `https://team.growc.id/login/` mengembalikan HTTP 200.
- Login browser dengan akun pemilik berhasil.
- Aplikasi memuat tanpa error JavaScript pada pemeriksaan browser.
- Pengiriman pesan di channel `general` berhasil; event langsung memuat ID pesan yang sama.
- Upload dan download berkas teks menghasilkan isi yang sama.
- Halaman undangan anggota terbuka tanpa login dan menyediakan form email.
- Tautan undangan dibuat untuk role member dengan masa berlaku tujuh hari.
- Akses profil tanpa autentikasi ditolak dengan HTTP 401.
- Form reset password berhasil menuju halaman konfirmasi.
- Cloudflare mencatat email reset password sebagai `delivered` pada `2026-09-21T12:17:19Z`.
- Email uji dan notifikasi login juga memiliki status `delivered`.
- Endpoint Worker menolak permintaan tanpa secret dengan HTTP 401.
- Lima test Worker dan empat test backend Django lulus.
- Syntax shell dan kompilasi modul Python lulus.
- Semua container berjalan; health check aplikasi berstatus healthy.
- Service Docker, aplikasi, Cloudflare Tunnel, dan koneksi privat Wulan aktif.
- Endpoint model privat menolak permintaan tanpa key dengan HTTP 401.

## Pemulihan

Backup lengkap berada di `/var/backups/grow-team/20260921T121758Z-zulip`.
Checksum arsip database/uploads dan operasi lulus.
Database backup dipulihkan dengan `pg_restore --exit-on-error` ke database sementara.
Pesan pemeriksaan dengan ID 14 ditemukan pada hasil pemulihan.
Database sementara kemudian dihapus; database aktif tetap digunakan aplikasi.

Backup Zulip dan arsip Buzz disalin ke komputer Rama di `~/.local/share/grow-team/backups`.
Checksum salinan diperiksa lagi setelah transfer.
Backup harian berikutnya dijadwalkan sekitar 04.15–04.20 WIB melalui `grow-team-backup.timer`.
Pemulihan seluruh stack pada VPS kedua belum diuji.

## HermesTrading dan Buzz

Sebanyak 56 file unit dan drop-in HermesTrading sama dengan hash sebelum migrasi.
Timer HermesTrading yang aktif sebelumnya tetap tersedia.
Pemeriksaan service ops-watch, public-sync, dan exit-caretaker menunjukkan eksekusi berlanjut setelah deployment.
Tidak ada perubahan aplikasi atau database HermesTrading dalam pekerjaan ini.

Container Buzz dan agent lama sudah dihentikan dan dilepas.
Engine Docker rootless dan connector user lama sudah dinonaktifkan.
Home, konfigurasi, dan runtime lama dipindahkan ke arsip pemulihan Buzz.
Koneksi privat Wulan menggunakan unit `grow-team-ai-tunnel.service`.

## Batas pemeriksaan

Status email `delivered` berasal dari Cloudflare; penempatan inbox atau spam tidak diperiksa.
Halaman undangan dan pengiriman email transaksi diuji; akun anggota nyata belum dibuat.
Integrasi agent AI, push notification aplikasi mobile, billing, dan landing page belum dibuat.
Uji beban tim dan pemulihan setelah reboot seluruh VPS belum dilakukan.
VPS tidak direboot agar pekerjaan HermesTrading dapat terus berjalan.
