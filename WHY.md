# 依赖来源查询（why）流程说明

本文说明 `python -m depinventory why <lockfile> <name> [--from 包名]` 从输入到
输出的完整数据流，并用文件名与函数名定位源码。行为约定与 README 一致；本文
只做解释，不改变任何产品代码、公开接口、README、SBOM.md 或样例文件。工具
只读本地输入文件，不联网。

## 总览：从命令行到 JSON 输出

一次 why 查询依次经过五个阶段，全部在 `depinventory/__main__.py` 的 `main()`
与 `depinventory/lockfile.py` 中完成：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `why` 子命令，位置
   参数为锁文件路径与目标包名，`--from` 存入 `args.source`（省略时为
   `None`）。
2. **本地文件读取与整份校验**：`main()` 调用 `lockfile.py` 的
   `load_lockfile(path)`，返回 `(root_deps, packages_map)` 两个数据结构。
3. **起点与目标匹配**：`main()` 把 `root_deps`、`packages_map`、`args.name`、
   `args.source` 传给 `lockfile.py` 的 `find_path()`，由它检查目标与起点
   是否已安装，并确定 BFS 的起点集合。
4. **路径选择**：`find_path()` 调用同文件的 `_shortest_path()`；
   `_shortest_path()` 把遍历交给 `_bfs_parents()`（与 SBOM 路径导出共用的
   同一套 BFS），命中后再由 `_reconstruct_path()` 沿前驱表回溯出路径。
5. **JSON 编码与写出**：`main()` 把结果组装为 `{"name": ..., "path": ...}`，
   先经 `__main__.py` 的 `_encode_result()` 整体序列化并编码为 UTF-8 字节，
   再一次性写入 `sys.stdout.buffer`，返回 0。

## 两个核心数据结构

`load_lockfile()` 的返回值是后续所有查询的输入：

- **根依赖 `root_deps`**：字符串列表，取自锁文件根节点（`packages` 中空串
  `""` 条目）的 `dependencies` 的键，按声明顺序保留。它表示根项目直接声明
  依赖了哪些包，是默认查询（不带 `--from`）的起点集合。
- **包映射 `packages_map`**：字典，键为完整包名（如 `alpha`、
  `@scope/name`），值为 `{"version": 版本字符串, "deps": [依赖包名...]}`。
  每个包条目来自 `packages` 中 `node_modules/name` 形式的平铺安装路径，
  `deps` 取自该条目的 `dependencies` 的键。

关系的边界：**只有根节点与包条目的 `dependencies` 建立依赖关系**，其余字段
（`devDependencies`、`peerDependencies`、`resolved`、`integrity` 等）一律不
参与连边；`dependencies` 中的**版本范围（如 `"*"`、`"^1.0.0"`）只是校验时
要求的字符串，查询阶段完全不解析**，匹配只按包名。支持范围沿用 README 的
约定：UTF-8 严格 JSON、`lockfileVersion` 为整数 3、平铺的
`node_modules/name` 与 `node_modules/@scope/name` 安装路径、版本为非空字符
串；嵌套安装路径与 `link: true` 条目不支持。

## 阶段一：本地文件读取与整份校验

`load_lockfile()`（`lockfile.py`）按顺序执行：

1. 以 UTF-8 文本模式读取文件；`OSError`（文件不可读）与
   `UnicodeDecodeError`（非法 UTF-8）都转为 `InputError`。
2. `json.loads(text, parse_constant=_reject_json_constant,
   object_pairs_hook=_reject_duplicate_keys)` 解析：裸
   `NaN`/`Infinity`/`-Infinity` 由 `_reject_json_constant()` 拒绝；同一
   JSON 对象内两个解码后完全相同的键由 `_reject_duplicate_keys()` 拒绝
   （即使两个值相同，覆盖顶层、`packages`、各条目、`dependencies`、额外
   元数据及数组内对象）；JSON 损坏同样转为 `InputError`。
3. 结构校验：根必须是对象、`lockfileVersion` 必须为整数 3、`packages` 必须
   是含空串根节点的对象；`_entry_name()` 把每个条目键解释为平铺安装路径，
   不支持的键（嵌套路径、空名、畸形作用域）直接拒绝；`link: true`、空版本、
   重复条目也拒绝。
4. `_validate_dependencies()` 对根节点和**每个**包条目检查
   `dependencies`：可省略，出现时必须是对象、键值均为字符串，且声明的包必须
   已安装（悬空依赖拒绝）。

关键时序：**整份校验（含重复对象键）先于任何查询包名检查**。`main()` 先完整
跑完 `load_lockfile()`，成功后才调用 `find_path()`；因此即使结构错误位于根
节点不可达的条目上，输入也整体失败，不会先报包不存在。

## 阶段二：起点与目标匹配

`find_path(root_deps, packages_map, target, source=None)`（`lockfile.py`）：

