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
Image resmi `ghcr.io/zulip/zulip-server:12.2-0` menjadi base image runtime.
Image fork `grow-team/server:12.2-grow-team.4` membawa identitas Grow Team.
Base image dan image pendukung dipatok dengan digest.
Image fork dibangun lokal dan mencatat commit sumber pada label serta `build_id`.

Backend email lokal dipasang sebagai modul Python `grow_team.cloudflare_email_backend`.
Folder deployment dipasang read-only agar pembaruan file terlihat di container.
Restart proses aplikasi setelah mengubah kode backend.
Perubahan source aplikasi membutuhkan image baru dari fork; `git push` saja tidak memperbarui runtime.

## Image fork

Bangun aset dari checkout yang dependensinya sudah terpasang sesuai lockfile.
Gunakan lingkungan pengembangan Zulip yang sudah diprovisi untuk build lengkap.
Jalankan `tools/update-prod-static` untuk membangun frontend, emoji, bantuan, dan katalog.
Periksa hasil build, lalu commit seluruh perubahan sumber.

### Build aset tanpa lingkungan terprovisi

Perubahan CSS atau TypeScript saja tidak memerlukan `tools/update-prod-static` lengkap.
`tools/webpack` berjalan dengan `python3` stdlib dan `node_modules` saja, tanpa virtualenv.
Pasang dependensi dengan `pnpm install --frozen-lockfile`, lalu jalankan `python3 tools/webpack --quiet`.

Webpack tetap memerlukan berkas hasil generate berikut. Siapkan lebih dulu:

| Berkas | Cara membangun | Perlu virtualenv |
| --- | --- | --- |
| `web/generated/timezones.json` | `python3 tools/setup/build_timezone_values` | Tidak |
| `web/generated/pygments_data.json` | `tools/setup/build_pygments_data` | Ya, paket `pygments` saja |
| `web/generated/supported_browser_regex.ts` | `node tools/setup/build_supported_browser_regex.ts` | Tidak |
| `web/generated/emoji/` dan `web/generated/emoji-styles/` | `tools/setup/emoji/build_emoji` | Ya, dan perlu `vips` serta root |

`build_emoji` memerlukan program `vips`, akses root, dan `orjson`. Image dasar menyediakan
ketiganya. Jalankan skrip itu di dalam container sekali pakai bila mesin build tidak terprovisi:

```bash
docker run --rm --network none --no-healthcheck \
  -v <dir-kerja>:/work -v <dir-cache>:/srv/zulip-emoji-cache \
  --entrypoint /home/zulip/deployments/current/.venv/bin/python \
  grow-team/server:<tag> /work/tools/setup/emoji/build_emoji
```

Direktori kerja memerlukan `tools/`, `scripts/`, `version.py`, `web/images/zulip-emoji/`,
`zerver/management/data/unified_reactions.json`, `zerver/lib/emoji_utils.py`, dan dua paket
`node_modules/emoji-datasource-*`. Berkas `zerver/lib/emoji_utils.py` tidak memakai Django.

Cache emoji memakai symlink absolut ke `/srv/zulip-emoji-cache`. Path itu hanya ada di dalam
container. Salin hasilnya dengan dereference dari dalam container, bukan dari host.
Sesudah itu salin `web/emoji` ke `web/generated/emoji`, `web/emoji-styles` ke
`web/generated/emoji-styles`, dan `static` ke `static/generated/emoji`.

Emoji dan pusat bantuan tidak berubah pada rilis yang hanya mengubah CSS. Pakai ulang hasil
build sebelumnya dari `/opt/grow-team/builds/<commit>/app/` pada kasus itu.


Paketkan build dari checkout yang bersih:

```bash
python3 tools/grow-team/package_image.py /tmp/grow-team-image.tar.gz
git rev-parse HEAD
```

Script menolak direktori kerja kotor serta source frontend atau bantuan yang lebih baru dari hasil build.
Bangun ulang aset setelah perubahan source; pemeriksaan waktu file bukan pengganti build baru.
Arsip hanya memasukkan source runtime yang terlacak Git dan direktori hasil build yang disebutkan dalam script.
Credentials dan virtualenv lokal tidak masuk ke paket.

Salin arsip ke direktori build baru di `/opt/grow-team/builds/` pada VPS.
Ekstrak arsip dalam direktori tersebut, lalu jalankan build melalui engine khusus Grow Team:

```bash
sudo env DOCKER_HOST=unix:///run/grow-team-docker/docker.sock \
  DOCKER_CONFIG=/opt/grow-team/lib/docker \
  /opt/grow-team/bin/docker buildx build --network none --progress plain --load \
  --build-arg GROW_TEAM_REVISION=<commit-penuh> \
  --tag grow-team/server:12.2-grow-team.4 <direktori-build>
```

Docker Buildx 0.30.0 terpasang pada direktori plugin engine Grow Team.
Build memakai dependensi base image, mengumpulkan aset, dan mengompilasi katalog bahasa.
Perakitan berjalan sebagai user `zulip` agar kepemilikan file sesuai dengan runtime.
Engine dan proses build berada dalam batas sumber daya `grow-team.slice`.
Konfigurasi build hanya berlaku dalam proses build; credentials produksi tidak diperlukan.
Gunakan tag baru pada rilis berikutnya, lalu perbarui `compose.override.yaml`.
Build menjalankan pemeriksaan Django sebagai user aplikasi setelah aset dirakit.
Ulangi pemeriksaan pada image hasil ekspor sebelum deployment:

