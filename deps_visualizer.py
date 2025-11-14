#!/usr/bin/env python3
import argparse
import sys
import os
import urllib.request
import tarfile
import gzip
import io
import json
from urllib.parse import urljoin, urlparse
from collections import deque


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


def parse_apkindex_to_dict(apkindex_content: str) -> dict:
    """Парсит APKINDEX и возвращает словарь {пакет: [зависимости]}."""
    packages = {}
    current_pkg = None
    current_deps = []

    for line in apkindex_content.splitlines():
        line = line.strip()
        if line.startswith('P:'):
            if current_pkg is not None:
                packages[current_pkg] = current_deps
            current_pkg = line[2:].strip()
            current_deps = []
        elif line.startswith('D:'):
            deps_str = line[2:].strip()
            if deps_str:
                # Убираем версионные зависимости вроде "so:libfoo.so.1" или "pkg>=1.0"
                # Для простоты оставляем только имена пакетов
                raw_deps = deps_str.split()
                clean_deps = []
                for d in raw_deps:
                    # Пропускаем shared object зависимости
                    if d.startswith('so:'):
                        continue
                    # Убираем версионную часть после >=, <=, = и т.д.
                    pkg_name = d.split('>=')[0].split('<=')[0].split('=')[0].split('!')[0].strip()
                    clean_deps.append(pkg_name)
                current_deps = clean_deps
            else:
                current_deps = []
        elif line == '':  # конец записи пакета
            if current_pkg is not None:
                packages[current_pkg] = current_deps
                current_pkg = None
                current_deps = []

    if current_pkg is not None:
        packages[current_pkg] = current_deps

    return packages


def load_test_repo(file_path: str) -> dict:
    """Загружает тестовый репозиторий из файла."""
    repo = {}
    try:
        with open(file_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if ':' not in line:
                    continue
                pkg, deps_part = line.split(':', 1)
                pkg = pkg.strip()
                deps_part = deps_part.strip()
                if deps_part:
                    dependencies = deps_part.split()
                else:
                    dependencies = []
                repo[pkg] = dependencies
        return repo
    except Exception as e:
        raise RuntimeError(f"Не удалось загрузить тестовый репозиторий из {file_path}: {e}")


def build_dependency_graph_dfs(start_package: str, get_deps_func) -> tuple:
    """
    Строит граф зависимостей с помощью DFS без рекурсии.
    Возвращает (граф, есть_цикла)
    """
    graph = {}
    visited = set()  # полностью обработанные узлы
    stack = [(start_package, 0)]  # (узел, состояние: 0 - начать обработку, 1 - завершить)
    in_stack = set()  # узлы в текущем пути обработки (для обнаружения циклов)
    has_cycle = False

    while stack:
        node, state = stack.pop()

        if state == 0:
            if node in visited:
                continue

            if node in in_stack:
                # print(f"Обнаружена циклическая зависимость: {node} уже в текущем пути")
                has_cycle = True
                continue

            in_stack.add(node)
            dependencies = get_deps_func(node)
            graph[node] = dependencies

            # Сначала вернемся к этому узлу для завершения обработки
            stack.append((node, 1))

            # Затем обработаем зависимости в обратном порядке (чтобы порядок был как в рекурсивном DFS)
            for dep in reversed(dependencies):
                stack.append((dep, 0))

        else:  # state == 1, завершение обработки
            in_stack.remove(node)
            visited.add(node)

    return graph, has_cycle


def print_graph(graph: dict, start_package: str) -> None:
    """Выводит граф зависимостей в виде дерева."""

    def print_dependencies(pkg, indent="", visited=None):
        if visited is None:
            visited = set()
        if pkg in visited:
            print(f"{indent}└── {pkg} (цикл)")
            return
        visited.add(pkg)

        deps = graph.get(pkg, [])
        print(f"{indent}└── {pkg}")

        if deps:
            for i, dep in enumerate(deps):
                is_last = (i == len(deps) - 1)
                new_indent = indent + ("    " if is_last else "│   ")
                print_dependencies(dep, new_indent, visited.copy())

    print(f"Граф зависимостей для пакета {start_package}:")
    print_dependencies(start_package)


def main():
    parser = argparse.ArgumentParser(
        description="Инструмент визуализации графа зависимостей пакетов (этапы 1-3).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--package', required=True, help='Имя анализируемого пакета.')
    parser.add_argument('--repo', required=True, help='URL репозитория или путь к APKINDEX.tar.gz / тестовому файлу.')
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

        # === Этап 2: получение данных репозитория в зависимости от режима ===
        repo_data = None
        if mode == 'test':
            print(f"Загрузка тестового репозитория из файла: {repo}")
            repo_data = load_test_repo(repo)
            print(f"Тестовый репозиторий загружен. Всего пакетов: {len(repo_data)}")
        else:
            print(f"Загрузка APKINDEX из: {repo}")
            apkindex_content = fetch_apkindex_content(repo)
            print("Парсинг APKINDEX...")
            repo_data = parse_apkindex_to_dict(apkindex_content)
            print(f"Загружено записей о пакетах: {len(repo_data)}")

        # Проверяем наличие начального пакета
        if package not in repo_data:
            print(f"Предупреждение: пакет '{package}' не найден в репозитории.", file=sys.stderr)
            dependencies = []
        else:
            dependencies = repo_data[package]

        if dependencies:
            print(f"\nПрямые зависимости пакета '{package}':")
            for dep in dependencies:
                print(f"  - {dep}")
        else:
            print(f"\nПакет '{package}' не имеет прямых зависимостей или не найден в репозитории.")

        # === Этап 3: построение графа зависимостей ===
        print("\n=== Этап 3: Построение графа зависимостей ===")

        # Функция для получения зависимостей
        def get_deps(pkg_name):
            return repo_data.get(pkg_name, [])

        # Проверяем, существует ли стартовый пакет в репозитории
        if package not in repo_data:
            print(f"Ошибка: стартовый пакет '{package}' отсутствует в репозитории. Невозможно построить граф.",
                  file=sys.stderr)
            sys.exit(1)

        try:
            graph, has_cycle = build_dependency_graph_dfs(package, get_deps)
            print("Граф зависимостей успешно построен.")

            if has_cycle:
                print("ВНИМАНИЕ: В графе обнаружены циклические зависимости.")
            else:
                print("Циклические зависимости не обнаружены.")

            # Вывод графа в формате зависимостей
            print("\nГраф зависимостей (все зависимости):")
            for pkg, deps in graph.items():
                if deps:
                    print(f"{pkg} -> {', '.join(deps)}")
                else:
                    print(f"{pkg} -> (нет зависимостей)")

            # Вывод графа в виде ASCII-дерева, если запрошено
            if ascii_tree:
                print("\nГраф зависимостей в виде ASCII-дерева:")
                print_graph(graph, package)

            # Демонстрация на тестовых данных
            if mode == 'test':
                print("\n=== Демонстрация на тестовых данных ===")
                print("Тестовый репозиторий позволяет легко проверить обработку циклических зависимостей.")
                print("Пример файла тестового репозитория:")
                print("A: B C")
                print("B: D")
                print("C: D E")
                print("D: ")
                print("E: B  # Циклическая зависимость: B -> D -> E -> B")
                print("\nДля проверки работы с циклическими зависимостями запустите:")
                print(
                    f"./script.py --package A --repo ваш_тестовый_файл.txt --mode test --output graph.svg --ascii-tree true")

        except Exception as e:
            print(f"Ошибка при построении графа: {e}", file=sys.stderr)
            sys.exit(1)

    except (ValueError, OSError, RuntimeError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()