# Spesifikasi Alur Agent: Penambahan, Mention, Eksekusi, dan Pemulihan

Tanggal: 2026-09-21.

Status: **sebagian sudah diimplementasikan; image `12.2-grow-team.22` (`83b1a57b9e4`) memuat admission mention, job, runner, dialog tugas, dan drawer tugas, tetapi [baris AF](../agent-acceptance.md) belum lulus.**

> **Status 2026-09-24:** job `answer` dan `manage` sekarang memakai jalur cepat.
> Lihat [spesifikasi jalur cepat](2026-09-24-agent-fast-lane.md),
> [spesifikasi streaming](2026-09-24-agent-streaming-delivery.md), dan
> [keputusan SDK](../agent-sdk-decision.md). Bila ada konflik, spesifikasi jalur cepat berlaku.
>
> Bagian yang digantikan untuk jalur cepat:
>
> - §1, §3, §8, §8.1: runtime ACP dan endpoint hanya untuk jalur code. Jalur cepat memakai
>   loop Anthropic SDK di proses runner (jalur cepat bagian 6).
> - §4.1, §13: tambah field `lane`; `job_kind` juga memuat `manage`.
> - §5.1, §5.2: "agent terpasang atau endpoint" menjadi koneksi model `anthropic_messages`.
> - §7.2: respons claim membawa paket konteks (jalur cepat bagian 5.3).
> - §7.4: trigger `manage` mengikuti [spesifikasi administrator](2026-09-23-agent-administrator.md).
> - §10: kapasitas per jalur (fast 4, code 1); runner dibangunkan long-poll `/runner/wake`.
> - §12.1, §12.2: batal jalur cepat = abort stream, lalu `attempt.stopped`.
> - §13.4: event baru `result.draft`.
> - §14, §16, §17.1, §18, §19: baris adapter, fixture ACP, dan urutan implementasi hanya
>   untuk jalur code. Urutan jalur cepat mengikuti fase F0–F5.
> - §17.2: AF-25, AF-28, AF-30, AF-37, AF-38 punya versi baru di
>   [matriks penerimaan](../agent-acceptance.md).
> - §20: target latensi ada di [spesifikasi latensi](2026-09-24-agent-latency-and-reliability.md).

Baseline Grow Team: `fe0da4b53b09e95abaa2a567952412a56d21a6ed`.
Baseline riset Buzz: `5079c770fe30bb3d8204822ce6c2431eacac6d4b`.

## 1. Tujuan dan hubungan dengan spesifikasi sebelumnya

Dokumen ini menjelaskan cara pengguna menambah agent, memanggilnya dari chat,
mengikuti pekerjaannya, dan memulihkan tugas yang terhenti. Tujuannya adalah
memindahkan pelajaran dari source, catatan desain, dan tes Buzz ke kontrak Grow
Team yang dapat langsung dipecah menjadi pekerjaan implementasi.

Fondasi tetap mengikuti [spesifikasi koneksi dan coding harness](2026-09-21-agent-connections-and-coding-harness.md):

- Grow Team memakai Zulip sebagai aplikasi web.
- Pengguna berinteraksi melalui browser tanpa aplikasi desktop.
- Runner berjalan pada laptop/server milik pengguna sebagai layanan latar.
- Job `answer` dan `manage` berjalan di jalur cepat: loop `@anthropic-ai/sdk`
  di dalam proses runner, tanpa container [jalur cepat]. Job `code` berjalan
  di jalur code: agent terpasang melalui ACP dan model dari endpoint
  OpenAI-compatible [jalur code].
- Perubahan kode berlangsung pada workspace terpisah, dengan izin dan bukti pemeriksaan.

Spesifikasi sebelumnya menjadi acuan untuk sandbox, credential, lease, budget,
artifact, dan rollback. Dokumen ini menambahkan kontrak alur, keputusan routing,
status UI, serta kasus regresi. Angka batas resource tidak diubah di sini, kecuali
batas antrean yang dinyatakan sebagai tambahan pada bagian 10.

Arahan produk berasal dari pengguna. Detail default pada dokumen ini adalah
rekomendasi implementasi. Pekerjaan ini membaca source dan tes; tidak menjalankan
suite Buzz, provider nyata, atau coding agent terhadap repository pengguna.

## 2. Cara memakai hasil riset Buzz

Gunakan tiga jenis bukti secara berbeda:

| Jenis bukti                | Makna                                                     | Cara memakai                                                                             |
| -------------------------- | --------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Implementasi source        | Jalur dan perilaku ditemukan pada commit yang dipatok.    | Jadikan acuan pemisahan komponen dan urutan operasi.                                     |
| Kasus tes                  | Upstream menulis pemeriksaan untuk suatu perilaku.        | Port skenario ke test harness Grow Team; jangan menganggap tes sudah dijalankan di sini. |
| Draft atau catatan insiden | Usulan, pengalaman, atau batas yang dicatat penulis Buzz. | Ambil pelajarannya; jangan menyebutnya fitur yang sudah terbukti bekerja.                |

Graf parsial dari penelitian sebelumnya dipakai untuk menemukan simbol queue dan
scope. Jalur yang menjadi dasar keputusan diperiksa lagi pada source commit di
atas. Graf tersebut bukan inventaris lengkap source terbaru.

### 2.1 Temuan yang langsung memengaruhi alur

| ID  | Bukti Buzz                                                                  | Temuan                                                                                                              | Keputusan Grow Team                                                                                                   |
| --- | --------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| B01 | [Pembuatan agent][buzz-create], [readiness][buzz-readiness]                 | Validasi, simpan identitas, start, dan sinkronisasi profil memiliki hasil yang berbeda.                             | Simpan profil sekali; tampilkan kegagalan setup sebagai status yang bisa diperbaiki.                                  |
| B02 | [Attachment setelah create][buzz-attach]                                    | Agent yang berhasil dibuat dapat gagal ditambahkan ke kanal.                                                        | Retry akses kanal memakai profil yang sama; jangan membuat agent duplikat.                                            |
| B03 | [Identitas profil][buzz-identity]                                           | Identitas yang dipilih tidak boleh dialihkan ke instance lain hanya karena persona sama.                            | Bot user ID dan profile ID tetap otoritatif meskipun nama berubah.                                                    |
| B04 | [Routing mention][buzz-mention-doc], [validasi sebelum publish][buzz-send]  | Pilihan penerima ditangkap sebelum operasi asinkron dan diperiksa lagi sebelum pengiriman.                          | Pertahankan ID, tujuan, dan revisi draft; validasi server tetap wajib.                                                |
| B05 | [Start setelah publish][buzz-wake], [replay floor][buzz-replay]             | Start tidak menahan pengiriman; replay mencakup pesan yang lebih dahulu terbit.                                     | Simpan pesan, job, dan outbox sebelum wake; runner mengambil pekerjaan dari database.                                 |
| B06 | [Filter event][buzz-filter], [admission loop][buzz-ingress]                 | Author gate dan subscription filter mendahului antrean; mention memakai identitas terstruktur.                      | Gunakan parser Zulip dan grant, bukan pencarian teks nama agent.                                                      |
| B07 | [SessionScope][buzz-scope]                                                  | Scope ditentukan sekali saat admission. Channel adalah default; thread merupakan pilihan eksplisit.                 | Turunkan `AgentConversation` stabil sekali; jangan memakai judul topik sebagai identitas sesi.                        |
| B08 | [EventQueue][buzz-queue]                                                    | Antrean dipartisi menurut scope; kapasitas dan pekerjaan aktif dibatasi.                                            | Satu eksekutor per attempt; antrean tahan restart dan penolakan kapasitas yang terlihat.                              |
| B09 | [ACP client][buzz-acp], [agent loop][buzz-agent]                            | Harness mengendalikan sesi; runtime memanggil model dan tools.                                                      | Grow Runner menjadi ACP client; provider model tidak merangkap pengelola job. Usang untuk jalur cepat 2026-09-24: loop Anthropic SDK menggantikan ACP client untuk `answer` dan `manage` [jalur cepat]. Baris ini tetap berlaku untuk jalur code [jalur code]. |
| B10 | [Context handoff][buzz-handoff], [regresi agent][buzz-regressions]          | Ada pemulihan konteks dan pemeriksaan pasangan tool/result saat cancel.                                             | Pertahankan input aktif, referensi, dan checkpoint; batasi recovery.                                                  |
| B11 | [Availability][buzz-availability], [management provenance][buzz-provenance] | Presence, catatan proses, kepemilikan, dan hak lifecycle merupakan fakta berbeda.                                   | Pisahkan runner online, runtime ready, profil enabled, dan status tugas.                                              |
| B12 | [Catatan kickoff][buzz-kickoff]                                             | Balasan pengakuan antaragent pernah membentuk loop; agent gagal start tidak dapat menjelaskan kegagalannya sendiri. | Pesan bot tidak memicu coding otomatis; aplikasi menampilkan error dari supervisor.                                   |
| B13 | [Draft information-flow][buzz-infoflow]                                     | Penulis mengusulkan broker dan pembatasan audiens; dokumen menyebut batas terhadap shell serta jaringan.            | Terapkan context/result broker bersama sandbox dari spec awal; jangan mengklaim proposal itu sudah terpasang di Buzz. |
| B14 | [Draft remote agents][buzz-remote]                                          | Lokasi eksekusi terpisah dari identitas agent dan provider model. Dokumen juga mencatat keputusan terbuka.          | Bedakan **Runner** dari **Koneksi model**; provider Kubernetes Buzz bukan syarat MVP.                                 |

### 2.2 Bagian yang tidak disalin sebagai default

- ACP client Buzz yang diperiksa memilih opsi `allow_once` bila tersedia. Grow Team harus memakai policy dan approval sendiri. [Implementasi permission][buzz-permission].
- Queue Buzz dapat membuang event lama ketika kapasitas tercapai. Grow Team tidak boleh menghilangkan tugas yang sudah diakui diterima. [Queue][buzz-queue].
- Author gate Buzz dapat menerima bot lain dari pemilik yang sama. Grow Team MVP menolak trigger otomatis dari semua bot. [Admission loop][buzz-ingress].
- Balasan melalui CLI dan key relay Buzz tidak dibawa ke proses model Grow Team. Publikasi melewati broker Grow Team.
- Konfigurasi environment desktop, format identitas Nostr, dan local storage Buzz bukan pengganti auth/realm/database Zulip.
- React desktop tidak ditempelkan ke frontend Grow Team. Pola interaksi diterapkan pada TypeScript/Handlebars existing.

