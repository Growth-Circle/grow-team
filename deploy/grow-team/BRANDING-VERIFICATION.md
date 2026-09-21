# Verifikasi identitas Grow Team

Tanggal: 2026-09-21.

## Pemeriksaan sumber

- Build webpack produksi dan pemeriksaan TypeScript lulus.
- Pengujian Node untuk onboarding, menu, notifikasi navbar, pengaturan, dan organisasi demo lulus.
- Pemeriksaan ESLint pada source frontend yang berubah lulus.
- Ruff, pemeriksaan template, serta parser Python dan Jinja lulus.
- Seluruh 50 katalog frontend memiliki set kunci sumber yang sama.
- Seluruh 50 katalog backend dapat dikompilasi; referensi produk aktif memakai Grow Team.
- Bantuan dibangun menjadi 258 halaman. Pemeriksaan Astro tidak menemukan error atau warning.
- Sebanyak 37 alias judul mempertahankan tautan bantuan lama.
- Audit bantuan memeriksa 6.519 fragment internal pada 338 file HTML tanpa tautan fragment rusak.
- Render lokal login, registrasi, dan reset password menampilkan Grow Team tanpa gambar rusak.
- Lisensi, lockfile dependensi, dan identifier protokol tetap dipertahankan.

Suite integrasi Django lengkap belum dijalankan pada lingkungan pengembangan terprovisi.
Pemeriksaan parser dan template tidak menggantikan suite tersebut.

## Rilis produksi

Screenshot awal masih menampilkan Zulip karena container produksi memakai image resmi.
Perubahan Git belum masuk ke image tersebut.
Image fork kini memuat source, aset produksi, bantuan, serta katalog bahasa Grow Team.
Hanya container aplikasi yang dibuat ulang; database dan tiga layanan pendukung tetap berjalan.

| Identitas rilis | Nilai |
| --- | --- |
| Domain | `https://team.growc.id` |
| Commit source image | `999fd535cc63a88782fc9679faa4626b72894b85` |
| Tag image | `grow-team/server:12.2-grow-team.1` |
| ID image | `sha256:231ad4d5148db25b4bf9a19aaed087fc3e972fb10cb1667cc664a7f719818f1a` |
| Waktu smoke final | 2026-09-21 14:04 UTC / 21:04 WIB |
| Status container | Healthy, tanpa restart otomatis saat verifikasi |

Commit dokumentasi setelah commit image tidak mengubah source runtime.
Label image dan `build_id` cocok dengan commit source di atas.
Pemeriksaan Django pada image hasil ekspor lulus sebagai user aplikasi `zulip`.

## Pemeriksaan domain publik

- Login, registrasi, dan reset password memberi HTTP 200 dengan judul Grow Team.
- Ketiga halaman tersebut tidak memuat teks merek Zulip atau gambar rusak.
- Hash logo publik, favicon, dan PNG email cocok dengan aset source.
- Halaman bantuan menampilkan identitas Grow Team.
- Sesi admin dapat membaca profil, daftar kanal, dan pesan melalui browser.
- Kanal bawaan tampil sebagai Grow Team, dan ikon organisasi memakai aset baru.
- Semua salinan logo inline pada indikator pemuatan memakai simbol Grow Team.
- Browser tidak melaporkan error JavaScript pada alur yang diuji.
- Pendaftaran event queue dan polling tanpa menunggu memberi hasil sukses.
- Endpoint profil tanpa autentikasi memberi HTTP 401.

Pemeriksaan sesi admin memakai sesi pengujian sementara yang dibuat oleh operator.
Pemeriksaan ini tidak mengubah password dan tidak membuktikan login dengan password baru.
Sesi pengujian sudah dicabut setelah pemeriksaan selesai.
Rilis branding ini tidak mengirim pesan chat atau email pengujian baru.
Smoke pesan, unggahan, undangan, dan email pada instalasi awal dicatat dalam `VERIFICATION.md`.

## Konten pilot dan isolasi

Script terjaga memeriksa realm, bot, kanal, hash pesan, serta riwayat edit sebelum menulis.
Penyesuaian mencakup nama kanal bawaan, deskripsi dua kanal, dan enam pesan Welcome Bot.
Panduan video pada pesan bawaan diganti dengan tautan bantuan lokal.
Hash isi dan topik tiga pesan anggota sama dengan baseline sebelum perubahan.
Script menolak penerapan ulang atau seed yang sudah berubah.

Hash 41 file unit Hermes sama dengan baseline sebelum deployment.
Sepuluh timer Hermes tetap berstatus enabled.
Tidak ada restart, perubahan konfigurasi, atau perubahan data Hermes dalam rilis ini.

## Backup dan pemulihan

- Sebelum deployment: `/var/backups/grow-team/20260921T132110Z-zulip`.
- Sesudah penyesuaian konten dan ikon: `/var/backups/grow-team/20260921T135739Z-zulip`.
- Kedua backup memiliki checksum database/uploads/settings serta konfigurasi operasi yang lulus.
- Salinan kedua backup tersedia di komputer operator, dalam `~/.local/share/grow-team/backups/`.
- Checksum salinan lokal cocok dengan backup server.
- Image resmi dan image Grow Team sebelumnya tetap tersedia untuk pemulihan.

Kandidat image pertama gagal karena file log dibuat sebagai root saat build.
Rollback ke image resmi berhasil dan halaman publik kembali memberi HTTP 200.
Perakitan image kemudian dipindahkan ke user aplikasi, disertai pemeriksaan startup.
Audit visual juga menemukan tiga logo inline lama; semuanya diperbaiki sebelum build final.

Pemulihan database ke target sementara sudah diuji pada instalasi awal.
Pemulihan stack penuh pada VPS kedua, reboot host, uji beban, dan matriks ACL lengkap belum diuji.
Ikuti [prosedur deployment dan rollback](README.md#image-fork) untuk rilis berikutnya.

## Batas branding

Lisensi dan kredit upstream tetap menyebut Zulip secara benar.
Dokumentasi aplikasi desktop/mobile pihak ketiga tetap memakai nama aplikasi sebenarnya.
Nama paket, field API, jalur kompatibilitas, dan migrasi historis tidak diganti.
Pesan anggota tidak diubah.
Penyesuaian konten database hanya mencakup seed pilot yang diaudit di atas.

## Dokumentasi produk

Delapan dokumen di [`internals/docs`](../../internals/docs/blueprint.md)
membedakan kondisi saat ini, target AI, dan kebutuhan sebelum penjualan ke klien.
Integrasi agen AI belum menjadi bagian dari rilis branding ini.
