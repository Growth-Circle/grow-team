# Bukti penerimaan platform agent Grow Team

Status: implementasi berlangsung. Kedua spesifikasi tetap berada dalam `spec/`.

Dokumen ini mencatat hasil nyata untuk setiap kriteria penerimaan. Baris hanya
menjadi lulus setelah tes dan bukti yang disebutkan tersedia pada commit rilis.
Tes unit, fixture protokol, atau handshake saja tidak membuktikan alur produk.

Target awal: Linux, runner terpisah, adapter ACP terpilih, dan runtime endpoint
TypeScript. Rilis endpoint mendukung Chat Completions dan Responses. Sertifikasi
memerlukan satu agent terpasang dan satu endpoint nyata yang disetujui.

## Bukti awal

- Keputusan runtime: [perbandingan dan probe ACP](agent-runtime-decision.md).
- Rencana: [tahap implementasi](../../docs/superpowers/plans/2026-09-21-grow-team-agent-platform.md).
- Handshake ACP protocol 1 berhasil tanpa prompt dan credential pada probe awal.
- Handshake tersebut belum membuktikan prompt, coding, cancel, atau isolasi.
- Produksi masih memakai image `grow-team/server:12.2-grow-team.3` saat pekerjaan dimulai.
- Tidak ada fitur agent baru yang sudah dinyatakan tersedia pada produksi.

## Bukti komponen yang sudah direview

Backend koneksi, policy, dan API pada commit `3469f2f` lulus 96 tes gabungan
bersama model dan protokol. Review spesifikasi dan kualitas lulus. Bukti tersedia
pada [tes koneksi](../../zerver/tests/test_agents_connections.py),
[tes policy](../../zerver/tests/test_agents_policy.py), dan
[tes API](../../zerver/tests/test_agents_api.py).

Lifecycle pada commit `8c59211` lulus 137 tes gabungan, termasuk race dengan
koneksi PostgreSQL terpisah. Review spesifikasi dan kualitas lulus. Pemeriksaan
controller pada commit yang sama juga lulus untuk tiga kasus otoritas dan race.
Lihat [tes lifecycle](../../zerver/tests/test_agents_lifecycle.py) dan
[tes race PostgreSQL](../../zerver/tests/test_agents_lifecycle_races.py). Runtime
runner, browser, serta proses native belum disertifikasi dari hasil ini.

Integrasi backup pada commit `bafbc67` lulus 43 tes dan review independen.
Lihat [tes file backup](../../zerver/tests/test_agents_backup.py),
[tes perintah backup](../../zerver/tests/test_agents_backup_command.py), dan
[syarat pemulihan](agent-recovery.md). Tes memakai tar nyata dengan query database
yang dimock. Restore database lengkap masih menjadi gate terpisah.

Baris berstatus **Sebagian** di bawah mencatat bukti komponen tersebut. Status
tersebut tidak menyatakan fitur sudah tersedia di produksi atau spesifikasi selesai.

## Arti status

- **Belum diuji:** belum ada bukti untuk kriteria lengkap.
- **Sebagian:** bukti komponen tersedia; masih ada alur wajib yang belum lulus.
- **Lulus:** semua hasil wajib pada mode yang didukung telah diperiksa.
- **Gagal:** pemeriksaan menemukan pelanggaran kontrak dan memerlukan perbaikan.

## AT: [2026-09-21-agent-connections-and-coding-harness.md](spec/2026-09-21-agent-connections-and-coding-harness.md)

