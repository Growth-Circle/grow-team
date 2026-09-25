# 15 · PWA & notifikasi push (approval dari HP)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat [keputusan rebrand](../sanji-rebrand-decision.md#konflik-halaman-dan-keputusan).

Tanggal: 24 September 2026 · Keputusan owner: **perlu di MVP**.

## Tujuan

sanji adalah aplikasi web, tapi harus terasa seperti aplikasi di HP:

1. Bisa **dipasang di layar utama** (ikon mark S-blok, layar penuh tanpa bar browser).
2. **Notifikasi push** untuk hal yang butuh manusia: approval agen, mention, pairing runner, runner offline, batas biaya 80%.
3. Approval dan pairing bisa diselesaikan **langsung dari HP** dalam ≤ 2 ketukan.

## Kenapa

- Inti produk adalah "manusia yang memutuskan". Kalau approval hanya bisa dari laptop, agen menunggu lama.
- Pairing runner memakai device code, yang paling nyaman disetujui dari HP (riset 04).

## Yang dibangun

### 1. Manifest & service worker

| Item                               | Nilai                                                                                                    |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `name` / `short_name`              | `sanji.space` / `sanji`                                                                                  |
| `start_url`                        | `/?source=pwa` (satu manifest per host; tautan dalam memakai rute hash Zulip, mis. `/#today`)            |
| `display`                          | `standalone`                                                                                             |
| `theme_color` / `background_color` | `#16161D` / `#FFFAF0`                                                                                    |
| Ikon                               | Mark S-blok (oranye/krem/hijau) di kotak gelap, 192/512 + maskable                                       |
| Service worker                     | Cache shell (HTML/CSS/JS/font) untuk buka cepat & offline ringan. **Data chat tidak di-cache** (privasi) |

### 2. Web Push

- Standar Web Push + VAPID. Satu `PushSubscription` per perangkat per user.
- **iOS/iPadOS:** push hanya bekerja jika sanji **sudah ditambahkan ke Layar Utama** (Safari, iOS 16.4+). Karena itu ajakan "Pasang aplikasi" harus muncul **sebelum** ajakan notifikasi di iPhone. _(Cek ulang dukungan terbaru sebelum rilis)_
- **Android/desktop Chrome/Edge/Firefox:** push bisa langsung dari browser.
- Izin notifikasi **tidak diminta saat halaman pertama dibuka**. Minta hanya setelah pengguna menekan tombol yang jelas ("Nyalakan notifikasi"), agar tidak ditolak permanen.

### 3. Jenis notifikasi & aksi langsung

| Event                   | Judul contoh                               | Aksi di notifikasi     | Default     |
| ----------------------- | ------------------------------------------ | ---------------------- | ----------- |
| Approval agen           | "Ayame minta approval: PR #214"            | **Approve** · Lihat    | ON          |
| Keputusan A/B           | "Kaki: pilih copy harga"                   | Pilih A · Pilih B      | ON          |
| Mention                 | "Raka menyebut kamu di # klien-kopi-senja" | Balas · Buka           | ON          |
| Pairing runner          | "Perangkat MACBOOK-DITA minta tersambung"  | Buka layar persetujuan | ON          |
| Runner offline > 15 mnt | "SERVER-KANTOR offline"                    | Lihat                  | Owner/Admin |
| Batas biaya 80%         | "Kredit tinggal 20%"                       | Lihat tagihan          | Owner       |
| Job gagal               | "Job Ayame berhenti: runner tidur"         | Coba lagi              | ON          |

- Aksi berisiko (Approve merge/kirim ke klien) dari notifikasi **membuka aplikasi dan meminta konfirmasi sekali** (bottom sheet). Tidak dieksekusi diam-diam dari lock screen.
- Notifikasi dikelompokkan per ruang. **Jam tenang** default 21.00–07.00 (kecuali pairing yang baru saja dimulai pengguna).

### 4. UI di prototipe

- **Hari ini**: kartu ajakan "Pasang sanji di HP & nyalakan notifikasi" dengan 2 tombol: **Pasang aplikasi** (instruksi per OS) dan **Nyalakan notifikasi** (memanggil izin browser). Bisa ditutup (×).
- **Menu profil → Notifikasi**: pengaturan per jenis (fase berikutnya).

## Kebutuhan backend

| Kebutuhan                | Detail                                                                                                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `PushSubscription`       | `user_id`, `realm_id`, `endpoint`, `p256dh`, `auth`, `user_agent`, `created_at`, `last_success_at`. Hapus otomatis bila endpoint 404/410                           |
| `NotificationPreference` | Per user per jenis event: on/off, jam tenang, zona waktu                                                                                                           |
| Endpoint                 | `POST /api/v1/sanji/push/subscribe` · `DELETE /push/subscribe` · `GET/PUT /notification-preferences`                                                               |
| Pengirim                 | Worker antrean (sudah ada queue Zulip) → Web Push dengan VAPID. Retry dengan backoff                                                                               |
| Integrasi dengan Zulip   | Zulip sudah punya push untuk aplikasi mobile resmi. Web Push adalah jalur tambahan untuk PWA. Hindari notifikasi ganda: satu event → satu notifikasi per perangkat |
| Deep link                | Payload berisi `url` ke halaman tujuan (`/need/{id}`, `/device/{code}`, `/room/{id}#msg`)                                                                          |
| Keamanan                 | Payload terenkripsi (standar Web Push). Isi minimal, tanpa isi dokumen klien. Aksi Approve via notifikasi tetap melewati endpoint approval yang sama + konfirmasi  |

## Pertanyaan terbuka

- Perlu notifikasi email sebagai cadangan untuk approval yang tidak dibuka > 1 jam?
- Jam tenang per workspace atau per user?