README dan komentar dapat tertinggal dari implementasi. Contohnya, komentar
admission menyebut scope sebagai telemetry, sedangkan `EventQueue` sudah memakai
scope sebagai key. Implementor harus memeriksa pemakai simbol, bukan menyalin
komentar sebagai kontrak. Nilai kapasitas sesi juga harus diambil dari versi
runtime yang diuji, bukan diagram vision.

## 3. Keputusan reuse dan batas komponen

| Pilihan                                                          | Dampak                                                               | Keputusan                                                      |
| ---------------------------------------------------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------- |
| Menjalankan seluruh `buzz-acp` dan relay Buzz di belakang Zulip  | Menambah identitas, routing, serta sinkronisasi dua sistem chat.     | Tidak dipilih untuk MVP.                                       |
| Grow Runner menjadi ACP client dan memakai runtime yang tersedia | Memakai loop agent existing; Django tetap mengatur tugas dan izin.   | Dipakai untuk `code` [jalur code].                              |
| Menulis loop model/tools baru                                    | Memberi kontrol penuh, tetapi harus mengulang banyak kasus recovery. | Dipilih untuk `answer` dan `manage` sejak 2026-09-24: loop `@anthropic-ai/sdk` di proses runner [jalur cepat]. Lihat [keputusan SDK agent](../agent-sdk-decision.md). |

Untuk mode endpoint pada jalur code, prioritaskan probe `buzz-agent` sebagai
subprocess ACP [jalur code]. Jika mode API, tool broker, isolasi secret, atau
kontrol lifecycle yang dibutuhkan tidak dapat dipenuhi, gunakan loop minimal
yang melewati suite konformansi yang sama. Jangan memelihara dua runtime
endpoint secara paralel sebelum ada kebutuhan. Loop Anthropic SDK jalur cepat
memenuhi aturan ini: ia melewati suite konformansi yang sama sebelum aktif
(gerbang F0, [jalur cepat](2026-09-24-agent-fast-lane.md) bagian 6.2).

Runtime provider pada Buzz dapat berarti penyedia lokasi eksekusi. Pada Grow
Team, **Koneksi model** selalu berarti endpoint inferensi; **Runner** selalu
berarti perangkat yang mengeksekusi pekerjaan. Label UI harus mengikuti arti ini.

## 4. Identitas, hak, dan status yang terlihat

### 4.1 Entitas yang tidak boleh disamakan

| Entitas        | Identitas tetap                 | Contoh                             | Bukan bukti untuk                             |
| -------------- | ------------------------------- | ---------------------------------- | --------------------------------------------- |
| Profil agent   | `agent_profile_id`              | Reviewer Grow                      | Runtime sedang berjalan.                      |
| Identitas chat | `bot_user_id` dalam realm       | Akun yang muncul pada mention      | Peminta boleh memakai runner pemilik bot.     |
| Runner         | `runner_id`                     | Server pengembangan Rama           | Provider model dapat dipakai.                 |
| Runtime        | `adapter_id` dan versi          | Agent ACP terpasang                | Semua tools aman atau diizinkan. [jalur code]; jalur cepat tidak memakai `adapter_id` (bagian 3). |
| Jalur (lane)   | Nilai `fast` atau `code` pada `AgentAttempt` | Job `answer` memakai `fast`        | Runner memilih jalur; server menetapkannya saat claim ([jalur cepat](2026-09-24-agent-fast-lane.md) bagian 3). |
| Provider model | `provider_id`, `config_version` | Endpoint kompatibel milik pengguna | Agent dapat mengedit repository tanpa runner. |
| Job            | `job_id`                        | Perbaiki formulir login            | ACP session ID atau judul topik. [jalur code]     |
| Attempt        | `attempt_id`, `lease_epoch`     | Usaha kedua setelah putus koneksi  | Job baru atau izin mengulang efek eksternal.  |

V1 memakai satu profil untuk satu bot user dan satu runner yang dipilih.
Pemindahan runner dilakukan melalui perubahan konfigurasi eksplisit, bukan
pengalihan otomatis ketika perangkat offline. Marketplace persona dan banyak
instance untuk satu profil ditunda.

### 4.2 Hak minimum

Pemilik runner menyetujui repository dan siapa yang boleh memberi tugas. Pemilik
profil dapat mengubah konfigurasi dalam hak tersebut. Admin kanal mengatur akses
bot ke kanal sesuai aturan Zulip. Satu peran tidak menggantikan peran lain.

Peminta harus memiliki akses profil, repository, konteks, dan provider yang
dipakai. Peninjau hanya dapat melihat hasil yang diizinkan untuknya. Operator
dengan grant kontrol dapat membatalkan job; hak itu tidak otomatis memberi akses
ke secret, raw transcript, atau repository lain.

### 4.3 Status terdiri dari beberapa sumbu

| Sumbu               | Nilai usulan                                              | Dasar                                                       |
| ------------------- | --------------------------------------------------------- | ----------------------------------------------------------- |
| Profil              | `draft`, `enabled`, `paused`, `archived`                  | Keinginan pemilik yang tersimpan.                           |
| Runner              | `online`, `offline`, `unknown`, `revoked`                 | Heartbeat sah, umur heartbeat, hasil query, dan revocation. |
| Konfigurasi runtime | `unchecked`, `checking`, `ready`, `needs_action`, `error` | Probe terhadap revision yang tepat.                         |
| Kemampuan           | `chat_ready`, `code_ready`, kemampuan opsional            | Hasil handshake/probe/sandbox.                              |
| Proses attempt      | `starting`, `active`, `stopping`, `stopped`, `unknown`    | Event supervisor dan lease untuk jalur code. Jalur cepat memakai loop dalam proses runner yang ada; bagian 6.6 spesifikasi jalur cepat mengatur isolasi antar job, bukan lifecycle proses baru per attempt [jalur cepat]. |
| Job                 | State machine pada spec awal                              | Record job dan transisi server.                             |

Query status yang gagal menghasilkan `unknown`, bukan `offline`. PID yang masih
tercatat tidak membuat kemampuan menjadi ready. Untuk jalur code, profil enabled
boleh idle tanpa proses model: runner melakukan cold start ketika ada job yang
sah [jalur code]. Untuk jalur cepat, runner mengklaim job lewat event
`agent_job_ready` tanpa cold start proses; loop model berjalan langsung di
proses runner yang sudah aktif [jalur cepat].

## 5. F01 — Menambah agent

### 5.1 Alur browser

1. Pengguna membuka **Pengaturan → Agent → Tambah agent**.
2. Pengguna mengisi nama dan deskripsi peran singkat.
3. Pengguna memilih runner yang dimiliki atau dibagikan kepadanya.
4. Jika belum ada runner, UI membuka pairing dari spec awal dan mempertahankan draft.
5. Mode **Diskusi** memilih satu **koneksi model** (jalur cepat, bagian 6.2
   [spesifikasi jalur cepat](2026-09-24-agent-fast-lane.md)) [jalur cepat]. Mode
   **Coding** memilih **Agent terpasang** atau **Endpoint model** [jalur code].
6. Mode terpasang menampilkan katalog adapter yang dilaporkan runner beserta status login.
7. Mode endpoint memilih koneksi model existing atau membuka form koneksi baru.
8. Pengguna memilih repository, kemampuan, dan siapa yang boleh memberi tugas.
9. Pengguna meninjau ringkasan scope, lalu menyimpan profil sebagai draft.
10. Runner memeriksa kesiapan; UI menampilkan hasilnya sebelum pengguna mengaktifkan revision yang lulus.

Mode awal profil baru adalah **Diskusi**. Preset **Coding** mengharuskan repository,
profil pemeriksaan, dan izin baca/edit/test yang eksplisit. Mengubah mode default
tidak mengubah tugas yang sudah diklaim. Profil coding yang dikonfigurasi lengkap
dapat langsung bekerja saat mention, tanpa meminta izin baca/edit/test berulang.

Nama boleh berubah dan boleh sama dengan nama profil lain. Picker membedakannya
dengan pemilik dan runner; ID tetap menjadi penerima sebenarnya. Perubahan nama
tidak mengubah referensi pada pesan lama.

### 5.2 Kontrak server dan runner

```mermaid
sequenceDiagram
    actor U as Pengguna
    participant W as Browser
    participant C as Grow Team
    participant R as Grow Runner
    participant A as Adapter, runtime endpoint, atau koneksi model
    U->>W: Simpan profil dengan scope
    W->>C: Create profile + idempotency key
    C->>C: Validasi hak, bot identity, revision, setup record
    C-->>W: Profil tersimpan + setup operation ID
    R->>C: Claim pemeriksaan konfigurasi
    C-->>R: Descriptor terikat revision + grant probe
    R->>A: Probe jalur code (ACP/tool round-trip) atau jalur cepat (gerbang F0, POST /v1/messages)
    A-->>R: Kemampuan atau requirement yang kurang
    R->>C: Laporan probe terstruktur
    C-->>W: Ready atau needs_action dengan tindakan perbaikan
    U->>W: Aktifkan profil
    W->>C: Enable revision yang diuji
    C-->>W: Profil aktif; menunggu tugas
```

Simpan bot user, profil, dan setup record dalam transaksi lokal yang konsisten.
Panggilan jaringan dan spawn terjadi setelah commit. Pelaporan setup tidak boleh
mengubah revision yang lebih baru. Ulangi submit dengan idempotency key yang sama
untuk menemukan profil yang sama; payload berbeda dengan key sama ditolak.

Readiness memeriksa **descriptor efektif yang sama dengan descriptor spawn**:
adapter/version, provider/model, secret reference, workspace binding, sandbox,
policy, serta versi konfigurasi. Jangan memeriksa satu gabungan konfigurasi lalu
menjalankan gabungan lain. Kunci yang mengatur identitas, control plane, atau
grant tidak dapat diganti lewat environment bebas.

