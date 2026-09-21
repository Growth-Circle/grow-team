# Roadmap Grow Team

Tidak ada tanggal komitmen dalam roadmap ini.

1. **Stabilisasi internal — terverifikasi sebagian.** Operasikan browser app, undangan, email, backup, dan monitoring. Gate: restore stack penuh/reboot test dan review kapasitas.
2. **Rilis image fork — dideploy, verifikasi identitas belum lengkap.** Image fork Grow Team berjalan di `team.growc.id`. Pemeriksaan publik dan browser inti lulus. Kandidat awal rollback sehat sesudah kegagalan ownership log; image berikutnya melewati pemeriksaan startup Django. Audit visual menemukan ikon Z upstream pada spinner feed. Gate tersisa: koreksi ikon, rebuild akhir, dan catatan bukti rilis.
3. **Pilot AI internal.** Rancang dan implementasikan FR-08–FR-18. Gate: izin,
   audit, idempotency/retry, cancel/resume, approval, model allowlist, dan uji gateway.
4. **Kesiapan B2B.** Putuskan model isolasi pelanggan. Gate: provisioning,
   backup/restore per pelanggan, offboarding, observability, dan review keamanan.
5. **Komersialisasi.** Tentukan penawaran dan operasi hanya setelah gate tahap sebelumnya. Tidak ada pricing atau SLA saat ini.

Ketergantungan utama: image fork sebelum branding live; pilot AI sebelum
komersialisasi; keputusan isolasi sebelum multi-klien; persetujuan keamanan
sebelum aksi eksternal. Lihat [blueprint](blueprint.md) dan [BRD](brd.md).
