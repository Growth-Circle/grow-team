# Plan V2: Halaman Terjadwal + identitas bot

Status: **DRAF untuk review Rama. Belum ada kode, commit, atau perubahan server.** Induk: `2026-10-18-viktor-adoption.md` fase V2 (keputusan induk Q3 = A: satu bot "Sanji Agent"). Dasar kode: `feature/team-mcp` `a3abb3cabb` (file yang dikutip identik di `eed41c862e`). Migrasi bernomor setelah V1 (`0845` dipakai V1, jadi V2 mulai `0846`).

## Bukti: bagaimana rutinitas jalan hari ini
- **Rutinitas "Cek pagi / Evaluasi 4 jam / Rekap harian" tidak ada di repo.** Pencarian seluruh repo hanya menemukan plan dan test Needs; tidak ada config runner, trigger `AgentJob`, unit systemd, atau cron. `services/grow-agent-runner/src` tidak punya penjadwal. Kesimpulan yang didukung bukti: dijalankan oleh **klien agen luar** dengan grant OAuth Rama lewat MCP Sanji, karena tool tulis berjalan "sebagai akun pemilik koneksi" (`deploy/grow-team/MCP.md`, bagian Izin; `zerver/views/mcp_server.py:141` `execute_tool(request, grant.user, ...)`). Bukti dampak: 132 dari 144 pesan sejak 8 Okt atas nama Rama, 13 run "MCP tidak memuat tool" tanpa peringatan (`2026-10-15-sanji-system-audit-plan.md:35-36`), mention massal membanjiri Needs (`2026-10-17-needs-v2.md:8`). **Belum terverifikasi dari repo:** klien dan penjadwalnya (Rama, mohon konfirmasi), serta peran bot "Opus" (id 14) dan "Catatan Rapat" (id 13), yang tidak terdefinisi di kode.
- **Kenapa identitasnya Rama dan tidak bisa langsung diganti:** `valid_grant` menolak pengguna bot (`zerver/lib/mcp_access.py:70`, `not grant.user.is_bot`). Rutinitas luar tidak bisa memakai identitas bot tanpa melonggarkan aturan auth itu.
- **Pola jadwal yang sudah ada di repo.** Hanya timer systemd host: `grow-team-agent-reconcile.timer` (15 detik), `grow-team-task-reminders.timer` (`*:0/15`) yang memanggil `deploy/grow-team/send-task-reminders.sh` dengan `timeout` + `flock` + exit 75 bila terkunci, dan idempotensi lewat `TaskReminderLog` unique(user,date) (`zerver/models/tasks.py:329-340`). Test deploy: `deploy/grow-team/tests/test_task_reminders_script.py`.
- **Pola "job agen terjadwal" yang sudah ada tetapi tidak dijadwalkan:** `send_room_digests` (`zerver/management/commands/send_room_digests.py`; keputusan Rama untuk tidak dijadwalkan: `2026-10-16-upbase-style-features.md:19`). `create_room_digest_job` memposting pesan sumber lewat bot agen lalu `create_job(trigger_kind="manual", allow_blocked=True)` (`zerver/lib/room_digests.py:137-175`); hasil job diposting oleh `job.profile.bot_user` (`zerver/lib/agent_results.py:460,573,683`). `AgentJob` unique (realm, requester, idempotency_key) mencegah job ganda (`zerver/models/agents.py:626`). Zona waktu: `realm_local_now` membaca `AgentRealmSettings.timezone`, default Asia/Jakarta (`zerver/lib/realm_clock.py`).
- **Batas yang harus diterima:** `TriggerKind` hanya `mention/direct_message/manual` (`agent_protocol.py:116`); runner memberi `mcpServers: []` (`acp-runtime.ts:227`), jadi **job agen dalam platform belum bisa memanggil tool Sanji** (papan tugas, catatan kartu) sampai gateway WP44 ada; `model_budget_state` selalu "ok" (`zerver/lib/model_budget.py`); biaya per job tidak diukur (`usage: "unknown"`, `agent_protocol.py:144`); pemilih model per job tidak ada (`ROOM_DIGEST_MODEL_PRESET` didefinisikan tetapi tidak ada pemanggil yang membacanya). `ScheduledMessage` Zulip hanya kirim-nanti satu kali, bukan berulang, jadi tidak dipakai ulang.

