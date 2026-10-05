from __future__ import annotations
import json
import os
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

try:
    import meshtastic.serial_interface
    import meshtastic.util
except ImportError:
    meshtastic = None

try:
    from pubsub import pub
except ImportError:
    pub = None

MAX_TEXT_BYTES = 233
CHANNEL_MARKER_PREFIX = "@channel:"
CHAT_RX_DEBUG_LIMIT = 5
CONNECT_HANDSHAKE_TIMEOUT_S = 8.0
ALIVE_CHECK_TIMEOUT_S = 2.0
DISCONNECT_WATCH_INTERVAL_S = 2.0
PORT_WATCH_INTERVAL_S = 1.5
ACK_TIMEOUT_S = 5.0
# Короткий таймаут в «мягком» режиме: если ROUTING-ответ по requestId
# не пришёл за это время, ждать дальше бессмысленно — принимаем, что
# узел недоступен, и идём к следующему.
ANY_ACK_TIMEOUT_S = 1.5
# Диагностика: включать на время тестовой рассылки, потом выключить —
# иначе журнал забьётся RX routing на каждый пакет.
DEBUG_ROUTING = True

ACK_BUFFER_MAX = 128
ACK_BUFFER_TTL_S = 30.0


@dataclass
class SendJob:
    job_id: str
    message: str
    recipients: list[str]
    channel_index: int
    broadcast_channel: str = ""
    source_task_id: str = ""
    next_index: int = 0
    delivered: list[str] = field(default_factory=list)
    not_delivered: list[str] = field(default_factory=list)
    phase: str = "first"
    status: str = "running"


