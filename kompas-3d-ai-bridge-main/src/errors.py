"""Классификация ошибок моста: устойчивые коды, подсказки, редакция данных.

Зачем этот модуль существует
---------------------------
Воркер возвращал агенту ``traceback.format_exc()`` целиком (``src/worker.py``).
Это плохо по двум причинам:

1. наружу утекала структура файловой системы и имена пользователей;
2. вместо ответа на вопрос «что не так и что делать» модель получала 40 строк
   внутренностей, которые ещё и занимали контекст.

Теперь наружу уходит только устойчивый код, короткое сообщение и подсказка,
а полная трассировка пишется в ``runtime/logs/errors.log``.
"""
from __future__ import annotations

import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

MAX_MESSAGE_CHARS = 400
MAX_HINT_CHARS = 300

# Код -> подсказка «что делать». Подсказка - часть контракта: агент читает её
# и меняет поведение, поэтому формулировки должны быть императивными.
CODE_HINTS: Mapping[str, str] = {
    "kompas_not_attached": (
        "Откройте ровно один экземпляр КОМПАС-3D в той же пользовательской сессии "
        "и с тем же уровнем прав, что и мост, убедитесь, что модель активна, "
        "закройте модальные окна и повторите вызов."
    ),
    "path_protected": (
        "Это защищённый корень исходников. Работайте на копии *_AGENT_COPY "
        "внутри каталога work или в разрешённом каталоге вывода."
    ),
    "path_not_allowed": (
        "Путь вне разрешённых корней записи. Создайте документ в work "
        "или добавьте нужный корень в config/agent_config.json -> safety.write_roots."
    ),
    "job_not_allowed": (
        "Задание не прошло проверку допуска. Задание должно лежать в каталоге jobs, "
        "иметь расширение .py и быть перечисленным в jobs/allowlist.json."
    ),
    "active_document_mismatch": (
        "Активный документ не тот, который ожидался. Перечитайте kompas_active_document "
        "и kompas_status, затем активируйте нужный документ перед повторной операцией."
    ),
    "verification_failed": (
        "Операция не прошла проверку после сохранения и повторного открытия. "
        "Модель не изменена - откат выполнен. Перечитайте состояние через "
        "kompas_status и kompas_model_tree перед повтором."
    ),
    "timeout": (
        "Воркер не ответил в отведённое время. Проверьте, не висит ли модальное окно "
        "в КОМПАСе, не выполняется ли длинное перестроение, и не запущен ли уже "
        "другой длительный запрос к тому же мосту."
    ),
    "file_not_found": (
        "Файл не найден. Проверьте имя и расположение, а также что документ "
        "действительно сохранён на диск."
    ),
    "permission_denied": (
        "Недостаточно прав на файловую операцию. Убедитесь, что файл не открыт "
        "в КОМПАСе монопольно и не заблокирован антивирусом."
    ),
    "invalid_argument": (
        "Аргумент не прошёл валидацию. Сверьтесь со схемой инструмента "
        "и передайте значения в указанных единицах."
    ),
    "internal_error": (
        "Внутренняя ошибка моста. Полная трассировка записана в runtime/logs/errors.log - "
        "передайте её разработчику."
    ),
}

# Порядок важен: сначала самые специфичные признаки.
_CLASSIFY_RULES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("kompas_not_attached", (
        "kompas_active_instance_not_found",
        "kompas_com_attach_failed",
        "kompas_api5_active_instance_not_found",
        "second kompas instance",
    )),
    ("job_not_allowed", (
        "job_must_be_existing_python_file",
        "job_not_in_allowlist",
        "job_references_protected_path",
        "job_failed",
        "job_timeout",
    )),
    ("path_protected", ("protected_path",)),
    ("path_not_allowed", ("write_outside_allowed_roots", "outside_allowed_roots")),
    ("active_document_mismatch", (
        "reopen_postcondition_failed",
        "active_path_mismatch",
        "active_document_mismatch",
        "copy_not_active",
        "no_active_document",
        "reopen_target",
    )),
    ("verification_failed", (
        "verification_failed",
        "postcondition",
        "readback",
        "geometry_verification",
        "did not match",
    )),
    ("timeout", ("bridge_timeout", "timed out", "timeout")),
    ("file_not_found", ("not found", "not_found", "no such file")),
    ("permission_denied", ("permission", "access is denied", "winerror 5")),
    ("invalid_argument", (
        "invalid",
        "must be",
        "required",
        "out of range",
        "exceeds",
        "unsupported",
    )),
)


