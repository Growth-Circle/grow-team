# PRD Grow Team

Status produk: internal, web-first. Target pertama adalah tim lima sampai enam anggota. Produk dapat dipertimbangkan untuk klien setelah pilot internal dan gate operasi selesai.

## Masalah dan sasaran

Tim membutuhkan percakapan kerja yang dapat dicari menurut kanal dan topik. Grow Team mempertahankan chat Zulip dan menambahkan kemampuan agent secara terkendali. Anggota memakai browser untuk chat, membuat tugas, melihat status, memberi input, dan meninjau approval. Runner dapat berada pada laptop atau server milik owner.

## Persona

- **Anggota:** memakai chat dan membuat tugas jika memiliki grant yang berlaku.
- **Owner runner:** memasangkan perangkat dan mendaftarkan workspace, provider, serta katalog yang disetujui.
- **Administrator realm:** mengelola kebijakan realm. Role ini tidak memberi authority runner atau credential owner lain.
- **Operator platform:** menjaga deploy, backup, recovery, dan isolasi host.

## Cakupan chat dan bukti sebelumnya

Chat, kanal, topik, DM, pencarian, unggah, peran, undangan, reset password, dan email transaksi berasal dari fork Zulip. Bukti deploy bertanggal mencakup `team.growc.id`, halaman publik, sesi admin, tampilan pesan, event queue, dan identitas Grow Team. Lihat [laporan verifikasi branding](../../deploy/grow-team/BRANDING-VERIFICATION.md). Bukti ini mendukung status chat. Bukti ini tidak menyatakan semua ACL, recovery penuh, atau rilis agent sudah selesai.

## Perilaku target pilot agent

Pilot memiliki dua jalur: jalur cepat (Anthropic SDK, answer dan manage) dan jalur code (container, ACP), tanpa fallback antarjalur. Tugas memasuki lifecycle durable dan melewati current access check. Operasi penting memerlukan approval yang terikat pada attempt, policy, argumen, dan tree. Hasil hanya diterbitkan setelah gate audience dan verification.

Pilot tidak melakukan pemantauan umum. Trigger otomatis berasal dari mention personal yang sah atau DM antara satu anggota dan satu agent yang telah diotorisasi. DM grup memerlukan mention personal eksplisit. Tugas manual memilih profil secara eksplisit; input susulan memilih job yang dituju. Mention grup, wildcard, pesan bot, edit pesan, dan default tim tidak menambah trigger.

## Status produk

| Produk | Status | Batas |
| --- | --- | --- |
| Workspace chat internal | Bukti deploy bertanggal tersedia | Bukti ulang diperlukan untuk perubahan rilis. |
| Control plane dan runner agent | Komponen source tersedia | Task 0–6 selesai dan lulus review komponen. |
| Image runner | Intermediate image `1c3ebcde3d7e` diuji | Image rilis final belum ditetapkan. |
| Enable profil | Kontrak explicit enable diterima | Source masih auto-enable setelah probe. Task9 harus memperbaikinya. |
| UI agent, provider pilot, dan activation | Pending | UI belum dikirim. Realm dan provider pilot belum diaktifkan. |
| Kesiapan rilis | Pending | Migrasi live, 106 acceptance rows, recovery, kapasitas, dan sertifikasi keamanan belum lengkap. |

## Metrik provisional

Metrik belum menjadi KPI komersial. Untuk pilot, ukur keberhasilan login dan undangan, pengiriman pesan realtime, pencarian, upload-download, delivery email, keberhasilan backup, dan jumlah insiden akses. Tetapkan baseline dan target setelah penggunaan internal stabil.

## Ruang lingkup komersial

Billing, SLA, desktop/mobile app, dan operasi multi-klien belum masuk rilis internal. Isolasi pelanggan, lifecycle credential, support, dan offboarding memerlukan keputusan bisnis dan bukti operasi.

## Kebutuhan terkait

| Product requirement | Business requirement | Functional requirement |
| --- | --- | --- |
| PR-01 Workspace internal | BR-01 validasi workspace internal dahulu | FR-01–FR-07 dan FR-20 |
| PR-02 Pilot agent internal | BR-02 validasi agent dengan authority eksplisit | FR-08–FR-18 |
| PR-03 Kesiapan B2B | BR-03 komersialisasi setelah gate | FR-19 |

Lihat [BRD](brd.md), [FRD](frd.md), [roadmap](roadmap.md), [security](security.md), dan tiga spesifikasi agent: [connections and coding harness](spec/2026-09-21-agent-connections-and-coding-harness.md), [lifecycle and mention flow](spec/2026-09-21-agent-lifecycle-and-mention-flow.md), serta [settings, connections, and team defaults](spec/2026-09-22-agent-settings-connections-and-team-defaults.md).
