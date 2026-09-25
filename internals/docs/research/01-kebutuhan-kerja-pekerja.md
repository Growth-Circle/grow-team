# Riset: Kerjaan apa yang paling sering dikerjakan pekerja kantoran?

Status: riset, bukan keputusan.

**Tujuan:** memilih use case yang ditonjolkan di landing sanji.space. Tiga use case sudah ditentukan user (Presentasi, Riset, Laporan). Riset ini mencari use case lain yang paling berat dan paling sering.
**Tanggal:** 24 September 2026
**Status:** desk research (sumber sekunder), belum ada wawancara pengguna.

---

## 1. Temuan utama

| #   | Temuan                                                                                                                                                           | Sumber                                        |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| 1   | Sekitar 60% waktu knowledge worker habis untuk "work about work": mengejar update status, rapat yang tidak perlu, dan pindah-pindah aplikasi.                    | Asian Efficiency, rangkuman 2026 (data Asana) |
| 2   | Email memakan sekitar 28% minggu kerja, kira-kira 13 jam per minggu.                                                                                             | Asian Efficiency 2026                         |
| 3   | Komunikasi tertulis memakan sekitar 19 jam per minggu (Grammarly).                                                                                               | Clockify 2026                                 |
| 4   | Rata-rata 11,3 jam per minggu habis di rapat. 64% rapat rutin tidak punya agenda, dan edit PowerPoint naik 122% di 10 menit terakhir sebelum rapat.              | Flowtrace 2025, via Speakwise                 |
| 5   | Karyawan diinterupsi sekitar setiap 2 menit oleh rapat, email, dan notifikasi.                                                                                   | Microsoft Work Trend Index 2025               |
| 6   | Sekitar 70% knowledge worker menghabiskan 5 jam atau lebih per minggu untuk mencari informasi.                                                                   | IDC / KMWorld                                 |
| 7   | Pemakaian AI di kantor: 42% untuk riset dan ide, 32% untuk menyusun pesan, 26% untuk laporan dan proposal, 22% untuk editing dan merangkum dokumen atau notulen. | Pitt AI Hub                                   |
| 8   | Di organisasi riset, LLM paling sering dipakai untuk menulis teks terstruktur yang gampang dicek, seperti email dan laporan.                                     | arXiv 2501.16577                              |
| 9   | Indonesia: 94% knowledge worker sudah memakai AI dan 50% memakainya setiap hari. Ini angka tertinggi dari 14 negara (HP WRI 2025).                               | Bloomberg Technoz                             |
| 10  | Indonesia: 96% pengguna GenAI harian merasa lebih produktif (global: 92%).                                                                                       | PwC Hopes & Fears 2025                        |
| 11  | Pekerja Indonesia kemungkinan memakai AI untuk hal sederhana: meringkas email, mengetik email, dan bertanya.                                                     | Bloomberg Technoz                             |

## 2. Peta kerjaan → prioritas

Skor 1 sampai 3 untuk **frekuensi** (seberapa sering) × **sakitnya** (seberapa makan waktu dan bikin stres) × **cocok untuk multi-agen** (seberapa perlu kerja bareng orang + agen).

| Kerjaan                             | Frek | Sakit | Multi-agen | Total | Keputusan                            |
| ----------------------------------- | ---- | ----- | ---------- | ----- | ------------------------------------ |
| **Bikin Presentasi**                | 3    | 3     | 3          | 9     | ⭐ Utama                             |
| **Bikin Riset**                     | 3    | 3     | 3          | 9     | ⭐ Utama                             |
| **Bikin Laporan**                   | 3    | 3     | 3          | 9     | ⭐ Utama                             |
| Rangkum rapat → tugas               | 3    | 3     | 2          | 8     | Pendukung (paling kuat)              |
| Balas email & pesan                 | 3    | 3     | 1          | 7     | Pendukung                            |
| Olah data / spreadsheet             | 2    | 3     | 2          | 7     | Pendukung                            |
| Update status / progres mingguan    | 3    | 2     | 2          | 7     | Pendukung                            |
| Bikin konten & copy (sosmed, iklan) | 2    | 2     | 3          | 7     | Pendukung                            |
| Proposal & penawaran klien          | 2    | 3     | 2          | 7     | Pendukung (bisa digabung ke Laporan) |
| Kode & PR                           | 2    | 2     | 3          | 7     | Pendukung (segmen tim teknis)        |

