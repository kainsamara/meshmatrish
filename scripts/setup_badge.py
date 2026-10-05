#!/usr/bin/env python3
"""
Настройка бейджиков Meshtastic для конференции.
Канал 0: стандартный открытый Meshtastic.
Канал 1: ZN_CHAT, зашифрованный канал для личных сообщений.
Дополнительно:
- регион LoRa: Russia (RU);
- modem preset: SHORT_FAST;
- BLE PIN: случайные 6 цифр;
- NodeID, короткое имя устройства и BLE PIN сохраняются в passwords.txt
  только после успешной проверки LoRa и отправки тестового сообщения;
- уже настроенные NodeID не прошиваются повторно;
- badges_list.txt не содержит дубликатов;
- после применения настроек скрипт закрывает интерфейс, ждёт перезагрузку,
  переподключается, проверяет LoRa, отправляет сообщение в канал ZN_CHAT
  и только затем записывает данные в файлы;
- после завершения Enter запускает настройку следующего бейджика.
"""
import sys
import importlib
import subprocess
import os
import secrets
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def ensure_dependencies():
    required_packages = {
        "meshtastic": "meshtastic",
        "serial": "pyserial",
        "protobuf": "protobuf",
    }
    missing = []
    for module_name, package_name in required_packages.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(package_name)

    if missing:
        print("=" * 70)
        print("УСТАНОВКА ОТСУТСТВУЮЩИХ ЗАВИСИМОСТЕЙ")
        print("=" * 70)
        print(f"Отсутствуют: {', '.join(missing)}")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--upgrade"] + missing
            )
            print("Зависимости установлены успешно")
        except Exception as e:
            print(f"Ошибка установки: {e}")
            print(f"pip install {' '.join(missing)}")
            input("\nНажмите Enter для выхода...")
            sys.exit(1)
    else:
        print("Все зависимости уже установлены")


ensure_dependencies()

import meshtastic.serial_interface
from meshtastic import channel_pb2
from meshtastic import config_pb2


def get_keys_path():
    return os.path.join(SCRIPT_DIR, "conference_keys.txt")


def get_badges_list_path():
    return os.path.join(SCRIPT_DIR, "badges_list.txt")


def get_passwords_path():
    return os.path.join(SCRIPT_DIR, "passwords.txt")


