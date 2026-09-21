# Roadmap Grow Team

Tidak ada tanggal komitmen dalam roadmap ini.

1. **Stabilisasi internal — terverifikasi sebagian.** Operasikan browser app, undangan, email, backup, dan monitoring. Gate: restore stack penuh/reboot test dan review kapasitas.
2. **Rilis image fork — selesai.** Image Grow Team berjalan di `team.growc.id`. Build, startup sebagai user aplikasi, pemeriksaan publik, sesi admin, dan identitas produk lulus. Backup tersedia sebelum serta sesudah penyesuaian konten bawaan. Bukti dan batas pengujian ada dalam [laporan verifikasi](../../deploy/grow-team/BRANDING-VERIFICATION.md).
3. **Pilot AI internal.** Rancang dan implementasikan FR-08–FR-18. Gate: izin,
   audit, idempotency/retry, cancel/resume, approval, model allowlist, dan uji gateway.
4. **Kesiapan B2B.** Putuskan model isolasi pelanggan. Gate: provisioning,
   backup/restore per pelanggan, offboarding, observability, dan review keamanan.
5. **Komersialisasi.** Tentukan penawaran dan operasi hanya setelah gate tahap sebelumnya. Tidak ada pricing atau SLA saat ini.

Ketergantungan utama: image fork sebelum branding live; pilot AI sebelum
komersialisasi; keputusan isolasi sebelum multi-klien; persetujuan keamanan
sebelum aksi eksternal. Lihat [blueprint](blueprint.md) dan [BRD](brd.md).
