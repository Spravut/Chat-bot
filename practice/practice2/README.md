# Практика 2 — RabbitMQ vs Redis: сравнение брокеров сообщений

## Структура файлов

```
practice2/
├── benchmark.py        # весь код: producer, consumer, monitor, runner
├── docker-compose.yml  # RabbitMQ + Redis + benchmark контейнер
├── Dockerfile          # образ для benchmark
├── requirements.txt    # pika, redis-py
├── REPORT.md           # отчёт с результатами и выводами
└── README.md           # этот файл
```

---

## Как запустить

```bash
cd practice/practice2

# Запустить всё (брокеры + бенчмарк)
docker compose up --build

# Только логи бенчмарка
docker compose logs -f benchmark
```

Полный прогон (24 теста × 30 с) занимает **≈ 25 минут**.

```bash
# Остановить
docker compose down

# RabbitMQ Management UI
# http://localhost:15672  →  guest / guest

# Очистить всё включая данные
docker compose down -v
```

---

## Разбор `benchmark.py` — каждая функция досконально

### Константы и переменные окружения

```python
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "localhost")
REDIS_HOST    = os.getenv("REDIS_HOST",    "localhost")
```

Адреса брокеров берутся из переменных окружения. Когда код запускается в Docker — docker-compose передаёт `RABBITMQ_HOST=rabbitmq` и `REDIS_HOST=redis` (имена сервисов из compose-сети). Если запускать локально без Docker — подтянется `localhost` по умолчанию.

```python
TEST_DURATION = 30    # секунд на один прогон
WARMUP_SECS   = 2     # первые N секунд не считаются в latency
STREAM_MAXLEN = 5_000 # максимум записей в Redis Stream
```

`WARMUP_SECS` нужен потому что в самом начале теста producer и consumer только стартуют, соединения устанавливаются — латентность там неестественно высокая и испортит статистику. Первые 2 секунды просто выбрасываются.

`STREAM_MAXLEN` — защита от OOM. Redis хранит стримы в памяти, при `XADD` с `maxlen=5000` старые записи автоматически вытесняются когда стрим переполняется. Без этого при 10,000 msg/s за 30 секунд в памяти окажется 300,000 записей.

```python
MSG_SIZES    = [128, 1_024, 10_240, 102_400]
TARGET_RATES = [1_000, 5_000, 10_000]
BROKERS      = ["rabbitmq", "redis"]
```

Матрица тестов: 4 размера × 3 скорости × 2 брокера = **24 прогона**. Чтобы быстро прогнать только нужные комбинации — достаточно поменять эти списки.

---

### `make_payload(size: int) -> str`

```python
def make_payload(size: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=size))
```

Генерирует случайную строку ровно `size` символов — это и есть тело сообщения. Случайность важна: реальные данные несжимаемы, а некоторые брокеры или сетевые стеки могут незаметно сжимать повторяющийся контент, что исказит результаты замера пропускной способности.

---

### `fmt_size(b: int) -> str`

```python
def fmt_size(b: int) -> str:
    if b >= 1_048_576:
        return f"{b // 1_048_576}MB"
    if b >= 1_024:
        return f"{b // 1_024}KB"
    return f"{b}B"
```

Чисто утилитарная функция для красивого вывода: `1024 → "1KB"`, `102400 → "100KB"`. Используется только в print-ах и таблице.

---

### `class RateLimiter`

```python
class RateLimiter:
    def __init__(self, rate: int):
        self._interval = 1.0 / rate if rate > 0 else 0.0
        self._next = time.monotonic()

    def wait(self) -> None:
        if self._interval == 0.0:
            return
        self._next += self._interval
        sleep = self._next - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)
```

**Зачем нужен.** Без ограничения скорости producer будет слать сообщения настолько быстро, насколько позволяет сеть и CPU — и мы не сможем сравнить поведение брокеров при одинаковой нагрузке. Rate limiter гарантирует что producer отправляет ровно `N` сообщений в секунду.

**Как работает.** Вместо банального `time.sleep(1/rate)` после каждого сообщения (что накапливает дрейф из-за overhead самой отправки), лимитер работает по монотонным часам:
- `_next` — момент времени когда нужно отправить _следующее_ сообщение
- каждый вызов `wait()` сдвигает `_next` на один интервал вперёд
- если реальное время уже опережает `_next` (отправка заняла дольше чем интервал) — sleep не вызывается вовсе, следующее сообщение уходит немедленно

