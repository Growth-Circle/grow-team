# Peta repositori

Sanji yang aktif di produksi dan cabang pengembangannya tidak berada di tempat yang sama.

## Produksi

| | |
| --- | --- |
| Produk | Sanji di <https://team.growc.id> |
| Repositori | [`Growth-Circle/grow-team`](https://github.com/Growth-Circle/grow-team) |
| Branch | `feature/team-mcp` |

Repositori `grow-team` tetap dipakai. Jangan menghapusnya.
Nama repo, branch historis `grow-team`, path deploy, dan image `grow-team/server` tetap untuk kompatibilitas.

Ambil kode yang sama dengan produksi:

```bash
git clone https://github.com/Growth-Circle/grow-team.git
cd grow-team
git fetch origin
git switch feature/team-mcp
```

Checkout yang sudah ada:

```bash
git fetch origin
git switch feature/team-mcp
git pull --ff-only origin feature/team-mcp
```

Image produksi dibangun dari checkout ini.
Push ke GitHub tidak mengganti image yang sedang berjalan.

## Pengembangan

Repositori `sanji` menyimpan cabang pengembangan.
Perubahan di sana belum menjadi sumber produksi sampai masuk ke `Growth-Circle/grow-team` pada branch `feature/team-mcp`.

## Bukan sumber produksi

- Branch `grow-team` di repositori yang sama bukan checkout Sanji yang aktif di produksi.
- Repositori `sanji` adalah tempat cabang pengembangan.
