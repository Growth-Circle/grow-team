# 14 · Runner: halaman, flow UI/UX, dan backend

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

Tanggal: 24 September 2026 · Prototipe: Dashboard → sidebar **Runner** (atau Pengaturan → Agen & runner → "+ Pasangkan perangkat", atau Tambah agen coding → "+ Pasangkan perangkat").
Dasar: [riset 04](../research/04-runner-cloud-vs-local.md) · tipe agen: [riset 03](../research/03-tipe-agen-kerja-vs-coding.md) · agen: [12](12-tambah-agen.md).

## Tujuan

Satu tempat untuk melihat dan menambah **tempat agen berjalan**:

- **Cloud dari sanji** (managed): paling mudah, selalu online.
- **VPS milik pengguna**: satu perintah, data tetap di server pengguna.
- **Laptop/PC (lokal)**: install CLI, lalu setujui pairing dari browser atau HP.

## Kenapa halaman sendiri

- Runner adalah infrastruktur yang bisa offline, basi, atau disusupi. Best practice: inventaris selalu terlihat dan runner basi dihapus agresif (riset 04 §2.1).
- Pairing perangkat dengan layar lewat sistem terkontrol (kode pendek + persetujuan). Token panjang (`sjr_…`) hanya untuk VPS tanpa layar, lihat §E.1.

---

## A. Halaman **Runner** (`view: runners`)

### Isi

1. Header "Tempat agen berjalan / Runner" + **+ Tambah runner**.
2. **4 kartu ringkasan**: Online (x dari N) · Cloud (sanji + VPS) · Lokal · Perlu dicek (offline/basi/dicabut).
3. Filter: Semua / Cloud / Lokal / Bermasalah.
4. **Kartu runner** (grid, 1 kolom di HP):
   - Ikon tipe (SJ/VPS/PC) + nama + badge tipe (CLOUD SANJI / VPS / LOKAL) + metadata (OS, provider/region, pemilik).
   - Pill status: **Online** (hijau) · **Offline** (merah) · **Basi** (abu, tidak aktif ≥ 7 hari) · **Dicabut**.
   - 3 metrik: Job aktif · Agen yang memakai · Terakhir aktif.
   - Label: `kerja`, `coding`, `grup:server`, `repo:landing`, region.
   - Peringatan kontekstual (merah muda): offline → job dialihkan ke runner lain di grup yang sama. Basi → dicabut otomatis dalam N hari.
   - Border putus-putus untuk runner yang tidak online.
5. Badge di sidebar "Runner" = jumlah runner yang tidak online.

### Aksi per status

| Status / tipe      | Tombol                                   | Efek                                                                                                                                               |
| ------------------ | ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| Online · lokal/VPS | **Log** · **Rotasi token** · **Cabut**   | Log dibuka. Rotasi: token baru diterbitkan dan runner otomatis memakainya (toast). Cabut: status jadi Dicabut, job berjalan dihentikan dengan aman |
| Online · cloud     | **Log** · **Ubah ukuran** · **Hentikan** | Ubah ukuran tanpa menghentikan job. Hentikan: agen kerja menunggu sampai dinyalakan lagi                                                           |
| Offline            | **Ingatkan pemilik** · **Cabut**         | Kaki mengirim DM ke pemilik                                                                                                                        |
| Basi               | **Hapus sekarang** (primer)              | Runner dihapus dan credential dicabut                                                                                                              |
| Dicabut            | **Hapus dari daftar**                    | Kartu hilang                                                                                                                                       |

---

## B. Flow **Tambah runner** (`view: addRunner`), 3 langkah

Stepper: **Tipe → Pengaturan → Sambungkan/Menyiapkan**. Tombol bawah: Batal/← Kembali dan Lanjut. Semua tombol minimal 44px (ramah jari).

### Langkah 1 · Tipe

Tiga kartu:
| Kartu | Tag | Poin |
|---|---|---|
| Cloud dari sanji | PALING MUDAH | Siap ±1 menit, selalu online · Jakarta/Singapura · biaya per menit |
| VPS kamu sendiri | SELALU ONLINE | Satu perintah/cloud-init · hanya koneksi keluar 443 · VPS dibayar pengguna |
| Laptop / PC | GRATIS | Pakai repo & tools lokal · setujui pairing dari HP · hanya saat perangkat menyala |
Memilih kartu otomatis mengisi nama default. Lanjut tanpa memilih → toast.

### Langkah 2a · Cloud dari sanji

- **Nama**, **Region** (Jakarta/Singapura), **Ukuran** (Kecil 2 vCPU / Sedang 4 / Besar 8, dengan perkiraan biaya per menit aktif), **Jaringan keluar** (Hanya layanan terhubung, disarankan / Internet terbuka).
- **Buat runner cloud** → langsung ke langkah 3 (Menyiapkan).

### Langkah 2b · VPS sendiri

