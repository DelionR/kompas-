"""Поиск по локальным документам проекта с цитатами и ссылкой на источник.

`SwitchScope` показал ценную дисциплину: ответ помощника обязан содержать
проверяемую ссылку на документ, иначе проверить его нечем и верить ему нельзя.
Здесь то же самое, но без векторной базы и без интернета: обычный полнотекстовый
поиск по файлам проекта, где каждый результат — файл, номер строки и сам
фрагмент.

Что модуль **не** делает:

- не ищет в интернете и не «вспоминает» нормы: найденное всегда взято из файла
  на диске, и это видно по полю ``file``;
- не делает семантический поиск: совпадение по подстроке, поэтому неточная
  формулировка запроса даст пустой результат, а не правдоподобный ответ;
- не читает ничего вне разрешённого корня: путь наружу отклоняется.

Ограничение по объёму задано намеренно: сканирование останавливается на
``max_files`` файлах, и об этом сообщается, чтобы тишина не выглядела как
«в проекте такого нет».
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

DEFAULT_EXTENSIONS = (".md", ".txt", ".rst")
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__",
             ".venv", "venv", ".mypy_cache", ".pytest_cache", "build", "dist"}
MAX_FILES = 4000
DEFAULT_MAX_RESULTS = 20

LIMITS = [
    "Поиск полнотекстовый, без семантики: совпадение по подстроке. Неточная "
    "формулировка даст пустой результат, а не правдоподобный ответ.",
    "Чтение допускается только внутри корня проекта и текущего каталога.",
    "Сканирование останавливается на max_files; если предел достигнут, это "
    "указано в truncated — пустой результат тогда не означает отсутствия.",
    "Файлы читаются как UTF-8; бинарные и нечитаемые пропускаются молча.",
    "Найденный фрагмент — цитата из файла, а не проверенная выдержка из нормы: "
    "сверка с действующей редакцией остаётся за инженером.",
]


def _as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _refusal(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    item: Dict[str, Any] = {"code": code, "message": message}
    item.update(extra)
    return item


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def allowed_roots() -> List[str]:
    roots = [project_root()]
    try:
        roots.append(os.path.realpath(os.getcwd()))
    except OSError:
        pass
    return [os.path.realpath(root) for root in roots]


def resolve_root(path: str) -> tuple:
    """Корень поиска, если он внутри разрешённой области."""
    candidate = os.path.realpath(os.path.abspath(path))
    for root in allowed_roots():
        if candidate == root or candidate.startswith(root + os.sep):
            return candidate, root, None
    return None, None, _refusal("path_outside_root",
                                "Каталог поиска вне корня проекта и вне текущего каталога.",
                                allowed_roots=allowed_roots())


def search_terms(query: Any) -> List[str]:
    """Слова запроса. Искомое считается совпадением, только если есть все."""
    if isinstance(query, (list, tuple)):
        raw = [str(item) for item in query]
    else:
        raw = [str(query)]
    terms: List[str] = []
    for chunk in raw:
        for word in chunk.split():
            word = word.strip()
            if word:
                terms.append(word)
    return terms


def iter_files(root: str, extensions: tuple, max_files: int):
    """Файлы с нужными расширениями. Возвращает (путь, истина_предела)."""
    collected: List[str] = []
    for directory, subdirs, files in os.walk(root):
        subdirs[:] = [name for name in subdirs if name not in SKIP_DIRS]
        for name in sorted(files):
            if extensions and not name.lower().endswith(extensions):
                continue
            collected.append(os.path.join(directory, name))
            if len(collected) >= max_files:
                return collected, True
    return collected, False


def search(payload: Any) -> Dict[str, Any]:
    """Поиск по файлам: цитата, файл, номер строки."""
    data = _as_dict(payload)
    terms = search_terms(data.get("query"))
    base: Dict[str, Any] = {
        "query": _text(data.get("query")) or terms,
        "terms": terms,
        "matches": [],
        "files_scanned": 0,
        "truncated": False,
        "notes": [],
        "limits": list(LIMITS),
    }
    if not terms:
        return dict(base, ok=False, verdict="refused",
                    refusals=[_refusal("empty_query",
                                       "Пустой запрос: нужны слова для поиска.")])

    root = _text(data.get("root")) or project_root()
    resolved, display_root, refusal = resolve_root(root)
    if refusal:
        return dict(base, ok=False, verdict="refused", refusals=[refusal])
    if not os.path.isdir(resolved):
        return dict(base, ok=False, verdict="refused",
                    refusals=[_refusal("no_root", "Каталог не найден: %s" % root,
                                       root=root)])

    extensions = tuple(data.get("extensions") or DEFAULT_EXTENSIONS)
    max_results = int(_num(data.get("max_results"), float(DEFAULT_MAX_RESULTS))
                      or DEFAULT_MAX_RESULTS)
    context = int(_num(data.get("context_lines"), 0.0) or 0.0)
    max_files = int(_num(data.get("max_files"), float(MAX_FILES)) or MAX_FILES)
    case_sensitive = bool(data.get("case_sensitive"))

    files, truncated = iter_files(resolved, extensions, max_files)
    lowered = [term.lower() for term in terms] if not case_sensitive else terms

    matches: List[Dict[str, Any]] = []
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="strict") as handle:
                lines = handle.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(lines):
            haystack = line if case_sensitive else line.lower()
            if not all(term in haystack for term in lowered):
                continue
            start = max(0, index - context)
            end = min(len(lines), index + context + 1)
            matches.append({
                "file": os.path.relpath(path, display_root),
                "line": index + 1,
                "text": line.strip(),
                "context": lines[start:end] if context else None,
            })
            if max_results > 0 and len(matches) >= max_results:
                break
        if max_results > 0 and len(matches) >= max_results:
            break

    notes: List[str] = []
    if truncated:
        notes.append("сканирование остановлено на %d файлах: просмотрено не всё, "
                     "пустой результат не означает отсутствия" % max_files)
    if max_results > 0 and len(matches) >= max_results:
        notes.append("показан максимум результатов (%d): уточните запрос" % max_results)

    return dict(base, ok=True, verdict="computed", root=display_root,
                matches=matches, files_scanned=len(files), truncated=truncated,
                notes=notes, refusals=[])
