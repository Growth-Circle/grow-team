# Keputusan SDK agent: Anthropic SDK untuk jalur cepat

Tanggal: 2026-09-24, Asia/Jakarta.

Status: **diputuskan oleh pemilik.** Keputusan ini menggantikan pilihan runtime untuk
job `answer` dan `manage` pada [keputusan runtime](agent-runtime-decision.md). Pilihan
untuk job `code` tidak berubah.

Spesifikasi: [jalur cepat](spec/2026-09-24-agent-fast-lane.md).

## 1. Masalah

Agent yang di-mention di chat menjawab dalam 47–118 s (median ±75 s) untuk pertanyaan
sepele. Dari 15 job, 7 gagal. Pengukuran per tahap ada di spesifikasi jalur cepat
bagian 1.

Waktu tidak habis di model atau di framework. Waktu habis di container per attempt,
pembacaan konteks lewat operasi, polling 2 s, effort `high` tanpa streaming, dan
publikasi yang menunggu container berhenti.

## 2. Opsi yang dinilai

Versi dicek pada 2026-09-24 dari npm, PyPI, dan GitHub.

| Opsi                          | Versi                        | Jalan di mana          | Dukungan Claude            | Penilaian |
| ----------------------------- | ---------------------------- | ---------------------- | -------------------------- | --------- |
| Anthropic SDK (Messages API)  | `@anthropic-ai/sdk` 0.128.0  | Di proses runner       | Penuh dan sama hari        | **Dipilih** |
| Vercel AI SDK 7               | `ai` 7.0.113                 | Di proses runner       | Sebagian besar, menyusul   | Cadangan |
| Claude Agent SDK              | TS 0.3.281, Py 0.2.159       | Subproses CLI per sesi | Penuh                      | Tidak untuk jalur cepat |
| Claude Managed Agents         | Beta                         | Infrastruktur Anthropic | Penuh                     | Ditolak |
| OpenAI Agents SDK             | TS 0.18.0, Py 0.22.3         | Di proses              | Lewat LiteLLM, beta        | Ditolak |
| OpenRouter Agent SDK          | `@openrouter/agent` 0.11.0   | Di proses              | Lewat API OpenRouter       | Ditolak |
| Cloudflare Agents SDK         | `agents` 0.24.0              | Workers + Durable Objects | Lewat Vercel AI SDK     | Ditolak |
| Mastra, LangGraph, Google ADK | Beragam                      | Di proses              | Beragam                    | Ditolak |

## 3. Keputusan

Jalur cepat memakai `@anthropic-ai/sdk` dengan Messages API streaming dan loop alat
yang ditulis sendiri di dalam proses `grow-agent-runner`.

Alasan:

1. Kita hanya memakai model Claude. Fitur yang paling memotong latensi tersedia penuh di
   SDK resmi: effort per request, prompt caching, streaming delta teks dan input alat,
   serta usage per giliran.
2. Lapisan paling sedikit. Tidak ada subproses, container, atau abstraksi provider.
3. Loop sendiri memberi titik yang pasti untuk jurnal, otoritas server, dan pembatalan
   sebelum setiap efek. Tool Runner SDK masih beta, jadi tidak dipakai.
4. Overhead framework hanya milidetik. Subproses atau container menambah 8–70 s.

Alasan opsi lain ditolak:

- **Vercel AI SDK 7:** layak, tetapi menambah abstraksi dan fitur Claude baru datang
  lebih lambat. Opsi ini menjadi cadangan bila endpoint model tidak mendukung format
  Anthropic Messages, atau bila tim nanti memakai model non-Claude.
- **Claude Agent SDK:** harness terkuat, tetapi setiap sesi menjalankan proses CLI dengan
  overhead ±12 s dan memori ±1 GB. Opsi ini dapat dinilai lagi untuk jalur code.
- **Managed Agents:** loop berjalan di infrastruktur Anthropic. Model tidak dapat lewat
  9router dan data chat keluar dari server kita.
- **OpenAI Agents SDK:** dukungan Claude hanya best-effort.
- **OpenRouter Agent SDK:** masih muda (dibuat April 2026, 28 bintang) dan terikat API
  OpenRouter.
- **Cloudflare Agents SDK:** memaksa runtime pindah ke Cloudflare Workers, dan panggilan
  modelnya tetap memakai Vercel AI SDK.
- **Mastra, LangGraph, Google ADK:** fitur durable dan graph tidak dibutuhkan untuk
  tanya-jawab singkat. Dukungan Claude di ADK paling lemah.

## 4. Keputusan lama yang berubah

| Dokumen                                                        | Isi lama                                              | Isi baru |
| -------------------------------------------------------------- | ----------------------------------------------------- | -------- |
| [Keputusan runtime](agent-runtime-decision.md) bagian awal     | Endpoint runtime + codex-acp untuk semua job          | Hanya jalur code |
| Keputusan runtime, batas untuk dua mode                        | Container rootless per attempt untuk semua job        | Hanya jalur code |
| [Desain sandbox](agent-sandbox-design.md)                      | Model di container, hanya `POST /v1/responses`        | Hanya jalur code |
| [Keputusan harness](agent-harness-decision.md) peringkat 1, 2, 9, 15 | Backlog                                        | Masuk jalur cepat |
| [Blueprint](blueprint.md), [BRD](brd.md), [PRD](prd.md), [FRD](frd.md), [tech stack](techstack.md), [security](security.md) | Dua mode: ACP dan endpoint | Dua jalur: fast dan code |
| Runner `RUNTIME.md`                                            | Publikasi menunggu bukti stop dan containment kosong | Jalur cepat terbit segera |

## 5. Konsekuensi

1. Protokol runner naik ke versi 2 (lane, paket konteks, snapshot draft).
2. Runner menyimpan koneksi model `anthropic_messages` di host.
3. Loop model jalur cepat berjalan di proses host, tanpa container. Proses itu tidak
   menjalankan kode. Semua efek tetap lewat server.
4. Jalur code tetap membawa dependensi ACP dan Codex sampai ada keputusan lain.

## 6. Tinjau ulang bila

1. Endpoint model produksi tidak mendukung Anthropic Messages (gerbang F0).
2. Tim memakai model non-Claude untuk jalur cepat.
3. Target latensi tidak tercapai sesudah fase F5 karena batas SDK.