- `target` 不在 `packages_map` 中 → 抛 `NotFoundError`。
- `source` 为 `None`（省略 `--from`）：起点集合为 `sorted(root_deps)`，即
  从虚拟根 `$root` 查起。虚拟根不是图节点，不参与 BFS，只在重建出的路径前
  补上 `ROOT` 常量（值为 `"$root"`）作为路径首元素。
- `source` 给定：先检查 `source` 是否已安装，未安装同样抛
  `NotFoundError`；已安装则以它为**唯一**起点，不加 `$root` 标记。起点是否
  能从根节点到达不作要求，一律沿其自身 `dependencies` 查询。

包名匹配规则：起点与目标都按**区分大小写的完整包名**匹配；作用域包以
`@scope/name` 整体作为一个包名匹配，不拆分。`--from` 的字面值没有任何特殊
含义——`--from '$root'` 是按普通安装包名 `$root` 查找（通常未安装，从而
`NOT_FOUND`），不是虚拟根的别名。

## 阶段三：路径选择

路径选择由三个函数串成，调用关系为：

```
find_path()  →  _shortest_path(starts, packages_map, target)
                   →  _bfs_parents(starts, packages_map, target)   # 遍历
                   →  _reconstruct_path(parent, target)            # 回溯
```

`_bfs_parents()`（`lockfile.py`）是**唯一维护路径选择规则的地方**，单包查询
（经 `_shortest_path()`）与 SBOM 路径导出（经 `_build_root_paths()`）共用同
一套遍历，规则不会在两处各自演化：

- `starts` 由调用方按完整包名字典序排好后传入，作为同一层起点入队，
  起点的前驱记为 `None`。
- `parent[node]` 记录**首次发现** `node` 的前驱；每个节点只在首次发现时
  入队一次，之后再次遇到（自环、循环、共享依赖）直接跳过，因此遍历天然
  正常结束，不会死循环。
- 扩展每个节点时对其 `deps` 执行 `sorted(...)` 后按序入队：BFS 按层扩展
  保证**边数最少优先**；每层入队顺序即到达该层节点的包名序列顺序，父路径
  严格有序、子列表排序后扩展相同后缀保序，故同长度路径中**包名序列的
  Unicode 码点字典序最小**者首次命中。结果只取决于图关系，与条目书写顺序、
  `dependencies` 声明顺序无关。
- `target` 给定时（why 查询总是给定），**目标出队即停止**：此时 `parent`
  中目标的前驱已是最优，函数不再扩展目标的邻接，直接返回前驱表。

`_shortest_path()` 只做命中判定与重建衔接：`target` 不在返回的 `parent`
表中则返回 `[]`；否则调用 `_reconstruct_path()` 沿 `parent` 链自目标回溯到
起点（起点前驱为 `None` 时停止），反转一次得到 起点→目标 的包名路径。

边界结果：

- 目标已安装但起点（集合）无法到达它 → 返回空路径 `[]`（成功，退出 0）。
- 起点等于目标（`--from x` 查 `x`）→ 目标首次出队即停止，返回单元素路径
  `["x"]`，不要求存在自环。

## 与 sbom --with-paths 的路径一致性

`sbom --with-paths` 的组件路径由 `lockfile.py` 的 `_build_root_paths()`
生成，它调用的是**同一个** `_bfs_parents(sorted(root_deps), packages_map)`
（不传 `target`，一次遍历算出全部可达节点的前驱），再统一用
`_reconstruct_path()` 回溯并在前面补 `ROOT` 标记。默认 why（省略
`--from`）经 `find_path()` 走的是同一种子、同一套排序与首次前驱规则，只是
在目标出队时提前停止——提前停止不改变已记录的前驱，因此两者对同一组件给出
的 `$root` 起始路径必然一致；根不可达的组件在 SBOM 中 `path` 为 `[]`，与
why 对不可达目标返回空路径同样对应。

## 阶段四：JSON 编码、写出与失败出口

`main()` 成功时先把结果交给 `__main__.py` 的 `_encode_result()`：
`json.dumps(result, ensure_ascii=False) + "\n"` 在内存中**先完成 UTF-8
编码**，得到整份字节后才 `sys.stdout.buffer.write(payload)` 一次写出并
flush。标准输出是仅含 `name` 与 `path` 两个键的 JSON 对象，末尾恰有一个
换行，标准错误为空，退出码 0；锁文件只读，不写回。

先编码后写出的时序保证：JSON 转义文本可解码出孤立 Unicode 代理码点（如
`"2.0-\ud83f"`），严格 UTF-8 无法表示它们，`.encode("utf-8")` 抛
`UnicodeEncodeError`，`main()` 按输入错误处理（退出码 2、标准错误仅
`INPUT_ERROR` 加换行），且此时尚未写过任何标准输出字节。判断只针对**实际
输出**的文本：why 的结果只含包名与路径，未参与输出的版本字符串或其他元数据
即使含孤立代理码点也不影响 why 成功。