Это называется **token bucket** — ошибки не накапливаются, временные задержки компенсируются на следующих итерациях. `time.monotonic()` вместо `time.time()` потому что монотонные часы не прыгают при синхронизации системного времени.

---

### `class RunResult`

```python
@dataclass
class RunResult:
    broker:       str
    msg_size:     int
    target_rate:  int
    duration:     int = TEST_DURATION
    sent:         int = 0
    received:     int = 0
    errors:       int = 0
    latencies_ms: List[float] = field(default_factory=list)
    peak_backlog: int   = 0
    avg_backlog:  float = 0.0
    peak_mem_mb:  float = 0.0
```

Контейнер данных одного прогона. Используется как общая память между тремя тредами — producer пишет `sent` и `errors`, consumer пишет `received` и `latencies_ms`, monitor пишет `peak_backlog` и `peak_mem_mb`. Тредов три, но каждый пишет в свои поля — конфликтов нет.

`field(default_factory=list)` — стандартный способ задать список как default в dataclass. Нельзя написать просто `latencies_ms: List[float] = []` потому что тогда все экземпляры класса делили бы один и тот же объект списка.

**Свойства (`@property`) вычисляются на лету:**

```python
@property
def lost(self) -> int:
    return max(0, self.sent - self.received)
```
Количество потерянных сообщений. `max(0, ...)` на случай если consumer успел принять чуть больше из предыдущего теста (race condition при старте).

```python
@property
def throughput(self) -> float:
    return self.received / self.duration
```
Реальная пропускная способность — сколько сообщений в секунду _реально обработано_ (не отправлено). Именно received, потому что sent может быть больше если брокер не справлялся.

```python
@property
def avg_ms(self) -> float:
    return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0
```
Средняя задержка. Считается честно — среднее арифметическое всех замеров после прогрева.

```python
@property
def p95_ms(self) -> float:
    s = sorted(self.latencies_ms)
    return s[int(len(s) * 0.95)]
```
95-й перцентиль. Сортируем все задержки и берём значение на позиции 95%. Это значит: 95% сообщений обработались быстрее этого числа, только 5% — медленнее. P95 важнее среднего когда есть выбросы — среднее можно не заметить что иногда latency улетает в секунды.

---

### `_rmq_producer(queue, result, payload, stop)`

```python
def _rmq_producer(queue, result, payload, stop):
    conn = pika.BlockingConnection(
        pika.ConnectionParameters(host=RABBITMQ_HOST, heartbeat=600,
                                  blocked_connection_timeout=60)
    )
    ch = conn.channel()
    ch.queue_declare(queue=queue, durable=False)
    rl = RateLimiter(result.target_rate)
    sent = 0

    while not stop.is_set():
        body = json.dumps({"ts": time.time(), "data": payload}).encode()
        try:
            ch.basic_publish(
                exchange="", routing_key=queue, body=body,
                properties=pika.BasicProperties(delivery_mode=1),
            )
            sent += 1
        except Exception:
            result.errors += 1
        rl.wait()

    result.sent = sent
```

Запускается в отдельном треде. Подключается к RabbitMQ через `pika.BlockingConnection` — это синхронный клиент, каждый `basic_publish` блокируется до подтверждения от брокера. Именно из-за этого мы и не можем достичь 5000/s на одном треде — Python + синхронный I/O = bottleneck.

`heartbeat=600` — RabbitMQ закрывает соединение если нет активности больше N секунд. 600 секунд с запасом перекрывает длительность теста (30 с) плюс паузы между тестами.

`blocked_connection_timeout=60` — если RabbitMQ применил backpressure и заблокировал publisher (память заполнена), через 60 секунд соединение разрывается с исключением вместо бесконечного ожидания.

`queue_declare(durable=False)` — объявляет очередь если её нет. `durable=False` означает очередь исчезнет при перезапуске RabbitMQ — нам это подходит, персистентность не нужна для бенчмарка и только замедлила бы запись.

В каждое сообщение кладётся `"ts": time.time()` — Unix timestamp в момент отправки. Consumer потом вычтет его из текущего времени и получит end-to-end latency.

`delivery_mode=1` — не-персистентное сообщение (в противовес `delivery_mode=2` который записывает на диск). Снова ради честного сравнения с Redis — Redis тоже не пишет на диск по умолчанию.

`sent` считается локально и записывается в `result.sent` только после остановки — это потокобезопасно, потому что только этот тред пишет в `result.sent`.

---

### `_rmq_consumer(queue, result, stop, warmup_until)`

