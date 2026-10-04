"""npm 锁文件（package-lock.json v3 平铺结构）的解析、校验与查询。

仅支持：
- UTF-8 编码的严格 JSON：Python json 默认接受的非标准数值常量
  NaN、Infinity、-Infinity 一律拒绝（出现在任意层级的值位置，
  即使落在不参与分析的元数据中）；字符串内的同名文本不受影响；
- lockfileVersion 为整数 3；
- packages 为对象，且含空串 "" 根节点；
- 根节点及每个包条目均为对象；
- 包条目键为 node_modules/name 或 node_modules/@scope/name（平铺安装，
  @ 后的作用域至少一个字符，包名非空）；
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


def _reject_json_constant(value):
    # Python json 默认把裸 NaN / Infinity / -Infinity 当作合法数值扩展
    # （NaN/Infinity 并非标准 JSON）。parse_constant 仅在这三个标记作为
    # 值出现时回调，不作用于字符串内容、对象键或普通数字；故抛
    # ValueError 即可整份拒绝，由 json.loads 原样向上传播。合法但超出
    # 浮点范围的数字文本（如 1e999）走数字解析路径返回 inf，不会到这里。
    raise ValueError("non-standard JSON constant: " + value)


def _entry_name(key):
    """从平铺安装路径取包名；非支持路径返回 None。"""
    if not key.startswith(_PACKAGES_PREFIX):
        return None
    rest = key[len(_PACKAGES_PREFIX):]
    if rest.startswith("@"):
        # scoped 包：@scope/name，恰好一段斜杠；@ 与斜杠之间的作用域
        # 至少一个字符（"@/pkg" 这类空作用域不支持），包名也非空。
        parts = rest.split("/")
        if len(parts) != 2 or not all(parts) or parts[0] == "@":
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
        # parse_constant 在解析阶段拒绝裸 NaN/Infinity/-Infinity，无论其
        # 位于根、包条目还是任意层级的忽略元数据中；字符串与 1e999 等
        # 合法数字文本不触发该回调，故仍可读取。
        data = json.loads(text, parse_constant=_reject_json_constant)
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


def reachable_names(root_deps, packages_map):
    """返回自根节点沿 dependencies 可达的包名集合（不含根节点）。

    可达性只看 dependencies 声明的包名连边，不解析版本范围，也不从其他
    元数据补充连边。根直接声明的包及其逐层依赖都可达；多条路径引入同一
    包只记录一次。自环与循环由 seen 集合保证每个节点只扩展一次，正常
    结束；完全脱离根节点的包（包括与根断开的循环）整体排除。
    """
    seen = set()
    queue = deque(root_deps)
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        queue.extend(packages_map[name]["deps"])
    return seen


def reachable_items(root_deps, packages_map):
    """返回自根节点沿 dependencies 可达的条目（不含根节点），按名称排序。

    可达性语义见 reachable_names；direct 仍只表示根节点 dependencies
    是否直接声明该包。
    """
    seen = reachable_names(root_deps, packages_map)
    direct = set(root_deps)
    return [
        {"name": name, "version": packages_map[name]["version"], "direct": name in direct}
        for name in sorted(seen)
    ]


def sbom_document(root_deps, packages_map, reachable=False):
    """生成简化 SBOM 文档（产品自有格式，不声明符合其他 SBOM 标准）。

    顶层仅含 format、formatVersion、components。默认 components 覆盖全部
    已安装包（不含根项目），根节点不可达的包同样保留；reachable 为 True
    时只保留自根节点沿 dependencies 可达的包（与 list --reachable 同一
    语义，可达性规则见 reachable_names），格式标记与其余字段含义不变。
    组件按完整包名 Unicode 码点升序排列，每个包名只出现一次。名称与版本
    字符串原样保留；direct 仅表示根节点 dependencies 是否声明该包，与
    其他包的依赖关系无关。许可证与安全元数据本轮不解读，license、
    securityStatus 固定为 "unknown"，未知不代表没有风险，也不根据版本
    推断安全结论。循环依赖不影响结果：组件来自 packages_map 本身，天然
    不重复。
    """
    if reachable:
        names = reachable_names(root_deps, packages_map)
    else:
        names = packages_map.keys()
    direct = set(root_deps)
    components = [
        {
            "name": name,
            "version": packages_map[name]["version"],
            "ecosystem": "npm",
            "direct": name in direct,
            "license": "unknown",
            "securityStatus": "unknown",
        }
        for name in sorted(names)
    ]
    return {
        "format": "depinventory-sbom",
        "formatVersion": 1,
        "components": components,
    }


def diff_items(before_map, after_map):
    """比较两份清单的已安装条目（不含根节点），返回版本差异记录。

    仅新清单存在的标记 added，仅旧清单存在的标记 removed，两边版本字符串
    不同的标记 changed；版本完全相同的包不输出。按包名 Unicode 码点排序，
    每个包最多出现一次。不解析版本范围，也不判断升级、降级或安全风险。

    本函数只比较传入的两份映射；diff --reachable 的根可达筛选由调用方
    先用 reachable_names 过滤两侧映射完成，两参数调用语义不变。
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


