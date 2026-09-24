# Tech Stack Grow Team

Status dokumen: penyelarasan sementara dari source, lockfile, dan bukti deploy bertanggal, 2026-09-22.

| Lapisan | Teknologi dan peran | Status |
| --- | --- | --- |
| Aplikasi server | Python 3.10+, Django 5.2, Tornado | Django menangani aplikasi dan data. Tornado menangani push ke browser. |
| Browser chat | TypeScript, jQuery, Handlebars, Webpack 5 | Browser Zulip tetap menjadi jalur chat utama. UI agent dan settings browser masih pending. |
| Data chat | PostgreSQL 14 dan volume uploads | PostgreSQL menyimpan metadata. Volume aplikasi menyimpan isi upload. |
| Asinkron chat | RabbitMQ 4.2 | Dipakai aplikasi chat. Jangan samakan dengan runner agent. |
| Data ephemeral | Redis | Dipakai aplikasi chat. |
| Cache | Memcached dengan SASL | Dipakai aplikasi chat. |
| Deploy chat | Docker 29.8.1, Docker Compose 5.5.1, systemd | Engine khusus Grow Team. Bukti deploy bertanggal ada untuk `team.growc.id`. |
| Ingress dan email | Cloudflare Tunnel dan Cloudflare Email Worker | Tunnel menyediakan ingress. Worker mengirim email transaksi. |
| Control plane agent | Django dan PostgreSQL | Source menyimpan pairing, policy, lifecycle, approval, publication, dan audit. |
| Runner Linux | TypeScript dan Node 24.18.0 | Runner terpisah dari image Django. Runner menangani proses lokal, journal, workspace, dan containment. |
| Protokol runner **[jalur code]** | ACP SDK 1.5.0, Codex ACP 1.12.0, Codex 0.154.0, Zod 4.6.5 | Versi dipatok untuk runner. Jalur cepat memakai `@anthropic-ai/sdk` (lihat [keputusan SDK agent](agent-sdk-decision.md)). |
| Native payload **[jalur code]** | Codex Linux x64, `bwrap`, `rg`, `zsh` | Inventaris dan notice disiapkan. Image `1c3ebcde3d7e` telah diuji sebagai intermediate image. |
| Base runner | Node 24.18.0 Trixie slim | Kandidat kompatibel untuk payload native. Final package dan SBOM masih gate rilis. |
| Isolasi eksekusi **[jalur code]** | Rootless Docker, sandbox, checkout owner | Task 0–6 telah selesai dan lulus review komponen. Sertifikasi runtime produk tetap pending. Jalur cepat berjalan di proses runner tanpa container. |
| Provider **[jalur code]** | Chat Completions atau Responses | Provider memakai kontrak wire terpisah. Provider nyata memerlukan bukti readiness dan rilis. Jalur cepat memakai dialek `anthropic_messages`. |
| Rahasia | Envelope encryption server dan referensi lokal runner | Field source bersifat write-only. Key production dan recovery penuh perlu bukti final. |

Bukti deploy chat sebelumnya meliputi image fork Grow Team, PostgreSQL, Redis, RabbitMQ, Memcached, Cloudflare Tunnel, Email Worker, dan deploy isolasi dari Hermes. Bukti itu juga mencakup pemeriksaan browser inti dan branding. Lihat [verifikasi branding](../../deploy/grow-team/BRANDING-VERIFICATION.md). Bukti tersebut tidak membuktikan rilis agent.

## Batas operasi

- Gunakan `deploy/grow-team/compose.sh` untuk engine Docker khusus Grow Team.
- Simpan konfigurasi dan credential host pada path privat.
- Runner memakai koneksi keluar. Jangan buka port masuk pada runner.
- Dua jalur berbeda: jalur cepat (Anthropic SDK, answer dan manage) dan jalur code (container, ACP). Jangan lakukan fallback model atau switch jalur otomatis.
- Kontrak enable eksplisit setelah probe adalah target Task9. Source saat ini masih auto-enable profil siap.

Rujukan: [blueprint](blueprint.md), [security](security.md), dan tiga spesifikasi agent: [connections and coding harness](spec/2026-09-21-agent-connections-and-coding-harness.md), [lifecycle and mention flow](spec/2026-09-21-agent-lifecycle-and-mention-flow.md), serta [settings, connections, and team defaults](spec/2026-09-22-agent-settings-connections-and-team-defaults.md).
