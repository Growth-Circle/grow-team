# Pemulihan data agen

Status: komponen penyalinan dan pemeriksaan file tersedia. Integrasi backup produksi
dan restore database lengkap masih menunggu gate rilis.

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

- Integrasikan staging ke perintah backup dan arsip operasi yang digunakan server.
- Tolak backup tidak lengkap jika database memiliki artifact atau secret tanpa sumbernya.
- Gunakan target restore baru dengan versi database dan image yang sesuai.
- Periksa referensi job, approval, operation, result, dan checksum artifact setelah restore.
- Uji dekripsi secret sintetis dengan versi kunci lama dan baru tanpa mencetak nilainya.
- Uji rollback aplikasi dan runner sambil mempertahankan journal dan data chat.
- Rekam hasil produksi dan pastikan layanan Hermes tetap sesuai baseline.

Tes file yang sudah tersedia:

```bash
tools/grow-team/test-environment/run.sh \
  .venv/bin/python -m unittest zerver.tests.test_agents_backup
```

Tes tersebut memakai file sementara. Tes ini belum membuktikan restore database,
pemulihan runner, atau pemulihan layanan produksi.

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
