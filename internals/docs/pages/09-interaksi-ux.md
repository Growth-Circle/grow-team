# 09 · Spesifikasi interaksi (UX/UI) sanji.space dashboard

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

Dokumen ini menjelaskan **apa yang terjadi saat setiap tombol, menu, dan kartu diklik** di `Sanji Dashboard.dc.html`: efek visual, animasi, state, dan aturan bisnis. Baca bersama file per halaman (01–07) untuk kebutuhan backend.

Tanggal: 24 September 2026

---

## A. Pola global (dipakai di semua halaman)

### A1. Toast (notifikasi singkat)

| Aspek                      | Spesifikasi                                                                                                                                                                           |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Kapan muncul               | Setelah setiap aksi yang mengubah data (approve, pindah tugas, buat ruang, jeda agen, dll.)                                                                                           |
| Posisi                     | Halaman _Hari ini_: toast hijau inline di bawah kotak brief. Halaman lain, atau saat drawer/modal terbuka: toast gelap melayang di **tengah bawah** layar (`fixed`, 24px dari bawah). |
| Tampilan                   | Latar `#16161D`, teks krem, ikon ✓ hijau 18px, radius 14px, bayangan lembut.                                                                                                          |
| Animasi                    | Masuk: `sj-pop` (fade + naik 8px) 250ms.                                                                                                                                              |
| Durasi                     | Hilang otomatis setelah **3,2 detik**. Jika ada tombol _Urungkan_, **5 detik**.                                                                                                       |
| Urungkan                   | Muncul untuk aksi yang bisa dibatalkan: pindah tugas, hapus tugas, selesaikan item Perlu kamu, putus folder Drive. Klik → state dikembalikan, toast berganti "Dibatalkan."            |
| Aksesibilitas (backend/FE) | `role="status"` dan `aria-live="polite"`.                                                                                                                                             |

### A2. Drawer (panel detail kanan)

| Aspek         | Spesifikasi                                                                                                                        |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| Dipakai untuk | Detail **Tugas**, **Job agen**, **Rapat**, **File Drive**                                                                          |
| Ukuran        | Lebar `min(480px, 100vw)`, tinggi penuh, menempel di kanan, border kiri 2px tinta                                                  |
| Scrim         | Latar gelap 30% di belakang drawer. Klik scrim = tutup                                                                             |
| Struktur      | Header (label jenis, mono, + tombol ✕ bulat) → isi yang bisa di-scroll → footer tombol aksi (kiri: sekunder, kanan: primer oranye) |
| Animasi       | Masuk `sj-pop` 200ms                                                                                                               |
| Tutup         | Tombol ✕, klik scrim, tombol **Esc**                                                                                               |
| Aturan        | Hanya satu drawer terbuka. Membuka modal atau palette menutup drawer.                                                              |

### A3. Modal (dialog tengah)

| Aspek         | Spesifikasi                                                                                         |
| ------------- | --------------------------------------------------------------------------------------------------- |
| Dipakai untuk | Buat workspace, Pengaturan workspace, Ruang baru, Buat/Atur agen, Hubungkan folder Drive, Integrasi |
| Ukuran        | `min(560px, 92vw)`, maks tinggi 86vh (isi scroll), radius 24px, bayangan keras 8px                  |
| Scrim         | Gelap 50%. Klik = tutup (setara Batal)                                                              |
| Footer        | **Batal** (sekunder) + CTA oranye. Modal Integrasi tidak punya footer karena aksi ada per baris.    |
| Tombol CTA    | Hover: berubah jadi tinta. Klik: turun 2px (efek ditekan).                                          |
| Tutup         | ✕, Batal, scrim, Esc                                                                                |

### A4. Command palette (⌘K)

