# 17 · Model & API key (OpenRouter)

Status: spesifikasi halaman, belum kontrak. Konflik: lihat peta dokumen.

Tanggal: 24 September 2026 · Prototipe: **Pengaturan → Model & API key** (khusus Owner/Admin). Dasar: [riset 06](../research/06-openrouter.md) · harga: [riset 05](../research/05-saran-harga.md).

## Tujuan
Satu tempat untuk menentukan **siapa yang membayar model**, **model apa yang dipakai** agen, **guardrail data**, dan **batas biaya** per workspace & per agen, dengan memanfaatkan fitur OpenRouter (Management API keys, guardrails, BYOK, OAuth PKCE, Agent SDK) daripada membangun dari nol.

## Isi halaman (4 kartu)

### 1. Sumber model
Tiga kartu pilihan (terpilih = kuning muda + bayangan):
| Pilihan | Perilaku | Panel di bawahnya |
|---|---|---|
| **Kredit sanji** (PALING MUDAH, default) | sanji membuat key OpenRouter workspace lewat Management API. Limit = kuota paket | Chip hijau: `sk-or-v1-…c9f2` + penjelasan |
| **Akun OpenRouter** | OAuth PKCE ke akun OpenRouter pengguna. Saldo OpenRouter mereka yang dipakai | Tombol **Hubungkan OpenRouter** ⇄ **Putuskan** + saldo |
| **API key sendiri** | Tempel key OpenRouter / Anthropic / OpenAI | Pil provider, input password mono, **Uji & simpan** (validasi panjang → toast "…abcd disimpan"), catatan vault |

### 2. Preset model
Pengguna (di wizard Tambah agen) hanya melihat **Cepat / Seimbang / Terbaik**. Admin memetakan tiap preset ke model OpenRouter:
| Preset | Pilihan contoh | Penggunaan |
|---|---|---|
| Cepat (RINGAN) | `anthropic/claude-haiku`, `google/gemini-flash`, `openai/gpt-mini`, `nvidia/nemotron-lightning` | Ringkasan, routing, balas pesan |
| Seimbang (DEFAULT) | `anthropic/claude-sonnet`, `openai/gpt`, `google/gemini-pro` | Laporan, deck, riset |
| Terbaik (MAHAL) | `anthropic/claude-opus`, `openai/gpt-pro` | Analisis rumit |
- Klik model → preset berganti + toast "berlaku mulai job berikutnya". Wizard Tambah agen langsung menampilkan nama model baru di kartu pilihan model.
- Toggle **Fallback provider otomatis** (default aktif).
- *Nama model di prototipe disederhanakan. Di produksi ambil dari `GET /models` OpenRouter dan pin versi.*

### 3. Guardrail (dipetakan ke Guardrails OpenRouter)
| Setting | Default | Catatan |
|---|---|---|
| Zero Data Retention | **Wajib** | Ruang tipe Klien selalu wajib meskipun toggle opsional |
| Provider yang diizinkan | Anthropic, OpenAI, Google, NVIDIA | Multi-pilih |
| Perlindungan prompt injection | Aktif | Untuk dokumen/web yang dibaca agen |
| DLP | Mati | Samarkan NIK, kartu, rekening |
| Hitung pemakaian key sendiri ke budget | **Dihitung** | `include_byok_in_budgets` |

### 4. Budget & key per agen
- Meter pemakaian workspace ("Rp412rb / Rp1 jt bulan ini") + pil batas (Rp500rb / Rp1 jt / Rp5 jt / Tanpa batas). Melebihi batas → request ditolak 402, Owner diberi tahu di 80%.
- Tabel per agen: nama + key tersamar, bar pemakaian/limit (oranye jika > 80%), tanggal reset, tombol **Rotasi**.
- Limit key agen = "Batas biaya per bulan" yang diisi di wizard Tambah agen langkah 4.

## Hubungan dengan halaman lain
| Halaman | Perubahan |
|---|---|
| Tambah agen (12) | Kartu model menampilkan `Preset · model` dari halaman ini. Label budget: "= limit key agen di OpenRouter" |
| Tambah agen, harness coding | "Endpoint sendiri" menyarankan `https://openrouter.ai/api/v1` |
| Tagihan (10) | Kredit sanji = saldo OpenRouter + margin. Biaya platform OpenRouter 5,5% saat top-up masuk hitungan harga |
| Agen kerja (riset 03/06) | Dijalankan dengan `@openrouter/agent` `callModel` + `stopWhen: [stepCountIs, maxCost]`. Tool approval SDK → `NeedItem` sanji |

## Kebutuhan backend
| Entitas | Field |
|---|---|
| `ModelSource` | `realm_id`, `mode` (`sanji_credit`/`openrouter_oauth`/`byok`), `provider` (openrouter/anthropic/openai), `credential_ref` (vault), `or_workspace_id` |
| `ModelPreset` | `realm_id`, `preset` (fast/balanced/best), `model_slug`, `fallback_enabled` |
| `ModelGuardrail` | `realm_id`, `zdr`, `provider_allowlist[]`, `prompt_injection`, `dlp`, `include_byok_in_budgets`, `or_guardrail_id` |
| `AgentModelKey` | `agent_id`, `or_key_hash`, `limit_monthly`, `usage_cache`, `rotated_at` |

| Method | Endpoint | Fungsi |
|---|---|---|
| GET/PUT | `/api/v1/sanji/settings/model-source` | Ubah mode. OAuth: mulai PKCE → callback simpan key user |
| POST | `/api/v1/sanji/settings/model-source/test` | Uji key (panggilan ringan) sebelum disimpan |
| GET/PUT | `/api/v1/sanji/settings/model-presets` | Pemetaan preset. Daftar model dari OpenRouter `GET /models` (cache 1 jam) |
| GET/PUT | `/api/v1/sanji/settings/guardrails` | Sinkron ke OpenRouter Guardrails via Management API |
| GET/PUT | `/api/v1/sanji/settings/budget` | Limit workspace → `limit` key workspace / budget workspace OpenRouter |
| POST | `/api/v1/sanji/agents/{id}/model-key/rotate` | Buat key baru, cabut lama |
| — | Worker | Polling `usage_monthly`/`limit_remaining` tiap key (±5 mnt) → meter UI, notifikasi 80%, kartu job BERHENTI `budget_exceeded` saat 402 |

**Aturan:** key OpenRouter **tidak pernah** dikirim ke runner/agen di luar cloud runner sanji. Runner coding dengan endpoint OpenRouter memakai key milik pengguna di env runner (sesuai doc 14). Agent SDK dipin versinya (beta) dan dibungkus adapter `sanji-agent-core`.

## Pertanyaan terbuka
- Jika saldo akun OpenRouter pengguna habis: fallback ke kredit sanji otomatis, atau berhenti?
- Perlu preset ke-4 "Hemat" (model open-weight murah) untuk paket Gratis?
