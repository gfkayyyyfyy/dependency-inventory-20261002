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
4. **路径选择**：`find_path()` 调用同文件的 `_shortest_path()` 做广度优先
   搜索，选出边数最少、同长度按包名序列字典序最小的路径。
5. **JSON 输出**：`main()` 把结果组装为 `{"name": ..., "path": ...}`，用
   `json.dumps(..., ensure_ascii=False)` 加一个换行写入标准输出，返回 0。

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
2. `json.loads(text, parse_constant=_reject_json_constant)` 解析；裸
   `NaN`/`Infinity`/`-Infinity` 由 `_reject_json_constant()` 拒绝，JSON
   损坏同样转为 `InputError`。
3. 结构校验：根必须是对象、`lockfileVersion` 必须为整数 3、`packages` 必须
   是含空串根节点的对象；`_entry_name()` 把每个条目键解释为平铺安装路径，
   不支持的键（嵌套路径、空名、畸形作用域）直接拒绝；`link: true`、空版本、
   重复条目也拒绝。
4. `_validate_dependencies()` 对根节点和**每个**包条目检查
   `dependencies`：可省略，出现时必须是对象、键值均为字符串，且声明的包必须
   已安装（悬空依赖拒绝）。

关键时序：**整份校验先于任何查询包名检查**。`main()` 先完整跑完
`load_lockfile()`，成功后才调用 `find_path()`；因此即使结构错误位于根节点
不可达的条目上，输入也整体失败，不会先报包不存在。

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

`_shortest_path(starts, packages_map, target)`（`lockfile.py`）做广度优先
搜索：

- `parent[node]` 记录首次发现 `node` 的前驱，起点的前驱为 `None`；每个节点
  只入队一次，因此**循环依赖与自环天然正常结束**，不会死循环。
- 扩展每个节点时对其 `deps` 排序后入队；BFS 按层扩展保证**边数最少优先**，
  同层按序扩展保证同长度路径中**包名序列的 Unicode 码点字典序最小**者首次
  命中。结果只取决于图关系，与条目书写顺序、`dependencies` 声明顺序无关。
- 命中后沿 `parent` 链回溯一次重建路径；未命中返回 `[]`。

边界结果：

- 目标已安装但起点（集合）无法到达它 → 返回空路径 `[]`（成功，退出 0）。
- 起点等于目标（`--from x` 查 `x`）→ 首次出队即命中，返回单元素路径
  `["x"]`，不要求存在自环。

## 阶段四：JSON 输出与失败出口

`main()` 成功时执行
`sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")`：标准输出
是仅含 `name` 与 `path` 两个键的 JSON 对象，末尾恰有一个换行，标准错误为
空，退出码 0。

失败出口（标准输出均为空，不输出部分结果或堆栈）：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 起点或目标未安装 | `find_path()` 抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行 |
| 文件不可读、非法 UTF-8、JSON 损坏、悬空依赖、不支持的结构 | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |

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

- 第一条：起点集合为根依赖 `["alpha"]`，BFS 沿 `alpha → beta → leaf` 命中，
  重建路径前补上根标记，得到 `["$root", "alpha", "beta", "leaf"]`。
  `beta` 与 `leaf` 的互相依赖由 `parent` 表保证每个节点只处理一次：扩展
  `leaf` 时 `beta` 已在表中，不会再次入队，循环正常结束。
- 第二条：`--from orphan` 把唯一起点改为 `orphan`。`orphan` 虽然从根节点不
  可达，但 `--from` 不要求起点可达，沿其自身 `dependencies` 一步到达
  `leaf`，路径为 `["orphan", "leaf"]`，不含 `$root` 标记。

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
- `_shortest_path()` 的 BFS 主体为 O(V + E)（V 为已安装包数，E 为依赖边
  数），但**每个节点出队时对其 `deps` 排序**，排序总量为
  O(Σ dᵢ log dᵢ)（dᵢ 为各包直接依赖数），是查询的主要额外开销。
- 命中后的路径回溯与反转各为 O(路径长度)，总量不超过 O(V)。
- 辅助存储 O(V)：`parent` 表与队列每节点至多一份，不携带路径副本。

加载阶段 `load_lockfile()` 的读取、JSON 解析与整份校验随文件大小线性增长，
与查询相互独立。

## 不变量

- 工具只读本地输入文件，不联网、不改写任何输入。
- 本文档不改动产品代码、公开接口（`depinventory/__init__.py` 的导出）、
  README、SBOM.md 及任何样例锁文件。