Perintah executable berasal dari katalog lokal yang diizinkan pemilik runner.
Browser tidak dapat menyisipkan shell command arbitrer untuk menginstal atau
menjalankan program host. Login vendor dilakukan pada perangkat runner sesuai
kemampuan adapter; UI menunjukkan instruksi yang relevan tanpa meminta salinan
credential login ke chat.

### 5.3 Hasil sebagian dan retry

| Keadaan                                   | Hasil tersimpan                                  | UI dan retry                                                  |
| ----------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------- |
| Simpan profil gagal                       | Tidak ada profil parsial yang diumumkan berhasil | Draft tetap ada; perbaiki field atau ulangi request.          |
| Profil tersimpan, runner offline          | Profil draft dan setup pending                   | “Profil tersimpan. Runner belum terhubung.”                   |
| Adapter tidak ditemukan [jalur code], atau koneksi model gagal probe F0 [jalur cepat] | Profil tetap ada, `needs_action`                 | Petunjuk pemasangan lokal; probe ulang pada profil yang sama. |
| Login/provider invalid                    | Profil tetap ada, requirement spesifik           | Perbaiki koneksi; jangan mengirim ulang tugas pengguna.       |
| Probe selesai setelah konfigurasi berubah | Laporan revision lama menjadi stale              | Jalankan probe revision baru sebelum enable.                  |
| Profil ready, akses kanal gagal           | Profil tetap ready                               | Ulangi penambahan akses kanal, bukan create agent.            |

Menyimpan profil atau mengaktifkan agent tidak menghasilkan ucapan perkenalan
dari model. Tombol **Kirim tugas uji** memakai prompt sintetis yang terlihat dan
menjelaskan penggunaan model. Tes tersebut memiliki job ID sendiri.

## 6. F02 — Memberi akses ke kanal atau percakapan

Tambahkan aksi **Tambahkan agent** pada pengaturan kanal dan pemilih agent.
Tampilkan profil yang boleh dipakai pengguna. Aksi ini memeriksa dua hal:
keanggotaan bot pada kanal dan grant profil untuk menerima tugas di kanal itu.

UI menyebut kanal, mode default, repository, serta kelompok peminta yang akan
mendapat akses. Pilihan “semua anggota kanal” harus merupakan grant eksplisit
pemilik runner/profil, bukan akibat otomatis dari status kanal publik.

Jika pengguna tidak memiliki salah satu hak, tampilkan tindakan meminta akses
tanpa menyatakan agent sudah ditambahkan. Tidak ada perluasan izin diam-diam saat
mention. Untuk private channel, bot harus mendapat keanggotaan yang sah terlebih
dahulu. Bot masuk kanal juga tidak otomatis membaca seluruh riwayat.

Penambahan membership dan grant memakai transaksi bila keduanya berupa state
lokal Zulip. Jika tindakan tambahan memiliki efek terpisah, simpan status tahap
dan retry hanya tahap yang gagal. Jangan mencabut membership yang sudah sah hanya
karena pengguna membatalkan draft pesan berikutnya.

Aturan DM:

- DM satu manusia dengan satu bot agent boleh menjadi trigger tanpa mention.
- Group DM membutuhkan mention agent yang eksplisit atau aksi tugas terstruktur.
- Penambahan anggota DM mengubah audiens; context dan sesi lama tidak diwariskan otomatis.
- DM dengan beberapa bot tidak membuat semua bot bekerja hanya karena menjadi penerima.

## 7. F03 — Mention menjadi tugas

### 7.1 Identitas mention dan integrasi parser Zulip

Picker menyimpan bot user ID dan profile ID. Gunakan sintaks mention Zulip yang
mengandung identitas, melalui helper existing; nama tampilan tidak dipakai sebagai
key dispatch. Nama yang diketik sebagai teks biasa hanya menjadi mention bila
parser server mengakuinya sebagai mention sah.

Pemeriksaan source Grow Team menemukan batas penting:

1. [MentionData](../../../zerver/lib/mention.py) mengumpulkan kandidat mention; kandidat belum membuktikan mention benar-benar dirender.
2. [build_message_send_dict](../../../zerver/actions/message_send.py) memakai `render_incoming_message` untuk menentukan mention aktual.
3. Pada sekitar baris 727, anggota grup ditambahkan ke `rendering_result.mentions_user_ids`.
4. [get_service_bot_events](../../../zerver/actions/message_send.py) menolak sender bot dan memfilter kandidat yang tidak menjadi mention aktual.

Untuk trigger agent, tangkap salinan mention personal aktual **sebelum ekspansi
anggota grup**, lalu tandai asal mention: personal, grup, wildcard, silent,
kutipan, atau kode. Jangan memakai kumpulan ID yang sudah diperluas sebagai
perintah kepada semua agent dalam grup. Kandidat hasil regex bukan input final.

Jika hasil renderer belum menyediakan provenance yang dibutuhkan, tambahkan
metadata pada jalur render yang otoritatif. Kasus personal versus grup, silent,
inline code, fenced code, dan blockquote wajib menjadi fixture sebelum trigger
dihubungkan. Jangan membuat regex parser kedua di worker.

Profil baru memakai bot Zulip `DEFAULT_BOT` dengan relasi `AgentProfile`.
Dispatcher khusus mengambil trigger dari metadata render dan relasi profil.
Jangan memasukkan bot tersebut ke cabang service bot yang hanya menerima outgoing
webhook atau embedded bot. Implementasi harus menjaga jalur bot lama tetap sama.

### 7.2 Urutan pengiriman

```mermaid
sequenceDiagram
    actor U as Pengguna
    participant W as Browser
    participant C as Django
    participant D as Database
    participant R as Runner
    U->>W: Kirim pesan dengan mention
    W->>W: Tangkap tujuan, recipient IDs, dan revisi draft
    W->>C: Preflight konteks dan grant
    C-->>W: Keputusan awal tanpa spawn
    W->>C: Kirim pesan melalui jalur chat existing
    C->>C: Render mention dan periksa admission terbaru
    C->>D: Transaksi pesan + dispatch receipt + job/outbox bila sah
    D-->>C: Commit
    C-->>W: Pesan tersimpan; receipt dapat dimuat
    W->>W: Bersihkan hanya draft yang benar-benar dikirim
    R->>C: Claim pekerjaan
    C->>D: Lease atomik dan recheck izin
    C-->>R: Attempt terikat scope dan revision, plus paket konteks untuk lane fast
    R->>C: Event starting, started, dan progres
    C-->>W: Panel status dari record tersimpan
```

Preflight adalah bantuan UX, bukan otorisasi final. Jika izin sudah ditolak saat
preflight, pertahankan draft dan tampilkan sebab yang boleh diketahui peminta.
Runner offline atau sibuk tidak menggagalkan chat; tugas dapat masuk antrean.

Jika izin berubah setelah preflight, server tetap memeriksa admission. Pesan chat
yang sah boleh tersimpan dengan receipt `rejected`; **tidak ada job atau spawn**.
UI membedakan “pesan terkirim” dari “tugas diterima”. Receipt penolakan hanya
dapat dibaca peminta/operator yang sah dan tidak mengungkap detail profil privat.

Klien Zulip lain yang belum mempunyai preflight tetap melewati pemeriksaan
server yang sama. Panel Grow Team dapat menemukan receipt melalui message ID.
Tidak adanya frontend Grow Team tidak boleh melewati grant atau menyebabkan
worker mengira semua mention sah untuk coding.

### 7.3 Snapshot dan pemulihan draft

Snapshot sebelum operasi asinkron berisi realm, tujuan kanal/DM, topik, profile
IDs, draft key, visit token, revisi yang ditulis pengguna, teks, dan attachment
references. Validasi dan upload selalu menggunakan snapshot tersebut.

Pindah topik A → B → A tidak menghidupkan kembali intent kirim lama. Respons
yang terlambat tidak dapat mengosongkan atau menimpa draft baru, termasuk draft
yang sengaja dikosongkan pengguna. Batalkan kelanjutan sebelum publish ketika
visit/revision tidak lagi sesuai; pertahankan recovery pada draft asal yang masih
berwenang. Jangan menampilkan error kanal privat A ketika pengguna sedang di B.

Jika request pengiriman sudah diterima server, menutup composer tidak membatalkan
job. Gunakan **Batalkan tugas** untuk cancel. Jika respons pengiriman hilang,
rekonsiliasi status pesan/receipt sebelum mengirim ulang. Key retry untuk
pengiriman agent harus tahan pengulangan request; `sender_queue_id` dan `local_id`
Zulip tidak diasumsikan sebagai jaminan idempotensi penyimpanan.

### 7.4 Matriks keputusan trigger

| Input                                                                 | Hasil                                                                                             |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Mention personal agent enabled, grant sah, konfigurasi lengkap        | Buat satu job dan satu receipt accepted.                                                          |
| Agent diulang beberapa kali dalam satu pesan                          | Satu target, bukan beberapa job.                                                                  |
| Mention profil Diskusi, pertanyaan biasa                              | Job `answer`, jalur cepat; tools mutasi tidak disediakan [jalur cepat].                           |
| Mention profil Diskusi, perintah alat tim (11 alat administrator)     | Job `manage`, jalur cepat; alat baca dan alat tim sesuai grant [jalur cepat].                     |
| Mention profil Coding dengan repository dan mandat lengkap            | Job `code`, jalur code; target default patch [jalur code].                                        |
| Mention Coding tetapi repository atau kriteria wajib belum ditentukan | Job draft; peminta mendapat form pelengkap tanpa panggilan model.                                 |
| Runner offline                                                        | Job queued dengan alasan `runner_offline`; tidak ada klaim sedang bekerja.                        |
| Runner sibuk                                                          | Job queued; tampilkan antrean tanpa janji waktu selesai palsu.                                    |
| Profil enabled tetapi auth/runtime kemudian memerlukan perbaikan      | Job blocked dengan requirement yang terlihat; tidak mencoba tool atau model berulang tanpa batas. |
| Profil paused/archived atau akses ditolak                             | Receipt rejected; tidak ada job eksekusi.                                                         |
| Mention silent, teks kutipan, inline/fenced code                      | Referensi saja; tidak membuat job.                                                                |
| Mention grup, `@all`, wildcard kanal/topik                            | Tidak memanggil agent secara implisit.                                                            |
| Pesan bot, termasuk agent lain dan bot sistem                         | Tidak menjadi trigger otomatis.                                                                   |
| Edit pesan lama yang menambahkan mention                              | Tidak membuat job baru; tampilkan aksi eksplisit untuk memberi tugas.                             |
| Satu manusia mengirim DM pribadi ke satu agent                        | Ikuti mode default profil dan grant DM.                                                           |
| Group DM tanpa mention agent                                          | Tidak membuat job.                                                                                |
| Aksi **Kerjakan dengan agent** pada pesan existing                    | Satu job manual; source message harus lolos pemeriksaan akses.                                    |
| Mention dua agent secara eksplisit                                    | Satu job per target yang sah; hasil admission ditampilkan per target.                             |
| Teks “lanjut” tanpa target tugas                                      | Tidak diarahkan ke job secara heuristik.                                                          |