def _shortest_path(starts, packages_map, target):
    """自给定起点集合 BFS 到 target，返回起点→目标的最短包名路径。

    starts 须已按字典序排好；不可达返回 []。

    parent[node] 记录首次发现 node 的前驱；起点的前驱为 None。
    同一节点只保留第一次命中：BFS 按层扩展，每一层的入队顺序即
    “到达该层节点的完整包名序列”的字典序（父路径严格有序，子列表
    排序后扩展相同后缀保序），故首次命中就是最短且字典序最小的路径。
    循环依赖与自环由 parent 表保证每个节点只处理一次。

    除起点排序与命中后的路径重建外，单次查询时间 O(V+E)、辅助存储
    O(V)：parent 只记录每个节点首次被发现时的前驱，队列不携带路径
    副本，且出队为 O(1)。不修改 packages_map 及其内的 deps 列表。
    """
    parent = {}
    queue = deque()
    for name in starts:
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
    path.reverse()
    return path


def find_path(root_deps, packages_map, target, source=None):
    """返回到 target 的最短包名路径；不可达返回 []。

    target 不存在抛 NotFoundError。同长度路径按包名序列的 Unicode 码点
    字典序取第一条；结果不受条目或依赖声明顺序影响。

    source 为 None（默认）时自虚拟根 $root 查起：路径以 ROOT 标记开始，
    语义与省略 --from 的 why 查询一致。source 给定时以该已安装包为唯一
    起点：路径从 source 开始、到 target 结束，不添加根标记；source 是否
    能从根节点到达不作要求，一律沿其自身 dependencies 查询。source 的
    字面值没有任何特殊含义，"$root" 也按普通包名查找。source 未安装同样
    抛 NotFoundError。source == target 时返回仅含该包名的路径，不要求存在
    自环；两包均已安装但 source 不可达 target 时返回 []。

    不修改 root_deps、packages_map 及其内的 deps 列表。
    """
    if target not in packages_map:
        raise NotFoundError(target)
    if source is None:
        starts = sorted(root_deps)
        path = _shortest_path(starts, packages_map, target)
        if not path:
            return []
        # 虚拟根不作为图节点参与 BFS，仅在重建出的路径前补上根标记。
        return [ROOT] + path

    if source not in packages_map:
        raise NotFoundError(source)
    # 单起点无需排序；source == target 时首次出队即命中，返回 [source]，
    # 不依赖自环。
    return _shortest_path([source], packages_map, target)


def find_parents(root_deps, packages_map, target):
    """返回 target 的直接上游：{"direct": 是否根声明, "parents": [包名]}。

    target 不存在抛 NotFoundError。

    direct 仅表示根节点 dependencies 是否声明 target。parents 是自身
    dependencies 中直接声明 target 的全部已安装包名（直接边，不逐层展开
    祖先），根项目不作为成员；查询覆盖整份清单，包括自根节点不可达的
    包，因此与根断开的包仍可能成为 parent。parents 按完整包名 Unicode
    码点升序排列并去重；多个包重复声明同一目标各自只出现一次。自环保留
    目标自身（target 声明 target 时 target 进入 parents），循环关系无需
    遍历即可正常结束。

    关系只取 dependencies，不解析版本范围，也不从其他字段补边。
    不修改 root_deps、packages_map 及其内的 deps 列表。
    """
    if target not in packages_map:
        raise NotFoundError(target)
    parents = {
        name
        for name, info in packages_map.items()
        if target in info["deps"]
    }
    return {"direct": target in root_deps, "parents": sorted(parents)}


def find_ancestors(root_deps, packages_map, target):
    """返回 target 的全部上游：沿 dependencies 至少一条边能到达 target 的包名。

    target 不存在抛 NotFoundError。

    祖先是沿自身 dependencies 经过至少一条边（传递展开，不限直接边）
    能到达 target 的全部已安装包；根项目与目标自身始终排除——即使自环
    或循环让目标重新到达自身，也不把目标列入结果。查询覆盖整份清单，
    包括自根节点不可达的包：与根断开的包只要能到达目标同样进入结果。
    结果按完整包名 Unicode 码点升序排列并去重，不受条目与依赖声明顺序
    影响。自环与其他循环由 seen 集合保证每个节点只扩展一次，正常结束；
    循环中能到达目标的其他包保留。已安装但没有其他上游的目标返回空列表。

    关系只取 dependencies，不解析版本范围，也不从其他字段补边。
    root_deps 仅为与其他查询函数保持一致的签名而保留，根项目本就不在
    packages_map 中，天然不会成为祖先。不修改 root_deps、packages_map
    及其内的 deps 列表。
    """
    if target not in packages_map:
        raise NotFoundError(target)
    # 反向 BFS：先建 dep -> 声明者 的反向邻接，自 target 沿反向边访问到
    # 的每个节点，沿正向 dependencies 都能到达 target。
    reverse = {}
    for name, info in packages_map.items():
        for dep in info["deps"]:
            reverse.setdefault(dep, []).append(name)
    seen = {target}
    queue = deque(reverse.get(target, ()))
    ancestors = []
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        ancestors.append(name)
        queue.extend(reverse.get(name, ()))
    return sorted(ancestors)
