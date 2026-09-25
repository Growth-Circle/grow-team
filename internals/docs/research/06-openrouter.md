# Riset 06: Adaptasi OpenRouter (API key + Agent SDK) untuk sanji.space

Status: riset, bukan keputusan.

Catatan konflik: isi riset ini bertentangan dengan keputusan pemilik di [`agent-sdk-decision.md`](../agent-sdk-decision.md) (24 September 2026). Keputusan pemilik yang berlaku sampai ada keputusan baru.

**Tanggal:** 24 September 2026
**Pertanyaan owner:** Bisakah sanji memakai API key OpenRouter dan **OpenRouter Agent SDK**, karena fiturnya sudah banyak, daripada membangun dari nol?

**Jawaban singkat:** ✅ **Ya, untuk agen kerja dan lapisan model**, dan ini menghemat banyak pekerjaan (billing per workspace, batas biaya, guardrail, ganti model, fallback provider). ⚠️ **Tidak menggantikan ACP** untuk agen coding (Claude Code/Codex tetap lewat runner), tapi runner bisa memakai OpenRouter sebagai _endpoint model_. ⚠️ Agent SDK masih **beta**, jadi versinya harus dipin.

---

## 1. Fitur OpenRouter yang relevan (per Sep 2026)

| Fitur                                  | Apa itu                                                                                                                                               | Sumber                    |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------- |
| **Agent SDK `@openrouter/agent`**      | SDK TypeScript model-agnostic yang menjalankan loop agen di 400+ model                                                                                | OpenRouter Blog, Apr 2026 |
| Loop & state                           | `callModel` otomatis berulang sampai kondisi berhenti terpenuhi. Tool dieksekusi SDK. State percakapan dilacak. Ada streaming                         | Docs Agent SDK            |
| **Batas berhenti biaya**               | `stopWhen: [stepCountIs(10), maxCost(0.50)]`                                                                                                          | Docs Agent SDK            |
| **Tool approval**                      | Tool bisa ditandai butuh approval. SDK berhenti, mengembalikan kontrol, dan menunggu keputusan sebelum lanjut                                         | OpenRouter Blog           |
| Status SDK                             | **Beta**, bisa ada breaking change. Disarankan pin versi. ESM-only                                                                                    | npm                       |
| **Workspaces**                         | Lingkungan terpisah, masing-masing dengan API key, default routing, guardrail, dan observability sendiri                                              | OpenRouter Blog           |
| **Guardrails**                         | Batas budget, **Zero Data Retention**, batasan model/provider, pertahanan prompt injection, **DLP**. Bisa dipasang ke key atau anggota                | OpenRouter Blog, Mei 2026 |
| Budget                                 | Reset harian/mingguan/bulanan. Melebihi batas → respons 402                                                                                           | OpenRouter Blog           |
| **Provisioning / Management API keys** | Membuat & mengelola key secara programatik untuk aplikasi yang membagikan atau merotasi key                                                           | Docs                      |
| Satu key per user                      | Bisa. Semua key memakai satu saldo akun, dan batas per key menjadi cap per user                                                                       | OpenRouter Help           |
| Pemakaian per key                      | Field `usage_daily/weekly/monthly`, `limit_remaining` bisa dipolling untuk menonaktifkan key mendekati cap                                            | OpenRouter Blog           |
| **BYOK**                               | Pakai key provider sendiri. Biaya 5% dari harga normal, gratis untuk 1 juta request BYOK pertama/bulan. Jika key gagal, fallback ke kredit OpenRouter | Docs BYOK                 |
| Harga                                  | Tanpa markup harga provider. Biaya platform dikenakan saat beli kredit (5,5% top-up kartu, min. $0,80). Request gagal tidak ditagih                   | OpenRouter Blog           |
| **Auto Exacto**                        | Menilai ulang provider tiap 5 menit (throughput, telemetri tool-call, benchmark). Aktif default untuk request yang memakai tools                      | OpenRouter Blog           |
| Lainnya                                | OAuth PKCE (pengguna menghubungkan akun OpenRouter-nya), MCP servers, usage accounting, user tracking, video/embeddings/reranker                      | Docs                      |

## 2. Apa yang tidak perlu dibangun sendiri

| Kebutuhan sanji (doc 10, 12, 14)        | Tanpa OpenRouter                        | Dengan OpenRouter                                                                        |
| --------------------------------------- | --------------------------------------- | ---------------------------------------------------------------------------------------- |
| Kuota model per workspace & batas biaya | Bangun metering + pricing table sendiri | **1 key per workspace** + `limit` bulanan + guardrail budget                             |
| Batas per agen (wizard Tambah agen)     | Hitung token per job                    | **1 key per agen** (limit harian/bulanan) atau `maxCost` per job di SDK                  |
| Ganti model Haiku/Sonnet/Opus/lainnya   | Integrasi tiap provider                 | Satu API, 400+ model                                                                     |
| Fallback saat provider down             | Retry manual                            | Routing + Auto Exacto                                                                    |
| Privasi data klien                      | Kontrak per provider                    | Guardrail **ZDR** + batasan provider                                                     |
| DLP / prompt injection                  | Bangun sendiri                          | Guardrail                                                                                |
| Approval tool                           | Bangun di runner                        | Tool approval SDK. **Tetap dihubungkan ke approval sanji**, supaya satu sumber kebenaran |
| BYO key pengguna                        | Simpan & enkripsi banyak jenis key      | OAuth PKCE atau BYOK di OpenRouter                                                       |

