# Grow Team

Grow Team adalah workspace web untuk tim internal di <https://team.growc.id>.
Repositori ini merupakan fork Zulip. Rilis pertama memakai Zulip 12.2.

## Masuk dan mengundang anggota

1. Buka <https://team.growc.id> dari browser.
2. Masuk sebagai `rama.aditya@growthcircle.id`.
3. Gunakan **Forgot your password?** untuk membuat password pribadi melalui email.
4. Pilih **Invite to organization** untuk mengundang anggota.
5. Kirim undangan lewat email atau buat tautan undangan dengan masa berlaku terbatas.
6. Anggota membuka undangan, memverifikasi email, lalu membuat akun.

Organisasi bersifat privat dan memerlukan undangan. Anggota biasa tidak mendapat hak admin.
Aplikasi desktop tidak diperlukan. Percakapan dapat dimulai di channel `general`.
Gunakan topik untuk memisahkan pekerjaan di dalam setiap channel.

## Cakupan rilis

- Chat, channel, topik, pesan langsung, pencarian, dan unggah berkas memakai fitur Zulip.
- Email transaksi memakai Cloudflare Email Sending melalui Worker yang memiliki autentikasi.
- Landing page, billing, dan desain produk belum dibuat.
- Koneksi model Wulan dipertahankan. Integrasi agent AI ke Zulip belum dibuat.
- Data Buzz lama disimpan dalam arsip pemulihan. Percakapannya tidak diimpor ke Zulip.

## Source dan runtime

Branch `grow-team` dimulai dari tag upstream `12.2`, commit `1e73e1d754761b73c18135a3f25d0673f31cd8b3`.
Remote `upstream` menunjuk ke `zulip/zulip`; `origin` menunjuk ke `Growth-Circle/grow-team`.
Image resmi `ghcr.io/zulip/zulip-server:12.2-0` menjadi runtime awal.
Semua image dipatok dengan digest dalam `compose.override.yaml`.

