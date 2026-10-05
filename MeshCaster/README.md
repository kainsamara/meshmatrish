# MeshCaster

GUI-приложение для массовых рассылок через Meshtastic: отправка сообщений
по выбранным бейджикам или в канал, работа по расписанию, приём и просмотр
чата по каналам. 
Написано специально для использования на конференции ZeroNights 2026.

## Возможности

- Подключение к базовой ноде Meshtastic по COM-порту (автоопределение,
  автоподключение при появлении устройства, авто-reconnect).
- Рассылка по списку получателей из `badges_list.txt` — с подтверждением
  доставки (ACK), повторным проходом по неподтверждённым и сохранением
  состояния, чтобы продолжить прерванную рассылку после сбоя.
- Отправка в канал ноды одним сообщением (без ACK).
- Планировщик задач: разовое сообщение на указанные дату и время,
  вкл/выкл задачи, «выполнить немедленно», автоматическое откладывание,
  если нода занята или не подключена.
- Приём чата по каналам с сохранением истории в `chat_logs/`.
- Локальное хранение заметок к бейджикам.
- Лимит сообщения 233 байта UTF-8 с подсчётом и автообрезкой.

## Стек

Python 3, PyQt6, meshtastic, pyserial. Работа с нодой и планировщиком —
в отдельных потоках; состояние рассылки сохраняется атомарно.

## Установка (из исходников)

Python 3.10+:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\activate
python -m pip install -U pip
pip install -r requirements.txt
```

Положите `badges_list.txt` рядом с `main.py`.
Формат файла: Имя | Пароль BLE| NodeID — по одному бейджику в строке, # в начале строки — комментарий.

## Запуск

```powershell
python main.py
```

## PyInstaller

```powershell
pip install pyinstaller
pyinstaller --noconfirm --clean --windowed --name MeshCaster --icon=icon.ico --add-data "logos;logos" --hidden-import=meshtastic --hidden-import=meshtastic.serial_interface --hidden-import=meshtastic.util --hidden-import=meshtastic.protobuf --hidden-import=google.protobuf --hidden-import=pubsub --hidden-import=serial --hidden-import=serial.tools --hidden-import=serial.tools.list_ports --collect-all pubsub --collect-all google.protobuf --exclude-module=matplotlib --exclude-module=scipy --exclude-module=pandas --exclude-module=numpy --exclude-module=pytest --exclude-module=PIL --exclude-module=tkinter --exclude-module=IPython main.py
```

После сборки скопируйте `badges_list.txt` и `icom.ico` в папку `_internal` рядом с exe.

Также при запуске можно использовать аргументы: MeshCaster.exe `--fullscreen` или `-f`	для запуска в полный экран 
												MeshCaster.exe `--maximized` или `-m`	для запуска на весь экран с рамкой