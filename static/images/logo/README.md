# sanji

Aset pada folder ini memakai identitas sanji.
Nama file lama dipertahankan agar referensi template dan pesan tetap berfungsi.

Sumber SVG berada di `static/images/sanji/`.
Wordmark memakai path huruf (outline) dari `SchibstedGrotesk[wght].ttf`,
repo `google/fonts`, folder `ofl/schibstedgrotesk`, lisensi OFL-1.1.
Path bukan dari paket npm `@fontsource-variable/schibsted-grotesk`.
Lihat `BRANDING.md` untuk ukuran, tracking, baseline, dan kerning path ini.

Bangun ulang salinan SVG dan PNG dari root repositori:

```sh
pnpm install --frozen-lockfile
node tools/grow-team/render-brand-assets.mjs
```

Saat bentuk ikon berubah, sesuaikan juga `web/templates/favicon.svg.hbs`.
Template tersebut menampilkan jumlah pesan belum dibaca.
