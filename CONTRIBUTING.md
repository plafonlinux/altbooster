# Участие в разработке

Спасибо за интерес к проекту! Ниже — всё что нужно знать перед тем как открывать PR.

## Требования к окружению

- ALT Linux (Workstation или Simply)
- Python 3.11+
- GTK 4.0 и libadwaita 1.x (`apt-get install python3-module-gi typelib-1_0-Adw-1`)

## Запуск из исходников

```bash
git clone https://github.com/plafonlinux/altbooster.git
cd altbooster
python3 src/main.py
```

## Стиль кода

Проект следует стандарту **PEP 8** с максимальной длиной строки 100 символов.

Проверить стиль перед коммитом:
```bash
pip install ruff
ruff check src/
```

Основные правила:
- Никаких однострочников через `;` — каждая инструкция на своей строке
- Type hints для всех публичных функций
- `docstring` для классов и нетривиальных функций
- `bare except` запрещён — всегда указывайте тип: `except OSError`

## Структура проекта

```
src/
  main.py     — точка входа, Adw.Application
  ui.py       — весь GTK4 интерфейс
  backend.py  — системные команды и проверки
  config.py   — состояние, задачи и список приложений
```

## Добавление нового приложения

Откройте `src/config.py` и добавьте запись в `APPS`:

```python
{"id": "my_app", "label": "My App", "desc": "Описание",
 "source": _flatpak("com.example.MyApp")},
```

Для RPM-пакета:
```python
"source": _epm_install("package-name")
```

## Добавление задачи обслуживания

Добавьте словарь в `TASKS` в `src/config.py`:

```python
{
    "id":   "my_task",
    "icon": "имя-иконки-adwaita-symbolic",
    "label": "Название задачи",
    "desc":  "Краткое описание",
    "cmd":   ["команда", "аргумент"],
},
```

## Отправка PR

1. Форкните репозиторий
2. Создайте ветку: `git checkout -b feat/my-feature`
3. Проверьте код: `ruff check src/`
4. Опишите что и зачем изменили в PR

## Сообщить об ошибке

Откройте [Issue](https://github.com/plafonlinux/altbooster/issues) с:
- Версией ALT Linux (`cat /etc/altlinux-release`)
- Текстом ошибки из терминала
- Шагами для воспроизведения
