# Pemulihan data agen

Status: perintah backup sudah mencakup artifact dan keyring. Restore database
sintetis berhasil pada target terpisah. Pemulihan source rilis dan produksi masih
menunggu gate rilis.

## Data yang wajib dipulihkan bersama

- Database chat beserta job, attempt, approval, operation, artifact, dan result.
- File pada `AGENT_ARTIFACT_ROOT`, sesuai checksum dan referensi database.
- Seluruh keyring pada `AGENT_SECRET_MASTER_KEY_FILE`, termasuk versi kunci lama.
- Konfigurasi aplikasi, identitas image, protokol, dan paket runner yang kompatibel.

Kunci dekripsi berada di luar database. Backup database saja tidak dapat memulihkan
secret provider. Arsip keyring tetap bersifat rahasia.

## Komponen file

`zerver.lib.agent_backup.stage_agent_backup` menyalin sumber ke direktori staging
baru. Fungsi ini tidak menimpa staging yang sudah ada.

```text
agent/
  manifest.json
  artifacts/       # Ada jika penyimpanan artifact dikonfigurasi.
  keyring.json     # Ada jika penyimpanan secret dikonfigurasi.
```

Manifest memuat versi format, path relatif, ukuran, dan SHA-256. Manifest tidak
memuat nilai kunci atau path konfigurasi host. Direktori memakai mode `0700`;
file memakai mode `0600`.

Penyalinan menolak symlink pada sumber maupun direktori induknya, hard link, FIFO,
dan perubahan file selama pembacaan.
Kegagalan tidak menghasilkan manifest keberhasilan. Fungsi juga menolak staging
atau keyring di dalam direktori artifact.

`verify_agent_backup` memeriksa arsip yang sudah diekstrak ke lokasi terpisah.
Fungsi memeriksa daftar file dan checksum, serta menolak path yang keluar dari
direktori tujuan. Permission grup atau publik pada file dan direktori juga ditolak.
Gunakan `umask 077` saat ekstraksi. Pemeriksaan tidak mengubah konfigurasi atau
database aktif.

## Urutan konsistensi

1. Hentikan cleanup artifact dan penghapusan versi kunci selama backup.
2. Ambil snapshot database melalui mekanisme backup aplikasi.
3. Salin artifact dan keyring setelah dump database selesai.
4. Gabungkan staging ke arsip privat dan periksa checksum arsip.
5. Verifikasi checksum lagi setelah arsip disalin ke lokasi cadangan.

Artifact yang sudah tercatat harus bersifat immutable. File baru setelah snapshot
dapat ikut tersalin, tetapi semua file yang dirujuk snapshot harus tetap tersedia.
Komponen file tidak menyediakan penguncian database atau menghentikan cleanup.
Pemanggil wajib menjaga syarat tersebut sepanjang operasi backup.

## Gate sebelum rilis

- Pasang perintah backup dan wrapper yang sudah mencakup data agen pada image rilis.
- Pastikan timer cleanup artifact dan penghapusan versi kunci tetap tidak aktif.
- Gunakan target restore baru dengan versi database dan image yang sesuai.
- Periksa referensi job, approval, operation, result, dan checksum artifact setelah restore.
- Uji dekripsi secret sintetis dengan versi kunci lama dan baru tanpa mencetak nilainya.
- Uji rollback aplikasi dan runner sambil mempertahankan journal dan data chat.
- Rekam hasil produksi dan pastikan layanan Hermes tetap sesuai baseline.

Tes file dan perintah backup yang sudah tersedia:

