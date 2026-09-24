# BRD Grow Team

## Konteks bisnis

Grow Team dimulai sebagai workspace internal kecil. Arah yang diterima adalah kemungkinan penjualan B2B setelah produk, operasi, isolasi pelanggan, dan keamanan siap. Dokumen ini tidak menetapkan harga, pendapatan, SLA, ROI, atau kontrak dukungan.

## Traceability

| ID | Keputusan bisnis | Product requirement | Functional requirement |
| --- | --- | --- | --- |
| BR-01 | Validasi workspace internal dahulu | PR-01 workspace internal | FR-01–FR-07 dan FR-20 |
| BR-02 | Validasi agent untuk tim sendiri dengan authority eksplisit | PR-02 pilot agent internal | FR-08–FR-18 |
| BR-03 | Multi-klien hanya setelah gate | PR-03 kesiapan B2B | FR-19 |

## Asumsi yang perlu diuji

- Tim klien memerlukan percakapan berbasis kanal dan topik yang dapat dicari.
- Browser-first cukup untuk tahap awal.
- Operasi per klien harus memberi batas data dan dampak insiden yang jelas.
- Dukungan komersial hanya dapat ditawarkan setelah model operasi dan respons insiden disetujui.

## Pilihan isolasi pelanggan

| Pilihan | Kelebihan | Risiko dan gate |
| --- | --- | --- |
| Instance per klien | Batas runtime dan data paling jelas | Biaya operasi, update, dan monitoring bertambah. |
| Realm bersama | Operasi lebih ringkas | Perlu bukti isolasi, provisioning, audit, dan batas administrasi. |

Jangan pilih model komersial sebelum gate keamanan, backup/restore, observability, dan offboarding pelanggan disetujui.

## Keputusan produk

- Pertahankan Zulip sebagai chat browser.
- Gunakan Django sebagai control plane dan runner Linux terpisah milik owner.
- Dukung dua jalur awal: jalur cepat (Anthropic SDK, answer dan manage) dan jalur code (container, ACP). Tanpa fallback antarjalur.
- Gunakan authority eksplisit per owner, principal, resource, scope, dan action.
- Jangan beri bypass authority karena seseorang adalah administrator platform.
- Jangan jadikan default tim sebagai grant atau trigger otomatis.
- Gunakan explicit enable setelah probe yang terikat revision. Source saat ini belum memenuhi kontrak ini dan Task9 harus memperbaikinya.

## Status keputusan

| Status | Keputusan |
| --- | --- |
| Bukti deploy bertanggal | Fork Zulip Grow Team berjalan pada `team.growc.id`. Laporan branding merekam browser checks dan isolasi deploy. |
| Diterima | Arsitektur agent, dua jalur runtime (jalur cepat dengan Anthropic SDK dan jalur code dengan container/ACP), dan authority eksplisit tercatat dalam spesifikasi agent. |
| Komponen selesai direview | Task 0–6 untuk control plane, runner, dan containment telah selesai dan lulus review komponen. Image `1c3ebcde3d7e` adalah intermediate image yang diuji. |
| Pending gate rilis | UI, provider nyata, package dan notice final, recovery, capacity, migrasi live, dan activation realm/provider. |
| Belum diputuskan | Isolasi klien, harga, SLA, support, billing, dan operasi komersial. |

Lihat [PRD](prd.md), [FRD](frd.md), [roadmap](roadmap.md), [security](security.md), dan tiga spesifikasi agent: [connections and coding harness](spec/2026-09-21-agent-connections-and-coding-harness.md), [lifecycle and mention flow](spec/2026-09-21-agent-lifecycle-and-mention-flow.md), serta [settings, connections, and team defaults](spec/2026-09-22-agent-settings-connections-and-team-defaults.md).
