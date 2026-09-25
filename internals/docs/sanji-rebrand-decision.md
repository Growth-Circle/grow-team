# Keputusan rebrand Sanji dan domain kustom

Tanggal: 2026-09-24, Asia/Jakarta. Koreksi review: 2026-09-25.

Status: **default tim.** Butir bertanda R menunggu konfirmasi Rama (lihat
[§7](#7-keputusan-terbuka-untuk-dokumen-ini)). Tim memakai default di bawah
sampai Rama menjawab lain ([Daftar keputusan default](#daftar-keputusan-default-p-t-q)).
Butir bertanda T adalah default teknis; tim memakainya langsung.

Sumber: keputusan orkestrator Sanji dan rencana kerja tim (dokumen kerja, di
luar repo ini). [Daftar keputusan default](#daftar-keputusan-default-p-t-q)
menyalin tabelnya di bawah, supaya dokumen ini berdiri sendiri tanpa rujukan
keluar repo.

## 1. Keputusan

1. Produk bernama Sanji. Wordmark: `sanji.space`. Nama pendek: `sanji`.
2. Sanji adalah fork yang sama. Chat, pesan, unggahan, user, dan agen tidak berubah.
3. `team.growc.id` menjadi domain kustom workspace Growth Circle.
4. Workspace baru memakai `{slug}.sanji.space`.
5. `https://sanji.space` adalah landing (Worker `sanji-landing`, dibangun terpisah).

## 2. Fakta produksi (dicek read-only, 2026-09-24)

| Item                                       | Nilai                                                                                                                                                                                 |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `EXTERNAL_HOST`                            | `team.growc.id`                                                                                                                                                                       |
| `REALM_HOSTS`                              | `{}`                                                                                                                                                                                  |
| `ROOT_DOMAIN_LANDING_PAGE`                 | `False`                                                                                                                                                                               |
| `ROOT_SUBDOMAIN_ALIASES`                   | `['www']`                                                                                                                                                                             |
| `SOCIAL_AUTH_SUBDOMAIN`                    | `None`                                                                                                                                                                                |
| `INTERNAL_BOT_DOMAIN`, `FAKE_EMAIL_DOMAIN` | `team.growc.id`                                                                                                                                                                       |
| Backend login                              | `EmailAuthBackend` saja                                                                                                                                                               |
| `OPEN_REALM_CREATION`                      | `False`                                                                                                                                                                               |
| Realm 1                                    | `zulipinternal`, 7 bot sistem                                                                                                                                                         |
| Realm 2                                    | `string_id` kosong (root), nama "Grow Team", 5 manusia aktif, 2 bot aktif, 18 ruang aktif, 243 pesan, 4 lampiran, bahasa `id`                                                         |
| Agen realm 2                               | 1 profil "Opus" (`endpoint`, `answer`, `enabled`, `ready`), 1 runner "Grow Team runner" (`server`, `online`), 1 provider "9router Opus 5" (`cc/claude-opus-5`), 21 job, 0 kartu tugas |
| Tunnel                                     | `grow-team-cloudflared.service`, dikelola jarak jauh dengan berkas token                                                                                                              |

## 3. Fakta kode yang menentukan rencana

| Fakta                                                                                                                                                                                  | Sumber                                                                                           |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Realm root selalu tinggal di `EXTERNAL_HOST`. `REALM_HOSTS` tidak berlaku untuk `string_id` kosong.                                                                                    | `zerver/models/realms.py:1266`                                                                   |
| Host kustom dipetakan lewat `REALM_HOSTS` sesudah cek sufiks `.{EXTERNAL_HOST}`.                                                                                                       | `zerver/lib/subdomains.py:30`                                                                    |
| **Ganti subdomain (slug) mencabut semua sesi.** Perintah memanggil `delete_realm_user_sessions(realm)`. Semua user harus login lagi.                                                   | `zerver/actions/create_realm.py:48`                                                              |
| Perintah punya opsi `--skip-redirect`. Tanpa opsi ini, perintah membuat realm pengalih nonaktif di slug lama, dan slug lama tidak bisa dipakai lagi sampai realm pengalih itu dihapus. | `zerver/management/commands/change_realm_subdomain.py`, `zerver/actions/create_realm.py:113-114` |
| Zulip sudah melarang subdomain `www`, `api`, `auth`, `admin`, `status`, `help`, dan `docs`. Subdomain `app`, `sanji`, dan `device` belum dilarang.                                     | `zerver/lib/name_restrictions.py`                                                                |
| Skrip rebrand lama menolak host selain `team.growc.id`.                                                                                                                                | `deploy/grow-team/rebrand_pilot.py:20`, `rebrand_system_bots.py:20`                              |
| Dengan `ROOT_DOMAIN_LANDING_PAGE=True`, pemeriksaan subdomain kosong pada halaman pendaftaran menolaknya (root menjadi landing, bukan realm baru).                                     | `zerver/forms.py:95-98`                                                                          |

## 4. Topologi domain target

| Host                                  | Dilayani oleh                                                                                              | Isi                                                                                            |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `sanji.space`, `www.sanji.space`      | Worker `sanji-landing`                                                                                     | Landing, waitlist, halaman legal, dan pengalih ke app.                                         |
| `app.sanji.space`                     | Worker proxy `sanji-app-proxy` (custom domain Worker, akun Prazze), meneruskan ke `team.growc.id`          | Halaman root: cari akun (`/accounts/find/`), buat workspace (`/new/`), dan pengalih `/device`. |
| `growc.sanji.space`                   | Worker proxy `sanji-app-proxy`, meneruskan ke `team.growc.id`                                              | Alamat pendek workspace Growth Circle. Browser dialihkan ke `team.growc.id`.                   |
| `team.growc.id`                       | Zulip langsung (domain asal, tidak berubah)                                                                | Workspace Growth Circle (domain kustom).                                                       |
| `{slug}.sanji.space` (workspace baru) | Worker proxy `sanji-app-proxy`, ditambah satu per satu sebagai custom domain (bukan wildcard di rilis ini) | Workspace baru.                                                                                |
| `auth.sanji.space`                    | Zulip (`SOCIAL_AUTH_SUBDOMAIN`)                                                                            | Callback OAuth, hanya bila login Google aktif.                                                 |
| `zulipinternal.sanji.space`           | Zulip                                                                                                      | Realm bot sistem. Tidak untuk pengguna.                                                        |

`app.sanji.space` dan `growc.sanji.space` tidak lewat tunnel: Worker `sanji-app-proxy`
membuat record DNS-nya sendiri lewat custom domain Cloudflare, tanpa butuh izin DNS
Edit atau Tunnel Edit terpisah (lihat [§7](#7-keputusan-terbuka-untuk-dokumen-ini) D-01).

## 5. Hubungan app dan landing

Landing tidak direncanakan ulang di sini. Tabel ini hanya titik sambung.

| Kebutuhan                  | Rancangan                                                                                                                                                                                                  |
| -------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tombol "Masuk" di landing  | Form "alamat workspace" → `https://{slug}.sanji.space/login/`. Link "Lupa alamat?" → `https://app.sanji.space/accounts/find/`.                                                                             |
| Link root dari email Zulip | Zulip membentuk link root dengan `EXTERNAL_HOST`. Worker mengalihkan (302/308) `/login`, `/accounts`, `/new`, `/device`, `/help`, `/static`, dan `/install.sh` ke `app.sanji.space` dengan path yang sama. |
| Pairing `/device`          | CLI mencetak `https://{host workspace}/device`. Host itu juga berlaku untuk `team.growc.id`. `sanji.space/device` hanya form pengalih.                                                                     |
| Waitlist                   | Worker menyimpan data. Tombol "Undang" di MVP = operator membuat link pembuatan realm (`manage.py generate_realm_creation_link`).                                                                          |
| Aset brand                 | Satu sumber: brand pack v2. Landing dan app memakai token warna yang sama.                                                                                                                                 |
| Halaman legal              | Tinggal di landing. Footer app menautkan ke `https://sanji.space/…`.                                                                                                                                       |

## 6. Tahap

| Tahap | Isi                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Perubahan data                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| 1     | Ganti nama produk di UI, email, bantuan, aset, dan dokumen. `EXTERNAL_HOST` tetap.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Tidak ada.                                     |
| 2     | Jendela kerja singkat (gerbang persetujuan Rama, lihat [§7](#7-keputusan-terbuka-untuk-dokumen-ini) D-03). Umumkan ke anggota dulu: semua sesi tercabut. Backup dengan checksum. Hentikan timer, runner, lalu semua servis. Ganti `string_id` realm 2 dengan `manage.py change_realm_subdomain -r 2 growc --skip-redirect` (**mencabut semua sesi**, §3). Restart memcached. Set env: `SETTING_EXTERNAL_HOST=sanji.space`, `SETTING_REALM_HOSTS={"growc": "team.growc.id"}`, `SETTING_ROOT_SUBDOMAIN_ALIASES=["www","app"]`, `SETTING_ROOT_DOMAIN_LANDING_PAGE=True`, `SETTING_STATIC_URL=https://team.growc.id/static/`, `SETTING_INTERNAL_BOT_DOMAIN=team.growc.id`. Jalankan tabel verifikasi. Hidupkan runner dan timer kembali. | `Realm.string_id` realm 2. Semua sesi dicabut. |
| 3     | Opsional. Pindah domain bot sistem ke `sanji.space` dengan pola audit/apply/reverse dari `rebrand_system_bots.py`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Email bot sistem.                              |

## 7. Keputusan terbuka untuk dokumen ini

Kolom "Default" memakai nilai terbaru dari [Daftar keputusan default](#daftar-keputusan-default-p-t-q)
di bawah (menggantikan usulan awal bila keduanya beda). Kolom "Status" mengikuti
aturan yang sama: tim memakai default ini sekarang; butir R tetap menunggu
konfirmasi Rama.

| ID   | Keputusan                                  | Default                                                                                                                                              | Status                 |
| ---- | ------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| D-01 | Slug baru realm 2                          | `growc` (P-20). **Mengganti slug mencabut semua sesi realm 2** (§3, §6 Tahap 2).                                                                     | R, menunggu konfirmasi |
| D-02 | Nama workspace realm 2 ("Grow Team")       | Tetap "Grow Team". Owner dapat mengganti di Pengaturan (P-22).                                                                                       | R, menunggu konfirmasi |
| D-03 | Waktu mulai Tahap 2                        | Di rilis produksi berikutnya (P-21). Verifikasi rilis wajib memeriksa URL `sanji.space`.                                                             | R, menunggu konfirmasi |
| D-04 | Domain bot sistem                          | Tetap `team.growc.id` di Tahap 2.                                                                                                                    | T, dipakai langsung    |
| D-05 | Domain pengirim email workspace baru       | `noreply@growc.id`. `INSTALLATION_NAME` = `sanji` (P-23). Pengirim app tetap `noreply@growc.id` di rilis ini.                                        | R, menunggu konfirmasi |
| D-06 | Prefix ID kartu `GT-`                      | Kolom `task_id_prefix`. Realm 2 tetap `GT`. Workspace baru: inisial nama (T-26).                                                                     | T, dipakai langsung    |
| D-07 | Nama runner "Grow Team runner"             | Pemilik mengganti lewat UI. Bukan migrasi.                                                                                                           | T, dipakai langsung    |
| D-08 | Agen bawaan Kaki, Ayame, Matcha di realm 2 | Buat di runner dan provider yang sudah ada (P-18). `default_profile` = Kaki. Profil "Opus" tetap.                                                    | R, menunggu konfirmasi |
| D-09 | Repo publik: riset 05 dan HTML desain      | Jangan commit keduanya (P-24). **Sudah diterapkan**: repo ini tidak commit `research/05-saran-harga.md`, berkas HTML, `support.js`, atau `uploads/`. | R, menunggu konfirmasi |
| D-10 | Cek merek "sanji" di PDKI/DJKI             | Rama memeriksa sebelum landing publik (P-33).                                                                                                        | R, tindakan Rama       |

## 8. Yang tidak berubah

- URL `team.growc.id`, termasuk link di pesan lama.
- Origin runner (`grow-agent` di `server-gteam` memakai `team.growc.id`). Runner tidak perlu pairing ulang.
- Email bot di realm 2 (`@team.growc.id`), karena host realm tetap.
- Target `fathom-zulip` (`team.growc.id`).
- Berkas unggahan. Path memakai ID realm.
- Semua identifier internal (`grow-team`, `grow-agent`, dll., lihat aturan rename).

## 9. Risiko

| Risiko                                                                         | Pengendalian                                                                                                                          |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| Tahap 2 mencabut semua sesi dan event queue.                                   | Pakai jendela kerja. Umumkan ke 5 anggota. Uji dulu pada restore backup di `rama-tuf`.                                                |
| Pengirim transaksional workspace baru belum di `sanji.space`.                  | Pengirim app tetap `noreply@growc.id` di rilis ini (D-05). Landing sudah memakai `halo@sanji.space` secara terpisah untuk waitlist.   |
| Dukungan `SETTING_REALM_HOSTS` (nilai dict) di image docker-zulip belum pasti. | Uji di harness sebelum Tahap 2.                                                                                                       |
| Rute model produksi lewat 9router.                                             | Pakai API key resmi untuk setiap workspace klien sebelum workspace itu aktif.                                                         |
| Sesi tidak berlaku lintas workspace (cookie per host).                         | Dropdown workspace mengikuti default login per workspace (lihat Q-01 di [Daftar keputusan default](#daftar-keputusan-default-p-t-q)). |

## 10. Rollback

Urutan (Fase 3 §6 Tahap 2, dari `manage.py shell` kecuali disebut lain):

1. `supervisorctl stop all`.
2. Di `manage.py shell`: `do_change_realm_subdomain(realm, "", acting_user=None, add_deactivated_redirect=False)`. Tanpa `add_deactivated_redirect=False`, langkah ini gagal pada percobaan kedua dengan pesan "Subdomain is already in use", dan gagal total selama `SETTING_ROOT_DOMAIN_LANDING_PAGE=True` masih aktif.
3. `compose.sh restart memcached`.
4. Pulihkan `compose.override.yaml` dari salinan `.bak-pre-sanji`, lalu `compose.sh up -d`.
5. Cek `GET https://team.growc.id/login/` = 200.
6. Hidupkan runner dan semua timer kembali.

## 11. Verifikasi

1. `GET https://team.growc.id/login/` = 200.
2. Workspace uji di `{slug}.sanji.space` bisa login.
3. Runner `online` dan profil "Opus" siap.
4. Mention ke "Opus" membuat job dan jawaban.
5. Unggah lalu unduh berkas memberi isi yang sama.
6. Email reset password terkirim.
7. Jumlah pesan (≥ 243) dan lampiran (≥ 4) di realm 2 tidak turun.
8. Email bot di realm 2 tidak berubah.

## Daftar keputusan default (P, T, Q)

Salinan tabel default rencana kerja Sanji, supaya butir R/T di atas tidak
butuh rujukan keluar repo. Kolom Status: "default, menunggu konfirmasi" untuk
butir yang Rama belum menjawab; "default teknis, dipakai" untuk butir tim
yang berjalan langsung. Koreksi yang sudah disetujui Rama: **D-01** (Worker
proxy tanpa tunnel, dipakai di [§4](#4-topologi-domain-target) dan [§6](#6-tahap))
dan **D-02** (runner cloud memakai Cloudflare Containers, bukan AWS, dipakai
di P-05 dan T-23 di bawah) menggantikan nilai lama pada baris itu.

### P-01..P-37 — Keputusan produk

| ID   | Keputusan                           | Default                                                                                                                                                                                                                                     | Status                       |
| ---- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| P-01 | SDK agen kerja                      | Tetap `@anthropic-ai/sdk` di jalur cepat. OpenRouter hanya sebagai `base_url` + key sesudah probe lulus.                                                                                                                                    | default teknis, dipakai      |
| P-02 | Model di preset                     | Allowlist provider default = Anthropic. Pil preset hanya menampilkan model yang lolos allowlist.                                                                                                                                            | default teknis, dipakai      |
| P-03 | Fallback provider                   | Default mati.                                                                                                                                                                                                                               | default teknis, dipakai      |
| P-04 | Kunci model di server               | Hanya untuk runner jenis `cloud`. Runner milik pengguna memakai key lokal.                                                                                                                                                                  | default teknis, dipakai      |
| P-05 | "Cloud sanji"                       | Runner jenis `cloud` per workspace, dibuat lewat **Cloudflare Containers** (akun Prazze, ganti usulan AWS lama). Token `sjr_` jenis `cloud` dari `manage.py create_runner_token --cloud`. Token dari UI selalu `vps`. Tanpa pool autoscale. | default, menunggu konfirmasi |
| P-06 | Kaki merutekan brief                | Kaki mode `answer`. Klien mengirim brief ke ruang yang disebut atau ke DM Kaki. Ruang baru hanya diusulkan Kaki, lalu pengguna membuatnya.                                                                                                  | default, menunggu konfirmasi |
| P-07 | Hak Admin atas runner orang lain    | Admin boleh mencabut saja. Admin tidak boleh memakai runner orang lain.                                                                                                                                                                     | default, menunggu konfirmasi |
| P-08 | Daftar workspace lintas realm       | Nama, URL, peran, warna. Tanpa hitungan "Perlu kamu" dari realm lain.                                                                                                                                                                       | default, menunggu konfirmasi |
| P-09 | Siapa boleh memberi tugas di wizard | Default "Hanya aku". Agen bawaan Kaki/Ayame/Matcha dibagikan ke grup `role:members`.                                                                                                                                                        | default, menunggu konfirmasi |
| P-10 | Teks mockup vs aturan copy          | Tata letak 100% mockup. Baris teks mockup yang melanggar aturan copy diganti.                                                                                                                                                               | default teknis, dipakai      |
| P-11 | Pool nama agen                      | 299 nama (300 nama mockup dikurangi 1 nama yang melanggar kriteria panjang). Nama dipesan tetap tidak dipakai untuk agen.                                                                                                                   | default, menunggu konfirmasi |
| P-12 | Grid Hari ini                       | ≥ 1452px: persis mockup. 1068–1451px: dua kolom. < 1068px: satu kolom.                                                                                                                                                                      | default teknis, dipakai      |
| P-13 | Ruang di HP                         | < 820px: kolom topik menjadi baris chip yang bisa di-scroll. Composer menempel di bawah.                                                                                                                                                    | default teknis, dipakai      |
| P-14 | DM, Inbox, Recent                   | Grup "Pesan langsung" di bawah grup ruang, tampil bila ada DM.                                                                                                                                                                              | default teknis, dipakai      |
| P-15 | Pengaturan                          | View tengah dengan tab mockup. Overlay Zulip tetap untuk Profil dan Notifikasi.                                                                                                                                                             | default teknis, dipakai      |
| P-16 | Tema gelap                          | Token gelap dirilis bersama token terang. Sidebar gelap di kedua tema.                                                                                                                                                                      | default, menunggu konfirmasi |
| P-17 | Tagihan                             | Tampilkan paket saat ini dan pemakaian. Sembunyikan kontrol pilih paket yang belum siap.                                                                                                                                                    | default teknis, dipakai      |
| P-18 | Agen bawaan di realm 2              | Buat Kaki, Ayame, Matcha di runner server yang ada, provider 9router yang ada. `default_profile` = Kaki. Profil "Opus" tetap.                                                                                                               | default, menunggu konfirmasi |
| P-19 | Agen kerja di realm 2               | Anggota boleh membuat agen kerja di runner kerja workspace dengan batas biaya workspace.                                                                                                                                                    | default, menunggu konfirmasi |
| P-20 | Slug realm 2 (D-01)                 | `growc`                                                                                                                                                                                                                                     | default, menunggu konfirmasi |
| P-21 | Waktu fase domain (D-03)            | Di rilis produksi berikutnya. Verifikasi wajib memeriksa URL `sanji.space`.                                                                                                                                                                 | default, menunggu konfirmasi |
| P-22 | Nama workspace realm 2 (D-02)       | Tetap "Grow Team". Owner bisa mengganti.                                                                                                                                                                                                    | default, menunggu konfirmasi |
| P-23 | Pengirim email (D-05)               | Tetap `noreply@growc.id`. `INSTALLATION_NAME` = `sanji`.                                                                                                                                                                                    | default, menunggu konfirmasi |
| P-24 | Repo publik (D-09)                  | Jangan commit riset 05, HTML desain, `support.js`, `uploads/`.                                                                                                                                                                              | default, menunggu konfirmasi |
| P-25 | Runner di macOS dan Windows         | Runner resmi = Linux x64 dan WSL2. Tab macOS dan Windows menulis cara lain.                                                                                                                                                                 | default teknis, dipakai      |
| P-26 | Harness Claude Agent                | Kartu Claude Agent tampil hanya bila katalog runner melaporkan `claude-agent-acp` yang lolos probe. Awal: Codex CLI dan Endpoint sendiri.                                                                                                   | default teknis, dipakai      |
| P-27 | Failover runner                     | Tidak dibangun. Job coding di runner offline menunggu 30 menit, lalu BERHENTI dengan alasan dan Coba lagi.                                                                                                                                  | default teknis, dipakai      |
| P-28 | Teks gagal di ruang                 | Tanpa nama perangkat di ruang. Nama perangkat hanya di drawer job untuk pemilik dan Admin.                                                                                                                                                  | default teknis, dipakai      |
| P-29 | Avatar manusia di Ruang             | Lingkaran inisial untuk semua manusia.                                                                                                                                                                                                      | default teknis, dipakai      |
| P-30 | Toolbar composer                    | Toolbar format disembunyikan. Satu tombol lampiran kecil tetap ada.                                                                                                                                                                         | default teknis, dipakai      |
| P-31 | Tanggal selesai ruang proyek        | Kaki hanya mengusulkan arsip lewat DM.                                                                                                                                                                                                      | default, menunggu konfirmasi |
| P-32 | Tombol primer item MENTION          | "Tandai beres" (menutup item). Tombol sekunder "Balas".                                                                                                                                                                                     | default teknis, dipakai      |
| P-33 | Merek dagang "sanji" (D-10)         | Rama memeriksa PDKI/DJKI sebelum landing publik.                                                                                                                                                                                            | tindakan Rama                |
| P-34 | Agen membuat tugas                  | Agen tidak membuat tugas di rilis ini. Nilai `source` `brief`, `agent`, `whatsapp` tetap ada tanpa pembuat.                                                                                                                                 | default, menunggu konfirmasi |
| P-35 | Hak Admin atas agen orang lain      | Admin boleh menjeda agen siapa pun. Admin tidak boleh mengubah, mengaktifkan, atau memakai agen orang lain.                                                                                                                                 | default teknis, dipakai      |
| P-36 | Ringkasan terjadwal vs PRD          | Ringkasan terjadwal hanya untuk ruang opt-in.                                                                                                                                                                                               | default teknis, dipakai      |
| P-37 | Isolasi tenant                      | Semua workspace memakai realm bersama di rilis ini. Gate keamanan BRD tetap berlaku.                                                                                                                                                        | default teknis, dipakai      |

### T-01..T-39 — Default teknis (tim memakai langsung)

| ID   | Topik                                    | Default                                                                                                                           | Status                       |
| ---- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| T-01 | Ambang offline runner                    | 45 detik. Basi = 7 hari. Cabut otomatis = 30 hari.                                                                                | default teknis, dipakai      |
| T-02 | Peringatan runner offline                | 5 menit ke pemilik runner. 15 menit ke Owner dan Admin.                                                                           | default teknis, dipakai      |
| T-03 | Tanda terima mention                     | Reaksi 👀 atas nama bot + indikator mengetik. Tanpa pesan "queued".                                                               | default teknis, dipakai      |
| T-04 | Kapan pesan bot dibuat                   | Pesan agen berisi kartu ANTRE dibuat saat admission. Draft dan hasil memakai pesan yang sama.                                     | default teknis, dipakai      |
| T-05 | Coba lagi                                | `POST /json/agent/jobs/{id}/resume`. Bila state tidak mengizinkan, buat job baru dengan `follows_job_id`.                         | default teknis, dipakai      |
| T-06 | Container per job                        | Aturan hanya untuk jalur code.                                                                                                    | default teknis, dipakai      |
| T-07 | Nama agen unik                           | Unik per workspace (NFKC, tanpa beda huruf besar).                                                                                | default teknis, dipakai      |
| T-08 | Irisan izin agen                         | Pemberi perintah ∩ grant pemilik ∩ bot ∩ audiens.                                                                                 | default teknis, dipakai      |
| T-09 | Enable agen                              | "Buat ✓" di langkah Review = enable eksplisit sesudah probe lulus.                                                                | default teknis, dipakai      |
| T-10 | Tool MCP tulis                           | Tulis hanya untuk server katalog terverifikasi. Server kustom: baca saja.                                                         | default teknis, dipakai      |
| T-11 | Jeda job                                 | Jeda agen = pause profil. "Jeda job" = cancel di batas langkah, lalu resume.                                                      | default teknis, dipakai      |
| T-12 | Kode gagal                               | Kode spec lifecycle + `budget_exceeded`, `profile_paused`, `role_not_allowed`. Tanpa `runner_sleep`.                              | default teknis, dipakai      |
| T-13 | Namespace API                            | Endpoint yang ada + nama netral.                                                                                                  | default teknis, dipakai      |
| T-14 | Fathom                                   | `fathom-zulip` tetap berjalan, mengirim rapat ke API rapat.                                                                       | default teknis, dipakai      |
| T-15 | Arti kata "tugas"                        | "tugas" = kartu papan. "job" = pekerjaan agen.                                                                                    | default teknis, dipakai      |
| T-16 | Tipe ruang                               | `ChannelFolder` bernama Proyek, Klien, Tim. Ruang tanpa folder tampil di grup "Lainnya".                                          | default teknis, dipakai      |
| T-17 | Sematan ruang                            | `Subscription.pin_to_top` per orang.                                                                                              | default teknis, dipakai      |
| T-18 | Pesan ke topik saat status tugas berubah | Setting realm `task_status_notices`, default mati.                                                                                | default teknis, dipakai      |
| T-19 | Urungkan approval                        | Klien menunda kirim keputusan 5 detik.                                                                                            | default teknis, dipakai      |
| T-20 | Brief                                    | Brief = pesan ke ruang yang disebut atau DM ke Kaki.                                                                              | default teknis, dipakai      |
| T-21 | Trigger ringkasan terjadwal              | Job `answer` dengan trigger `manual` atas nama pemilik ruang.                                                                     | default teknis, dipakai      |
| T-22 | Web Push                                 | Enkripsi RFC 8291 dan VAPID dengan library yang sudah ada. Tanpa dependensi baru.                                                 | default teknis, dipakai      |
| T-23 | Adaptor VM cloud                         | **Cloudflare Containers** (ganti usulan AWS lama, akun Prazze, scope `containers`). Satu container per job coding, scale to zero. | default, menunggu konfirmasi |
| T-24 | Format kode pairing                      | 8 karakter Crockford base32, tampil `XXXX-XXXX`.                                                                                  | default teknis, dipakai      |
| T-25 | Home view                                | Hash kosong membuka `#today`. Opsi "home view" disembunyikan dari pengaturan pribadi.                                             | default teknis, dipakai      |
| T-26 | Prefix ID tugas                          | Kolom `task_id_prefix`. Kosong = `GT` (realm 2). Workspace baru: inisial nama.                                                    | default teknis, dipakai      |
| T-27 | Nama kolom papan                         | Workspace baru: Belum, Dikerjakan, Review, Selesai. Realm 2: rename lewat skrip yang bisa dibalik.                                | default teknis, dipakai      |
| T-28 | Pratinjau wizard                         | ≥ 1332px: persis mockup. 988–1331px: dua kolom. < 988px: satu kolom.                                                              | default teknis, dipakai      |
| T-29 | Pengaturan di layar sempit               | < 1024px: nav tab menjadi baris pil. < 1150px: tabel Anggota menjadi baris bertumpuk.                                             | default teknis, dipakai      |
| T-30 | Satu sumber data runner                  | `GET /json/agent/runners` untuk halaman Runner, tab Agen & runner, dan wizard.                                                    | default teknis, dipakai      |
| T-31 | Satu setting batas biaya                 | `AgentRealmSettings.monthly_budget_microunits` untuk tab Agen & runner dan tab Model.                                             | default teknis, dipakai      |
| T-32 | Modal Atur agen                          | Simpan semua field: peran, bentuk, warna, instruksi, akses, preset.                                                               | default teknis, dipakai      |
| T-33 | Gating peran di UI                       | Setiap kontrol memeriksa `permissions.can(key)`. Server tetap penentu.                                                            | default teknis, dipakai      |
| T-34 | `startView`                              | Tidak ditiru. Rute hash menentukan view.                                                                                          | default teknis, dipakai      |
| T-35 | Modal `wsSettings`                       | Tidak dibangun. "Pengaturan workspace" membuka tab Umum.                                                                          | default teknis, dipakai      |
| T-36 | Palette                                  | Kosong = 8 halaman + 1 ruang teratas, disaring izin.                                                                              | default teknis, dipakai      |
| T-37 | Tanggal selesai ruang                    | Input tanggal native (`<input type="date">`).                                                                                     | default teknis, dipakai      |
| T-38 | Status job menunggu jawaban              | Pill "MENUNGGU KEPUTUSAN" dengan gaya MENUNGGU APPROVAL.                                                                          | default teknis, dipakai      |
| T-39 | Mode Riset dulu                          | Default realm = `research_first`.                                                                                                 | default teknis, dipakai      |

### Q-01..Q-32 — Pertanyaan terbuka dari spec

| ID   | Hal.   | Default                                                                                     | Status                       |
| ---- | ------ | ------------------------------------------------------------------------------------------- | ---------------------------- |
| Q-01 | 01     | Login per workspace. `SOCIAL_AUTH_SUBDOMAIN` hanya bila login Google dibuka.                | default, menunggu konfirmasi |
| Q-02 | 01     | Ruang Proyek hanya diusulkan untuk arsip.                                                   | default, menunggu konfirmasi |
| Q-03 | 02     | Digest jam 07.00 zona waktu workspace. Ruang tanpa pesan baru dilewati.                     | default teknis, dipakai      |
| Q-04 | 02     | Digest memakai preset Cepat dan masuk budget workspace. Berhenti saat budget 100%.          | default teknis, dipakai      |
| Q-05 | 03     | Mention masuk bila DM 1:1, atau mention personal tanpa balasan pengguna selama 2 jam.       | default teknis, dipakai      |
| Q-06 | 03     | Approval kedaluwarsa = 120 menit (Q-32). Keputusan = 24 jam. Mention = 7 hari.              | default teknis, dipakai      |
| Q-07 | 04     | Topik ditautkan ke tugas lewat field tugas.                                                 | default teknis, dipakai      |
| Q-08 | 05     | Drag HTML5 yang ada + tombol maju.                                                          | default teknis, dipakai      |
| Q-09 | 05     | Sinkron Linear/Jira tidak di rilis ini.                                                     | default teknis, dipakai      |
| Q-10 | 06     | Label model: agen kerja "{Preset} · {model}", agen coding "{harness} · {model}".            | default teknis, dipakai      |
| Q-11 | 07     | Akun Google per pengguna yang menghubungkan folder. Satu workspace boleh punya banyak akun. | default, menunggu konfirmasi |
| Q-12 | 07     | Shared Drive tidak di rilis ini.                                                            | default, menunggu konfirmasi |
| Q-13 | 10     | Peran kustom ditunda.                                                                       | default teknis, dipakai      |
| Q-14 | 10     | Moderator mengundang Anggota dan Tamu (aturan Zulip).                                       | default teknis, dipakai      |
| Q-15 | 10     | Batas biaya per workspace dan per agen. Batas agen ≤ batas workspace.                       | default teknis, dipakai      |
| Q-16 | 12     | Runner orang lain hanya lewat grant eksplisit.                                              | default, menunggu konfirmasi |
| Q-17 | 12     | BYO API key di tingkat workspace.                                                           | default, menunggu konfirmasi |
| Q-18 | 13     | Edit oleh publisher tanpa label "diedit".                                                   | default teknis, dipakai      |
| Q-19 | 13     | Balasan panjang dipotong di batas pesan. Teks lengkap menjadi artefak.                      | default teknis, dipakai      |
| Q-20 | 14     | Token `sjr_` tetap sekali pakai.                                                            | default teknis, dipakai      |
| Q-21 | 15     | Email cadangan sekali sesudah 1 jam, di luar jam tenang.                                    | default teknis, dipakai      |
| Q-22 | 15     | Jam tenang per user (21.00–07.00, zona waktu profil).                                       | default teknis, dipakai      |
| Q-23 | 16     | Tanpa lingkup grup. Batasi lewat grant agen ke user group.                                  | default teknis, dipakai      |
| Q-24 | 16     | Server stdio tidak ada di rilis ini.                                                        | default teknis, dipakai      |
| Q-25 | 17     | Saldo OpenRouter habis → berhenti + notifikasi Owner. Tanpa fallback diam-diam.             | default teknis, dipakai      |
| Q-26 | 17     | Tanpa preset "Hemat".                                                                       | default, menunggu konfirmasi |
| Q-27 | 13     | Kartu job tanpa label "diedit": pakai widget pesan `agent_job`.                             | default teknis, dipakai      |
| Q-28 | 14     | Token multi-pakai tidak dibuat.                                                             | default teknis, dipakai      |
| Q-29 | 02     | Rapat hari ini = baris rapat hari ini + event Google Calendar pengguna (bila terhubung).    | default teknis, dipakai      |
| Q-30 | 09     | "Kelola" Fathom dan WhatsApp membuka modal kecil, bukan toast.                              | default teknis, dipakai      |
| Q-31 | 02     | Notulen tidak diarsip ke Drive di rilis ini. Drawer rapat menautkan notulen Fathom.         | default, menunggu konfirmasi |
| Q-32 | 03, 15 | TTL approval default 120 menit. Email cadangan terkirim sekali sesudah 60 menit.            | default, menunggu konfirmasi |

## Konflik halaman dan keputusan

Resolusi K-01..K-31 (bentrok antara halaman spec dan dokumen agen yang sudah
ada) dan I-01..I-14 (bentrok antar halaman spec). Setiap halaman `pages/*.md`
menaut ke bagian ini alih-alih ke dokumen kerja di luar repo.

### K-01..K-31

| ID   | Topik                      | Resolusi                                                                                                                                                          |
| ---- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| K-01 | Runtime agen kerja         | Pertahankan keputusan pemilik 2026-09-24 (`agent-sdk-decision.md`): loop `@anthropic-ai/sdk`. OpenRouter hanya sebagai `base_url` bila probe F0 lulus.            |
| K-02 | Tempat agen kerja berjalan | "Cloud sanji" = runner milik operator platform, dibagikan ke workspace lewat grant. Tanpa pool autoscale.                                                         |
| K-03 | Lokasi kunci model         | Kunci di server hanya untuk runner milik platform (K-02). Runner milik pengguna tetap memakai key lokal.                                                          |
| K-04 | Fallback provider          | Default mati.                                                                                                                                                     |
| K-05 | Model non-Claude           | Preset Cepat/Seimbang/Terbaik = Haiku/Sonnet/Opus.                                                                                                                |
| K-06 | Ringkasan ruang terjadwal  | Trigger `scheduled` hanya dengan opt-in pemilik ruang.                                                                                                            |
| K-07 | Isolasi tenant dan skala   | Realm bersama untuk paket Gratis/Tim/Bisnis. Instance per klien hanya Enterprise.                                                                                 |
| K-08 | Harga dan billing          | Dicatat sebagai usulan. Tidak ada build billing di MVP.                                                                                                           |
| K-09 | Gateway MCP dan token      | Gateway server untuk MCP HTTP. Stdio tetap di runner pemilik.                                                                                                     |
| K-10 | Siapa boleh memberi tugas  | Default "Hanya aku". "Semua anggota ruang" = grant eksplisit.                                                                                                     |
| K-11 | Teks gagal di ruang        | "{Agen} berhenti karena perangkat pemiliknya offline." Nama perangkat hanya di drawer untuk pemilik dan admin.                                                    |
| K-12 | OS runner                  | MVP: Linux x64 dan WSL2. Tab macOS menulis cara lain.                                                                                                             |
| K-13 | Tanda terima mention       | Reaksi 👀 + indikator mengetik. Tanpa pesan "queued".                                                                                                             |
| K-14 | Kapan pesan bot dibuat     | Kartu ANTRE dibuat saat admission. Satu pesan per job tetap berlaku.                                                                                              |
| K-15 | Coba lagi                  | `POST /agent/jobs/{id}/resume` bila state mengizinkan. Bila tidak, job baru dengan `follows_job_id`.                                                              |
| K-16 | Batas offline              | 45 detik.                                                                                                                                                         |
| K-17 | Peringatan runner offline  | 5 menit ke pemilik runner. 15 menit ke Owner/Admin.                                                                                                               |
| K-18 | Container per job          | Aturan hanya untuk jalur code.                                                                                                                                    |
| K-19 | Harness Claude Agent       | Tampil sesudah conformance probe lulus. Awal: Codex CLI dan Endpoint.                                                                                             |
| K-20 | Token pendaftaran VPS      | Pembuatan token oleh Owner/Admin/pemegang izin runner adalah momen approval itu sendiri.                                                                          |
| K-21 | Nama agen unik             | Unik per workspace, supaya mention tidak ambigu.                                                                                                                  |
| K-22 | Irisan izin agen           | Pemberi perintah ∩ grant pemilik ∩ bot ∩ audiens.                                                                                                                 |
| K-23 | Enable agen kerja          | Tombol "Buat ✓" di langkah Review = enable eksplisit sesudah probe lulus.                                                                                         |
| K-24 | Tool MCP tulis             | Hanya untuk konektor katalog terverifikasi. Server kustom: baca saja.                                                                                             |
| K-25 | Letak MCP dan Pengaturan   | **Default tim** masuk tab **Agen & runner**; **Koneksi model** masuk tab **Model & API key**. **Agent** ada di halaman Agen. **Perangkat** ada di halaman Runner. |
| K-26 | Jeda agen dan jeda job     | Jeda agen = pause profil. "Jeda job" = cancel di batas langkah, lalu resume.                                                                                      |
| K-27 | Kode gagal                 | Kode lama tanpa `runner_sleep`, tambah `budget_exceeded`.                                                                                                         |
| K-28 | Namespace API              | Endpoint yang ada, nama netral.                                                                                                                                   |
| K-29 | PWA                        | PWA masuk MVP. Aplikasi native tetap di luar.                                                                                                                     |
| K-30 | Integrasi Fathom           | Pertahankan `fathom-zulip`. Tambah pembuatan tugas lewat API tugas.                                                                                               |
| K-31 | Arti kata "tugas"          | "tugas" = kartu papan. "job" = pekerjaan agen.                                                                                                                    |

### I-01..I-14

| ID   | Halaman          | Resolusi                                                                                    |
| ---- | ---------------- | ------------------------------------------------------------------------------------------- |
| I-01 | 06, 09, 11 vs 12 | Buat agen = halaman penuh (12), bukan modal.                                                |
| I-02 | 09 vs 14         | Breakpoint 820px di semua halaman.                                                          |
| I-03 | 05 vs 09         | Keduanya: drag HTML5 di desktop, tombol maju di layar sentuh.                               |
| I-04 | 01 vs mockup     | Navigasi utama: 7 halaman untuk semua anggota (8 untuk Owner dan Admin, tambah Pengaturan). |
| I-05 | 10 vs 17         | Tambah tab "Model & API key".                                                               |
| I-06 | 09 vs 12 vs 17   | Model: Cepat/Haiku, Seimbang/Sonnet, Terbaik/Opus (12), dengan batas K-01 dan K-05.         |
| I-07 | 15 vs 01         | Manifest per host. `start_url` = `/?source=pwa`.                                            |
| I-08 | 14               | Token `sjr_…` hanya untuk VPS tanpa layar.                                                  |
| I-09 | Riset 03 vs 06   | Keduanya kalah oleh `agent-sdk-decision.md` sampai Rama memutuskan K-01.                    |
| I-10 | 10               | Moderator boleh memasangkan runner (default "✓").                                           |
| I-11 | 09 vs 13         | Ikuti 13: reaksi 300 ms, kartu ANTRE 600 ms, tanpa teks "Siap".                             |
| I-12 | 11               | Zulip membentuk email bot dari host realm. Realm domain kustom tetap host aslinya.          |
| I-13 | Riset 03         | Basi sejak `agent-sdk-decision.md` (hari yang sama).                                        |
| I-14 | 11               | Pool 299 nama (nama yang melanggar aturan panjang dihapus).                                 |
