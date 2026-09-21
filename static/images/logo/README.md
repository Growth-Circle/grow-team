# Grow Team

Aset pada folder ini memakai identitas Grow Team.
Nama file lama dipertahankan agar referensi template dan pesan tetap berfungsi.

Sumber SVG berada di `static/images/grow-team/`.
Wordmark menggunakan bentuk huruf Source Sans 3 dari paket `source-sans`.
Lisensi font tersedia pada paket tersebut.

Bangun ulang salinan SVG dan PNG dari root repositori:

```sh
pnpm install --frozen-lockfile
node tools/grow-team/render-brand-assets.mjs
```

Saat bentuk ikon berubah, sesuaikan juga `web/templates/favicon.svg.hbs`.
Template tersebut menampilkan jumlah pesan belum dibaca.
