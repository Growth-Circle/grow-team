# 03 · Perlu kamu (inbox triase)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

## Tujuan

Satu daftar berisi **semua hal yang menunggu keputusan atau jawaban pengguna**, dari semua ruang. Kalau daftar ini kosong, pengguna tidak perlu membuka ruang mana pun.

## Kenapa

Tim yang mengurus puluhan kanal membutuhkan inbox triase supaya tetap bisa dipakai ([riset 02](../research/02-dashboard-kebanyakan-channel.md)). Unread count Zulip mencampur info dan permintaan. Halaman ini hanya menampilkan **permintaan**.

## Isi layar

- Header "Perlu kamu" dan nama workspace.
- Tab filter dengan jumlah: Semua / Approval / Mention / Keputusan.
- Kartu item: bentuk pengirim (agen atau inisial manusia), tag jenis, pengirim · ruang · waktu, teks, lampiran (PR, file), serta dua tombol (sekunder + primer).
- State kosong: "Tidak ada yang menunggu".
- **Selesai hari ini:** item yang sudah ditangani ditampilkan tercoret.

## Jenis item

| Tag       | Sumber                                                          | Tombol primer                     | Tombol sekunder     |
| --------- | --------------------------------------------------------------- | --------------------------------- | ------------------- |
| APPROVAL  | Job agen butuh izin (merge PR, kirim ke klien, bagikan dokumen) | Approve / Kirim / Bagikan         | Lihat (buka detail) |
| MENTION   | Pesan manusia yang @mention pengguna dan berisi pertanyaan      | Aksi yang disarankan (mis. Kirim) | Balas               |
| KEPUTUSAN | Agen menawarkan pilihan (A/B)                                   | Pilih A                           | Pilih B             |

## Kebutuhan backend

### Data: `NeedItem` (baru)

| Field                                    | Keterangan                                       |
| ---------------------------------------- | ------------------------------------------------ |
| `id`, `realm_id`, `assignee_user_id`     | Pemilik item                                     |
| `kind`                                   | `approval` / `mention` / `decision`              |
| `source_type`, `source_id`               | `agent_approval` / `message` / `agent_decision`  |
| `room_id`, `topic`, `actor` (user/agent) | Konteks                                          |
| `title`, `attachment` (JSON: PR, file)   | Tampilan                                         |
| `actions` (JSON)                         | Label + aksi server untuk tombol primer/sekunder |
| `status`                                 | `open` / `resolved` / `expired`                  |
| `resolved_at`, `resolved_action`         | Audit                                            |

### Aturan pembuatan item

- **approval:** dibuat oleh control plane saat attempt agen masuk status `awaiting_approval`. Approval harus terikat pada attempt, policy, argumen, dan tree, sesuai [PRD](../prd.md). **Tombol di UI hanya memanggil endpoint approval yang sama**, bukan membuat jalur baru.
- **mention:** dibuat jika pesan berisi mention personal ke pengguna, belum dibalas, dan (opsional) terdeteksi sebagai pertanyaan. Mention grup/wildcard **tidak** membuat item.
- **decision:** dibuat oleh agen lewat team-tool `request_decision(options[])`.
- Item otomatis `resolved` jika pengguna membalas di ruang atau approval diproses dari tempat lain.

### API

| Method | Endpoint                                          | Fungsi                                                                                                                |
| ------ | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| GET    | `/api/v1/sanji/needs?kind=&status=open`           | Daftar + jumlah per tab                                                                                               |
| POST   | `/api/v1/sanji/needs/{id}/act`                    | `{action: "primary" \| "secondary" \| "option:B"}`. Server menjalankan aksi (approve attempt, kirim file, pilih opsi) |
| GET    | `/api/v1/sanji/needs?status=resolved&since=today` | Daftar "Selesai hari ini"                                                                                             |

### Event realtime

`sanji_need_created`, `sanji_need_resolved` → memperbarui badge sidebar, Home, dan dropdown workspace.

### Izin & keamanan

- Aksi approve dicek ulang di server: pengguna masih berhak, attempt belum kedaluwarsa, argumen tidak berubah.
- Setiap aksi dicatat di log audit (dipakai juga oleh [06 Agen](06-agen.md)).

## Pertanyaan terbuka

- Apakah mention tanpa tanda tanya tetap masuk? Usulan: masuk hanya jika berupa DM atau thread yang menunggu balasan lebih dari 2 jam.
- Kapan item `expired`? Usulan: mengikuti TTL approval attempt.
