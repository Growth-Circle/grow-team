# Verifikasi bot sistem dan Linkifiers

Tanggal: 2026-09-21.

## Penyebab dan perubahan

Alamat bot merupakan identitas akun yang tersimpan dalam database.
Mengubah teks antarmuka tidak mengubah alamat tersebut.
Konfigurasi dan database kini memakai domain `team.growc.id` untuk bot sistem.

Tiga akun yang tampil dalam pengaturan organisasi:

| ID | Akun | Alamat |
| --- | --- | --- |
| 1 | Email Gateway | `emailgateway@team.growc.id` |
| 6 | Notification Bot | `notification-bot@team.growc.id` |
| 7 | Welcome Bot | `welcome-bot@team.growc.id` |

Empat bot pemantauan internal memakai domain yang sama.
Tidak ada akun bot baru yang dibuat.
Label `No owner` mengikuti aturan API untuk bot sistem yang memiliki dirinya sendiri.

Contoh Linkifiers memakai `https://github.com/Growth-Circle/grow-team/issues/2468`.
Seluruh 50 katalog frontend memiliki kunci sumber yang sama setelah perubahan.
Istilah unduhan konfigurasi bot memakai `botserverrc format`.
Avatar Notification Bot memakai simbol Grow Team pada ukuran standar dan medium.

## Pemeriksaan sumber

- Enam pengujian SQLite terpisah menjalankan script perubahan alamat yang sebenarnya.
- Audit tidak menulis data; apply dan reverse mempertahankan identitas akun.
- Baris tidak sesuai, data campuran, benturan alamat, host, dan realm yang salah ditolak.
- Ruff, format Python, parser JavaScript, pemeriksaan template, dan `git diff --check` lulus.
- Build webpack produksi lulus dengan peringatan ukuran aset yang sudah ada sebelumnya.
- Pemeriksaan image mengimpor konfigurasi Django dengan domain bot uji tanpa akses database.

Pengujian SQLite memakai model minimal dan pengganti fungsi cache dalam proses tersendiri.
Pengujian tersebut tidak membuktikan penguncian PostgreSQL atau perilaku cache produksi.
Suite integrasi Django lengkap belum dijalankan pada lingkungan pengembangan terprovisi.

## Perubahan data produksi

Audit sumber menerima tujuh bot pada realm sistem ID 1 sebelum perubahan.
Operator menghentikan proses aplikasi sebelum memperbarui `email` dan `delivery_email`.
Cache default Grow Team dibersihkan sebelum aplikasi dimulai dengan konfigurasi baru.
Audit berikutnya menerima tujuh bot pada domain target.

Hash identitas tujuh bot sama dengan baseline sebelum perubahan.
Pembanding mencakup ID, realm, nama, status, pemilik, API key, peran, dan atribut avatar.
Hash isi, topik, pengirim, penerima, serta realm untuk 16 pesan sebelumnya tetap sama.
Lookup ketiga bot lintas realm tetap menghasilkan ID 1, 6, dan 7.

## Image dan pemeriksaan publik

| Identitas rilis | Nilai |
| --- | --- |
| Domain | `https://team.growc.id` |
| Commit source image | `07987acf6103e38ba9e86c5990f40a6295afb277` |
| Tag image | `grow-team/server:12.2-grow-team.3` |
| ID image | `sha256:7050a7a43a63a9c8074f8b67e359006405cf18c141b68716bdc680b8f57081e3` |
| Pemeriksaan browser final | 2026-09-21 15:06 UTC / 22:06 WIB |
| Status container | Healthy, tanpa restart otomatis saat verifikasi |

Pemeriksaan Django pada image hasil ekspor lulus sebagai user aplikasi.
Commit dokumentasi sesudah commit image tidak mengubah kode runtime.

- Daftar bot di browser dan API menampilkan tiga alamat baru dengan ID akun yang sama.
- Semua avatar dalam daftar bot selesai dimuat tanpa gambar rusak.
- Pemeriksaan visual memastikan Notification Bot dan Welcome Bot memakai simbol Grow Team.
- Hash kedua avatar Notification Bot dari URL manifest publik cocok dengan source.
- Halaman Linkifiers menampilkan contoh repo Growth-Circle/grow-team.
- Browser tidak melaporkan error JavaScript pada alur yang diuji.
- Login, registrasi, dan reset password memberi HTTP 200 dengan identitas Grow Team.
- Sesi admin dapat membaca profil, kanal, dan pesan.
- Pendaftaran event queue serta polling berhasil; profil tanpa autentikasi memberi HTTP 401.
- Hash logo publik, favicon, dan PNG email cocok dengan source.

Empat container pendukung tetap memiliki ID yang sama dan tidak mengalami restart.
Hash 41 file unit Hermes sama dengan baseline; sepuluh timernya tetap enabled dan active.
Tidak ada konfigurasi atau layanan Hermes yang diubah dalam pekerjaan ini.

## Backup dan pemulihan

- Sebelum perubahan alamat: `/var/backups/grow-team/20260921T142408Z-zulip`.
- Sesudah perubahan alamat: `/var/backups/grow-team/20260921T145343Z-zulip`.
- Kedua backup memuat database, unggahan, settings, serta konfigurasi operasi.
- Checksum backup dan salinan lokal dalam `~/.local/share/grow-team/backups/` cocok.
- Image `.1` dan konfigurasi lama tersedia untuk pemulihan domain.
- Image `.2` dan konfigurasi sesudah perubahan alamat tersedia untuk pemulihan avatar.

Pemulihan domain harus memakai urutan dalam [prosedur bot](README.md#domain-bot-sistem).
Hentikan proses aplikasi sebelum menjalankan reverse, bersihkan cache, lalu pulihkan konfigurasi dan image lama.
Jangan menjalankan image `.1` ketika database masih memakai domain baru.
Pemulihan avatar ke `.2` tidak memerlukan perubahan alamat bot.

## Batas pemeriksaan

Pemeriksaan browser memakai sesi admin sementara tanpa perubahan password.
Sesi pengujian dicabut setelah pemeriksaan; sesi tersebut kemudian memberi HTTP 401.
Rilis ini tidak mengirim pesan chat atau email pengujian baru.
Integrasi agent AI tetap di luar cakupan perbaikan branding ini.
