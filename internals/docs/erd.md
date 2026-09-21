# ERD Grow Team

Status: diagram pertama adalah model source Zulip yang terverifikasi dari `zerver/models`. Diagram kedua adalah proposal AI untuk ide inti Grow Team, tetapi belum ada migrasi, tabel, atau agent runtime.

## Model aktual

```mermaid
erDiagram
  REALM {
    int id PK
    string string_id
  }
  USER_PROFILE {
    int id PK
    int realm_id FK
  }
  STREAM {
    int id PK
    int realm_id FK
    int recipient_id FK "nullable one-to-one"
  }
  DIRECT_MESSAGE_GROUP {
    int id PK
    int recipient_id FK "nullable one-to-one"
  }
  RECIPIENT {
    int id PK
    int type
    int type_id
  }
  SUBSCRIPTION {
    int id PK
    int user_profile_id FK
    int recipient_id FK
  }
  MESSAGE {
    int id PK
    int realm_id FK
    int sender_id FK
    int recipient_id FK
  }
  USER_MESSAGE {
    int id PK
    int user_profile_id FK
    int message_id FK
  }
  REACTION {
    int id PK
    int user_profile_id FK
    int message_id FK
  }
  ATTACHMENT {
    int id PK
    int realm_id FK
    int owner_id FK
  }
  REALM ||--o{ USER_PROFILE : owns
  REALM ||--o{ STREAM : owns
  REALM ||--o{ MESSAGE : scopes
  STREAM o|--o| RECIPIENT : nullable_stream_recipient
  DIRECT_MESSAGE_GROUP o|--o| RECIPIENT : nullable_dm_recipient
  USER_PROFILE ||--o{ SUBSCRIPTION : has
  RECIPIENT ||--o{ SUBSCRIPTION : has
  USER_PROFILE ||--o{ MESSAGE : sends
  RECIPIENT ||--o{ MESSAGE : receives
  MESSAGE ||--o{ USER_MESSAGE : delivers
  USER_PROFILE ||--o{ USER_MESSAGE : reads
  MESSAGE ||--o{ REACTION : has
  USER_PROFILE ||--o{ REACTION : creates
  MESSAGE }o--o{ ATTACHMENT : uses
  REALM ||--o{ ATTACHMENT : owns
  USER_PROFILE ||--o{ ATTACHMENT : owns
```

`Recipient` tidak memiliki foreign key langsung ke `Realm`. Ia memakai pasangan
polimorfik `type` dan `type_id`: stream atau direct-message group. Pasangan ini
bukan foreign key database. `Stream.recipient` dan `DirectMessageGroup.recipient`
adalah OneToOne nullable, sehingga kedua sisi relasi dapat tidak ada (`o|--o|`).
Realm pesan berasal dari `Message.realm`; batas realm untuk recipient diturunkan
melalui targetnya.
`Subscription` menghubungkan pengguna dan recipient. `UserMessage` menyimpan
status per pengguna. Relasi rinci tetap mengikuti source.

## Model AI yang diusulkan

```mermaid
erDiagram
  AI_JOB {
    uuid id PK
    int realm_id "reference Realm.id"
    int requester_user_id "reference UserProfile.id, nullable"
    int bot_user_id "reference UserProfile.id, nullable"
    uuid ai_model_id FK
    string idempotency_key
    string status
    int attempt_count
    datetime next_attempt_at
  }
  AI_CONTEXT_REF {
    uuid id PK
    uuid ai_job_id FK
    int realm_id "reference Realm.id"
    int message_id "reference Message.id, nullable"
    int stream_id "reference Stream.id, nullable"
    int recipient_id "reference Recipient.id, nullable"
    string permission_snapshot
  }
  AI_APPROVAL {
    uuid id PK
    uuid ai_job_id FK
    int realm_id "reference Realm.id"
    int decided_by_user_id "reference UserProfile.id, nullable"
    string action_type
    string status
    datetime decided_at
  }
  AI_AUDIT_EVENT {
    uuid id PK
    uuid ai_job_id FK
    int realm_id "reference Realm.id"
    int actor_user_id "reference UserProfile.id, nullable"
    string event_type
    datetime occurred_at
  }
  AI_MODEL {
    uuid id PK
    string gateway_model
    string allowlist_version
    boolean enabled
  }
  REALM ||--o{ AI_JOB : proposed_boundary
  USER_PROFILE o|--o{ AI_JOB : requests_or_bots
  AI_MODEL ||--o{ AI_JOB : selected_for
  USER_PROFILE o|--o{ AI_APPROVAL : decides
  AI_JOB ||--o{ AI_CONTEXT_REF : reads
  AI_JOB ||--o| AI_APPROVAL : requires
  AI_JOB ||--o{ AI_AUDIT_EVENT : records
```

Proposal sidecar storage: simpan referensi konteks, bukan salinan pesan penuh bila
tidak perlu. Field `int` merujuk ke primary key integer ORM Zulip saat ini;
ia tidak menyatakan foreign key lintas database sudah ada. Validasi izin pada
enqueue dan sebelum eksekusi. Ini bukan migrasi ORM Zulip dan belum ada tabel
runtime. Jangan membuatnya sebelum FR-08–FR-19 dan review keamanan disetujui.
