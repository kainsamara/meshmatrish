from __future__ import annotations
import json
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from storage import JsonStore


@dataclass
class ScheduleTask:
    task_id: str
    message: str
    recipients: list[str]
    run_at: str
    enabled: bool = True
    last_run: str = ""

    @staticmethod
    def from_dict(data: dict) -> "ScheduleTask":
        return ScheduleTask(
            task_id=str(data.get("task_id") or uuid.uuid4().hex[:10]),
            message=str(data.get("message", "")),
            recipients=[str(x) for x in data.get("recipients", ["*"])],
            run_at=str(data.get("run_at", "")),
            enabled=bool(data.get("enabled", True)),
            last_run=str(data.get("last_run", "")),
        )


class SchedulerWorker(QObject):
    task_due = pyqtSignal(object)
    changed = pyqtSignal()
    log = pyqtSignal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.store = JsonStore(path)
        self._stop = threading.Event()
        # snapshot() зовётся напрямую из GUI-потока, мутации — в потоке
        # планировщика; все обращения к tasks — под этим локом.
        self._tasks_lock = threading.RLock()
        self._timer: QTimer | None = None
        self.tasks: list[ScheduleTask] = []
        self._load()

    def _load(self) -> None:
        raw = self.store.load([])
        with self._tasks_lock:
            self.tasks = [
                ScheduleTask.from_dict(x) for x in raw if isinstance(x, dict)
            ]

    def _save(self) -> None:
        with self._tasks_lock:
            payload = [asdict(x) for x in self.tasks]
        if not self.store.save(payload):
            self.log.emit("Не удалось сохранить schedule_queue.json")
        self.changed.emit()

    def _find(self, task_id: str) -> ScheduleTask | None:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    @pyqtSlot()
    def run(self) -> None:
        self.changed.emit()
        self.log.emit("Планировщик запущен")
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    @pyqtSlot()
    def _tick(self) -> None:
        if self._stop.is_set():
            if self._timer is not None:
                self._timer.stop()
            return

        now = datetime.now()
        due_tasks: list[ScheduleTask] = []
        dirty = False
        with self._tasks_lock:
            for task in list(self.tasks):
                if not task.enabled:
                    continue
                if not task.message.strip():
                    task.enabled = False
                    dirty = True
                    self.log.emit(
                        f"Задача {task.task_id}: пустое сообщение — отключена"
                    )
                    continue
                try:
                    due = datetime.fromisoformat(task.run_at)
                except ValueError:
                    task.enabled = False
                    dirty = True
                    self.log.emit(
                        f"Задача {task.task_id}: некорректная дата "
                        f"{task.run_at!r} — отключена"
                    )
                    continue
                if now >= due:
                    task.last_run = now.isoformat(timespec="seconds")
                    task.enabled = False
                    dirty = True
                    due_tasks.append(task)
        if dirty:
            self._save()
        for task in due_tasks:
            self.task_due.emit(task)

    @pyqtSlot()
    def stop(self) -> None:
        self._stop.set()

    @pyqtSlot(str, str, str, bool)
    def add_task(
        self,
        message: str,
        recipients_json: str,
        run_at: str,
        enabled: bool,
    ) -> None:
        try:
            recipients = json.loads(recipients_json)
            if not isinstance(recipients, list):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self.log.emit("Ошибка: некорректный список получателей")
            return
        task = ScheduleTask(
            task_id=uuid.uuid4().hex[:10],
            message=message,
            recipients=[str(x) for x in recipients],
            run_at=run_at,
            enabled=enabled,
        )
        with self._tasks_lock:
            self.tasks.append(task)
        self._save()
        self.log.emit(f"Задача {task.task_id} добавлена")

    @pyqtSlot(str)
    def delete_task(self, task_id: str) -> None:
        with self._tasks_lock:
            before = len(self.tasks)
            self.tasks = [x for x in self.tasks if x.task_id != task_id]
            removed = len(self.tasks) != before
        if not removed:
            self.log.emit(f"Задача {task_id} не найдена")
            return
        self._save()
        self.log.emit(f"Задача {task_id} удалена")

    @pyqtSlot(str)
    def run_now(self, task_id: str) -> None:
        with self._tasks_lock:
            task = self._find(task_id)
            if task is not None:
                task.enabled = False
        if task is None:
            self.log.emit(f"Задача {task_id} не найдена")
            return
        self._save()
        self.task_due.emit(task)

    @pyqtSlot(str, int)
    def postpone_task(self, task_id: str, seconds: int) -> None:
        """Перевзводит задачу на now+seconds (например, когда в момент
        срабатывания шла другая рассылка) вместо тихой потери."""
        new_run = (
            datetime.now() + timedelta(seconds=int(seconds))
        ).isoformat(timespec="seconds")
        with self._tasks_lock:
            task = self._find(task_id)
            if task is not None:
                task.run_at = new_run
                task.enabled = True
        if task is None:
            self.log.emit(f"Задача {task_id} не найдена")
            return
        self._save()
        self.log.emit(f"Задача {task_id} отложена до {new_run}")

    @pyqtSlot(str, str, str, str, bool)
    def update_task(
        self,
        task_id: str,
        message: str,
        recipients_json: str,
        run_at: str,
        enabled: bool,
    ) -> None:
        try:
            recipients = json.loads(recipients_json)
            if not isinstance(recipients, list):
                raise ValueError
        except (ValueError, TypeError, json.JSONDecodeError):
            self.log.emit("Ошибка: некорректный список получателей")
            return
        with self._tasks_lock:
            task = self._find(task_id)
            if task is not None:
                task.message = message
                task.recipients = [str(x) for x in recipients]
                task.run_at = run_at
                task.enabled = enabled
        if task is None:
            self.log.emit(f"Задача {task_id} не найдена")
            return
        self._save()
        self.log.emit(f"Задача {task_id} обновлена")

    def snapshot(self) -> list[ScheduleTask]:
        with self._tasks_lock:
            return [ScheduleTask(**asdict(t)) for t in self.tasks]
