# Bukti penerimaan platform agent Grow Team

Status: implementasi berlangsung. Ketiga spesifikasi tetap berada dalam `spec/`.

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

Admission pesan pada `9c0ea40` lulus 165 tes gabungan. Koreksi requester pada
`042b620` kemudian lulus 62 tes terkait dan review akhir. Tiga pemeriksaan
controller pada commit sebelum koreksi juga lulus. Lihat
[tes admission](../../zerver/tests/test_agent_message_admission.py) dan
[tes race pengiriman](../../zerver/tests/test_agent_message_admission_races.py).
Tiga kegagalan fixture Markdown yang sama direproduksi pada baseline; suite
Markdown lengkap belum dinyatakan lulus. Runner dan browser belum disertifikasi.

Integrasi backup pada commit `bafbc67` lulus 43 tes dan review independen.
Lihat [tes file backup](../../zerver/tests/test_agents_backup.py),
[tes perintah backup](../../zerver/tests/test_agents_backup_command.py), dan
[syarat pemulihan](agent-recovery.md). Tes memakai tar nyata dengan query database
yang dimock. Tooling recovery pada `7fb02b5` kemudian membuktikan dump/restore
nyata pada dua database terpisah. Sebanyak 997 identitas migrasi cocok; relasi,
checksum artifact, dan dekripsi kunci lama/baru lulus. Restore source rilis dan
transfer produksi masih menjadi gate terpisah.

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
| AT-11 | Mention di code block atau pesan dari bot | Tidak membuat job coding. | Sebagian: provenance renderer serta penolakan code/bot lulus pada `042b620`; integrasi runner belum diuji. |
| AT-12 | Trigger dikirim ulang | Hanya satu job logis terbentuk. | Sebagian: deduplikasi pesan, receipt, dan job termasuk race PostgreSQL lulus pada `042b620`; retry browser/runner belum diuji. |
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
| AT-35 | Pemulihan database dan artifact | Hubungan job, approval, result, dan checksum tetap konsisten. | Sebagian: tooling `7fb02b5` memverifikasi dump/restore sintetis, relasi data, checksum artifact, serta dekripsi kunci lama/baru; source rilis dan produksi belum diuji. |
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
| AF-07 | Mention personal biasa pada profil Coding | Satu pesan, receipt, job, outbox, dan attempt sesuai scope. | Sebagian: mention Coding lengkap membuat job dan wake atomik pada `042b620`; claim runner dan alur browser belum diuji. |
| AF-08 | Agent sama disebut berulang pada satu pesan | Hanya satu job untuk target itu. | Sebagian: target personal berulang menghasilkan satu job pada `042b620`; alur browser belum diuji. |
| AF-09 | Mention grup/wildcard yang mencakup agent | Tidak ada job; mention personal bersamaan tetap diproses. | Sebagian: grup/wildcard tidak menjadi trigger; mention personal terpisah tetap diterima pada `042b620`; browser belum diuji. |
| AF-10 | Mention pada inline code, fenced code, blockquote, atau silent mention | Tidak ada trigger dari reference tersebut. | Sebagian: inline/fenced code, quote, dan silent mention ditolak renderer pada `042b620`; browser belum diuji. |
| AF-11 | Bot atau agent lain menyebut agent | Tidak ada loop job baru. | Sebagian: pengirim bot tidak memicu job otomatis pada `042b620`; integrasi balasan runtime belum diuji. |
| AF-12 | Edit pesan menambahkan mention | Tidak ada job baru tanpa tindakan eksplisit. | Sebagian: edit pesan tidak memicu admission pada `042b620`; tindakan eksplisit di browser belum diuji. |
| AF-13 | Grant dicabut setelah preflight | Pesan sah boleh tersimpan; receipt rejected dan jumlah spawn nol. | Sebagian: pencabutan grant setelah preflight menolak receipt tanpa job/wake pada `042b620`; jumlah proses runner belum diuji. |
| AF-14 | Runner start lambat setelah pesan terkirim | Tugas tetap ditemukan dan dijalankan; composer tidak tertahan oleh start. | Belum diuji. |
| AF-15 | Runner offline kemudian online sebelum deadline | Job yang sama diklaim dengan recheck izin. | Sebagian: receipt offline/busy/unknown dan antrean durable lulus pada `042b620`; reconnect runner belum diuji. |
| AF-16 | Job belum mulai hingga deadline | Job blocked; tidak mulai diam-diam setelah deadline. | Sebagian: rekonsiliasi deadline durable lulus pada `8c59211`; polling daemon dan tampilan blocked belum diuji. |
| AF-17 | Dua tab/worker mengirim wake bersamaan | Paling banyak satu attempt aktif untuk job. | Sebagian: claim bersamaan melalui koneksi PostgreSQL terpisah lulus pada `8c59211`; wake dari runner nyata belum diuji. |
| AF-18 | Antrean penuh | Admission ditolak secara terlihat; job yang sudah diterima tetap utuh. | Sebagian: antrean penuh menghasilkan receipt rejected pada `042b620`; tampilan browser belum diuji. |
| AF-19 | Pindah A → B → A saat invite/upload/preflight tertahan | Intent lama tidak mengirim ke B atau menimpa draft A yang baru. | Belum diuji. |
| AF-20 | Pengguna mengedit lalu sengaja mengosongkan draft | Recovery terlambat tidak menghidupkan teks/attachment lama. | Belum diuji. |
| AF-21 | Respons kirim hilang, client mengulang key sama | Message ID dan receipts sama; tidak ada pesan/job tambahan. | Sebagian: retry key yang sama mempertahankan ID, termasuk setelah pesan dihapus, pada `042b620`; pemulihan browser belum diuji. |
| AF-22 | Key pengiriman sama dengan payload berbeda | Conflict; tidak mengubah pesan pertama. | Sebagian: race key yang sama dengan payload berbeda menolak conflict pada `042b620`; alur browser belum diuji. |
| AF-23 | Group DM tanpa mention versus DM satu agent | Hanya trigger yang didefinisikan pada bagian 6 diterima. | Sebagian: DM satu manusia/satu agent dan group DM mengikuti target personal pada `042b620`; alur browser belum diuji. |
| AF-24 | Dua job pada topik yang sama | Tombol follow-up menulis input ke job yang dipilih saja. | Sebagian: API follow-up memakai job eksplisit dan mention biasa membuat tugas baru pada `042b620`; tombol browser belum diuji. |
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

