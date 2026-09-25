# 10 · Pengaturan & peran (role)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

Tanggal: 24 September 2026 · Prototipe: `Sanji Dashboard.dc.html` → menu **Pengaturan** (pakai Tweak **viewAs** untuk melihat tampilan tiap peran).

## Tujuan
Satu tempat bagi Owner dan Admin untuk mengatur **siapa boleh apa** di workspace, sebelum fitur lain (agen admin, integrasi, tagihan) dibuka ke klien.

## Kenapa peran diatur dulu
- sanji.space multi-tenant. Klien dan freelancer ikut masuk sebagai Tamu, jadi batas akses harus jelas sejak awal.
- Agen bisa mengubah data (mode *Team management*). Spec [agent-administrator](../spec/2026-09-23-agent-administrator.md) sudah mensyaratkan setting `can_command_administrator_agents_group` dengan default `role:administrators`.
- FRD FR-05: admin dapat mengundang anggota dengan peran dan **masa berlaku terbatas**.

---

## 1. Model peran

Memakai 5 peran bawaan Zulip, dengan label Indonesia. **Tidak membuat peran kustom di MVP.** Yang bisa diatur adalah izin per peran (matriks).

| Peran (UI) | Zulip `role` | Ringkasan |
|---|---|---|
| **Owner** | `ROLE_REALM_OWNER` (100) | Semua izin. Satu-satunya yang mengurus tagihan, menghapus/ekspor workspace, dan mengangkat Owner lain. Minimal 1 Owner per workspace. |
| **Admin** | `ROLE_REALM_ADMINISTRATOR` (200) | Anggota, peran, integrasi, keamanan, kebijakan agen. Tidak bisa mengangkat Owner atau mengubah izin kolom Admin. |
| **Moderator** | `ROLE_MODERATOR` (300) | Menjaga ruang (arsip, pindah topik). Tidak mengubah pengaturan. |
| **Anggota** | `ROLE_MEMBER` (400) | Chat, buat ruang, buat agen pribadi, approve hasil di ruangnya. |
| **Tamu** | `ROLE_GUEST` (600) | Hanya ruang yang diundang. Tidak bisa memberi tugas ke agen. |
| **Agen** | Bot user + `AgentProfile` | Bukan peran. Agen tampil di daftar anggota dengan label "Agen · milik X" dan **mewarisi paling banyak** akses pemiliknya. |

> Role Admin **tidak** memberi akses ke runner, provider, atau credential milik orang lain (spec agent-administrator §183).

## 2. Matriks izin default

✓ = diizinkan · – = tidak · 🔒 = terkunci (tidak bisa diubah dari UI)

| Grup | Izin | Owner | Admin | Moderator | Anggota | Tamu |
|---|---|:-:|:-:|:-:|:-:|:-:|
| Workspace | Ubah pengaturan workspace | ✓🔒 | ✓ | – | – | –🔒 |
| | Kelola paket & tagihan | ✓🔒 | –🔒 | –🔒 | –🔒 | –🔒 |
| | Hapus / ekspor workspace | ✓🔒 | –🔒 | –🔒 | –🔒 | –🔒 |
| Anggota | Undang anggota | ✓🔒 | ✓ | ✓ | – | –🔒 |
| | Ubah peran orang lain | ✓🔒 | ✓ | –🔒 | –🔒 | –🔒 |
| | Nonaktifkan anggota | ✓🔒 | ✓ | – | –🔒 | –🔒 |
| Ruang | Buat ruang | ✓🔒 | ✓ | ✓ | ✓ | – |
| | Arsipkan ruang milik orang lain | ✓🔒 | ✓ | ✓ | – | –🔒 |
| | Nyalakan ringkasan Kaki (ruang miliknya) | ✓🔒 | ✓ | ✓ | ✓ | –🔒 |
| Agen | Buat agen pribadi | ✓🔒 | ✓ | ✓ | ✓ | –🔒 |
| | Memberi tugas ke agen di ruang | ✓🔒 | ✓ | ✓ | ✓ | – |
| | Memberi tugas ke agen admin | ✓🔒 | ✓ | – | – | –🔒 |
| | Approve hasil agen | ✓🔒 | ✓ | ✓ | ✓ | –🔒 |
| | Pasangkan runner | ✓🔒 | ✓ | – | ✓ | –🔒 |
| Integrasi & keamanan | Hubungkan integrasi workspace | ✓🔒 | ✓ | – | – | –🔒 |
| | Sambungkan folder Drive ke ruang | ✓🔒 | ✓ | ✓ | ✓ | –🔒 |
| | Lihat log audit | ✓🔒 | ✓ | – | – | –🔒 |

**Aturan penguncian**
- Kolom Owner selalu ✓ dan terkunci.
- Kolom Admin hanya bisa diubah oleh Owner.
- Izin berisiko tinggi untuk Tamu terkunci ✗.
- Tagihan dan hapus workspace hanya untuk Owner, terkunci.

## 3. Struktur halaman Pengaturan

