# 依赖来源（`why` / `why --from`）查询说明

本文档说明 `why` 命令从锁文件路径参数到 JSON 输出的完整数据流，并以文件名
与函数名定位源码。工具只读本地输入、仅使用 Python 3 标准库，不联网、不安装
或执行锁文件中的依赖，也不改写输入文件。支持范围与 README「支持范围」一节
完全相同：平铺的 npm `package-lock.json` v3 锁文件（`node_modules/name` 与
`node_modules/@scope/name`），嵌套安装路径与 `link: true` 条目不在支持范围。

## 数据流与源码对应关系

```text
命令行：python -m depinventory why <lockfile> <name> [--from <包名>]
   │
   ▼
main(argv)                               depinventory/__main__.py
   │  _build_parser() 中 why 子命令解析 lockfile、name 与 --from
   │  （dest="source"，缺省为 None），随后先调用 load_lockfile
   ▼
load_lockfile(path)                      depinventory/lockfile.py
   │  本地文件读取 → 严格 JSON 解析 → 整份结构校验，
   │  返回 (root_deps, packages_map)；任何读取、解析或结构
   │  问题抛 InputError（不区分条目是否与查询相关）
   ▼
find_path(root_deps, packages_map, target, source=None)
                                         depinventory/lockfile.py
   │  先做起点/目标包名匹配：未安装抛 NotFoundError；
   │  再选定起点集合并调用 _shortest_path 做 BFS
   ▼
_shortest_path(starts, packages_map, target)
                                         depinventory/lockfile.py
   │  起点须已排序；按层扩展，每层邻接先 sorted() 再入队，
   │  parent 表只保留每个节点的首个前驱；命中目标后回溯一次
   ▼
main() 组装 {"name": args.name, "path": path}
                                         depinventory/__main__.py
   │  sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
   ▼
标准输出：仅含 name 与 path 的 JSON 对象 + 末尾一个换行（退出码 0）
```

关键源码位置：

| 阶段 | 位置 |
| --- | --- |
| 命令行参数定义（`name`、`--from`） | `depinventory/__main__.py` 的 `_build_parser`（`why_parser`） |
| 加载与 `InputError` 处理（退出码 2） | `depinventory/__main__.py` 的 `main`，`load_lockfile` 调用段 |
| 查询与 `NotFoundError` 处理（退出码 1） | `depinventory/__main__.py` 的 `main`，`find_path` 调用段 |
| 成功输出（`name`/`path` 对象与末尾换行） | `depinventory/__main__.py` 的 `main`：`json.dumps(..., ensure_ascii=False) + "\n"` |
| 读取、解析、整份校验 | `depinventory/lockfile.py` 的 `load_lockfile` |
| 安装路径取包名 | `depinventory/lockfile.py` 的 `_entry_name` |
| 悬空依赖等 `dependencies` 校验 | `depinventory/lockfile.py` 的 `_validate_dependencies` |
| 起点/目标匹配与两种起点模式 | `depinventory/lockfile.py` 的 `find_path` |
| BFS、字典序裁决、循环终止、路径重建 | `depinventory/lockfile.py` 的 `_shortest_path` |
| 根标记常量 `ROOT = "$root"` | `depinventory/lockfile.py`，并由 `depinventory/__init__.py` 再导出 |

## 整份校验与加载产物：`root_deps` 与 `packages_map`

`load_lockfile(path)` 在任何包名查询之前完成**整份**输入处理：

1. **本地文件读取**：以 UTF-8 打开并整体读入。文件不可读抛 `InputError`
   （"cannot read lockfile"）；字节不是合法 UTF-8 时严格拒绝，不替换坏字节、
   不猜测编码，同样抛 `InputError`。
2. **整份 JSON 解析**：`json.loads` 配合 `parse_constant` 拒绝裸
   `NaN`/`Infinity`/`-Infinity`（任意层级，包括不参与分析的元数据）；JSON
   损坏（含非法常量）抛 `InputError`（"invalid JSON"）。