失败出口（标准输出均为空，不输出部分结果或堆栈）：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 起点或目标未安装 | `find_path()` 抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行 |
| 文件不可读、非法 UTF-8、JSON 损坏、重复对象键、悬空依赖、不支持的结构 | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |
| 实际输出文本含孤立代理码点 | `_encode_result()` 抛 `UnicodeEncodeError` | 2 | `INPUT_ERROR` 加换行 |

`main()` 中加载阶段的 `try/except` 先于查询阶段，故同一命令上输入无效与包
名不存在同时成立时，报 `INPUT_ERROR`（整份校验优先）。

## 示例：sample-lock.json 的两次查询

`sample-lock.json` 中：根节点仅声明 `alpha`；`alpha` 依赖 `beta`；`beta` 与
`leaf` 互相依赖（构成循环）；`orphan` 依赖 `leaf` 但不被根节点引用（根不可
达）；`isolated` 无任何依赖。

```sh
$ python -m depinventory why sample-lock.json leaf
{"name": "leaf", "path": ["$root", "alpha", "beta", "leaf"]}
$ python -m depinventory why sample-lock.json leaf --from orphan
{"name": "leaf", "path": ["orphan", "leaf"]}
```

两条命令都以退出码 0 结束，标准错误为空，输出末尾有一个换行。

- 第一条：起点集合为排序后的根依赖 `["alpha"]`。`_bfs_parents()` 先令
  `parent["alpha"] = None`；`alpha` 出队（不是目标），扩展发现
  `beta`（`parent["beta"] = "alpha"`）；`beta` 出队，扩展发现
  `leaf`（`parent["leaf"] = "beta"`）；`leaf` 出队即命中目标，遍历在此
  停止——`leaf` 自身声明的 `beta` 这条回边**不会被扩展**，谈不上再次入队，
  循环就此正常结束。`_reconstruct_path()` 沿前驱链
  `leaf → beta → alpha` 回溯反转得 `["alpha", "beta", "leaf"]`，
  `find_path()` 补上根标记，输出 `["$root", "alpha", "beta", "leaf"]`。
- 第二条：`--from orphan` 把唯一起点改为 `orphan`。`orphan` 虽然从根节点不
  可达，但 `--from` 不要求起点可达：`orphan` 出队后扩展发现
  `leaf`（`parent["leaf"] = "orphan"`），`leaf` 出队即停止，回溯得
  `["orphan", "leaf"]`，不含 `$root` 标记。

两次查询路径不同，正是因为起点不同：默认查询从根声明出发，必须穿过
`alpha`、`beta`；`--from` 查询从指定包出发，只走它自己的依赖边。

## 等长路径的选择规则

`tests/test_why_paths.py` 的 `cycle_lock_data()` 构造了三条同为 3 跳的
leaf 路径：`$root/alpha/charlie/leaf`、`$root/alpha/delta/leaf`、
`$root/zeta/beta/leaf`，且书写顺序刻意与字典序相反（根依赖 `zeta` 写在
`alpha` 前，`alpha` 的 `delta` 写在 `charlie` 前）。

- 边数优先：更短的路径无条件胜出，见
  `test_direct_root_declaration_beats_lexicographic_order`（根直接声明
  `leaf` 后，即使 `leaf` 在根依赖中排最后，结果也是 `["$root", "leaf"]`）。
- 同长度按包名序列的 Unicode 码点字典序取第一条：`"alpha" < "zeta"`，且
  `"charlie" < "delta"`，故结果为 `["$root", "alpha", "charlie", "leaf"]`，
  见 `test_equal_length_paths_use_lexicographic_order_api` 与
  `test_equal_length_paths_use_lexicographic_order_cli`（类常量
  `EXPECTED_LEAF_PATH`）。
- 声明顺序不影响结果：`test_declaration_order_swap_keeps_result_identical`
  递归交换所有对象的键书写顺序后，接口与命令行结果保持不变。

## 开销说明

单次 why 查询（不含加载与校验）的成本来自几处明确的位置，不能笼统称为线性：

- `find_path()` 对 `root_deps` 排序一次：O(k log k)，k 为根直接依赖数。
- `_bfs_parents()` 的 BFS 主体为 O(V + E)（V 为已安装包数，E 为依赖边
  数），但**每个节点出队时对其 `deps` 排序**，排序总量为
  O(Σ dᵢ log dᵢ)（dᵢ 为各包直接依赖数），是查询的主要额外开销；目标出队
  即停止，不必遍历全图。
- 命中后 `_reconstruct_path()` 的回溯与反转各为 O(路径长度)，总量不超过
  O(V)。
- 辅助存储 O(V)：`parent` 表与队列每节点至多一份，不携带路径副本。

加载阶段 `load_lockfile()` 的读取、JSON 解析与整份校验随文件大小线性增长，
与查询相互独立。

## 不变量

- 工具只读本地输入文件，不联网、不改写任何输入。
- 本文档不改动产品代码、公开接口（`depinventory/__init__.py` 的导出）、
  README、SBOM.md 及任何样例锁文件。