Menu **Pengaturan** muncul di sidebar (dan di menu profil) **hanya untuk Owner & Admin**. Peran lain yang membuka lewat URL atau ⌘K melihat kartu "Halaman ini khusus Owner dan Admin".

Navigasi kiri (sticky):

| Tab | Isi | Siapa |
|---|---|---|
| **Anggota & agen** (default) | Tabel orang + agen, filter per peran, cari, undang, ubah peran, nonaktifkan | Owner, Admin |
| **Peran & izin** | 5 kartu penjelasan peran + matriks izin yang bisa diklik | Owner, Admin (Admin tidak bisa mengubah kolom Admin) |
| **Umum** | Nama, alamat (read-only), zona waktu, bahasa agen, default ringkasan ruang | Owner, Admin |
| **Agen & runner** | Daftar runner (cabut/pasangkan), kebijakan agen, batas biaya | Owner, Admin |
| **Integrasi** | Drive, Fathom, WhatsApp, Calendar, GitHub | Owner, Admin |
| **Keamanan & audit** | 2FA wajib, domain email, masa berlaku undangan, log audit + ekspor CSV | Owner, Admin |
| **Tagihan** (label OWNER) | Paket, pemakaian model | Hanya Owner. Admin melihat pesan "hanya Owner" |

---

## 4. UX detail per tab

### 4.1 Anggota & agen
| Elemen | Klik → efek |
|---|---|
| Kolom **Cari** | Menyaring nama/email secara langsung |
| Chip filter peran (Semua/Owner/…/Agen · jumlah) | Menyaring tabel. Chip aktif berwarna tinta |
| **+ Undang** | Modal *Undang anggota* (4.2) |
| Dropdown **Peran** di baris | Pilih peran → baris langsung berubah warna sesuai peran, toast "X sekarang Y" (+ catatan khusus untuk Tamu), dan entri baru di log audit |
| Peran terkunci (abu) | Diri sendiri ("Owner (kamu)"), Owner jika kamu Admin, dan semua agen |
| **Nonaktifkan** | Baris hilang. Toast "Riwayat pesannya tetap ada". Masuk audit |
| **Batalkan** (baris undangan MENUNGGU) | Undangan dicabut |
| Baris agen | Bentuk agen + "Agen · peran · milik Dita". Peran agen diatur di halaman Agen, bukan di sini |
| Label **KLIEN** / **MENUNGGU** | Tag kecil di samping nama |

**Aturan bisnis (wajib di server)**
1. Tidak bisa mengubah peran diri sendiri.
2. Admin tidak bisa mengubah Owner dan tidak bisa mengangkat Owner (opsi Owner tidak muncul di dropdown).
3. Workspace minimal punya 1 Owner aktif.
4. Menurunkan Admin → langsung menolak admission baru untuk agen *manage* miliknya. Job aktif berhenti di request runner berikutnya (spec AD-04).
5. Mengubah ke **Tamu** → keluarkan dari semua ruang kecuali ruang yang dipilih. Minta konfirmasi di produksi.
6. Nonaktifkan = `do_deactivate_user`. Sesi dicabut, agen milik user dijeda.

### 4.2 Modal Undang anggota
- **Email** (textarea, dipisah koma/spasi). Validasi: minimal 1 email valid, kalau tidak muncul toast error.
- **Peran**: Admin / Moderator / Anggota (default) / Tamu. Di bawahnya tampil penjelasan peran yang dipilih.
- **Ruang awal**: # umum / # marketing / # klien-… (untuk Tamu wajib minimal 1 ruang).
- **Berlaku**: 7 hari (default) / 30 hari / Tanpa batas.
- **Kirim undangan** → baris "MENUNGGU" baru di tabel. Toast "N undangan terkirim sebagai Y".
- Email di luar domain yang diizinkan (tab Keamanan) otomatis jadi Tamu.

### 4.3 Peran & izin
| Elemen | Klik → efek |
|---|---|
| Kartu peran | Info saja: warna peran, jumlah orang, deskripsi |
| Sel matriks (bisa diubah) | Toggle ✓ (hijau) ⇄ kosong (putih). Tombol **Reset** dan **Simpan perubahan** muncul begitu ada perubahan |
| Sel terkunci | Latar pucat, border abu, kursor *not-allowed*. Klik → toast alasan ("Owner selalu punya semua izin" / "Hanya Owner yang bisa mengubah izin Admin" / "Terkunci demi keamanan"). Tooltip `title` menampilkan alasan yang sama |
| **Simpan perubahan** | Toast "Berlaku langsung untuk semua anggota" + entri audit |
| **Reset** | Kembali ke versi terakhir yang disimpan |
| Layar sempit | Matriks di-scroll horizontal (lebar minimum 720px) |

### 4.4 Umum
Nama (input), alamat (read-only, mono), zona waktu (WIB/WITA/WIT), bahasa agen (Indonesia/English), toggle default ringkasan ruang (Mati = opt-in), tombol **Simpan** → toast.

### 4.5 Agen & runner
- **Runner**: kartu per perangkat (titik hijau/abu, OS, pemilik, jumlah agen). Tombol:
  - Online → **Cabut akses** (credential dirotasi, agen di runner berhenti)
  - Offline milik orang lain → **Ingatkan {pemilik}** (Kaki mengirim DM)
  - Dicabut → **Pasangkan lagi**
