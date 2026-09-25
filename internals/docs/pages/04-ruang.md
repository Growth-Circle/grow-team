# 04 · Ruang (percakapan + topik)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

## Tujuan

Tempat diskusi. Ini adalah Stream Zulip yang diberi tipe, pemilik, folder Drive, dan ringkasan agen.

## Kenapa

Chat tetap dibutuhkan, tapi **tidak lagi menjadi pintu masuk utama**. Ringkasan di atas ruang membuat orang yang baru masuk tidak perlu membaca 46 pesan untuk memahami keadaan.

## Isi layar

1. **Header:** tombol "← Hari ini", `# nama`, chip tipe (PROYEK/KLIEN/TIM), pemilik, jumlah orang dan agen (plus tanda WA bila tersambung ke WhatsApp), chip folder Drive, dan toggle "Ringkasan Kaki: ON/OFF".
2. **Kolom topik:** daftar topik dengan jumlah pesan. Topik aktif disorot.
3. **Banner ringkasan** (jika ON): "Kaki merangkum N pesan sejak kemarin" beserta isinya.
4. **Pesan:** avatar manusia (inisial) atau bentuk agen, label AGEN, waktu.
5. **Composer:** "Tulis di {topik}… ketik @ untuk memanggil agen".

## Kebutuhan backend

- **Pesan & topik:** memakai Zulip apa adanya (`/messages`, narrow stream+topic).
- **Mention agen:** mengikuti [spec lifecycle & mention flow](../spec/2026-09-21-agent-lifecycle-and-mention-flow.md). Trigger hanya dari mention personal yang sah. Pesan bot tidak memicu agen lain.
- **Label AGEN:** berasal dari `UserProfile.is_bot` + keberadaan `AgentProfile`.
- **Bentuk agen** (lingkaran/cincin/kotak) dan warnanya disimpan di `AgentProfile.avatar_shape`, `avatar_color`, sehingga frontend bisa merender tanpa gambar.

### API baru

| Method | Endpoint                             | Fungsi                                                                                 |
| ------ | ------------------------------------ | -------------------------------------------------------------------------------------- |
| GET    | `/api/v1/sanji/rooms/{id}`           | Meta ruang (tipe, pemilik, folder Drive, integrasi, summary_enabled) + digest hari ini |
| PATCH  | `/api/v1/sanji/rooms/{id}`           | Ubah tipe/pemilik/due_date/summary_enabled (khusus pemilik/admin)                      |
| POST   | `/api/v1/sanji/rooms/{id}/summarize` | Minta ringkasan ulang secara manual                                                    |

### Ringkasan ruang

- Job terjadwal Kaki membaca pesan sejak digest terakhir → `RoomDigest`.
- Wajib opt-in (`summary_enabled`). Saat OFF, agen **tidak boleh** membaca riwayat ruang untuk keperluan ringkasan.
- Ringkasan hanya boleh dilihat oleh anggota ruang (gate audiens).

### Integrasi WhatsApp (opsional per ruang)

- `RoomChannelLink` (baru): `room_id`, `provider` (whatsapp), `external_id` (nomor/grup), `direction`.
- Pesan masuk dari WA → pesan Zulip dengan pengirim tamu eksternal. Balasan agen/manusia → dikirim balik ke WA.
- ⚠️ Perlu dicek apakah WhatsApp Business Platform resmi mendukung grup. Jika tidak, batasi ke nomor 1:1 dan perbaiki copy di landing.

## Pertanyaan terbuka

- Apakah topik "Rilis Jumat" dan sejenisnya perlu ditautkan ke Tugas (`task_id` di topik)?
