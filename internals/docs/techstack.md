# Tech Stack Grow Team

Status: terverifikasi dari `pyproject.toml`, `package.json`, dan `deploy/grow-team/compose*.yaml` pada 2026-09-21.

| Lapisan | Teknologi dan peran | Status |
| --- | --- | --- |
| Aplikasi server | Python 3.10+, Django 5.2, Tornado | Terverifikasi. Django menangani aplikasi/data; Tornado menangani push server ke klien. |
| Browser | TypeScript, jQuery, Handlebars, Webpack 5 | Terverifikasi. Ini bukan SPA baru atau aplikasi desktop. |
| Data | PostgreSQL 14; volume uploads | Terverifikasi. PostgreSQL menyimpan metadata berkas; isi upload berada di volume aplikasi terpisah. |
| Asinkron internal | RabbitMQ 4.2 | Terverifikasi untuk pesan/tugas durable aplikasi. Jangan samakan dengan worker AI baru. |
| Data ephemeral | Redis | Terverifikasi sebagai penyimpanan ephemeral aplikasi. |
| Cache | Memcached dengan SASL | Terverifikasi sebagai cache aplikasi. |
| Runtime | Docker 29.8.1, Docker Compose 5.5.1, systemd | Terverifikasi dari panduan deploy. Engine khusus Grow Team. |
| Ingress dan email | Cloudflare Tunnel; Worker Email Sending | Terverifikasi. Worker mengirim email transaksi; tidak ada SMTP/API-token blocker. |
| AI privat | Gateway pada server Wulan lewat Tailscale/SSH | Terverifikasi sebagai koneksi privat. Integrasi aplikasi belum ada. |

Image aplikasi produksi memakai fork Grow Team. Pemeriksaan publik dan browser lulus
untuk alur inti serta identitas baru, termasuk indikator pemuatan pesan.
PostgreSQL, Redis, RabbitMQ, dan
Memcached juga dipatok digest dalam override. Host aplikasi adalah `team.growc.id`;
port HTTP hanya diekspos ke loopback. Nama paket, queue, dan setting Zulip tetap ada
sebagai kompatibilitas teknis. Lihat
[verifikasi branding](../../deploy/grow-team/BRANDING-VERIFICATION.md).

## Catatan operasi

- Gunakan `deploy/grow-team/compose.sh`, bukan engine Docker lain.
- Konfigurasi dan credential host berada di path privat dan tidak dicantumkan di sini.
- Backend email Python dipasang read-only ke container. Perakitan dan pemeriksaan startup image berjalan sebagai user aplikasi `zulip`.
- Backup sebelum dan sesudah branding mencakup database, uploads, dan konfigurasi; checksum lokal serta remote lulus. Lokasinya dicatat dalam laporan verifikasi. Uji pemulihan stack penuh belum tercatat.

## Target yang belum ada

Tidak ada Celery, katalog multi-model, task runner AI, atau agent otonom Grow Team dalam source/runtime terverifikasi. Rancangan target ada di [blueprint](blueprint.md) dan [roadmap](roadmap.md).
