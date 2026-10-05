"""npm 锁文件（package-lock.json v3 平铺结构）的解析、校验与查询。

仅支持：
- UTF-8 编码的严格 JSON：Python json 默认接受的非标准数值常量
  NaN、Infinity、-Infinity 一律拒绝（出现在任意层级的值位置，
  即使落在不参与分析的元数据中）；字符串内的同名文本不受影响；
- 同一个 JSON 对象内出现两个解码后完全相同的键一律拒绝（即使两个值
  相同）：覆盖顶层、packages、根条目、各包条目、dependencies、额外
  元数据以及数组内的对象，根不可达包内的重复键同样使整份输入失败；
  重复只在同一对象内判断，不同对象各自的同名键合法；
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


def _reject_json_constant(value):
    # Python json 默认把裸 NaN / Infinity / -Infinity 当作合法数值扩展
    # （NaN/Infinity 并非标准 JSON）。parse_constant 仅在这三个标记作为
    # 值出现时回调，不作用于字符串内容、对象键或普通数字；故抛
    # ValueError 即可整份拒绝，由 json.loads 原样向上传播。合法但超出
    # 浮点范围的数字文本（如 1e999）走数字解析路径返回 inf，不会到这里。
    raise ValueError("non-standard JSON constant: " + value)


def _reject_duplicate_keys(pairs):
    # object_pairs_hook 对解析出的每个 JSON 对象回调一次（顶层、
    # packages、根条目、包条目、dependencies、额外元数据及数组内对象
    # 全覆盖），pairs 保留重复键的每一次出现，且键名已经按 JSON 字符串
    # 规则解码完成。故只在同一个对象内部比对：出现两个完全相同的键即抛
    # ValueError 整份拒绝，即使两个值相同也拒绝；不同对象各有同名键互不
    # 影响。按解码后的完整文本区分大小写直接比较，不做 Unicode 规范化
    # （JSON 键 "alpha" 与 "alpha" 的转义写法——首字母 a 写成
    # 反斜杠 u0061——解码后相同，算重复；"alpha" 与 "Alpha" 不算）；
    # 字符串值里的同文文本根本不是键，不参与判断。json 解析器先
    # 回调内层对象、后回调外层对象，故根不可达包内、不参与分析的元数据
    # 中的重复键同样在此被拦下，--reachable 等筛选发生在加载成功之后，
    # 无法绕过。异常经 json.loads 原样向上传播，由加载入口统一包成
    # InputError，不返回部分数据。
    seen = set()
    for key, _value in pairs:
        if key in seen:
            raise ValueError("duplicate JSON object key")
        seen.add(key)
    return dict(pairs)


def _entry_name(key):
    """从平铺安装路径取包名；非支持路径返回 None。"""
    if not key.startswith(_PACKAGES_PREFIX):
        return None
    rest = key[len(_PACKAGES_PREFIX):]
    if rest.startswith("@"):
        # scoped 包：@scope/name，恰好一段斜杠；@ 与斜杠之间的作用域
        # 至少一个字符（"@" 本身不算作用域），包名一侧同样非空。
        parts = rest.split("/")
        if len(parts) != 2 or parts[0] == "@" or not all(parts):
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
        # 合法数字文本不触发该回调，故仍可读取。object_pairs_hook 在同一
        # 解析阶段拒绝任何对象内的重复键（重复对仍完整保留在 pairs 中，
        # 键已解码），覆盖范围与可达性筛选无关。两者抛出的 ValueError
        # 都在此统一转为 InputError。
        data = json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
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


def _sorted_items(names, packages_map, direct):
    """由包名集合生成统一结构的清单条目，按完整包名 Unicode 码点升序。

    四种清单（完整、直接、可达、不可达）共用的唯一条目构造处，共同的
    输出规则只在此维护一次：

    - 每项仅含 name、version、direct 三个键；version 取安装条目的原始
      字符串，不解析版本范围；
    - direct 只表示根节点 dependencies 是否直接声明该包，其他包的引用
      不改变它；
    - 结果按完整包名的 Unicode 码点升序排列，区分大小写，作用域包作为
      完整名称参与排序，与安装条目及依赖声明的书写顺序无关；
    - 根项目不在 packages_map 中，天然不会进入任何清单。

    只读，不修改 packages_map 及其内的 deps 列表。
    """
    return [
        {"name": name, "version": packages_map[name]["version"], "direct": name in direct}
        for name in sorted(names)
    ]


def list_items(root_deps, packages_map):
    """返回全部已安装条目（不含根节点）。

    成员为 packages_map 全部包名；条目结构、排序与 direct 语义统一由
    _sorted_items 维护。不修改 root_deps、packages_map 及其内的 deps
    列表。
    """
    return _sorted_items(packages_map, packages_map, set(root_deps))


def direct_items(root_deps, packages_map):
    """返回根节点 dependencies 直接声明的已安装条目。

    只保留空串根节点 dependencies 直接声明的包：所有条目 direct 均为
    True；同一包即使同时被其他包引用也只出现一次；仅经其他包引入的传递
    依赖与未被引用的包同样排除。根 dependencies 省略或为空时返回 []。
    条目结构、排序与 direct 语义统一由 _sorted_items 维护。
    不修改 root_deps、packages_map 及其内的 deps 列表。
    """
    direct = set(root_deps)
    return _sorted_items(direct, packages_map, direct)


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
    """返回自根节点沿 dependencies 可达的条目（不含根节点）。

    可达性语义见 reachable_names；条目结构、排序与 direct 语义统一由
    _sorted_items 维护。不修改 root_deps、packages_map 及其内的 deps
    列表。
    """
    seen = reachable_names(root_deps, packages_map)
    return _sorted_items(seen, packages_map, set(root_deps))


def unreachable_items(root_deps, packages_map):
    """返回自根节点沿 dependencies 不可达的条目（不含根节点）。

    可达集合的判定与 reachable_names 完全一致；本函数取其补集：全部已安装
    包中不属于可达集合的条目。不可达包即使声明了某个可达包，也不会因此
    被视为可达（关系方向只从声明者到依赖包）。与根断开的自环和循环中的
    包全部保留。结果中 direct 均为 False——不可达包不可能被根节点直接
    声明。根 dependencies 省略或为空时，全部已安装包都在结果中。
    条目结构与排序统一由 _sorted_items 维护。
    不修改 root_deps、packages_map 及其内的 deps 列表。
    """
    seen = reachable_names(root_deps, packages_map)
    names = [name for name in packages_map if name not in seen]
    return _sorted_items(names, packages_map, set())


def _bfs_parents(starts, packages_map, target=None):
    """统一的依赖图 BFS：返回各节点首次被发现时的前驱表。

    这是唯一维护路径选择规则的地方，单包查询（find_path 经
    _shortest_path）与批量导出（sbom_document 经 _build_root_paths）
    共用同一套遍历，规则不会在两处各自演化：

    - 路径只沿 dependencies 连边，不解析版本范围；
    - starts 须已按完整包名 Unicode 码点字典序排好，作为同一层起点；
    - parent[node] 记录首次发现 node 的前驱，起点的前驱为 None；
    - 每层扩展前对邻接 sorted(deps) 排序：BFS 按层扩展保证边数最少，
      每层入队顺序即"到达该层节点的完整包名序列"的字典序（父路径严格
      有序，子列表排序后扩展相同后缀保序），故首次命中就是边数最少且
      包名序列字典序最小的路径，与条目及声明书写顺序无关；
    - 同一节点只保留第一次命中：自环、循环与共享依赖由 parent 表保证
      每个节点只扩展一次，正常结束。

    target 为 None 时遍历全部可达节点（批量导出一次算出所有来源）；
    给定时在 target 出队即停止（单包查询不必遍历全图，此时 parent 中
    target 的前驱已是最优）。时间 O(V+E)、辅助存储 O(V)：parent 只记录
    每个节点首次被发现时的前驱，队列不携带路径副本，出队为 O(1)。
    只读，不修改 packages_map 及其内的 deps 列表。
    """
    parent = {}
    queue = deque()
    for name in starts:
        if name not in parent:
            parent[name] = None
            queue.append(name)
    while queue:
        node = queue.popleft()
        if node == target:
            break
        for dep in sorted(packages_map[node]["deps"]):
            if dep not in parent:
                parent[dep] = node
                queue.append(dep)
    return parent


def _reconstruct_path(parent, target):
    """沿 parent 表自 target 回溯到起点，返回 起点→target 的包名路径。

    target 必须在 parent 中；长度等于路径边数，仅回溯一次。
    """
    path = [target]
    predecessor = parent[target]
    while predecessor is not None:
        path.append(predecessor)
        predecessor = parent[predecessor]
    path.reverse()
    return path


def _build_root_paths(root_deps, packages_map):
    """一次 BFS 得到全部包相对根标记 $root 的最短路径。

    遍历与裁决规则统一由 _bfs_parents 维护，与省略 --from 的 find_path
    同源同序：第一层按 sorted(root_deps) 播种。根不可达的包不在结果中
    （调用方以 [] 补齐）。直接依赖只含根标记与包名，作用域包是单个路径
    元素。只读，不修改 root_deps、packages_map 及其内的 deps 列表。
    """
    parent = _bfs_parents(sorted(root_deps), packages_map)
    return {name: [ROOT] + _reconstruct_path(parent, name) for name in parent}


_PURL_UNRESERVED = frozenset(
    b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


def _purl_quote(text):
    """对单个名称片段或版本做 PURL 百分号编码。

    按 UTF-8 字节编码：仅保留 ASCII 字母、数字与 -._~ 不转义，其余字节
    一律写成大写十六进制的 %XX（空格 %20、加号 %2B、百分号 %25）；
    输入中已有的百分号文本只作普通字符处理，绝不先解码再编码，非 ASCII
    字符按其 UTF-8 字节逐字节转义。结构分隔符 / 与 @ 不在此函数处理，
    由调用方按 PURL 结构原样保留。text 含孤对代理等无法编码为 UTF-8 的
    码点时向上抛 UnicodeEncodeError，由调用方统一转为 InputError。
    """
    out = []
    for byte in text.encode("utf-8"):
        if byte in _PURL_UNRESERVED:
            out.append(chr(byte))
        else:
            out.append("%{:02X}".format(byte))
    return "".join(out)


def _package_purl(name, version):
    """由完整包名与原始版本构造 npm Package URL。

    普通包为 pkg:npm/包名@版本；作用域包为
    pkg:npm/作用域/包名@版本，作用域含开头的 @（@ 本身不是非保留字符，
    编码为 %40，只有结构分隔用的 / 与版本前的 @ 原样保留）。名称与版本
    各自经 _purl_quote 按 UTF-8 字节编码，不解析版本范围、不改大小写，
    也不从其他元数据补充标识。name 须为已通过结构校验的平铺包名。
    """
    if name.startswith("@"):
        # 已校验的作用域包名恰好含一段斜杠，两侧均非空。
        scope, _, pkg_name = name.partition("/")
        path = _purl_quote(scope) + "/" + _purl_quote(pkg_name)
    else:
        path = _purl_quote(name)
    return "pkg:npm/" + path + "@" + _purl_quote(version)


def sbom_document(
    root_deps, packages_map, reachable=False, with_paths=False,
    with_dependencies=False, with_purl=False,
):
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

    with_paths 为 True（CLI 的 --with-paths）时，每个组件在既有六个字段
    之外再附带 path 数组；省略或为 False 时输出字段与本函数旧结果完全
    一致。path 与同一输入省略 --from 的 why 查询一致：自 "$root" 开始、
    到组件完整包名结束，只沿 dependencies 取边数最少的路径，等长时按
    包名序列的 Unicode 码点字典序取第一条；直接依赖只经过根标记与包名。
    已安装但根不可达的组件（完整导出保留、reachable 筛选后不存在）path
    为 []，作用域包作为单个路径元素，自环、循环与共享依赖正常结束且每
    个组件只出现一次；路径选择不受条目与依赖声明顺序影响。可达路径只做
    一次 BFS 统一计算，不修改入参。

    with_dependencies 为 True（CLI 的 --with-dependencies）时，每个组件
    再附带 dependencies 数组：该包条目 dependencies 直接声明的完整包名，
    不展开传递依赖、不附带版本范围，也不从其他元数据补边；按完整包名
    Unicode 码点升序排列并去重，区分大小写，作用域包名作为整体保留；
    声明省略或为空时输出 []。自环保留自身，循环双方分别保留各自的声明。
    与 reachable 组合时仅为筛选后保留的组件附加该数组，被排除组件的
    关系不影响保留组件的数组内容；与 with_paths 可同时启用，互不影响。
    省略或为 False 时组件不含 dependencies 字段，输出与旧结果完全一致。
    只读，不修改入参。

    with_purl 为 True（CLI 的 --with-purl）时，每个组件再附带字符串
    purl：只使用完整包名与原始版本，普通包为 pkg:npm/包名@版本，作用域
    包为 pkg:npm/作用域/包名@版本，作用域含开头的 @。各名称片段与版本按
    UTF-8 字节作百分号编码，只保留 ASCII 字母、数字及 -._~，十六进制
    字母大写；结构分隔的 / 与 @ 保留，空格、加号、百分号分别编码为
    %20、%2B、%25，不解码输入中已有的百分号文本。不解析版本范围、不改
    大小写，也不从其他元数据补充标识；name 与 version 字段原样保留。
    未加 --reachable 时根不可达的包同样获得 purl，空清单仍为 []。
    名称或版本含无法按 UTF-8 编码的码点（如 JSON 转义出的孤对代理）时
    抛 InputError；省略或为 False 时组件不含 purl 字段，输出与旧结果
    完全一致。可与 reachable、with_paths、with_dependencies 任意组合，
    筛选、排序与既有附加字段语义不变。只读，不修改入参。
    """
    if reachable:
        names = reachable_names(root_deps, packages_map)
    else:
        names = packages_map.keys()
    ordered = sorted(names)
    direct = set(root_deps)
    paths = _build_root_paths(root_deps, packages_map) if with_paths else None
    components = []
    for name in ordered:
        version = packages_map[name]["version"]
        component = {
            "name": name,
            "version": version,
            "ecosystem": "npm",
            "direct": name in direct,
            "license": "unknown",
            "securityStatus": "unknown",
        }
        if with_purl:
            try:
                component["purl"] = _package_purl(name, version)
            except UnicodeEncodeError as exc:
                raise InputError("purl text cannot be encoded as UTF-8") from exc
        if with_dependencies:
            # 只取该条目直接声明的包名：去重后按 Unicode 码点升序，
            # 自环与循环声明原样保留，不展开传递依赖。
            component["dependencies"] = sorted(set(packages_map[name]["deps"]))
        if with_paths:
            component["path"] = paths.get(name, [])
        components.append(component)
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

    本函数只比较传入的两份映射；diff --reachable 的根可达筛选、
    diff --unreachable 的根不可达筛选与 diff --direct 的根直接声明
    筛选都由调用方先过滤两侧映射完成，两参数调用语义不变。
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

    遍历与裁决规则统一由 _bfs_parents 维护（边数最少、等长按完整包名
    序列的 Unicode 码点字典序、自环与循环只扩展一次，详见该函数）；
    这里只负责命中判定与路径重建。target 命中即提前结束，不必遍历全图；
    仅对命中的目标做一次回溯重建，长度等于路径边数，总量仍为 O(V)。
    不修改 packages_map 及其内的 deps 列表。
    """
    parent = _bfs_parents(starts, packages_map, target)
    if target not in parent:
        return []
    return _reconstruct_path(parent, target)


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


def _traverse_related(target, neighbors_of):
    """自 target 的直接邻接节点出发 BFS，返回至少经过一条边可达的成员。

    neighbors_of(name) 返回 name 的邻接节点可迭代对象：下游查询传其正向
    dependencies，上游查询传反向邻接（声明了 name 的全部包）。

    去重、循环终止与排除查询包自身的规则在此统一维护，两项查询不再各自
    实现遍历：target 预先放入 seen，自环与回到 target 的循环都不会把
    target 计入结果，循环中的其他成员保留；同一节点经多条路径汇合只记录
    一次。初始队列直接播种 target 的邻接，保证结果成员都至少经过一条边。
    结果按完整包名 Unicode 码点升序排列，与邻接的声明、遍历顺序无关。
    遍历只读，不修改 packages_map 及其内的 deps 列表。
    """
    seen = {target}
    queue = deque(neighbors_of(target))
    members = []
    while queue:
        name = queue.popleft()
        if name in seen:
            continue
        seen.add(name)
        members.append(name)
        queue.extend(neighbors_of(name))
    return sorted(members)


def find_ancestors(root_deps, packages_map, target):
    """返回 target 的全部上游：沿 dependencies 至少一条边能到达 target 的包名。

    target 不存在抛 NotFoundError。

    祖先是沿自身 dependencies 经过至少一条边（传递展开，不限直接边）
    能到达 target 的全部已安装包；根项目与目标自身始终排除——即使自环
    或循环让目标重新到达自身，也不把目标列入结果。查询覆盖整份清单，
    包括自根节点不可达的包：与根断开的包只要能到达目标同样进入结果。
    结果按完整包名 Unicode 码点升序排列并去重，不受条目与依赖声明顺序
    影响。自环与其他循环在 _traverse_related 中由 seen 集合保证每个节点
    只扩展一次，正常结束；循环中能到达目标的其他包保留。已安装但没有
    其他上游的目标返回空列表。

    关系只取 dependencies，不解析版本范围，也不从其他字段补边。
    root_deps 仅为与其他查询函数保持一致的签名而保留，根项目本就不在
    packages_map 中，天然不会成为祖先。不修改 root_deps、packages_map
    及其内的 deps 列表。
    """
    if target not in packages_map:
        raise NotFoundError(target)
    # 反向邻接 dep -> 声明者；自 target 沿反向边访问到的每个节点，沿正向
    # dependencies 都能到达 target。遍历规则统一由 _traverse_related 维护。
    reverse = {}
    for name, info in packages_map.items():
        for dep in info["deps"]:
            reverse.setdefault(dep, []).append(name)
    return _traverse_related(target, lambda name: reverse.get(name, ()))


def find_descendants(root_deps, packages_map, target):
    """返回 target 的全部下游：自 target 沿 dependencies 可达的包名。

    target 不存在抛 NotFoundError。

    下游是自 target 出发、沿其 dependencies 经过至少一条边（直接与传递
    依赖都保留）能到达的全部已安装包；根项目与 target 自身始终排除——
    自环或循环让遍历重新到达 target 也不把 target 列入结果。起点即使
    从根节点不可达，也一律沿其自身 dependencies 展开；根 dependencies
    省略或为空不改变该规则。结果按完整包名 Unicode 码点升序排列并去重，
    同一包被多条路径引入只出现一次，不受条目与依赖声明顺序影响。自环与
    其他循环在 _traverse_related 中由 seen 集合保证每个节点只扩展一次，
    正常结束；循环中其他可达包保留。已安装但没有依赖的 target 成功返回
    空列表。

    关系只取 dependencies，不解析版本范围，也不从其他字段补边。
    root_deps 仅为与其他查询函数保持一致的签名而保留，根项目本就不在
    packages_map 中，天然不会成为下游。不修改 root_deps、packages_map
    及其内的 deps 列表。
    """
    if target not in packages_map:
        raise NotFoundError(target)
    # 正向邻接即各包的 dependencies；遍历规则统一由 _traverse_related 维护。
    return _traverse_related(target, lambda name: packages_map[name]["deps"])