MVP tidak menafsirkan emosi, kata kunci “bug”, atau potongan kode sebagai izin
menulis. Mode dan scope berasal dari konfigurasi yang dipilih pengguna. Untuk
multi-target, task ID tetap berbeda; keduanya tidak berbagi workspace writable.

## 8. F04 — Cara agent mengerjakan tugas

1. Dispatcher memilih pekerjaan berdasarkan runner, grant, kemampuan, dan kapasitas.
2. Runner melakukan claim; server membuat attempt dan lease secara atomik. Untuk
   lane fast, respons claim membawa paket konteks langsung [jalur cepat].
3. Runner memeriksa revision, deadline, akses repository, dan identitas checkout.
4. Jalur code: supervisor menyiapkan workspace terpisah serta resource dan
   network policy [jalur code]. Jalur cepat tidak menyiapkan workspace atau
   container; tidak ada checkout repository [jalur cepat].
5. Context broker menyusun tugas, input aktif, aturan proyek, dan referensi yang masih boleh dibaca.
6. Jalur code: runner memulai adapter dan melakukan handshake ACP; session ID
   disimpan pada attempt [jalur code]. Jalur cepat: runner memanggil
   `client.messages.stream` lewat `@anthropic-ai/sdk` di proses runner, tanpa
   handshake sesi terpisah [jalur cepat].
7. Runtime menerima prompt dan daftar tools yang sesuai grant.
8. Model mengusulkan tool call; broker memeriksa nama, argumen, scope, dan budget sebelum eksekusi.
9. Hasil tool kembali dengan call ID yang sesuai; progres terstruktur disimpan sebelum diakui diterima.
10. Bila diperlukan, job menunggu input atau approval melalui panel browser.
11. Jalur code: setelah turn selesai, verifier memeriksa diff, tree akhir, tes
    wajib, dan artifact [jalur code]. Jalur cepat: server menerbitkan hasil
    segera sesudah `result.prepared`, tanpa diff, tree, atau artifact
    [jalur cepat].
12. Result broker mempublikasikan hasil ke audiens yang sah dan menyimpan receipt publikasi.

Jalur code memakai lifecycle job yang sama untuk mode agent terpasang dan mode
endpoint. Perbedaan hanya berada pada adapter: agent native menyediakan
loopnya; mode endpoint memakai runtime ACP yang memanggil provider/model yang
dipilih [jalur code]. Jalur cepat memakai loop Anthropic SDK yang sama untuk
`answer` dan `manage`; tidak ada pilihan adapter atau endpoint
([jalur cepat](2026-09-24-agent-fast-lane.md) bagian 6).

`session/update` merupakan event runtime, bukan instruksi kepada server. Field
yang menjadi status produk harus berasal dari schema yang dikenal. Teks model
tidak dapat mengubah job menjadi completed, menyetujui operasi, atau memilih
identitas pengguna. Raw reasoning tidak dikirim ke chat atau panel publik.

Tool progress boleh diperbarui pada panel. Jangan memposting satu pesan chat
untuk setiap token, pembacaan file, atau heartbeat. Default chat menerima satu
pesan status awal dengan tautan tugas, keputusan yang memerlukan pengguna,
serta hasil akhir. Pembaruan status tidak boleh memicu agent lain.

### 8.1 Kontrak session dan context

Gunakan key efektif `(realm_id, agent_profile_id, conversation_id, repository_id,
audience_epoch)` dan ikat sesi eksekusi ke attempt. Conversation mempunyai ID
internal serta anchor message. Stream/topik hanya metadata rute. Rename topik
tidak membuka sesi baru secara kebetulan; pemindahan audiens dapat mewajibkannya.

Jalur code: satu attempt mempunyai satu proses/sesi kerja yang dapat dibuktikan
isolasinya. Jangan menggunakan ulang proses yang pernah menerima konteks privat
untuk audiens yang lebih luas. Session resume native hanya digunakan jika
adapter mendukungnya dan scope tetap cocok. Jika tidak, buat sesi baru dari
checkpoint terstruktur dan checkout yang diperiksa [jalur code].

Jalur cepat: beberapa job berjalan dalam satu proses runner. Aturan pengganti
"satu proses per attempt" adalah isolasi per attempt dari
[spesifikasi jalur cepat](2026-09-24-agent-fast-lane.md) bagian 6.6: setiap
attempt punya objek percakapan, `AbortController`, jurnal, dan batas sendiri;
tidak ada riwayat atau hasil alat yang dipakai bersama antar attempt
[jalur cepat].

Pemadatan konteks mempertahankan tujuan, input terbaru, status tool, file/hash,
batas izin, serta langkah tersisa. Ringkasan tidak menggantikan bukti tes atau
menjadi instruksi sistem tepercaya. Terapkan batas recovery dari spec awal.

## 9. F05 — Instruksi lanjutan dan banyak tugas

Tombol **Lanjutkan percakapan tugas** membuka composer dengan target job yang
terlihat. Pengguna juga dapat menyebut job ID melalui kontrol yang dikenali
server. Ini adalah jalur untuk menambah instruksi pada tugas existing.

Mention biasa di topik tanpa target job membuat tugas baru. Judul topik, agent
yang terakhir membalas, atau satu job yang kebetulan sedang aktif tidak cukup
untuk memilih target. Keputusan ini mengikuti spec awal dan mencegah instruksi
masuk ke pekerjaan lain ketika dua anggota bekerja pada topik yang sama.

Instruksi lanjutan disimpan sebagai `AgentInput`, dengan peminta, message ID,
sequence, target job, dan scope. Periksa bahwa penulis boleh memberi input pada
job itu. Menambahkan input tidak memberi izin baru untuk repository atau provider.

| Keadaan job                            | Perlakuan input                                                                                             |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Draft                                  | Lengkapi konfigurasi atau tujuan; belum menjalankan model.                                                  |
| Queued                                 | Tambahkan input menurut urutan commit sebelum claim.                                                        |
| Running                                | Simpan input untuk turn berikutnya; UI menyatakan belum diterapkan.                                         |
| Waiting for input                      | Jawaban dipetakan ke pertanyaan yang tepat dan memerlukan recheck izin.                                     |
| Waiting for approval                   | Instruksi tidak dianggap approval; perubahan operasi membatalkan proposal lama.                             |
| Verifying                              | Perubahan kode yang diminta mengembalikan fase edit dan membatalkan bukti terkait.                          |
| Completed/cancelled/failed/interrupted | Tampilkan **Buat tugas lanjutan** atau **Lanjutkan attempt** sesuai state; jangan membuka attempt terminal. |

MVP memakai antrean input antar-turn. Native steering merupakan kemampuan
opsional yang diaktifkan setelah diuji. Input hanya ditandai delivered setelah
runtime mengakui penerimaan pada batas yang terdokumentasi. Jika acknowledgement
hilang, simpan keadaan tidak pasti dan rekonsiliasi; jangan menghapus input dari
antrean hanya karena request sudah ditulis ke pipe.

## 10. F06 — Antrean, cold start, dan pembatasan

Ambil pola queue per scope dari Buzz, tetapi simpan job/input/outbox Grow Team
dalam database. Reaction, timer frontend, koneksi WebSocket, serta map di browser
bukan bukti durabilitas. Satu scope tidak dapat diproses dua executor aktif.

Default pilot satu job aktif per runner dan dua per realm dari spec awal tetap
berlaku untuk jalur code [jalur code]. Jalur cepat memakai kapasitas per jalur:
default 4 job `fast` aktif per runner, batas realm 8 job jalur cepat aktif dan
2 job jalur code aktif ([jalur cepat](2026-09-24-agent-fast-lane.md) bagian
5.2). Satu job jalur code aktif tidak memblokir job jalur cepat.

Tambahan batas antrean: paling banyak 20 job queued per profil dan 100 per realm.
Angka ini adalah konfigurasi awal yang harus diuji, bukan kemampuan terukur.
Saat penuh, admission baru ditolak dengan `queue_full`; tugas existing tidak
dibuang. Draft yang belum lengkap tidak dicampur ke kapasitas antrean eksekusi.

Job queued mempunyai deadline mulai default 24 jam. Sebelum deadline, runner
yang kembali online boleh melanjutkan claim dalam mandat yang masih berlaku.
Setelah deadline, job masuk blocked dengan alasan `start_deadline_expired` dan
memerlukan tindakan lanjut. UI menjelaskan deadline saat antrean dibuat.

Jalur code: cold start tidak perlu menyalakan semua runtime yang terdaftar.
Runner memulai proses saat ada claim dan menutupnya sesuai lifecycle attempt
[jalur code]. Jalur cepat: tidak ada proses per claim. Event `agent_job_ready`
membangunkan runner yang sudah aktif; runner memanggil `POST /runner/claims`
langsung sesudah menerima event
([jalur cepat](2026-09-24-agent-fast-lane.md) bagian 5.1) [jalur cepat].
Penggabungan wake harus dilakukan server/runner dengan key scope yang tepat,
bukan hanya map lokal satu tab. Dua browser dan dua worker tetap harus
menghasilkan satu attempt aktif.

Job yang efek eksternalnya belum pasti tidak boleh mendapat attempt pengganti
hanya karena heartbeat hilang. Lease dan fencing mengikuti spec awal; status
penghentian proses tetap dibedakan dari status koneksi.

## 11. F07 — Review, hasil, dan tugas yang tidak menghasilkan balasan