```python
def _rmq_consumer(queue, result, stop, warmup_until):
    conn = pika.BlockingConnection(...)
    ch = conn.channel()
    ch.queue_declare(queue=queue, durable=False)
    ch.basic_qos(prefetch_count=500)

    def on_msg(ch_, method, _props, body):
        msg = json.loads(body)
        if time.time() > warmup_until:
            latencies.append((time.time() - msg["ts"]) * 1000)
        received += 1
        ch_.basic_ack(delivery_tag=method.delivery_tag)

    ch.basic_consume(queue=queue, on_message_callback=on_msg)

    while not stop.is_set():
        conn.process_data_events(time_limit=0.1)
```

Consumer создаёт **своё собственное подключение** — нельзя использовать одно соединение из двух тредов, pika не thread-safe.

`basic_qos(prefetch_count=500)` — говорит RabbitMQ: отдавай мне не больше 500 неподтверждённых сообщений за раз. Без этого RabbitMQ льёт все сообщения в consumer без ограничений, что при большом backlog заполнит память клиента. 500 — баланс между throughput и потреблением памяти.

`on_msg` — callback, который pika вызывает на каждое полученное сообщение. Внутри:
- вычисляется `latency = (сейчас - ts_в_сообщении) * 1000` — это реальное время от отправки до получения
- `basic_ack` — подтверждение что сообщение обработано. Без ack RabbitMQ считает сообщение необработанным и может отдать его другому consumer'у. Это гарантия доставки в AMQP.

`process_data_events(time_limit=0.1)` — pika работает через event loop внутри BlockingConnection. Этот вызов говорит: обработай все входящие события в течение максимум 0.1 секунды, потом вернись. Так мы периодически проверяем `stop.is_set()` и можем корректно завершиться.

---

### `_redis_producer(stream, result, payload, stop)`

```python
def _redis_producer(stream, result, payload, stop):
    r = redis_lib.Redis(host=REDIS_HOST, decode_responses=True)
    rl = RateLimiter(result.target_rate)
    sent = 0

    while not stop.is_set():
        try:
            r.xadd(stream, {"ts": str(time.time()), "data": payload},
                   maxlen=STREAM_MAXLEN, approximate=True)
            sent += 1
        except Exception:
            result.errors += 1
        rl.wait()
```

Аналог `_rmq_producer` для Redis. Использует **Redis Streams** (`XADD`) — это правильный примитив для очереди сообщений в Redis (не LPUSH/RPOP, которые не дают ack и group semantics).

`XADD stream {"ts": ..., "data": ...} MAXLEN ~ 5000` — добавить запись в стрим. `MAXLEN ~ 5000` означает приближённое ограничение длины — Redis обрезает стрим лениво (не каждый раз до ровно 5000), что быстрее точного обрезания.

`decode_responses=True` — redis-py по умолчанию возвращает байты. С этим флагом возвращаются строки — удобнее работать без постоянного `.decode()`.

`"ts": str(time.time())` — timestamp как строка, потому что значения в Redis Streams хранятся как строки. Consumer преобразует обратно в float через `float(fields["ts"])`.

---

### `_redis_consumer(stream, group, result, stop, warmup_until)`

```python
def _redis_consumer(stream, group, result, stop, warmup_until):
    r = redis_lib.Redis(host=REDIS_HOST, decode_responses=True)
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis_lib.exceptions.ResponseError:
        pass  # group already exists

    while not stop.is_set():
        entries = r.xreadgroup(group, "c1", {stream: ">"}, count=200, block=100)
        for _, msgs in entries:
            for msg_id, fields in msgs:
                latencies.append((time.time() - float(fields["ts"])) * 1000)
                received += 1
                r.xack(stream, group, msg_id)
```

**Consumer Groups в Redis** — механизм, аналогичный очереди с ack. Без consumer group `XREAD` работает как pub/sub (каждый читатель получает все сообщения с начала). Consumer group гарантирует что каждое сообщение получит ровно один consumer из группы.

`xgroup_create(..., id="0", mkstream=True)` — создать группу начиная с самого начала стрима (`id="0"`). `mkstream=True` — создать стрим если его ещё нет. `ResponseError` возникает если группа уже существует — просто игнорируем.

`xreadgroup(group, "c1", {stream: ">"}, count=200, block=100)`:
- `group` — имя consumer group
- `"c1"` — имя конкретного consumer внутри группы
- `{stream: ">"}` — `">"` означает "дай мне только новые сообщения, которые ещё никому не доставлялись"
- `count=200` — читать пачками по 200 сообщений за раз (эффективнее чем по одному)
- `block=100` — если новых сообщений нет, ждать максимум 100 мс и вернуть пустой список. Это блокирующий вызов — не крутит CPU вхолостую.