| Aspek           | Spesifikasi                                                                                                                                                                                          |
| --------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Buka            | Klik kolom "Cari atau tanya Kaki…" di sidebar, **atau** `⌘K` / `Ctrl+K` di mana saja (toggle)                                                                                                        |
| Tampilan        | Kotak 620px di 12% dari atas, scrim 45%, input fokus otomatis, ikon lingkaran Kaki                                                                                                                   |
| Isi saat kosong | 9 hasil: 5 halaman + ruang-ruang                                                                                                                                                                     |
| Saat mengetik   | Baris pertama selalu **"Tanya Kaki: '…'"**, diikuti hasil yang cocok dari Halaman, Ruang, Tugas, Aksi (maks. 8)                                                                                      |
| Hasil teratas   | Disorot kuning. **Enter** = jalankan hasil teratas                                                                                                                                                   |
| Efek klik       | Halaman → pindah view. Ruang → buka ruang. Tugas → pindah ke Tugas + buka drawer tugas itu. Aksi → buka modal terkait. Tanya Kaki → job baru "mencari" di Hari ini + toast "Jawabannya muncul di DM" |
| Tutup           | Esc, klik scrim, ⌘K lagi                                                                                                                                                                             |
| Backend         | `GET /api/v1/sanji/search?q=` (ruang, tugas, pesan, file). "Tanya Kaki" = `POST /briefs` dengan `kind=question`.                                                                                     |

### A5. Keyboard

| Tombol                               | Aksi                                                                        |
| ------------------------------------ | --------------------------------------------------------------------------- |
| `⌘K` / `Ctrl+K`                      | Buka/tutup palette                                                          |
| `Esc`                                | Tutup semua overlay (drawer, modal, palette, menu user, dropdown workspace) |
| `Enter` di palette                   | Jalankan hasil teratas                                                      |
| `Enter` di input komentar / composer | Kirim                                                                       |

### A6. Hover & fokus

- Tombol pil: hover mengganti latar ke kuning `#FFD84D` (sekunder) atau tinta (primer).
- Kartu yang bisa diklik: naik 1–3px + bayangan keras 3–4px.
- Input: fokus memberi ring kuning 3px (`box-shadow`), tanpa outline browser.
- Kursor `grab` pada kartu tugas (bisa diseret), `pointer` pada semua elemen klik lainnya.

---

## B. Sidebar & shell

| Elemen                                           | Klik → efek                                                                                                                                                                                      |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Pemilih workspace**                            | Dropdown melayang di bawahnya (krem, bayangan oranye). Chevron ▼ berubah jadi ▲.                                                                                                                 |
| └ Item workspace                                 | Pindah workspace: view kembali ke _Hari ini_, dropdown tertutup, toast "Pindah ke X. Ruang, agen, dan Drive ikut berganti." Workspace aktif diberi ✓ dan latar kuning muda.                      |
| └ **+ Buat workspace baru**                      | Modal _Buat workspace_ (lihat D1)                                                                                                                                                                |
| └ **Pengaturan workspace**                       | Modal _Pengaturan workspace_ (D2)                                                                                                                                                                |
| **Cari atau tanya Kaki…**                        | Command palette (A4)                                                                                                                                                                             |
| **Hari ini / Perlu kamu / Tugas / Agen / Drive** | Pindah view. Item aktif: latar krem, teks tinta, titik oranye. Badge oranye menunjukkan jumlah: Perlu kamu = item terbuka, Tugas = tugas milikku yang belum selesai.                             |
| **+** di samping RUANG                           | Modal _Ruang baru_ (D3)                                                                                                                                                                          |
| Item ruang                                       | Buka view Ruang. Item aktif berlatar krem. Ruang dengan pesan belum dibaca tampil putih tebal + badge jumlah.                                                                                    |
| **N ruang sepi > 30 hari**                       | Buka/lipat daftar ruang sepi (nama + lama tidak aktif, abu-abu, tidak bisa diklik). Setelah diarsip: label jadi "9 ruang sudah diarsipkan".                                                      |
| **Kartu pengguna** (bawah)                       | Menu melayang di atasnya: Profil saya, Notifikasi, Pintasan keyboard (menampilkan daftar pintasan di toast), Keluar (merah). Tiap item menutup menu dan menampilkan toast. Klik di luar = tutup. |