## 3. Rekomendasi untuk landing

**Tonjolkan 3 use case utama** sebagai tab interaktif. Setiap tab menunjukkan bagaimana Kaki (perencana), Ayame (pembangun), dan Matcha (peninjau) membagi kerja, lalu manusia yang menyetujui:

1. **Bikin Presentasi:** brief → outline → slide → cek alur & angka → siap dipresentasikan.
   _Kenapa:_ rapat memakan sekitar 28% minggu kerja, dan slide sering dikebut di menit terakhir.
2. **Bikin Riset:** pertanyaan → cari sumber → rangkum temuan dengan kutipan → cek fakta.
   _Kenapa:_ mencari informasi memakan 5 jam atau lebih per minggu, dan riset adalah pemakaian AI nomor 1 di kantor (42%).
3. **Bikin Laporan:** data + catatan → draf → grafik → review → kirim.
   _Kenapa:_ laporan dan email termasuk teks terstruktur yang paling sering diserahkan ke AI.

4. **Notulen Rapat (integrasi Fathom):** rapat Google Meet / Zoom → Fathom merekam dan meringkas → Kaki mengubah action item jadi tugas lengkap dengan PIC dan deadline → Matcha mengingatkan item yang belum punya PIC.
   _Kenapa:_ rapat memakan sekitar 11 jam per minggu, dan 64% rapat rutin tidak punya agenda. Notulen yang langsung jadi tugas menutup celah "habis rapat, lupa siapa ngapain".
   _Teknis:_ Fathom otomatis ikut rapat Zoom / Google Meet / Teams. sanji menerima ringkasan dan action item lewat integrasi Fathom (API / webhook). Detail endpoint perlu divalidasi di dokumentasi developer Fathom sebelum dibangun.

**Use case pendukung** cukup tampil sebagai chip "…dan yang lain": Balas email, Olah data, Update mingguan, Konten sosmed, Proposal klien, Kode & PR.

**Pesan besarnya:** kurangi _work about work_. sanji.space menggabungkan chat, tugas, dan hasil kerja di satu ruang, jadi tim tidak perlu mengejar status atau pindah aplikasi.

## 4. Catatan & batasan

- Sebagian besar angka berasal dari survei global dan kawasan AS. Data Indonesia masih sebatas tingkat adopsi AI, belum sampai rincian jenis tugas.
- Langkah berikutnya: wawancara 5–8 pekerja Indonesia dari target segmen (agensi, UMKM digital, tim marketing, startup) untuk memvalidasi ranking di tabel §2.
- Angka dari situs rangkuman statistik (Asian Efficiency, Clockify, Speakwise) sebaiknya dicek ke laporan aslinya sebelum dipakai di materi publik.

## 5. Sumber

- Asian Efficiency — _100+ Productivity Statistics for 2026_: https://www.asianefficiency.com/productivity-statistics-2026/
- Clockify — _2026 Time Management Statistics_: https://clockify.me/time-management-statistics
- Speakwise — _Knowledge Worker Productivity Statistics 2026_: https://speakwiseapp.com/blog/knowledge-worker-productivity-statistics
- Pitt AI Hub — _How Employees are Actually Using Generative AI Tools_: https://www.aihub.pitt.edu/news/ai-revolution-work-how-employees-are-actually-using-generative-ai-tools
- arXiv 2501.16577 — _Generative AI Uses and Risks for Knowledge Workers in a Science Organization_
- LinkedIn (IDC/KMWorld) — _Time spent searching_: https://www.linkedin.com/pulse/time-spent-searching-chronology-myth-some-recent-research-white
- Bloomberg Technoz — _Survei: Lebih dari 90% Pekerja di Indonesia telah Gunakan AI_: https://www.bloombergtechnoz.com/detail-news/88593/survei-lebih-dari-90-pekerja-di-indonesia-telah-gunakan-ai
- PwC Indonesia — _Hopes and Fears 2025_: https://www.pwc.com/id/en/media-centre/press-release/2026/indonesian/hopes-and-fears-2025-indonesia.html
