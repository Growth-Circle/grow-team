# BRD Grow Team

## Konteks bisnis

Grow Team dimulai sebagai workspace internal kecil. Arah yang disetujui adalah kemungkinan penjualan B2B setelah produk, operasi, isolasi pelanggan, dan keamanan siap. Dokumen ini tidak menetapkan harga, pendapatan, SLA, ROI, atau kontrak dukungan.

## Traceability

| ID | Keputusan bisnis | Turunan produk/teknis |
| --- | --- | --- |
| BR-01 | Validasi workspace internal dahulu | PR-01; FR-01–FR-07, FR-20; roadmap tahap 1–2 |
| BR-02 | Validasi agent untuk tim sendiri dahulu | PR-02; FR-08–FR-18; roadmap tahap 3 |
| BR-03 | Multi-klien hanya setelah gate | PR-03; FR-19; roadmap tahap 4–5 |

## Asumsi yang perlu diuji

- Tim klien memerlukan percakapan berbasis kanal/topik yang dapat dicari.
- Browser-first cukup untuk tahap awal.
- Operasi per klien harus memberi batas data dan dampak insiden yang jelas.
- Dukungan komersial hanya dapat ditawarkan setelah model operasi dan respons insiden disetujui.

## Pilihan isolasi pelanggan

| Pilihan | Kelebihan | Risiko/gate |
| --- | --- | --- |
| Instance per klien | Batas runtime dan data paling jelas | Biaya operasi, update, dan monitoring bertambah |
| Realm bersama | Operasi lebih ringkas | Perlu pembuktian isolasi, provisioning, audit, dan batas administrasi |

Jangan pilih salah satu untuk komersial sebelum gate keamanan, backup/restore, observability, dan offboarding pelanggan disetujui. Lihat [security](security.md) dan [roadmap](roadmap.md).

## Keputusan terbuka

1. Segmen klien pertama dan kebutuhan regulasinya.
2. Pemilik dukungan, jam layanan, dan escalation path.
3. Model isolasi dan model biaya yang sesuai.
4. Ketentuan penggunaan AI dan persetujuan aksi eksternal.

## Register keputusan

| Status | Keputusan |
| --- | --- |
| Diterima | Fork Zulip 12.2, browser-first, repo `Growth-Circle/grow-team`, domain internal, dan jaringan AI privat. |
| Dalam proses | Deploy image fork berbranding Grow Team ke `team.growc.id`; belum selesai sampai runtime dan rollback diverifikasi. |
| Proposal | Sidecar bot/API, storage durable AI, dan worker terpisah. |
| Belum diputuskan | Model deployment pelanggan, isolasi tenant, katalog model, dan operasi komersial. |