| ID | Skenario | Hasil wajib | Status dan bukti |
| --- | --- | --- | --- |
| AT-01 | Pairing normal | Perangkat terikat pengguna dan realm yang menyetujui. | Sebagian: pairing dan binding backend lulus pada `3469f2f`; alur CLI dan browser belum diuji. |
| AT-02 | Kode pairing expired, replay, atau brute force | Ditolak tanpa menerbitkan credential. | Sebagian: expiry, replay, dan batas percobaan backend lulus pada `3469f2f`; integrasi runner belum diuji. |
| AT-03 | Runner/token dicabut | Claim baru ditolak; lease dan eksekusi aktif masuk jalur penghentian. | Sebagian: rotasi, pencabutan credential, dan permintaan stop backend lulus pada `3469f2f`; penghentian proses nyata belum diuji. |
| AT-04 | Agent ACP terpilih | Handshake, prompt, progres, izin, dan cancel sesuai kemampuan yang dinegosiasikan. | Belum diuji. |
| AT-05 | ACP tanpa `loadSession` | Resume memakai sesi baru dan checkpoint; tidak memanggil metode yang tidak didukung. | Belum diuji. |
| AT-06 | Endpoint hanya Chat Completions | Probe tool round-trip dan coding fixture lulus melalui mode tersebut. | Belum diuji. |
| AT-07 | Endpoint hanya Responses | Probe dan coding lulus tanpa mengirim schema Chat Completions. | Belum diuji. |
| AT-08 | Endpoint teks tanpa tools | Siap chat; coding tidak diaktifkan. | Belum diuji. |
| AT-09 | Endpoint localhost/private | Request berasal dari runner yang dipilih, dengan policy jaringan yang sesuai. | Belum diuji. |
| AT-10 | JSON tool stream terpotong atau invalid | Tidak ada tool mutasi yang dijalankan. | Belum diuji. |
| AT-11 | Mention di code block atau pesan dari bot | Tidak membuat job coding. | Belum diuji. |
| AT-12 | Trigger dikirim ulang | Hanya satu job logis terbentuk. | Belum diuji. |
| AT-13 | Crash setelah commit sebelum queue publish | Outbox dipulihkan; job tidak hilang. | Sebagian: rekonsiliasi outbox tanpa notifikasi lulus pada `8c59211`; daemon dan restart nyata belum diuji. |
| AT-14 | Claim berhasil tetapi respons hilang | Runner menemukan lease yang sama; tidak ada attempt aktif kedua. | Sebagian: retry claim dan race PostgreSQL lulus pada `8c59211`; journal runner setelah respons hilang belum diuji. |
| AT-15 | Laptop offline ketika shell berjalan | UI benar, tool baru ditahan, process tree dihentikan sesuai deadline. | Belum diuji. |
| AT-16 | Event attempt lama setelah resume | Ditolak berdasarkan lease epoch. | Sebagian: penolakan event, context, dan artifact dengan epoch lama lulus pada `8c59211`; reconnect runner belum diuji. |
| AT-17 | Cancel saat menunggu approval | Approval tidak dipakai; request ACP diberi outcome yang sesuai. | Sebagian: race cancel dan konsumsi approval lulus pada `8c59211`; respons ACP belum diuji. |
| AT-18 | Process child membuat grandchild | Cancel/timeout menghentikan seluruh tree pada platform yang didukung. | Belum diuji. |
| AT-19 | Working tree pengguna sudah dirty | WIP tetap utuh; pekerjaan berjalan pada salinan terpisah dari commit terpilih. | Belum diuji. |
| AT-20 | Symlink atau common Git dir keluar scope | Akses tidak memperluas mount atau izin repository. | Belum diuji. |
| AT-21 | Member kehilangan akses kanal/repo | Pembacaan berikutnya dan publikasi ditolak sesuai scope baru. | Sebagian: pemeriksaan akses terkini pada context, attachment, dan publikasi lulus pada `8c59211`; integrasi runner/browser belum diuji. |
| AT-22 | Permintaan lintas realm | List, count, detail, event, approval, dan download tidak membocorkan data. | Sebagian: penolakan API job, event, input, approval, dan artifact lintas realm lulus pada `8c59211`; integrasi UI belum diuji. |
| AT-23 | Topik pindah dari privat ke audiens lebih luas | Hasil ditahan sampai tujuan dan audiens disetujui. | Sebagian: audience binding dan race perubahan akses lulus pada `8c59211`; alur review browser belum diuji. |
| AT-24 | Diff berubah setelah approval | Approval lama tidak dapat digunakan. | Sebagian: pemeriksaan hash, nonce, tree, expiry, dan grant approval lulus pada `8c59211`; broker Git nyata belum diuji. |
| AT-25 | Dua keputusan approval bersamaan | Tepat satu konsumsi operasi berhasil. | Sebagian: satu konsumsi durable dari dua konsumen PostgreSQL lulus pada `8c59211`; efek tool runner belum diuji. |
| AT-26 | Push/PR berhasil, respons terputus | Remote direkonsiliasi; efek tidak digandakan. | Belum diuji. |
| AT-27 | Model mengaku tes lulus tanpa proses tes | Completion verifier menolak completed. | Sebagian: penolakan completion tanpa operasi pemeriksaan yang sah lulus pada `8c59211`; proses pemeriksaan runner belum diuji. |
| AT-28 | Edit setelah tes | Bukti tes yang sudah tidak sesuai tree dinyatakan stale. | Sebagian: penolakan tree stale dan hasil gagal terbaru lulus pada `8c59211`; edit/check melalui runner belum diuji. |
| AT-29 | Konteks penuh, ringkasan gagal, atau provider error | Retry terbatas; tugas/checkpoint tetap dapat ditinjau. | Belum diuji. |
| AT-30 | Titen tidak tersedia | Pekerjaan yang aman dapat berjalan tanpa klaim recall berhasil. | Belum diuji. |
| AT-31 | Reply akhir diulang karena worker restart | Satu pesan hasil, dengan result message ID yang sama. | Sebagian: publikasi serta receipt atomik saat retry dan race lulus pada `8c59211`; restart worker nyata belum diuji. |
| AT-32 | Secret sintetis pada error/header/output | Tidak muncul pada chat, event, artifact yang dibagikan, atau telemetry. | Sebagian: enkripsi, field secret write-only, dan error backend lulus pada `3469f2f`; output runner, artifact, dan telemetry belum diuji. |
| AT-33 | UI dark/light, keyboard, layar sempit | Semua state utama dan approval dapat dioperasikan. | Belum diuji. |
| AT-34 | Upgrade/rollback runner | Versi tidak kompatibel ditolak dengan pesan pemulihan; job lama tetap terbaca. | Belum diuji. |
| AT-35 | Pemulihan database dan artifact | Hubungan job, approval, result, dan checksum tetap konsisten. | Sebagian: arsip file, referensi artifact, dan retained keys lulus pada `bafbc67`; restore database dan dekripsi hasil restore belum diuji. |
| AT-36 | Anggota memakai browser tanpa aplikasi desktop | Login, chat, pengaturan, trigger, review, approval, cancel, dan resume bekerja; job tetap berjalan setelah tab ditutup. | Belum diuji. |

