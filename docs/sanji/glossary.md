# Sanji glossary

One name for each feature, in English and in Indonesian. Use these
names in every UI string, help text, message the system sends, and
chat with the team. When a string uses another word for the same
thing, change the string, not the glossary. A word that is missing here
and that names a feature should be added here in the same change that
puts it on screen.

`tools/grow-team/check-id-translations` fails when a string on a screen
we own has no Indonesian text. It does not check which word was used;
this file does that, in review.

## Names

| Feature                                                                            | English (UI)     | Indonesian (UI)                       | Do not write             |
| ---------------------------------------------------------------------------------- | ---------------- | ------------------------------------- | ------------------------ |
| A place to talk about one subject (sidebar)                                        | Room(s)          | Ruang                                 | Saluran, Grup            |
| The same place, in create and settings dialogs                                     | Channel          | Channel                               | Stream                   |
| A conversation inside a room                                                       | Topic            | Topik                                 | Utas, Thread             |
| One item on the task board                                                         | Card             | Kartu                                 | Card, Tiket              |
| Work that a card or an agent stands for                                            | Task             | Tugas                                 | Pekerjaan (for one task) |
| The board with columns                                                             | Task board       | Papan tugas                           | Tugas, Kanban            |
| One column of the board                                                            | Column           | Kolom                                 | Lajur                    |
| A person's open cards, by deadline                                                 | My tasks         | Tugas saya                            | Tugasku                  |
| The cards a person picked for today                                                | Today plan       | Rencana hari ini                      | Rencana harian           |
| Put a card in today's plan                                                         | Add to today     | Tambah ke hari ini                    | Masukkan ke hari ini     |
| What waits on this person in every room                                            | Needs you        | Perlu kamu                            | Butuh kamu, Inbox        |
| An at-sign message to a person                                                     | Mention          | Mention (noun and verb: "me-mention") | Sebutan, Disebut, Tag    |
| A request an agent makes before it acts                                            | Approval         | Persetujuan                           | Approval                 |
| Say yes to an approval                                                             | Approve          | Setujui                               | Approve                  |
| A question an agent asks, with choices                                             | Decision         | Keputusan                             | Pilihan                  |
| A message kept at the top of a channel                                             | Pin              | Pin ("Dipin", "Lepas pin")            | Sematkan                 |
| An AI team member                                                                  | Agent            | Agen                                  | Agent, Bot               |
| A reusable document of steps that the team follows, and that an agent can be given | Playbook         | Playbook                              | Skill, SOP               |
| One saved version of a playbook                                                    | Revision         | Revisi                                | Version, Versi           |
| A playbook for the whole workspace, and the tab that lists them                    | Shared           | Bersama                               | Publik, Tim              |
| A playbook that only its owner can open, and the tab that lists them               | Mine             | Milikku                               | Pribadi, Personal        |
| Move a playbook out of the list, and bring it back                                 | Archive, Restore | Arsipkan, Pulihkan                    | Hapus, Delete            |
| A routine that runs on a schedule                                                  | Scheduled        | Terjadwal                             | Otomatis, Rutin          |
| A message that comes back later                                                    | Reminder         | Pengingat                             | Reminder                 |
| The person a card is given to                                                      | Responsible      | Penanggung jawab                      | PIC, Owner               |
| The person who checks a card                                                       | Reviewer         | Peninjau                              | Reviewer                 |
| A person in a room                                                                 | Member           | Anggota                               | Pelanggan, Subscriber    |
| The whole organization's space                                                     | Workspace        | Workspace                             | Organisasi               |
| The card has not got what it needs                                                 | Blocked          | Terhambat                             | Diblokir                 |
| Cancel the last action                                                             | Undo             | Urungkan                              | Undo                     |

## Rules

- **Card or task.** A card is the item you move on the board. A task is
  the work, and the name of the pages that list it ("My tasks", "New
  task"). A button that makes a card from nothing says "New task".
- **Room or channel.** English uses both: the sidebar says "Rooms" and
  the create and settings dialogs say "channel". Indonesian follows the
  English word on the same screen. This is an open product decision
  (see below).
- **Mention is not translated.** The team already says "mention" as a
  noun and a verb. "Mentions" is the tab on Needs you and reads
  "Mention".
- **Playbook is not translated.** The team says "playbook" in
  Indonesian too. A playbook is never called a skill or an SOP, in the
  page, in the API docs or in a message. "Mine" and "Shared" name the
  two tabs and the two choices when a playbook is made; the API calls
  them and .
- **Numbers, money, dates, times.** Never format by hand. The web app
  uses `web/src/sanji_format.ts` and the server uses
  `zerver/lib/sanji_format.py`: "6,9 rb", "Rp 1.500.000", "14 Okt",
  "13.30" (24-hour, as the person set it, in their time zone).
- **Placeholders stay.** `{count}`, `{date}` and the other braces are
  copied as they are. Do not translate what is inside.

## Open decisions

1. **Room or channel in English.** One word for the sidebar, the dialogs
   and the messages the bots send. Indonesian then gets `Ruang` for it
   everywhere, and about 30 existing strings change from "channel".
2. **Card or task in English.** "Add card", "New task", "Show 3 older
   cards" and "3 tasks" refer to the same thing on one page.
3. **Today plan, Today's plan.** The title says "Today plan", the rows
   say "In today's plan" and "Planned for today".

## Found when this file was written

Fixed in this change (translations, `locale/id/pending/v0.json`):

- "Card" and "card" in Indonesian text became "Kartu" and "kartu"
  (Needs you, the carry-over prompt, the reminder message).
- The Needs you tab "Mentions" read "Disebut" while the text under it
  said "Mention"; it reads "Mention" now.
- "Undo" had no Indonesian text; it reads "Urungkan".
- "Notify all channel subscribers" said "pelanggan"; it says "anggota".
- 97 strings of the task board, the card panel, notes, the channel
  create form and the server messages had no Indonesian text at all.

Left, and why:

- "Channel" and "Ruang" are both in use (see open decision 1).
- The old pending files `r4.json` and `wp32.json` give different texts
  for the same eight Needs you strings ("Approval" against
  "Persetujuan", "Tandai beres" against "Tandai selesai"). The catalog
  holds the `r4.json` texts, which are the ones the app shows, so the
  `wp32.json` lines do nothing; they should be removed by their owner.
- `wp27.json` says "Tugas" for "Task board" and "Pintasan keyboard" for
  "Keyboard shortcuts", where the catalog says "Papan tugas" and
  "Shorcut keyboard" (a typo). The catalog texts show.
- Strings in agent, MCP and reminders-overlay code are not in the
  guard's path list yet; they belong to the agent platform work.
