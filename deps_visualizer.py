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
from collections import deque, defaultdict


def validate_package_name(name: str) -> str:
    if not name or not isinstance(name, str):
        raise ValueError("Имя пакета не может быть пустым.")
    name = name.strip()
    if not name.replace('-', '_').replace('.', '_').replace('+', '_').isidentifier():
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


def validate_reverse_deps(mode: str) -> bool:
    if mode.lower() in ('true', '1', 'yes', 'on'):
        return True
    elif mode.lower() in ('false', '0', 'no', 'off'):
        return False
    else:
        raise ValueError("Режим обратных зависимостей должен быть булевым: true/false, yes/no, 1/0 и т.п.")


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
            repo_url = repo_url.rstrip('/') + '/APKINDEX.tar.gz'
            with urllib.request.urlopen(repo_url) as response:
                compressed_data = response.read()

        decompressed = gzip.decompress(compressed_data)
        tar_stream = io.BytesIO(decompressed)
        with tarfile.open(fileobj=tar_stream, mode='r') as tar:
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
                raw_deps = deps_str.split()
                clean_deps = []
                for d in raw_deps:
                    if d.startswith('so:'):
                        continue
                    pkg_name = d.split('>=')[0].split('<=')[0].split('=')[0].split('!')[0].strip()
                    clean_deps.append(pkg_name)
                current_deps = clean_deps
            else:
                current_deps = []
        elif line == '':
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
    visited = set()
    stack = [(start_package, 0)]
    in_stack = set()
    has_cycle = False

    while stack:
        node, state = stack.pop()

        if state == 0:
            if node in visited:
                continue

            if node in in_stack:
                has_cycle = True
                continue

            in_stack.add(node)
            dependencies = get_deps_func(node)
            graph[node] = dependencies

            stack.append((node, 1))

            for dep in reversed(dependencies):
                stack.append((dep, 0))

        else:
            in_stack.remove(node)
            visited.add(node)

    return graph, has_cycle


def build_reverse_dependency_graph(start_package: str, all_packages: dict) -> dict:
    """
    Строит обратный граф зависимостей с помощью DFS без рекурсии.
    Обратный граф показывает, какие пакеты зависят от данного пакета.
    """
    # Создаем маппинг: для каждого пакета храним список пакетов, которые от него зависят
    reverse_deps = defaultdict(list)

    # Проходим по всем пакетам в репозитории
    for package, dependencies in all_packages.items():
        for dep in dependencies:
            # Если зависимость совпадает с искомым пакетом или является частью пути к нему
            if dep == start_package:
                reverse_deps[start_package].append(package)
            # Также проверяем, не является ли зависимость префиксом искомого пакета
            elif start_package.startswith(dep + ".") or start_package.startswith(dep + "/"):
                reverse_deps[start_package].append(package)

    # Теперь строим полный граф обратных зависимостей с помощью DFS
    result_graph = defaultdict(list)
    visited = set()
    stack = [start_package]

    while stack:
        current = stack.pop()
        if current in visited:
            continue

        visited.add(current)
        result_graph[current] = reverse_deps.get(current, [])

        # Добавляем все пакеты, которые зависят от текущего, для дальнейшей обработки
        for pkg in reverse_deps.get(current, []):
            if pkg not in visited:
                stack.append(pkg)

    # Преобразуем defaultdict в обычный dict для вывода
    return dict(result_graph)


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


def print_reverse_dependencies_graph(graph: dict, target_package: str) -> None:
    """Выводит граф обратных зависимостей в виде дерева."""

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

    print(f"Обратные зависимости для пакета {target_package} (пакеты, которые зависят от {target_package}):")
    print_dependencies(target_package)


