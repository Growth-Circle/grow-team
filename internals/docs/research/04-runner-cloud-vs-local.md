# Riset 04: Runner sanji.space (Cloud Runner vs Local Runner) + web yang nyaman di laptop & HP

Status: riset, bukan keputusan.

**Tanggal:** 24 September 2026
**Pertanyaan owner:** sanji adalah aplikasi web yang harus nyaman di laptop dan HP. Runner ada 2 tipe:

- **Cloud runner:** disediakan sanji, _atau_ di VPS milik pengguna.
- **Local runner:** pengguna install CLI runner, lalu menyambungkannya ke sanji.

Apa best practice-nya?

**Konteks repo:** `grow-agent-runner` sudah punya pairing yang dikonfirmasi lewat browser, credential runner disimpan sebagai hash dan bisa dirotasi/dicabut, container rootless, lease, epoch, dan journal ([security.md](../security.md), [agent-sandbox-design.md](../agent-sandbox-design.md)).

---

## 1. Ringkasan jawaban

| Topik                     | Best practice                                                                                          | Status sanji                                                             |
| ------------------------- | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------ |
| Arah koneksi              | Runner **hanya outbound HTTPS/443**, tanpa port masuk                                                  | ✅ sesuai desain runner. Pertahankan                                     |
| Pendaftaran runner        | **Device authorization flow** (kode pendek → setujui di browser/HP) + token pendaftaran berumur pendek | ✅ ada pairing via browser. Standarkan ke pola RFC 8628                  |
| Credential setelah daftar | Credential runner bisa dirotasi dan dicabut, bukan token abadi                                         | ✅ hash + rotasi. Tambah kedaluwarsa otomatis bila runner mati > 30 hari |
| Isolasi job               | **Ephemeral:** 1 job = 1 container bersih, dihapus setelahnya                                          | ✅ container per job. Pastikan tidak ada cache credential antar job      |
| Inventaris                | Daftar runner selalu terlihat. Runner basi dibersihkan agresif                                         | ➕ perlu halaman **Runner** khusus                                       |
| Egress                    | Batasi jaringan keluar sesuai kebutuhan                                                                | ➕ allowlist per runner (Pengaturan)                                     |
| Cloud runner              | Managed pool yang autoscale + opsi **BYO VPS** yang dipasang otomatis (cloud-init/one-liner)           | ➕ desain baru di doc 14                                                 |
| Web mobile                | Sidebar jadi drawer di HP, target sentuh ≥ 44px, pairing bisa disetujui dari HP                        | ➕ tambah responsive shell                                               |

## 2. Temuan (dengan sumber)

### 2.1 Koneksi & pendaftaran

- Runner self-hosted GitHub terhubung **keluar** lewat HTTPS, tanpa aturan firewall masuk, dan didaftarkan dengan **token berumur pendek**. _('GithubActionsLabs')_
- Host runner cukup bisa membuat koneksi HTTPS keluar lewat port 443. _(GitHub Docs)_
- Risiko nyata: token pendaftaran berlaku 1 jam, tapi credential hasil pendaftaran **bertahan selamanya** di mesin. _(systemshardening.com)_ Penyerang bahkan memakai runner palsu sebagai backdoor, yaitu koneksi keluar dari mesin korban ke infrastruktur GitHub. _(Sysdig, Mar 2026)_
- Jangan membiasakan orang menyalin token ke shell atau menempel perintah dari chat. **Provision runner lewat sistem terkontrol, simpan inventaris, dan hapus runner basi.** _(DeepFrame, Jun 2026)_

➡️ **Implikasi:** jangan tampilkan token panjang untuk ditempel. Pakai **kode pendek + persetujuan di browser**, lalu tampilkan inventaris runner lengkap dengan "terakhir aktif".

### 2.2 Device authorization flow (RFC 8628), pola login CLI

- Perangkat memulai otorisasi, pengguna menyelesaikannya **di perangkat lain (HP/laptop)**, sementara perangkat melakukan polling di latar. _(Medium, Jun 2026)_ Dipakai oleh `gh auth login`, `gcloud auth login`, AWS CLI.
- Pengguna melihat **kode pendek + URL**, membukanya di HP/komputer, memasukkan kode, lalu menyetujui. Perangkat polling sampai selesai atau kode kedaluwarsa. _(oauth.net)_
- `device_code` (tidak ditampilkan) harus berentropi sangat tinggi. `user_code` pendek demi kemudahan, dengan **rate limit**. _(RFC 8628 §5)_
- Polling wajib menghormati `interval` dari server (default 5 dtk). Setiap `slow_down` menambah 5 dtk. _(knowledgelib)_

➡️ **Implikasi:** `sanji runner login` menampilkan `sanji.space/device` + kode `SNJ-4829`. Pengguna menyetujui dari laptop **atau HP**. Ini cocok dengan target web-first dan mobile.

### 2.3 Isolasi & siklus hidup

- GitHub merekomendasikan autoscaling dengan runner **ephemeral**. _(GitHub Docs)_
- Runner ephemeral: daftar → jalankan 1 job → keluar, sehingga tidak ada kontaminasi state antar job. Dipasangkan dengan runner berbasis container untuk isolasi maksimal. _('GithubActionsLabs')_
- Runner persisten mengumpulkan state (riwayat shell, credential tersimpan, sisa workspace) yang diwarisi job berikutnya. _(systemshardening.com)_
- Batasi egress (hanya 443 ke tujuan yang perlu), jangan campur runner untuk kode tak tepercaya dengan runner yang memegang credential cloud, dan jalankan non-root tanpa privilege escalation. _(systemshardening.com)_

### 2.4 Managed vs self-hosted