---

## C. Halaman

### C1. Hari ini

| Elemen                                                              | Klik → efek                                                                                                                                            |
| ------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Chip **Drive terhubung** / **Fathom · WhatsApp**                    | Modal _Integrasi_ (D6)                                                                                                                                 |
| Input brief                                                         | Ketik bebas                                                                                                                                            |
| Chip jenis (Presentasi/Riset/Laporan/Notulen/Lainnya)               | Chip aktif jadi tinta. Petunjuk tujuan di kanan ikut berganti (mis. "→ # riset-churn")                                                                 |
| **Serahkan ke Kaki →** (atau Enter)                                 | Input dikosongkan. Kartu job baru muncul **di posisi teratas** "Agen sedang kerja" dengan animasi pop, progress 8%. Toast hijau menyebut ruang tujuan. |
| Kartu **Perlu kamu**: tombol primer (Approve/Kirim/Bagikan/Pilih A) | Kartu hilang. Badge berkurang. Headline berubah ("2 hal perlu kamu" → "Semua beres"). Toast + Urungkan.                                                |
| Tombol sekunder (Lihat/Balas)                                       | Buka ruang sumber item                                                                                                                                 |
| Tombol **Pilih B** (item Keputusan)                                 | Sama dengan primer, dengan pesan versi B                                                                                                               |
| State kosong                                                        | Kartu hijau "Semua beres."                                                                                                                             |
| Kartu **Ringkasan ruang**: hover                                    | Naik 3px + bayangan                                                                                                                                    |
| └ **Buka ruang →**                                                  | View Ruang                                                                                                                                             |
| Kartu **Agen sedang kerja**                                         | Drawer _Job_ (E2). Hover: bergeser 3px ke kiri                                                                                                         |
| Baris **Rapat hari ini**                                            | Drawer _Rapat_ (E3). Hover: latar krem                                                                                                                 |
| **Arsipkan 9 ruang**                                                | Ruang sepi diarsip. Kartu berubah jadi "Beres…" tanpa tombol. Toast                                                                                    |
| **Lihat dulu**                                                      | Membuka daftar ruang sepi di sidebar                                                                                                                   |

### C2. Perlu kamu

| Elemen                                         | Klik → efek                                                                                  |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Tab **Semua / Approval / Mention / Keputusan** | Filter daftar. Tab aktif berwarna tinta. Angka di tiap tab selalu menunjukkan jumlah terkini |
| Tombol kartu                                   | Sama seperti di Hari ini                                                                     |
| Setelah ditangani                              | Item pindah ke bagian **Selesai hari ini** (tercoret, latar krem, nama ruang di kanan)       |
| Tab kosong                                     | "Tidak ada yang menunggu di kategori ini."                                                   |

### C3. Ruang

| Elemen                                             | Klik → efek                                                                                                                                                                  |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **← Hari ini**                                     | Kembali ke Hari ini                                                                                                                                                          |
| Chip **Drive / nama-ruang ↗**                     | Pindah ke halaman Drive dengan folder milik ruang ini terpilih. Jika belum ada folder: pindah ke Drive + toast "Ruang ini belum punya folder…"                               |
| **Ringkasan Kaki: ON/OFF**                         | Toggle. ON = latar kuning + banner ringkasan tampil di atas pesan. OFF = banner hilang. Toast menjelaskan konsekuensinya ("Kaki berhenti membaca ruang ini untuk rangkuman") |
| Topik di kolom kiri                                | Topik aktif berlatar tinta. Placeholder composer berganti "Tulis di {topik}…"                                                                                                |
| **Composer** + ↑ (atau Enter)                      | Pesan muncul di bawah sebagai "Dita · baru". Input dikosongkan                                                                                                               |
| └ Jika pesan berisi `@kaki` / `@ayame` / `@matcha` | Reaksi 👀 dalam ±300 ms, lalu kartu job ANTRE dalam ±600 ms; job baru muncul di Hari ini. Tanpa balasan teks. Backend: mention personal → admission → job (spec lifecycle).  |