3. **整份结构校验**：要求根为对象、`lockfileVersion` 为整数 `3`、`packages`
   为含空串根节点 `""` 的对象；随后遍历**每一个**包条目——安装路径须能被
   `_entry_name` 识别为平铺的 `node_modules/name` 或
   `node_modules/@scope/name`，条目须为对象、不得 `link: true`、`version`
   须为非空字符串，重名条目拒绝。
4. **依赖声明校验**：`_validate_dependencies` 检查根节点与**每个**包条目
   （包括根不可达条目）的 `dependencies`：可省略；出现时须为对象，键与值均
   为字符串，且每个声明目标都必须已安装，否则以悬空依赖抛 `InputError`。

校验通过后返回一个二元组：

- **`root_deps`（根依赖）**：根节点 `""` 的 `dependencies` 键名列表，按 JSON
  中的声明顺序保留。它表示根项目**直接声明**了哪些已安装包，是省略 `--from`
  时 BFS 的起点集合；根项目本身不是图中的包节点。
- **`packages_map`（包映射）**：`{包名: {"version": 版本字符串, "deps":
  [依赖包名, ...]}}`。键是 `_entry_name` 从平铺安装路径剥离
  `node_modules/` 前缀得到的**完整包名**（作用域包整体保留 `@scope/name`）；
  `deps` 同样只保留声明顺序的包名列表。

### 关系边界：只有 `dependencies` 建立连边

- 图的边**只**取自根节点与包条目的 `dependencies`：键声明谁，就有一条指向谁
  的有向边。`devDependencies`、`peerDependencies`、`optionalDependencies` 等
  其他字段一律不参与连边。
- **版本范围不参与查询**：`"*"`、`"^1.0.0"`、`"1.x"` 等值只被校验为非空
  字符串，从不解析、不比较、不影响路径；路径选择只看包名序列与边的有无。
- 因此 `sample-lock.json` 中所有值都是 `"*"` 与版本都是 `"1.0.0"` 对
  `why` 结果没有任何影响。

## 起点与目标匹配

`find_path(root_deps, packages_map, target, source=None)` 的匹配规则：

- 按**区分大小写的完整包名**在 `packages_map` 中精确匹配：`leaf` 与 `LEAF`
  是不同的名字，不存在即未安装；不做前缀、子串或大小写折叠匹配。
- **作用域包整体匹配**：`@scope/name` 作为一个完整名字使用，`@scope` 与
  `name` 不能拆开查询。
- `target` 先检查；给定 `source` 时再检查 `source`。有效输入中任一未安装即
  抛 `NotFoundError`（CLI 退出码 1，见下文）。
- 省略 `--from`（`source is None`）：起点集合是**根依赖**
  `sorted(root_deps)`，命中后在路径前补虚拟根标记 `ROOT`（`"$root"`）。
  `$root` 不是图节点，不参与 BFS。
- 带 `--from 包名`：以该已安装包为**唯一**起点，路径从起点包开始，不加
  `$root` 标记；起点是否根可达不作要求，一律沿其自身 `dependencies` 查询。
  `--from` 的字面值没有任何特殊含义——**`--from "$root"` 表示名为 `$root`
  的普通安装包**，不是虚拟根别名；该包未安装时同样 `NOT_FOUND`。
- **起点等于目标**：该起点首次出队即命中，返回只含该包名的单元素路径
  （如 `["leaf"]`），不要求存在自环。
- **已安装但起点无法到达目标**：BFS 结束未命中，返回空路径 `[]`。省略
  `--from` 时，"起点"即根依赖集合：包已安装但与根断开（含与根断开的循环）
  时返回 `[]`，这仍是成功查询而非错误。

## 路径选择：边数优先，等长按包名序列字典序

`_shortest_path(starts, packages_map, target)` 用广度优先搜索保证：

1. **边数最少优先**：BFS 按层扩展，先在第 1 层（起点直接依赖）、再第 2
   层……目标第一次被取出队列时所在层即最少边数；更短的路径永远优先，与包名
   大小无关。