`xack(stream, group, msg_id)` — подтверждение обработки. Аналог `basic_ack` в RabbitMQ. Без ack сообщение остаётся в pending-листе и может быть переполучено.

---

### `_rmq_api(path)` и `_monitor_rmq(queue, result, stop)`

```python
_RMQ_CREDS = base64.b64encode(b"guest:guest").decode()

def _rmq_api(path):
    url = f"http://{RABBITMQ_HOST}:15672/api/{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Basic {_RMQ_CREDS}")
    with urllib.request.urlopen(req, timeout=1) as resp:
        return json.loads(resp.read())

def _monitor_rmq(queue, result, stop):
    depths, mems = [], []
    while not stop.is_set():
        q = _rmq_api(f"queues/%2F/{queue}")
        depths.append(q.get("messages", 0))
        nodes = _rmq_api("nodes")
        mems.append(nodes[0].get("mem_used", 0) / 1_048_576)
        time.sleep(1)

    result.peak_backlog = max(depths, default=0)
    result.avg_backlog  = sum(depths) / len(depths) if depths else 0.0
    result.peak_mem_mb  = max(mems, default=0.0)
```

RabbitMQ поставляется с **Management HTTP API** на порту 15672 — это тот же UI что в браузере, только в виде JSON. Мы используем его чтобы раз в секунду снимать метрики без влияния на основные соединения.

`_rmq_api` — вспомогательная функция: формирует URL, добавляет Basic Auth заголовок (credentials закодированы в base64 один раз при старте в `_RMQ_CREDS`), делает запрос и возвращает распарсенный JSON.

`_monitor_rmq` запускается в третьем треде параллельно с producer и consumer:
- `/api/queues/%2F/{queue}` — данные конкретной очереди. `%2F` — URL-encoded `/` (имя vhost в RabbitMQ). Поле `messages` — текущая глубина очереди (backlog).
- `/api/nodes` — информация об узле RabbitMQ. `mem_used` — байты RAM которые использует брокер. Делим на 1_048_576 чтобы получить MB.

После остановки (`stop.set()`) записывает пиковые и средние значения в `result`.

---

### `_monitor_redis(stream, result, stop)`

```python
def _monitor_redis(stream, result, stop):
    r = redis_lib.Redis(host=REDIS_HOST, decode_responses=True)
    depths, mems = [], []
    while not stop.is_set():
        depths.append(r.xlen(stream))
        mems.append(r.info("memory")["used_memory"] / 1_048_576)
        time.sleep(1)

    result.peak_backlog = max(depths, default=0)
    result.avg_backlog  = sum(depths) / len(depths) if depths else 0.0
    result.peak_mem_mb  = max(mems, default=0.0)
    r.close()
```

Аналог `_monitor_rmq` для Redis, но через Redis-команды а не HTTP API:

`XLEN stream` — длина стрима прямо сейчас. Это и есть backlog: сколько сообщений добавлено но ещё не acknowledged consumer'ом. Если растёт — consumer не успевает.

`INFO memory` — команда Redis которая возвращает словарь с метриками памяти. `used_memory` — байты занятые Redis под данные (сами стримы, ключи, структуры). Именно здесь мы видели рост с 1840 MB до 2582 MB при тестах с 100KB — после чего Redis падал с OOM.

---

### `run_one(broker, msg_size, target_rate) -> RunResult`

```python
def run_one(broker, msg_size, target_rate):
    result       = RunResult(broker=broker, msg_size=msg_size, target_rate=target_rate)
    payload      = make_payload(msg_size)
    stop         = threading.Event()
    warmup_until = time.time() + WARMUP_SECS
    run_id       = f"{broker}_{msg_size}_{target_rate}_{int(time.time())}"

    # создаём три треда: consumer, producer, monitor
    consumer.start()
    time.sleep(0.5)
    producer.start()
    monitor.start()

    time.sleep(TEST_DURATION)
    stop.set()

    producer.join(timeout=5)
    consumer.join(timeout=5)
    monitor.join(timeout=3)

    return result
```

Оркестратор одного прогона. Несколько важных деталей:

**`run_id = f"{broker}_{msg_size}_{target_rate}_{int(time.time())}"` — уникальное имя** для очереди/стрима каждого прогона. Это гарантирует что между тестами нет пересечений: данные предыдущего прогона не попадут в статистику следующего. Timestamp в имени делает его уникальным даже если тест запускается несколько раз подряд.

