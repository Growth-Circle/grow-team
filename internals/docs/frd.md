# FRD Grow Team

Status: FR-01, FR-02, FR-04, FR-05, FR-06, dan FR-07 memiliki smoke teramati.
FR-03 adalah fitur upstream tersedia, tetapi ACL/DM/search penuh belum diuji.
FR-20 memiliki pemeriksaan publik, browser inti, dan identitas produk yang lulus.
FR-08–FR-19 adalah target.

| ID | Kebutuhan | Kriteria penerimaan |
| --- | --- | --- |
| FR-01 | Akses browser terautentikasi | Pengguna sah login; halaman profil tanpa autentikasi memberi 401. |
| FR-02 | Kanal dan topik | Anggota dapat mengirim pesan ke kanal dan topik; event realtime memuat pesan sama. |
| FR-03 | Pesan langsung dan pencarian | Fitur tersedia; pilot wajib membuktikan pengguna tanpa hak tidak dapat membaca atau menemukan pesan privat. |
| FR-04 | Berkas | Upload lalu download menjaga isi; akses mengikuti hak pesan/realm. |
| FR-05 | Administrasi | Admin dapat mengundang anggota dengan role dan masa berlaku terbatas. |
| FR-06 | Email transaksi | Reset password dan notifikasi dapat dikirim melalui Worker terlindungi; request tanpa rahasia ditolak. |
| FR-07 | Pemulihan | Backup ber-checksum dapat diuji ke target sementara tanpa mengubah database aktif. |
| FR-20 | Rilis branding runtime | Image fork Grow Team berjalan di `team.growc.id`. Login memuat “Log in to Grow Team”. Logo, favicon, footer, bantuan, dan indikator pemuatan memakai Grow Team. Pemeriksaan publik dan browser inti lulus; lihat laporan verifikasi. |
| FR-08 | Monitor kanal | Target: admin mengonfigurasi kanal; agent hanya memantau kanal itu dan menulis audit setiap trigger. |
| FR-09 | Mention dan tugas manual | Target: mention bot atau perintah membuat satu job dengan origin realm/kanal/topik/pesan. |
| FR-10 | Katalog model | Target: worker hanya memilih model dari allowlist gateway Wulan; fallback tercatat. |
| FR-11 | Job tahan restart | Target: job menyimpan state, idempotency key, attempt, dan hasil; restart tidak menggandakan aksi. |
| FR-12 | Jalur panjang | Target: streaming/long-running job memakai jalur privat langsung, bukan request Cloudflare yang dibatasi 120 detik. |
| FR-13 | Batas eksekusi | Target: setiap job memiliki timeout, token/biaya limit, retry terbatas, dan alasan gagal. |
| FR-14 | Cancel dan resume | Target: peminta/admin dapat cancel; resume membuat attempt baru dengan jejak ke job asal. |
| FR-15 | Konteks sesuai izin | Target: worker cek hak akses saat enqueue dan sebelum baca setiap reference. |
| FR-16 | Approval manusia | Target: aksi eksternal/data penting berhenti pada pending approval; keputusan dan aktor dicatat. |
| FR-17 | Audit dan hasil | Target: event lifecycle dicatat; hasil dipost ke topik asal atau ditahan jika akses berubah. |
| FR-18 | Runner terisolasi | Target: kerja opsional memakai container ephemeral dengan limit; aplikasi realtime tidak dihentikan/dibiarkan tidur. |
| FR-19 | Tenant boundary | Target B2B: setiap job, audit, bot, dan reference memiliki realm/tenant boundary; tes lintas tenant gagal tertutup. |

FR-08–FR-19 tidak membuktikan adanya agent saat ini. FR-20 mencakup deployment dan pemeriksaan yang dicatat dalam [laporan verifikasi](../../deploy/grow-team/BRANDING-VERIFICATION.md). Implementasi AI memakai sidecar/API dan record durable, bukan asumsi Celery. Detail entitas target ada di [ERD](erd.md); gate ada di [roadmap](roadmap.md).