Backend email lokal dipasang sebagai modul Python `grow_team.cloudflare_email_backend`.
Folder deployment dipasang read-only agar pembaruan file terlihat di container.
Restart proses aplikasi setelah mengubah kode backend.
Perubahan source aplikasi Zulip membutuhkan image baru dari fork; `git push` saja tidak memperbarui runtime.
Ikuti [panduan build image Zulip](https://zulip.readthedocs.io/projects/docker/en/latest/how-to/compose-upgrading.html).

## Layout server

| Lokasi | Fungsi |
| --- | --- |
| `ssh server-gteam` | Akses server proyek |
| `/opt/grow-team/bin` | Docker 29.8.1 dan cloudflared 2026.9.1 |
| `/opt/grow-team/lib/docker/cli-plugins` | Docker Compose 5.5.1 |
| `/opt/grow-team/deploy` | Salinan konfigurasi deployment |
| `/etc/grow-team` | Konfigurasi dan credentials privat |
| `/var/lib/grow-team/docker` | Container dan volume proyek |
| `/var/backups/grow-team` | Backup Zulip dan arsip Buzz |

Engine Docker hanya memakai socket `/run/grow-team-docker/docker.sock`.
Gunakan `compose.sh`; jangan memakai engine lain atau menjalankan perintah prune global.
Web origin hanya diterbitkan pada `127.0.0.1:18300`.
Cloudflare Tunnel yang sudah ada tetap menghubungkan domain ke port tersebut.
Tidak ada perubahan DNS saat perpindahan dari Buzz.

`grow-team.slice` membatasi total CPU ke tiga core dan RAM maksimum 6 GiB.
Batas per container menambah pembatasan untuk setiap layanan.
Engine, data, dan unit proyek terpisah dari HermesTrading.
Alias SSH asli `server-hermestrading` tetap tersedia.

## Operasi

Jalankan perintah berikut di server sebagai administrator.

```bash
sudo /opt/grow-team/deploy/compose.sh ps
sudo systemctl status grow-team.service grow-team-cloudflared.service
sudo /opt/grow-team/deploy/compose.sh logs --tail 100 zulip
sudo /opt/grow-team/deploy/compose.sh exec -T zulip supervisorctl status
```

Untuk memperbarui konfigurasi deployment:

1. Buat backup dengan `sudo /opt/grow-team/deploy/backup.sh`.
2. Salin file deployment yang sudah diperiksa ke `/opt/grow-team/deploy`.
3. Jalankan `sudo /opt/grow-team/deploy/compose.sh config --quiet`.
4. Jalankan `sudo /opt/grow-team/deploy/compose.sh up -d --wait --wait-timeout 300`.
5. Periksa login, pesan, unggahan, dan email dari domain publik.

Semua service `grow-team` diaktifkan melalui systemd.
User sistem `grow-team-tunnel` harus tersedia sebelum menjalankan koneksi SSH Wulan.
Simpan alamat, port, dan user Wulan di `/etc/grow-team/wulan-tunnel.env`.
Simpan key dan known-hosts di path `LoadCredential` dalam unit.

## Email Cloudflare

Worker: `grow-team-email`, akun Prazze.
Endpoint: `https://grow-team-email.arieko.workers.dev/send`.
Pengirim: `noreply@growc.id`.
Pesan dari alamat administrator menggunakan pengirim terverifikasi dan mempertahankan alamat balasan administrator.

Wrangler OAuth digunakan untuk deployment. Pengiriman saat runtime menggunakan binding `send_email`.
Runtime tidak bergantung pada token OAuth yang dapat kedaluwarsa.
Secret `RELAY_TOKEN` di Worker harus sama dengan `/etc/grow-team/secrets/email` di server.
Secret tersebut hanya memberi akses ke penghubung email ini, bukan API akun Cloudflare.
Saat merotasi secret, perbarui kedua sisi lalu buat ulang container Zulip dengan `compose.sh up -d --force-recreate zulip`.

```bash
npx wrangler deploy --config deploy/grow-team/mail-worker/wrangler.jsonc
npx wrangler secret put RELAY_TOKEN --config deploy/grow-team/mail-worker/wrangler.jsonc
node --test deploy/grow-team/mail-worker/handler.test.mjs
```

Backend Python diuji dengan Django dan requests dari virtualenv image Zulip.
Jalankan `tests/test_email_backend.py` dengan folder deployment dalam `PYTHONPATH`.
Penghubung mempertahankan MIME, lampiran, header, dan privasi BCC.
Batas Cloudflare adalah 5 MiB per pesan.
Setiap penerima menggunakan satu pengiriman. Kegagalan setelah pengiriman sebagian dapat menghasilkan email duplikat saat retry.
Worker tidak menyimpan isi pesan atau token di log aplikasi.
Status `delivered` pada Cloudflare berarti server email penerima menerima pesan; folder inbox atau spam tetap ditentukan penerima.

Referensi: [Workers email API](https://developers.cloudflare.com/email-service/api/send-emails/workers-api/),
[email logs](https://developers.cloudflare.com/email-service/observability/logs/).

## Credentials

Jangan simpan credentials di Git atau dokumen proyek.
Direktori `/etc/grow-team/secrets` hanya dapat dibuka root.
File secret umumnya memakai mode `0600`.
Secret Memcached memakai UID/GID `11211:11211` dan mode `0400` karena image berjalan sebagai user tersebut.
Compose file secrets mempertahankan izin file sumber.

Password awal admin disimpan pada komputer Rama di `~/.config/grow-team/admin-password` dengan mode `0600`.
Ganti password melalui reset email setelah masuk.

## Backup dan pemulihan

`grow-team-backup.timer` membuat backup harian sekitar 04.15–04.20 WIB.
`backup.sh` menyimpan database, uploads, pengaturan Zulip, konfigurasi host, dan checksum.
Arsip mengandung credentials; direktori backup memakai mode `0700`.
Backup harian tersimpan di VPS. Salinan di luar VPS perlu dijadwalkan terpisah.
Tidak ada penghapusan backup otomatis; periksa kapasitas disk secara berkala.

```bash
sudo /opt/grow-team/deploy/backup.sh
sudo systemctl list-timers grow-team-backup.timer
sudo sh -c 'cd /var/backups/grow-team/<backup> && sha256sum --check SHA256SUMS'
```

Jalankan pemeriksaan checksum dari direktori backup karena nama file di manifest bersifat relatif.
Uji pemulihan pada database atau server terpisah sebelum mengganti data aktif.
Gunakan versi Zulip, PostgreSQL, dan image yang sama dengan backup.
Ikuti [prosedur pemulihan Zulip](https://zulip.readthedocs.io/en/stable/production/export-and-import.html#restoring-backups).
Perintah restore upstream mengganti database target; jangan arahkan ke database aktif tanpa keputusan pemulihan yang jelas.

Arsip Buzz sebelum migrasi berada di `/var/backups/grow-team/20260921T114027Z-buzz-retired`.
Arsip itu mencakup dump PostgreSQL, volume, konfigurasi, dan unit sebelumnya.
Runtime lama sudah dinonaktifkan dan dipindahkan ke arsip tersebut.
Untuk rollback, hentikan Grow Team, pulihkan runtime Buzz dari arsip, lalu arahkan tunnel ke aplikasi yang dipulihkan.
Jangan menjalankan kedua aplikasi pada port `18300` secara bersamaan.

## Lisensi

Pertahankan lisensi dan atribusi upstream Zulip.
`compose.yaml` berasal dari `zulip/docker-zulip` tag `12.2-0`.
Lisensinya disertakan sebagai `LICENSE.docker-zulip`.
