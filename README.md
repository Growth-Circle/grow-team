# Grow Team

Grow Team adalah aplikasi kolaborasi berbasis web untuk tim Growth Circle.
Percakapan dibagi menjadi kanal dan topik agar keputusan mudah ditemukan kembali.

- Aplikasi tim: <https://team.growc.id>
- Repositori: <https://github.com/Growth-Circle/grow-team>
- Panduan operasional: [deploy/grow-team/README.md](deploy/grow-team/README.md)
- Catatan identitas produk: [BRANDING.md](BRANDING.md)
- Blueprint produk dan arsitektur: [internals/docs/blueprint.md](internals/docs/blueprint.md)

## Kemampuan

Chat kanal dan pesan pribadi, topik percakapan, pencarian, unggahan file,
undangan anggota, pengaturan peran, bot, webhook, dan API.
Anggota dapat bekerja langsung melalui browser.

## Pengembangan

Branch utama proyek ini adalah `grow-team`.
Panduan teknis pengembangan tersedia di [docs](docs/index.md).
Konfigurasi instalasi tim tersedia di [deploy/grow-team](deploy/grow-team/README.md).

Perubahan sumber perlu dibangun menjadi image aplikasi sebelum digunakan di server.
Push GitHub tidak otomatis mengganti image produksi.

## Lisensi dan asal proyek

Grow Team merupakan fork Zulip 12.2 dan menggunakan lisensi [Apache 2.0](LICENSE).
Hak cipta kontributor upstream tetap berlaku.
Nama paket, field API, dan identifier protokol upstream dipertahankan untuk kompatibilitas.
Kode sumber upstream tersedia di <https://github.com/zulip/zulip>.