## Gap
Tidak ada model rutinitas, penjadwal, riwayat run, jeda/lanjut, template, atau identitas bot; rutinitas hidup di luar Sanji dan tak terlihat.

## Pendekatan
Rutinitas = **job agen dalam platform** yang dijadwalkan timer host (pola task-reminders), diposting oleh bot agen. **Tanpa mengubah `AgentJob`, `TriggerKind`, atau protokol runner** (milik Rama). Rutinitas luar yang ada didaftarkan dulu sebagai entri "luar" (hanya tampil), dan dipindahkan satu per satu. Test hanya di WSL, deploy lewat alur resmi server-gteam dengan rollback (memori `sanji-dev-env`, `sanji-live-branch`).

### U1. Model & matematika jadwal
**Files:** `zerver/models/routines.py` (re-export di `models/__init__.py`), migrasi `0846_agentroutine.py`, `zerver/lib/routine_schedule.py`, `zerver/tests/test_routine_schedule.py`.
- `AgentRoutine(AgentRecord)`: `title`(100), `instructions`(maks 4.000), `agent_profile` FK PROTECT, `owner` FK (`db_constraint=False`), `stream` FK + `topic`(60), `pattern_kind` (`every_minutes|every_hours|daily|weekdays|weekly|monthly`), `interval`, `time_of_day`, `weekdays` JSON, `month_day`, `timezone` (default `Asia/Jakarta`, `db_default`), `model_preset` (pilihan seperti `AgentProfile`), `enabled`, `paused_reason`, `next_run_at` (indeks), `last_run_at`, `last_status`, `executor` (`agent|external`).
- `AgentRoutineRun`: `routine` CASCADE, `scheduled_for`, `started_at`, `finished_at`, `status` (`queued|running|succeeded|failed|skipped_budget|skipped_busy|skipped_missed`), `job` FK SET_NULL, `result_message` (`db_constraint=False`), `error_code`, `cost_microunits` (null = tidak diketahui), **unique(`routine`,`scheduled_for`)**.
- **AC-V2-001 (Event):** When a routine is saved, the system shall compute `next_run_at` in the routine's timezone (WIB default) from its pattern.
- **AC-V2-002 (Unwanted):** If a monthly day does not exist in a month, then the run shall fall on that month's last day; if an interval is below 15 minutes or above its pattern bound, then the save shall be rejected with 400.
- Test: `test_routine_schedule` (semua pola, batas bulan, zona waktu non-WIB).

### U2. Penjadwal (timer host)
**Files:** `zerver/management/commands/run_scheduled_routines.py`, `zerver/lib/routines.py`, `deploy/grow-team/run-routines.sh`, `deploy/grow-team/systemd/grow-team-routines.{service,timer}` (setiap 1 menit, `flock` `routines.lock`, `timeout`, exit 75), `deploy/grow-team/tests/test_routines_script.py`, `zerver/tests/test_routines.py`.
Per realm di dalam `agent_realm()`: ambil rutinitas due dengan `select_for_update(skip_locked=True)` (maks 20), `get_or_create` `AgentRoutineRun` pada `scheduled_for`, maju ke kemunculan berikutnya **setelah sekarang** (tanpa menyusul beruntun), buat job lewat `create_job(trigger_kind="manual", idempotency_key=uuid5(routine.id, scheduled_for), allow_blocked=True)` setelah pesan sumber diposting oleh bot agen di `stream/topic` (meniru `create_room_digest_job`). `AgentBusy` berarti coba lagi menit berikutnya tanpa maju. Konteks: pesan terbaru ruang ditambah ringkasan papan teks yang dibangun server (karena belum ada tool Sanji untuk agen, lihat Q2).
- **AC-V2-003 (Unwanted):** If the scheduler runs twice for the same `scheduled_for`, or two instances overlap, then exactly one run and one job shall exist.
- **AC-V2-004 (Unwanted):** If the host was down and several occurrences were missed, then the system shall run at most the latest one within a 2-hour grace and record the rest as `skipped_missed`.
- **AC-V2-005 (Event):** When the timer fires, the system shall only act on routines with `enabled` and `next_run_at <= now`.
- **AC-V2-006 (Unwanted):** If the command exceeds its timeout or the lock is held, then it shall exit without side effects and without affecting the app.

