#!/usr/bin/env python3
import argparse
import sys
import os
import urllib.request
import tarfile
import gzip
import io
import subprocess
from urllib.parse import urljoin, urlparse
from collections import defaultdict


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
    if not filename.endswith('.png'):
        filename += '.png'
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
    stack = [(start_package, False)]  # (узел, флаг обработки)
    in_stack = set()
    has_cycle = False

    while stack:
        node, processed = stack.pop()

        if processed:
            in_stack.remove(node)
            continue

        if node in visited:
            continue

        # Проверка на цикл
        if node in in_stack:
            has_cycle = True
            continue

        in_stack.add(node)
        visited.add(node)

        dependencies = get_deps_func(node)
        graph[node] = dependencies.copy()

        # Сначала добавляем текущий узел с флагом processed=True
        stack.append((node, True))

        # Затем добавляем все зависимости для обработки
        for dep in reversed(dependencies):
            stack.append((dep, False))

    return graph, has_cycle


def build_reverse_dependency_graph(target_package: str, repo_data: dict) -> list:
    """
    Строит список пакетов, которые зависят от target_package.
    """
    reverse_deps = []

    for package, dependencies in repo_data.items():
        if target_package in dependencies:
            reverse_deps.append(package)

    return reverse_deps


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


def generate_mermaid_code(dependency_graph: dict, start_package: str) -> str:
    """
    Генерирует код на языке Mermaid для визуализации графа зависимостей.
    """
    lines = ["graph TD"]

    # Собираем все уникальные узлы
    nodes = set()
    for pkg, deps in dependency_graph.items():
        nodes.add(pkg)
        for dep in deps:
            nodes.add(dep)

    # Добавляем узлы
    for node in sorted(nodes):
        if node == start_package:
            lines.append(f"    {node}[{node}]")
        else:
            lines.append(f"    {node}[{node}]")

    # Добавляем связи
    edges = set()
    for pkg, deps in dependency_graph.items():
        for dep in deps:
            edges.add((pkg, dep))

    for pkg, dep in sorted(edges):
        lines.append(f"    {pkg} --> {dep}")

    return "\n".join(lines)


def generate_and_save_mermaid_image(mermaid_code: str, output_file: str) -> bool:
    """
    Сохраняет код Mermaid в .mmd файл и пытается сгенерировать изображение.
    """
    # Создаем .mmd файл
    mmd_file = output_file.rsplit('.', 1)[0] + '.mmd'
    with open(mmd_file, 'w', encoding='utf-8') as f:
        f.write(mermaid_code)

    print(f"Код Mermaid сохранен в файл: {mmd_file}")

    # Пытаемся сгенерировать изображение, если установлен mmdc
    try:
        subprocess.run(['mmdc', '--version'], capture_output=True, check=True)
        print("Генерация изображения с помощью mmdc...")

        cmd = ['mmdc', '-i', mmd_file, '-o', output_file]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            print(f"Изображение успешно сохранено в файл: {output_file}")
            return True
        else:
            print(f"Ошибка при генерации изображения: {result.stderr}", file=sys.stderr)
            print("Изображение не было сгенерировано. Используйте сохраненный .mmd файл для ручной генерации.")
            return False

    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Инструмент mmdc (mermaid-cli) не найден в PATH.", file=sys.stderr)
        print("Для автоматической генерации изображений установите его командой:", file=sys.stderr)
        print("  npm install -g @mermaid-js/mermaid-cli", file=sys.stderr)
        print("После установки выполните команду для генерации изображения:", file=sys.stderr)
        print(f"  mmdc -i {mmd_file} -o {output_file}")
        return False


def compare_with_standard_tools(package: str, mode: str, repo: str):
    """
    Сравнивает результаты с штатными инструментами визуализации.
    """
    print("\n=== Сравнение с штатными инструментами ===")

    if mode in ('online', 'offline'):
        print(f"Для Alpine Linux (apk) можно использовать следующие команды для анализа зависимостей:")
        print(f"  apk info -R {package}              # Показать прямые зависимости")
        print(f"  apk info --depends {package}       # Показать обратные зависимости")
        print("\nНаш инструмент предоставляет дополнительные возможности:")
        print("- Визуализацию полного графа транзитивных зависимостей")
        print("- Обнаружение циклических зависимостей")
        print("- Представление зависимостей в виде наглядного графа и ASCII-дерева")
    else:
        print("В тестовом режиме сравнение со штатными инструментами не выполняется.")


