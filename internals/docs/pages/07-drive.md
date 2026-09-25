# 07 · Drive

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

## Tujuan

Menghubungkan folder Google Drive ke ruang, lalu menampilkan **file apa yang dibaca, dibuat, atau dicek agen** di folder itu.

## Kenapa

File kerja tim sudah ada di Drive. Memindahkannya adalah friksi. Prinsipnya: file tetap di Drive, agen yang datang ke sana (sesuai klaim di landing).

## Isi layar

- Header: akun Google yang terhubung, tombol "+ Hubungkan folder".
- Kartu folder: nama, jumlah file, ruang tujuan (`→ # ruang`). Kartu aktif disorot.
- Tabel file untuk folder terpilih: nama, aktivitas terakhir (agen/manusia + aksi), waktu diubah. File baru dari agen disorot kuning.
- Catatan: "Agen hanya bisa membaca dan menulis di folder yang terhubung".

## Kebutuhan backend

### Data

| Entitas             | Field                                                                                                                        |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `DriveConnection`   | `realm_id`, `google_account`, token terenkripsi, `scopes`, `connected_by`                                                    |
| `DriveFolderLink`   | `room_id`, `folder_id`, `folder_name`, `mode` (read / read_write)                                                            |
| `DriveFileActivity` | `folder_link_id`, `file_id`, `file_name`, `mime`, `actor` (user/agent), `action` (read/create/update/review), `job_id`, `at` |

### Izin OAuth, penting

- Klaim di UI dan landing adalah **akses per folder**. Gunakan scope sempit (mis. `drive.file` + Google Picker untuk memilih folder), bukan scope penuh `drive`. Jika scope sempit tidak cukup untuk membaca isi folder, putuskan ulang dan **perbaiki copy di UI/landing** agar tetap jujur.
- Token disimpan terenkripsi dan dikelola sesuai lifecycle credential di [security.md](../security.md).

### API

| Method | Endpoint                                 | Fungsi                                   |
| ------ | ---------------------------------------- | ---------------------------------------- |
| GET    | `/api/v1/sanji/drive/folders`            | Folder terhubung + jumlah file           |
| POST   | `/api/v1/sanji/drive/folders`            | Hubungkan folder ke ruang (hasil Picker) |
| DELETE | `/api/v1/sanji/drive/folders/{id}`       | Putuskan                                 |
| GET    | `/api/v1/sanji/drive/folders/{id}/files` | File + aktivitas terakhir                |

### Tool agen

- `drive.list`, `drive.read`, `drive.write` (buat Docs/Sheets/Slides/PDF) dipasang sebagai team-tool di `tool-broker` runner. Setiap panggilan harus:
  1. Memeriksa bahwa folder terhubung ke ruang tempat job berjalan.
  2. Menulis `DriveFileActivity` + `AgentAuditEvent`.
- Menulis file ke folder klien termasuk aksi yang berdampak keluar, jadi masuk alur approval.

## Pertanyaan terbuka

- Satu akun Google per workspace, atau per pengguna?
- Perlu dukungan Shared Drive sejak MVP?
