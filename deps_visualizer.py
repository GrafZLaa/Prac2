#!/usr/bin/env python3
import argparse
import sys
import os
import urllib.request
import tarfile
import gzip
import io
from urllib.parse import urljoin, urlparse

def validate_package_name(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("Имя пакета не может быть пустым.")
    name = name.strip()
    if not name.replace('-', '_').replace('.', '_').replace('+', '_').isidentifier():
        # Alpine допускает '+' в именах (например, libstdc++)
        # Простая проверка: не должно быть пробелов или слешей
        if ' ' in name or '/' in name or '\\' in name:
            raise ValueError("Имя пакета не должно содержать пробелов или путей.")
    return name

def validate_repo_url_or_path(repo: str) -> str:
    if not repo:
        raise ValueError("URL репозитория или путь к файлу не может быть пустым.")
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

def fetch_apkindex_content(repo_url: str) -> str:
    """Загружает и распаковывает APKINDEX.tar.gz, возвращает содержимое APKINDEX."""
    try:
        if repo_url.startswith(('http://', 'https://')):
            with urllib.request.urlopen(repo_url) as response:
                compressed_data = response.read()
        elif repo_url.startswith('file://'):
            local_path = repo_url[7:]
            with open(local_path, 'rb') as f:
                compressed_data = f.read()
        elif os.path.isfile(repo_url):
            with open(repo_url, 'rb') as f:
                compressed_data = f.read()
        else:
            # Предполагаем, что это базовый URL репозитория → добавляем APKINDEX.tar.gz
            repo_url = repo_url.rstrip('/') + '/APKINDEX.tar.gz'
            with urllib.request.urlopen(repo_url) as response:
                compressed_data = response.read()

        # Распаковка gzip
        decompressed = gzip.decompress(compressed_data)

        # Чтение tar-архива в памяти
        tar_stream = io.BytesIO(decompressed)
        with tarfile.open(fileobj=tar_stream, mode='r') as tar:
            # Ищем файл APKINDEX (без расширения)
            for member in tar.getmembers():
                if member.name == 'APKINDEX':
                    f = tar.extractfile(member)
                    if f:
                        return f.read().decode('utf-8')
            raise FileNotFoundError("Файл APKINDEX не найден внутри APKINDEX.tar.gz")
    except Exception as e:
        raise RuntimeError(f"Не удалось загрузить или распаковать APKINDEX: {e}")

def parse_dependencies_from_apkindex(apkindex_content: str, package_name: str) -> list:
    """Парсит APKINDEX и возвращает список прямых зависимостей для package_name."""
    lines = apkindex_content.splitlines()
    current_pkg = None
    dependencies = []
    in_target = False

    for line in lines:
        if line.startswith('P:'):
            current_pkg = line[2:].strip()
            in_target = (current_pkg == package_name)
        elif in_target and line.startswith('D:'):
            deps_str = line[2:].strip()
            if deps_str:
                # В APK зависимости разделены пробелами
                dependencies = deps_str.split()
            else:
                dependencies = []
            break  # Нашли — выходим
        elif line == '':  # Пустая строка = конец записи
            in_target = False

    return dependencies

def main():
    parser = argparse.ArgumentParser(
        description="Инструмент визуализации графа зависимостей пакетов (этап 1+2).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--package', required=True, help='Имя анализируемого пакета.')
    parser.add_argument('--repo', required=True, help='URL репозитория или путь к APKINDEX.tar.gz.')
    parser.add_argument('--mode', required=True, choices=['online', 'offline', 'test'], help='Режим работы.')
    parser.add_argument('--output', required=True, help='Имя файла изображения графа.')
    parser.add_argument('--ascii-tree', required=True, help='Выводить ASCII-дерево? true/false.')

    try:
        args = parser.parse_args()

        # === Этап 1: валидация и вывод параметров ===
        package = validate_package_name(args.package)
        repo = validate_repo_url_or_path(args.repo)
        mode = validate_mode(args.mode)
        output = validate_output_file(args.output)
        ascii_tree = validate_ascii_tree(args.ascii_tree)

        print("Параметры запуска:")
        print(f"package = {package}")
        print(f"repo = {repo}")
        print(f"mode = {mode}")
        print(f"output = {output}")
        print(f"ascii_tree = {ascii_tree}")
        print()  # пустая строка

        # === Этап 2: получение зависимостей (только если режим позволяет) ===
        # Для этапа 2 будем использовать repo как URL к APKINDEX.tar.gz
        # Поддерживаем: прямой URL к APKINDEX.tar.gz или базовый URL репозитория
        apkindex_url = repo

        print(f"Загрузка APKINDEX из: {apkindex_url}")
        apkindex_content = fetch_apkindex_content(apkindex_url)

        print(f"Поиск пакета: {package}")
        deps = parse_dependencies_from_apkindex(apkindex_content, package)

        if deps:
            print(f"Прямые зависимости пакета '{package}':")
            for dep in deps:
                print(f"  - {dep}")
        else:
            print(f"Пакет '{package}' не имеет прямых зависимостей или не найден в индексе.")

    except (ValueError, OSError, RuntimeError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        sys.exit(130)

if __name__ == "__main__":
    main()