Panel menyediakan tiga tindakan terpisah: **Lihat perubahan**, **Lihat pemeriksaan**,
dan **Lihat keputusan**. Diff berasal dari artifact workspace; status tes berasal
dari eksekusi command yang tercatat, bukan kalimat ringkasan model.

Target `answer` selesai setelah jawaban atau blocker yang relevan tersimpan dan
terkirim. Target `patch` dan `draft_pr` mengikuti verifier pada spec awal. Jawaban
“tes lulus” tanpa verification record tidak cukup untuk menyelesaikan job coding.

ACP `end_turn` berarti runtime selesai berbicara pada turn itu. Jika tidak ada
hasil yang memenuhi kontrak, server menyimpan alasan, lalu menawarkan retry,
input, atau failure yang tepat. Aplikasi harus dapat menjelaskan runtime crash
tanpa menunggu runtime menulis permintaan maaf.

Pesan sistem menyatakan fakta, misalnya: “Tugas belum berjalan karena adapter
tidak ditemukan pada runner.” Jangan mengatasnamakan agent untuk membuat narasi
yang tidak pernah dihasilkannya. Tidak ada timer yang menyatakan sukses atau
memberi ucapan sambutan seolah agent sudah siap.

Untuk hasil akhir, gunakan delivery key stabil dan `result_message_id` dari spec
awal. Jika posting berhasil tetapi respons hilang, retry menemukan pesan yang
sama. Jika akses audiens berubah, hasil tetap pada panel yang sah dan publikasi
ditahan. Menyimpan artifact berhasil bukan bukti pesan hasil sudah terkirim.

## 12. F08 — Approval, cancel, resume, dan perubahan konfigurasi

### 12.1 Approval

Jalur code: permintaan permission ACP dipetakan ke grant terlebih dahulu
[jalur code]. Jalur cepat: alat tim memakai propose, konfirmasi, dan `execute`
dari [spesifikasi administrator](2026-09-23-agent-administrator.md) bagian
3.2, tanpa permintaan permission ACP [jalur cepat]. Operasi dalam mandat
berjalan tanpa pertanyaan berulang. Operasi di luar mandat menghasilkan
proposal yang dapat ditinjau: tindakan, repository/remote, branch, diff/commit,
argumen, budget, serta expiry. Pengguna memberi keputusan lewat panel browser.

Keputusan diikat ke attempt, operation hash, dan versi policy. Server memeriksa
ulang hak approver ketika keputusan dipakai. Perubahan diff atau tujuan membuat
approval lama kedaluwarsa. Jalur code: adapter memakai `optionId` yang
benar-benar ditawarkan [jalur code]. Jalur cepat: setiap `tool_use` alat tim
memakai nama dan argumen dari katalog jalur, divalidasi server sebelum
dispatch [jalur cepat]. Tidak boleh mengarang opsi izin atau otomatis
mengizinkan saat timeout.

### 12.2 Cancel

Cancel bukan pesan natural-language kepada model. UI memakai endpoint kontrol
job dengan optimistic concurrency. Server menahan tool/claim baru, membatalkan
approval pending, dan mengirim perintah supervisor. Jalur code: supervisor
meneruskan cancel ACP serta menghentikan process tree sesuai deadline
[jalur code]. Jalur cepat: server menulis `cancel_requested`; runner
membatalkan stream lewat `AbortController`, menunggu loop berakhir, lalu
mengirim `attempt.stopped`
([jalur cepat](2026-09-24-agent-fast-lane.md) bagian 9) [jalur cepat].

Job menjadi cancelled hanya setelah penghentian terkonfirmasi. Jika runner
tidak dapat dihubungi, tampilkan interrupted dan “penghentian belum terkonfirmasi”.
Cancel yang berulang aman. Cancel eksplisit tidak otomatis dimasukkan kembali
ke antrean oleh mekanisme retry kegagalan.

### 12.3 Resume

Resume memeriksa permission, revision, artifact, workspace, lease lama, serta
operation ledger. Setelah aman, buat attempt baru pada job yang sama. Checkpoint
berisi pekerjaan terverifikasi dan input yang belum diterapkan. Pending approval
attempt lama tidak ikut aktif. Efek Git dengan outcome belum pasti harus
direkonsiliasi sebelum dilanjutkan.

### 12.4 Edit, pause, dan arsip profil

Edit konfigurasi menaikkan revision dan membuat readiness lama stale. Job yang
belum diklaim menunggu revision baru lulus probe. Attempt aktif mempertahankan
snapshot yang dipakai, kecuali pencabutan izin/secret yang harus segera
menghentikan akses. Perpindahan provider atau repository membutuhkan replan dan
persetujuan scope, bukan penggantian diam-diam di tengah turn.

**Pause** menahan admission dan claim baru. UI memberi opsi terpisah untuk
membatalkan tugas aktif; pause sendiri tidak menjanjikan proses sudah berhenti.
**Arsipkan** mempertahankan ID, pesan, dan audit. Stop/cancel yang diperlukan
harus dikonfirmasi sebelum arsip dinyatakan selesai. Penghapusan permanen dan
pembersihan workspace bukan bagian aksi arsip MVP.

## 13. Kontrak data tambahan terhadap spec awal

Bagian ini memperluas model pada spec pertama. Nama merupakan usulan lokasi
tanggung jawab, bukan klaim model database sudah tersedia.

| Record atau field       | Data pokok                                                                       | Invariant                                                                                       |
| ----------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `AgentProfile` tambahan | Revision, desired state, default mode, default repository, readiness revision    | Enable memakai revision yang benar-benar diuji.                                                 |
| `AgentSetupOperation`   | Profile, runner, revision, phase, requirements, timestamps, retry key            | Retry setup tidak membuat identitas baru.                                                       |
| `AgentSendIntent`       | Realm, sender, client key, payload digest, source message ID                     | Satu logical send menghasilkan satu pesan untuk client key yang sama.                           |
| `AgentDispatchReceipt`  | Source message, target profile, requester, decision, reason, job ID opsional     | Unique `(realm, source_message, profile, trigger_kind)`; rejection tidak mendapat job eksekusi. |
| `AgentInput`            | Job, author, source reference, sequence, input type, content ref, delivery state | Input hanya diterapkan sekali pada batas penerimaan yang dapat dibuktikan.                      |
| `AgentJob` tambahan     | `job_kind`, `start_deadline`, admission revision                                 | `answer`, `manage`, dan `code` mempunyai kriteria selesai berbeda (v2, 2026-09-24). |
| `AgentAttempt` tambahan | Descriptor digest, runtime session reference, input cursor, `lane`, `model_policy.effort` | Session/runtime reference tidak menjadi grant. `lane` ditulis server saat claim, runner tidak memilihnya (v2, 2026-09-24). |

`job_kind` bernilai `answer`, `manage`, atau `code` (v2, 2026-09-24). `answer`
dan `manage` berjalan di lane `fast`; `code` berjalan di lane `code`. Lihat
[jalur cepat](2026-09-24-agent-fast-lane.md) bagian 3 untuk pemilihan jalur dan
`model_policy.effort` per jenis job. `delivery_target=answer` adalah
tambahan terhadap `patch` dan `draft_pr` pada spec awal. `answer` tidak membuka
tools mutasi; `manage` membuka alat baca dan alat tim sesuai grant. Review kode
tanpa mengedit dapat menjadi job answer dengan grant baca repository yang
terpisah dari akses chat.

Tidak diperlukan tabel persona/marketplace atau state presence baru untuk setiap
baris UI. Query status dibagikan oleh panel induk dan dibatasi frekuensinya.
Semua record tenant memakai realm, pemeriksaan reference satu realm, serta ACL
yang setara dengan model awal.

### 13.1 Idempotensi pengiriman dan input

Tambahkan field opsional `agent_send_key` pada jalur kirim chat untuk client Grow
Team. Ini adalah usulan perubahan API yang wajib mengikuti OpenAPI/changelog
Zulip saat implementasi. Browser membuat key ketika intent kirim dibentuk dan
mempertahankannya untuk retry payload yang sama.

Server mengunci atau mengklaim record intent dalam transaksi pengiriman. Digest
mencakup tujuan, teks, attachment, penerima agent, serta metadata target job yang
relevan. Key sama dengan digest berbeda menghasilkan conflict. Jika pesan sudah
tersimpan, kembalikan message ID existing dan receipt tanpa mengirim lagi.

Legacy client tanpa key tetap mendapat deduplikasi dispatch per message ID.
Dua pesan berbeda dari legacy client tetap merupakan dua permintaan berbeda;
jangan menjanjikan deduplikasi lintas pesan berdasarkan kesamaan teks.

Client membatasi retry otomatis satu intent sampai 24 jam. Simpan pemetaan
key/digest/message setidaknya 30 hari pada pilot dan sepanjang job masih aktif.
Jaminan deduplikasi berlaku selama pemetaan itu tersimpan; bukan selamanya setelah
retensi berakhir. Client tidak mengirim ulang draft lama secara otomatis setelah
batas retry; pengguna meninjau status sebelum memberi intent kirim baru.

Input lanjutan mempunyai key yang berbeda dari key pengiriman job awal. Gunakan
unique source message/reference dan client input key pada scope job. Sequence
ditetapkan server, tidak memakai jam browser sebagai urutan otoritatif.

### 13.2 Readiness report

Contoh report sintetis; ID adalah contoh, bukan record produksi:

```json
{
  "schema_version": 1,
  "profile_id": "bb70d753-b8cf-4a69-895c-a77eb124ec29",
  "profile_revision": 4,
  "runner_id": "beea6b19-f0bf-409b-a304-4c2db3e8aa35",
  "state": "needs_action",
  "capabilities": {
    "chat_ready": true,
    "code_ready": false
  },
  "requirements": [
    {
      "code": "workspace_not_registered",
      "surface": "runner_workspace",
      "action": "register_workspace"
    }
  ]
}
```

`surface` memilih UI perbaikan, bukan shell command dari runner. Client memakai
daftar aksi yang dikenal. Unknown requirement tetap ditampilkan sebagai pesan
umum yang aman dan reference diagnosis; client tidak mengeksekusi isi error.

### 13.3 Receipt pengiriman

