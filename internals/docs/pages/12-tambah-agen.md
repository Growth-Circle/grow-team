# 12 · Halaman Tambah agen

Status: spesifikasi halaman, belum kontrak. Konflik: lihat [keputusan rebrand](../sanji-rebrand-decision.md#konflik-halaman-dan-keputusan).

Tanggal: 24 September 2026 · Prototipe: Dashboard → Agen → **+ Buat agen** (sekarang berupa halaman penuh, bukan modal). Mengedit agen yang sudah ada tetap memakai modal "Atur".

Dasar keputusan: [riset 03: tipe agen kerja vs coding](../research/03-tipe-agen-kerja-vs-coding.md) · nama acak: [11](11-nama-default-agen.md) · peran & izin: [10](10-pengaturan-dan-peran.md).

## Tujuan

Membuat agen baru dalam ±1 menit, dengan pilihan yang jelas antara **Agen kerja** (langsung aktif di cloud sanji) dan **Agen coding** (berjalan di runner milik pengguna lewat ACP).

## Tata letak

- **Header:** tombol "← Agen", judul, dan **stepper 5 langkah** (Tipe → Identitas → Kemampuan/Lingkungan → Akses → Review). Langkah selesai = ✓ hijau, langkah aktif = hitam + titik oranye. Stepper bisa diklik untuk kembali ke langkah sebelumnya, atau maju setelah tipe dipilih.
- **Kolom kiri (2/3):** form langkah aktif + tombol **← Kembali / Batal** dan **Lanjut →**. Di langkah 5 tombol berubah jadi hijau **Buat {nama} ✓**.
- **Kolom kanan (sticky):** **Pratinjau** kartu agen (bentuk, warna, badge KERJA/CODING, peran, deskripsi, 3 fakta) + **Contoh di ruang** (mention dari pengguna dan balasan agen). Keduanya berubah langsung mengikuti isian.

## Langkah & interaksi

### 1 · Tipe

Dua kartu besar yang bisa dipilih (terpilih = latar kuning muda, border 2px, bayangan keras):
| | Agen kerja | Agen coding |
|---|---|---|
| Berjalan di | Cloud sanji | Runner milik pengguna |
| Setup | Tidak perlu | Runner + CLI + API key |
| Tool | Drive, web, Fathom, WA | File, shell, git (di container) |
| Biaya model | Kuota workspace | API key milik pengguna |
Memilih **coding** otomatis mengatur peran ke _Pembangun_, bentuk _Kotak_, dan warna _Ungu_. Lanjut tanpa memilih → toast "Pilih tipe agen dulu".

### 2 · Identitas

- **Nama** terisi acak dari pool 299 nama. **🎲 Acak** mengganti nama, kategori, dan warna. Keterangan: "Dari kategori X. Mention dengan @x". Nama yang diketik manual → "Nama buatan sendiri".
- **Peran** (Perencana/Pembangun/Peninjau/Custom), **Bentuk**, **Warna**.
- **Instruksi** (textarea) + chip template yang mengisi teks siap pakai: Kerja = Analis riset / Penulis laporan klien / Notulis rapat. Coding = Engineer frontend / Reviewer kode / Penulis test.
- Validasi: nama wajib diisi.

### 3a · Kemampuan (agen kerja)

- **Bisa bikin** (multi): Presentasi, Riset, Laporan, Notulen, Email & pesan, Olah data, Konten sosmed.
- **Tool & integrasi** (multi): Drive, Sheets, Slides, Pencarian web, Fathom, WhatsApp, Kalender.
- **Model** (kartu): Cepat/Haiku (RINGAN), Seimbang/Sonnet (DEFAULT), Terbaik/Opus (MAHAL).

### 3b · Lingkungan (agen coding)

- **Runner** (kartu): nama, OS, pemilik, badge ONLINE/OFFLINE. Status mengikuti Pengaturan → Agen & runner.
- Jika runner terpilih offline: banner merah muda + tombol **+ Pasangkan perangkat** (toast kode pairing). Tombol _Lanjut_ ditolak dengan toast.
- **Harness via ACP** (kartu): Claude Agent (`claude-agent-acp`, wajib `ANTHROPIC_API_KEY`; tampil hanya bila probe runner lulus, K-19/P-26), Codex CLI (`codex-acp`, dipin di image), atau Endpoint sendiri (OpenAI-compatible).
- **Cek runner** (checklist otomatis): container rootless, adapter/endpoint + versi, API key ditemukan (tidak dikirim ke sanji), akses git ke repo, kapasitas job paralel. Gagal = baris merah muda dengan "!".
- Catatan tetap: login langganan Claude Pro/Max tidak bisa dipakai.
- **Repository**: pilih satu repo.

### 4 · Akses & approval

- **Ruang yang boleh dimasuki** (multi, minimal 1).
- **Siapa boleh memberi tugas**: Semua anggota ruang / Hanya aku / Admin saja.
- **Wajib approval sebelum** (checklist). Item 🔒 WAJIB tidak bisa dimatikan (klik → toast):
  - Kerja: kirim ke luar workspace 🔒, tulis ke Drive klien 🔒, bagikan dokumen, buat tugas untuk orang lain.
  - Coding: push/merge ke branch utama 🔒, deploy production 🔒, jaringan di luar allowlist 🔒, buka PR, install dependensi baru.
- **Batas biaya/bulan**: Kerja = Rp100rb / Rp300rb / Rp1 jt / Ikut workspace. Coding = Tanpa batas (key milikmu) / Rp500rb / Rp1 jt.

### 5 · Review

Tabel ringkas (Tipe, Nama & peran, Runner & harness / Bisa bikin, Repo / Model, Ruang, jumlah approval), masing-masing dengan link **Ubah** ke langkahnya. Kotak kuning menjelaskan apa yang terjadi setelah agen dibuat. **Buat ✓** → pindah ke halaman Agen, kartu baru muncul (peran berakhiran `· KERJA` / `· CODING`), dan toast menyarankan mention pertama.

## Kebutuhan backend

| Kebutuhan                                       | Detail                                                                                                                                                                                                                                                                                                                                                              |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AgentProfile.kind`                             | `work` / `coding` (baru). Menentukan runtime: `work` → runner cloud sanji, `coding` → runner milik pengguna                                                                                                                                                                                                                                                         |
| `AgentProfile` field lain                       | `name`, `role`, `avatar_shape`, `avatar_color`, `instructions`, `model` (work) atau `runner_id` + `harness` (`claude-agent-acp`/`codex-acp`/`endpoint`) + `repo` (coding), `budget_monthly`                                                                                                                                                                         |
| `AgentGrant`                                    | Ruang, tool/integrasi (work), repo (coding), `who_can_command`                                                                                                                                                                                                                                                                                                      |
| `AgentApprovalPolicy`                           | Daftar aksi yang wajib approval. Item wajib dipaksa di server dan tidak disimpan sebagai pilihan                                                                                                                                                                                                                                                                    |
| `GET /api/v1/sanji/agents/suggest-name`         | Lihat doc 11                                                                                                                                                                                                                                                                                                                                                        |
| `GET /api/v1/sanji/runners/{id}/probe?harness=` | Hasil cek runner (container, adapter + versi, env key ada/tidak (boolean saja), akses repo, kapasitas). Runner menjawab lewat channel broker yang sudah ada                                                                                                                                                                                                         |
| `POST /api/v1/sanji/agents`                     | Validasi: izin `agent_create`, grant ⊆ akses pembuat, runner milik pembuat atau dibagikan, nama unik. Buat bot user Zulip + profil. Untuk `kind=coding`, **explicit enable** setelah probe lulus (bukan auto-enable, lihat [spec pengaturan §9 langkah 8](../spec/2026-09-22-agent-settings-connections-and-team-defaults.md#9-menambah-dan-mengatur-profil-agent)) |
| Draft                                           | Simpan progres wizard di `localStorage` supaya tidak hilang saat pindah halaman (opsional di MVP)                                                                                                                                                                                                                                                                   |

## Pertanyaan terbuka

- Runner milik orang lain (mis. SERVER-KANTOR milik Raka) boleh dipakai agen buatan Dita? Usulan: hanya jika pemilik runner membagikannya ke workspace.
- Agen kerja perlu opsi "BYO API key" untuk workspace yang ingin memakai key sendiri?