2. **同长度按包名序列的 Unicode 码点字典序取第一条**：起点列表在
   `find_path` 中先 `sorted()`（`--from` 单起点无需排序）；BFS 扩展每个节点
   时对其 `deps` 先 `sorted()` 再入队。父路径在同层内严格有序、子列表排序后
   保持同序后缀扩展，因此队列中"到达各节点的完整包名序列"在每一层都按
   Unicode 码点字典序排列。`parent[node]` 只记录节点**首次被发现**时的前驱，
   后到的等长路径被丢弃，所以首次命中同时是最短且字典序最小的路径。
3. **声明顺序不影响结果**：加载器保留 JSON 键的声明顺序，但裁决完全依赖上述
   两处显式排序；交换锁文件中条目或依赖的书写顺序（图关系不变），结果不变。
4. **循环依赖与自环正常结束**：`parent` 表使每个节点至多入队、处理一次；沿
   循环边回到已发现节点时直接跳过，不会无限展开，也不需要遍历整张图之外的
   任何状态。
5. 查询不修改 `root_deps`、`packages_map` 及其中的 `deps` 列表（排序作用于
   临时列表）。

## 用 `sample-lock.json` 走两次查询

`sample-lock.json` 的关系（根仅声明 `alpha`）：

```text
$root → alpha → beta ⇄ leaf
orphan → leaf        （orphan 已安装但根不可达）
isolated             （已安装，无任何依赖声明）
```

其中 `beta` 依赖 `leaf`、`leaf` 又依赖 `beta`，二者互相依赖构成循环。

### 查询一：省略 `--from`，自根节点查 `leaf`

```sh
$ python -m depinventory why sample-lock.json leaf
{"name": "leaf", "path": ["$root", "alpha", "beta", "leaf"]}
```

- 退出码 `0`，标准错误为空，标准输出就是上面这一个**仅含 `name` 与 `path`**
  的 JSON 对象，末尾有一个换行。
- 起点集合为 `sorted(root_deps)`，即只有 `["alpha"]`；BFS 逐层得到
  `alpha`（1 跳）→ `beta`（2 跳）→ `leaf`（3 跳），命中后回溯为
  `alpha → beta → leaf`，再补上虚拟根标记，得到
  `["$root", "alpha", "beta", "leaf"]`。
- `orphan → leaf` 这条边**不参与**本次查询：`orphan` 不在根依赖集合中、自根
  不可达，根的遍历根本不会到达它。
- `leaf → beta` 的回头边指向已发现的 `beta`，`parent` 表阻止其再次入队，
  `beta ⇄ leaf` 循环不会造成死循环；目标在本次出队命中时即停止扩展。
- 对照：`why sample-lock.json isolated` 与
  `why sample-lock.json orphan` 均成功返回 `"path": []`（已安装但根不可达）。

### 查询二：`--from orphan`，以 `orphan` 为起点查 `leaf`

```sh
$ python -m depinventory why sample-lock.json leaf --from orphan
{"name": "leaf", "path": ["orphan", "leaf"]}
```

- 退出码 `0`，标准错误为空，输出同样是仅含 `name` 与 `path` 的 JSON 对象加
  末尾换行；路径不含 `$root` 标记。
- `--from` 不要求起点根可达：`orphan` 作为唯一起点，其 `deps` 直接含
  `leaf`，1 跳命中，故为 `["orphan", "leaf"]`。`alpha`/`beta` 一支与
  `$root` 标记都不出现。
- 两次查询的路径差异完全来自起点集合不同：默认查询只能走根项目声明的
  `alpha` 一支（3 跳）；指定 `orphan` 后直接利用它自己声明的边（1 跳）。
- 本例中 `leaf` 在出队时即命中、其 `deps` 尚未扩展；即便扩展，回头的
  `beta` 也已是已发现节点。同一循环在两个方向的查询下都由 `parent` 表保证
  正常结束。

