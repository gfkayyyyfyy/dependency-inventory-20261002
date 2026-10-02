"""npm 锁文件（package-lock.json v3 平铺结构）的解析、校验与查询。

仅支持：
- UTF-8 编码的 JSON；
- lockfileVersion 为整数 3；
- packages 为对象，且含空串 "" 根节点；
- 根节点及每个包条目均为对象；
- 包条目键为 node_modules/name 或 node_modules/@scope/name（平铺安装）；
- version 为非空字符串；
- 依赖关系只取自根节点及包条目的 dependencies：
  可省略；出现时须为对象，值为字符串，且声明的包必须存在。

不解析版本范围；嵌套安装路径与 link: true 条目不在支持范围。
"""

import json

ROOT = "$root"
_ROOT_KEY = ""
_PACKAGES_PREFIX = "node_modules/"


class InputError(Exception):
    """锁文件不可读、不是合法 UTF-8、JSON 损坏或结构不受支持。"""


class NotFoundError(Exception):
    """查询的包名不在锁文件中。"""


def _is_int(value):
    # bool 是 int 的子类，需显式排除。
    return isinstance(value, int) and not isinstance(value, bool)


def _entry_name(key):
    """从平铺安装路径取包名；非支持路径返回 None。"""
    if not key.startswith(_PACKAGES_PREFIX):
        return None
    rest = key[len(_PACKAGES_PREFIX):]
    if rest.startswith("@"):
        # scoped 包：@scope/name，恰好一段斜杠，两侧均非空。
        parts = rest.split("/")
        if len(parts) != 2 or not all(parts):
            return None
        return rest
    if not rest or "/" in rest:
        # 空名称，或嵌套路径（node_modules/a/node_modules/b）均不支持。
        return None
    return rest


def _validate_dependencies(node, present_names):
    # 键可省略；显式 null（或任何非对象值）都不符合约定，必须拒绝。
    if "dependencies" not in node:
        return
    deps = node["dependencies"]
    if not isinstance(deps, dict):
        raise InputError("dependencies must be an object")
    for dep_name, spec in deps.items():
        if not isinstance(dep_name, str) or not isinstance(spec, str):
            raise InputError("dependency names and values must be strings")
        if dep_name not in present_names:
            raise InputError("dependency points to missing package")


def load_lockfile(path):
    """读取并校验锁文件，返回 (root_deps, {name: {"version", "deps"}})。

    任何读取、解析或结构问题都抛 InputError。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise InputError("cannot read lockfile") from exc
    except UnicodeDecodeError as exc:
        # 文件可读但不是合法 UTF-8：严格模式下整份输入作废，
        # 不得忽略、替换坏字节或猜测其他编码后继续解析。
        raise InputError("lockfile is not valid UTF-8") from exc

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise InputError("invalid JSON") from exc

    if not isinstance(data, dict):
        raise InputError("lockfile root must be an object")

    version = data.get("lockfileVersion")
    if not _is_int(version) or version != 3:
        raise InputError("only lockfileVersion 3 is supported")

    packages = data.get("packages")
    if not isinstance(packages, dict) or _ROOT_KEY not in packages:
        raise InputError("packages must be an object containing the root entry")

    root_node = packages[_ROOT_KEY]
    if not isinstance(root_node, dict):
        raise InputError("root entry must be an object")

    entries = {}
    for key, node in packages.items():
        if not isinstance(key, str):
            raise InputError("package keys must be strings")
        if key == _ROOT_KEY:
            continue
        name = _entry_name(key)
        if name is None:
            raise InputError("unsupported package install path")
        if name in entries:
            raise InputError("duplicate package entry")
        if not isinstance(node, dict):
            raise InputError("package entry must be an object")
        if node.get("link") is True:
            raise InputError("linked packages are not supported")
        pkg_version = node.get("version")
        if not isinstance(pkg_version, str) or pkg_version == "":
            raise InputError("package version must be a non-empty string")
        entries[name] = {"version": pkg_version, "node": node}

    present = set(entries)
    _validate_dependencies(root_node, present)
    for info in entries.values():
        _validate_dependencies(info["node"], present)

    def dep_names(node):
        deps = node.get("dependencies")
        return list(deps.keys()) if isinstance(deps, dict) else []

    root_deps = dep_names(root_node)
    packages_map = {
        name: {"version": info["version"], "deps": dep_names(info["node"])}
        for name, info in entries.items()
    }
    return root_deps, packages_map


def list_items(root_deps, packages_map):
    """返回全部已安装条目（不含根节点），按名称 Unicode 码点排序。"""
    direct = set(root_deps)
    return [
        {"name": name, "version": packages_map[name]["version"], "direct": name in direct}
        for name in sorted(packages_map.keys())
    ]


def find_path(root_deps, packages_map, target):
    """返回从 $root 到 target 的最短包名路径；不可达返回 []。

    包不存在抛 NotFoundError。同长度路径按包名序列的 Unicode 码点字典序
    取第一条；用 visited 集合保证循环依赖不会导致无限遍历。
    """
    if target not in packages_map:
        raise NotFoundError(target)

    sorted_root_deps = sorted(root_deps)
    # BFS：每个节点只需处理一次；邻居按名称排序，使字典序最小的路径先到达。
    visited = set()
    queue = []
    for name in sorted_root_deps:
        if name not in visited:
            visited.add(name)
            queue.append((name, [ROOT, name]))

    while queue:
        node, path = queue.pop(0)
        if node == target:
            return path
        for dep in sorted(packages_map[node]["deps"]):
            if dep in visited:
                continue
            visited.add(dep)
            queue.append((dep, path + [dep]))
    return []
