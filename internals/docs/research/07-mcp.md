# Riset 07: MCP di sanji.space (koneksi, pemisahan antar user, dan pembatasan agen)

Status: riset, bukan keputusan.

**Tanggal:** 24 September 2026
**Pertanyaan owner:** Tambah halaman _Connect MCP_. Bagaimana memisahkan MCP antar user supaya tidak tabrakan, dan bagaimana agar agen tidak asal memakai tool (misalnya harus riset dulu)?

## 1. MCP singkat

- MCP adalah standar JSON-RPC 2.0 dengan dua transport, **stdio** dan **Streamable HTTP**, serta tiga primitif server: tools, resources, prompts. **Revisi 2026-07-28 membuat MCP stateless**: handshake `initialize` dihapus, dan setiap request membawa versi protokol serta kapabilitas klien. _(Maxim)_
- Otorisasi MCP bersifat opsional dan berlaku untuk transport HTTP. Server stdio mengambil credential dari environment. _(Aembit)_
- Server MCP remote berperan sebagai **resource server OAuth 2.1**: menerbitkan metadata, memvalidasi bahwa _audience_ token adalah server itu sendiri, tidak menerima token milik layanan lain, dan menegakkan scope per user di setiap request. _(Corgea)_
- **Token passthrough dilarang** (confused deputy): server tidak boleh meneruskan token dari klien ke API upstream. _(Maxim)_
- Revisi 2025-11-25 menurunkan Dynamic Client Registration menjadi opsional dan lebih memilih: credential pra-registrasi → Client ID Metadata Document → DCR sebagai cadangan. _(Wavect)_

## 2. Pola yang direkomendasikan

| Pola                              | Isi                                                                                                                                                             | Sumber    |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| **MCP gateway**                   | Untuk banyak server di banyak tenant, gateway memberi satu titik isolasi dan satu titik audit                                                                   | Wavect    |
| Kredensial terpusat               | Agen memegang satu credential ke gateway, dan gateway memakai credential yang benar ke tiap MCP downstream                                                      | Maxim     |
| **Per-user OAuth**                | Layanan pribadi butuh credential per user. Tiap user login sekali, token disimpan terikat identitasnya dan dipakai ulang. Mencabut di IdP menghapus ikatan itu  | Maxim     |
| Remote > stdio untuk hal per-user | stdio mewarisi environment proses dan tidak punya auth per panggilan                                                                                            | Maxim     |
| **Delegasi, bukan impersonasi**   | User asli tetap di klaim `sub`, rantai agen dicatat di `act`, supaya audit jujur                                                                                | Wavect    |
| **Allowlist tool**                | Tool filtering membatasi tool yang boleh dipanggil, sehingga permukaan kemampuan agen menyempit sebelum ada request                                             | Maxim     |
| Guardrail di batas tool           | Periksa argumen sebelum eksekusi dan hasil sebelum dikembalikan, supaya prompt injection diblok di batas tool                                                   | Maxim     |
| Policy per panggilan              | Model: subjek (user) · delegasi (agen) · resource (server) · action (tool) · argumen · konteks (tenant, repo, risiko, persetujuan). Cek RBAC/ABAC per panggilan | Permit.io |
| Anti "blank check"                | Tanpa lapisan policy, tim cenderung memberi scope read-write-admin "karena agen mungkin butuh"                                                                  | Permit.io |
| Vetting server                    | Cek penerbit & repo, baca setiap deskripsi tool (instruksi tersembunyi), review source, **pin versi**, dan scan ulang setiap ganti versi                        | Corgea    |
| Tool sempit & jujur               | Tool kecil, **read-only by default**, deskripsi sesuai fungsi                                                                                                   | Corgea    |
| Refresh token                     | Agen berjalan di latar tanpa sesi user, jadi refresh harus non-interaktif dan token disimpan di secret store terenkripsi                                        | RockB     |

## 3. Desain sanji

### 3.1 Tiga lingkup koneksi (supaya antar user tidak tabrakan)

| Lingkup       | Siapa yang memasang | Credential                                                        | Siapa/agen yang bisa pakai                               | Contoh                                    |
| ------------- | ------------------- | ----------------------------------------------------------------- | -------------------------------------------------------- | ----------------------------------------- |
| **Workspace** | Owner/Admin         | 1 credential bersama (akun layanan)                               | Agen yang diberi grant, di ruang mana pun yang diizinkan | Linear org, Sentry, database read replica |
| **Ruang**     | Pemilik ruang       | 1 credential per ruang                                            | Hanya job yang berjalan **di ruang itu**                 | Figma file klien, GA4 properti klien      |
| **Pribadi**   | Masing-masing user  | **Per-user OAuth**, disimpan dengan kunci `(realm, user, server)` | Agen, **hanya saat dipanggil oleh user itu** (delegasi)  | Gmail, Google Calendar, Notion pribadi    |

