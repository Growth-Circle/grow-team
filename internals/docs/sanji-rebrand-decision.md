# Keputusan rebrand Sanji dan domain kustom

Tanggal: 2026-09-24, Asia/Jakarta.

Status: **default tim.** Butir bertanda R menunggu konfirmasi Rama (lihat §7).
Tim memakai default di bawah sampai Rama menjawab lain (`PLAN.md` §3). Butir
bertanda T adalah default teknis; tim memakainya langsung.

Sumber: peta dokumen Sanji (rencana kerja, di luar repo ini) §9, dikoreksi
dengan default terbaru di rencana kerja §3.

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

| Fakta                                                                                                                                              | Sumber                                                              |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Realm root selalu tinggal di `EXTERNAL_HOST`. `REALM_HOSTS` tidak berlaku untuk `string_id` kosong.                                                | `zerver/models/realms.py:1266`                                      |
| Host kustom dipetakan lewat `REALM_HOSTS` sesudah cek sufiks `.{EXTERNAL_HOST}`.                                                                   | `zerver/lib/subdomains.py:30`                                       |
| **Ganti subdomain (slug) mencabut semua sesi.** Perintah memanggil `delete_realm_user_sessions(realm)`. Semua user harus login lagi.               | `zerver/actions/create_realm.py:48`                                 |
| Perintah punya opsi `--skip-redirect`.                                                                                                             | `zerver/management/commands/change_realm_subdomain.py`              |
| Zulip sudah melarang subdomain `www`, `api`, `auth`, `admin`, `status`, `help`, dan `docs`. Subdomain `app`, `sanji`, dan `device` belum dilarang. | `zerver/lib/name_restrictions.py`                                   |
| Skrip rebrand lama menolak host selain `team.growc.id`.                                                                                            | `deploy/grow-team/rebrand_pilot.py:20`, `rebrand_system_bots.py:20` |

## 4. Topologi domain target

| Host                        | Dilayani oleh                                     | Isi                                                                                            |
| --------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `sanji.space`               | Worker `sanji-landing`                            | Landing, waitlist, halaman legal, dan pengalih ke app.                                         |
| `www.sanji.space`           | Worker                                            | Redirect ke apex.                                                                              |
| `app.sanji.space`           | Zulip (alias root lewat `ROOT_SUBDOMAIN_ALIASES`) | Halaman root: cari akun (`/accounts/find/`), buat workspace (`/new/`), dan pengalih `/device`. |
| `{slug}.sanji.space`        | Zulip lewat tunnel                                | Workspace baru.                                                                                |
| `team.growc.id`             | Zulip lewat `REALM_HOSTS`                         | Workspace Growth Circle (domain kustom).                                                       |
| `auth.sanji.space`          | Zulip (`SOCIAL_AUTH_SUBDOMAIN`)                   | Callback OAuth, hanya bila login Google aktif.                                                 |
| `zulipinternal.sanji.space` | Zulip                                             | Realm bot sistem. Tidak untuk pengguna.                                                        |

## 5. Hubungan app dan landing

Landing tidak direncanakan ulang di sini. Tabel ini hanya titik sambung.

| Kebutuhan                  | Rancangan                                                                                                                                                      |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tombol "Masuk" di landing  | Form "alamat workspace" → `https://{slug}.sanji.space/login/`. Link "Lupa alamat?" → `https://app.sanji.space/accounts/find/`.                                 |
| Link root dari email Zulip | Zulip membentuk link root dengan `EXTERNAL_HOST`. Worker mengalihkan (302) `/accounts/*`, `/new/*`, dan `/device*` ke `app.sanji.space` dengan path yang sama. |
| Pairing `/device`          | CLI mencetak `https://{host workspace}/device`. Host itu juga berlaku untuk `team.growc.id`. `sanji.space/device` hanya form pengalih.                         |
| Waitlist                   | Worker menyimpan data. Tombol "Undang" di MVP = operator membuat link pembuatan realm (`manage.py generate_realm_creation_link`).                              |
| Aset brand                 | Satu sumber: brand pack v2. Landing dan app memakai token warna yang sama.                                                                                     |
| Halaman legal              | Tinggal di landing. Footer app menautkan ke `https://sanji.space/…`.                                                                                           |

## 6. Tahap

| Tahap | Isi                                                                                                                                                                                                                                                                                                                                                                                                                                    | Perubahan data                                 |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| 1     | Ganti nama produk di UI, email, bantuan, aset, dan dokumen. `EXTERNAL_HOST` tetap.                                                                                                                                                                                                                                                                                                                                                     | Tidak ada.                                     |
| 2     | Di rilis ini (WP55, lihat §7 D-03). Set `EXTERNAL_HOST=sanji.space`. Ganti `string_id` realm 2 dengan `manage.py change_realm_subdomain --skip-redirect`. **Perintah ini mencabut semua sesi user (§3); umumkan jendela kerja dulu.** Set `REALM_HOSTS = {"<slug>": "team.growc.id"}`. Tambah `app` ke `ROOT_SUBDOMAIN_ALIASES`. Tambah `app`, `sanji`, dan `device` ke daftar subdomain terlarang. Arahkan `*.sanji.space` ke tunnel. | `Realm.string_id` realm 2. Semua sesi dicabut. |
| 3     | Opsional. Pindah domain bot sistem ke `sanji.space` dengan pola audit/apply/reverse dari `rebrand_system_bots.py`.                                                                                                                                                                                                                                                                                                                     | Email bot sistem.                              |

