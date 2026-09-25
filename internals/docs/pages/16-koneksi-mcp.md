# 16 · Halaman Koneksi MCP

Status: spesifikasi halaman, belum kontrak. Konflik: lihat [keputusan rebrand](../sanji-rebrand-decision.md#konflik-halaman-dan-keputusan).

Tanggal: 24 September 2026 · Prototipe: sidebar **Koneksi MCP** (atau ⌘K → "Koneksi MCP"). Dasar: [riset 07](../research/07-mcp.md).

## Tujuan

Menghubungkan tool eksternal (Linear, Notion, GA4, Sentry, dll.) ke agen lewat MCP, **tanpa bentrok antar user** dan **tanpa agen memakai tool sembarangan**.

## Isi halaman

1. **Header** + tab **Terhubung · N** / **Katalog**. Badge di sidebar = jumlah koneksi yang perlu ditinjau.
2. **3 kartu lingkup** (jumlah koneksi + penjelasan):
   - **Workspace** (kuning): dipasang Admin, 1 akun bersama.
   - **Ruang** (hijau): dipasang pemilik ruang, hanya job di ruang itu.
   - **Pribadi** (ungu): login masing-masing, dipakai agen hanya saat user itu yang memanggil.
3. **Tab Terhubung**, dua kolom (bertumpuk di HP):
   - Kiri: daftar koneksi (ikon, nama, badge lingkup, siapa yang memasang). Label **TINJAU** jika server mengubah tool-nya.
   - Kanan (panel koneksi terpilih):
     - Detail lingkup + jumlah tool + namespace (`linear.*`).
     - Banner **Perlu ditinjau** + tombol "Sudah ditinjau" bila ada tool baru (tool baru diblokir sampai ditinjau).
     - **Kebijakan per tool**: segmented `Izinkan` (hijau) / `Approval` (kuning) / `Blokir` (hitam), dengan nama tool mono dan label BARU untuk tool baru.
     - **Agen yang boleh memakai** (multi-pilih).
     - Kotak aturan kontekstual (mis. untuk koneksi pribadi: "Jika Raka memanggil @ayame, Ayame tidak memakai Notion Dita").
     - **Putuskan**.
   - Bawah: kartu gelap **Mode kerja default agen**: _Riset dulu (disarankan)_ / _Langsung kerjakan_, plus contoh **kartu Rencana** (✓ baca, → butuh persetujuan, × diblokir) dengan tombol Setujui rencana / Ubah.
4. **Tab Katalog**: grid server terverifikasi (ikon, versi dipin, deskripsi, lingkup yang didukung) + **Hubungkan** (OAuth) atau **Terhubung ✓**. Kartu putus-putus **+ Server kustom (URL)** khusus Owner/Admin.

## Interaksi

| Aksi                                      | Efek                                                                                                                                                                                   |
| ----------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Klik koneksi                              | Panel kanan menampilkan koneksi itu                                                                                                                                                    |
| Ubah kebijakan tool                       | Segmented berganti warna + toast `linear.create_issue → Approval`                                                                                                                      |
| Ubah kebijakan tool BARU sebelum ditinjau | Ditolak + toast "Tinjau dulu"                                                                                                                                                          |
| Sudah ditinjau                            | Banner hilang, tool baru jadi _Approval_, label TINJAU & badge sidebar berkurang                                                                                                       |
| Pilih/hapus agen                          | Chip ✓ berubah. Agen yang tidak dipilih tidak melihat tool server ini                                                                                                                  |
| Putuskan                                  | Koneksi hilang, toast "Token dicabut di gateway"                                                                                                                                       |
| Katalog → Hubungkan                       | (Produksi: OAuth) koneksi baru dengan lingkup default server, **tanpa agen** (harus dipilih eksplisit), tool baca = Izinkan, tulis = Approval, hapus = Blokir. Pindah ke tab Terhubung |
| Mode Riset dulu / Langsung kerjakan       | Teks penjelasan berubah                                                                                                                                                                |
| Setujui rencana                           | Toast "Ayame boleh memanggil linear.create_issue maksimal 3× untuk job ini"                                                                                                            |
| Server kustom                             | Owner/Admin: toast langkah tinjau. Peran lain: ditolak                                                                                                                                 |

## Aturan pemisahan & pembatasan (wajib di server)

1. `boleh_pakai(tool) = grant_agen ∋ server ∧ lingkup cocok ∧ kebijakan ≠ Blokir ∧ izin peran pemanggil ∧ mode job mengizinkan`.
2. **Pribadi:** credential dikunci `(realm, user, server)`. Dipakai hanya jika `job.requested_by == user`. Kalau tidak ada, agen meminta pemanggil menghubungkan akunnya sendiri.
3. **Ruang:** hanya tersedia untuk job dengan `room_id` yang sama.
4. **Namespace tool** `server.tool` untuk semua tool yang ditawarkan ke model.
5. **Riset dulu:** hanya tool read-only sampai `plan` disetujui. Tool tulis di luar rencana tetap tertutup. **Pemakaian pertama** server di ruang baru selalu minta approval.
6. Maks. **40 panggilan tool/job** (bisa diatur) + `maxCost`.
7. **Tool drift:** perubahan daftar/deskripsi tool → status _Perlu ditinjau_, tool baru diblokir, notifikasi ke pemasang.
8. **Tanpa token passthrough.** Validasi audience. Delegasi `sub`=user, `act`=agen.

## Kebutuhan backend

| Entitas               | Field                                                                                                                                                                                                    |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `McpServer` (katalog) | `slug`, `name`, `url`, `version_pin`, `verified`, `supported_scopes[]`, `auth` (oauth/header/none), `tool_manifest_hash`                                                                                 |
| `McpConnection`       | `realm_id`, `server_id`, `scope` (`workspace`/`room`/`personal`), `room_id?`, `user_id?` (pribadi), `installed_by`, `credential_ref` (vault), `status` (`active`/`needs_review`/`revoked`), `rate_limit` |
| `McpToolPolicy`       | `connection_id`, `tool_name`, `policy` (`allow`/`approve`/`block`), `hints` (readOnly/destructive dari server), `reviewed_at`                                                                            |
| `McpAgentGrant`       | `connection_id`, `agent_id`                                                                                                                                                                              |
| `AgentPlan`           | `job_id`, `goal`, `tools[] {name, max_calls, sample_args}`, `status` (`proposed`/`approved`/`rejected`), `approved_by`                                                                                   |
| `McpCallLog`          | `job_id`, `user_id`, `agent_id`, `connection_id`, `tool`, `args_redacted`, `result_status`, `latency_ms`, `cost`                                                                                         |

| Method          | Endpoint                             | Fungsi                                                                                                                       |
| --------------- | ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| GET             | `/api/v1/sanji/mcp/catalog`          | Katalog terverifikasi                                                                                                        |
| GET/POST/DELETE | `/api/v1/sanji/mcp/connections`      | Daftar, hubungkan (mulai OAuth), putuskan                                                                                    |
| GET             | `/api/v1/sanji/mcp/oauth/callback`   | Selesaikan OAuth. Simpan token ke vault                                                                                      |
| PUT             | `/mcp/connections/{id}/tools/{tool}` | Ubah kebijakan tool                                                                                                          |
| PUT             | `/mcp/connections/{id}/agents`       | Grant agen                                                                                                                   |
| POST            | `/mcp/connections/{id}/review`       | Tandai tool drift sudah ditinjau                                                                                             |
| POST            | `/api/v1/sanji/mcp/custom`           | Server kustom (Owner/Admin): ambil manifest → mode tinjau                                                                    |
| POST            | `/jobs/{id}/plan` · `/plan/approve`  | Kartu Rencana (muncul di ruang + _Perlu kamu_)                                                                               |
| —               | **sanji MCP Gateway**                | Endpoint MCP tunggal untuk agen/runner. Menegakkan aturan §1–8, menyuntikkan credential dari vault, dan menulis `McpCallLog` |

## Pertanyaan terbuka

- Perlu grup (mis. "Tim marketing") sebagai lingkup ke-4 di antara Workspace dan Ruang?
- Server stdio di runner lokal: kelola dari halaman ini, atau cukup lewat `sanji runner mcp add` di CLI?