- **+ Pasangkan perangkat** → toast kode pairing + instruksi CLI. Pairing baru aktif setelah disetujui di browser (security.md).
- **Kebijakan agen**:
  - *Siapa boleh memberi tugas ke agen admin*: Hanya Admin (default) / Admin + Moderator / Tidak ada. Dipetakan ke `can_command_administrator_agents_group`.
  - *Approval untuk aksi keluar*: 🔒 selalu aktif.
  - *Agen boleh memicu agen lain*: 🔒 mati (PRD: pesan bot tidak memicu agen).
  - *Batas biaya model/bulan*: Rp500rb / Rp1 jt (default) / Rp5 jt / Tanpa batas.

### 4.6 Integrasi
Daftar yang sama dengan modal Integrasi (09 §D6). Tombol Kelola / Hubungkan / Putuskan.

### 4.7 Keamanan & audit
- Toggle **2FA wajib** (Opsional ⇄ Wajib) → toast jumlah anggota yang akan diminta mengaktifkan.
- **Domain email** yang diizinkan (mis. `kopisenja.id`).
- **Masa berlaku undangan** default.
- **Log audit**: waktu · pelaku (manusia/agen) · aksi. Setiap ubah peran, nonaktifkan, batalkan undangan, dan simpan izin langsung muncul di atas. **Ekspor CSV** → toast.

### 4.8 Tagihan
Owner: kartu kuning "Pilot internal · gratis" + pemakaian model bulan ini. Admin: kartu merah muda "hanya Owner". Billing belum masuk rilis internal (PRD, ruang lingkup komersial).

---

## 5. Kebutuhan backend

### Data
| Entitas | Field | Catatan |
|---|---|---|
| `UserProfile.role` (bawaan) | Owner/Admin/Moderator/Member/Guest | Tidak ada peran kustom |
| `RolePermission` (baru) | `realm_id`, `permission_key`, `role`, `allowed` | Satu baris per sel matriks. `permission_key` sesuai tabel §2. Sel terkunci **tidak disimpan**, dipaksa di kode |
| Setting realm bawaan Zulip | `invite_to_realm_policy`, `can_create_*_channel_group`, `can_command_administrator_agents_group`, dll. | Jika izin sudah punya padanan setting Zulip, **matriks menulis ke setting itu**, bukan ke tabel baru |
| `PreregistrationUser` / `MultiuseInvite` (bawaan) | + `expires_at`, `initial_stream_ids`, `invited_as` | Untuk undangan dengan masa berlaku |
| `RealmAuditLog` (bawaan) | Ditambah event agen | Sumber tab Keamanan & audit |
| `RealmSanjiSettings` (baru) | `timezone`, `agent_language`, `summary_default`, `model_budget_monthly`, `require_2fa`, `allowed_email_domains[]` | |

### Pemetaan izin → setting Zulip (usulan)
| permission_key | Setting Zulip yang dipakai |
|---|---|
| `invite` | `can_invite_users_group` |
| `room_create` | `can_create_public_channel_group` / `can_create_private_channel_group` |
| `room_archive` | Izin arsip stream berbasis grup |
| `agent_admin` | `can_command_administrator_agents_group` (spec) |
| Lainnya | `RolePermission` baru |

### API
| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/api/v1/sanji/settings/members?role=&q=` | Orang + undangan + agen |
| PATCH | `/api/v1/users/{id}` (bawaan) | Ubah `role`. Validasi aturan §4.1 |
| DELETE | `/api/v1/users/{id}` (bawaan) | Nonaktifkan |
| POST | `/api/v1/invites` (bawaan) | + `expires_in`, `stream_ids`, `invite_as` |
| GET/PUT | `/api/v1/sanji/settings/permissions` | Matriks. PUT ditolak untuk sel terkunci dan kolom Admin bila pemanggil bukan Owner |
| GET/PATCH | `/api/v1/sanji/settings/general` | `RealmSanjiSettings` |
| GET | `/api/v1/sanji/settings/runners` · POST `/runners/pair` · DELETE `/runners/{id}` | Runner |
| GET | `/api/v1/sanji/settings/audit?cursor=` · GET `/audit.csv` | Audit |

### Penegakan izin
- **Setiap endpoint** memanggil `check_permission(user, key, target)`. UI yang menyembunyikan tombol bukan pengaman.
- Agen dicek dua kali: izin **pemilik** agen, **dan** grant agen itu sendiri. Hasilnya irisan keduanya.
- Perubahan peran/izin dikirim sebagai event realtime (`realm_user` update, `sanji_permissions`) supaya sidebar (mis. menu Pengaturan) langsung berubah tanpa reload.

## 6. Pertanyaan terbuka
- Perlu **peran kustom** (mis. "Klien Viewer") setelah pilot? Usulan: tunda, pakai Tamu + ruang.
- Apakah Moderator boleh mengundang **Tamu** saja, atau semua peran?
- Batas biaya model per workspace atau per agen?