```json
{
  "schema_version": 1,
  "source_message_id": 1204,
  "dispatch_receipts": [
    {
      "profile_id": "bb70d753-b8cf-4a69-895c-a77eb124ec29",
      "decision": "accepted",
      "job_id": "dd052769-8dce-4482-9dc6-75788f36702d",
      "job_status": "queued",
      "reason": "runner_offline"
    }
  ]
}
```

Keputusan receipt adalah `accepted`, `needs_input`, atau `rejected`.
`needs_input` menunjuk job draft yang belum dapat dijalankan. `rejected` tidak
membawa job ID eksekusi. Receipt dapat dimuat melalui endpoint agent setelah
pengiriman; tidak harus memperbesar payload semua event chat existing.

### 13.4 Event panel

Envelope tetap mengikuti schema version, event ID, job/attempt ID, lease epoch,
sequence, dan waktu pada spec awal. Event yang relevan untuk UI meliputi:

- `job.queued`, `attempt.starting`, `attempt.started`.
- `input.received`, `input.applied`, `input.delivery_uncertain`.
- `tool.started`, `tool.finished`, `verification.finished`.
- `input.requested`, `approval.requested`, `approval.resolved`.
- `attempt.stop_requested`, `attempt.stopped`, `attempt.interrupted`.
- `result.draft` (v2, 2026-09-24, lane fast saja): snapshot draft dengan
  `draft_seq`, dipakai server untuk mengedit pesan draft
  ([spesifikasi jalur cepat](2026-09-24-agent-fast-lane.md) bagian 8.2).
- `result.prepared`, `result.published`, `publication.blocked`.

Authority berbeda per event: server membuat admission dan keputusan approval;
runner melaporkan proses dan tools; verifier/publisher menetapkan hasil akhir.
Server tidak menerima event `approval.resolved` dari runner sebagai keputusan
manusia. Event yang berasal dari runner harus sesuai lease dan schema.

## 14. Endpoint dan tanggung jawab service

Endpoint dasar pairing, provider, job, approval, dan artifact tetap memakai spec
awal. Tambahan berikut membuat alur UI eksplisit. Semua route merupakan usulan
relatif terhadap `/api/v1`.

| Endpoint                                    | Tanggung jawab                                                              |
| ------------------------------------------- | --------------------------------------------------------------------------- |
| `POST /agent/profiles`                      | Create idempotent, bot identity, revision, setup operation.                 |
| `PATCH /agent/profiles/{id}`                | Edit dengan expected revision; invalidate readiness yang terdampak.         |
| `POST /agent/profiles/{id}/check`           | Menjadwalkan probe revision tertentu pada runner sah.                       |
| `GET /agent/profiles/{id}/readiness`        | Memuat report dan requirements yang boleh diketahui caller.                 |
| `POST /agent/profiles/{id}/enable`          | Enable setelah version/requirements sesuai.                                 |
| `POST /agent/profiles/{id}/pause`           | Menahan kerja baru; tidak mengklaim proses aktif sudah berhenti.            |
| `POST /agent/profiles/{id}/archive`         | Lifecycle arsip tanpa menghapus journal atau workspace.                     |
| `POST /agent/profiles/{id}/channels`        | Membership/grant kanal sesuai hak pemilik dan pengelola kanal.              |
| `POST /agent/message-preflight`             | Advisory validation terhadap snapshot mention dan tujuan.                   |
| `GET /agent/messages/{message_id}/dispatch` | Receipt milik caller; recheck akses source message.                         |
| `GET /agent/send-intents/{client_key}`      | Rekonsiliasi request pengiriman yang responsnya hilang; scope sender/realm. |
| `POST /agent/jobs/{id}/inputs`              | Input lanjutan idempotent dengan hak partisipasi job.                       |
| `GET /agent/setup-operations/{id}`          | Status setup dan retryable phase tanpa nilai secret.                        |

Endpoint profil tidak boleh menjadi jalur mengganti path perangkat atau command
host arbitrer. Aksi “retry” memanggil operasi spesifik; tidak menjalankan ulang
seluruh wizard, seluruh chat send, atau seluruh job tanpa mengetahui tahap gagal.

Service harus terpisah menurut tanggung jawab:

| Service                   | Input                                      | Output                              | Batas                                                    |
| ------------------------- | ------------------------------------------ | ----------------------------------- | -------------------------------------------------------- |
| Profile service           | Config dan hak pengguna                    | Profile/revision/setup record       | Tidak spawn di dalam transaksi.                          |
| Admission service         | Pesan tersimpan, provenance mention, grant | Receipt serta job/input/outbox      | Tidak memanggil LLM atau endpoint runner.                |
| Scheduler                 | Job queued dan kapasitas                   | Attempt/lease                       | Tidak menentukan izin dari teks prompt.                  |
| Runner supervisor         | Descriptor dan lease                       | Lifecycle proses, checkpoint, event | Tidak memegang credential admin Grow Team.               |
| Runtime adapter [jalur code] | Session/prompt/tools terbatas           | Event ACP dan stop reason           | Tidak menetapkan job completed.                          |
| Loop Anthropic SDK [jalur cepat] | Prompt, alat baca, paket konteks       | Delta streaming, `result.draft`, `result.prepared`, `attempt.stopped` | Tidak menetapkan job completed; tidak menjalankan kode. |
| Context/tool broker       | Permintaan dengan principal job            | Data/tool result sesuai scope       | Tidak menyediakan API key bot atau unrestricted signing. |
| Verifier/result publisher | Artifact, bukti pemeriksaan, tujuan        | Gate hasil dan publikasi idempotent | Recheck audiens dan tidak menebak keberhasilan tes.      |

## 15. Pesan UI dan recovery yang wajib

| Kode/keadaan               | Pesan utama                                                  | Tindakan yang ditawarkan                          |
| -------------------------- | ------------------------------------------------------------ | ------------------------------------------------- |
| `runner_offline`           | Tugas tersimpan. Menunggu runner terhubung.                  | Lihat perangkat atau batalkan tugas.              |
| `runner_unknown`           | Status runner belum dapat diperiksa.                         | Muat ulang status; jangan spawn duplikat.         |
| `runtime_missing`          | Adapter belum tersedia pada runner [jalur code], atau koneksi model belum lulus probe [jalur cepat]. | Buka petunjuk setup perangkat.                    |
| `auth_required`            | Agent perlu login ulang pada runner [jalur code], atau kunci koneksi model tidak sah [jalur cepat]. | Buka langkah autentikasi yang sesuai jalur.       |
| `provider_probe_failed`    | Koneksi model belum siap dipakai.                            | Lihat penyebab aman dan uji ulang.                |
| `workspace_not_registered` | Repository belum didaftarkan pada runner.                    | Pemilik mendaftarkan repository.                  |
| `profile_paused`           | Agent sedang dijeda.                                         | Pemilik dapat mengaktifkan kembali.               |
| `admission_denied`         | Pesan terkirim, tetapi tugas tidak diizinkan.                | Periksa akses atau pilih profil lain.             |
| `configuration_needed`     | Pilih repository atau lengkapi tugas terlebih dahulu.        | Buka form job draft.                              |
| `queue_full`               | Antrean agent penuh. Tugas belum diterima.                   | Coba setelah kapasitas tersedia.                  |
| `start_deadline_expired`   | Tugas belum mulai sampai batas waktunya.                     | Tinjau lalu lanjutkan atau batalkan.              |
| `start_failed`             | Tugas diterima, tetapi proses agent gagal dimulai.           | Retry tahap start setelah requirement diperbaiki. |
| `approval_pending`         | Tugas menunggu keputusan untuk tindakan ini.                 | Tinjau proposal, setujui, atau tolak.             |
| `stop_unconfirmed`         | Koneksi terputus. Penghentian proses belum terkonfirmasi.    | Periksa runner dan rekonsiliasi.                  |
| `publication_blocked`      | Hasil tersimpan, tetapi belum dapat diposting ke percakapan. | Tinjau tujuan yang masih sah.                     |

Kegagalan provider tidak langsung berarti credential salah. Bedakan auth,
network, rate limit, quota, dan schema incompatibility. Error detail harus
teredaksi dan hanya tersedia bagi pengguna yang berhak memperbaikinya.

Status bukan animasi berdasarkan stopwatch. Timer hanya menentukan batas
menunggu dan kapan melakukan pemeriksaan ulang; event nyata menentukan fase.
Semua status dan CTA harus dapat dipakai dengan keyboard dan pembaca layar.

## 16. Peta implementasi Grow Team

Path bertanda **baru** adalah usulan. Gunakan pola existing dan pisahkan perubahan
metadata render dari perubahan admission agar regresi chat mudah dilacak.

| Area             | Source Grow Team                                                                                                                                                      | Pekerjaan                                                                        |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| Profil/bot       | [settings_bots.ts](../../../web/src/settings_bots.ts), model pada spec awal                                                                                           | Tambahkan pengaturan agent tanpa mengganti bot existing.                         |
| Picker/composer  | [composebox_typeahead.ts](../../../web/src/composebox_typeahead.ts), [compose.ts](../../../web/src/compose.ts), [compose_state.ts](../../../web/src/compose_state.ts) | Profil yang sah, exact IDs, target job, preflight, dan recovery draft.           |
| Mention          | [mention.py](../../../zerver/lib/mention.py), [message_send.py](../../../zerver/actions/message_send.py)                                                              | Metadata mention personal sebelum ekspansi grup; provenance kode/kutipan/silent. |
| Bot trigger lama | `get_service_bot_events` pada `message_send.py`                                                                                                                       | Pertahankan behavior; adapter agent memakai jalur admission tersendiri.          |
| Admission        | `zerver/actions/agent_dispatch.py` — baru                                                                                                                             | Receipt, mode job, idempotency, scope, job/input/outbox atomik.                  |
| Profil/setup     | `zerver/actions/agent_profiles.py` — baru                                                                                                                             | Revision, readiness operation, enable/pause/archive.                             |
| Izin             | [message.py](../../../zerver/lib/message.py), [streams.py](../../../zerver/lib/streams.py), agent policy pada spec awal                                               | Gunakan helper ACL existing pada setiap batas.                                   |
| Job/input        | Model/action agent pada spec awal                                                                                                                                     | Tambahkan records dan field pada bagian 13.                                      |
| Event/results    | [queue.py](../../../zerver/lib/queue.py), worker/publisher agent baru                                                                                                 | Outbox scan, event dedupe, progress, dan hasil atomik.                           |
| Panel browser    | `web/src/settings_agents.ts`, `web/src/agent_jobs.ts` — baru                                                                                                          | Readiness, status multi-sumbu, receipt, review, approval, recovery.              |
| Runner [jalur code] | `services/grow-agent-runner/` — baru                                                                                                                               | Descriptor, adapter ACP, process supervisor, broker, checkpoint.                 |
| Runner [jalur cepat] | `services/grow-agent-runner/` — baru                                                                                                                              | Loop `@anthropic-ai/sdk`, `operations/run`, snapshot draft ([jalur cepat](2026-09-24-agent-fast-lane.md) bagian 6). |
| Dokumentasi API  | [Panduan API](../../../docs/documentation/api.md)                                                                                                                     | OpenAPI dan changelog untuk field/route baru.                                    |