### U3. Riwayat run, biaya, kegagalan
**Files:** `zerver/lib/routines.py` (rekonsiliasi di command yang sama: job terminal memperbarui run), endpoint `GET /json/routines/<id>/runs`, `web/src/routine_history.ts`.
- **AC-V2-007 (State):** While a run's job is not terminal, its run shall show `running`; when the job ends, the run shall store `finished_at`, status, `result_message` link and cost (or "unknown" when not measured).
- **AC-V2-008 (Event):** When a run fails, the system shall DM the owner via Notification Bot with the reason; after 3 consecutive failures it shall pause the routine (`paused_reason="failures"`).
- **AC-V2-009 (State):** While usage is unmeasured, the history shall display "unknown", never zero (selaras AC-V5-003).

### U4. Identitas bot & migrasi rutinitas
**Files:** `zerver/management/commands/ensure_builtin_agents.py` (builtin "Sanji Agent", role `custom`, ditemukan lewat `live_builtin`), `zerver/lib/routines.py`, command `import_external_routines`.
Pengirim = `agent_profile.bot_user`; default profil rutinitas = "Sanji Agent" (keputusan induk A), jadi `agent_results.py` tidak diubah. Migrasi per rutinitas: (1) Rama menyerahkan prompt dan jadwal sekarang; (2) buat `AgentRoutine`; (3) jalan paralel 2 hari dengan rutinitas luar tetap hidup namun ke topik uji; (4) beralih, lalu rutinitas luar dimatikan oleh Rama; (5) revoke grant Rama yang dipakai rutinitas. Rutinitas yang butuh tool papan (Cek pagi, Evaluasi 4 jam) tetap `executor=external` sampai ada jalur tool (Q2).
- **AC-V2-010 (State):** While a routine runs in the platform, every post it makes shall be sent by its agent bot user and never by a human account.
- **AC-V2-011 (Unwanted):** If the agent profile is archived, paused or not ready, then the run shall be `skipped` with `error_code` and the owner notified, and no post shall be made under another identity.

### U5. API
**Files:** `zerver/views/routines.py`, `zproject/urls.py`, OpenAPI + changelog, `zerver/tests/test_routines.py`.
`GET/POST /json/routines`, `GET/PATCH/DELETE /json/routines/<id>`, `POST .../pause|resume|run`. Izin: pembuat butuh `agent_task` dan `accessible_profiles`; edit oleh owner atau admin; room harus bisa dibaca owner. "Run now" dibatasi 1 per menit per rutinitas.
- **AC-V2-012 (Unwanted):** If the actor is a guest, cannot read the target channel, or cannot use the agent, then create and update shall be rejected and nothing saved.
- **AC-V2-013 (Event):** When Run now is used, the system shall start one job with a fresh key and a run row marked manual, without changing `next_run_at`.

### U6. UI
**Files:** `web/src/routines_ui.ts`, `routines_data.ts`, `routine_form.ts`, `web/templates/routines.hbs`, `routine_form.hbs`, `hashchange.ts`, `left_sidebar_navigation_area.ts`, CSS, node tests, string id. Memakai `PageHeader/EmptyState` dan formatter V0.
Daftar: judul, agen (nama), ruang/topik, pola ("Setiap hari pukul 09.00 WIB"), run berikutnya, hasil terakhir, Jeda/Lanjutkan, Jalankan sekarang, Riwayat. Kalender = grid 7 hari hanya bila ada rutinitas (kosong = satu empty state, hindari UX-24). Form manual: judul, instruksi, pola, jam **24-jam via dua pilihan jam/menit** (hindari UX-27), ruang/topik, agen, model. Aksi "Hapus" satu kali dengan konfirmasi (UX-23).
- **AC-V2-014 (State):** While the page is open, it shall list every routine including external ones (read-only, labelled "Dijalankan di luar Sanji"), with next run and last result.
- **AC-V2-015 (Event):** When a user pauses or resumes a routine, the state shall update live and survive reload; resuming shall recompute `next_run_at` from now.
- **AC-V2-016 (State):** While at 390px, light or dark, the form and list shall fit and work by keyboard.