- **Nama**, **Cara pasang**: _Satu perintah_ (`curl … | sh -s -- --runner --name … --group …`) / _Cloud-init_ (untuk kolom user data DigitalOcean, Vultr, IDCloudHost, AWS) / _Docker_ (`docker run … ghcr.io/sanji/runner`).
- Kotak perintah gelap + **Salin**. Perintah ikut berubah sesuai nama & grup.
- **Grup**: Server / Produksi / Tanpa grup (dipakai untuk failover).
- **Saya sudah menjalankan perintah →** → langkah 3 (Sambungkan).

### Langkah 2c · Laptop / PC

- Tab OS: macOS (`brew install sanji-space/tap/sanji`) / Linux (`curl … | sh`) / Windows (WSL2, `winget install Sanji.CLI`).
- 1 · Install CLI → 2 · `sanji runner login --name …` → 3 · (opsional) `sanji runner service install` (launchd/systemd/Task Scheduler) supaya otomatis jalan saat login.
- Catatan: butuh Docker/Podman, dan agen hanya berjalan saat perangkat menyala.

### Langkah 3 · Sambungkan (VPS & lokal)

Dua panel berdampingan (bertumpuk di HP):

1. **Menunggu runner terhubung…** (titik kuning berdenyut) + **kode besar** `SNJ-4829` di kotak putus-putus + hitung mundur kedaluwarsa (10 menit, polling tiap 5 detik). Instruksi: buka `sanji.space/device` di HP/laptop.
   - Tombol prototipe **▶ Simulasikan: runner menjalankan perintah** memunculkan panel 2.
2. **Layar persetujuan `/device`** (dirancang untuk HP, bingkai 360px):
   - Judul "Sambungkan perangkat?" + "Kode SNJ-4829 cocok".
   - Info perangkat: nama, sistem (OS + arsitektur), **lokasi IP**, workspace, waktu permintaan.
   - Peringatan anti-phishing: "Setujui hanya jika kamu sendiri yang menjalankan perintah ini."
   - **Tolak** / **Setujui** (48px). Tolak → kode baru dibuat, toast "kode lama tidak berlaku". Setujui → pemeriksaan.
3. **Pemeriksaan otomatis** (progress + checklist bertahap): token runner diterbitkan → container rootless → adapter ACP (`claude-agent-acp`, `codex-acp`) → API key di environment (tidak dikirim ke sanji) → heartbeat pertama.
4. Selesai: kotak hijau "{nama} siap menerima job" + **Lihat runner** / **Buat agen coding →** (membuka wizard doc 12). Runner baru muncul di daftar.

### Langkah 3 · Menyiapkan (cloud)

Checklist: siapkan VM di region → pasang runtime & image agen → uji koneksi ke sanji → daftarkan ke workspace. Lalu kotak hijau yang sama.

---

## C. Shell responsif (laptop & HP)

| Lebar   | Perilaku                                                                                                                                                                                            |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ≥ 820px | Sidebar tetap 260px di kiri                                                                                                                                                                         |
| < 820px | **Top bar gelap**: ☰ (buka sidebar) · avatar + nama workspace · ⌕ (command palette). Sidebar menjadi **drawer kiri** (maks. 300px / 86vw) dengan scrim. Memilih menu/ruang otomatis menutup drawer |
| Semua   | Target sentuh ≥ 44px di halaman Runner & Tambah runner. Grid kartu turun menjadi 1 kolom. Padding halaman mengecil (16px)                                                                           |
| HP      | Drawer detail sudah `min(480px,100vw)` = layar penuh. Tugas: tombol "Mulai →" tetap tersedia sebagai pengganti drag                                                                                 |

---

## D. Kebutuhan backend

### Data