## AF: [2026-09-21-agent-lifecycle-and-mention-flow.md](spec/2026-09-21-agent-lifecycle-and-mention-flow.md)

| ID | Skenario | Hasil wajib | Status dan bukti |
| --- | --- | --- | --- |
| AF-01 | Create diklik dua kali atau respons hilang | Satu bot user, satu profil, dan setup operation yang dapat ditemukan kembali. | Sebagian: retry HTTP dan race create menghasilkan satu identitas pada `3469f2f`; alur browser belum diuji. |
| AF-02 | Profil tersimpan tetapi probe gagal | Profil tetap ada; retry tidak membuat identitas baru. | Sebagian: retry setup mempertahankan profil pada `3469f2f`; probe melalui runner nyata belum diuji. |
| AF-03 | Kanal gagal ditambahkan setelah create | UI membedakan profil berhasil dari akses kanal gagal. | Sebagian: grant dan retry penambahan kanal lulus pada `3469f2f`; pemulihan UI belum diuji. |
| AF-04 | Profil diedit selama probe berlangsung | Hasil revision lama tidak mengaktifkan revision baru. | Sebagian: revision dan descriptor probe lama ditolak pada `3469f2f`; alur edit di browser belum diuji. |
| AF-05 | Dua profil bernama sama | ID penerima yang dipilih tetap tepat setelah rename dan upload. | Belum diuji. |
| AF-06 | Presence/query gagal dengan cache Online lama | UI menampilkan unknown tanpa membuat proses duplikat. | Belum diuji. |
| AF-07 | Mention personal biasa pada profil Coding | Satu pesan, receipt, job, outbox, dan attempt sesuai scope. | Belum diuji. |
| AF-08 | Agent sama disebut berulang pada satu pesan | Hanya satu job untuk target itu. | Belum diuji. |
| AF-09 | Mention grup/wildcard yang mencakup agent | Tidak ada job; mention personal bersamaan tetap diproses. | Belum diuji. |
| AF-10 | Mention pada inline code, fenced code, blockquote, atau silent mention | Tidak ada trigger dari reference tersebut. | Belum diuji. |
| AF-11 | Bot atau agent lain menyebut agent | Tidak ada loop job baru. | Belum diuji. |
| AF-12 | Edit pesan menambahkan mention | Tidak ada job baru tanpa tindakan eksplisit. | Belum diuji. |
| AF-13 | Grant dicabut setelah preflight | Pesan sah boleh tersimpan; receipt rejected dan jumlah spawn nol. | Belum diuji. |
| AF-14 | Runner start lambat setelah pesan terkirim | Tugas tetap ditemukan dan dijalankan; composer tidak tertahan oleh start. | Belum diuji. |
| AF-15 | Runner offline kemudian online sebelum deadline | Job yang sama diklaim dengan recheck izin. | Belum diuji. |
| AF-16 | Job belum mulai hingga deadline | Job blocked; tidak mulai diam-diam setelah deadline. | Sebagian: rekonsiliasi deadline durable lulus pada `8c59211`; polling daemon dan tampilan blocked belum diuji. |
| AF-17 | Dua tab/worker mengirim wake bersamaan | Paling banyak satu attempt aktif untuk job. | Sebagian: claim bersamaan melalui koneksi PostgreSQL terpisah lulus pada `8c59211`; wake dari runner nyata belum diuji. |
| AF-18 | Antrean penuh | Admission ditolak secara terlihat; job yang sudah diterima tetap utuh. | Belum diuji. |
| AF-19 | Pindah A → B → A saat invite/upload/preflight tertahan | Intent lama tidak mengirim ke B atau menimpa draft A yang baru. | Belum diuji. |
| AF-20 | Pengguna mengedit lalu sengaja mengosongkan draft | Recovery terlambat tidak menghidupkan teks/attachment lama. | Belum diuji. |
| AF-21 | Respons kirim hilang, client mengulang key sama | Message ID dan receipts sama; tidak ada pesan/job tambahan. | Belum diuji. |
| AF-22 | Key pengiriman sama dengan payload berbeda | Conflict; tidak mengubah pesan pertama. | Belum diuji. |
| AF-23 | Group DM tanpa mention versus DM satu agent | Hanya trigger yang didefinisikan pada bagian 6 diterima. | Belum diuji. |
| AF-24 | Dua job pada topik yang sama | Tombol follow-up menulis input ke job yang dipilih saja. | Belum diuji. |
| AF-25 | Input datang saat job berjalan | Input durable dan terlihat pending; diterapkan pada batas turn yang sah. | Sebagian: input terurut dan receipt durable lulus pada `8c59211`; penerapan pada turn runtime dan UI belum diuji. |
| AF-26 | Ack input hilang atau runtime restart | Input tidak ditandai delivered tanpa bukti; recovery tidak menggandakan efek tool. | Sebagian: rekonsiliasi eksplisit untuk input uncertain lulus pada `8c59211`; restart journal runner belum diuji. |
| AF-27 | Mode Diskusi mendapat instruksi mengedit/push | Tools mutasi tidak tersedia; pengguna diarahkan membuat tugas coding. | Sebagian: penolakan backend terhadap mutasi pada mode answer lulus pada `8c59211`; pembatasan tools runtime belum diuji. |
| AF-28 | Native agent menyelesaikan turn tanpa hasil valid | Job tidak completed; UI memberikan sebab dan tindakan lanjut. | Sebagian: penolakan completion yang hanya berdasarkan event model lulus pada `8c59211`; turn native dan pesan UI belum diuji. |
| AF-29 | Runner gagal sebelum dapat membalas | UI/sistem melaporkan start failure dari record, tanpa membutuhkan output model. | Belum diuji. |
| AF-30 | Cancel saat permission request menunggu | Approval tidak dapat dipakai; proses berhenti atau tampil belum terkonfirmasi. | Sebagian: otoritas approval saat cancel dan syarat stop lulus pada `8c59211`; process tree, ACP, dan UI belum diuji. |
| AF-31 | Cancel bersamaan dengan completion | Transisi memakai versi; hanya hasil sah yang menang dan tersimpan. | Sebagian: satu pemenang terminal pada race cancel dan publikasi lulus pada `8c59211`; integrasi proses nyata belum diuji. |
| AF-32 | Resume setelah operasi remote tidak pasti | Rekonsiliasi dilakukan sebelum retry; approval lama tidak diaktifkan ulang. | Sebagian: penahanan resume untuk outcome remote yang belum pasti lulus pada `8c59211`; rekonsiliasi provider Git nyata belum diuji. |
| AF-33 | Profil pause saat ada tugas aktif | Kerja baru tertahan; UI tidak mengklaim tugas aktif otomatis berhenti. | Sebagian: pause memerlukan revision saat ini pada `3469f2f`; perilaku job aktif dan status UI belum diuji. |
| AF-34 | Profil arsip/rename dan pesan lama dibuka | Identitas historis tetap tepat; tidak dialihkan ke agent lain. | Belum diuji. |
| AF-35 | Pindah/ubah audiens ketika hasil akan terbit | Result broker menahan publikasi yang memperluas akses. | Sebagian: serialisasi perubahan source, membership, dan audiens dengan publikasi lulus pada `8c59211`; UI pemulihan belum diuji. |
| AF-36 | Tab ditutup lalu dibuka dari browser lain | Job tetap berjalan; status, inputs, approval, dan hasil dipulihkan dari server. | Belum diuji. |
| AF-37 | Provider/mode runtime berbeda untuk tugas identik | Kedua mode melewati admission, policy, verifier, dan publisher yang sama. | Belum diuji. |
| AF-38 | JSON tool terpotong atau context overflow | Tidak ada mutasi parsial; input aktif tetap ada dan recovery dibatasi. | Belum diuji. |