### C4. Tugas

| Elemen                                                | Klik / gesture → efek                                                                                                  |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Filter **Semua / Punyaku / Agen**                     | Menyaring kartu di semua kolom. Jumlah per kolom ikut berubah                                                          |
| **Seret kartu** (drag)                                | Kartu yang diseret jadi transparan 40%. Kolom yang sedang dilewati berubah **kuning dengan border putus-putus**        |
| **Lepas di kolom** (drop)                             | Kartu pindah. Toast "'Judul' dipindah ke X" + **Urungkan**. Aktivitas tugas mencatat "Dita memindah ke X"              |
| └ Aturan: tugas **agen** dijatuhkan ke **Selesai**    | **Ditolak.** Kartu kembali. Toast: "Tugas agen harus lewat approval. Buka detail tugas lalu klik 'Approve hasil'."     |
| Tombol **Mulai → / Ke review → / Selesai ✓** di kartu | Alternatif untuk drag (layar sentuh/aksesibilitas). Aturan yang sama berlaku. Klik tombol **tidak** membuka drawer     |
| **Klik kartu**                                        | Drawer _Tugas_ (E1)                                                                                                    |
| Hover kartu                                           | Bergeser 1px + bayangan keras 3px                                                                                      |
| Mobile / sentuh                                       | HTML5 drag tidak berjalan di iOS Safari. Gunakan tombol maju, atau library pointer-events (mis. dnd-kit) saat produksi |

### C5. Agen

| Elemen                                                      | Klik → efek                                                                                                                                                 |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **+ Buat agen**                                             | Halaman _Tambah agen_ (D4)                                                                                                                                  |
| Toggle **Aktif / Jeda**                                     | Aktif = hijau. Jeda = abu-abu, titik status abu-abu, teks "Dijeda. Tidak menerima tugas baru." Toast menjelaskan bahwa mention akan dibalas "sedang dijeda" |
| **Log**                                                     | Menyaring _Log aktivitas_ di bawah ke agen itu (chip filter ikut aktif). FE: scroll ke bagian log                                                           |
| **Atur**                                                    | Modal _Atur agen_ (D4) dengan isian terisi                                                                                                                  |
| Chip filter log (Semua / Kaki / Ayame / Matcha / agen baru) | Filter baris log                                                                                                                                            |
| Agen buatan                                                 | Tampil sebagai kartu baru dengan bentuk & warna pilihan, statistik "0 / –"                                                                                  |

### C6. Drive

| Elemen                 | Klik → efek                                                                                                       |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------- |
| **+ Hubungkan folder** | Modal _Hubungkan folder_ (D5)                                                                                     |
| Kartu folder           | Jadi terpilih (latar putih, border 2px, bayangan). Tabel di bawah menampilkan isinya                              |
| **Putuskan**           | Folder hilang dari daftar. Toast + Urungkan. Backend: cabut `DriveFolderLink`, dan agen langsung kehilangan akses |
| Baris file             | Drawer _File_ (E4). Hover: latar krem. File baru dari agen berlatar kuning                                        |
| Folder tanpa aktivitas | "Belum ada aktivitas. Agen akan mulai membaca folder ini saat ada tugas di # ruang."                              |

---

## D. Modal (detail isi & validasi)

### D1. Buat workspace

- **Nama** (input) → alamat `slug.sanji.space` ditampilkan langsung di bawahnya (huruf kecil, tanpa spasi/simbol).
- **Warna**: 4 pil (Kuning/Hijau/Ungu/Oranye). Pil terpilih dibalik (latar tinta, teks berwarna).
- **Buat workspace** → workspace baru ditambah ke dropdown dan langsung aktif. Toast "Kaki, Ayame, dan Matcha sudah siap."
- Validasi (backend): slug unik, 3–30 karakter, bukan kata terlarang (`www`, `api`, `auth`, `app`).

