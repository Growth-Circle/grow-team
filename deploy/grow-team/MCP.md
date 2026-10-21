# Koneksi MCP untuk agen eksternal

Endpoint: `https://team.growc.id/mcp`.
Dashboard: `https://team.growc.id/#mcp`.

## Claude Cowork dan ChatGPT

Tambahkan endpoint sebagai konektor MCP jarak jauh dengan autentikasi OAuth.
Masuk ke akun Grow Team, periksa nama aplikasi, alamat callback, dan izin yang diminta.
Aktifkan **Izinkan menulis atas nama saya** agar agen dapat mengirim pesan, mention, pesan langsung, dan mengubah tugas.
Pilih **Setujui koneksi** atau **Tolak** di dashboard Grow Team.
Koneksi lama dengan izin baca perlu disambungkan ulang untuk mendapat persetujuan izin tulis.

Claude Cowork menyediakan konektor di **Customize → Connectors**.
ChatGPT menyediakan MCP kustom melalui **Plugins**. Aktifkan **Developer mode** di **Settings → Security and login** pada akun yang mendukungnya.
Tampilan serta ketersediaan menu mengikuti paket dan kebijakan workspace masing-masing.

## OpenClaw, Hermes, dan klien HTTP lain

1. Buka **Koneksi MCP** di sidebar Grow Team.
2. Isi nama agen. Aktifkan izin tulis hanya bila diperlukan.
3. Pilih **Create token**, lalu salin token yang ditampilkan sekali.
4. Simpan token pada penyimpanan rahasia agen.
5. Gunakan transport HTTP dengan URL endpoint dan header `Authorization: Bearer <token>`.

Klien yang hanya menyediakan stdio memerlukan bridge ke transport HTTP.
Jangan simpan token pada Git, pesan percakapan, atau dokumentasi.

## Izin dan masa berlaku

Koneksi memakai izin akun yang menyetujuinya, termasuk batas channel privat, pesan langsung, dan tugas.
Izin bawaan `team:read` hanya membuka tool baca.
Izin tambahan `team:write` membuka pengiriman pesan serta pembuatan dan perubahan tugas.
Pesan dan tugas ditulis sebagai akun pemilik koneksi.

Token manual dan persetujuan OAuth berlaku maksimal 90 hari.
Access token OAuth berlaku satu jam. Refresh token berotasi dan berlaku maksimal 30 hari.
Penggunaan ulang refresh token mencabut seluruh koneksi tersebut.
Kode otorisasi hanya dapat digunakan sekali, dengan PKCE S256, dalam lima menit.

Pilih **Revoke access** pada koneksi untuk menutup seluruh tokennya.
Permintaan yang sudah mulai sebelum pencabutan dapat menyelesaikan operasi yang sedang berjalan.
Akun atau workspace yang dinonaktifkan tidak dapat menggunakan token.

## Tool

| Tool | Fungsi | Izin |
| --- | --- | --- |
| `search` | Cari pesan; hasil berisi ID dan tautan untuk `fetch` | Baca |
| `fetch` | Baca `message:<id>` atau `task:<id>` | Baca |
| `list_users` | Cari anggota berdasarkan nama; dapatkan ID untuk mention dan pesan langsung | Baca |
| `list_channels` | Daftar channel yang dapat diakses | Baca |
| `list_topics` | Daftar topik pada channel | Baca |
| `get_messages` | Baca hingga 100 pesan, dengan filter topik atau anchor | Baca |
| `get_task_board` | Baca board dan tugas yang terlihat | Baca |
| `get_me` | Baca profil akun pemilik koneksi | Baca |
| `get_direct_messages` | Baca pesan langsung dengan satu atau beberapa anggota | Baca |
| `get_needs` | Daftar item Perlu kamu milik akun; persetujuan dan pertanyaan agen tetap dijawab di aplikasi | Baca |
| `send_message` | Kirim pesan ke channel, dengan mention opsional | Tulis |
| `send_direct_message` | Kirim pesan langsung ke satu atau beberapa anggota | Tulis |
| `edit_message` | Ubah isi pesan yang dikirim akun pemilik koneksi | Tulis |
| `add_reaction` | Tambah reaksi emoji ke pesan | Tulis |
| `remove_reaction` | Hapus reaksi emoji milik akun pemilik koneksi | Tulis |
| `upload_file` | Unggah file hingga 15 MiB, atau batas unggah workspace bila lebih kecil; hasil berisi `url` dan `markdown` siap tempel | Tulis |
| `resolve_needs` | Tandai mention dan item kartu sebagai selesai; jenis lain gagal | Tulis |
| `reopen_needs` | Batalkan `resolve_needs`; item kartu hanya dibuka lagi dalam beberapa menit | Tulis |
| `snooze_needs` | Sembunyikan mention, item kartu, dan balasan sampai pukul 07:00 berikutnya di jam workspace | Tulis |
| `unsnooze_needs` | Tampilkan lagi mention, item kartu, dan balasan yang ditunda | Tulis |
| `mark_read` | Tandai satu channel, satu topik, atau satu percakapan langsung sebagai sudah dibaca | Tulis |
| `delete_task` | Hapus tugas yang dapat diubah akun | Tulis |
| `create_task` | Buat tugas | Tulis |
| `update_task` | Ubah tugas yang dapat diakses | Tulis |

Metadata OAuth tersedia di `/.well-known/oauth-protected-resource/mcp` dan `/.well-known/oauth-authorization-server`.
Server mendukung registrasi dinamis untuk klien OAuth publik.
Transport Streamable HTTP memakai respons JSON, tanpa sesi atau koneksi SSE yang persisten.

Audit menyimpan nama tool dan hasilnya. Audit tidak menyimpan isi pesan, argumen tool, atau token.
Database hanya menyimpan hash token dan kode otorisasi.

Sumber protokol: [otorisasi MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) dan [transport MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
Panduan produk: [konektor Claude](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp).

Panduan ChatGPT: [koneksi MCP](https://developers.openai.com/plugins/build/app-quickstart) dan [OAuth](https://developers.openai.com/plugins/build/auth).

Tool baca `get_messages`, `get_direct_messages`, dan `fetch` menandai pesan yang mereka kembalikan sebagai sudah dibaca, hanya bila koneksi punya akses tulis dan tool `mark_read` bernilai Auto, dan kecuali argumen `mark_read` bernilai `false`. Persetujuan dan pertanyaan agen tetap dijawab di aplikasi.
Gunakan `mention_user_ids` untuk mention anggota pada tool pengiriman pesan.
Gunakan `recipient_user_ids` untuk penerima pesan langsung. Cari ID melalui `list_users`.
Gunakan `upload_file` untuk mengunggah gambar atau file, lalu tempel `markdown` hasilnya ke isi pesan. Gambar tampil langsung di pesan.
Isi pesan mendukung Markdown workspace. Mention dikirim sebagai notifikasi anggota.

## Identitas konektor

Identitas server MCP memakai nama **sanji.space** dan ikon aplikasi sanji.space berukuran 512 × 512.
Metadata `serverInfo` menyediakan `title`, `websiteUrl`, dan `icons` untuk klien yang mendukungnya.
Ikon tersedia melalui HTTPS pada origin workspace tanpa token.

Halaman persetujuan Claude memakai logo resmi Claude dari [situs Claude](https://claude.com).
Aset lokal `static/images/mcp/claude.png` berasal dari favicon PNG situs tersebut.
Logo ini ditampilkan untuk nama Claude, Claude Cowork, atau Claude Desktop dengan callback `claude.ai`.

Sumber metadata ikon: [spesifikasi MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic).