Simpan snapshot scope pada batas async, bukan mengambil realm/topik/profil aktif
ulang ketika promise selesai. Backend tetap memeriksa authority terbaru. Frontend
snapshot mencegah salah tujuan; backend ACL mencegah tindakan tanpa hak.

## 17. Paket regresi yang dipindahkan dari Buzz

Jalur code: gunakan fake ACP process, fake HTTP provider, deferred promise,
clock yang dapat dikendalikan, serta repository fixture [jalur code]. Jalur
cepat: gunakan fake Anthropic Messages endpoint dengan streaming dan
`tool_use` terkendali, deferred promise, dan clock yang sama; tidak perlu fake
process karena tidak ada proses/container per attempt [jalur cepat]. Tes harus
mengamati efek nyata pada database, jumlah spawn, proses anak, dan artifact.
Assertion tidak cukup hanya memeriksa status string yang diisi mock.

### 17.1 Sumber fixture upstream

| Paket sumber                                  | Kasus yang dibaca                                                                                                                               | Bentuk adaptasi                                                                  |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| [Detached start tests][buzz-wake-tests]       | Scope tenant/identity, wake bersamaan, A → B → A, failure yang melepas key.                                                                     | Dua tab/worker dan start runner lambat tidak membuat attempt ganda.              |
| [Send cancellation tests][buzz-cancel-tests]  | Cancel saat invite, callback terlambat, draft edit-clear, upload, paste, perpindahan thread.                                                    | Composer tidak mengirim ke tujuan baru atau memulihkan draft yang sudah diganti. |
| [Draft authority tests][buzz-authority-tests] | Recovery lama melawan send atau pengeditan yang lebih baru.                                                                                     | Revisi draft dan key pengiriman menjaga perubahan terbaru pengguna.              |
| [Scope tests][buzz-scope]                     | Root canonical, repeated mention, DM, scope berbeda.                                                                                            | Conversation ID tetap; topik dengan dua job tidak mencampur input.               |
| [Queue tests][buzz-queue]                     | In-flight scope, kapasitas agregat, retry, withheld steering.                                                                                   | Antrean database, admission penuh terlihat, dan input tidak hilang.              |
| [Agent regression tests][buzz-regressions]    | `cancel_leaves_history_valid_for_next_prompt`, `mcp_init_timeout_kills_child`, `max_tokens_recovers_in_turn_without_running_partial_tool_call`. | Cancel aman, process cleanup, dan argumen tool parsial tidak dieksekusi.         |
| [Agent regression tests][buzz-regressions]    | `forced_handoff_retains_live_prompt_exactly_once`, `context_recovery_budget_exhaustion_surfaces_the_error`.                                     | Input aktif tidak hilang/berganda; recovery berhenti dengan sebab yang terlihat. |
| [Shell tests][buzz-shell]                     | Timeout dan batas durasi shell.                                                                                                                 | Buktikan seluruh process tree berhenti pada OS yang didukung.                    |

Jangan menyalin test double desktop Buzz sebagai bukti aplikasi web native
bekerja. Banyak tes UI upstream memakai mock IPC. Grow Team membutuhkan smoke
browser → Django → runner yang menguji wire contract sesungguhnya.

### 17.2 Kriteria penerimaan alur

AF merupakan tambahan bagi AT pada spec pertama. Tes resource, sandbox, dan
provider tingkat rendah tetap mengikuti matriks AT existing.

| ID    | Skenario                                                               | Bukti penerimaan                                                                   |
| ----- | ---------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| AF-01 | Create diklik dua kali atau respons hilang                             | Satu bot user, satu profil, dan setup operation yang dapat ditemukan kembali.      |
| AF-02 | Profil tersimpan tetapi probe gagal                                    | Profil tetap ada; retry tidak membuat identitas baru.                              |
| AF-03 | Kanal gagal ditambahkan setelah create                                 | UI membedakan profil berhasil dari akses kanal gagal.                              |
| AF-04 | Profil diedit selama probe berlangsung                                 | Hasil revision lama tidak mengaktifkan revision baru.                              |
| AF-05 | Dua profil bernama sama                                                | ID penerima yang dipilih tetap tepat setelah rename dan upload.                    |
| AF-06 | Presence/query gagal dengan cache Online lama                          | UI menampilkan unknown tanpa membuat proses duplikat.                              |
| AF-07 | Mention personal biasa pada profil Coding                              | Satu pesan, receipt, job, outbox, dan attempt sesuai scope.                        |
| AF-08 | Agent sama disebut berulang pada satu pesan                            | Hanya satu job untuk target itu.                                                   |
| AF-09 | Mention grup/wildcard yang mencakup agent                              | Tidak ada job; mention personal bersamaan tetap diproses.                          |
| AF-10 | Mention pada inline code, fenced code, blockquote, atau silent mention | Tidak ada trigger dari reference tersebut.                                         |
| AF-11 | Bot atau agent lain menyebut agent                                     | Tidak ada loop job baru.                                                           |
| AF-12 | Edit pesan menambahkan mention                                         | Tidak ada job baru tanpa tindakan eksplisit.                                       |
| AF-13 | Grant dicabut setelah preflight                                        | Pesan sah boleh tersimpan; receipt rejected dan jumlah spawn nol.                  |
| AF-14 | Runner start lambat setelah pesan terkirim                             | Tugas tetap ditemukan dan dijalankan; composer tidak tertahan oleh start.          |
| AF-15 | Runner offline kemudian online sebelum deadline                        | Job yang sama diklaim dengan recheck izin.                                         |
| AF-16 | Job belum mulai hingga deadline                                        | Job blocked; tidak mulai diam-diam setelah deadline.                               |
| AF-17 | Dua tab/worker mengirim wake bersamaan                                 | Paling banyak satu attempt aktif untuk job.                                        |
| AF-18 | Antrean penuh                                                          | Admission ditolak secara terlihat; job yang sudah diterima tetap utuh.             |
| AF-19 | Pindah A → B → A saat invite/upload/preflight tertahan                 | Intent lama tidak mengirim ke B atau menimpa draft A yang baru.                    |
| AF-20 | Pengguna mengedit lalu sengaja mengosongkan draft                      | Recovery terlambat tidak menghidupkan teks/attachment lama.                        |
| AF-21 | Respons kirim hilang, client mengulang key sama                        | Message ID dan receipts sama; tidak ada pesan/job tambahan.                        |
| AF-22 | Key pengiriman sama dengan payload berbeda                             | Conflict; tidak mengubah pesan pertama.                                            |
| AF-23 | Group DM tanpa mention versus DM satu agent                            | Hanya trigger yang didefinisikan pada bagian 6 diterima.                           |
| AF-24 | Dua job pada topik yang sama                                           | Tombol follow-up menulis input ke job yang dipilih saja.                           |
| AF-25 | Input datang saat job berjalan                                         | Input durable dan terlihat pending; diterapkan pada batas turn yang sah.           |
| AF-25b (v2, 2026-09-24) | Input susulan pada job jalur cepat aktif [jalur cepat]      | Input masuk di batas giliran alat berikutnya (maks 8 untuk `answer`, 20 untuk `manage`); input sesudah job selesai membuat job baru dengan `follows_job_id`. |
| AF-26 | Ack input hilang atau runtime restart                                  | Input tidak ditandai delivered tanpa bukti; recovery tidak menggandakan efek tool. |
| AF-27 | Mode Diskusi mendapat instruksi mengedit/push                          | Tools mutasi tidak tersedia; pengguna diarahkan membuat tugas coding.              |
| AF-28 | Turn selesai tanpa hasil valid: native agent [jalur code] atau `stop_reason` `max_tokens`/`refusal`/`end_turn` tanpa teks [jalur cepat] | Job tidak completed di kedua jalur; UI memberikan sebab dan tindakan lanjut. |
| AF-29 | Runner gagal sebelum dapat membalas                                    | UI/sistem melaporkan start failure dari record, tanpa membutuhkan output model.    |
| AF-30 | Cancel saat permission request menunggu [jalur code]                   | Approval tidak dapat dipakai; proses berhenti atau tampil belum terkonfirmasi.     |
| AF-30b (v2, 2026-09-24) | Cancel saat alat tim menunggu konfirmasi [jalur cepat]       | Approval server saja: tidak ada permission request ACP; propose ditahan, stream dibatalkan lewat `AbortController`. |
| AF-31 | Cancel bersamaan dengan completion                                     | Transisi memakai versi; hanya hasil sah yang menang dan tersimpan.                 |
| AF-32 | Resume setelah operasi remote tidak pasti                              | Rekonsiliasi dilakukan sebelum retry; approval lama tidak diaktifkan ulang.        |
| AF-33 | Profil pause saat ada tugas aktif                                      | Kerja baru tertahan; UI tidak mengklaim tugas aktif otomatis berhenti.             |
| AF-34 | Profil arsip/rename dan pesan lama dibuka                              | Identitas historis tetap tepat; tidak dialihkan ke agent lain.                     |
| AF-35 | Pindah/ubah audiens ketika hasil akan terbit                           | Result broker menahan publikasi yang memperluas akses.                             |
| AF-36 | Tab ditutup lalu dibuka dari browser lain                              | Job tetap berjalan; status, inputs, approval, dan hasil dipulihkan dari server.    |
| AF-37 | Provider/mode runtime berbeda untuk tugas identik, dan jalur berbeda untuk job_kind berbeda | Kedua mode jalur code melewati admission, policy, verifier, dan publisher yang sama [jalur code]; jalur cepat dan jalur code melewati admission, grant, dan publisher yang sama sesuai job_kind [paritas jalur]. |
| AF-38 | JSON tool terpotong atau context overflow [jalur code]                 | Tidak ada mutasi parsial; input aktif tetap ada dan recovery dibatasi.             |
| AF-38b (v2, 2026-09-24) | Delta `input_json` alat baca terpotong dari Anthropic SDK [jalur cepat] | Runner menyusun respons lengkap sebelum menjalankan alat; delta sebagian tidak pernah menjalankan alat (FL-14). |