```bash
sudo env DOCKER_HOST=unix:///run/grow-team-docker/docker.sock \
  DOCKER_CONFIG=/opt/grow-team/lib/docker \
  /opt/grow-team/bin/docker run --rm --network none --no-healthcheck \
  --user zulip --entrypoint /home/zulip/deployments/current/.venv/bin/python \
  grow-team/server:12.2-grow-team.4 /opt/grow-team-build/check_image.py
```

Sebelum mengganti aplikasi:

1. Jalankan backup dan periksa checksum.
2. Simpan salinan konfigurasi Compose serta ID image yang sedang aktif.
3. Pasang konfigurasi image baru dan jalankan `compose.sh config --quiet`.
4. Jalankan `compose.sh up -d --no-deps --no-build --wait --wait-timeout 300 zulip`.
5. Periksa login, sesi anggota, bantuan, logo, koneksi realtime, dan status container.

Jika pemeriksaan gagal, pulihkan file Compose sebelumnya dan jalankan perintah pada langkah empat.
Database dan layanan pendukung tidak dibuat ulang untuk rilis branding ini.
Pertahankan image lama sampai pemeriksaan selesai.

`rebrand_pilot.py` hanya berlaku untuk data bawaan pilot pertama di `team.growc.id`.
Script memeriksa realm, kanal, bot, riwayat edit, dan hash isi enam pesan sebelum perubahan.
Script tidak mengubah pesan anggota dan tidak membuat pesan atau email baru.
Jalankan `run()` melalui `manage.py shell` untuk audit tanpa perubahan.
Sesudah backup dan audit lulus, `run(apply=True)` menerapkan perubahan dalam satu transaksi.
Bersihkan cache Grow Team dan restart proses aplikasi setelah penerapan.
Script menolak penerapan ulang atau data yang tidak lagi sama dengan seed yang diaudit.
Penerapan pilot pada 2026-09-21 sudah selesai; jangan jalankan kembali.
Lihat [rilis branding awal](BRANDING-VERIFICATION.md) dan
[verifikasi bot serta Linkifiers](SYSTEM-BOT-VERIFICATION.md) untuk hasil pemeriksaan.

## Domain bot sistem

Konfigurasi `SETTING_INTERNAL_BOT_DOMAIN=team.growc.id` menentukan alamat bot sistem.
Nama setting, daftar bot lintas realm, dan pembuatan bot memakai domain yang sama.
Gunakan setting domain ini untuk deployment Grow Team.
Prosedur ini tidak mendukung override alamat per bot yang berbeda dari domain tersebut.
Alamat bot adalah identitas aplikasi; pengirim email transaksi tetap `noreply@growc.id`.

Database instalasi lama memerlukan perubahan alamat yang terpisah dari konfigurasi.
`rebrand_system_bots.py` hanya menerima host `team.growc.id` dan realm sistem ID 1.
Script mempertahankan akun dan hanya memperbarui `email` serta `delivery_email`.
Script menolak data campuran, akun yang tidak sesuai, atau benturan alamat.

Setelah backup, salin script ke folder deployment dan jalankan audit sebagai user aplikasi:

```bash
sudo /opt/grow-team/deploy/compose.sh exec -T --interactive=false --user zulip zulip \
  /home/zulip/deployments/current/.venv/bin/python \
  /home/zulip/deployments/current/grow_team/rebrand_system_bots.py \
  --audit --source-domain zulip.com --target-domain team.growc.id
```

Jika audit melaporkan tujuh bot pada domain sumber:

1. Hentikan proses dalam container aplikasi dengan `supervisorctl stop all`.
2. Jalankan perintah audit di atas dengan `--apply` sebagai pengganti `--audit`.
3. Bersihkan cache default Grow Team sementara proses aplikasi masih berhenti.
4. Pasang konfigurasi domain dan image baru, lalu buat ulang hanya container aplikasi.
5. Jalankan audit kembali; hasilnya harus menyebut domain target.
6. Periksa daftar bot, lookup bot, avatar, pesan, dan event queue.

Audit tidak menulis data. Jangan jalankan `--apply` lagi setelah alamat sudah berubah.
Untuk pemulihan, hentikan proses aplikasi dan gunakan `--reverse` pada script yang sama.
Bersihkan cache, pulihkan konfigurasi serta image sebelumnya, lalu hidupkan aplikasi.
Jangan hidupkan proses aplikasi dengan alamat database dan konfigurasi yang berbeda.
Pertahankan ID bot dan riwayat pesan saat memulihkan perubahan ini.

Uji prosedur secara terpisah dengan database SQLite sementara:

```bash
.venv/bin/python deploy/grow-team/tests/test_system_bot_rebrand.py
```

Pengujian ini mengganti modul model dan cache hanya dalam proses pengujian tersebut.
Jangan gabungkan dengan proses suite Django utama.
Pengujian ini tidak memeriksa penguncian PostgreSQL atau perilaku cache produksi.

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