## 3. Arsitektur yang disarankan

```
                         ┌─────────── sanji control plane ────────────┐
Workspace dibuat  ──────▶│ Management API → buat KEY workspace          │
                         │   limit = kuota paket (mis. $X/bulan)        │
                         │   guardrail: ZDR, allowlist model, DLP       │
Agen dibuat       ──────▶│ Management API → buat KEY agen (opsional)    │
                         │   limit = batas biaya dari wizard            │
                         └───────────────┬──────────────────────────────┘
                                         │ key disimpan terenkripsi (vault)
         ┌───────────────────────────────┼───────────────────────────────┐
         ▼                               ▼                               ▼
  AGEN KERJA (cloud runner)      AGEN CODING — endpoint           AGEN CODING — ACP
  @openrouter/agent callModel    runner runtime OpenAI-compat     Claude Code / Codex
  tools = MCP/team-tools sanji   baseURL = openrouter.ai/api/v1   (tetap key provider
  stopWhen: steps + maxCost      key = workspace/agen/BYOK         pengguna, tidak via OR)
  approval tool → NeedItem sanji
```

**Pilihan sumber model per workspace** (Pengaturan → Model & API key):

1. **Kredit sanji (default):** sanji membuat key OpenRouter untuk workspace lewat Management API. Pemakaian dipotong dari kredit paket (riset 05). Pengguna tidak perlu tahu OpenRouter.
2. **Hubungkan akun OpenRouter (OAuth PKCE):** workspace memakai saldo OpenRouter sendiri. Tidak memakai kredit sanji.
3. **Tempel API key:** OpenRouter key, atau key provider langsung (Anthropic/OpenAI) untuk runtime endpoint.

## 4. Dampak ke keputusan sebelumnya

| Dokumen               | Perubahan                                                                                                                                                                                                                                                               |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Riset 03 (agen kerja) | Opsi A (ACP di mana-mana) direvisi: **agen kerja memakai `@openrouter/agent`** di cloud runner (loop, tool, approval, `maxCost` sudah ada, dan bisa ganti model). ACP tetap untuk agen coding. Jalur approval & journal sanji dipakai ulang lewat callback approval SDK |
| Riset 05 (harga)      | Kredit sanji = saldo OpenRouter + margin. OpenRouter tidak mark up harga provider, jadi margin sanji jelas. Biaya platform 5,5% saat top-up harus masuk hitungan                                                                                                        |
| Doc 10 (Pengaturan)   | Tab baru **Model & API key**. Guardrail (ZDR, allowlist model) dipetakan ke UI                                                                                                                                                                                          |
| Doc 12 (Tambah agen)  | Pilihan model tidak terbatas Haiku/Sonnet/Opus. Tampilkan preset "Cepat/Seimbang/Terbaik" yang dipetakan ke model OpenRouter. "Batas biaya per bulan" = `limit` key agen                                                                                                |
| Doc 14 (Runner)       | Runtime endpoint runner bisa diarahkan ke `https://openrouter.ai/api/v1`                                                                                                                                                                                                |

## 5. Risiko & mitigasi

| Risiko                                          | Mitigasi                                                                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| Agent SDK beta, bisa breaking                   | Pin versi, bungkus di adapter internal `sanji-agent-core`, conformance test tiap upgrade                                 |
| Ketergantungan satu vendor (OpenRouter)         | Adapter tipis. Runtime endpoint OpenAI-compatible tetap bisa ke provider langsung                                        |
| Data klien lewat pihak ketiga tambahan          | Guardrail **ZDR wajib ON** untuk workspace berlabel klien. Cantumkan OpenRouter di Kebijakan Privasi sebagai subprosesor |
| BYOK tidak dihitung ke budget secara default    | Nyalakan `include_byok_in_budgets` di guardrail workspace                                                                |
| Model Claude via OpenRouter vs aturan Anthropic | Pemakaian lewat API (bukan login langganan) diperbolehkan. Branding sanji tetap sendiri                                  |
| Latensi tambahan                                | Ukur saat pilot. Pakai region terdekat. Untuk agen kerja biasanya tidak signifikan dibanding durasi tool                 |

## 6. Sumber

- OpenRouter Blog: Announcements: https://openrouter.ai/blog/announcements/
- OpenRouter Blog: April Release Spotlight: https://openrouter.ai/blog/announcements/april-release-spotlight/
- OpenRouter Blog: Agent SDK with callModel: https://openrouter.ai/blog/tutorials/agent-sdk-with-callmodel/
- OpenRouter Docs: Agent SDK: https://openrouter.ai/docs/agent-sdk/overview
- npm `@openrouter/agent`: https://www.npmjs.com/package/@openrouter/agent
- OpenRouter Blog: Guardrails: https://openrouter.ai/blog/announcements/guardrails/
- OpenRouter Blog: Governing team AI spend: https://openrouter.ai/blog/insights/governing-team-ai-spend/
- OpenRouter Blog: Team spend controls setup: https://openrouter.ai/blog/tutorials/team-spend-controls-setup/
- OpenRouter Docs: BYOK: https://openrouter.ai/docs/use-cases/byok · https://openrouter.ai/docs/guides/overview/auth/byok
- OpenRouter Docs: Provisioning API keys: https://openrouter.ai/docs/features/provisioning-api-keys
- OpenRouter Help: one key per user: https://openrouter.zendesk.com/hc/en-us/articles/51680687417499
