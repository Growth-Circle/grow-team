# Security Grow Team

## Kontrol terverifikasi

- Aplikasi hanya publish ke loopback dan masuk melalui Cloudflare Tunnel.
- Engine Docker, volumes, systemd services, CPU/RAM limits, dan backup dipisah dari Hermes.
- Container memakai secrets; credential host berada di path privat dan tidak dicatat di Git.
- Email Worker menolak request tanpa relay secret; backend tidak mencatat isi email/token.
- Endpoint profil tanpa autentikasi diuji memberi 401. Ini bukan bukti setiap halaman UI atau setiap ACL sudah diuji.
- Backup memakai checksum; restore database sementara sudah diuji tanpa mengubah database aktif.
- Gateway AI privat dapat dicapai melalui jalur Tailscale/SSH dan menolak request tanpa key.

## Batas/gap terverifikasi

- Status email `delivered` bukan bukti email masuk inbox.
- Pemulihan seluruh stack pada VPS kedua dan reboot host belum diuji.
- Uji beban tim belum tercatat.
- Runtime live masih image resmi dengan branding lama. Deploy image fork berbranding Grow Team sedang berlangsung; source rebrand belum sama dengan deployment sampai smoke dan rollback lulus.
- Push mobile, billing, landing, dan AI agent belum diimplementasikan.
- Fitur upstream untuk role, DM, search, dan realm tersedia di source, tetapi tes
  otorisasi negatif untuk setiap kombinasi ACL, search, DM, dan tenant belum tercatat.

## Persyaratan sebelum AI

1. Terapkan least privilege pada bot/API dan cek akses per context reference.
2. Gunakan job durable dengan idempotency key, retry terbatas, dead-letter/review path, dan audit immutable sesuai retensi yang disetujui.
3. Jangan kirim data lebih dari yang diperlukan ke gateway/model.
4. Minta approval manusia untuk aksi eksternal atau aksi yang mengubah data penting.
5. Threat-model sidecar, worker, gateway, prompt injection, kebocoran konteks, dan replay sebelum rilis.

## Persyaratan sebelum B2B

Review isolasi tenant, lifecycle credential, logging/redaction, backup encryption/retention, restore drill, patch process, incident response, dan offboarding. Model instance-per-client atau realm-bersama belum diputuskan. Lihat [BRD](brd.md) dan [roadmap](roadmap.md).

## Bukti dan traceability

Kontrol runtime bersumber dari `deploy/grow-team/compose.override.yaml` dan
`VERIFICATION.md`. Kebutuhan AI adalah FR-08–FR-18; tenant isolation adalah
FR-19; gate branding runtime adalah FR-20. Sebelum perubahan claim keamanan, jalankan skenario negatif yang dapat
diulang dan simpan bukti rilis terpisah.