这两条样例同时被 `tests/test_why_from_combinatorial.py` 的
`test_queries_without_from_unchanged`（`["$root","alpha","beta","leaf"]`）与
`test_orphan_to_leaf`（`["orphan","leaf"]`）作为命令行验收锁定。

## 等长路径核对：`tests/test_why_paths.py`

`tests/test_why_paths.py` 的 `cycle_lock_data()` 构造了三条**等长**（均为
3 跳）到达 `leaf` 的路径，且声明顺序刻意与字典序相反（根先声明 `zeta` 后
`alpha`；`alpha` 先声明 `delta` 后 `charlie`）：

```text
$root → alpha → charlie → leaf
$root → alpha → delta   → leaf
$root → zeta  → beta    → leaf
```

另有 `charlie → alpha` 构成可达循环，根不可达的 `orphan` 自依赖。类属性
`WhyPathRegression.EXPECTED_LEAF_PATH` 固定期望结果：

```python
EXPECTED_LEAF_PATH = ["$root", "alpha", "charlie", "leaf"]
```

裁决过程：起点排序后为 `["alpha", "zeta"]`（而非声明顺序）；同层先扩展
`alpha`，其邻接排序后先入队 `charlie` 后 `delta`；于是第 3 层最先被发现的
`leaf` 前驱是 `charlie`。三条路径逐项比较包名序列：第 1 项 `alpha` <
`zeta`，第 2 项 `charlie` < `delta`，故取
`["$root", "alpha", "charlie", "leaf"]`。

对应测试定位：

| 测试方法 | 核对内容 |
| --- | --- |
| `test_equal_length_paths_use_lexicographic_order_api` | API 层面等长路径取字典序最小者 |
| `test_equal_length_paths_use_lexicographic_order_cli` | 同一规则的命令行输出、退出码与换行 |
| `test_declaration_order_swap_keeps_result_identical` | 递归反转全部键顺序后结果不变（声明顺序无关） |
| `test_direct_root_declaration_beats_lexicographic_order` | 根直接声明 `leaf` 后 1 跳路径优先：**边数优先于字典序** |
| `test_reachable_cycle_terminates_with_determined_paths` | 循环上各节点查询均正常结束且路径唯一确定 |
| `test_orphan_unreachable_with_self_dependency_api/_cli` | 已安装但根不可达（且自依赖）返回 `[]` |
| `test_uninstalled_ghost_not_found_api/_cli` | 未安装目标：`NotFoundError` / 退出码 1 |

`--from` 一侧的等长路径、自环、作用域名、字面 `$root` 包、区分大小写与
未安装语义见 `tests/test_why_from.py`（如
`test_shortest_path_with_lexicographic_tie_break_api/_cli`、
`test_source_equals_target_needs_no_self_loop`、
`test_literal_root_name_is_a_normal_package`、`test_case_sensitive_full_name_only`）；
`tests/test_why_from_combinatorial.py` 还在 4096 种边组合上用独立路径枚举
逐包对照了全部起点 × 目标结果。

## 成功与失败的输出约定

**成功**（无论路径是否为空）：

- 退出码 `0`，标准错误为空；
- 标准输出为单个 JSON 对象，**只有** `name` 与 `path` 两个字段：
  `{"name": "<查询名>", "path": [...]}`，以 `json.dumps(..., ensure_ascii=False)`
  序列化（非 ASCII 包名原样输出），末尾恰好一个换行；
- 起点无法到达目标（含目标根不可达）时 `path` 为 `[]`，仍是退出码 `0` 的
  成功输出；起点等于目标时 `path` 为单元素数组。

**有效输入但查询包未安装**（`find_path` 抛 `NotFoundError`）：

- 起点或目标任一未安装；
- 退出码 `1`，标准错误仅为 `NOT_FOUND` 加一个换行（`NOT_FOUND\n`），
  标准输出为空。

**输入错误**（`load_lockfile` 抛 `InputError`）：

