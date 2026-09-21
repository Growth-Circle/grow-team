# Identitas produk Grow Team

Permintaan: ganti seluruh branding produk Zulip dengan Grow Team, lalu commit dan push ke `Growth-Circle/grow-team`.

## Batas dan kriteria selesai

- Nama, judul, logo, ikon, onboarding, email, bot bawaan, halaman bantuan, dan terjemahan menggunakan Grow Team.
- Tautan produk mengarah ke aplikasi, bantuan lokal, atau repositori Grow Team yang tersedia.
- Identitas baru berupa simbol lingkaran dan tiga anggota dengan wordmark Grow Team.
- Pertahankan lisensi, nama pemegang hak cipta, kredit upstream, dan identitas protokol yang diperlukan untuk kompatibilitas.
- Jangan mengganti identifier Python, jalur impor, nama paket, migrasi historis, atau identifier API secara massal.
- Jangan mengubah pesan buatan pengguna atau layanan HermesTrading.
- Terapkan image fork ke `team.growc.id` setelah backup dan pemeriksaan build.
- Pertahankan image lama dan konfigurasi pemulihan.
- Catat sisa rujukan upstream yang disengaja dan hasil pengujian.

## Pembagian pekerjaan

### 1. Aplikasi browser

- [x] Audit dan ubah salinan UI di `web/src` dan `web/templates`.
- [x] Ganti onboarding video upstream dengan panduan awal berbasis teks dan bantuan lokal.
- [x] Perbarui menu, halaman tentang, notifikasi, bantuan, dan tautan produk.
- [x] Pertahankan kontrak API serta kredit hukum.

### 2. Halaman server, email, dan konten bawaan

- [x] Ubah branding dalam `templates` dan konten yang dibuat `zerver`.
- [x] Ganti logo inline serta judul login, undangan, registrasi, dan reset kata sandi.
- [x] Ubah bot dan pesan onboarding baru tanpa mengubah pesan pengguna.
- [x] Perbarui pengujian perilaku yang memakai nama produk.

### 3. Aset, bantuan, dan bahasa

- [x] Buat aset SVG Grow Team dan PNG turunannya, termasuk favicon dan ikon email.
- [x] Sesuaikan bantuan yang disajikan dan identitas repositori.
- [x] Sinkronkan katalog terjemahan setelah perubahan teks selesai.
- [x] Audit seluruh referensi dan klasifikasikan pengecualian.

### 4. Verifikasi dan integrasi

- [x] Jalankan pemeriksaan format, kompilasi frontend, dan pemeriksaan katalog/template yang relevan.
- [x] Tinjau aset hasil render serta halaman representatif.
- [x] Tinjau perubahan lintas bagian dan perbaiki temuan.
- [x] Catat batas verifikasi dan cara memasukkan aset baru ke image produksi.

### 5. Dokumentasi produk dan arsitektur

Tambahan permintaan pengguna pada sesi yang sama.

- [x] Buat delapan dokumen dalam `internals/docs/`: blueprint, techstack, PRD, BRD, FRD, ERD, roadmap, dan security.
- [x] Selaraskan dengan tim awal 5–6 orang, aplikasi web, agen AI, dan rencana penjualan ke klien.
- [x] Pisahkan kondisi saat ini, usulan arsitektur, dan keputusan yang masih terbuka.

### 6. Penyerahan

- [x] Buat backup baru dan catat kondisi layanan Hermes sebelum deployment.
- [x] Bangun image fork dengan source, aset produksi, bantuan, dan katalog bahasa baru.
- [x] Ganti hanya container aplikasi, lalu periksa halaman publik dan sesi anggota.
- [x] Pastikan konfigurasi layanan Hermes tetap sama dengan baseline.
- [x] Commit dengan trailer CADIS.
- [ ] Gabungkan secara fast-forward ke `grow-team` dan push.
- [ ] Verifikasi SHA lokal sama dengan remote serta working tree bersih.

## Antarmuka bersama

Frontend dan template tetap memakai jalur aset lama. Aset pada jalur tersebut diganti dengan identitas baru.
Nama internal seperti `zulip_version` tetap digunakan oleh server dan klien.
Katalog bahasa diperbarui setelah teks sumber selesai agar kunci terjemahan tetap cocok.
