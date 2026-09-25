# 08 · Landing publik (sanji.space)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat [keputusan rebrand](../sanji-rebrand-decision.md#konflik-halaman-dan-keputusan).

## Tujuan

Menjelaskan sanji.space dalam satu scroll dan mengumpulkan waitlist early access.

## Kenapa

Positioning: "Kerja bareng, manusia & agen." Use case diambil dari riset kebutuhan pekerja ([riset 01](../research/01-kebutuhan-kerja-pekerja.md)).

## Section

1. **Nav sticky:** logo, Bikin apa?, Demo, Agen, dropdown Unduh (Desktop/iOS/Android → saat ini mengarah ke waitlist).
2. **Hero:** headline, deskripsi, CTA "Gabung waitlist" / "Lihat demo".
3. **Marquee** teks berjalan.
4. **Mau bikin apa hari ini?:** 4 tab (Presentasi, Riset, Laporan, Notulen Rapat) berisi brief, alur Kaki→Ayame→Matcha→Kamu, chip "TERHUBUNG", alasan dengan data riset, dan contoh hasil. Plus chip kerjaan lain.
5. **Google Drive:** penjelasan integrasi + contoh folder.
6. **WhatsApp:** penjelasan integrasi + mockup chat.
7. **Demo chat otomatis:** chat, status agen, dan tugas yang tercentang.
8. **Tiga pihak:** Ngobrol, Ajak agen, Kirim hasil.
9. **Kenalan sama timnya:** 3 agen + "Agen buatanmu".
10. **Waitlist:** email, nama, tim.
11. **Footer:** CTA mengetik, form email singkat, 4 kolom link, wordmark, sosmed.

## Kebutuhan backend

| Kebutuhan                   | Detail                                                                                                                                                   |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /api/public/waitlist` | `email` (wajib), `name`, `team`, `source` (`hero`/`footer`), UTM. Rate limit + captcha ringan. Kirim email konfirmasi (email transaksi Zulip sudah ada). |
| Admin waitlist              | Daftar, ekspor CSV, tombol "Undang" → membuat realm/undangan                                                                                             |
| Halaman legal               | Syarat Layanan, Privasi, Pedoman Komunitas, Bantuan (saat ini link masih `#top`)                                                                         |
| Link sosmed                 | URL Instagram, LinkedIn, Threads                                                                                                                         |

## Klaim yang harus divalidasi sebelum publik

- Fathom: ikut rapat otomatis dan webhook/API tersedia.
- Google Drive: akses per folder (scope).
- WhatsApp: dukungan grup di jalur resmi.
- Angka riset di tab "Kenapa": cek ke laporan aslinya.