### D2. Pengaturan workspace

- Nama & warna bisa diubah. Alamat hanya ditampilkan (tidak bisa diubah).
- **Ringkasan ruang (default ruang baru)**: _Mati (opt-in)_ / _Nyala_. Default: Mati, sesuai prinsip tanpa pemantauan umum.
- **Simpan** → nama/avatar di sidebar berubah. Toast.

### D3. Ruang baru

- Kartu oranye **"Biar Kaki yang buat dari brief (disarankan)"** → modal tutup, view Hari ini, toast yang mengarahkan ke kotak brief.
- Pemisah "ATAU BUAT MANUAL".
- **Nama ruang** (spasi otomatis jadi `-`), **Tipe** (Proyek/Klien/Tim). Jika _Proyek_: muncul **Tanggal selesai**.
- **Buat ruang** → ruang muncul di grup sidebar yang sesuai. View pindah ke ruang itu. Pesan pertama dari Kaki menjelaskan pemilik & tanggal arsip.
- Pemilik = pembuat (wajib, sesuai 01-shell).

### D4. Buat / Atur agen

- **Pratinjau** di atas: bentuk + warna + nama + peran · model, diperbarui langsung saat isian berubah.
- **Nama**, **Peran** (Perencana/Pembangun/Peninjau/Custom), **Bentuk** (Lingkaran/Cincin/Kotak), **Warna** (4), **Instruksi** (textarea), **Akses** (multi-pilih, pil terpilih diberi ✓), **Model**: agen kerja pakai preset Cepat/Seimbang/Terbaik, agen coding pakai Claude Code/Codex CLI/Endpoint sendiri.
- **Buat agen** → kartu baru di halaman Agen + chip filter log baru. Toast "Undang ke ruang dengan @Nama".
- Mode **Atur**: tombol jadi _Simpan_. Nama, instruksi, akses, dan model diperbarui di kartu.
- Backend: `AgentProfile` + `AgentGrant`. Akses di luar hak pembuat harus ditolak di server.

### D5. Hubungkan folder Drive

1. **Pilih folder** (daftar radio). Terpilih = latar kuning muda + border tinta. Di produksi, langkah ini diganti Google Picker.
2. **Sambungkan ke ruang** (pil).
3. **Akses agen**: _Baca & tulis_ / _Baca saja_.

- Kotak hijau: "Agen hanya bisa melihat folder ini."
- **Hubungkan** → kartu folder baru terpilih, label mode tampil di header tabel. Toast.

### D6. Integrasi

Baris per integrasi: ikon singkatan, nama, status, tombol.
| Integrasi | Tombol | Efek |
|---|---|---|
| Google Drive | Kelola | Pindah ke halaman Drive |
| Fathom | Kelola | Toast (halaman pengaturan belum didesain) |
| WhatsApp | Kelola | Toast (halaman pengaturan belum didesain) |
| Google Calendar | Hubungkan ⇄ Putuskan | Toggle status + toast. Hubungkan = tombol tinta |
| GitHub | Hubungkan ⇄ Putuskan | Toggle status + toast |

---

## E. Drawer (detail isi)

### E1. Tugas

