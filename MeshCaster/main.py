from __future__ import annotations
import argparse
import json
import sys
import ctypes
from pathlib import Path
from PyQt6.QtCore import QThread, QObject, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QApplication
from meshtastic_worker import MeshtasticWorker
from scheduler import SchedulerWorker
from ui_main_window import MainWindow

BASE = Path(__file__).resolve().parent
BADGES = BASE / "badges_list.txt"
STATE = BASE / "send_state.json"
SCHEDULE = BASE / "schedule_queue.json"

AUTO_CONNECT_DELAY_MS = 200
SCHEDULE_POSTPONE_S = 60
UNDELIVERED_PREVIEW = 5


def _set_windows_app_id(app_id: str) -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="MeshCaster",
        description="Meshtastic Broadcast Manager",
        add_help=True,
    )
    parser.add_argument(
        "-f", "--fullscreen",
        action="store_true",
        help="Запустить в полноэкранном режиме (без рамки и заголовка)",
    )
    parser.add_argument(
        "-m", "--maximized",
        action="store_true",
        help="Запустить развёрнутым на весь экран (с рамкой окна)",
    )
    args, _unknown = parser.parse_known_args(argv)
    return args


class AppController(QObject):

    schedule_run_requested = pyqtSignal(str)
    schedule_task_done = pyqtSignal(str)
    schedule_postpone_requested = pyqtSignal(str, int)

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window
        self._pending_resume: dict | None = None
        self._auto_connect_attempted = False
        self._connected = False

        self.mesh_thread = QThread(self)
        self.mesh = MeshtasticWorker(STATE)
        self.mesh.moveToThread(self.mesh_thread)
        self.mesh_thread.started.connect(self.mesh.check_resume)
        self.mesh_thread.started.connect(self.mesh.start_port_watch)
        self.mesh_thread.start()

        self.scheduler_thread = QThread(self)
        self.scheduler = SchedulerWorker(SCHEDULE)
        self.scheduler.moveToThread(self.scheduler_thread)
        self.scheduler_thread.started.connect(self.scheduler.run)
        self.scheduler_thread.start()

        self._wire()

        # Сообщаем воркеру текущее значение режима ACK, сохранённое в UI.
        # Сигнал пойдёт как QueuedConnection — воркер живёт в mesh_thread.
        self.window.any_ack_changed.emit(self.window.any_ack())

        self.window.ports_requested.emit()
        self.window.log("Приложение запущено", "success")
        self.window.log(f"Файл бейджиков: {BADGES}", "info")

        # Первичная загрузка расписания: не полагаемся на changed.emit()
        # из SchedulerWorker.run() — к моменту старта рабочего потока
        # _wire() мог ещё не выполниться, и сигнал бы потерялся.
        QTimer.singleShot(0, self._refresh_schedule_table)

    def _wire(self) -> None:
        w = self.window
        m = self.mesh
        s = self.scheduler

        w.connect_requested.connect(m.connect_radio)
        w.disconnect_requested.connect(m.disconnect_radio)
        w.ports_requested.connect(m.list_ports)
        w.start_requested.connect(m.start_send)
        w.pause_requested.connect(m.pause_send)
        w.stop_requested.connect(m.stop_send)
        w.resume_requested.connect(m.resume_from_state)
        w.resume_current_requested.connect(m.resume_send)
        w.any_ack_changed.connect(m.set_any_ack)

        w.schedule_add.connect(s.add_task)
        w.schedule_delete.connect(s.delete_task)
        w.schedule_edit.connect(s.update_task)
        w.schedule_run_now.connect(self.on_run_task_now)

        self.schedule_run_requested.connect(s.run_now)
        self.schedule_task_done.connect(s.delete_task)
        self.schedule_postpone_requested.connect(s.postpone_task)

        m.connected.connect(w.set_connected)
        m.connected.connect(self._on_connected_after_auto)
        m.port_list.connect(self._on_port_list)
        m.channels_info.connect(w.set_channels)
        m.device_appeared.connect(self._on_device_appeared)
        m.log.connect(w.log)
        m.progress.connect(w.update_progress)
        m.paused.connect(w.set_paused)
        m.stopped.connect(self._on_stopped)
        m.failed.connect(self._on_failed)
        m.finished.connect(self._on_finished)
        m.resume_available.connect(self.on_resume)
        m.chat_message.connect(w.append_chat_message)

        s.log.connect(w.log)
        s.changed.connect(self._on_scheduler_changed)
        s.task_due.connect(self.on_scheduled_task)

    # ------------------------------------------------------- auto connect

    def _on_port_list(self, ports: object) -> None:
        ports = list(ports)
        self.window.set_ports(ports)
        if self._auto_connect_attempted:
            return
        self._auto_connect_attempted = True
        if ports:
            QTimer.singleShot(AUTO_CONNECT_DELAY_MS, self._auto_connect)
        else:
            self.window.log(
                "COM-порты не найдены — жду появления устройства", "info"
            )

    def _auto_connect(self) -> None:
        self.window.log("Автоподключение к базовой ноде...", "info")
        self.window.connect_requested.emit("")

    def _on_connected_after_auto(self, connected: bool, text: str) -> None:
        self._connected = bool(connected)
        self._try_show_resume()

    # ------------------------------------------------ device appears

    def _on_device_appeared(self, port: str) -> None:
        self.window.log(
            f"Новое устройство {port} — попытка автоподключения", "info"
        )
        self.window.ports_requested.emit()
        self.window.connect_requested.emit(port)

    # ------------------------------------------------------- resume

    def on_resume(self, state: object) -> None:
        self._pending_resume = dict(state)
        self._try_show_resume()

    def _try_show_resume(self) -> None:
        if self._pending_resume is None:
            return
        if not self._auto_connect_attempted:
            return
        if not self._connected:
            return
        state = self._pending_resume
        self._pending_resume = None
        QTimer.singleShot(0, lambda: self._ask_resume(state))

    def _ask_resume(self, state: dict) -> None:
        if self.window.show_resume(state):
            self.window.set_sending(scheduled=False)
            self.window.resume_requested.emit(json.dumps(state, ensure_ascii=False))
        else:
            self.window.log("Прерванная рассылка оставлена без продолжения.", "warning")

    # ------------------------------------------------------- scheduler

    def _on_scheduler_changed(self) -> None:
        QTimer.singleShot(0, self._refresh_schedule_table)

    def _refresh_schedule_table(self) -> None:
        self.window.set_schedule(self.scheduler.snapshot())

    def on_scheduled_task(self, task: object) -> None:
        busy = self.mesh.is_busy()
        if busy or not self._connected:
            reason = "идёт рассылка" if busy else "нода не подключена"
            self.window.log(
                f"Планировщик: задача {task.task_id} отложена на "
                f"{SCHEDULE_POSTPONE_S} с ({reason})",
                "warning",
            )
            self.schedule_postpone_requested.emit(
                task.task_id, SCHEDULE_POSTPONE_S
            )
            return

        recipients = task.recipients
        if recipients == ["*"]:
            recipients = MeshtasticWorker.load_badges(BADGES)
        if not recipients:
            self.window.log(
                f"Планировщик: у задачи {task.task_id} нет получателей — "
                "пропуск",
                "error",
            )
            return

        self.mesh.set_source_task_id(task.task_id)
        self.window.log(f"Планировщик: задача {task.task_id}", "info")
        self.window.set_sending(scheduled=True)
        self.window.start_requested.emit(
            task.message,
            json.dumps(recipients, ensure_ascii=False),
            self.window.current_dm_channel(),
        )

    @pyqtSlot(str)
    def on_run_task_now(self, task_id: str) -> None:
        if not any(t.task_id == task_id for t in self.scheduler.snapshot()):
            self.window.log(f"Задача {task_id} не найдена", "error")
            return
        if self.mesh.is_busy():
            self.window.log(
                "Сначала завершите или остановите текущую рассылку", "error"
            )
            return
        if not self._connected:
            self.window.log("Сначала подключите базовую ноду", "error")
            return
        self.window.log(f"Немедленный запуск задачи {task_id}", "info")
        self.schedule_run_requested.emit(task_id)

    # ------------------------------------------------------- finish paths

    def _on_finished(self, result: dict) -> None:
        self.window.clear_sending()

        sent = int(result.get("sent", 0))
        total = int(result.get("total", 0))
        not_delivered = int(result.get("not_delivered", 0))
        undelivered = list(result.get("undelivered", []) or [])
        any_ack = self.window.any_ack()

        if any_ack:
            # В «мягком» режиме мы принимаем ROUTING-ответ от любой ноды,
            # поэтому sent — верхняя граница «сколько пакетов получило
            # хоть какой-то подтверждение в меше», а не число реальных
            # доставок адресатам. Точное число доставок в этом режиме
            # посчитать невозможно в принципе.
            level = "warning" if not_delivered else "success"
            self.window.log(
                f"Готово (режим «любая нода»): отправлено {total} пакетов, "
                f"по {sent} пришёл ответ из меша, "
                f"без ответа {not_delivered}. "
                f"Точное число доставок адресатам в этом режиме неизвестно.",
                level,
            )
        else:
            level = "success" if not_delivered == 0 else "warning"
            self.window.log(
                f"Готово: доставлено {sent} из {total}, "
                f"без подтверждения {not_delivered}",
                level,
            )

        if undelivered:
            preview = ", ".join(undelivered[:UNDELIVERED_PREVIEW])
            more = (
                f" и ещё {len(undelivered) - UNDELIVERED_PREVIEW}"
                if len(undelivered) > UNDELIVERED_PREVIEW else ""
            )
            self.window.log(f"Без ответа: {preview}{more}", "info")

        task_id = str(result.get("source_task_id", "") or "")
        if task_id:
            self.schedule_task_done.emit(task_id)
            self.window.log(f"Задача {task_id} выполнена", "success")

    def _on_failed(self, text: str) -> None:
        self.window.log(text, "error")
        self.window.clear_sending()

    def _on_stopped(self) -> None:
        self.window.clear_sending()

    def close(self) -> None:
        self.scheduler.stop()
        self.mesh.shutdown()
        self.scheduler_thread.quit()
        self.mesh_thread.quit()
        self.scheduler_thread.wait(3000)
        self.mesh_thread.wait(3000)
        try:
            self.mesh.disconnect_radio()
        except Exception:
            pass


def main() -> int:
    _set_windows_app_id("MeshCaster")
    app = QApplication(sys.argv)
    app.setApplicationName("MeshCaster")
    app.setOrganizationName("Rotten Mechanism")

    args = _parse_args(sys.argv[1:])

    window = MainWindow(BADGES)
    controller = AppController(window)

    def shutdown() -> None:
        controller.close()

    app.aboutToQuit.connect(shutdown)

    if args.fullscreen:
        window.showFullScreen()
    elif args.maximized:
        window.showMaximized()
    else:
        window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())