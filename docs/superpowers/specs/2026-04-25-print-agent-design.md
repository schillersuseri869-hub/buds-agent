# Print Agent — Технический дизайн

**Дата:** 2026-04-25
**Компонент:** Print Agent (шаг 2 из порядка разработки MVP)

---

## Контекст

Print Agent — второй компонент BUDS после фундамента. Отвечает исключительно за доставку ярлыка Яндекс Маркета на термопринтер флориста.

Принтер: **Xprinter XP-365B**, USB, ESC/POS, этикетки 58×40 мм.
Принтер подключён к локальному ПК флориста (Windows, два admin-профиля).
VPS и ПК флориста — в разных сетях, связь через WebSocket.

---

## Сценарии работы

### Заказ пришёл, ПК флориста включён

```
order.created
  → Print Agent
  → GET /campaigns/{id}/orders/{orderId}/delivery/labels  (PDF binary)
  → PrintJob(status=pending) в БД
  → send_print_job() → клиент подключён → статус sent
  → print_client скачивает PDF, рендерит, печатает
  → ACK {job_id, status: done} → статус done
```

### Заказ пришёл, ПК флориста выключен

```
order.created
  → Print Agent
  → PDF скачан, PrintJob(status=pending) в БД
  → send_print_job() → клиент не подключён → статус pending
  → алерт владельцу: «Принтер офлайн, ярлык напечатается при подключении»

Флорист приходит на работу → входит в систему под любым профилем
  → Task Scheduler запускает print_client.py автоматически
  → print_client подключается к WebSocket
  → сервер flush: отправляет все pending задания
  → печать → ACK done
```

---

## Компоненты

### `app/agents/print_agent/agent.py` (новый)

Модуль агента на VPS.

**Инициализация:**
- Подписывается на `order.created` через `EventBus`

**Обработчик `order.created`:**
1. Извлекает `market_order_id` из события
2. Вызывает Яндекс API: `GET /campaigns/{campaignId}/orders/{orderId}/delivery/labels`
   - При ошибке API → алерт владельцу «Не удалось скачать ярлык заказа #X», завершить
3. Сохраняет PDF binary в Redis с ключом `print:pdf:{job_id}`, TTL 24 часа
4. Создаёт `PrintJob(order_id=..., status=pending, label_url=f"redis:print:pdf:{job_id}")` в БД
5. Вызывает `send_print_job({job_id})` — PDF достаётся из Redis внутри ws_print.py
   - Успех → обновляет статус на `sent`
   - Нет клиента → оставляет `pending`, отправляет алерт владельцу

**Обработчик ACK** (вызывается из `ws_print.py`):
- `status=done` → обновить `PrintJob.status=done`, `completed_at=now()`
- `status=failed` → обновить `PrintJob.status=failed` → алерт владельцу «Ошибка печати заказа #X»

### `app/api/ws_print.py` (расширить)

**При подключении нового клиента:**
- Проверить: уже есть активный клиент → отправить `{"error": "already_connected"}`, закрыть новое соединение
- Иначе: добавить в список, запустить flush pending jobs

**Flush pending jobs:**
- Запрос в БД: все `PrintJob` со статусом `pending` или `sent`, упорядоченные по `created_at`
- Для каждого: достать PDF из Redis, отправить клиенту, обновить статус → `sent`

**При получении ACK `{job_id, status}`:**
- Вызвать обработчик ACK из `print_agent`

**При отключении клиента:**
- Убрать из списка

### `print_client/print_client.py` (расширить)

Запускается на ПК флориста. Единственный исполняемый файл на той стороне.

**Single-instance guard:**
- При старте создаёт lock-файл `%TEMP%\buds_print.lock` с PID
- Если файл существует и процесс с тем PID жив — завершить новый экземпляр
- При завершении удаляет lock-файл

