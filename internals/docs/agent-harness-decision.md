# Keputusan harness agent: adaptasi Buzz, fork Buzz, atau fork ZCode

Tanggal: 2026-09-23, Asia/Jakarta.

Status: **diputuskan untuk rilis 23.** Tinjau ulang hanya bila syarat pada bagian 6 terpenuhi.

Baseline Grow Team: `83b1a57b9e4` (image `12.2-grow-team.22`).

## 1. Pertanyaan

Tim Grow Team berisi lima sampai enam orang dan bekerja dari browser. Tim perlu
harness agent untuk coding dan untuk agent administrator yang mengelola channel,
grup, dan topik dari chat. Keputusan ini memilih satu dari tiga jalur.

| Opsi                  | Arti                                                                                                        |
| --------------------- | ----------------------------------------------------------------------------------------------------------- |
| A. Adaptasi pola Buzz | Pertahankan Zulip, control plane Django, dan `grow-agent-runner`. Tulis ulang pola Buzz; jangan salin kode. |
| B. Fork Buzz          | Pakai relay, harness `buzz-acp`, dan aplikasi Buzz sebagai dasar produk.                                    |
| C. Fork ZCode         | Pakai agent CLI ZCode sebagai dasar coding agent Grow Team.                                                 |

Riset ZCode juga menilai opsi adapter: runner menjalankan ZCode sebagai runtime ketiga tanpa fork.

## 2. Bukti yang dipelajari

