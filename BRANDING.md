# Identitas Grow Team

Grow Team adalah identitas produk fork ini.
Perubahan mencakup aplikasi browser, halaman akun, email, onboarding, bantuan,
konten bawaan organisasi baru, dan katalog bahasa.

## Aset

Sumber aset berada di `static/images/grow-team/`.
Ikon menampilkan tiga anggota dalam lingkaran hijau.
Wordmark memakai bentuk huruf Source Sans 3 dari dependensi font yang sudah tersedia.

Jalankan perintah berikut untuk memperbarui salinan SVG dan PNG:

```sh
pnpm install --frozen-lockfile
node tools/grow-team/render-brand-assets.mjs
```

Template `web/templates/favicon.svg.hbs` memakai simbol yang sama.
Template ini mempertahankan penghitung pesan dan indikator pesan pribadi.

## Kompatibilitas dan atribusi

Nama file lama tetap tersedia agar template, pesan lama, dan klien tetap berfungsi.
Contohnya `zulip-org-logo.svg` sekarang berisi wordmark Grow Team.

Referensi upstream yang tetap diperlukan meliputi:

- Lisensi Apache 2.0, pemegang hak cipta, dan asal proyek dalam informasi lisensi.
- Nama paket, impor, field API, identifier protokol, dan migrasi historis.
- Jalur bantuan lama yang dapat tersimpan dalam pesan atau bookmark.
- Dokumentasi teknis upstream dan nama aplikasi pihak ketiga yang benar-benar terpisah.
- Riwayat pengembangan dan materi korporat upstream yang tidak diaktifkan pada instalasi tim.

Grow Team tidak mengklaim layanan komersial, dukungan, atau aplikasi seluler milik upstream.
Pesan dan unggahan anggota tidak diubah oleh perubahan sumber ini.

## Penerapan

Image fork `grow-team/server:12.2-grow-team.3` memuat sumber, katalog bahasa,
bantuan, dan aset produksi. Base image dipatok pada Zulip 12.2.
Commit dan push sumber harus diikuti build image serta pergantian container aplikasi.
Ikuti [prosedur deployment](deploy/grow-team/README.md#image-fork).

Untuk organisasi yang sudah dibuat, pesan onboarding dan nama kanal bawaan tersimpan dalam database.
Perubahan sumber berlaku untuk konten baru.
Penyesuaian konten lama memerlukan pemeriksaan tersendiri agar pesan anggota tetap aman.

Pada pilot `team.growc.id`, penyesuaian terjaga sudah diterapkan pada 2026-09-21.
Kanal bawaan, deskripsi dua kanal, dan enam pesan Welcome Bot memakai Grow Team.
Ikon organisasi juga memakai aset Grow Team.
Hash isi dan topik tiga pesan anggota tetap sama dengan baseline sebelum perubahan.
Script `deploy/grow-team/rebrand_pilot.py` hanya menerima seed yang diaudit dan menolak penerapan ulang.

Bot sistem memakai domain `team.growc.id` pada konfigurasi dan alamat dalam database.
Perubahan alamat mempertahankan ID akun, API key, izin, serta riwayat pesan.
Avatar Welcome Bot dan Notification Bot memakai simbol Grow Team.
Contoh Linkifiers mengarah ke repo `Growth-Circle/grow-team`.
Ikuti [prosedur domain bot](deploy/grow-team/README.md#domain-bot-sistem) untuk audit dan pemulihan.

Hasil pemeriksaan perubahan tersedia di
[laporan branding awal](deploy/grow-team/BRANDING-VERIFICATION.md) dan
[verifikasi bot serta Linkifiers](deploy/grow-team/SYSTEM-BOT-VERIFICATION.md).