- Tidak pernah ada credential pribadi yang dipakai untuk permintaan orang lain. Jika Raka memanggil @ayame dan tool butuh Notion pribadi Raka yang belum terhubung, agen menjawab: "Hubungkan Notion kamu dulu" (tombol) daripada memakai Notion Dita.
- **Nama tool diberi namespace** `server.tool` (mis. `linear.create_issue`, `notion.search`), supaya dua server dengan tool bernama sama tidak bentrok.
- Rate limit & kuota **per koneksi**, supaya satu user atau agen tidak menghabiskan kuota API bersama.

### 3.2 Aturan akses agen = irisan

```
boleh_pakai(tool) =
    grant_agen ∋ server            (diatur di Tambah/Atur agen)
  ∧ lingkup koneksi cocok          (workspace | ruang job | pribadi milik pemanggil)
  ∧ kebijakan tool ≠ Blokir        (per tool)
  ∧ izin peran pemanggil           (doc 10)
  ∧ mode job mengizinkan           (Riset dulu → hanya read-only)
```

### 3.3 Kebijakan per tool (3 status)

| Status             | Arti                                                                                       | Default                                         |
| ------------------ | ------------------------------------------------------------------------------------------ | ----------------------------------------------- |
| **Izinkan**        | Agen boleh memanggil langsung                                                              | Tool `readOnlyHint` (search, list, get)         |
| **Minta approval** | Kartu job masuk MENUNGGU APPROVAL + item di _Perlu kamu_, berisi argumen yang akan dikirim | Tool tulis (create, update, send)               |
| **Blokir**         | Tool tidak ditawarkan ke model sama sekali                                                 | Tool `destructiveHint` (delete, drop, transfer) |

### 3.4 "Jangan asal pakai": mode **Riset dulu**

1. Default untuk job baru: **Riset dulu**. Agen hanya boleh memakai tool read-only.
2. Sebelum memakai tool tulis, agen wajib mengirim **kartu Rencana**: tujuan, tool yang akan dipanggil + contoh argumen, perkiraan biaya.
3. Pengguna klik **Setujui rencana** → tool tulis dalam rencana terbuka untuk job itu saja (tetap mengikuti status per tool).
4. Opsi per agen: _Riset dulu_ (default) / _Langsung kerjakan_ (hanya untuk tool berstatus Izinkan).
5. **Pertama kali** agen memakai server baru di sebuah ruang → selalu minta approval sekali.
6. Batas **maks. panggilan tool per job** (default 40) dan `maxCost` (riset 06).

### 3.5 Gateway MCP sanji

- Semua panggilan agen (cloud maupun runner) lewat **sanji MCP Gateway**. Runner **tidak pernah** menerima token upstream.
- Credential di vault terenkripsi. Refresh non-interaktif dengan overlap 60 detik.
- Validasi audience, tanpa passthrough, delegasi `sub`=user & `act`=agen.
- **Deteksi perubahan tool (tool drift):** jika server mengubah daftar/deskripsi tool, koneksi masuk status _Perlu ditinjau_. Tool baru **Blokir** sampai Admin meninjau.
- Log audit per panggilan: waktu, user, agen, server, tool, argumen (disamarkan), hasil, biaya.

### 3.6 Katalog vs server kustom

- **Katalog terverifikasi** (dikurasi sanji, versi dipin): siapa pun bisa menghubungkan ke lingkup yang diizinkan perannya.
- **Server kustom (URL)**: hanya Owner/Admin. Wizard menampilkan semua tool + deskripsi mentah untuk ditinjau. Tool tulis default _Minta approval_, tool destruktif _Blokir_.
- Server **stdio** hanya didukung di **runner lokal/VPS** milik pengguna (credential dari env runner), tidak di cloud multi-tenant.

## 4. Sumber

- Maxim: MCP security checklist 2026: https://www.getmaxim.ai/articles/mcp-security-best-practices-enterprise-checklist-2026/
- Maxim: MCP gateway governance: https://www.getmaxim.ai/articles/best-mcp-gateway-for-governing-mcp-server-access-in-2026/
- Maxim: Best MCP gateways 2026: https://www.getmaxim.ai/articles/best-mcp-gateways-in-2026-what-they-do-and-how-to-pick-one/
- Wavect: Multi-tenant MCP authorization: https://wavect.io/blog/enterprise-mcp-authorization-architecture/
- Corgea: MCP security checklist: https://corgea.com/learn/mcp-security-best-practices
- Aembit: MCP OAuth 2.1 & agent identity: https://aembit.io/blog/mcp-oauth-2-1-pkce-and-the-future-of-ai-authorization/
- Descope: MCP auth spec: https://www.descope.com/blog/post/mcp-auth-spec
- Permit.io: OAuth on MCP: https://www.permit.io/blog/oauth-on-mcp
- RockB: MCP OAuth guide: https://baeseokjae.github.io/posts/mcp-oauth-authentication-guide-2026/
