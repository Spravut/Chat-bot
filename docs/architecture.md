# Architecture

Ниже зафиксирована архитектура Dating-бота с разделением на “быстрый” Bot Service и “тяжелый/фоновой” Worker Service.
На Этап 1 важно показать, как данные идут и где будут выполняться пересчеты рейтингов.

## 1) Поток обработки (логика end-to-end)

```mermaid
flowchart TD
  Telegram[Telegram Bot API] -->|"updates"| Bot[Bot Service (aiogram)]

  Bot -->|"profile CRUD"| Profiles[Profiles Service]
  Bot -->|"like/match/interaction"| Interactions[Interactions Service]
  Bot -->|"chat/dialogs"| Dialogs[Dialogs Service]
  Bot -->|"rank request"| Ranking[Ranking Service]

  Profiles -->|"read/write"| Postgres[(PostgreSQL)]
  Interactions -->|"read/write"| Postgres
  Dialogs -->|"read/write"| Postgres
  Ranking -->|"read/write"| Postgres

  Interactions -->|"publish interaction event"| MQ[RabbitMQ Event Queue]
  MQ --> Worker[Celery Workers (Ranking/Prefetch tasks)]

  Worker -->|"update ranking results"| Ranking
  Ranking -->|"persist computed scores"| Postgres

  Ranking -->|"prefetched candidates"| Redis[(Redis feed cache)]
  Worker -->|"cache warm-up"| Redis

  Bot -->|"photo upload"| Media[Media Service]
  Media -->|"store objects"| MinIO[MinIO (S3-compatible storage)]
```



## 2) Что делает Bot Service в потоке

- Получает апдейт (команда или действие пользователя).
- Записывает результат действия через соответствующие микросервисы (например, `Profiles`, `Interactions`, `Dialogs`), которые в итоге работают с PostgreSQL.
- Запускает фоновую задачу (или планирует ее запуск), если нужно обновить ранжирование/кандидатов.

## 3) Что делает Worker Service в потоке

- Читает события взаимодействий и/или текущие данные профиля.
- Пересчитывает рейтинги по уровням:
  - `level1` по заполненности анкеты и первичным предпочтениям
  - `level2` по лайкам/пропускам/матчам/инициированию диалогов и временным факторам
  - `level3` по весовой модели (комбинация `level1` и `level2` + рефералы)
- (На следующих этапах) формирует предранжированный список кандидатов и кладет в Redis для ускорения первой выдачи.

## 4) Почему разделяем Bot/Worker

- Telegram требует быстрого ответа, а пересчет рейтингов может быть дорогим.
- Фоновая обработка позволяет масштабировать нагрузку и проводить регулярные перерасчеты.

## 5) Минимальный набор “контрактов” между сервисами

На уровне документации зафиксируем:

- Какие события создаются в БД после действий пользователя (например `likes`, `matches`, `rating_events`).
- Какие типы задач будут выполняться воркером (например пересчет рейтинга конкретного пользователя, подготовка кандидатов для сессии).

Эти пункты обеспечивают, что архитектура остается понятной даже без полного кода на Этапе 1.