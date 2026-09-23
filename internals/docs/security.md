# Security Grow Team

Status dokumen: penyelarasan sementara, 2026-09-22. Kontrol source, preflight, dan bukti deploy chat tidak menjadi sertifikasi keamanan atau kesiapan AI produksi.

## Bukti deploy chat bertanggal

- Aplikasi chat dipublikasi ke loopback melalui Cloudflare Tunnel.
- Engine Docker, volumes, systemd services, limits, dan backup dipisahkan dari Hermes.
- PostgreSQL 14, Redis, RabbitMQ, dan Memcached mendukung runtime chat Grow Team.
- Cloudflare Email Worker menolak request tanpa relay secret. Backend tidak mencatat isi email atau token.
- Endpoint profil tanpa autentikasi memberi 401 dalam pemeriksaan terdahulu.
- Backup memakai checksum. Restore database sementara diuji tanpa mengubah database aktif.
- Bukti deploy fork dan browser inti dicatat dalam [verifikasi branding](../../deploy/grow-team/BRANDING-VERIFICATION.md).

Bukti ini menetapkan konteks deploy chat. Bukti ini tidak membuktikan setiap ACL, reboot host, pemulihan penuh, atau rilis agent.

## Kontrol agent di source

- Record agent membawa realm. Validasi dan policy menolak referensi lintas realm dan memakai current access.
- Pairing perangkat tidak memberi authority tenant sebelum approval browser. Credential runner disimpan sebagai hash dan dapat dirotasi atau dicabut.
- Secret provider bersifat write-only dan terenkripsi. Runner menyimpan referensi secret lokal, bukan isi secret dalam registry atau descriptor.
- Grant memilih principal dan resource eksplisit. Administrator platform tidak otomatis mewarisi runner, provider, repository, atau profil milik owner lain.
- Scope conversation dan audience binding membatasi context. Scope setup tidak memberi hak membaca history pesan.
- Job, attempt, input, operation, approval, audit, artifact, verification, dan outbox menyimpan identity serta state durable.
- Artifact private mengikuti ACL saat ini. Publication berhenti jika audience berubah atau verification tidak sah.
- Task 0–6 untuk runner dan containment telah selesai dan lulus review komponen. Image `1c3ebcde3d7e` adalah intermediate image yang diuji, bukan bukti sertifikasi produk akhir.

## Batas authority dan effect

Trigger otomatis berasal dari mention personal yang sah atau DM antara satu anggota dan satu agent yang telah diotorisasi. DM grup memerlukan mention personal eksplisit. Default tim tidak menambah recipient, grant, atau trigger. Mode ACP dan endpoint memakai konfigurasi, policy, dan readiness sendiri. Sistem tidak melakukan fallback model atau switch mode otomatis.

Kontrak yang diterima mengharuskan enable eksplisit sesudah probe pada revision yang diuji. Source saat ini masih auto-enable profil siap. Task9 harus memperbaiki perilaku ini.

Operasi lokal dan remote memakai proposal, consumption, evidence, dan reconciliation. Push dan draft PR adalah effect terpisah. Approval read, edit, atau check tidak memberi authority push atau PR.

## Kontrak agent administrator (rilis 23)

[Spec agent administrator](spec/2026-09-23-agent-administrator.md) menambah alat tim yang server jalankan di Zulip. Kontrak authority-nya:

- Pemberi perintah harus ada di setting realm `can_command_administrator_agents_group`. Default setting adalah `role:administrators`.
- Pemilik profil `manage` harus administrator realm.
- Pemberi perintah yang bukan pemilik memerlukan grant profil dengan `team.manage`, serta grant runner dan provider.
- Server menjalankan setiap alat dengan `acting_user` = pemberi perintah. Server memakai pemeriksaan izin Zulip yang sama dengan view normal. Bot tidak menambah izin.
- Server memutuskan konfirmasi saat propose. Hanya pemberi perintah yang dapat menyetujui.
- Isi pesan dan repository tidak menambah izin. Katalog alat tertutup.

Kontrak ini belum menjadi bukti. Baris AD pada [bukti penerimaan](agent-acceptance.md) belum diuji.

## Gate keamanan dan rilis

- UI browser, provider nyata, dua mode runtime, dan smoke pilot belum disertifikasi.
- Image final, package dan notice final, migrasi live, dan activation realm/provider belum memiliki bukti rilis final.
- Backup/restore produksi, retained-key decryption, rollback kompatibel, capacity, dan host isolation pascarilis masih memerlukan evidence final.
- Recovery rehearsal terbatas tidak membuktikan reboot host, VPS kedua, atau disaster recovery penuh.
- Isolasi tenant komersial, lifecycle credential pelanggan, support, dan offboarding belum selesai.

Simpan rahasia di path privat. Jangan salin rahasia ke Git, descriptor, event, artifact, telemetry, atau dokumen produk. Aktifkan hanya realm, runner, provider, dan fixture yang disetujui setelah gate final selesai. Lihat [roadmap](roadmap.md), [FRD](frd.md), serta tiga spesifikasi agent: [connections and coding harness](spec/2026-09-21-agent-connections-and-coding-harness.md), [lifecycle and mention flow](spec/2026-09-21-agent-lifecycle-and-mention-flow.md), dan [settings, connections, and team defaults](spec/2026-09-22-agent-settings-connections-and-team-defaults.md).
