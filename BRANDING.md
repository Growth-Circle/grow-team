# Identitas sanji

sanji adalah identitas produk fork ini.
Perubahan mencakup aplikasi browser, halaman akun, email, onboarding, bantuan,
konten bawaan organisasi baru, dan katalog bahasa.

## Aset

Sumber aset berada di `static/images/sanji/`.
Ikon menampilkan mark dua blok dan satu titik: orang, agen, dan kerjaan.
Wordmark memakai path huruf (outline), bukan font web.
Path membuat gambar tetap tampil tanpa memuat font tambahan.

Path datang dari `SchibstedGrotesk[wght].ttf`, repo `google/fonts`, folder
`ofl/schibstedgrotesk`, lisensi OFL-1.1.
Path bukan dari paket npm `@fontsource-variable/schibsted-grotesk`.
Paket itu hanya berisi woff dan woff2, tanpa file TTF.
Paket itu bukan dependency pada cabang ini.

Aturan gambar wordmark:

- fontTools mengubah font variable menjadi dua instance statis: berat 800
  untuk "sanji" dan titik, berat 400 untuk "space".
- Ukuran wordmark 52px. Tracking −0,05em.
- Baseline memakai kotak baris CSS `line-height: 1`, bukan kotak tinta huruf.
  Rumus: `baseline_y = (tinggi_mark − ukuran_wordmark) / 2 + ukuran_wordmark
  × 0,859375`.
  Angka 0,859375 datang dari metrik hhea font ini: ascent 2000, descent
  −528, em 2048.
- Kerning per pasangan huruf, dalam unit font, dari tabel GPOS font ini:
  `sa` +23, `nj` −25, `i.` −1, `sp` −3, `pa` +28.

Jalankan perintah berikut untuk memperbarui salinan SVG dan PNG:

```sh
pnpm install --frozen-lockfile
node tools/grow-team/render-brand-assets.mjs
```

Template `web/templates/favicon.svg.hbs` memakai mark yang sama.
Template ini mempertahankan penghitung pesan belum dibaca.
Untuk pesan pribadi, template mengganti warna kotak dan mark. Template tidak
menambah tanda titik terpisah.

## Kompatibilitas dan atribusi

Nama file lama tetap tersedia agar template, pesan lama, dan klien tetap berfungsi.
Contohnya `zulip-org-logo.svg` sekarang berisi lockup sanji.

Nama dan path berikut tetap memakai "grow-team" atau "grow_team".
Rename akan merusak deploy, state runner di mesin anggota, atau data yang
sudah tersimpan:

- Repo dan folder kode: `Growth-Circle/grow-team`, `deploy/grow-team/`,
  `tools/grow-team/`, `services/grow-agent-runner/`.
- Paket dan binari runner: `@grow-team/agent-runner`, bin `grow-agent`,
  env `GROW_AGENT_STATE`, dan variabel lain berawalan `GROW_AGENT_`.
- Service dan unit deploy: `grow-agent.service`, `grow-team.slice`,
  `grow-team.service`, `grow-team-docker.service`, dan
  `grow-team-agent-reconcile.timer`.
- Path host dan image container: `/opt/grow-team`, `/etc/grow-team`,
  `/var/backups/grow-team`, dan image `grow-team/server:12.2-grow-team.*`.
- Tabel database: semua tabel berawalan `zerver_` dan tabel agen.
- Nama file aset lama: `zulip-org-logo.svg`, `zulip-icon-*.png`, dan
  `grow-team-wordmark-white.svg`. Isinya sudah berganti ke aset sanji.
  Hanya nama file yang tetap.

Referensi upstream yang tetap diperlukan meliputi:

- Lisensi Apache 2.0, pemegang hak cipta, dan asal proyek dalam informasi lisensi.
- Nama paket, impor, field API, identifier protokol, dan migrasi historis.
- Jalur bantuan lama yang dapat tersimpan dalam pesan atau bookmark.
- Dokumentasi teknis upstream dan nama aplikasi pihak ketiga yang benar-benar terpisah.
- Riwayat pengembangan dan materi korporat upstream yang tidak diaktifkan pada instalasi tim.

sanji tidak mengklaim layanan komersial, dukungan, atau aplikasi seluler milik upstream.
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
Avatar Welcome Bot dan Notification Bot memakai mark sanji.
Contoh Linkifiers mengarah ke repo `Growth-Circle/grow-team`.
Ikuti [prosedur domain bot](deploy/grow-team/README.md#domain-bot-sistem) untuk audit dan pemulihan.

Hasil pemeriksaan perubahan tersedia di
[laporan branding awal](deploy/grow-team/BRANDING-VERIFICATION.md) dan
[verifikasi bot serta Linkifiers](deploy/grow-team/SYSTEM-BOT-VERIFICATION.md).