```bash
tools/grow-team/test-environment/run.sh .venv/bin/python - <<'PY'
import django
import unittest

django.setup()
suite = unittest.defaultTestLoader.loadTestsFromNames([
    "zerver.tests.test_agents_backup",
    "zerver.tests.test_agents_backup_command",
])
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

Sebanyak 43 tes memakai file sementara dan tar yang nyata. Tes perintah memakai
mock untuk query database. Bukti ini belum membuktikan restore database, pemulihan
runner, atau pemulihan layanan produksi.

## Rehearsal database terpisah

Jalankan dari root worktree yang sudah memiliki lingkungan tes:

```bash
tools/grow-team/recovery-check/run.sh
```

[Petunjuk rehearsal](../../tools/grow-team/recovery-check/README.md) menjelaskan
prasyarat dan penyimpanan bukti privat. Script memakai endpoint Docker rootless
lokal yang diperiksa. Setiap percobaan membuat database, network, volume, dan
direktori bukti baru. Target yang sudah berisi data ditolak sebelum restore.

Rehearsal 22 September 2026 memakai dump dari perintah backup sebenarnya.
Pemeriksaan mencocokkan 997 identitas migrasi serta relasi dan status data agen.
Checksum artifact dan dekripsi secret sintetis dengan kunci `v1` serta `v2` lulus.
Container percobaan berhenti setelah pemeriksaan; volume dan bukti tetap tersimpan.
Perbaikan tooling lulus review independen dan pemeriksaan target terakhir.

Source saat rehearsal masih dalam pengembangan. Ulangi pada source rilis yang
bersih sebelum aktivasi. Bukti ini belum mencakup transfer backup produksi,
rollback runner, atau pemulihan layanan produksi.

## Rekonsiliasi terjadwal

`deploy/grow-team/reconcile-agents.sh` menjalankan `reconcile_agents --limit 100`
sebagai user aplikasi melalui engine Grow Team. Timer menjalankannya setiap
15 detik setelah proses sebelumnya selesai, dengan jeda acak maksimal 2 detik.
Timer tidak menyalakan aplikasi yang sengaja dihentikan.

Lock berada di dalam container. Request berikutnya tidak berjalan bersamaan jika
koneksi Docker dari host terputus. Batas proses remote adalah 60 detik, diikuti
SIGKILL setelah tambahan 5 detik jika proses belum berhenti.

Sebelum mengaktifkan timer, siapkan `/data/grow-team-agent-private` dengan pemilik
`zulip` dan mode `0700`. Direktori tersebut memakai volume aplikasi yang sudah ada.
Simpan artifact dalam subdirektori `artifacts` dan keyring pada `keyring.json`.
Keduanya terpisah dari lokasi unggahan publik.

Wrapper dan unit ini masih berupa konfigurasi rilis. Aktivasi serta pemeriksaan
rekonsiliasi setelah restart dilakukan bersama gate produksi.

## Inisialisasi penyimpanan privat

`deploy/grow-team/init_agent_storage.py` dijalankan sebagai user `zulip` setelah
operator menyiapkan direktori induk privat. Script membuat direktori artifact dan
keyring hanya jika belum tersedia. Script memeriksa seluruh versi kunci yang sudah
ada dan mempertahankan isi file persis seperti sebelumnya.

Script menolak pemilik atau permission yang salah, symlink, file khusus, dan
keyring yang tidak valid. Lock inisialisasi memiliki batas tunggu satu detik.
Output hanya menunjukkan status `created` dan `ready`; nilai kunci tidak dicetak.

## Arsip operasional

`deploy/grow-team/backup.sh` membuat direktori privat yang unik untuk setiap
percobaan backup, termasuk dua backup pada detik yang sama. Aplikasi mengirimkan
arsip dari staging sementara di dalam container. Staging tersebut dibersihkan
setelah transfer selesai atau gagal.

Arsip yang belum selesai memakai akhiran `.partial`. Script hanya menulis
`SHA256SUMS` setelah arsip aplikasi dan operasi selesai serta arsip aplikasi dapat
dibaca. Backup yang gagal tidak menimpa backup sebelumnya.

Arsip operasi mencakup service dan timer rekonsiliasi agen. Pasang kedua unit
tersebut sebelum memasang versi baru script backup. Script menolak backup operasi
yang tidak lengkap jika unit belum tersedia.

Perintah `manage.py backup` menyalin artifact dan keyring setelah dump database.
Perintah memeriksa ukuran serta checksum artifact yang tercatat dan semua versi
kunci yang dirujuk secret. Sumber yang hilang, permission yang terbuka, serta
referensi yang tidak lengkap membuat backup gagal. Opsi `--skip-db` dan
`--skip-uploads` tetap menyertakan file privat agen.

Target `--output` harus berupa path baru. Perintah membuat arsip melalui descriptor
file privat, lalu menerbitkannya secara atomik tanpa menimpa target yang sudah ada.
Kegagalan hanya membersihkan staging milik percobaan tersebut. Penghentian paksa
dapat meninggalkan staging privat; staging itu bukan bukti backup berhasil.

Cleanup artifact dan penghapusan versi kunci masih nonaktif. Operator wajib
mempertahankan kondisi tersebut sepanjang backup. Implementasi cleanup mendatang
harus memakai barrier yang sama dengan backup. Bukti restore database lengkap
tetap menjadi syarat rilis.
