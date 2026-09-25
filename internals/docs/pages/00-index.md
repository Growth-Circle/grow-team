# Dokumentasi halaman sanji.space

Dokumen di folder ini menjelaskan **fungsi**, **alasan**, dan **kebutuhan backend** untuk setiap halaman di prototipe `Sanji Dashboard.dc.html` dan `Sanji Landing.dc.html`.

Tanggal: 24 September 2026 · Basis kode: fork Zulip `grow-team` + `services/grow-agent-runner`.

Keputusan rebrand dan domain kustom: [sanji-rebrand-decision.md](../sanji-rebrand-decision.md).

## Prinsip produk (berlaku di semua halaman)

1. **Kerjaan dulu, kanal belakangan.** Layar utama menjawab pertanyaan "apa yang butuh aku?", bukan "kanal mana yang harus kubuka?". Latar belakangnya ada di [riset 02](../research/02-dashboard-kebanyakan-channel.md).
2. **Manusia yang memutuskan.** Setiap hasil agen yang berdampak ke luar (merge, kirim ke klien, bagikan) harus melewati approval manusia.
3. **Agen adalah anggota tim, bukan fitur tersembunyi.** Agen punya nama, bentuk, status, dan log. Setiap aksinya bisa dilacak.
4. **Tenant = workspace = realm Zulip.** Data tidak pernah bocor antar workspace.

## Sumber desain

Berkas prototipe ini ada di luar repo ini. Repo ini publik, jadi berkas ini
belum masuk repo (lihat D-09 di [keputusan rebrand](../sanji-rebrand-decision.md)).

| Berkas | SHA-256 |
|---|---|
| `Sanji Dashboard.dc.html` | `32061995f5b5f9200ddd79ce32a84e9f0763f71e265faff7c1ee5dd4d247aa60` |
| `Sanji Brand Identity v2.dc.html` | `df78c08c9c43eefa82c8466113f192c28ce3df8ebeb40e588534df486fcb688c` |
| `Sanji Landing.dc.html` | `4676cad91a9103dff4673d64b5025328f5dfdbd2bb2a4c29d118d2c83a193245` |
| `support.js` | `8fe7df74405f3c55f49b7249c74ea1397e65d07dea2b1bd3b4a489bec2e28cbe` |

## Daftar halaman

| # | Halaman | File | Status prototipe |
|---|---|---|---|
| 01 | Shell: sidebar, pemilih workspace, navigasi ruang | [01-shell.md](01-shell.md) | Interaktif |
| 02 | Hari ini (home) | [02-hari-ini.md](02-hari-ini.md) | Interaktif |
| 03 | Perlu kamu (inbox triase) | [03-perlu-kamu.md](03-perlu-kamu.md) | Interaktif |
| 04 | Ruang (percakapan + topik) | [04-ruang.md](04-ruang.md) | Interaktif |
| 05 | Tugas (papan) | [05-tugas.md](05-tugas.md) | Interaktif |
| 06 | Agen | [06-agen.md](06-agen.md) | Interaktif |
| 07 | Drive | [07-drive.md](07-drive.md) | Interaktif |
| 08 | Landing publik | [08-landing.md](08-landing.md) | Interaktif |
| 09 | **Spesifikasi interaksi semua tombol, menu, drawer, modal** | [09-interaksi-ux.md](09-interaksi-ux.md) | Referensi FE |
| 10 | **Pengaturan & peran (role, matriks izin, undangan, runner, audit)** | [10-pengaturan-dan-peran.md](10-pengaturan-dan-peran.md) | Interaktif |
| 11 | **Nama default agen: 300 nama + algoritma acak** | [11-nama-default-agen.md](11-nama-default-agen.md) | Interaktif (modal Buat agen) |
| 12 | **Halaman Tambah agen (wizard Kerja / Coding)** | [12-tambah-agen.md](12-tambah-agen.md) | Interaktif |
| 13 | **Pesan agen di ruang: kartu job, reaksi 👀, gagal + coba lagi** | [13-pesan-agen-di-ruang.md](13-pesan-agen-di-ruang.md) | Interaktif |
| 14 | **Runner: cloud sanji / VPS / lokal, device pairing, shell responsif** | [14-runner.md](14-runner.md) | Interaktif |
| 15 | **PWA & notifikasi push (approval dari HP)** | [15-pwa-notifikasi.md](15-pwa-notifikasi.md) | Interaktif (kartu di Hari ini) |
| 16 | **Koneksi MCP: lingkup workspace/ruang/pribadi, kebijakan per tool, mode Riset dulu** | [16-koneksi-mcp.md](16-koneksi-mcp.md) | Interaktif |
| 17 | **Model & API key (OpenRouter): sumber model, preset, guardrail, budget per agen** | [17-model-dan-api-key.md](17-model-dan-api-key.md) | Interaktif |

## Istilah

| Istilah UI | Arti | Padanan di kode |
|---|---|---|
| Workspace | Satu tenant (perusahaan/tim) | `Realm` |
| Ruang | Tempat percakapan dengan tipe & pemilik | `Stream` + tabel baru `RoomMeta` |
| Topik | Sub-percakapan di dalam ruang | `topic` (bawaan Zulip) |
| Agen | Anggota non-manusia yang menjalankan tugas | Bot user + `AgentProfile` (spec agent) |
| Job | Satu tugas yang dikerjakan agen | `AgentJob` / attempt (spec lifecycle) |
| Approval | Permintaan persetujuan manusia atas aksi agen | Approval terikat attempt (PRD) |
| Runner | Mesin (laptop/server) yang menjalankan agen | `grow-agent-runner` |

## Cara memakai dokumen ini

Setiap file halaman punya bagian yang sama: **Tujuan → Kenapa → Isi layar → Interaksi → Kebutuhan backend (data, API, event realtime, izin) → Pertanyaan terbuka**. Mulai dari bagian "Kebutuhan backend" untuk memecah tiket.
