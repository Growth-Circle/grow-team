# 01 · Shell: sidebar, pemilih workspace, navigasi ruang

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

## Tujuan
Kerangka yang selalu terlihat di setiap halaman. Fungsinya memberi tahu pengguna **sedang di workspace mana**, membawa ke 5 halaman utama, dan menampilkan ruang yang *relevan saja*.

## Kenapa
- **Multi-tenant:** salah workspace berarti pesan atau file bisa terkirim ke klien yang salah. Karena itu nama workspace ditaruh di posisi paling atas-kiri, menggantikan logo brand.
- **Kebanyakan kanal:** sidebar Zulip yang berisi semua kanal membuat orang bingung harus mulai dari mana. Di sini ruang dikelompokkan per tipe, dan ruang yang sepi disembunyikan.

## Isi layar
1. **Pemilih workspace** (atas): avatar huruf berwarna, nama, alamat `{slug}.sanji.space`. Saat diklik, muncul dropdown berisi:
   - Daftar workspace milik pengguna, dengan peran (Owner/Admin/Anggota/Tamu), jumlah item "Perlu kamu" di workspace lain, dan tanda ✓ di workspace aktif.
   - Tombol "Buat workspace baru" dan "Pengaturan workspace".
   - Logo sanji.space kecil di bagian bawah.
2. **Cari / tanya Kaki (⌘K):** pencarian global sekaligus tempat mengetik perintah ke agen perencana.
3. **Navigasi utama:** Hari ini, Perlu kamu (badge jumlah), Tugas (badge jumlah tugas pribadi yang belum selesai), Agen, Drive.
4. **Daftar Ruang**, dikelompokkan: Disematkan, Proyek, Klien, Tim. Setiap item menampilkan jumlah pesan belum dibaca. Ruang yang berisi pesan belum dibaca ditampilkan tebal.
5. **Ruang sepi:** ruang yang tidak aktif lebih dari 30 hari dilipat ke tombol "N ruang sepi". Bila dibuka, tampil nama ruang dan lama tidak aktifnya.
6. **Tombol + Ruang:** mengarahkan ke kotak "Mau bikin apa?". Ruang proyek sebaiknya dibuat oleh Kaki dari sebuah brief, bukan dibuat manual.
7. **Kartu pengguna** (bawah): nama, peran, nama workspace.

## Interaksi
- Berpindah workspace mengganti seluruh konteks (ruang, agen, Drive, tugas). Tampilkan pemberitahuan singkat.
- Mengklik ruang membuka [04 Ruang](04-ruang.md).

## Kebutuhan backend

### Data
| Entitas | Field penting | Catatan |
|---|---|---|
| `Realm` (bawaan) | `string_id` (slug subdomain), `name`, `icon` | Tambah `brand_color` untuk avatar |
| `RoomMeta` (baru) | `stream_id`, `type` (`project`/`client`/`team`), `owner_user_id` (wajib), `due_date` (khusus project), `pinned`, `summary_enabled`, `last_activity_at`, `archived_at` | Relasi 1:1 dengan Stream |
| `UserRealmMembership` | Daftar realm yang diikuti satu identitas login | Zulip memisahkan user per realm. Perlu tabel penghubung **identitas email → banyak realm** supaya dropdown bisa menampilkan semua workspace. |

### API
| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/api/v1/sanji/workspaces` | Daftar workspace milik identitas + peran + jumlah item "Perlu kamu" |
| GET | `/api/v1/sanji/sidebar` | Ruang dikelompokkan per tipe + unread + ruang sepi |
| POST | `/api/v1/sanji/rooms/{id}/pin` | Sematkan / lepas |
| POST | `/api/v1/sanji/rooms/archive-quiet` | Arsipkan ruang sepi secara massal (lihat 02) |

### Event realtime (event queue Zulip)
- `sanji_room_meta` (update tipe/pemilik/arsip), `unread_count` (bawaan), `sanji_need_count` (badge Perlu kamu per realm, termasuk realm lain untuk dropdown).

### Izin
- Hanya Owner/Admin realm yang boleh membuat workspace baru dan mengubah pengaturan.
- Tamu hanya melihat ruang yang ia ikuti. Grup "ruang sepi" tidak ditampilkan untuk Tamu.

### Job terjadwal
- **Deteksi ruang sepi** (harian): tandai ruang dengan `last_activity_at` > 30 hari. Kaki mengirim DM ke pemilik ruang sebelum ruang diusulkan untuk diarsip.

## Pertanyaan terbuka
- Login SSO lintas subdomain: pakai satu domain auth (`auth.sanji.space`) atau login per subdomain?
- Apakah ruang tipe Proyek diarsip otomatis saat `due_date` lewat, atau hanya diusulkan?