**`threading.Event()`** — примитив синхронизации. `stop.set()` устанавливает флаг, `stop.is_set()` возвращает True. Все три треда проверяют этот флаг в своём цикле — так они узнают что пора завершаться.

**Порядок старта:**
1. Сначала стартует **consumer** — он объявляет очередь/создаёт стрим. Если producer стартует первым, его первые сообщения могут уйти в никуда (очередь не объявлена).
2. `time.sleep(0.5)` — 500 мс паузы чтобы consumer успел подключиться и задекларировать очередь.
3. Стартует **producer**.
4. Стартует **monitor**.

**`join(timeout=5)`** — ждём завершения тредов максимум 5 секунд после `stop.set()`. Если тред завис — не блокируемся вечно, идём дальше. `daemon=True` на тредах гарантирует что они умрут вместе с основным процессом даже если join не помог.

---

### `wait_for_rabbitmq()` и `wait_for_redis()`

```python
def wait_for_rabbitmq():
    for i in range(30):
        try:
            c = pika.BlockingConnection(pika.ConnectionParameters(host=RABBITMQ_HOST))
            c.close()
            print("RabbitMQ: ready")
            return
        except Exception:
            print(f"  Waiting for RabbitMQ... ({i + 1}/30)")
            time.sleep(2)
    raise RuntimeError("RabbitMQ not available after 60 s")
```

RabbitMQ стартует медленно — около 20–30 секунд на инициализацию Erlang VM, загрузку плагинов, выбор Khepri-лидера. Docker healthcheck в compose-файле уже ждёт его готовности, но у benchmark-контейнера есть `depends_on: condition: service_healthy` — значит он стартует только после healthcheck. Эти функции — дополнительная страховка на случай если healthcheck прошёл но брокер ещё не принимает AMQP-соединения.

Пробуем подключиться 30 раз с паузой 2 секунды = максимум 60 секунд ожидания. Для Redis достаточно `PING`.

---

### `print_table(results)`

```python
def print_table(results):
    cols   = ["Broker", "Size", "Target/s", "Sent", "Recv", "Lost",
              "Tput/s", "Avg ms", "P95 ms", "BacklogPk", "MemPk MB"]
    widths = [12, 7, 9, 9, 9, 7, 9, 9, 9, 10, 10]
    ...
    prev_size = None
    for r in results:
        if prev_size and r.msg_size != prev_size:
            print(sep)  # разделитель между группами размеров
        prev_size = r.msg_size
        ...
```

Форматирует итоговую таблицу с фиксированной шириной колонок через `ljust(w)`. Между группами сообщений разного размера печатается разделитель `---` для читаемости. Вся логика — чистый cosmetics, никакой бизнес-логики.

---

### `main()`

```python
def main():
    wait_for_rabbitmq()
    wait_for_redis()

    for msg_size in MSG_SIZES:
        for target_rate in TARGET_RATES:
            for broker in BROKERS:
                r = run_one(broker, msg_size, target_rate)
                results.append(r)

    print_table(results)
```

Точка входа. Тройной цикл перебирает все комбинации из матрицы. Порядок важен: сначала размер сообщения, потом скорость, потом брокер — так в таблице результаты сгруппированы по размеру что удобно для сравнения.

---

## Как читать результаты

```
Broker    Size  Target/s  Sent    Recv    Lost  Tput/s  Avg ms  P95 ms  BacklogPk  MemPk MB
rabbitmq  100KB 1,000     20,905  7,966  12,939   266   10525   18102   11,153     452
redis     100KB 1,000     18,943  18,942      1   631       6      10    5,000    2,582
```

| Колонка | Что значит |
|---|---|
| `Sent` | Сколько сообщений producer отправил за 30 с |
| `Recv` | Сколько consumer получил и подтвердил |
| `Lost` | Разница: либо в очереди остались, либо потеряны |
| `Tput/s` | Recv / 30 — реальная пропускная способность |
| `Avg ms` | Средняя end-to-end задержка |
| `P95 ms` | 95% сообщений быстрее этого значения |
| `BacklogPk` | Пиковая глубина очереди во время теста |
| `MemPk MB` | Пиковое потребление RAM брокером |

**Сигналы деградации:**
- `Lost > 0` — брокер начал терять/отбрасывать сообщения
- `Tput/s` значительно меньше `Target/s` — producer упёрся в backpressure
- `P95 ms` растёт быстрее `Avg ms` — появились выбросы latency
- `BacklogPk` большой и растёт — consumer не успевает за producer'ом