## 7. Keputusan terbuka untuk dokumen ini

Kolom "Default" memakai nilai terbaru dari `PLAN.md` §3 (menggantikan usulan awal
di peta dokumen bila keduanya beda). Kolom "Status" mengikuti aturan yang sama:
tim memakai default ini sekarang; butir R tetap menunggu konfirmasi Rama.

| ID   | Keputusan                                  | Default (`PLAN.md` §3)                                                                                                                           | Status                 |
| ---- | ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------- |
| D-01 | Slug baru realm 2                          | `growc` (P-20). **Mengganti slug mencabut semua sesi realm 2** (§3, §6 Tahap 2).                                                                 | R, menunggu konfirmasi |
| D-02 | Nama workspace realm 2 ("Grow Team")       | Tetap "Grow Team". Owner dapat mengganti di Pengaturan (P-22).                                                                                   | R, menunggu konfirmasi |
| D-03 | Waktu mulai Tahap 2                        | Di rilis ini, WP55 (P-21). Verifikasi rilis wajib memeriksa URL `sanji.space`.                                                                   | R, menunggu konfirmasi |
| D-04 | Domain bot sistem                          | Tetap `team.growc.id` di Tahap 2.                                                                                                                | T, dipakai langsung    |
| D-05 | Domain pengirim email workspace baru       | `noreply@growc.id`. `INSTALLATION_NAME` = `sanji` (P-23). Domain kirim `sanji.space` belum disiapkan.                                            | R, menunggu konfirmasi |
| D-06 | Prefix ID kartu `GT-`                      | Kolom `task_id_prefix`. Realm 2 tetap `GT`. Workspace baru: inisial nama (T-26).                                                                 | T, dipakai langsung    |
| D-07 | Nama runner "Grow Team runner"             | Pemilik mengganti lewat UI. Bukan migrasi.                                                                                                       | T, dipakai langsung    |
| D-08 | Agen bawaan Kaki, Ayame, Matcha di realm 2 | Buat di runner dan provider yang sudah ada (P-18). `default_profile` = Kaki. Profil "Opus" tetap.                                                | R, menunggu konfirmasi |
| D-09 | Repo publik: riset 05 dan HTML desain      | Jangan commit keduanya (P-24). **Sudah diterapkan**: WP11 tidak commit `research/05-saran-harga.md`, berkas HTML, `support.js`, atau `uploads/`. | R, menunggu konfirmasi |
| D-10 | Cek merek "sanji" di PDKI/DJKI             | Rama memeriksa sebelum landing publik (P-33).                                                                                                    | R, tindakan Rama       |

## 8. Yang tidak berubah

- URL `team.growc.id`, termasuk link di pesan lama.
- Origin runner (`grow-agent` di `server-gteam` memakai `team.growc.id`). Runner tidak perlu pairing ulang.
- Email bot di realm 2 (`@team.growc.id`), karena host realm tetap.
- Target `fathom-zulip` (`team.growc.id`).
- Berkas unggahan. Path memakai ID realm.
- Semua identifier internal (`grow-team`, `grow-agent`, dll., lihat aturan rename).

## 9. Risiko

| Risiko                                                                                                                                   | Pengendalian                                                                                |
| ---------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| Tahap 2 mencabut semua sesi dan event queue.                                                                                             | Pakai jendela kerja. Umumkan ke 5 anggota. Uji dulu pada restore backup di `rama-tuf`.      |
| Tunnel dan zona `sanji.space` bisa beda akun Cloudflare. README `fathom-zulip` mencatat `growc.id` dan `ramadigital.id` di akun berbeda. | Cek akun pemilik tunnel. Bila beda, buat tunnel kedua di akun "Prazze@gmail.com's Account". |
| Dukungan `SETTING_REALM_HOSTS` (nilai dict) di image docker-zulip belum pasti.                                                           | Uji di harness sebelum Tahap 2.                                                             |
| Domain pengirim email untuk workspace baru belum ada.                                                                                    | Tetap `noreply@growc.id` sampai domain kirim `sanji.space` siap (D-05).                     |
| Rute model `cc/claude-opus-5` dan `cx/gpt-5.6-luna` lewat 9router. Login langganan tidak boleh dipakai produk pihak ketiga.              | Cek apakah rute ini memakai login langganan. Wajib API key sebelum workspace klien aktif.   |
| Sesi tidak berlaku lintas workspace (cookie per host).                                                                                   | Dropdown workspace mengikuti default login per workspace (`PLAN.md` Q-01).                  |

## 10. Rollback

1. Pulihkan `compose.override.yaml` dari backup (`deploy-server.sh` membuat salinan `.bak-*`).
2. Kembalikan `string_id` realm 2 ke kosong dengan `change_realm_subdomain`.
3. Kembalikan image menurut prosedur rilis bila perlu.
4. Cek `GET /login/` = 200 di `team.growc.id`.

## 11. Verifikasi

1. `GET https://team.growc.id/login/` = 200.
2. Workspace uji di `{slug}.sanji.space` bisa login.
3. Runner `online` dan profil "Opus" siap.
4. Mention ke "Opus" membuat job dan jawaban.
5. Unggah lalu unduh berkas memberi isi yang sama.
6. Email reset password terkirim.
7. Jumlah pesan (≥ 243) dan lampiran (≥ 4) di realm 2 tidak turun.
8. Email bot di realm 2 tidak berubah.