## Gate rilis tambahan

| Gate | Status dan bukti |
| --- | --- |
| Migrasi aditif dan feature flag mati | Sebagian: migrasi backend serta kontrol feature-off lulus pada `8c59211`; image rilis belum diuji. |
| Matriks ACL negatif, termasuk count dan artifact | Sebagian: backend dan race ACL lulus pada `8c59211`; matriks runtime/browser belum lengkap. |
| Tes regresi chat existing | Belum diuji. |
| Versi serta image runner dipatok | Belum diuji. |
| Backup database, artifact, dan key eksternal | Sebagian: komponen perintah lulus pada `bafbc67`; dump dan transfer produksi belum diuji. |
| Restore pada target terpisah | Belum diuji. |
| Rollback kompatibel tanpa kehilangan pesan | Belum diuji. |
| Kapasitas pilot dan telemetry tanpa secret | Belum diuji. |
| Smoke produksi untuk dua mode | Belum diuji. |
| Layanan Hermes tidak berubah | Baseline perlu dibandingkan setelah rilis. |
| SHA lokal, remote, dan runtime sesuai | Belum diuji. |
| Dokumen produk sesuai implementasi | Belum diuji. |

Jangan pindahkan spesifikasi ke `done/` sebelum semua kriteria yang berlaku dan
gate rilis lengkap. Adapter atau platform yang tidak didukung harus disebutkan
secara eksplisit; keterbatasan tersebut tidak menggantikan mode awal yang wajib.