Setiap fixture mencatat expected message count, job count, spawn count, status,
dan tindakan eksternal. Untuk state yang ditolak, nol efek merupakan assertion
utama. Untuk race, tahan dependency pada titik tertentu lalu ubah izin, scope,
atau revision sebelum melepaskannya.

## 18. Urutan implementasi agar pekerjaan tidak diulang

Bagian ini memecah P0–P6 pada spec awal menjadi keluaran alur. Ini bukan
otorisasi implementasi atau jadwal berbasis estimasi hari.

Gunakan S0–S6 sebagai urutan backlog implementasi. P0–P6 pada spec awal tetap
menjadi daftar cakupan dan gate teknis, bukan urutan pengerjaan kedua. Perubahan
urutan tidak menghapus gate tersebut. Cancel dasar dan penghentian proses dari
P1 harus tersedia sebelum fixture coding berjalan; S5 melengkapi recovery dan
alur approval, bukan menunda kontrol penghentian sampai tahap akhir.

Untuk lane fast, S3 (job Diskusi/`answer` dan `manage`) mengikuti fase F0–F5 di
[jalur cepat](2026-09-24-agent-fast-lane.md) bagian 12: F0 probe koneksi
model, F1 perbaikan cepat harness lama, F2 protokol v2 dan loop SDK di balik
flag, F3 pesan draft dan snapshot streaming, F4 katalog alat baca v2, F5 flag
aktif untuk semua profil [jalur cepat]. S4–S5 tetap memakai gate AF/AT untuk
lane code [jalur code].

| Urutan | Keluaran yang dapat ditinjau                                                       | Dependensi                                  | Gate                                                              |
| ------ | ---------------------------------------------------------------------------------- | ------------------------------------------- | ----------------------------------------------------------------- |
| S0     | Paket konformansi ACP dan endpoint untuk lane code; gerbang F0 koneksi model untuk lane fast | Runner fixture dan source Buzz yang dipatok | Dua mode lane code dapat round-trip; probe F0 lane fast lulus; batas permission dan cancel diketahui. |
| S1     | Profil, setup operation, pairing/readiness, enable/pause, dan panel perangkat      | Model dasar P1 spec awal                    | AF-01–AF-06; perubahan revision terlihat.                         |
| S2     | Provenance mention, receipt, send intent, admission, job/input/outbox              | S1 dan helper chat existing                 | AF-07–AF-13, AF-19–AF-23; fake runner cukup.                      |
| S3     | Claim, event `agent_job_ready`, antrean, job Diskusi/`manage` lane fast (F0–F5), progres dan jawaban melalui browser | S0–S2 | AF-14–AF-18, AF-24–AF-29, AF-36; FL-01–FL-30. |
| S4     | Workspace coding, tools, verification, diff, dan result broker [jalur code]        | S3 serta sandbox spec awal                  | AT coding relevan, AF-35–AF-38.                                   |
| S5     | Approval, cancel/resume, input delivery recovery, dan lifecycle arsip              | S3–S4                                       | AF-30–AF-34 serta AT approval/recovery.                           |
| S6     | Pilot tim, restore/rollback, uji dua browser, dua mode agent, dan dua jalur        | Semua gate sebelumnya                       | Seluruh AF, AT, dan FL yang relevan lulus dengan bukti runtime.   |

S0 hanya perlu menutup gap integrasi, bukan mengulang penelitian arsitektur dari
nol. Gunakan sumber dan kasus regresi di dokumen ini sebagai daftar awal. Semua
slice awal berada di balik feature flag dan belum diaktifkan untuk penggunaan
tim sebelum gate cancel, isolation, dan recovery yang relevan lulus.

Untuk setiap slice, simpan bukti minimum: commit/build runner, versi adapter,
profile revision, fixture ID, correlation IDs, jumlah efek yang diamati, dan
hasil pemeriksaan. Jangan menyimpan secret atau raw prompt pengguna dalam laporan.

## 19. Walkthrough penerimaan produk

Gunakan pengguna pemilik runner, anggota berizin, anggota tanpa izin, satu
repository fixture, serta satu kanal privat. Jalankan pada browser tanpa Buzz
Desktop atau Grow Team Desktop.

Walkthrough jalur code [jalur code]:

1. Pemilik memasangkan runner server dan membuat profil Coding dengan endpoint yang diuji.
2. Pemilik memberi anggota akses profil/repository; pengelola kanal menambahkan bot ke kanal privat.
3. Anggota menyebut agent untuk mengubah validasi kecil dan menjalankan pemeriksaan fixture.
4. Pesan tampil segera; panel menunjukkan queued, starting, running, lalu verifying berdasarkan event.
5. Anggota menutup tab; pekerjaan tetap berjalan pada runner.
6. Anggota membuka browser lain dan meninjau diff serta command/exit code pada tree akhir.
7. Anggota menambah input ke job aktif melalui kontrol target tugas; untuk job selesai, buat tugas lanjutan dengan hubungan ke job asal.
8. Pemilik menguji cancel saat proses anak berjalan dan memeriksa bahwa turunannya berhenti.
9. Runner diputus sebelum job berikutnya mulai; UI menampilkan antrean dan memulihkannya setelah terhubung.
10. Anggota tanpa izin mencoba trigger; tidak ada spawn atau kebocoran artifact.
11. Profil dipindah ke mode runtime lain (agent ACP terpasang atau endpoint) melalui revision baru yang diuji, tetap pada jalur code.
12. Ulangi jalur inti dan pastikan perilaku admission, status, review, serta cancel sama.

Walkthrough jalur cepat (v2, 2026-09-24) [jalur cepat]:

1. Pemilik membuat profil Diskusi dengan koneksi model yang lulus probe F0.
2. Anggota me-mention agent dengan pertanyaan sepele di topik kanal.
3. Indikator mengetik tampil dalam 1 detik; pesan draft muncul dan bertambah
   sedikit demi sedikit sampai hasil final, tanpa menunggu `attempt.stopped`.
4. Anggota me-mention agent dengan perintah alat tim untuk job `manage`;
   panel menunjukkan alat baca dan alat tim yang dipakai lewat `operations/run`.
5. Pemilik membatalkan job jalur cepat yang sedang streaming; pesan draft
   berubah menjadi teks batal dan job menjadi `cancelled`.
6. Runner offline saat mention; peminta langsung menerima teks "offline" dan
   job berjalan begitu runner kembali.

Sukses berarti bukti teknis dan perilaku browser sesuai kontrak. Animasi selesai,
pesan “done”, atau proses yang tercatat running tidak menggantikan bukti tersebut.

## 20. Batas riset dan catatan handoff

Yang sudah dilakukan: membaca source Grow Team yang relevan; menelusuri jalur
Buzz untuk create, readiness, mention preparation/publication, queue, session,
permission, dan recovery; membaca kasus regresi serta catatan desain/insiden.

Yang belum dibuktikan: build Buzz pada mesin ini, kelulusan suite upstream,
kompatibilitas provider pengguna, isolasi setiap adapter, dan latency end-to-end.
Target latensi dan keandalan sekarang ada di
[spesifikasi latensi dan keandalan](2026-09-24-agent-latency-and-reliability.md),
bukan di dokumen ini. Draft remote lifecycle dan information-flow Buzz tetap
diperlakukan sebagai proposal, bukan bukti fitur selesai atau keamanan produksi.

Implementor mulai dari spec awal untuk fondasi, dokumen ini untuk flow dan
regresi, lalu menutup gap runtime S0. Perubahan scope baru harus dicatat secara
eksplisit; jangan memulai migrasi platform, marketplace, Kubernetes provider,
atau multi-agent orchestration sebagai syarat alur MVP.

[buzz-create]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src-tauri/src/commands/agents.rs#L342
[buzz-readiness]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src-tauri/src/managed_agents/readiness.rs#L88
[buzz-attach]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src/features/agents/useCreatedAgentChannelAttachment.ts#L54
[buzz-identity]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/docs/agent-profile-identity.md
[buzz-mention-doc]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/docs/remote-mention-routing.md
[buzz-send]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src/features/messages/ui/useMentionSendFlow.ts#L553
[buzz-wake]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src/features/messages/ui/useDetachedAgentStart.ts
[buzz-replay]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/lib.rs#L2404
[buzz-filter]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/filter.rs#L368
[buzz-ingress]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/lib.rs#L3482
[buzz-scope]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/scope.rs
[buzz-queue]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/queue.rs#L294
[buzz-acp]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/acp.rs#L644
[buzz-permission]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-acp/src/acp.rs#L1944
[buzz-agent]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/src/agent.rs#L318
[buzz-handoff]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/src/handoff.rs
[buzz-regressions]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-agent/tests/regressions.rs
[buzz-availability]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/docs/agent-availability.md
[buzz-provenance]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/docs/agent-management-provenance.md
[buzz-kickoff]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/docs/welcome-kickoff-silent-failures.md#L152
[buzz-infoflow]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/docs/practical-information-flow-for-buzz-agents.md
[buzz-remote]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/docs/remote-agents.md
[buzz-wake-tests]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src/features/messages/ui/useDetachedAgentStart.test.mjs
[buzz-cancel-tests]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src/features/messages/ui/useMentionSendFlow.cancellation.test.mjs
[buzz-authority-tests]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/desktop/src/features/messages/ui/useMentionSendFlow.authority.test.mjs
[buzz-shell]: https://github.com/block/buzz/blob/5079c770fe30bb3d8204822ce6c2431eacac6d4b/crates/buzz-dev-mcp/src/shell.rs