**Основной цикл:**
- `websockets.connect()` с авто-переподключением (уже есть)
- При получении задания `{job_id, pdf_data_b64}`:
  1. Декодировать PDF из base64
  2. Рендерить через PyMuPDF (`fitz.open("pdf", data)`) → первая страница → PIL Image
  3. Ресайз под ширину 464px (58мм × 203 DPI) с сохранением пропорций
  4. Конвертировать в ч/б (1-bit) для ESC/POS
  5. `printer.image(img)` → `printer.cut()`
  6. Отправить ACK `{job_id, status: "done"|"failed"}`

**Конфигурация через env:**
```
BUDS_WS_URL=ws://82.22.3.55:8000/ws/print
PRINTER_USB_VENDOR=0x1FC9   # проверить через Device Manager на ПК флориста
PRINTER_USB_PRODUCT=0x0082  # проверить через Device Manager на ПК флориста
```

> Для получения точных VID/PID: Device Manager → Xprinter → Properties → Details → Hardware IDs

### `print_client/install_task.ps1` (новый)

PowerShell-скрипт для однократной установки задачи автозапуска.

- Триггер: вход любого пользователя (`-Trigger (New-ScheduledTaskTrigger -AtLogOn)`)
- Действие: `pythonw.exe print_client.py`
- Параметр: запускать только при входе пользователя (для доступа к USB)
- Запускается один раз от администратора

### `print_client/requirements.txt` (новый)

```
websockets>=12.0
pymupdf>=1.24.0
python-escpos>=3.0
Pillow>=10.0
```

---

## Формат WebSocket-сообщений

**Сервер → клиент (задание печати):**
```json
{
  "job_id": "uuid",
  "pdf_data": "<base64-encoded PDF bytes>"
}
```

**Клиент → сервер (ACK):**
```json
{
  "job_id": "uuid",
  "status": "done" | "failed",
  "error": "optional error message"
}
```

**Сервер → клиент (при дублирующем подключении):**
```json
{
  "error": "already_connected"
}
```

---

## Обработка ошибок

| Ситуация | Действие |
|---|---|
| Яндекс API не вернул ярлык | Алерт владельцу, `PrintJob` не создаётся |
| Клиент не подключён в момент заказа | `PrintJob` остаётся `pending`, алерт владельцу |
| Печать не удалась (`failed` ACK) | `PrintJob.status=failed`, алерт владельцу |
| WebSocket разрыв во время отправки | Job остаётся `sent`; при переподключении сервер повторно отправляет все `pending` и `sent` |
| Второй экземпляр print_client | Новый процесс завершается через lock-файл |
| PDF не в Redis (TTL истёк) | Повторно скачать с Яндекс API, переотправить |

---

## Тестирование

| Тест | Способ |
|---|---|
| `PrintJob` создаётся при `order.created` | Unit: мок Яндекс API + мок event bus |
| Flush pending при подключении клиента | Integration: job `pending` в БД → клиент подключается → job становится `sent` |
| ACK `done` обновляет статус и `completed_at` | Unit: WebSocket ACK → проверить БД |
| ACK `failed` триггерит алерт владельцу | Unit: мок Telegram-бота |
| Алерт при офлайн-принтере | Unit: `send_print_job()` возвращает False → Telegram вызван |
| Lock-файл блокирует второй экземпляр | Запустить два процесса, второй должен завершиться без ошибки |
| PDF рендеринг в правильный размер | Unit: синтетический PDF → проверить ширину изображения = 464px |

---

## Деплой print_client на ПК флориста

1. Скопировать папку `print_client/` на ПК (USB или общая папка)
2. Установить Python 3.11+ на ПК флориста
3. `pip install -r print_client/requirements.txt`
4. Задать env-переменные (BUDS_WS_URL, PRINTER_USB_VENDOR, PRINTER_USB_PRODUCT)
5. Запустить `install_task.ps1` один раз от администратора
6. Проверить: войти под обоими профилями, убедиться что print_client стартует

VPS-адрес (`BUDS_WS_URL`) — единственное что нужно изменить при смене сервера.