## AS: [2026-09-22-agent-settings-connections-and-team-defaults.md](spec/2026-09-22-agent-settings-connections-and-team-defaults.md)

| ID | Skenario | Hasil wajib | Status dan bukti |
| --- | --- | --- | --- |
| AS-01 | Anggota memakai agent yang dibagikan                            | Dapat membuat tugas tanpa pairing, instalasi lokal, atau profil baru. | Belum diuji. |
| AS-02 | Workstation dan server melalui pairing                          | Protokol sama; owner/realm terikat benar; kategori berasal dari deklarasi pemilik. | Belum diuji. |
| AS-03 | Browser berbeda, termasuk ponsel                                | Nama/lokasi runner tetap tepat; tidak ada tebakan “perangkat ini”. | Belum diuji. |
| AS-04 | Empat kombinasi lokasi runner dan model                         | Label membedakan eksekusi tools dari lokasi inferensi; localhost merujuk runner. | Belum diuji. |
| AS-05 | Kategori atau status belum diketahui                            | Unknown tetap terlihat; tidak berubah menjadi server atau offline berdasarkan fallback. | Belum diuji. |
| AS-06 | Katalog adapter runner A dan B berbeda                          | Form hanya menawarkan hasil runner terpilih; callback lama tidak mengganti hasil runner baru. | Belum diuji. |
| AS-07 | Adapter terpasang tetapi auth unknown/logout                    | UI meminta tindakan yang tepat; tidak mengumumkan code_ready. | Belum diuji. |
| AS-08 | Endpoint tanpa discovery model atau tools                       | Model manual dapat diprobe; kegagalan tools tidak disamarkan sebagai Coding siap. | Belum diuji. |
| AS-09 | Runner diubah saat form koneksi terbuka                         | Provider/repository yang tidak cocok dibatalkan; secret/path tidak berpindah otomatis. | Belum diuji. |
| AS-10 | Endpoint localhost/private                                      | Request probe berasal dari runner berizin; browser dan server web tidak melakukan probe ke host tersebut. | Belum diuji. |
| AS-11 | Create/retry profil setelah respons hilang                      | Satu identitas profil dan bot; status setup dapat ditemukan kembali. | Belum diuji. |
| AS-12 | Probe selesai setelah pengguna mengetik atau mengosongkan field | Nilai draft terbaru tetap utuh; tidak ada probe ulang per keystroke. | Belum diuji. |
| AS-13 | Save berlangsung lalu pengguna mengedit atau menutup form       | Hasil lama tidak menghapus draft baru; cancel sebelum submit tidak mengirim mutasi. | Belum diuji. |
| AS-14 | Draft lulus probe, belum enable                                 | Profil siap tetap draft dan tidak menerima tugas; enable revision yang tepat mengaktifkannya. | Belum diuji. |
| AS-15 | Hanya grant profil yang diberikan                               | UI tidak mengklaim akses lengkap; admission menolak resource lain yang belum diberikan. | Belum diuji. |
| AS-16 | Admin memilih profil privat atau profil realm lain              | Tidak menembus ACL; pilihan default tidak memberi grant tambahan. | Belum diuji. |
| AS-17 | Default tim dipakai dua anggota                                 | Identitas agent sama, job terpisah, izin kedua anggota diperiksa sendiri. | Belum diuji. |
| AS-18 | Anggota memilih profil lain                                     | Pilihan eksplisit menang atas default, recents, serta refresh status. | Belum diuji. |
| AS-19 | Anggota mengosongkan pemilih                                    | Callback resolver tidak mengisi ulang default pada draft yang sama. | Belum diuji. |
| AS-20 | Admin mengganti default A menjadi B saat draft A terbuka        | Draft dan job A tetap ke A; form baru memakai B jika sah. | Belum diuji. |
| AS-21 | Default offline atau kapasitas penuh                            | Tidak berpindah agent otomatis; antrean/rejection mengikuti admission existing. | Belum diuji. |
| AS-22 | Default paused, stale, revoked, atau tidak dapat diketahui      | Tidak dipilih untuk form baru; reason disesuaikan ACL dan tidak membocorkan ID tersembunyi. | Belum diuji. |
| AS-23 | Default Diskusi dipilih untuk permintaan Coding eksplisit       | Tidak menaikkan kemampuan atau grant; pengguna memilih agent yang sesuai. | Belum diuji. |
| AS-24 | Mention eksplisit, chat biasa, DM, dan pesan bot                | Default tim tidak menambah penerima atau trigger di luar aturan AT/AF. | Belum diuji. |
| AS-25 | Dua admin menyimpan expected revision yang sama                 | Satu perubahan menang; pihak lain mendapat conflict dan dapat membaca keadaan terbaru. | Belum diuji. |
| AS-26 | Izin dicabut antara resolve dan submit                          | Tidak ada spawn tanpa otoritas; profile ID kosong tidak diisi default server diam-diam. | Belum diuji. |
| AS-27 | Logout/realm switch dengan request tertunda                     | Data/cache/callback realm lama tidak muncul pada konteks baru. | Belum diuji. |
| AS-28 | Credential atau model profil berubah                            | Kesiapan lama tidak berlaku; secret tidak tersalin ke browser/anggota atau artifact. | Belum diuji. |
| AS-29 | Default diganti, label runner diedit, atau default dikosongkan  | Tidak ada restart proses, perubahan budget, atau pembatalan job sebagai efek samping. | Belum diuji. |
| AS-30 | Profil default diarsip atau pemilik dinonaktifkan               | Tidak ada pengambilalihan credential/ownership atau pengalihan job; pengguna mendapat jalur pemulihan yang sah. | Belum diuji. |
| AS-31 | Penggunaan dipindah dari laptop ke server                       | Profil/runner baru diprobe dan diberi grant; job serta bot lama tetap dapat ditelusuri. | Belum diuji. |
| AS-32 | Migrasi aditif, UI lintas browser, dan regresi chat             | Data existing memakai default null/lokasi unknown; query tidak per kartu; AT-33/AT-36 dan regresi chat tetap lulus. | Belum diuji. |

