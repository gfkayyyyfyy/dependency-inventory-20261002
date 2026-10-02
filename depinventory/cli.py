"""命令行入口：解析 npm 锁文件并支持 list / why 查询。

仅支持：UTF-8 JSON、lockfileVersion 为整数 3、packages 为对象且含空串
根节点的平铺锁文件。其余字段不参与连边，不解析版本范围。
"""

import json
import sys
from collections import deque

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_INPUT_ERROR = 2

ROOT_KEY = ""
ROOT_LABEL = "$root"


class InputError(Exception):
    """锁文件不可读取、结构或字段无效。"""


def _load_document(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, UnicodeError, ValueError):
        raise InputError()


def _validate_dependencies(value):
    """dependencies 可省略；出现时须为对象且每个值为字符串。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InputError()
    for spec in value.values():
        if not isinstance(spec, str):
            raise InputError()
    return value


def _name_from_path(key):
    """从 node_modules/name 或 node_modules/@scope/name 路径取名称。"""
    parts = key.split("/")
    if len(parts) == 2 and parts[0] == "node_modules":
        name = parts[1]
        if name.startswith("@"):
            # @ 开头必须是三段的 @scope/name 形式。
            raise InputError()
    elif len(parts) == 3 and parts[0] == "node_modules" \
            and len(parts[1]) > 1 and parts[1].startswith("@") and parts[2]:
        name = parts[1] + "/" + parts[2]
    else:
        # 嵌套安装路径等平铺之外的结构不支持。
        raise InputError()
    if not name:
        raise InputError()
    return name


def parse_lockfile(path):
    """读取并校验锁文件，返回 (根节点 dependencies, 包条目列表)。"""
    doc = _load_document(path)

    if not isinstance(doc, dict):
        raise InputError()
    version = doc.get("lockfileVersion")
    if type(version) is not int or version != 3:
        raise InputError()

    packages = doc.get("packages")
    if not isinstance(packages, dict) or ROOT_KEY not in packages:
        raise InputError()

    root_node = packages[ROOT_KEY]
    if not isinstance(root_node, dict):
        raise InputError()
    root_deps = _validate_dependencies(root_node.get("dependencies"))

    entries = []
    for key, node in packages.items():
        if key == ROOT_KEY:
            continue
        if not isinstance(node, dict):
            raise InputError()
        if node.get("link") is True:
            raise InputError()
        version_field = node.get("version")
        if not isinstance(version_field, str) or not version_field:
            raise InputError()
        deps = _validate_dependencies(node.get("dependencies"))
        entries.append({"name": _name_from_path(key),
                        "version": version_field, "dependencies": deps})

    # 只按 dependencies 连边：声明的包须存在（名称取自平铺安装路径）。
    installed = {entry["name"] for entry in entries}
    for dep_name in root_deps:
        if dep_name not in installed:
            raise InputError()
    for entry in entries:
        for dep_name in entry["dependencies"]:
            if dep_name not in installed:
                raise InputError()

    return root_deps, entries


def _build_graph(root_deps, entries):
    """构造以包名为节点、按 dependencies 连边的有向图（含 $root 起点）。"""
    graph = {ROOT_LABEL: set(root_deps)}
    for entry in entries:
        graph.setdefault(entry["name"], set()).update(entry["dependencies"])
    return graph


def cmd_list(path):
    root_deps, entries = parse_lockfile(path)
    direct = set(root_deps)

    rows = [{"name": entry["name"], "version": entry["version"],
             "direct": entry["name"] in direct} for entry in entries]
    # Python 字符串默认比较即 Unicode 码点序。
    rows.sort(key=lambda row: row["name"])
    sys.stdout.write(json.dumps(rows, ensure_ascii=False) + "\n")
    return EXIT_OK


def _shortest_path(graph, target):
    """边数最少的路径；同长按包名序列码点字典序取第一条。

    BFS 逐层扩展，同层按已排序路径顺序入队并在首次到达时标记访问，
    故首次到达目标的路径既是最短路径也是字典序最小路径；visited
    同时保证循环依赖不会导致无限遍历。
    """
    queue = deque([[ROOT_LABEL]])
    visited = {ROOT_LABEL}
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == target:
            return path
        for neighbor in sorted(graph.get(node, ())):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(path + [neighbor])
    return []


def cmd_why(path, package_name):
    root_deps, entries = parse_lockfile(path)
    installed = {entry["name"] for entry in entries}
    if package_name not in installed:
        sys.stderr.write("NOT_FOUND\n")
        return EXIT_NOT_FOUND

    route = _shortest_path(_build_graph(root_deps, entries), package_name)
    sys.stdout.write(
        json.dumps({"name": package_name, "path": route}, ensure_ascii=False)
        + "\n")
    return EXIT_OK


def main(argv):
    try:
        if len(argv) == 2 and argv[0] == "list":
            return cmd_list(argv[1])
        if len(argv) == 3 and argv[0] == "why":
            return cmd_why(argv[1], argv[2])
    except InputError:
        sys.stderr.write("INPUT_ERROR\n")
        return EXIT_INPUT_ERROR

    sys.stderr.write("INPUT_ERROR\n")
    return EXIT_INPUT_ERROR