def load_keys():
    keys_file = get_keys_path()
    if not os.path.exists(keys_file):
        print(f"Файл {keys_file} не найден!")
        print("\nСначала запустите setup_base.py в этой же папке")
        return None
    try:
        with open(keys_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            for line in lines:
                if "ZN_CHAT PSK:" in line:
                    return line.split(":", 1)[1].strip()
        return None
    except Exception as e:
        print(f"Ошибка чтения файла: {e}")
        return None


def read_badges_ids():
    """Прочитать NodeID из badges_list.txt и убрать дубликаты."""
    badges_file = get_badges_list_path()
    if not os.path.exists(badges_file):
        return set()
    existing_ids = set()
    try:
        with open(badges_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                existing_ids.add(line)
    except Exception as e:
        print(f"Не удалось прочитать {badges_file}: {e}")
    return existing_ids


def normalize_node_id(value):
    """
    Привести NodeID к единому виду.
    Поддерживаются:
    - десятичные значения, которые записывал старый скрипт;
    - Meshtastic ID вида !aabbccdd.
    """
    value = str(value).strip()
    if not value:
        return value
    if value.startswith("!"):
        try:
            return str(int(value[1:], 16))
        except ValueError:
            return value.lower()
    try:
        return str(int(value))
    except ValueError:
        return value.lower()


def save_badge_nodeid(node_id):
    """
    Добавить NodeID в badges_list.txt без дубликатов.
    Если в старом файле уже были дубликаты, файл автоматически
    переписывается без них.
    """
    badges_file = get_badges_list_path()
    node_id = str(node_id)
    existing_ids = read_badges_ids()
    normalized_existing = {}
    for item in existing_ids:
        normalized_existing.setdefault(normalize_node_id(item), item)

    normalized_new = normalize_node_id(node_id)
    already_present = normalized_new in normalized_existing

    try:
        if not os.path.exists(badges_file):
            with open(badges_file, "w", encoding="utf-8") as f:
                f.write("# Список NodeID бейджиков конференции\n")
                f.write("# Один NodeID на строку\n")

        # Переписываем файл, чтобы удалить старые дубликаты.
        unique_lines = []
        seen = set()
        with open(badges_file, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    unique_lines.append(line)
                    continue
                normalized = normalize_node_id(stripped)
                if normalized not in seen:
                    unique_lines.append(f"{stripped}\n")
                    seen.add(normalized)

        if not already_present:
            unique_lines.append(f"{node_id}\n")

        with open(badges_file, "w", encoding="utf-8") as f:
            f.writelines(unique_lines)

        if already_present:
            print(f"NodeID {node_id} уже есть в badges_list.txt")
            return False
        print(f"NodeID {node_id} добавлен в badges_list.txt")
        return True
    except Exception as e:
        print(f"Не удалось сохранить NodeID: {e}")
        return False


def is_badge_already_configured(node_id):
    """Проверить, есть ли NodeID среди уже настроенных бейджиков."""
    existing_ids = read_badges_ids()
    normalized_node_id = normalize_node_id(node_id)
    return any(
        normalize_node_id(existing_id) == normalized_node_id
        for existing_id in existing_ids
    )


def save_password(node_id, device_name, ble_pin):
    """
    Сохранить NodeID, короткое имя устройства и BLE PIN в passwords.txt.
    Формат:
    NodeID | Короткое имя | BLE PIN
    """
    passwords_file = get_passwords_path()
    try:
        if not os.path.exists(passwords_file):
            with open(passwords_file, "w", encoding="utf-8") as f:
                f.write("# Пароли BLE бейджиков конференции\n")
                f.write("# NodeID | Короткое имя | BLE PIN\n")

        with open(passwords_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Не создаём вторую запись для того же NodeID.
        normalized_node_id = normalize_node_id(node_id)
        for line in lines:
            if line.startswith("#") or "|" not in line:
                continue
            stored_node_id = line.split("|", 1)[0].strip()
            if normalize_node_id(stored_node_id) == normalized_node_id:
                return False

        with open(passwords_file, "a", encoding="utf-8") as f:
            f.write(f"{node_id} | {device_name} | {ble_pin}\n")
        print(f"Данные BLE сохранены в {passwords_file}")
        return True
    except Exception as e:
        print(f"Не удалось сохранить BLE PIN: {e}")
        return False


def apply_config(node, section, attr, value):
    try:
        if section == "lora":
            setattr(node.localConfig.lora, attr, value)
            node.writeConfig("lora")
        elif section == "position":
            setattr(node.localConfig.position, attr, value)
            node.writeConfig("position")
        elif section == "telemetry":
            setattr(node.moduleConfig.telemetry, attr, value)
            node.writeConfig("telemetry")
        elif section == "bluetooth":
            setattr(node.localConfig.bluetooth, attr, value)
            node.writeConfig("bluetooth")
        elif section == "user":
            setattr(node.localConfig.user, attr, value)
            node.writeConfig("user")
        return True
    except Exception as e:
        print(f"Не удалось применить {section}.{attr}: {e}")
        return False


def get_short_name(interface, node_id):
    """Получить короткое имя Meshtastic-ноды."""
    # Сначала пытаемся получить из interface.nodes
    try:
        node_hex_id = f"!{int(node_id):08x}"
        info = interface.nodes.get(node_hex_id, {})
        user = info.get("user", {})
        if isinstance(user, dict):
            name = user.get("shortName")
            if name:
                return name
    except Exception:
        pass
    # Фолбэк: пробуем через getUser()
    try:
        user = interface.localNode.getUser()
        if user is not None:
            if hasattr(user, "short_name") and user.short_name:
                return user.short_name
    except Exception:
        pass
    return "Unknown"


def generate_ble_pin():
    """Сгенерировать случайный шестизначный BLE PIN."""
    return f"{secrets.randbelow(900000) + 100000:06d}"


def resolve_region_russia():
    """
    Получить значение enum для региона Russia.
    В разных версиях protobuf Meshtastic регион может называться
    по-разному (RU, RUSSIA и т.п.). Перебираем варианты.
    """
    candidates = [
        lambda: config_pb2.Config.LoRaConfig.RegionCode.RU,
        lambda: config_pb2.Config.LoRaConfig.RU,
        lambda: config_pb2.Config.LoRaConfig.RegionCode.RUSSIA,
        lambda: config_pb2.Config.LoRaConfig.RUSSIA,
    ]
    for getter in candidates:
        try:
            return getter()
        except AttributeError:
            continue
    # Фолбэк: ищем по числовому значению (RU обычно = 9 в свежих версиях).
    try:
        enum_type = config_pb2.Config.LoRaConfig.RegionCode.DESCRIPTOR
        for val in enum_type.values:
            if val.name in ("RU", "RUSSIA"):
                return val.number
    except Exception:
        pass
    raise RuntimeError(
        "Не удалось найти код региона Russia в protobuf. "
        "Обновите meshtastic: pip install --upgrade meshtastic"
    )


def resolve_modem_preset_short_fast():
    """
    Получить значение enum для модем-пресета SHORT_FAST.
    """
    candidates = [
        lambda: config_pb2.Config.LoRaConfig.ModemPreset.SHORT_FAST,
        lambda: config_pb2.Config.LoRaConfig.SHORT_FAST,
    ]
    for getter in candidates:
        try:
            return getter()
        except AttributeError:
            continue
    try:
        enum_type = config_pb2.Config.LoRaConfig.ModemPreset.DESCRIPTOR
        for val in enum_type.values:
            if val.name == "SHORT_FAST":
                return val.number
    except Exception:
        pass
    raise RuntimeError(
        "Не удалось найти модем-пресет SHORT_FAST в protobuf. "
        "Обновите meshtastic: pip install --upgrade meshtastic"
    )


# --- ПЕРЕПОДКЛЮЧЕНИЕ, ПРОВЕРКА LoRa, ОТПРАВКА СООБЩЕНИЙ ---


def reconnect_after_reboot(old_interface, expected_node_id, wait_seconds=10):
    """
    Закрыть старый интерфейс, дождаться перезагрузки бейджика и
    переподключиться. Проверяет, что подключились именно к тому же
    устройству (по NodeID). Делает несколько попыток, если порт
    ещё не освободился.
    """
    print(f"\nОжидание перезагрузки бейджика...")

    # Закрываем старый интерфейс (соединение уже могло разорваться из-за ребута)
    try:
        old_interface.close()
    except Exception:
        pass

    time.sleep(wait_seconds)

    max_attempts = 6
    last_error = None
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"Попытка переподключения ({attempt}/{max_attempts})...")
            new_interface = meshtastic.serial_interface.SerialInterface()
            time.sleep(3)

            new_node_id = new_interface.localNode.nodeNum
            if not new_node_id or new_node_id == 0:
                print("  Бейджик не обнаружен после переподключения. Повтор...")
                try:
                    new_interface.close()
                except Exception:
                    pass
                time.sleep(2)
                continue

            if str(new_node_id) != str(expected_node_id):
                print(
                    f"  Подключились к другому устройству: "
                    f"{new_node_id} (ожидался {expected_node_id}). Повтор..."
                )
                try:
                    new_interface.close()
                except Exception:
                    pass
                time.sleep(2)
                continue

            print(f"Переподключение успешно. Node ID: {new_node_id}")
            return new_interface
        except Exception as e:
            last_error = e
            print(f"  Ошибка переподключения: {e}")
            time.sleep(3)

    raise RuntimeError(
        f"Не удалось переподключиться к бейджику после перезагрузки: {last_error}"
    )


def check_lora_radio(interface):
    """
    Проверка работоспособности микросхемы LoRa через API.
    Выполняется после перезагрузки и переподключения.
    Делает несколько попыток, потому что сразу после подключения
    myInfo и конфиги могут быть ещё не полностью загружены.
    """
    print("\nПроверка работы микросхемы LoRa...")
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        try:
            # Проверяем, что myInfo получен от устройства.
            # НЕ проверяем firmwareVersion на непустоту — сразу после
            # переподключения оно может быть пустой строкой, и это нормально.
            my_info = interface.myInfo
            if my_info is None:
                print(f"  Попытка {attempt}/{max_attempts}: myInfo недоступен, ожидание...")
                time.sleep(2)
                continue

            # Чтение конфигурации LoRa: если радиомодуль не отвечает,
            # protobuf-объект localConfig.lora не будет корректно заполнен
            # или обращение к нему выбросит исключение.
            lora_config = interface.localNode.localConfig.lora
            region = lora_config.region

            fw = getattr(my_info, "firmwareVersion", None)
            if not fw:
                fw = "неизвестно"
            print(f"  Прошивка: {fw}")
            print(f"  Регион LoRa в конфиге: {region}")
            print("  Микросхема LoRa отвечает.")
            return True
        except Exception as e:
            print(f"  Попытка {attempt}/{max_attempts}: ошибка: {e}")
            time.sleep(2)

    print("  Ошибка: не удалось связаться с LoRa после нескольких попыток.")
    return False


def send_test_message(interface):
    """
    Отправляет тестовое сообщение в канал ZN_CHAT (channel 1).
    Сообщение будет видно всем устройствам, подписанным на этот канал.
    """
    message_text = "Нода настроена и готова к работе"
    
    print("\n" + "=" * 60)
    print("ТЕСТОВАЯ ОТПРАВКА В КАНАЛ ZN_CHAT")
    print(f"Отправитель: {interface.localNode.nodeNum}")
    print(f"Канал: 1 (ZN_CHAT)")
    print(f"Сообщение: {message_text}")
    print("=" * 60)

    try:
        print("\nОтправка сообщения...")

        interface.sendText(
            text=message_text,
            channelIndex=1,
            wantAck=False,
        )

        print(f"Сообщение '{message_text}' отправлено в канал ZN_CHAT.")
        print("Ожидание 3 секунды для завершения передачи...")
        time.sleep(3)

        print("Проверка завершена.")
        return True

    except Exception as e:
        print(f"ОШИБКА отправки: {e}")
        import traceback
        traceback.print_exc()
        return False


# -------------------------------------------------------------


def configure_badge(interface, chat_psk):
    node = interface.localNode
    node_id = interface.localNode.nodeNum
    print(f"Node ID: {node_id}")

    # Проверяем бейджик до изменения каких-либо настроек.
    if is_badge_already_configured(node_id):
        print()
        print("ВНИМАНИЕ: этот бейджик уже есть в badges_list.txt.")
        print("Настройка пропущена, чтобы не перезаписывать BLE PIN.")
        return False

    short_name = get_short_name(interface, node_id)
    print(f"Короткое имя: {short_name}")

    print("\nКанал 0: PRIMARY")
    print("Не изменяем.")

    print("\nКанал 1: ZN_CHAT")
    try:
        ch1 = node.channels[1]
        ch1.settings.name = "ZN_CHAT"
        ch1.settings.psk = bytes.fromhex(chat_psk)
        ch1.role = channel_pb2.Channel.Role.SECONDARY
        ch1.settings.uplink_enabled = True
        node.writeChannel(1)
        print("ZN_CHAT настроен.")
    except Exception as e:
        print(f"Ошибка настройки ZN_CHAT: {e}")
        raise

    print("\nПрименение настроек...")
    if apply_config(node, "position", "position_broadcast_secs", 0):
        print("Позиция: OFF")
    if apply_config(node, "position", "position_broadcast_smart_enabled", False):
        print("Smart position: OFF")
    if apply_config(node, "telemetry", "device_update_interval", 0):
        print("Телеметрия устройства: OFF")
    if apply_config(node, "telemetry", "environment_update_interval", 0):
        print("Телеметрия среды: OFF")
    if apply_config(node, "lora", "hop_limit", 2):
        print("Hop limit: 2")
    if apply_config(node, "lora", "tx_power", 14):
        print("TX Power: 14 dBm")

    # Регион Russia.
    try:
        region_code = resolve_region_russia()
        node.localConfig.lora.region = region_code
        node.writeConfig("lora")
        print("Регион: Russia (RU)")
    except Exception as e:
        print(f"Не удалось установить регион Russia: {e}")
        raise

    # Modem preset SHORT_FAST.
    try:
        preset_value = resolve_modem_preset_short_fast()
        node.localConfig.lora.modem_preset = preset_value
        node.writeConfig("lora")
        print("Modem preset: SHORT_FAST")
    except Exception as e:
        print(f"Не удалось установить modem preset SHORT_FAST: {e}")
        raise

    # Случайный BLE PIN.
    ble_pin = generate_ble_pin()
    if apply_config(node, "bluetooth", "fixed_pin", int(ble_pin)):
        print(f"BLE PIN: {ble_pin}")
    else:
        raise RuntimeError("Не удалось установить BLE PIN")

    # --- ПЕРЕПОДКЛЮЧЕНИЕ ПОСЛЕ РЕБУТА ---
    new_interface = None
    try:
        new_interface = reconnect_after_reboot(interface, node_id, wait_seconds=10)

        # --- ПРОВЕРКА LoRa ---
        if not check_lora_radio(new_interface):
            print("Ошибка: микросхема LoRa не отвечает после перезагрузки.")
            return False

        # --- ОТПРАВКА ТЕСТОВОГО СООБЩЕНИЯ ---
        if not send_test_message(new_interface):
            print("Ошибка: не удалось отправить тестовое сообщение.")
            return False

    finally:
        if new_interface is not None:
            try:
                new_interface.close()
            except Exception:
                pass

    # --- ЗАПИСЬ В ФАЙЛЫ ТОЛЬКО ПОСЛЕ УСПЕШНОЙ ПРОВЕРКИ И ОТПРАВКИ ---
    print("\nСохранение данных...")
    save_badge_nodeid(str(node_id))
    save_password(str(node_id), short_name, ble_pin)

    print("\n" + "=" * 70)
    print("БЕЙДЖИК НАСТРОЕН")
    print("=" * 70)
    print()
    print(f"Короткое имя:   {short_name}")
    print(f"BLE PIN:        {ble_pin}")
    return True


def run_one_badge(chat_psk):
    interface = None
    print("\n" + "=" * 70)
    print("ПОДКЛЮЧЕНИЕ К СЛЕДУЮЩЕМУ БЕЙДЖИКУ")
    print("=" * 70)
    try:
        print("\nПодключение к бейджику...")
        interface = meshtastic.serial_interface.SerialInterface()
        time.sleep(3)

        # Проверка, что бейджик действительно обнаружен
        node_num = interface.localNode.nodeNum
        if not node_num or node_num == 0:
            print("Ошибка: бейджик не обнаружен. Проверьте подключение по USB.")
            return False

        print(f"Бейджик обнаружен. Node ID: {node_num}")
        return configure_badge(interface, chat_psk)
    except Exception as e:
        print(f"\nОшибка настройки: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if interface is not None:
            try:
                interface.close()
            except Exception:
                pass


def main():
    print("\n" + "=" * 70)
    print("НАСТРОЙКА БЕЙДЖИКОВ MESHTASTIC ДЛЯ КОНФЕРЕНЦИИ")
    print("=" * 70)
    print(f"Папка: {SCRIPT_DIR}")

    print("\nЗагрузка ключей...")
    chat_psk = load_keys()
    if not chat_psk:
        input("\nНажмите Enter для выхода...")
        sys.exit(1)

    print("Ключ ZN_CHAT загружен.")
    print(f"ZN_CHAT: {chat_psk[:20]}...")

    while True:
        run_one_badge(chat_psk)
        print("\n" + "=" * 70)
        print("Готово.")
        print("Подключите следующий бейджик.")
        print("=" * 70)
        try:
            input("Нажмите Enter для настройки следующего бейджика...")
        except EOFError:
            break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nРабота прервана.")
    except Exception as e:
        print(f"\nОшибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        input("\nНажмите Enter для выхода...")