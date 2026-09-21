# Verifikasi identitas Grow Team

Tanggal: 2026-09-21.

## Pemeriksaan sumber

- Build webpack produksi dan pemeriksaan TypeScript lulus.
- Pengujian Node untuk onboarding, menu, notifikasi navbar, pengaturan, dan organisasi demo lulus.
- Pemeriksaan ESLint pada source frontend yang berubah lulus.
- Ruff, pemeriksaan template, serta parser Python dan Jinja lulus.
- Seluruh 50 katalog frontend memiliki set kunci sumber yang sama.
- Seluruh 50 katalog backend dapat dikompilasi; referensi produk aktif memakai Grow Team.
- Bantuan dibangun menjadi 258 halaman. Pemeriksaan Astro tidak menemukan error atau warning.
- Sebanyak 37 alias judul mempertahankan tautan bantuan lama.
- Render lokal login, registrasi, dan reset password menampilkan Grow Team tanpa gambar rusak.
- Lisensi, lockfile dependensi, dan identifier protokol tetap dipertahankan.

Suite integrasi Django lengkap belum dijalankan pada lingkungan pengembangan terprovisi.
Pemeriksaan parser dan template tidak menggantikan suite tersebut.

## Rilis produksi

Pengguna mengirim screenshot runtime lama sesudah perubahan sumber selesai.
Scope diperluas untuk membangun image fork dan memasangnya pada `team.growc.id`.

Backup sebelum deployment tersedia di
`/var/backups/grow-team/20260921T132110Z-zulip`.
Checksum database/uploads/settings dan konfigurasi operasi lulus.
Baseline hash 41 file unit Hermes tersimpan terpisah sebelum perubahan aplikasi.

Status saat dokumen ini dibuat: image fork sedang disiapkan.
Hasil smoke test domain publik dan ID image dicatat setelah pergantian container.

## Batas branding

Lisensi dan kredit upstream tetap menyebut Zulip secara benar.
Dokumentasi aplikasi desktop/mobile pihak ketiga tetap memakai nama aplikasi sebenarnya.
Nama paket, field API, jalur kompatibilitas, dan migrasi historis tidak diganti.
Pesan anggota tidak diubah.
Konten bawaan yang sudah tersimpan di database memerlukan audit terpisah dari source.

## Dokumentasi produk

Delapan dokumen di [`internals/docs`](../../internals/docs/blueprint.md)
membedakan kondisi saat ini, target AI, dan kebutuhan sebelum penjualan ke klien.
Integrasi agen AI belum menjadi bagian dari rilis branding ini.
