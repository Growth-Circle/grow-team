# Roadmap Grow Team

Tidak ada tanggal komitmen dalam roadmap ini. Status membedakan bukti deploy chat, komponen source, dan gate rilis produk.

1. **Workspace internal chat — bukti deploy bertanggal tersedia.** Fork Grow Team berjalan pada `team.growc.id`. Bukti branding mencakup halaman publik, sesi admin, browser inti, dan identitas produk. Pertahankan bukti ini untuk chat. Ulangi bukti bila rilis berubah. Gate tersisa mencakup ACL penuh, capacity, dan restore stack penuh.
2. **Control plane dan runner agent — komponen selesai direview.** Task 0–6 telah selesai dan lulus review komponen. Model durable, policy, lifecycle, mention admission, runner, dan containment tersedia. Image `1c3ebcde3d7e` diuji sebagai intermediate image.
3. **Kontrak settings dan browser — pending Task9–Task10.** Tambahkan settings dan default yang aditif. Probe harus merekam readiness tanpa auto-enable. Enable eksplisit pada revision yang diuji adalah kontrak yang masih perlu dikoreksi pada source. Kirim UI pairing, provider, profil, grant, default tim, job panel, dan aksesibilitas.
4. **Release agent — gate pending.** Tetapkan image final, paket dan notice, provider nyata, migrasi additive dengan feature flag mati, smoke dua mode, Git/approval, recovery, rollback, capacity, host isolation, dan acceptance matrix.
5. **Kesiapan B2B — belum diputuskan.** Pilih isolasi pelanggan. Buktikan provisioning, backup/restore per pelanggan, offboarding, observability, support, dan review keamanan.
6. **Komersialisasi — sesudah gate.** Tentukan penawaran dan operasi hanya setelah gate sebelumnya selesai.

Jangan menyatakan migrasi live, UI agent, readiness AI produksi, sertifikasi keamanan, atau seluruh 106 baris acceptance telah selesai sebelum evidence final tersedia. Lihat [blueprint](blueprint.md), [BRD](brd.md), [FRD](frd.md), [security](security.md), dan tiga spesifikasi agent: [connections and coding harness](spec/2026-09-21-agent-connections-and-coding-harness.md), [lifecycle and mention flow](spec/2026-09-21-agent-lifecycle-and-mention-flow.md), serta [settings, connections, and team defaults](spec/2026-09-22-agent-settings-connections-and-team-defaults.md).