- 文件不可读、非法 UTF-8、损坏 JSON（含裸 `NaN`/`Infinity`/`-Infinity`）、
  悬空依赖，或任何不支持的结构（`lockfileVersion` 非整数 `3`、根节点缺失、
  条目非对象、版本缺失或非字符串、重名条目、嵌套安装路径、`link: true`、
  `dependencies` 非对象或键值非字符串等）；
- 退出码 `2`，标准错误仅为 `INPUT_ERROR` 加一个换行（`INPUT_ERROR\n`），
  标准输出为空，不输出部分结果或堆栈。

两类失败标准输出都为空。顺序上，`main` 先 `load_lockfile` 后 `find_path`：
**整份校验先于查询包名检查**。即使查询的起点、目标都不存在，或结构错误位于
根不可达条目上（`load_lockfile` 遍历并校验全部条目与全部 `dependencies`），
也先以 `INPUT_ERROR`（退出码 2）失败，而不是 `NOT_FOUND`。对应测试：
`tests/test_why_from.py` 的 `WhyFromInputErrors`
（`test_invalid_input_beats_missing_query_packages`、
`test_nested_path_and_link_still_rejected_with_from`、
`test_unreadable_file_is_input_error`）与
`tests/test_why_paths.py` 的 `UnsupportedStructuresStillRejected`；非法
UTF-8 另由 `tests/test_bad_utf8.py` 覆盖仓库根目录的 `bad-utf8.json`。

## 开销说明

一次 `why` 查询的成本不能笼统说成线性，它由"整份加载"与"带排序的图搜索"
两部分组成：

- **加载与整份校验（每次查询都先付一次）**：读取、UTF-8 解码与 JSON 解析的
  成本正比于文件字节数；结构遍历覆盖全部 `V` 个包条目与全部声明依赖边
  （合计 `O(V + E)`），与该条目是否参与本次查询、是否根可达无关。
- **起点排序**：省略 `--from` 时对根依赖做一次 `sorted(root_deps)`，
  `O(R log R)`（`R` 为根直接声明数）；`--from` 单起点免去这一项。
- **BFS 遍历本身**：借助 `collections.deque`，出队 O(1)，`parent` 表保证每个
  节点至多处理一次，节点与边的展开为 `O(V + E)`（以被访问到的分量为界）。
- **每层邻接排序（实际的排序成本）**：每个被取出的节点都要对其 `deps` 执行
  一次 `sorted()` 再入队，成本为对所有被访问节点的
  `Σ d_v · log d_v`（`d_v` 为该节点出度），而不是一次性的全局排序；这是
  字典序裁决的代价，使总时间上界高于纯线性。
- **路径重建**：仅在命中目标后沿 `parent` 回溯一次，`O(L)`（`L` 为路径
  长度，不超过 `O(V)`）；不可达时无重建。
- **辅助存储**：`parent` 表与队列各至多 O(V) 项；队列不携带路径副本；
  `sorted()` 只产生长度不超过最大出度的临时列表。命中后的路径数组 O(L)。

综合：单次查询时间为
`O(R log R) + O(V + E) + Σ_{被访问 v} O(d_v log d_v)`，辅助存储 `O(V)`；
外加正比于锁文件大小的一次性整份加载/校验成本，以及正比于输出路径长度的
JSON 序列化成本。这些规模性质由 `tests/test_why_scale.py` 以固定大图做结构
性锁定（无端到端计时阈值）：根直连 5000 个叶子的宽图（强制支付根依赖排序）、
2000 节点长链接 2000 个叶子（完整 2002 项路径）、不可达自环节点的空路径，
并在 40 张随机小图上与重构前的参考实现（队列元素携带逐节点复制的路径、
`list.pop(0)`，最坏 O(V²) 存储）逐包对照结果。

## 兼容性

本文档为新增说明，不改动任何产品代码与公开接口：`find_path`、
`load_lockfile`、`ROOT`、`InputError`、`NotFoundError` 的签名与行为，
`python -m depinventory why` / `why --from` 的命令行输出，README、SBOM.md
及全部样例锁文件（含 `sample-lock.json`）均保持不变。工具仍只读本地输入、
全程离线、不联网。