def main():
    parser = argparse.ArgumentParser(
        description="Инструмент визуализации графа зависимостей пакетов (этапы 1-4).",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--package', required=True, help='Имя анализируемого пакета.')
    parser.add_argument('--repo', required=True, help='URL репозитория или путь к APKINDEX.tar.gz / тестовому файлу.')
    parser.add_argument('--mode', required=True, choices=['online', 'offline', 'test'], help='Режим работы.')
    parser.add_argument('--output', required=True, help='Имя файла изображения графа.')
    parser.add_argument('--ascii-tree', required=True, help='Выводить ASCII-дерево? true/false.')
    parser.add_argument('--reverse-deps', required=True, help='Выводить обратные зависимости? true/false.')

    try:
        args = parser.parse_args()

        # === Этап 1: валидация и вывод параметров ===
        package = validate_package_name(args.package)
        repo = validate_repo_url_or_path(args.repo)
        mode = validate_mode(args.mode)
        output = validate_output_file(args.output)
        ascii_tree = validate_ascii_tree(args.ascii_tree)
        reverse_deps = validate_reverse_deps(args.reverse_deps)

        print("Параметры запуска:")
        print(f"package = {package}")
        print(f"repo = {repo}")
        print(f"mode = {mode}")
        print(f"output = {output}")
        print(f"ascii_tree = {ascii_tree}")
        print(f"reverse_deps = {reverse_deps}")
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

        def get_deps(pkg_name):
            return repo_data.get(pkg_name, [])

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

            print("\nГраф зависимостей (все зависимости):")
            for pkg, deps in graph.items():
                if deps:
                    print(f"{pkg} -> {', '.join(deps)}")
                else:
                    print(f"{pkg} -> (нет зависимостей)")

            if ascii_tree:
                print("\nГраф зависимостей в виде ASCII-дерева:")
                print_graph(graph, package)

            if mode == 'test':
                print("\n=== Демонстрация на тестовых данных ===")
                print("Тестовый репозиторий позволяет легко проверить обработку циклических зависимостей.")
                print("Пример файла тестового репозитория:")
                print("A: B C")
                print("B: D")
                print("C: D E")
                print("D: ")
                print("E: B  # Циклическая зависимость: B -> D -> E -> B")

        except Exception as e:
            print(f"Ошибка при построении графа: {e}", file=sys.stderr)
            sys.exit(1)

        # === Этап 4: вывод обратных зависимостей ===
        if reverse_deps:
            print("\n=== Этап 4: Построение графа обратных зависимостей ===")

            if package not in repo_data:
                print(
                    f"Ошибка: пакет '{package}' отсутствует в репозитории. Невозможно построить граф обратных зависимостей.",
                    file=sys.stderr)
                sys.exit(1)

            try:
                reverse_graph = build_reverse_dependency_graph(package, repo_data)
                print(f"Граф обратных зависимостей для пакета '{package}' успешно построен.")

                if package in reverse_graph and reverse_graph[package]:
                    print(f"\nПакеты, зависящие от '{package}':")
                    for pkg in reverse_graph[package]:
                        print(f"  - {pkg}")

                    print("\nПолный граф обратных зависимостей:")
                    for pkg, deps in reverse_graph.items():
                        if deps:
                            print(f"{pkg} <- {', '.join(deps)}")
                        else:
                            print(f"{pkg} <- (нет обратных зависимостей)")

                    if ascii_tree:
                        print("\nГраф обратных зависимостей в виде ASCII-дерева:")
                        print_reverse_dependencies_graph(reverse_graph, package)
                else:
                    print(f"\nНе найдено пакетов, зависящих от '{package}'.")

                if mode == 'test':
                    print("\n=== Демонстрация обратных зависимостей на тестовых данных ===")
                    print("Для тестового репозитория с содержимым:")
                    print("A: B C")
                    print("B: D")
                    print("C: D E")
                    print("E: B")
                    print("Обратные зависимости для пакета D:")
                    print("  - B (зависимость B: D)")
                    print("  - C (зависимость C: D E)")

            except Exception as e:
                print(f"Ошибка при построении графа обратных зависимостей: {e}", file=sys.stderr)
                sys.exit(1)

    except (ValueError, OSError, RuntimeError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()