class MeshtasticWorker(QObject):
    connected = pyqtSignal(bool, str)
    log = pyqtSignal(str, str)
    progress = pyqtSignal(int, int, str)
    finished = pyqtSignal(object)
    paused = pyqtSignal()
    stopped = pyqtSignal()
    failed = pyqtSignal(str)
    resume_available = pyqtSignal(object)
    port_list = pyqtSignal(object)
    channels_info = pyqtSignal(object)
    chat_message = pyqtSignal(int, str, str, str)
    device_appeared = pyqtSignal(str)

    def __init__(self, state_path: Path) -> None:
        super().__init__()
        self.state_path = state_path
        self.interface = None
        self.port: Optional[str] = None
        self._local_node_num: Optional[int] = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.clear()
        self.job: Optional[SendJob] = None
        self._send_thread: Optional[threading.Thread] = None
        self._pending_source_task_id: str = ""
        self._rx_debug_count = 0
        self._lock = threading.RLock()

        self._ack_lock = threading.Lock()
        self._ack_results: "OrderedDict[int, tuple[str, object, float, str]]" = OrderedDict()
        self._ack_waiters: dict[int, tuple[threading.Event, Optional[int]]] = {}

        self._watch_stop = threading.Event()
        self._watch_thread: Optional[threading.Thread] = None

        self._port_watch_stop = threading.Event()
        self._port_watch_thread: Optional[threading.Thread] = None
        self._known_ports: set[str] = set()
        self._known_ports_initialized = False

        self._connecting = False
        # «Мягкий» режим ACK: принимать ROUTING-ответ от любой ноды, не
        # дожидаясь именно адресата. Управляется из UI (set_any_ack).
        self._any_ack = False

        if pub is not None:
            for topic in ("meshtastic.receive.routing", "meshtastic.receive.ack"):
                try:
                    pub.subscribe(self._on_routing, topic)
                except Exception:
                    pass
            try:
                pub.subscribe(self._on_receive_text, "meshtastic.receive.text")
            except Exception:
                pass
            try:
                pub.subscribe(
                    self._on_connection_lost, "meshtastic.connection.lost"
                )
            except Exception:
                pass

    # -------------------------------------------------------------- state

    def _load_state(self) -> Optional[dict]:
        if not self.state_path.exists():
            return None
        try:
            with self.state_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("status") in {"running", "paused", "connection_lost"}:
                return data
        except Exception:
            pass
        return None

    def check_resume(self) -> None:
        state = self._load_state()
        if state:
            self.resume_available.emit(state)

    def _save_state(self) -> None:
        with self._lock:
            job = self.job
            if job is None:
                return
            payload = {
                "job_id": job.job_id,
                "message": job.message,
                "recipients": job.recipients,
                "channel_index": job.channel_index,
                "broadcast_channel": job.broadcast_channel,
                "source_task_id": job.source_task_id,
                "next_index": job.next_index,
                "delivered": list(job.delivered),
                "not_delivered": list(job.not_delivered),
                "phase": job.phase,
                "status": job.status,
                "updated_at": time.time(),
            }
        ok, err = self._write_json_atomic(self.state_path, payload)
        if not ok:
            self.log.emit(
                f"Не удалось сохранить состояние ({err}). Рассылка продолжается.",
                "warning",
            )

    def _clear_state(self) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(5):
            try:
                self.state_path.unlink()
                return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_exc = exc
                time.sleep(0.1 * (2 ** attempt))
        if last_exc is not None:
            self.log.emit(
                f"Не удалось удалить {self.state_path.name} ({last_exc}).",
                "warning",
            )

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> tuple[bool, Optional[Exception]]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                prefix=path.name + ".",
                suffix=".tmp",
                dir=str(path.parent),
            )
        except OSError as exc:
            return False, exc

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            return False, exc

        last_exc: Optional[Exception] = None
        for attempt in range(5):
            try:
                os.replace(tmp_name, path)
                return True, None
            except OSError as exc:
                last_exc = exc
                time.sleep(0.1 * (2 ** attempt))

        try:
            with path.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            return True, None
        except Exception as exc:
            return False, exc
        finally:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass

    @staticmethod
    def load_badges(path: Path) -> list[str]:
        if not path.exists():
            return []
        result: list[str] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        result.append(parts[2])
                    else:
                        result.append(line)
        return result

    # --------------------------------------------------------- chat receive

    @staticmethod
    def _short_node_id(raw) -> str:
        s = str(raw or "").strip()
        if s.startswith("!") and len(s) > 5:
            return s[-4:]
        return s

    def _on_receive_text(self, packet=None, interface=None, **kwargs) -> None:
        try:
            if not isinstance(packet, dict):
                return
            decoded = packet.get("decoded") or {}
            text = decoded.get("text", "")
            if not text:
                return

            # Protobuf json_format не пишет поля со значением по умолчанию,
            # а у channel дефолт 0. Для primary-канала ключа в dict нет.
            ch = packet.get("channel")
            if ch is None:
                ch = 0

            from_id = packet.get("fromId") or packet.get("from") or ""

            if self._rx_debug_count < CHAT_RX_DEBUG_LIMIT:
                self._rx_debug_count += 1
                self.log.emit(
                    f"RX text: channel={ch} from={from_id}", "info"
                )

            ts = time.strftime("%H:%M:%S")
            short_id = self._short_node_id(from_id)
            self.chat_message.emit(int(ch), ts, short_id, str(text))
        except Exception:
            pass

    # ------------------------------------------------------------- ACK/NAK

    def _reset_ack_state(self) -> None:
        """Будит все активные ожидания (иначе wait_for_ack висит до таймаута)
        и чистит буферы — это нужно при Стопе/паузе/старте новой рассылки."""
        with self._ack_lock:
            for ev, _exp in self._ack_waiters.values():
                try:
                    ev.set()
                except Exception:
                    pass
            self._ack_results.clear()
            self._ack_waiters.clear()

    def _prune_ack_buffer_locked(self, now: float) -> None:
        expired = [
            pid for pid, entry in self._ack_results.items()
            if now - entry[2] > ACK_BUFFER_TTL_S
        ]
        for pid in expired:
            self._ack_results.pop(pid, None)
        while len(self._ack_results) > ACK_BUFFER_MAX:
            self._ack_results.popitem(last=False)

    def _on_routing(self, packet=None, interface=None, **kwargs) -> None:
        try:
            if not isinstance(packet, dict):
                return
            decoded = packet.get("decoded")
            if not isinstance(decoded, dict):
                return
            routing = decoded.get("routing")
            if routing is None:
                return
            request_id = decoded.get("requestId")
            if not request_id:
                return

            pid = int(request_id)
            from_id = packet.get("from")

            err = routing.get("errorReason", "NONE") if isinstance(routing, dict) else "NONE"
            if isinstance(err, int):
                err_str = "NONE" if err == 0 else str(err)
            else:
                err_str = str(err).upper()
            is_none = err_str in ("NONE", "0", "")
            is_pki = "PKI" in err_str

            if DEBUG_ROUTING:
                try:
                    dbg = json.dumps(
                        {"requestId": pid, "from": from_id, "errorReason": err_str},
                        ensure_ascii=False,
                    )
                except Exception:
                    dbg = f"requestId={pid} from={from_id} err={err_str}"
                self.log.emit(f"RX routing: {dbg}", "info")

            now = time.monotonic()
            with self._ack_lock:
                waiter = self._ack_waiters.get(pid)

                if is_pki:
                    self._ack_results[pid] = ("pki", from_id, now, err_str)
                    if waiter is not None:
                        waiter[0].set()
                    else:
                        self._prune_ack_buffer_locked(now)
                    return

                if waiter is not None:
                    expected = waiter[1]
                    if expected is not None:
                        if from_id is None:
                            return
                        try:
                            if int(from_id) != int(expected):
                                return
                        except Exception:
                            return
                    # expected is None — «мягкий» режим: принимаем ответ от
                    # любой ноды, лишь бы requestId совпал.
                    result = "ack" if is_none else "nak"
                    self._ack_results[pid] = (result, from_id, now, err_str)
                    waiter[0].set()
                else:
                    result = "ack" if is_none else "nak"
                    self._ack_results[pid] = (result, from_id, now, err_str)
                    self._prune_ack_buffer_locked(now)
        except Exception:
            pass

    def _wait_for_ack(
        self,
        packet_id: int,
        expected_from: Optional[int],
        timeout: float,
    ) -> tuple[Optional[str], object, str]:
        if not packet_id:
            return None, None, ""

        now = time.monotonic()
        with self._ack_lock:
            self._prune_ack_buffer_locked(now)
            entry = self._ack_results.pop(packet_id, None)
            if entry is not None:
                result, from_id, _ts, err_str = entry
                if result == "pki":
                    return result, from_id, err_str
                if expected_from is None:
                    # «мягкий» режим — любой ответ годится
                    return result, from_id, err_str
                if from_id is not None:
                    try:
                        if int(from_id) == int(expected_from):
                            return result, from_id, err_str
                    except Exception:
                        pass
            ev = threading.Event()
            self._ack_waiters[packet_id] = (ev, expected_from)

        got = ev.wait(timeout)

        with self._ack_lock:
            self._ack_waiters.pop(packet_id, None)
            if got:
                entry = self._ack_results.pop(packet_id, None)
                if entry is not None:
                    result, from_id, _ts, err_str = entry
                    return result, from_id, err_str
            self._ack_results.pop(packet_id, None)
            return None, None, ""

    # -------------------------------------------- port watcher (always on)

    @staticmethod
    def _list_serial_ports() -> set[str]:
        try:
            import serial.tools.list_ports
            return {
                (p.device or "").strip()
                for p in serial.tools.list_ports.comports()
                if p.device
            }
        except Exception:
            return set()

    def _port_watch_loop(self) -> None:
        while not self._port_watch_stop.wait(PORT_WATCH_INTERVAL_S):
            try:
                self._check_port_changes()
            except Exception:
                pass

    def _check_port_changes(self) -> None:
        ports = self._list_serial_ports()

        with self._lock:
            if not self._known_ports_initialized:
                self._known_ports = ports
                self._known_ports_initialized = True
                return

            new_ports = ports - self._known_ports
            self._known_ports = ports
            connected = self.interface is not None
            connecting = self._connecting

        if not new_ports:
            return
        if connected or connecting:
            return

        port = sorted(new_ports)[0]
        self.log.emit(f"Появилось устройство: {port}", "info")
        self.device_appeared.emit(port)

    def _start_port_watch_thread(self) -> None:
        self._stop_port_watch_thread()
        self._port_watch_stop.clear()
        self._port_watch_thread = threading.Thread(
            target=self._port_watch_loop, daemon=True, name="mesh-port-watch"
        )
        self._port_watch_thread.start()

    def _stop_port_watch_thread(self) -> None:
        self._port_watch_stop.set()
        t = self._port_watch_thread
        self._port_watch_thread = None
        if t is not None and t is not threading.current_thread():
            try:
                t.join(timeout=1.5)
            except Exception:
                pass

    @pyqtSlot()
    def start_port_watch(self) -> None:
        self._start_port_watch_thread()

    @pyqtSlot()
    def stop_port_watch(self) -> None:
        self._stop_port_watch_thread()

    # ---------------------------------------------- connection loss watch

    def _get_stream_port_name(self) -> Optional[str]:
        if self.interface is None:
            return None
        stream = getattr(self.interface, "stream", None)
        if stream is not None:
            for attr in ("portstr", "port", "name"):
                try:
                    val = getattr(stream, attr, None)
                except Exception:
                    continue
                if val:
                    return str(val)
        for attr in ("devPath", "port"):
            try:
                val = getattr(self.interface, attr, None)
            except Exception:
                continue
            if val:
                return str(val)
        return None

    def _check_connection_alive(self) -> None:
        if self.interface is None:
            return
        actual = self._get_stream_port_name()
        if actual:
            available = {p.upper() for p in self._list_serial_ports()}
            if available and actual.strip().upper() not in available:
                self._handle_connection_lost(f"порт {actual} пропал из системы")
                return
        stream = getattr(self.interface, "stream", None)
        if stream is not None:
            try:
                is_open = getattr(stream, "is_open", None)
            except Exception:
                is_open = None
            if is_open is False:
                self._handle_connection_lost("поток ввода-вывода закрыт")
                return

    def _watch_loop(self) -> None:
        while not self._watch_stop.wait(DISCONNECT_WATCH_INTERVAL_S):
            try:
                self._check_connection_alive()
            except Exception:
                pass

    def _start_watch_thread(self) -> None:
        self._stop_watch_thread()
        self._watch_stop.clear()
        self._watch_thread = threading.Thread(
            target=self._watch_loop, daemon=True, name="mesh-watch"
        )
        self._watch_thread.start()

    def _stop_watch_thread(self) -> None:
        self._watch_stop.set()
        t = self._watch_thread
        self._watch_thread = None
        if t is not None and t is not threading.current_thread():
            try:
                t.join(timeout=1.5)
            except Exception:
                pass

    def _on_connection_lost(self, **kwargs) -> None:
        self._handle_connection_lost("meshtastic lib сообщила о разрыве")

    @staticmethod
    def _safe_close_interface(iface) -> None:
        try:
            iface.close()
        except Exception:
            pass

    def _handle_connection_lost(self, reason: str) -> None:
        if self.interface is None:
            return
        iface = self.interface
        self.interface = None
        self._watch_stop.set()

        with self._lock:
            prev_status = self.job.status if self.job is not None else None
            if prev_status == "running" and self.job is not None:
                self.job.status = "paused"
        was_running = (prev_status == "running")

        if was_running:
            self._pause.set()
            self._reset_ack_state()
            self._save_state()

        self.log.emit(f"Потеря связи с нодой: {reason}", "warning")
        self.connected.emit(False, "Устройство отключено")
        self.channels_info.emit([])

        if was_running:
            self.log.emit(
                "Рассылка поставлена на паузу. Переподключитесь и нажмите "
                "«Продолжить».",
                "warning",
            )
            self.paused.emit()
        elif prev_status is None:
            self.failed.emit(
                "Потеря связи с базовой нодой. Подключите устройство заново."
            )

        threading.Thread(
            target=self._safe_close_interface,
            args=(iface,),
            daemon=True,
            name="mesh-close",
        ).start()

    # -------------------------------------------------------------- ports

    @pyqtSlot()
    def list_ports(self) -> None:
        if meshtastic is None:
            self.port_list.emit([])
            return
        try:
            ports = meshtastic.util.findPorts(True)
        except Exception:
            ports = sorted(self._list_serial_ports())
        self.port_list.emit(ports)

    # -------------------------------------------------------------- connect

    def _close_interface_silent(self) -> None:
        self._watch_stop.set()
        iface = self.interface
        self.interface = None
        if iface is not None:
            threading.Thread(
                target=self._safe_close_interface,
                args=(iface,),
                daemon=True,
                name="mesh-close-silent",
            ).start()

    def _wait_for_local_node(
        self, iface, timeout: float = CONNECT_HANDSHAKE_TIMEOUT_S
    ) -> Optional[int]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                wait = getattr(iface, "waitForConfig", None)
                if wait is not None:
                    try:
                        wait(timeout=1)
                    except Exception:
                        pass
            except Exception:
                pass
            try:
                local = getattr(iface, "localNode", None)
                num = getattr(local, "nodeNum", None) if local is not None else None
                if num:
                    return int(num)
            except Exception:
                pass
            try:
                mi = getattr(iface, "myInfo", None)
                num2 = getattr(mi, "myNodeNum", None) if mi is not None else None
                if num2:
                    return int(num2)
            except Exception:
                pass
            time.sleep(0.2)
        return None

    def _is_interface_alive(self, timeout: float = ALIVE_CHECK_TIMEOUT_S) -> bool:
        if self.interface is None:
            return False
        return self._wait_for_local_node(self.interface, timeout=timeout) is not None

    def _collect_channels(self) -> list[tuple[int, str]]:
        result: list[tuple[int, str]] = []
        if self.interface is None:
            return result
        try:
            node = getattr(self.interface, "localNode", None)
            channels = getattr(node, "channels", None)
            if not channels:
                return result
            if isinstance(channels, dict):
                iterable = channels.items()
            else:
                iterable = enumerate(channels)
            for idx, ch in iterable:
                if ch is None:
                    continue
                settings = getattr(ch, "settings", None)
                nm = (getattr(settings, "name", "") if settings else "") or ""
                nm = nm.strip()
                role = getattr(ch, "role", None)
                try:
                    role_int = int(role) if role is not None else None
                except (TypeError, ValueError):
                    role_int = None
                if role_int is not None:
                    # role: 0=DISABLED, 1=PRIMARY, 2=SECONDARY
                    if role_int == 0:
                        continue
                elif not nm:
                    # Роль недоступна — как раньше, только именованные.
                    continue
                if not nm:
                    nm = f"канал {int(idx)}"
                result.append((int(idx), nm))
        except Exception:
            pass
        return result

    @pyqtSlot(str)
    def connect_radio(self, port: str) -> None:
        self._connecting = True
        try:
            self._close_interface_silent()
            self._stop_watch_thread()

            iface = None
            try:
                if meshtastic is None:
                    raise RuntimeError("Пакет meshtastic не установлен")

                self.log.emit("Подключение к Meshtastic...", "info")
                if port.strip():
                    iface = meshtastic.serial_interface.SerialInterface(
                        port.strip(), connectNow=True
                    )
                else:
                    iface = meshtastic.serial_interface.SerialInterface(
                        connectNow=True
                    )

                node_num = self._wait_for_local_node(iface)
                if node_num is None:
                    try:
                        iface.close()
                    except Exception:
                        pass
                    raise RuntimeError(
                        "Порт открыт, но базовая нода не ответила "
                        "(нет myInfo/localNode.nodeNum). Проверьте питание, "
                        "прошивку и что это действительно Meshtastic-устройство."
                    )

                self.interface = iface
                self.port = port.strip() or "AUTO"
                self._local_node_num = int(node_num)
                self.connected.emit(True, f"Подключено | Node {node_num}")
                self.log.emit(
                    f"Подключение успешно: {self.port} (Node {node_num})",
                    "success",
                )

                self._log_channels()
                self._rx_debug_count = 0

                self._start_watch_thread()

                self.channels_info.emit(self._collect_channels())

            except Exception as exc:
                if iface is not None and self.interface is not iface:
                    try:
                        iface.close()
                    except Exception:
                        pass
                self.interface = None
                self._local_node_num = None
                self.connected.emit(False, "Не подключено")
                self.channels_info.emit([])
                self.failed.emit(f"Ошибка подключения: {exc}")
        finally:
            self._connecting = False

    def _log_channels(self) -> None:
        try:
            node = getattr(self.interface, "localNode", None)
            channels = getattr(node, "channels", None)
            if not channels:
                self.log.emit("Каналы ноды недоступны", "warning")
                return
            iterable = (
                channels.items() if isinstance(channels, dict)
                else enumerate(channels)
            )
            names: list[str] = []
            for idx, ch in iterable:
                if ch is None:
                    continue
                settings = getattr(ch, "settings", None)
                nm = getattr(settings, "name", "") if settings else ""
                names.append(f"{idx}:'{nm or '(без имени)'}'")
            self.log.emit("Каналы ноды: " + ", ".join(names), "info")
        except Exception as exc:
            self.log.emit(f"Ошибка чтения каналов: {exc}", "warning")

    @pyqtSlot()
    def disconnect_radio(self) -> None:
        self._close_interface_silent()
        self._stop_watch_thread()
        self._local_node_num = None
        self.connected.emit(False, "Не подключено")
        self.channels_info.emit([])

    # -------------------------------------------------------------- channels

    def _find_channel_index(self, name: str) -> Optional[int]:
        if self.interface is None or not name:
            return None
        try:
            node = getattr(self.interface, "localNode", None)
            if node is None:
                return None
            channels = getattr(node, "channels", None)
            if not channels:
                return None
            if isinstance(channels, dict):
                iterable = channels.items()
            else:
                iterable = enumerate(channels)
            for idx, ch in iterable:
                if ch is None:
                    continue
                settings = getattr(ch, "settings", None)
                if settings is None:
                    continue
                ch_name = getattr(settings, "name", "") or ""
                if ch_name == name:
                    return int(idx)
        except Exception:
            pass
        return None

    # -------------------------------------------------------------- helpers

    def _send_thread_alive(self) -> bool:
        t = self._send_thread
        return t is not None and t.is_alive()

    def is_busy(self) -> bool:
        """Есть ли активная или приостановленная рассылка (потокобезопасно)."""
        with self._lock:
            return self.job is not None or self._send_thread_alive()

    def _launch_send_thread(self) -> None:
        t = threading.Thread(target=self._send_loop_safe, daemon=True)
        self._send_thread = t
        t.start()

    def _send_loop_safe(self) -> None:
        try:
            self._send_loop()
        except Exception as exc:
            try:
                self.failed.emit(f"Ошибка рассылки: {exc}")
            except Exception:
                pass

    # -------------------------------------------------------------- send

    @pyqtSlot(str, str, int)
    def start_send(self, message: str, recipients_json: str, channel: int) -> None:
        # Потребляем source_task_id сразу: при отказе валидации он не должен
        # прилипнуть к следующей (чужой) рассылке.
        with self._lock:
            source = self._pending_source_task_id
            self._pending_source_task_id = ""
        if self._send_thread_alive():
            self.failed.emit("Рассылка уже идёт")
            return
        try:
            raw = [str(x) for x in json.loads(recipients_json)]
        except Exception:
            self.failed.emit("Некорректный список получателей")
            return

        broadcast_channel = ""
        if len(raw) == 1 and raw[0].startswith(CHANNEL_MARKER_PREFIX):
            broadcast_channel = raw[0][len(CHANNEL_MARKER_PREFIX):].strip()
            recipients: list[str] = []
        else:
            seen: set[str] = set()
            recipients = []
            for r in raw:
                if r not in seen:
                    seen.add(r)
                    recipients.append(r)

        payload_size = len(message.encode("utf-8"))
        if not message.strip():
            self.failed.emit("Сообщение пустое")
            return
        if payload_size > MAX_TEXT_BYTES:
            self.failed.emit(
                f"Сообщение слишком длинное: {payload_size} байт, максимум {MAX_TEXT_BYTES}"
            )
            return
        if not broadcast_channel and not recipients:
            self.failed.emit("Нет получателей")
            return
        if self.interface is None:
            self.failed.emit("Базовая нода не подключена")
            return

        if not self._is_interface_alive():
            self.failed.emit(
                "Базовая нода не отвечает. Переподключите устройство."
            )
            return

        self._reset_ack_state()

        with self._lock:
            self._stop.clear()
            self._pause.clear()
            self.job = SendJob(
                job_id=uuid.uuid4().hex,
                message=message,
                recipients=recipients,
                channel_index=int(channel),
                broadcast_channel=broadcast_channel,
                source_task_id=source,
            )
        self._save_state()
        self._launch_send_thread()

    @pyqtSlot()
    def pause_send(self) -> None:
        if not self._send_thread_alive():
            return
        self._pause.set()
        # Будим ожидание ACK, чтобы пауза наступала сразу, а не по таймауту.
        self._reset_ack_state()
        with self._lock:
            if self.job is not None:
                self.job.status = "paused"
        self._save_state()
        self.paused.emit()
        self.log.emit("Рассылка поставлена на паузу", "warning")

    @pyqtSlot()
    def resume_send(self) -> None:
        with self._lock:
            if self.job is None:
                self.failed.emit("Нет активной рассылки для продолжения")
                return
            if self.interface is None:
                self.failed.emit("Сначала подключите базовую ноду")
                return

        if self._send_thread_alive():
            self._send_thread.join(timeout=1.5)
            if self._send_thread_alive():
                self.log.emit(
                    "Подождите, предыдущий поток ещё завершается", "warning"
                )
                return

        if not self._is_interface_alive():
            self.failed.emit(
                "Базовая нода не отвечает. Переподключите устройство."
            )
            return

        self._reset_ack_state()

        with self._lock:
            if self.job is None:
                return
            self._stop.clear()
            self._pause.clear()
            self.job.status = "running"
        self._save_state()
        self.log.emit("Рассылка продолжена", "info")
        self._launch_send_thread()

    @pyqtSlot()
    def stop_send(self) -> None:
        self._stop.set()
        self._pause.clear()
        with self._lock:
            self.job = None
            self._pending_source_task_id = ""
        # Разбудим ожидания ACK, чтобы поток вышел немедленно.
        self._reset_ack_state()
        self._clear_state()
        self.log.emit("Рассылка отменена. Очередь очищена.", "warning")
        self.stopped.emit()

    @pyqtSlot()
    def shutdown(self) -> None:
        self._stop_watch_thread()
        self._stop_port_watch_thread()
        self._stop.set()
        self._pause.clear()
        with self._lock:
            has_job = self.job is not None
        if has_job:
            self._save_state()

    @pyqtSlot(str)
    def resume_from_state(self, state_json: str) -> None:
        if self._send_thread_alive():
            self.failed.emit("Рассылка уже идёт")
            return
        if self.interface is None:
            self.failed.emit("Сначала подключите базовую ноду")
            return
        try:
            state = json.loads(state_json)
            with self._lock:
                self.job = SendJob(
                    job_id=state["job_id"],
                    message=state["message"],
                    recipients=[str(x) for x in state.get("recipients", [])],
                    channel_index=int(state.get("channel_index", 0)),
                    broadcast_channel=str(state.get("broadcast_channel", "") or ""),
                    source_task_id=str(state.get("source_task_id", "") or ""),
                    next_index=int(state.get("next_index", 0)),
                    delivered=[str(x) for x in state.get("delivered", [])],
                    not_delivered=[str(x) for x in state.get("not_delivered", [])],
                    phase=str(state.get("phase", "first") or "first"),
                    status="running",
                )
                self._stop.clear()
                self._pause.clear()
            self._reset_ack_state()
            self._save_state()
            self._launch_send_thread()
        except Exception as exc:
            self.failed.emit(f"Не удалось продолжить рассылку: {exc}")

    @pyqtSlot(str)
    def set_source_task_id(self, task_id: str) -> None:
        with self._lock:
            self._pending_source_task_id = task_id or ""

    @pyqtSlot(bool)
    def set_any_ack(self, enabled: bool) -> None:
        """Переключить режим «принимать ACK от любой ноды».

        True  — не проверяем, что ROUTING-ответ пришёл именно от адресата;
                подходит для массовых рассылок, где часть бейджиков оффлайн.
                Использует укороченный таймаут ANY_ACK_TIMEOUT_S и не делает
                повторный проход.
        False — строгий режим: ждём ACK только от получателя, таймаут
                ACK_TIMEOUT_S, есть второй проход.
        """
        self._any_ack = bool(enabled)
        self.log.emit(
            "Режим ACK: "
            + ("любая нода" if self._any_ack else "только адресат"),
            "info",
        )

    # -------------------------------------------------------------- loop

    def _send_one(self, job: SendJob, node_id: str) -> tuple[str, object]:
        try:
            packet = self.interface.sendText(
                text=job.message,
                destinationId=int(node_id),
                channelIndex=job.channel_index,
                wantAck=True,
            )
        except Exception as exc:
            if self._looks_like_port_error(exc):
                return "connection_lost", None
            return "error", None

        packet_id = getattr(packet, "id", None) if packet is not None else None
        if not packet_id:
            return "no_packet_id", None

        if self._any_ack:
            # «Мягкий» режим: принимаем ROUTING-ответ от любой ноды и
            # ждём недолго — если ответа нет, дальше ждать бессмысленно.
            expected_from: Optional[int] = None
            timeout = ANY_ACK_TIMEOUT_S
        else:
            try:
                expected_from = int(node_id)
            except Exception:
                expected_from = None
            timeout = ACK_TIMEOUT_S

        result, from_id, _err_str = self._wait_for_ack(
            int(packet_id), expected_from, timeout
        )
        if result == "ack":
            return "ack", from_id
        if result == "nak":
            return "nak", from_id
        if result == "pki":
            return "pki", from_id
        return "timeout", None

    def _store_progress(
        self, delivered: list[str], not_delivered: list[str]
    ) -> None:
        with self._lock:
            if self.job is not None:
                self.job.delivered = list(delivered)
                self.job.not_delivered = list(not_delivered)
        self._save_state()

    def _abort_send_connection_lost(self) -> None:
        with self._lock:
            if self.job is not None and self.job.status == "running":
                self.job.status = "connection_lost"
        self._save_state()
        self.failed.emit(
            "COM-порт недоступен. Рассылка сохранена и поставлена на паузу."
        )

    @staticmethod
    def _fmt_status(status: str) -> str:
        return {
            "ack": "доставлено",
            "timeout": "нет ответа",
            "nak": "NAK",
            "pki": "PKI, пропуск",
            "no_packet_id": "нет id",
            "error": "ошибка",
        }.get(status, status)

    def _log_one(self, prefix: str, status: str, node_id: str) -> None:
        level = "success" if status == "ack" else "warning"
        self.log.emit(f"{prefix} {node_id}: {self._fmt_status(status)}", level)

    def _send_loop(self) -> None:
        with self._lock:
            job = self.job
        if job is None:
            return

        if job.broadcast_channel:
            self._send_broadcast_loop(job)
            return

        total = len(job.recipients)
        delivered: list[str] = list(job.delivered)
        not_delivered: list[str] = list(job.not_delivered)

        # ------------------------------------------------ первый проход
        if job.phase != "second":
            if job.next_index == 0:
                if self._any_ack:
                    ack_info = (
                        f"режим «любая нода», таймаут {ANY_ACK_TIMEOUT_S:.1f} с, "
                        "без повторов"
                    )
                else:
                    ack_info = (
                        f"режим «только адресат», таймаут {ACK_TIMEOUT_S:.0f} с, "
                        "есть повторный проход"
                    )
                self.log.emit(
                    f"Рассылка {job.job_id[:8]}: {total} получателей, "
                    f"канал {job.channel_index}, {ack_info}",
                    "info",
                )

            for i in range(job.next_index, total):
                if self._stop.is_set() or self._pause.is_set():
                    return
                if self.interface is None:
                    self._abort_send_connection_lost()
                    return

                node_id = job.recipients[i]
                status, _from_id = self._send_one(job, node_id)

                # Стоп во время ожидания ACK — тихий выход без эмитов.
                if self._stop.is_set() or self.job is None:
                    return
                if self._pause.is_set():
                    return
                if status == "connection_lost":
                    self._abort_send_connection_lost()
                    return

                if status == "ack":
                    delivered.append(node_id)
                else:
                    not_delivered.append(node_id)

                self._log_one(f"[{i + 1}/{total}]", status, node_id)

                with self._lock:
                    if self.job is not None:
                        self.job.next_index = i + 1
                self._store_progress(delivered, not_delivered)
                self.progress.emit(i + 1, total, str(node_id))

            with self._lock:
                if self.job is not None:
                    self.job.phase = "second"
            self._store_progress(delivered, not_delivered)

        # ------------------------------------------------ второй проход
        # В «мягком» режиме повторы не имеют смысла: если ответ по requestId
        # не пришёл за ANY_ACK_TIMEOUT_S, он не придёт и за следующие полторы
        # секунды — только зря удвоим время рассылки.
        if (not_delivered and not self._any_ack
                and not self._stop.is_set() and not self._pause.is_set()):
            retry = list(not_delivered)
            not_delivered = []
            retry_total = len(retry)
            self.log.emit(
                f"Повторный проход: {retry_total} неподтверждённых",
                "info",
            )

            for j, node_id in enumerate(retry):
                if self._stop.is_set() or self._pause.is_set():
                    not_delivered.extend(retry[j:])
                    self._store_progress(delivered, not_delivered)
                    return
                if self.interface is None:
                    not_delivered.extend(retry[j:])
                    self._store_progress(delivered, not_delivered)
                    self._abort_send_connection_lost()
                    return

                status, _from_id = self._send_one(job, node_id)

                if self._stop.is_set() or self.job is None:
                    return
                if self._pause.is_set():
                    not_delivered.extend(retry[j:])
                    self._store_progress(delivered, not_delivered)
                    return
                if status == "connection_lost":
                    not_delivered.extend(retry[j:])
                    self._store_progress(delivered, not_delivered)
                    self._abort_send_connection_lost()
                    return

                if status == "ack":
                    delivered.append(node_id)
                else:
                    not_delivered.append(node_id)

                self._log_one(
                    f"[retry {j + 1}/{retry_total}]", status, node_id
                )
                self._store_progress(delivered, not_delivered)
                self.progress.emit(j + 1, retry_total, str(node_id))

        # ------------------------------------------------ финиш
        with self._lock:
            if self.job is None:
                return
            self.job.status = "completed"
            job_id = self.job.job_id
            source_task_id = self.job.source_task_id
            self.job = None
        self._save_state_after_complete(job_id, total)

        delivered_n = len(delivered)
        failed_n = len(not_delivered)

        self.finished.emit({
            "job_id": job_id,
            "total": total,
            "sent": delivered_n,
            "not_delivered": failed_n,
            "undelivered": list(not_delivered),
            "source_task_id": source_task_id,
        })

    def _send_broadcast_loop(self, job: SendJob) -> None:
        target = job.broadcast_channel
        self.log.emit(
            f"Рассылка {job.job_id[:8]}: в канал {target}",
            "info",
        )
        try:
            ch_idx = self._find_channel_index(target)
            if ch_idx is None:
                with self._lock:
                    if self.job is not None:
                        self.job.status = "paused"
                self._save_state()
                self.log.emit(f"ERR канал {target}: не найден", "error")
                self.failed.emit(
                    f"Канал {target} не найден на ноде. Рассылка сохранена."
                )
                return
            self.interface.sendText(
                text=job.message,
                channelIndex=ch_idx,
                wantAck=False,
            )
            ts = time.strftime("%H:%M:%S")
            self.chat_message.emit(int(ch_idx), ts, "me", job.message)
        except Exception as exc:
            with self._lock:
                if self.job is not None:
                    if self._looks_like_port_error(exc):
                        self.job.status = "connection_lost"
                    else:
                        self.job.status = "paused"
            self._save_state()
            self.log.emit(f"ERR канал {target}: {exc}", "error")
            self.failed.emit(
                f"Ошибка отправки в канал {target}. Рассылка сохранена."
            )
            return

        with self._lock:
            if self.job is None:
                return
            self.job.status = "completed"
            job_id = self.job.job_id
            source_task_id = self.job.source_task_id
            self.job = None
        self._save_state_after_complete(job_id, 1)
        self.finished.emit({
            "job_id": job_id,
            "total": 1,
            "sent": 1,
            "not_delivered": 0,
            "undelivered": [],
            "source_task_id": source_task_id,
        })

    def _save_state_after_complete(self, job_id: str, total: int) -> None:
        payload = {
            "job_id": job_id,
            "status": "completed",
            "next_index": total,
            "updated_at": time.time(),
        }
        self._write_json_atomic(self.state_path, payload)
        self._clear_state()

    @staticmethod
    def _looks_like_port_error(exc: Exception) -> bool:
        text = str(exc).lower()
        needles = (
            "serial", "port", "device", "access denied", "permission",
            "no such file", "disconnected", "input/output error",
            "i/o error", "clearcommerror", "writefile", "readfile",
        )
        return any(x in text for x in needles)