class BridgeError(Exception):
    """Ошибка с устойчивым кодом, пригодным для чтения агентом."""

    def __init__(self, code: str, message: str, *, hint: Optional[str] = None,
                 details: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint or CODE_HINTS.get(code, CODE_HINTS["internal_error"])
        self.details: Dict[str, Any] = dict(details or {})

    def to_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "code": self.code,
            "message": _clip(self.message, MAX_MESSAGE_CHARS),
            "hint": _clip(self.hint, MAX_HINT_CHARS),
        }
        if self.details:
            body["details"] = self.details
        return body


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def classify(exc: BaseException) -> Tuple[str, str, str]:
    """Определить (код, сообщение, подсказка) по исключению."""
    if isinstance(exc, BridgeError):
        return exc.code, exc.message, exc.hint

    raw = f"{type(exc).__name__}: {exc}"
    lowered = raw.lower()

    code = "internal_error"
    if isinstance(exc, PermissionError):
        code = "permission_denied"
    elif isinstance(exc, FileNotFoundError):
        code = "file_not_found"
    elif isinstance(exc, (TimeoutError,)):
        code = "timeout"
    elif isinstance(exc, (ValueError, TypeError, KeyError, IndexError)):
        code = "invalid_argument"

    for candidate, needles in _CLASSIFY_RULES:
        if any(needle in lowered for needle in needles):
            code = candidate
            break

    return code, raw, CODE_HINTS.get(code, CODE_HINTS["internal_error"])


def _user_profile_token() -> Optional[Path]:
    for env in ("USERPROFILE", "HOME"):
        value = os.environ.get(env)
        if value:
            try:
                return Path(value).resolve()
            except OSError:
                continue
    return None


_WIN_ABS_PATH = re.compile(r"[A-Za-z]:[\\/][^\s\"'<>|,;)\]]*")


def redact(text: Any, bridge_root: Any = None, *, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    """Убрать из текста абсолютные пути, имена пользователей и лишнюю длину.

    Известные корни заменяются на говорящие токены, остальные абсолютные пути -
    на ``<path:basename>``: имя файла сохраняется, потому что без него сообщение
    становится бесполезным, а полный путь - это уже утечка.
    """
    value = str(text or "")

    known: list[Tuple[Path, str]] = []
    if bridge_root is not None:
        try:
            known.append((Path(str(bridge_root)).resolve(), "<bridge_root>"))
        except OSError:
            pass
    profile = _user_profile_token()
    if profile is not None:
        known.append((profile, "<user_profile>"))
    known.sort(key=lambda item: len(str(item[0])), reverse=True)

    for path, token in known:
        for form in {str(path), str(path).replace("\\", "/")}:
            value = re.sub(re.escape(form), token, value, flags=re.IGNORECASE)

    def _replace(match: "re.Match[str]") -> str:
        found = match.group(0)
        name = re.split(r"[\\/]", found.rstrip("\\/"))[-1]
        return f"<path:{name}>" if name else "<path>"

    value = _WIN_ABS_PATH.sub(_replace, value)
    return _clip(value, max_chars)


def error_body(action: str, exc: BaseException, bridge_root: Any = None) -> Dict[str, Any]:
    """Тело ответа воркера при ошибке. Трассировки здесь нет и не должно быть."""
    code, message, hint = classify(exc)
    return {
        "code": code,
        "message": redact(message, bridge_root),
        "hint": redact(hint, bridge_root, max_chars=MAX_HINT_CHARS),
        "action": action,
    }


def log_traceback(bridge_root: Any, action: str, exc: BaseException) -> Optional[Path]:
    """Записать полную трассировку в runtime/logs/errors.log. Не бросает исключений."""
    try:
        log_dir = Path(str(bridge_root)) / "runtime" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "errors.log"
        stamp = datetime.now(timezone.utc).astimezone().isoformat()
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"\n===== {stamp} | action={action} =====\n")
            handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        return path
    except Exception:
        return None
