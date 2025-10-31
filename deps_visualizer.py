#!/usr/bin/env python3
import argparse

import sys
import os
from urllib.parse import urlparse

def validate_package_name(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("Имя пакета не может быть пустым.")
    if not name.replace('-', '_').replace('.', '_').isidentifier():
        raise ValueError("Имя пакета должно быть корректным идентификатором (без пробелов, специальных символов кроме '-' и '.').")
    return name.strip()

def validate_repo_url_or_path(repo: str) -> str:
    if not repo:
        raise ValueError("URL репозитория или путь к файлу не может быть пустым.")
    # Проверка: либо валидный URL, либо существующий локальный путь
    parsed = urlparse(repo)
    if parsed.scheme in ('http', 'https', 'file'):
        return repo
    elif os.path.exists(repo):
        return os.path.abspath(repo)
    else:
        raise ValueError(f"Указанный репозиторий '{repo}' не является валидным URL или существующим локальным путём.")

def validate_mode(mode: str) -> str:
    allowed = {'online', 'offline', 'test'}
    if mode not in allowed:
        raise ValueError(f"Режим работы должен быть одним из: {', '.join(allowed)}. Получено: '{mode}'.")
    return mode

def validate_output_file(filename: str) -> str:
    if not filename:
        raise ValueError("Имя выходного файла не может быть пустым.")
    if not filename.endswith(('.png', '.svg', '.pdf', '.jpg')):
        raise ValueError("Имя файла изображения должно иметь расширение: .png, .svg, .pdf или .jpg.")
    return filename

def validate_ascii_tree(mode: str) -> bool:
    if mode.lower() in ('true', '1', 'yes', 'on'):
        return True
    elif mode.lower() in ('false', '0', 'no', 'off'):
        return False
    else:
        raise ValueError("Режим ASCII-дерева должен быть булевым: true/false, yes/no, 1/0 и т.п.")

def main():
    parser = argparse.ArgumentParser(
        description="Инструмент визуализации графа зависимостей пакетов (этап 1: минимальный прототип).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        '--package',
        required=True,
        help='Имя анализируемого пакета (обязательный).'
    )
    parser.add_argument(
        '--repo',
        required=True,
        help='URL репозитория или путь к локальному файлу тестового репозитория (обязательный).'
    )
    parser.add_argument(
        '--mode',
        required=True,
        choices=['online', 'offline', 'test'],
        help='Режим работы с репозиторием: online, offline или test (обязательный).'
    )
    parser.add_argument(
        '--output',
        required=True,
        help='Имя генерируемого файла с изображением графа (обязательный, например: graph.png).'
    )
    parser.add_argument(
        '--ascii-tree',
        required=True,
        help='Режим вывода зависимостей в формате ASCII-дерева: true/false.'
    )

    try:
        args = parser.parse_args()

        # Валидация параметров
        package = validate_package_name(args.package)
        repo = validate_repo_url_or_path(args.repo)
        mode = validate_mode(args.mode)
        output = validate_output_file(args.output)
        ascii_tree = validate_ascii_tree(args.ascii_tree)

        # Вывод параметров в формате ключ-значение
        print("Параметры запуска:")
        print(f"package = {package}")
        print(f"repo = {repo}")
        print(f"mode = {mode}")
        print(f"output = {output}")
        print(f"ascii_tree = {ascii_tree}")

    except (ValueError, OSError) as e:
        print(f"Ошибка валидации: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Неожиданная ошибка: {e}", file=sys.stderr)
        sys.exit(2)

if __name__ == "__main__":
    main()