| Sumber                                                                                  | Commit                                     | Cara baca                                                                 |
| --------------------------------------------------------------------------------------- | ------------------------------------------ | ------------------------------------------------------------------------- |
| [Buzz](https://github.com/block/buzz/tree/ec7ea38f62ea917f15e85a678bc94f3bbee5bb64)     | `ec7ea38f62ea917f15e85a678bc94f3bbee5bb64` | Source, dokumen, dan tes dibaca. Tidak ada kode Buzz yang dijalankan.     |
| [ZCode](https://github.com/zai-org/ZCode/tree/872ad960de7ec172591f7e1952f7849229f94521) | `872ad960de7ec172591f7e1952f7849229f94521` | Source, lisensi, dan ukuran dibaca. Tidak ada kode ZCode yang dijalankan. |
| Grow Team                                                                               | `83b1a57b9e4`                              | Source, tes, dan dokumen dibaca dengan path dan nomor baris.              |

Commit ZCode tersebut adalah HEAD publik pertama, "feat: open source", 2026-09-20.
Repository ZCode belum mempunyai rilis atau tag. Antara riset Buzz sebelumnya
(`a3117a47`) dan commit Buzz tersebut ada 12 commit. Commit-commit itu tidak mengubah
crate agent, harness, CLI, atau auth.

### 2.1 Ringkasan bukti

Path pada tabel ini relatif ke repository masing-masing pada commit di atas.

| Aspek            | Buzz                                                                                                                                         | ZCode                                                                                                                                          | Grow Team sekarang                                                    |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| Jenis produk     | Workspace chat berbasis relay. Agent adalah anggota dengan kunci sendiri.                                                                    | Workbench coding Z.AI dengan desktop Electron, web, dan TUI.                                                                                   | Fork Zulip dengan control plane Django dan runner TypeScript.         |
| Antarmuka agent  | Desktop pemilik membuat kunci agent dan menjalankan harness (`desktop/src-tauri/src/managed_agents/runtime.rs:447`).                         | Desktop Electron, web, dan terminal.                                                                                                           | Browser saja. Runner berjalan sebagai layanan tanpa jendela.          |
| Protokol agent   | ACP lewat harness `buzz-acp` dengan 1 sampai 32 proses (`crates/buzz-acp/src/config.rs:298-300`).                                            | Protokol sendiri. ACP sudah dipensiunkan (`packages/services/test/nonCliAcpRetirement.test.ts`).                                               | ACP (`codex-acp` 1.12.0) dan runtime endpoint.                        |
| Izin alat        | Default `bypassPermissions`, jadi tidak ada approval per alat (`crates/buzz-acp/src/config.rs:459-470`).                                     | Headless memakai mode `yolo` secara default dan tidak punya permukaan approval (`apps/zcode-cli/packages/cli/src/headless-workflow.ts:31-51`). | Grant per resource, propose dan consume, serta approval sekali pakai. |
| Isolasi          | Proses lokal dengan process group, atau pod Kubernetes.                                                                                      | Tidak ada sandbox OS (`NOTICE.md:9`).                                                                                                          | Container rootless, lease, epoch, dan journal.                        |
| Daya tahan       | Sesi ACP hanya di memori (`ARCHITECTURE.md:702`).                                                                                            | Sesi di SQLite lokal.                                                                                                                          | Job, attempt, checkpoint, dan input di PostgreSQL.                    |
| Kelola workspace | CLI `buzz` membuat, mengarsip, dan menghapus channel, serta mengatur anggota, dengan kunci agent. Aksi sensitif menjadi draft untuk pemilik. | Tidak ada. ZCode adalah alat coding.                                                                                                           | Belum ada. Rilis 23 menambah alat tim bertipe.                        |
| Ukuran           | Tidak diukur untuk keputusan ini.                                                                                                            | Closure agent 330.785 baris, 1.201 paket npm produksi, dan 4 berkas tes publik.                                                                | Runner 8.536 baris dengan suite tes sendiri.                          |
| Lisensi          | Tidak dinilai, karena tidak ada kode Buzz yang disalin.                                                                                      | Apache-2.0. Nama "ZCode" tidak ikut dilisensikan (`LICENSE:139-142`).                                                                          | Tidak berubah.                                                        |

## 3. Keputusan

1. Pertahankan Zulip, control plane Django, dan `grow-agent-runner`.
2. Adaptasi pola Buzz yang berguna. Tulis ulang pola itu di Grow Team; jangan salin kode Buzz.
3. Ambil ide ZCode saja. Jangan fork ZCode dan jangan jalankan ZCode sebagai runtime.
4. Jangan fork Buzz.

Rilis ini tidak menyalin kode ZCode, jadi kewajiban notice Apache-2.0 tidak berlaku.
Bila tim kelak menyalin kode ZCode, sertakan LICENSE dan NOTICE, lalu tandai berkas
yang berubah.

## 4. Alasan

| No  | Alasan                                                                                                                                      | Bukti                                                                                                                                   |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Tim bekerja dari browser tanpa aplikasi desktop. Zulip sudah memenuhi syarat ini. Buzz memakai desktop untuk membuat dan menjalankan agent. | [Spec harness](spec/2026-09-21-agent-connections-and-coding-harness.md), bagian 1; Buzz `desktop/src-tauri/src/commands/agents.rs:403`. |
| 2   | Buzz dan ZCode menjalankan alat tanpa approval per alat secara default. Grow Team memeriksa setiap efek di server.                          | Buzz `crates/buzz-acp/src/config.rs:459-470`; ZCode `apps/zcode-cli/packages/core/src/permission/service.ts:136-165`.                   |
| 3   | Agent Buzz bertindak dengan kuncinya sendiri untuk semua peminta. Pola ini membuka risiko confused deputy.                                  | Buzz `desktop/src/features/agents/ui/RespondToField.tsx:37-41`.                                                                         |
| 4   | Job Grow Team tahan restart. Harness Buzz tidak menyimpan state.                                                                            | Buzz `ARCHITECTURE.md:702`; [keputusan runtime](agent-runtime-decision.md).                                                             |
| 5   | ZCode tidak memakai ACP, dan protokolnya belum stabil.                                                                                      | ZCode `packages/shared/src/zcode-protocol/index.ts:7-18`.                                                                               |
| 6   | ZCode tidak punya sandbox OS. Keamanan Grow Team bergantung pada container dan broker alat.                                                 | ZCode `NOTICE.md:9`; [kontrak containment](../../services/grow-agent-runner/CONTAINMENT.md).                                            |
| 7   | Biaya rawat fork ZCode terlalu besar untuk tim lima sampai enam orang.                                                                      | Estimasi riset: fork 45 hari, adapter 15 hari, dan paket ide pertama 8 hari.                                                            |
| 8   | Adapter ZCode memberi nilai kecil. Semua alat Grow Team lewat broker, jadi alat bawaan ZCode harus mati.                                    | [Tool broker runner](../../services/grow-agent-runner/src/tool-broker.ts).                                                              |
| 9   | Keputusan runtime sebelumnya sudah menolak fork harness penuh.                                                                              | [Keputusan runtime](agent-runtime-decision.md).                                                                                         |

## 5. Yang rilis 23 adopsi

| Kontrak rilis 23                                                                  | Asal pola                                            | Cara Grow Team menerapkannya                                                          |
| --------------------------------------------------------------------------------- | ---------------------------------------------------- | ------------------------------------------------------------------------------------- |
| Agent administrator (`spec/2026-09-23-agent-administrator.md`) dengan 11 alat tim | Buzz: kelola workspace lewat aksi                    | Server menjalankan alat dengan izin pemberi perintah. Bot tidak memakai izin sendiri. |
| Setting `can_command_administrator_agents_group`, default `role:administrators`   | Buzz: gerbang `respond_to` dengan default owner-only | Pakai `GroupPermissionSetting` Zulip. Administrator dapat mengganti grup.             |
| Konfirmasi server untuk alat sensitif                                             | Buzz: draft untuk pemilik                            | Server memutuskan konfirmasi saat propose. Hanya pemberi perintah yang menyetujui.    |
| Isi pesan dan repository tidak menambah izin                                      | ZCode: konten repo tidak menaikkan izin              | Katalog alat tertutup. Izin hanya berasal dari setting, grant, dan izin Zulip.        |
| Berbagi agent ke tim dalam satu langkah                                           | Buzz: satu pilihan audiens dengan peringatan         | Satu transaksi membuat grant profil, runner, provider, dan repository.                |
| Pesan status di percakapan dan mention di hasil                                   | Buzz: balasan ke manusia dan callback mention        | Bot menyebut peminta saat tugas menunggu keputusan atau input, dan saat hasil terbit. |
| Kode alasan yang stabil                                                           | Buzz: setup mode menjelaskan kebutuhan yang kurang   | UI mengubah kode menjadi kalimat. Kode mentah pindah ke blok "Technical details".     |

Rilis 23 juga memuat perbaikan dari daftar friksi Grow Team sendiri: tugas Coding dari
dialog tugas, default budget dari provider, dan daftar tugas `#agent-jobs`.

## 6. Syarat tinjau ulang

Tinjau opsi adapter ZCode hanya bila ZCode merilis versi bertag dengan ACP atau
approval headless eksternal.

## 7. Backlog adaptasi berperingkat

Urutan memakai nilai, lalu ukuran. Ukuran mengikuti riset: S kecil, M sedang, dan L
besar. Menurut riset ZCode, peringkat 1, 3, 4, 5, dan 6 membentuk paket pertama
dengan estimasi sekitar 8 hari.

| Peringkat | Adaptasi                                                                   | Asal  | Nilai  | Ukuran | Titik mulai di Grow Team                                                                                                                                                                                               |
| --------- | -------------------------------------------------------------------------- | ----- | ------ | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1         | Ganti batas absolut 60 detik dengan timeout idle.                          | ZCode | Tinggi | S      | `model-broker.ts` memutus request model sesudah 60 detik total. `endpoint-child.ts` memutus request alat sesudah 60 detik tanpa aktivitas, padahal shell boleh berjalan 120 detik. Buktikan dengan tes shell 90 detik. |
| 2         | Kirim status job secara real-time dan tampilkan agent yang sedang bekerja. | Buzz  | Tinggi | M      | Drawer tugas memakai polling 5 detik.                                                                                                                                                                                  |
| 3         | Aturan izin per perintah: allow, ask, dan deny.                            | ZCode | Tinggi | M      | Approval hanya berlaku per aksi.                                                                                                                                                                                       |
| 4         | Izin untuk pola perintah yang sama sampai job selesai.                     | ZCode | Tinggi | M      | Bergantung pada peringkat 3. `git.push` dan `git.draft_pr` tetap butuh approval per operasi.                                                                                                                           |
| 5         | Alat `grow_ask`: pertanyaan pilihan di topik.                              | ZCode | Tinggi | M      | Runner tidak pernah mengirim `input.requested`, jadi status `waiting_for_input` tidak terjadi.                                                                                                                         |
| 6         | Compaction: kosongkan hasil alat lama, lalu ringkas.                       | ZCode | Tinggi | M      | Menutup gap EX-33 dan AT-29.                                                                                                                                                                                           |
| 7         | Perbaikan otomatis terbatas saat required check gagal.                     | ZCode | Tinggi | M      | Runner mengirim `result.prepared` walau verifikasi gagal.                                                                                                                                                              |
| 8         | Rencana dulu, approval, lalu coding.                                       | ZCode | Tinggi | M      | Job answer sudah read-only.                                                                                                                                                                                            |
| 9         | Runner dengan beberapa slot.                                               | Buzz  | Tinggi | L      | Runner mengklaim `capacity: 1`, dan batas realm adalah 2 job aktif.                                                                                                                                                    |
| 10        | Catat token dan biaya per attempt.                                         | Buzz  | Sedang | S      | Model broker menegakkan budget, tetapi UI tidak menampilkan pemakaian.                                                                                                                                                 |
| 11        | Instruksi tim tingkat realm.                                               | Buzz  | Sedang | S      | Bergantung pada instruksi profil (EX-38).                                                                                                                                                                              |
| 12        | Penjaga loop: peringatan untuk panggilan alat yang berulang.               | ZCode | Sedang | S      | Runtime berhenti keras di `tool_rounds` tanpa peringatan.                                                                                                                                                              |
| 13        | Steer di tengah turn pada titik aman.                                      | Buzz  | Sedang | M      | Input berlaku di batas turn.                                                                                                                                                                                           |
| 14        | Klasifikasi shell read-only berbasis argv.                                 | ZCode | Sedang | M      | Shell read-only tetap mereset tree server.                                                                                                                                                                             |
| 15        | Dialek `anthropic-messages` dan preset provider.                           | ZCode | Sedang | M      | Codec hanya menerima Chat Completions dan Responses.                                                                                                                                                                   |
| 16        | Template channel dengan orang, grup, dan agent.                            | Buzz  | Sedang | M      | Bergantung pada alat tim rilis 23.                                                                                                                                                                                     |
| 17        | Draft profil agent dari chat untuk ditinjau pemilik.                       | Buzz  | Sedang | M      | Profil hanya dibuat dari pengaturan.                                                                                                                                                                                   |
| 18        | Skills tim sebagai playbook.                                               | ZCode | Sedang | M      | Tumpang tindih dengan EX-39 sampai EX-42.                                                                                                                                                                              |
| 19        | Tugas terjadwal read-only, misalnya ringkasan harian.                      | Buzz  | Rendah | M      | Trigger hanya mention, DM, dan tindakan manual.                                                                                                                                                                        |
| 20        | Delegasi agent ke agent sebagai job anak.                                  | Buzz  | Rendah | L      | Pesan bot tidak memicu job.                                                                                                                                                                                            |

## 8. Pola yang tidak diambil

| Pola                                                    | Sumber | Alasan                                                                                 |
| ------------------------------------------------------- | ------ | -------------------------------------------------------------------------------------- |
| Default `bypassPermissions`                             | Buzz   | Grow Team mempertahankan broker dan approval.                                          |
| Agent bertindak dengan izin sendiri untuk semua peminta | Buzz   | Risiko confused deputy.                                                                |
| CLI langsung untuk hapus, arsip, dan keluarkan anggota  | Buzz   | Grow Team memakai alat bertipe yang server validasi.                                   |
| Kebijakan yang hanya diperiksa di client                | Buzz   | Grow Team menegakkan kebijakan di server.                                              |
| Engine workflow dan approval gate Buzz                  | Buzz   | Approval gate Buzz belum berjalan. Grow Team sudah punya ledger approval.              |
| Telemetri dan cancel hanya untuk pemilik                | Buzz   | Peminta dan pemegang `job.control` juga perlu kontrol.                                 |
| Stop lewat perintah chat `!shutdown`                    | Buzz   | Grow Team memakai API stop dengan lease dan bukti proses.                              |
| Trigger dari semua pesan atau kata kunci                | Buzz   | Pola ini menambah noise dan biaya. Grow Team memakai mention, DM, dan tindakan manual. |
| Mode `yolo` dan eksekusi tanpa sandbox                  | ZCode  | Grow Team mewajibkan broker alat dan container.                                        |
| Layanan akun, gateway, dan marketplace Z.ai             | ZCode  | Coupling ke layanan luar tanpa manfaat untuk tim.                                      |