def demonstrate_three_packages(repo_data, get_deps_func, mode, ascii_tree, output_prefix):
    """
    Демонстрирует визуализацию для трех различных пакетов.
    """
    print("\n=== Демонстрация визуализации зависимостей для трех различных пакетов ===")

    # Выбираем три пакета для демонстрации
    demo_packages = []
    if mode == 'test':
        # Для тестового режима берем первые три пакета
        demo_packages = list(repo_data.keys())[:3]
    else:
        # Для реального репозитория Alpine выбираем распространенные пакеты
        candidates = ['curl', 'busybox', 'openssl']
        for pkg in candidates:
            if pkg in repo_data and len(demo_packages) < 3:
                demo_packages.append(pkg)

    # Если не нашли достаточно пакетов, берем любые доступные
    if len(demo_packages) < 3:
        remaining = [p for p in repo_data.keys() if p not in demo_packages]
        demo_packages.extend(remaining[:3 - len(demo_packages)])

    for pkg in demo_packages:
        print(f"\n--- Пакет: {pkg} ---")

        if pkg not in repo_data:
            print(f"  Пакет {pkg} не найден в репозитории.")
            continue

        # Получаем прямые зависимости
        pkg_deps = repo_data[pkg]
        if pkg_deps:
            print(f"  Прямые зависимости:")
            for dep in pkg_deps:
                print(f"    - {dep}")
        else:
            print("  Нет прямых зависимостей")

        # Строим граф зависимостей
        pkg_graph, has_cycle = build_dependency_graph_dfs(pkg, get_deps_func)

        # Генерируем код Mermaid
        pkg_mermaid_code = generate_mermaid_code(pkg_graph, pkg)
        print("\nКод Mermaid:")
        print(pkg_mermaid_code)

        # Сохраняем изображение
        pkg_output = f"{output_prefix}_{pkg}.png"
        generate_and_save_mermaid_image(pkg_mermaid_code, pkg_output)

        # Выводим в ASCII-дереве, если требуется
        if ascii_tree:
            print("\nГраф зависимостей в виде ASCII-дерева:")
            print_graph(pkg_graph, pkg)


def main():
    parser = argparse.ArgumentParser(
        description="Инструмент визуализации графа зависимостей пакетов Alpine Linux.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument('--package', required=True, help='Имя анализируемого пакета.')
    parser.add_argument('--repo', required=True, help='URL репозитория или путь к файлу тестового репозитория.')
    parser.add_argument('--mode', required=True, choices=['online', 'offline', 'test'],
                        help='Режим работы с тестовым репозиторием.')
    parser.add_argument('--output', required=True, help='Имя сгенерированного файла с изображением графа.')
    parser.add_argument('--ascii-tree', required=True, help='Режим вывода зависимостей в формате ASCII-дерева.')

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
        print()

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

        except Exception as e:
            print(f"Ошибка при построении графа: {e}", file=sys.stderr)
            sys.exit(1)

        # === Этап 4: вывод обратных зависимостей ===
        print("\n=== Этап 4: Вывод обратных зависимостей ===")

        if package not in repo_data:
            print(f"Ошибка: пакет '{package}' отсутствует в репозитории. Невозможно найти обратные зависимости.",
                  file=sys.stderr)
            sys.exit(1)

        try:
            reverse_deps = build_reverse_dependency_graph(package, repo_data)
            if reverse_deps:
                print(f"\nОбратные зависимости для пакета '{package}' (пакеты, которые зависят от него):")
                for rdep in reverse_deps:
                    print(f"  - {rdep}")
            else:
                print(f"\nНет обратных зависимостей для пакета '{package}'.")

            if mode == 'test':
                print("\n=== Демонстрация обратных зависимостей на тестовых данных ===")
                print("Для тестового репозитория с содержимым:")
                print("A: B C")
                print("B: D")
                print("C: D E")
                print("D: ")
                print("E: B")
                print("Обратные зависимости для пакета D:")
                print("  - B (зависимость: B -> D)")
                print("  - C (зависимость: C -> D E)")

        except Exception as e:
            print(f"Ошибка при поиске обратных зависимостей: {e}", file=sys.stderr)
            sys.exit(1)

        # === Этап 5: Визуализация графа зависимостей ===
        print("\n=== Этап 5: Визуализация графа зависимостей ===")

        try:
            # Генерируем код Mermaid
            mermaid_code = generate_mermaid_code(graph, package)
            print("\nКод Mermaid для визуализации графа:")
            print(mermaid_code)

            # Генерируем и сохраняем изображение
            generate_and_save_mermaid_image(mermaid_code, output)

            # Сравнение с штатными инструментами
            compare_with_standard_tools(package, mode, repo)

            # Демонстрация для трех пакетов
            demonstrate_three_packages(repo_data, get_deps, mode, ascii_tree, output.rsplit('.', 1)[0])

            print("\nЭтап 5 успешно завершен. Визуализация графа сохранена в файл(ы).")

        except Exception as e:
            print(f"Ошибка при визуализации графа: {e}", file=sys.stderr)
            sys.exit(1)

    except (ValueError, OSError, RuntimeError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nПрервано пользователем.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()