# FRD Grow Team

Status dokumen: penyelarasan sementara, 2026-09-22. Status menunjukkan bukti yang tersedia atau pekerjaan yang masih diperlukan. Status tidak menggantikan acceptance evidence rilis.

| ID | Kebutuhan | Kriteria penerimaan stabil | Status |
| --- | --- | --- | --- |
| FR-01 | Akses browser terautentikasi | Pengguna sah login. Halaman profil tanpa autentikasi memberi 401. | Bukti deploy chat bertanggal tersedia. |
| FR-02 | Kanal dan topik | Anggota dapat mengirim pesan ke kanal dan topik. Event realtime memuat pesan yang sama. | Bukti browser inti bertanggal tersedia. |
| FR-03 | Pesan langsung dan pencarian | Fitur tersedia. Pilot membuktikan pengguna tanpa hak tidak dapat membaca atau menemukan pesan privat. | Fitur upstream tersedia. Bukti ACL, DM, dan search negatif penuh masih diperlukan. |
| FR-04 | Berkas | Upload lalu download menjaga isi. Akses mengikuti hak pesan atau realm. | Bukti upload-download perlu dipelihara per rilis. |
| FR-05 | Administrasi | Admin dapat mengundang anggota dengan role dan masa berlaku terbatas. | Bukti deploy chat bertanggal tersedia. |
| FR-06 | Email transaksi | Reset password dan notifikasi dapat dikirim melalui Worker terlindungi. Request tanpa rahasia ditolak. | Bukti deploy email bertanggal tersedia. |
| FR-07 | Pemulihan | Backup ber-checksum dapat diuji ke target sementara tanpa mengubah database aktif. | Bukti terbatas tersedia. Pemulihan stack penuh tetap gate rilis. |
| FR-08 | Trigger agent | Mention personal, DM satu anggota dengan satu agent yang diotorisasi, dan tindakan manual membuat receipt durable. DM grup memerlukan mention personal eksplisit. Mention grup, wildcard, pesan bot, edit, dan pemantauan kanal umum tidak memicu job. | Ada di source. UI dan activation belum dirilis. |
| FR-09 | Tugas dan provenance | Job menyimpan requester, source, profil, runner, repository, trigger, scope, dan idempotency. | Ada di source. Browser flow belum dirilis. |
| FR-10 | Mode dan provider | ACP dan endpoint memakai kontrak berbeda. Provider dipilih eksplisit dari konfigurasi yang diuji. | Ada di source. Jangan gunakan fallback atau switch mode otomatis. |
| FR-11 | Lifecycle tahan restart | Job menyimpan state, idempotency key, attempt, event, input, checkpoint, dan hasil. Restart tidak menggandakan effect. | Ada di source. Bukti runtime nyata masih gate rilis. |
| FR-12 | Jalur runner | Runner memakai koneksi keluar terikat origin. | Ada di source. Transport provider nyata belum disertifikasi. |
| FR-13 | Batas eksekusi | Setiap job memiliki policy, budget, lease, deadline, limit, dan alasan gagal. | Ada di source. Pengukuran biaya dan capacity produksi belum lengkap. |
| FR-14 | Cancel dan resume | Pembatalan dan resume memakai state durable serta attempt baru. Authority owner dan grant tetap berlaku. | Ada di source. Administrator tanpa grant tidak mendapat bypass. |
| FR-15 | Konteks sesuai izin | Server memeriksa akses saat enqueue dan sebelum memakai reference. Scope setup tidak memberi hak membaca history pesan. | Ada di source. |
| FR-16 | Approval manusia | Effect diproposalkan, dikonsumsi sekali, dan direkonsiliasi. Push dan draft PR adalah action terpisah. | Ada di source. Bukti effect end-to-end masih diperlukan. |
| FR-17 | Audit dan hasil | Lifecycle dicatat. Hasil dipost hanya jika audience dan verification tetap sah. | Ada di source. Audit tidak dinyatakan administrator-proof. |
| FR-18 | Runner terisolasi | Kerja opsional memakai containment rootless dan limit. Aplikasi realtime tidak dihentikan atau dibiarkan tidur. | Task 0–6 selesai dan lulus review komponen. Image `1c3ebcde3d7e` diuji sebagai intermediate image. Sertifikasi runtime produk masih pending. |
| FR-19 | Tenant boundary | Setiap job, audit, bot, dan reference memiliki realm boundary. Tes lintas tenant gagal tertutup. | Model dan policy ada di source. Operasi B2B belum siap. |
| FR-20 | Branding runtime | Image fork Grow Team berjalan di `team.growc.id`. Login, logo, favicon, footer, bantuan, dan indikator pemuatan memakai identitas Grow Team. | Bukti publik dan browser inti bertanggal ada dalam [laporan verifikasi branding](../../deploy/grow-team/BRANDING-VERIFICATION.md). |

## Kontrak settings dan default

AS-01–AS-32 dari [spesifikasi settings](spec/2026-09-22-agent-settings-connections-and-team-defaults.md) masih pending Task9. Kontrak ini mencakup metadata runner, directory ACL, readiness draft, enable eksplisit, default tim revision-safe, dan UI lintas browser.

Kontrak yang diterima menyatakan probe hanya merekam readiness. Enable adalah aksi eksplisit pada revision yang diuji. Source saat ini masih auto-enable profil setelah probe siap. Dokumentasi ini tidak menyatakan koreksi Task9 sudah diterapkan.

Default tim tidak memberi grant, tidak mengubah job aktif, dan tidak membuat trigger baru. Gunakan [ERD](erd.md) untuk record aktual, [roadmap](roadmap.md) untuk gate rilis, serta spesifikasi [connections and coding harness](spec/2026-09-21-agent-connections-and-coding-harness.md) dan [lifecycle and mention flow](spec/2026-09-21-agent-lifecycle-and-mention-flow.md) untuk kontrak runner dan lifecycle.
