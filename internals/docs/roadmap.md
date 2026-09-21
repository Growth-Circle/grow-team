# Roadmap Grow Team

Tidak ada tanggal komitmen dalam roadmap ini.

1. **Stabilisasi internal — terverifikasi sebagian.** Operasikan browser app, undangan, email, backup, dan monitoring. Gate: restore stack penuh/reboot test dan review kapasitas.
2. **Rilis image fork — dalam proses.** Bangun dan deploy image berisi branding source ke `team.growc.id`; lakukan staging, smoke test, dan rollback image. Gate: tidak ada perubahan data anggota dan bukti runtime memakai image fork. Sampai gate lulus, login live dapat menampilkan branding upstream lama.
3. **Pilot AI internal.** Rancang dan implementasikan FR-08–FR-18. Gate: izin,
   audit, idempotency/retry, cancel/resume, approval, model allowlist, dan uji gateway.
4. **Kesiapan B2B.** Putuskan model isolasi pelanggan. Gate: provisioning,
   backup/restore per pelanggan, offboarding, observability, dan review keamanan.
5. **Komersialisasi.** Tentukan penawaran dan operasi hanya setelah gate tahap sebelumnya. Tidak ada pricing atau SLA saat ini.

Ketergantungan utama: image fork sebelum branding live; pilot AI sebelum
komersialisasi; keputusan isolasi sebelum multi-klien; persetujuan keamanan
sebelum aksi eksternal. Lihat [blueprint](blueprint.md) dan [BRD](brd.md).