| Entitas               | Field                                                                                                                                                                                                                                                                                                                                   |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Runner`              | `id`, `realm_id`, `owner_user_id`, `name`, `kind` (`cloud_managed`/`vps`/`local`), `os`, `arch`, `region`, `size`, `group`, `labels[]`, `status` (`online`/`offline`/`stale`/`revoked`), `last_heartbeat_at`, `ip_geo`, `capabilities` (container runtime, adapter ACP + versi, key tersedia: boolean), `max_parallel`, `egress_policy` |
| `RunnerCredential`    | hash token, `issued_at`, `rotated_at`, `expires_at`. Sudah ada: hash + rotasi ([security.md](../security.md))                                                                                                                                                                                                                           |
| `DeviceAuthorization` | `device_code` (entropi tinggi, tidak ditampilkan), `user_code` (8 char tanpa huruf ambigu), `interval` = 5 dtk, `expires_at` = +10 mnt, `status` (pending/approved/denied/expired), `requested_meta` (hostname, OS, IP, geo), `approved_by`                                                                                             |
| `CloudRunnerInstance` | `provider_vm_id`, `region`, `size`, `state`, `billing_minutes`                                                                                                                                                                                                                                                                          |

### API

| Method | Endpoint                                                                       | Fungsi                                                                                                                 |
| ------ | ------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| POST   | `/api/v1/sanji/device/code`                                                    | Dipanggil CLI → `{device_code, user_code, verification_uri, interval, expires_in}`                                     |
| POST   | `/api/v1/sanji/device/token`                                                   | Polling CLI. Balasan `authorization_pending` / `slow_down` (+5 dtk) / `access_denied` / `expired_token` / token runner |
| GET    | `/api/v1/sanji/device/{user_code}`                                             | Halaman `/device` mengambil meta perangkat                                                                             |
| POST   | `/api/v1/sanji/device/{user_code}/approve` · `/deny`                           | Butuh sesi login + izin `runner` (doc 10)                                                                              |
| GET    | `/api/v1/sanji/runners?filter=`                                                | Daftar + status + metrik                                                                                               |
| POST   | `/api/v1/sanji/runners/cloud`                                                  | Buat runner managed (region, size, egress) → provisioning async + event progres                                        |
| POST   | `/api/v1/sanji/runners/{id}/rotate` · `/revoke` · `/stop` · `/resize` · DELETE | Aksi kartu                                                                                                             |
| GET    | `/install.sh`, `ghcr.io/sanji/runner:12`                                       | Installer & image. Checksum + signature dipublikasikan                                                                 |

### Aturan

1. Runner **hanya outbound HTTPS/443** (long-poll/WebSocket ke broker). Tidak ada port masuk.
2. **1 job = 1 container ephemeral**. Workspace & cache credential dihapus setelah job (sudah ada, lihat [agent-sandbox-design.md](../agent-sandbox-design.md)).
3. **Heartbeat** tiap 15 dtk. Tanpa heartbeat 60 dtk → `offline`. 7 hari → `stale` (notifikasi pemilik). 30 hari → credential dicabut otomatis.
4. **Failover:** job coding di runner offline menunggu maks. 30 menit (bisa diatur), lalu pindah ke runner online di **grup & label** yang sama jika pemilik agen mengizinkan. Jika tidak, kartu job BERHENTI + Coba lagi (doc 13).
5. **Rate limit** `user_code`: 10 percobaan / 10 menit per IP & per workspace.
6. Runner milik orang lain hanya bisa dipakai jika pemiliknya membagikan ke workspace/grup.
7. Event realtime: `sanji_runner_status`, `sanji_device_request` (supaya halaman menunggu langsung berubah saat disetujui dari HP), `sanji_cloud_provision_progress`.

### CLI `sanji`

| Perintah                                             | Fungsi                                                                        |
| ---------------------------------------------------- | ----------------------------------------------------------------------------- |
| `sanji runner login [--name] [--group] [--headless]` | Device flow. `--headless` untuk cloud-init: kode dikirim ke email Owner/Admin |
| `sanji runner status`                                | Status koneksi, adapter yang terdeteksi, job aktif                            |
| `sanji runner service install/uninstall`             | Layanan launchd/systemd/Task Scheduler                                        |
| `sanji runner doctor`                                | Cek container runtime, adapter ACP, env key, koneksi 443                      |
| `sanji runner logout`                                | Cabut credential lokal + beri tahu server                                     |

## E. Keputusan (24 Sep 2026)

1. **VPS headless memakai token pendaftaran sekali pakai**, bukan kode yang dikirim ke Owner. Alasannya: paling mudah (cukup salin-tempel satu perintah atau cloud-init, tanpa orang di depan terminal) dan sudah jadi pola industri (runner GitHub). Aturannya:
   - Token `sjr_…` dibuat saat halaman langkah 2b dibuka. **Sekali pakai, berlaku 1 jam**, terikat ke workspace + grup, dan hangus setelah dipakai atau kedaluwarsa.
   - Hanya Owner/Admin/pemegang izin `runner` yang bisa membuatnya. Token terlihat sekali lalu disembunyikan (`sjr_7Kx2…`) dengan tombol "Buat token baru".
   - Credential runner hasil pendaftaran tetap dirotasi berkala dan bisa dicabut dari kartu runner.
   - **Laptop/PC tetap memakai device code** (ada orang di depan layar, dan persetujuan dari HP lebih aman daripada token yang bisa bocor).
2. **Harga runner cloud:** kuota menit termasuk paket (Gratis 100, Tim 1.000, Bisnis 5.000), kelebihannya **Rp100/200/400 per menit** (Kecil/Sedang/Besar), dihitung hanya saat job berjalan. Runner lokal/VPS gratis. Lihat riset harga (privat).
3. **PWA + Web Push masuk MVP.** Lihat [`15-pwa-notifikasi.md`](15-pwa-notifikasi.md).

## F. Pertanyaan terbuka

- Perlu opsi token pendaftaran **multi-pakai** untuk autoscaling VPS (mis. 10 mesin sekaligus) di paket Bisnis?
