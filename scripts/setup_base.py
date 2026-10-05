#!/usr/bin/env python3
"""
Настройка базовой ноды для конференции
Канал 0: стандартный открытый Meshtastic (не трогаем)
Канал 1: ZN_CHAT (зашифрованный, для личных сообщений)
"""

import sys
import importlib
import subprocess
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def ensure_dependencies():
    required_packages = {
        'meshtastic': 'meshtastic',
        'serial': 'pyserial',
        'protobuf': 'protobuf'
    }
    missing = []
    for module_name, package_name in required_packages.items():
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(package_name)
    if missing:
        print("=" * 70)
        print("🔧 УСТАНОВКА ОТСУТСТВУЮЩИХ ЗАВИСИМОСТЕЙ")
        print("=" * 70)
        print(f"Отсутствуют: {', '.join(missing)}")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--upgrade"] + missing
            )
            print("✅ Зависимости установлены успешно")
        except Exception as e:
            print(f"❌ Ошибка установки: {e}")
            print(f"   pip install {' '.join(missing)}")
            input("\nНажмите Enter для выхода...")
            sys.exit(1)
    else:
        print("✅ Все зависимости уже установлены")

ensure_dependencies()

import meshtastic.serial_interface
from meshtastic import channel_pb2
import secrets
import time

def get_keys_path():
    return os.path.join(SCRIPT_DIR, "conference_keys.txt")

def load_existing_keys():
    keys_file = get_keys_path()
    if not os.path.exists(keys_file):
        return None
    try:
        with open(keys_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        for line in lines:
            if "ZN_CHAT PSK:" in line:
                return line.split(":")[1].strip()
        return None
    except Exception as e:
        print(f"⚠️  Ошибка чтения: {e}")
        return None

def save_keys(chat_psk_hex):
    keys_file = get_keys_path()
    try:
        with open(keys_file, 'w', encoding='utf-8') as f:
            f.write("КЛЮЧИ ДЛЯ КОНФЕРЕНЦИИ\n")
            f.write("=" * 70 + "\n")
            f.write(f"ZN_CHAT PSK:     {chat_psk_hex}\n")
            f.write("=" * 70 + "\n")
        return True
    except Exception as e:
        print(f"❌ Не удалось сохранить ключи: {e}")
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
        return True
    except Exception:
        return False

def main():
    print("\n" + "=" * 70)
    print("НАСТРОЙКА БАЗОВОЙ НОДЫ ДЛЯ КОНФЕРЕНЦИИ")
    print("=" * 70)
    print(f"📁 Рабочая папка: {SCRIPT_DIR}")
    
    # === Проверка ключей ===
    print("\n🔍 Проверка ключей...")
    chat_psk_hex = load_existing_keys()
    keys_are_new = False
    
    if chat_psk_hex:
        print("✅ Найден файл conference_keys.txt")
        print(f"   ZN_CHAT:     {chat_psk_hex[:20]}...")
        print("💡 Использую СУЩЕСТВУЮЩИЕ ключи")
    else:
        print("📄 Файл не найден — генерирую НОВЫЕ ключи")
        chat_psk = secrets.token_bytes(32)
        chat_psk_hex = chat_psk.hex()
        keys_are_new = True
        print("\n" + "=" * 70)
        print("НОВЫЙ КЛЮЧ")
        print("=" * 70)
        print(f"ZN_CHAT PSK:     {chat_psk_hex}")
        print("=" * 70)
    
    # === Подключение ===
    print("\n🔌 Подключение к базовой ноде...")
    try:
        interface = meshtastic.serial_interface.SerialInterface()
        time.sleep(3)
        print("✅ Подключение успешно")
        print(f"   Node ID: {interface.localNode.nodeNum}")
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        input("\nНажмите Enter для выхода...")
        sys.exit(1)
    
    node = interface.localNode
    
    # === Канал 0: оставляем стандартный открытый ===
    print("\n📡 Канал 0: PRIMARY (стандартный открытый)...")
    print("✅ Не трогаем — оставляем как есть")
    
    # === Канал 1: ZN_CHAT (зашифрованный) ===
    print("\n📡 Канал 1: ZN_CHAT...")
    try:
        ch1 = node.channels[1]
        ch1.settings.name = "ZN_CHAT"
        ch1.settings.psk = bytes.fromhex(chat_psk_hex)
        ch1.role = channel_pb2.Channel.Role.SECONDARY
        ch1.settings.uplink_enabled = True
        node.writeChannel(1)
        print("✅ ZN_CHAT настроен (зашифрованный, для личных сообщений)")
    except Exception as e:
        print(f"❌ Ошибка ZN_CHAT: {e}")
    
    # === Имя ноды ===
    print("\n⚙️  Установка имени ноды...")
    try:
        node.setOwner(long_name="ANNOUNCE", short_name="CONF")
        print("✅ Имя ноды: ANNOUNCE (CONF)")
    except Exception as e:
        print(f"⚠️  Не удалось установить имя: {e}")
    
    # === Настройки ===
    print("\n⚙️  Применение настроек...")
    if apply_config(node, "position", "position_broadcast_secs", 0):
        print("✅ Позиция: OFF")
    if apply_config(node, "position", "position_broadcast_smart_enabled", False):
        print("✅ Smart position: OFF")
    if apply_config(node, "telemetry", "device_update_interval", 0):
        print("✅ Телеметрия устройства: OFF")
    if apply_config(node, "telemetry", "environment_update_interval", 0):
        print("✅ Телеметрия среды: OFF")
    if apply_config(node, "lora", "hop_limit", 2):
        print("✅ Hop limit: 2")
    if apply_config(node, "lora", "tx_power", 14):
        print("✅ TX Power: 14 dBm")
    
    # === Сохранение ключей ===
    if keys_are_new:
        print(f"\n💾 Сохранение ключей: {get_keys_path()}")
        if save_keys(chat_psk_hex):
            print("✅ Ключи сохранены")
    else:
        print(f"\n💾 Файл ключей уже существует (не перезаписываю)")
    
    # Финал
    print("\n" + "=" * 70)
    print("✅ БАЗОВАЯ НОДА НАСТРОЕНА")
    print("=" * 70)
    print(f"\n📁 Ключи: {get_keys_path()}")
    print(f"🏷️  Имя: ZN_CONF (BASE)")
    print(f"\n📋 Структура каналов:")
    print(f"  ✓ Канал 0 (PRIMARY): стандартный открытый Meshtastic")
    print(f"  ✓ Канал 1 (SECONDARY): ZN_CHAT — зашифрованный")
    print(f"\n💡 Анонсы будут отправляться КАЖДОМУ бейджику в личку")
    print(f"   через канал ZN_CHAT (индивидуальные сообщения)")
    
    interface.close()
    print("\n" + "=" * 70)
    input("Нажмите Enter для выхода...")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Прервано")
        input("Нажмите Enter для выхода...")
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        input("Нажмите Enter для выхода...")