| Bagian                             | Perilaku                                                                                                                                |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Judul                              | Input tanpa border. Fokus memunculkan garis bawah putus-putus. Tersimpan saat diketik (backend: debounce 500ms → `PATCH /tasks/{id}`)   |
| **Status** (4 pil)                 | Klik = pindah kolom, dengan aturan approval yang sama seperti drag                                                                      |
| PIC · Ruang ↗ · Deadline · Sumber | Kotak info 2×2. _Ruang ↗_ membuka ruang                                                                                                |
| Catatan agen (ungu)                | Hanya muncul untuk tugas agen. Menjelaskan bahwa status mengikuti job dan Selesai harus lewat approval                                  |
| Deskripsi                          | Teks                                                                                                                                    |
| **Checklist**                      | Klik item = centang/batal (kotak hijau ✓, teks tercoret). Penghitung "2/3" di label                                                     |
| **Aktivitas**                      | Timeline waktu + pelaku + aksi                                                                                                          |
| Komentar                           | Input + Kirim. Komentar masuk ke timeline. Jika berisi `@kaki/@ayame/@matcha`, agen memberi reaksi 👀 dalam ±300 ms, tanpa balasan teks |
| Footer                             | **Hapus** (sekunder → toast + Urungkan) dan tombol primer kontekstual:                                                                  |
| └ Tugas agen di **Review**         | **Approve hasil** → pindah ke Selesai (satu-satunya jalan agen ke Selesai)                                                              |
| └ Tugas agen lain                  | **Buka di ruang**                                                                                                                       |
| └ Tugas manusia belum selesai      | **Tandai selesai**                                                                                                                      |
| └ Tugas selesai                    | **Buka lagi** → kembali ke Belum                                                                                                        |

### E2. Job agen

- Bentuk agen 40px, judul, "Agen · # ruang · langkah".
- Progress bar bergaris yang beranimasi.
- **Langkah**: 5 tahap (baca brief → baca Drive → kerjakan → cek Matcha → approval). Tahap selesai = ✓ hijau, tahap aktif = titik kuning tebal, tahap berikutnya abu-abu.
- **Log langsung**: panel gelap monospace berisi event runner (grant check, tool call).
- Footer: **Jeda job ⇄ Lanjutkan job** (toast: "dijeda setelah langkah ini selesai") dan **Buka ruang** (primer).
- Backend: event `sanji_job_progress` + journal runner. Jeda mengikuti perilaku `paused-stop`.

### E3. Rapat

- Rapat **selesai**: Ringkasan Fathom + daftar _Action item → tugas_ (dengan PIC · hari). Footer: **Buka di Fathom ↗** dan **Lihat 3 tugas** (pindah ke halaman Tugas).
- Rapat **akan datang**: kartu toggle **"Fathom ikut rapat ini"** (Ikut hijau ⇄ Tidak ikut abu-abu). Footer: **Salin link rapat** dan **Gabung rapat ↗** (Meet/Zoom sesuai platform).

### E4. File Drive

- Ikon file berwarna, nama, path folder.
- Area pratinjau bergaris (di produksi: embed pratinjau Google Drive).
- **Riwayat**: siapa atau agen apa yang membaca, membuat, mengecek, dan kapan.
- Footer: **Minta Matcha cek** (job baru Matcha + drawer tertutup + toast) dan **Buka di Drive ↗**.

---

## F. Status & edge case yang wajib ditangani di produksi

| Kasus                              | Perilaku yang diharapkan                                                                                                     |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Aksi gagal di server               | Kembalikan UI ke state sebelumnya (optimistic rollback). Toast merah "Gagal menyimpan. Coba lagi." dengan tombol _Coba lagi_ |
| Approval kedaluwarsa               | Tombol Approve dinonaktifkan. Label "Kedaluwarsa. Minta agen ulangi"                                                         |
| Dua orang memindah tugas bersamaan | Server menang. Event realtime memindahkan kartu, toast info "Raka memindah tugas ini ke Review"                              |
| Agen dijeda saat job berjalan      | Drawer job menampilkan "Menunggu langkah selesai lalu berhenti"                                                              |
| Runner offline                     | Banner kuning di atas halaman Agen dan kartu job: "Runner LAPTOP-DITA offline. Job akan lanjut saat online"                  |
| Workspace tanpa ruang / tanpa agen | Empty state dengan satu CTA (Buat ruang / Buat agen)                                                                         |
| Layar < 820px                      | Sidebar menjadi drawer kiri (tombol ☰). Kolom Tugas di-scroll horizontal. Drawer jadi layar penuh                           |
| Loading                            | Skeleton krem bergaris pada kartu, tanpa spinner besar                                                                       |
