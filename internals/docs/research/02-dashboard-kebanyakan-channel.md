# Riset: Dashboard sanji.space, keluar dari "kebanyakan channel"

Status: riset, bukan keputusan.

**Tanggal:** 24 September 2026
**Konteks:** Sanji adalah fork Zulip yang tersusun atas kanal → topik → pesan, ditambah agen lewat mention (lihat [PRD](../prd.md) dan spec lifecycle mention). Keluhannya: channel sudah terlalu banyak, jadi bingung harus kerja dari mana.

## 1. Diagnosis

| Gejala                                    | Penyebab                                                                                                                      | Sumber                |
| ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| Tidak tahu harus buka apa dulu            | Layar utama berpusat pada _tempat_ (kanal), bukan _kerjaan_. Orang harus memindai sidebar untuk menemukan apa yang butuh dia. | Pola umum Slack/Zulip |
| Kanal menumpuk, banyak yang mati          | Kanal dibuat reaktif tanpa struktur dan tidak pernah diarsipkan.                                                              | Social Intents 2026   |
| Tidak jelas kanal mana yang masih penting | Kanal tanpa pemilik tidak punya orang yang bisa ditanya, jadi lolos dari setiap bersih-bersih.                                | Siit                  |
| "Aku nggak lihat pesan itu"               | Masalahnya bukan jumlah kanal, tapi tidak ada sistem yang disepakati sejak awal.                                              | Question Base 2026    |
| Sidebar penuh                             | Merapikan sidebar secara pribadi hanya mengobati gejala. Sprawl adalah masalah tim, bukan per orang.                          | Question Base 2026    |
| Skala                                     | Setelah tim mengurus puluhan kanal, fitur seperti inbox triase, filter AI, dan tampilan antrean jadi syarat.                  | ClearFeed 2026        |

Catatan: Slack sendiri berpendapat kanal yang banyak tidak masalah asal sempit fokusnya dan tiap orang hanya ikut sedikit. Artinya yang perlu diperbaiki adalah **cara masuk dan menyaring**, bukan menghapus kanal.

## 2. Ide: balik urutannya. Kerjaan dulu, kanal belakangan

1. **"Hari ini" jadi layar utama, bukan daftar kanal.** Isinya hanya tiga hal: _Perlu kamu_ (approval, mention yang butuh jawaban, keputusan), _Agen sedang kerja_, dan _Rapat & deadline_.
2. **Kanal diganti "Ruang" dengan tipe yang jelas:** Proyek (punya tanggal selesai dan diarsip otomatis), Tim (permanen), dan Klien. Setiap ruang wajib punya pemilik. Ini adaptasi dari konvensi prefix `proj-` / `team-` / `cust-`, tapi dibuat struktural, bukan sekadar nama.
3. **Rangkuman ruang oleh agen.** Daripada membaca 80 pesan, Kaki menulis 3 baris per ruang: keputusan, blocker, dan siapa ditunggu. Topik Zulip tetap ada di dalam ruang.
4. **Satu kotak "Mau bikin apa?"** untuk memulai kerja. Pengguna tidak perlu memilih kanal dulu. Kaki yang menaruhnya di ruang yang tepat atau membuat ruang proyek baru.
5. **Sidebar dibatasi:** yang tampil hanya ruang yang disematkan dan yang aktif 7 hari terakhir. Ruang sepi lebih dari 30 hari masuk grup "Sepi" dan diusulkan untuk diarsip, beserta nama pemiliknya.

## 3. Pemetaan ke arsitektur Zulip yang ada

| Konsep sanji      | Di Zulip / Sanji                                                                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Ruang             | Stream + metadata tipe/pemilik/tanggal selesai (tabel baru)                                                                                                   |
| Topik             | Topic (tanpa perubahan)                                                                                                                                       |
| Perlu kamu        | Gabungan: mention personal belum dibalas, approval job agen (FR approval), DM belum dibaca                                                                    |
| Agen sedang kerja | Status job/attempt dari control plane runner                                                                                                                  |
| Rangkuman ruang   | Job agen terjadwal. Perlu grant baca stream, dan harus mematuhi aturan "tidak ada pemantauan umum" di PRD, jadi hanya aktif jika pemilik ruang menyalakannya. |
| Arsip otomatis    | Stream archive (fitur bawaan Zulip)                                                                                                                           |

⚠️ PRD saat ini menyebut "Pilot tidak melakukan pemantauan umum." Rangkuman ruang harus opt-in per ruang dan terlihat jelas di UI.

## 4. Sumber

- ClearFeed — Slack best practices 2026: https://clearfeed.ai/blogs/best-practices-to-organize-your-slack-for-maximum-productivity
- Social Intents — Slack channel organization 2026: https://www.socialintents.com/blog/slack-channel-organization-best-practices/
- Siit — Slack best practices: https://www.siit.io/blog/slack-best-practices
- Question Base — How to organize Slack channels: https://www.questionbase.com/resources/blog/how-to-organize-slack-channels-a-practical-system-that-actually-scales
- Slack — Advice for large teams: https://slack.com/blog/collaboration/advice-for-large-teams-on-slack
