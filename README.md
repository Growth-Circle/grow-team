# Sanji

Sanji adalah aplikasi kolaborasi berbasis web untuk tim Growth Circle.
Percakapan dibagi menjadi kanal dan topik agar keputusan mudah ditemukan kembali.

- Aplikasi tim: <https://team.growc.id>
- Repositori produksi: <https://github.com/Growth-Circle/grow-team>, branch `feature/team-mcp`
- Peta repositori: [REPOSITORY.md](REPOSITORY.md)
- Panduan operasional: [deploy/grow-team/README.md](deploy/grow-team/README.md)
- Catatan identitas produk: [BRANDING.md](BRANDING.md)
- Blueprint produk dan arsitektur: [internals/docs/blueprint.md](internals/docs/blueprint.md)
- Keputusan rebrand dan domain: [internals/docs/sanji-rebrand-decision.md](internals/docs/sanji-rebrand-decision.md)

## Kemampuan

Chat kanal dan pesan pribadi, topik percakapan, pencarian, unggahan file,
undangan anggota, pengaturan peran, bot, webhook, dan API.
Anggota dapat bekerja langsung melalui browser.

## Sumber kode

Sanji yang aktif di produksi memakai repositori `grow-team`, branch `feature/team-mcp`.
Repositori `grow-team` tetap dipakai dan tidak dihapus.
Repositori `sanji` menyimpan cabang pengembangan, bukan sumber produksi.

Ambil kode yang sama dengan produksi:

```bash
git clone https://github.com/Growth-Circle/grow-team.git
cd grow-team
git switch feature/team-mcp
```

Peta repositori dan perintah lengkap ada di [REPOSITORY.md](REPOSITORY.md).

## Pengembangan

Panduan teknis pengembangan tersedia di [docs](docs/index.md).
Konfigurasi instalasi tim tersedia di [deploy/grow-team](deploy/grow-team/README.md).

Perubahan sumber perlu dibangun menjadi image aplikasi sebelum digunakan di server.
Push GitHub tidak otomatis mengganti image produksi.

## Lisensi dan asal proyek

Sanji merupakan fork Zulip 12.2 dan menggunakan lisensi [Apache 2.0](LICENSE).
Kode sumber, branch, dan image tetap memakai nama `grow-team` untuk kompatibilitas.
Hak cipta kontributor upstream tetap berlaku.
Nama paket, field API, dan identifier protokol upstream dipertahankan untuk kompatibilitas.
Kode sumber upstream tersedia di <https://github.com/zulip/zulip>.
