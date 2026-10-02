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
from collections import deque

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


def diff_items(before_map, after_map):
    """比较两份清单的已安装条目（不含根节点），返回版本差异记录。

    仅新清单存在的标记 added，仅旧清单存在的标记 removed，两边版本字符串
    不同的标记 changed；版本完全相同的包不输出。按包名 Unicode 码点排序，
    每个包最多出现一次。不解析版本范围，也不判断升级、降级或安全风险。
    """
    records = []
    for name in sorted(set(before_map) | set(after_map)):
        before = before_map.get(name)
        after = after_map.get(name)
        if before is None:
            records.append(
                {"name": name, "change": "added", "before": None, "after": after["version"]}
            )
        elif after is None:
            records.append(
                {"name": name, "change": "removed", "before": before["version"], "after": None}
            )
        elif before["version"] != after["version"]:
            records.append(
                {
                    "name": name,
                    "change": "changed",
                    "before": before["version"],
                    "after": after["version"],
                }
            )
    return records


def find_path(root_deps, packages_map, target):
    """返回从 $root 到 target 的最短包名路径；不可达返回 []。

    包不存在抛 NotFoundError。同长度路径按包名序列的 Unicode 码点字典序
    取第一条；循环依赖由 parent 表保证每个节点只处理一次。

    除根直依赖排序与命中后的路径重建外，单次查询时间 O(V+E)、辅助存储
    O(V)：parent 只记录每个节点首次（即最短、字典序最小）被发现时的
    前驱，队列不携带路径副本，且出队为 O(1)。不修改 root_deps、
    packages_map 及其内的 deps 列表。
    """
    if target not in packages_map:
        raise NotFoundError(target)

    # parent[node] 记录首次发现 node 的前驱；根直依赖的前驱为 None。
    # 用 None 而非 ROOT 字符串作哨兵，避免与同名包 "$root" 混淆。
    # 同一节点只保留第一次命中：BFS 按层扩展，每一层的入队顺序即
    # “到达该层节点的完整包名序列”的字典序（父路径严格有序，子列表
    # 排序后扩展相同后缀保序），故首次命中就是最短且字典序最小的路径。
    parent = {}
    queue = deque()
    for name in sorted(root_deps):
        if name not in parent:
            parent[name] = None
            queue.append(name)

    found = False
    while queue:
        node = queue.popleft()
        if node == target:
            found = True
            break
        for dep in sorted(packages_map[node]["deps"]):
            if dep not in parent:
                parent[dep] = node
                queue.append(dep)

    if not found:
        return []

    # 仅对命中的目标做一次回溯重建，长度等于路径边数，总量仍为 O(V)。
    path = [target]
    predecessor = parent[target]
    while predecessor is not None:
        path.append(predecessor)
        predecessor = parent[predecessor]
    path.append(ROOT)
    path.reverse()
    return path