## Gate rilis tambahan

| Gate | Status dan bukti |
| --- | --- |
| Migrasi aditif dan feature flag mati | Sebagian: migrasi backend serta kontrol feature-off lulus pada `8c59211`; image rilis belum diuji. |
| Matriks ACL negatif, termasuk count dan artifact | Sebagian: backend dan race ACL lulus pada `8c59211`; matriks runtime/browser belum lengkap. |
| Tes regresi chat existing | Sebagian: pengiriman pesan dan service bot lulus pada `9c0ea40`; tiga kegagalan Markdown ada pada baseline; regresi UI rilis belum diuji. |
| Versi serta image runner dipatok | Belum diuji. |
| Backup database, artifact, dan key eksternal | Sebagian: komponen perintah lulus pada `bafbc67`; dump dan transfer produksi belum diuji. |
| Restore pada target terpisah | Sebagian: dua database baru dan 997 identitas migrasi cocok pada rehearsal tooling `7fb02b5`; ulangi pada source rilis bersih. |
| Rollback kompatibel tanpa kehilangan pesan | Belum diuji. |
| Kapasitas pilot dan telemetry tanpa secret | Belum diuji. |
| Smoke produksi untuk dua mode | Belum diuji. |
| Layanan Hermes tidak berubah | Baseline perlu dibandingkan setelah rilis. |
| SHA lokal, remote, dan runtime sesuai | Belum diuji. |
| Dokumen produk sesuai implementasi | Belum diuji. |

Jangan pindahkan spesifikasi ke `done/` sebelum semua kriteria yang berlaku dan
gate rilis lengkap. Adapter atau platform yang tidak didukung harus disebutkan
secara eksplisit; keterbatasan tersebut tidak menggantikan mode awal yang wajib.