### U7. Template (copy Indonesia)
**Files:** `zerver/lib/routine_templates.py` (`gettext_lazy`), endpoint `GET /json/routines/templates`.
- **Brief pagi** (harian 08.00): "Rangkum yang perlu diperhatikan hari ini: kartu jatuh tempo, kartu macet, dan keputusan yang menunggu. Maksimal 8 butir, tanpa mention massal."
- **Ringkasan akhir hari** (hari kerja 17.30): "Tulis apa yang selesai hari ini, apa yang masih terbuka, dan satu hal untuk besok."
- **Rangkuman mingguan tim** (Jumat 16.00): "Rangkum minggu ini per orang: selesai, berjalan, terhambat. Tutup dengan tiga prioritas minggu depan."
- **Cek klien** (hari kerja 10.00): "Periksa percakapan klien di ruang ini: pesan yang belum dibalas lebih dari 1 hari dan janji yang jatuh tempo."
- **AC-V2-017 (Event):** When a user picks a template, the form shall open prefilled and editable, and nothing shall be created until the user saves.

### U8. Kait budget (untuk V5)
**Files:** `zerver/lib/routine_budget.py`, dipanggil di U2 sebelum `create_job`.
`gate(routine) -> ok|warn|exceeded` membungkus `model_budget_state(realm)` sekarang; V5 menggantinya dengan per-agen/per-rutinitas tanpa mengubah penjadwal.
- **AC-V2-018 (Unwanted):** If the gate returns `exceeded`, then the run shall be `skipped_budget`, the routine paused with `paused_reason="budget"`, and the owner notified once.
- **AC-V2-019 (Event):** When the gate returns `warn`, the run shall proceed and the owner shall get one notice per month.

## Migrasi, rollback, test
Dua tabel baru, additive, semua kolom bertipe `db_default`/null; tidak menyentuh `AgentJob`. **Rollback:** nonaktifkan timer (`systemctl disable --now grow-team-routines.timer`), revert kode; rutinitas luar Rama tidak pernah dimatikan sebelum parity, jadi layanan lama tetap jalan. Gate: `./tools/test-backend zerver.tests.test_routine_schedule zerver.tests.test_routines zerver.tests.test_agents zerver.tests.test_room_digests zerver.tests.test_task_reminders`, `deploy/grow-team/tests/test_routines_script.py`, `./tools/test-js-with-node` penuh, lint, mypy, Puppeteer (terang/gelap, 1280 dan 390, keyboard), deploy bergerbang (memori < 20 PSI, beban < 10, login 200), smoke: satu rutinitas uji tiap 15 menit ke topik uji.

## Risiko (MEDIUM)
Pesan otomatis ke orang (spam bila salah jadwal, mitigasi unique + batas 2 jam + mode uji); unit systemd baru di server prod bersama; beban `agent_transaction` global lock saat banyak rutinitas bersamaan; kualitas hasil rutinitas dalam platform lebih rendah selama agen tak punya tool papan.

## Pertanyaan untuk Rama (rekomendasi di depan)
1. Konfirmasi: rutinitas sekarang dijalankan oleh klien mana dan di mana jadwalnya? Apa peran bot Opus (14) dan Catatan Rapat (13)? **A (rek.)** Rama jelaskan, lalu didaftarkan sebagai `external`.
2. Tool untuk job agen: **A (rek.)** V2 jalan dengan konteks pesan + ringkasan papan buatan server; rutinitas papan tetap `external` sampai WP44 · B V2 menunggu gateway · C izinkan grant MCP untuk bot (melonggarkan `mcp_access.py:70`, tidak disarankan).
3. Bot: **A (rek.)** builtin "Sanji Agent" (role `custom`) sebagai pengirim default · B bot biasa tanpa profil agen (butuh hook di `agent_results.py`).
4. Model per rutinitas: **A (rek.)** simpan `model_preset` dan tampilkan, pakai profil sampai Rama menyediakan pemilih per job · B Rama tambah field di deskriptor sekarang.
5. Interval minimum "setiap N menit": **A (rek.)** 15 menit · B 5 menit · C bebas.
6. Penjadwal: **A (rek.)** timer host tiap menit + `manage.py` · B di dalam runner.
7. Waktu cutover rutinitas luar dan siapa mematikannya: **A (rek.)** Rama, setelah 2 hari parity.