- Tren: layanan managed menyediakan runner ephemeral yang scalable dengan batas keamanan kuat dan start cepat, jadi pengguna tidak perlu memelihara infrastruktur. _(AWS DevOps Blog)_
- Taruh runner **dekat dengan data/registry** untuk mengurangi waktu pull image. _(matthewswong.com)_ Untuk pengguna Indonesia: region **Jakarta/Singapura**.

---

## 3. Rekomendasi model runner sanji

|                       | **Cloud: sanji-managed**                   | **Cloud: VPS milik pengguna (BYO VPS)**                                       | **Local runner**                                                            |
| --------------------- | ------------------------------------------ | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| Untuk                 | Agen kerja (default) + agen coding ringan  | Tim yang ingin data/kode di server sendiri, 24/7                              | Developer yang ingin memakai laptop/PC sendiri                              |
| Siapa yang memelihara | sanji                                      | Pengguna (sanji memasang & memperbarui runner otomatis)                       | Pengguna                                                                    |
| Cara pasang           | Otomatis saat workspace dibuat             | Salin **perintah 1 baris** / cloud-init ke VPS → runner login via device code | `brew/npm/curl` install `sanji` CLI → `sanji runner login` → setujui di web |
| Selalu online         | Ya                                         | Ya (selama VPS hidup)                                                         | Hanya saat laptop menyala, tidak sleep                                      |
| Isolasi               | microVM/container ephemeral per job        | Container rootless ephemeral per job                                          | Container rootless ephemeral per job (wajib Docker/Podman)                  |
| Credential model      | Key sanji (ditagih) atau BYO key workspace | Key di env VPS (tidak dikirim ke sanji)                                       | Key di env lokal                                                            |
| Region                | Jakarta (default), Singapura               | Bebas                                                                         | –                                                                           |
| Biaya                 | Termasuk paket / per menit                 | VPS milik pengguna                                                            | Gratis                                                                      |

**Aturan routing job:**

1. Agen **kerja**: selalu cloud sanji-managed, kecuali workspace memaksa "hanya runner sendiri".
2. Agen **coding**: runner yang dipilih saat membuat agen. Jika offline: tunggu (maks. 30 menit, bisa diatur) → _failover_ ke runner lain di **grup** yang sama bila diizinkan → kalau tidak, kartu job **BERHENTI: runner offline** + Coba lagi (doc 13).
3. Runner bisa dikelompokkan dalam **grup** (mis. "Server kantor", "Laptop tim") dengan label (`gpu`, `docker`, `repo:landing`), mengikuti pola runner groups/labels GitHub.

## 4. Mobile-first untuk web

| Area           | Rekomendasi                                                                                                                         |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Shell          | < 820px: sidebar jadi **drawer kiri** (tombol ☰ di top bar), konten satu kolom                                                     |
| Target sentuh  | Minimal 44×44px untuk tombol & item daftar                                                                                          |
| Drag tugas     | Tidak andal di iOS Safari → tombol "Mulai → / Ke review →" tetap ada. Produksi: library pointer-events                              |
| Drawer detail  | Layar penuh di HP                                                                                                                   |
| Pairing runner | **Halaman `/device` dirancang untuk HP**: satu input kode besar, tombol Setujui/Tolak, informasi perangkat (nama host, OS, IP kota) |
| Notifikasi     | Web Push (PWA) untuk approval & runner offline, supaya HP bisa approve tanpa membuka app                                            |
| PWA            | Manifest + ikon (mark S-blok) → "Tambahkan ke layar utama"                                                                          |

## 5. Risiko

| Risiko                                    | Mitigasi                                                                                                                                    |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Kode pairing ditebak                      | `user_code` 8 karakter tanpa huruf ambigu, kedaluwarsa 10 menit, rate limit per IP & per workspace                                          |
| Phishing kode ("tolong setujui kode ini") | Halaman persetujuan menampilkan **nama host, OS, lokasi IP, waktu**, dan peringatan "Setujui hanya jika kamu yang menjalankan perintah ini" |
| Runner basi / lupa                        | Tanda **basi** setelah 7 hari tidak aktif. Credential dicabut otomatis setelah 30 hari (bisa diatur)                                        |
| Laptop sleep di tengah job                | Heartbeat + lease (sudah ada). Job dilanjutkan dari checkpoint saat bangun, atau failover                                                   |
| VPS pengguna disusupi                     | Runner hanya menerima job untuk grant miliknya. Tidak ada credential sanji selain token runner. Rotasi token dari UI                        |

## 6. Sumber

- GitHub Docs: Self-hosted runners reference: https://docs.github.com/en/actions/reference/runners/self-hosted-runners
- 'GithubActionsLabs': Self-hosted runners: https://nirgeier.github.io/GithubActionsLabs/028-self-hosted-runners/
- systemshardening.com: Self-hosted runner hardening: https://www.systemshardening.com/articles/cicd/github-actions-self-hosted-runner/
- Sysdig: runner sebagai backdoor: https://www.sysdig.com/blog/how-threat-actors-are-using-self-hosted-github-actions-runners-as-backdoors
- DeepFrame: 12 checks: https://deepframe.xyz/blog/self-hosted-github-actions-runner-security-checklist
- AWS DevOps Blog: runners at scale: https://aws.amazon.com/blogs/devops/best-practices-working-with-self-hosted-github-action-runners-at-scale-on-aws/
- Medium: RFC 8628 breakdown: https://medium.com/@nishchayr/oauth-2-0-device-authorization-grant-rfc-8628-a-complete-technical-breakdown-6e859ece51dd
- oauth.net: Device flow: https://oauth.net/2/device-flow/
- RFC 8628: https://www.rfc-editor.org/info/rfc8628/
- knowledgelib: device flow reference: https://knowledgelib.io/software/patterns/oauth2-device-flow/2026
