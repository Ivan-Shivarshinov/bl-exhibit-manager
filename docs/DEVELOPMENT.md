# Разработка BL Exhibit Manager

[Главная страница](../README.md) · [Инструкция пользователя](PILOT.md)

## Запуск из исходников

Нужны Python 3.12 и Node.js 22.12+; Node используется только при сборке интерфейса.

Windows, PowerShell в папке проекта:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
npm.cmd ci --prefix web
npm.cmd run build --prefix web
.\Start.cmd
```

macOS, Terminal в папке проекта:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
npm ci --prefix web
npm run build --prefix web
sh Start.command
```

Откроется `http://127.0.0.1:8765`. Повторный запуск использует работающий сервер. Закрытие вкладки не завершает сервер; остановка — `Stop.cmd` / `Stop.command`. Для диагностического запуска с остановкой через Ctrl+C:

```powershell
.\.venv\Scripts\python.exe -m uvicorn exhibit.app:app --host 127.0.0.1 --port 8765
```

На macOS используйте `.venv/bin/python`. Не публикуйте локальный сервер в интернете.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
npm.cmd run build --prefix web
```

На macOS замените пути Python и `npm.cmd` на `.venv/bin/python` и `npm`. Тесты используют временные папки и явные имитации моделей, не расходуют подписки. Word/LibreOffice, реальный вход и пользовательские переходы проверяются отдельно.

`python scripts/verify_feedback_office.py` проверяет настоящий установленный Word/LibreOffice на вымышленных сносках: полные названия, несколько документов в сноске, переносы строк, чёрный цвет ссылок, сохранение обычной ссылки на сайт и пути к PDF. Скрипт сохраняет готовый комплект и снимки страниц в `output/feedback-review/word`. В CI эта проверка запускается с LibreOffice на обоих вариантах macOS.

Структура: `exhibit/` — Python/FastAPI и документы; `web/` — React/Vite; `tests/` — тесты; `scripts/` — запуск и дополнительные проверки. Учебные материалы создаёт `python -m exhibit.samples`; результаты, документы, окружения и локальные настройки исключены из Git.

### Сборка дистрибутива

На целевой ОС установите `requirements-build.txt` в отдельное Python-окружение, соберите `web/dist`, затем выполните `python scripts/build_bundle.py`. Результат — `release/BLExhibitManager-<версия>-<платформа>.zip` и SHA-256. `python scripts/bundle_smoke.py <путь-к-ZIP>` проверяет распакованную сборку с отдельной папкой данных и без Python/Node в PATH, включая повторный запуск, PDF, экспорт, остановку и перенос приложения без потери проекта.

GitHub Actions выполняет тесты, нативную сборку и эти проверки на Windows, macOS Intel и macOS ARM. Это не заменяет ручную проверку скачанного пакета через защиту ОС, установленных office-программ и подписок на компьютерах участников, а также рабочих материалов. Пакеты включают Python и уведомления зависимостей в `THIRD-PARTY-NOTICES`; сервисы перевода и office-программы не включены.
