# DB Schema (Stage 1)

Ниже логическая схема данных для Dating-бота.
На Этапе 1 цель: показать, что база поддерживает сценарии анкет/взаимодействий и систему рейтинга (уровни 1-3).

## 1) ER Diagram

```mermaid
erDiagram
  users ||--o| user_profiles : "has"
  user_profiles ||--o{ user_interests : "uses"
  interests ||--o{ user_interests : "tagged_as"
  users ||--o{ user_photos : "uploads"

  users ||--o{ likes : "gives"
  users ||--o{ likes : "receives"
  users ||--o{ matches : "member_a"
  users ||--o{ matches : "member_b"

  users ||--o{ dialogs : "participant_a"
  users ||--o{ dialogs : "participant_b"
  dialogs ||--o{ messages : "contains"
  users ||--o{ messages : "sent_by"

  users ||--|| ratings : "has"
  users ||--o{ rating_events : "generates"

  users ||--o{ referrals : "inviter"
  users ||--o{ referrals : "referred"

  users {
    bigint id PK
    bigint telegram_id UK
  }
  user_profiles {
    bigint id PK
    bigint user_id FK UK
    int age
    string gender
    string seeking_gender
    string city
    int age_min
    int age_max
  }
  interests {
    bigint id PK
    string name UK
  }
  user_interests {
    bigint user_profile_id FK
    bigint interest_id FK
  }
  user_photos {
    bigint id PK
    bigint user_id FK
    string photo_url
    int sort_order
  }
  likes {
    bigint id PK
    bigint from_user_id FK
    bigint to_user_id FK
  }
  matches {
    bigint id PK
    bigint user_a_id FK
    bigint user_b_id FK
  }
  dialogs {
    bigint id PK
    bigint user_a_id FK
    bigint user_b_id FK
  }
  messages {
    bigint id PK
    bigint dialog_id FK
    bigint sender_id FK
    string content
    datetime created_at
  }
  ratings {
    bigint user_id PK,FK
    numeric level1_score
    numeric level2_score
    numeric level3_score
    datetime computed_at
  }
  rating_events {
    bigint id PK
    bigint user_id FK
    string event_type
    bigint target_user_id FK
    datetime created_at
    jsonb payload
  }
  referrals {
    bigint id PK
    bigint inviter_user_id FK
    bigint referred_user_id FK UK
    datetime created_at
  }
```

## 2) Таблицы и ключевые ограничения (концептуально)

### `users`
- `id` (PK)
- `telegram_id` (UK, not null) - уникальный идентификатор пользователя в Telegram.

### `user_profiles`
- `id` (PK)
- `user_id` (FK -> `users.id`, UK) - один профиль на пользователя.
- Поля анкеты для `level1` рейтинга:
  - возраст, пол, “кто ищется”, диапазон возраста (если используется)
  - география: city/country (минимум city; остальное по ТЗ)

### `interests` + `user_interests`
- `interests` - справочник интересов.
- `user_interests` - связь “профиль -> интересы”.
- Уникальность: `(user_profile_id, interest_id)`.

### `user_photos`
- `user_id` (FK -> `users.id`)
- `photo_url` (string/text)
- `sort_order` - порядок фото.
- Уникальность: `(user_id, sort_order)` (чтобы не было двух фото с одинаковой позицией).

### `likes`
- `from_user_id` (FK -> `users.id`)
- `to_user_id` (FK -> `users.id`)
- Уникальность пары: `(from_user_id, to_user_id)` - чтобы исключить дубли лайка.
- Практика: self-like лучше запретить:
  - либо `CHECK (from_user_id <> to_user_id)`
  - либо валидировать в приложении.

### `matches`
- Представляем матч как неориентированную пару: `user_a_id`, `user_b_id`.
- Уникальность: `(user_a_id, user_b_id)`
- Важно: хранить в каноничном порядке (например `user_a_id < user_b_id`) через:
  - `CHECK` constraint в PostgreSQL + приложение, которое подставляет порядок.

### `dialogs` + `messages`
- `dialogs` - разговор между двумя участниками.
- Уникальность диалога: `(user_a_id, user_b_id)` (в каноничном порядке).
- `messages` - сообщения внутри диалога.

### `ratings`
- `user_id` (PK,FK) - рейтинги храним по пользователю.
- `level1_score`, `level2_score`, `level3_score`
- `computed_at` - когда значения были посчитаны.

### `rating_events`
Журнал событий взаимодействия для `level2`:
- `user_id` - кто совершил действие (actor).
- `event_type` - например: `like`, `skip`, `match_created`, `dialog_started`, `dialog_message_sent` (перечень уточняется в реализации).
- `target_user_id` - кого затронуло действие (если применимо).
- `payload` (jsonb) - дополнительные параметры:
  - например timestamp активности по времени, агрегаты и т.п.

### `referrals`
Для `level3`:
- `inviter_user_id` - пригласивший
- `referred_user_id` - приглашенный
- Уникальность: `referred_user_id` (один пользователь может иметь только одного инвайтера).

## 3) Как схема поддерживает уровни рейтинга

- `level1`: читает поля из `user_profiles` + наличие/количество фото из `user_photos` (например, нормализация заполнения анкеты).
- `level2`: агрегирует события из `rating_events` и/или данные из `likes`, `matches`, `dialogs/messages`.
- `level3`: комбинирует `level1` и `level2` (весовая модель) + учитывает наличие `referrals`.

