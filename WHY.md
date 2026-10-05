# 依赖来源查询（why）流程说明

本文说明 `python -m depinventory why <lockfile> <name> [--from 包名]` 从公开
命令输入到 JSON 字节输出的完整数据流，并用文件名与函数名定位源码。路径遍历
与 SBOM 的 `--with-paths` 导出共用同一份 BFS 实现，本文同时标出这一共用入口。
行为约定与 README 一致；本文只做解释，不改变任何产品代码、公开接口、README、
SBOM.md 或样例文件。工具只读本地输入文件，不联网，也不写回锁文件。

## 总览：从命令行到 JSON 输出

一次 why 查询依次经过五个阶段，全部在 `depinventory/__main__.py` 的 `main()`
与 `depinventory/lockfile.py` 中完成：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `why` 子命令，位置
   参数为锁文件路径与目标包名，`--from` 经 `dest="source"` 存入 `args.source`
   （省略时为 `None`）。
2. **本地文件读取与整份校验**：`main()` 调用 `lockfile.py` 的
   `load_lockfile(path)`，返回 `(root_deps, packages_map)` 两个数据结构。
3. **起点与目标匹配**：`main()` 把 `root_deps`、`packages_map`、`args.name`、
   `args.source` 传给 `lockfile.py` 的 `find_path()`，由它检查目标与起点是否
   已安装、确定 BFS 的起点集合，并在重建出的路径前补根标记。
4. **路径选择（共用遍历）**：`find_path()` 调用同文件的 `_shortest_path()`；
   后者调用共用遍历 `_bfs_parents()` 取前驱表，再调用 `_reconstruct_path()`
   回溯成路径。SBOM 的 `_build_root_paths()` 也调用同一个 `_bfs_parents()`。
5. **UTF-8 编码与字节写出**：`main()` 把结果组装为
   `{"name": args.name, "path": path}`，先经 `__main__.py` 的
   `_encode_result()` 在内存中完成 JSON 序列化与整份 UTF-8 编码，再通过
   `sys.stdout.buffer.write(payload)` 一次写出，返回 0。

## 两个核心数据结构

`load_lockfile()` 的返回值是后续所有查询的输入：

- **根依赖 `root_deps`**：字符串列表，取自锁文件根节点（`packages` 中空串
  `""` 条目）的 `dependencies` 的键，按声明顺序保留。它表示根项目直接声明
  依赖了哪些包，是默认查询（不带 `--from`）的起点集合来源。
- **包映射 `packages_map`**：字典，键为完整包名（如 `alpha`、
  `@scope/name`），值为 `{"version": 版本字符串, "deps": [依赖包名...]}`。
  每个包条目来自 `packages` 中 `node_modules/name` 形式的平铺安装路径
  （`_entry_name()` 负责从路径键取包名，作用域包 `node_modules/@scope/name`
  整体成为一个包名），`deps` 取自该条目的 `dependencies` 的键；版本字符串
  保留在映射里但不参与图遍历。

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
   object_pairs_hook=_reject_duplicate_keys)` 一次解析，两个钩子都在解析
   阶段生效：
   - 裸 `NaN`/`Infinity`/`-Infinity` 由 `_reject_json_constant()` 拒绝；
   - `_reject_duplicate_keys()` 对解析出的每个 JSON 对象回调，同一对象内
     出现两个按 JSON 字符串解码后完全相同的键即抛 `ValueError`（即使两个值
     相同）。覆盖顶层、`packages`、根条目、各包条目、`dependencies`、额外
     元数据以及数组内的对象；根不可达包内的重复键同样使整份输入失败，重复
     只在同一对象内判断。键按解码后文本区分大小写比较，不做 Unicode 规范化。
   - JSON 语法损坏与上述钩子抛出的 `ValueError` 都在此统一转为 `InputError`。
3. 结构校验：根必须是对象、`lockfileVersion` 必须为整数 3、`packages` 必须
   是含空串根节点的对象；`_entry_name()` 把每个条目键解释为平铺安装路径，
   不支持的键（嵌套路径、空名、畸形作用域）直接拒绝；`link: true`、空版本、
   重复安装条目也拒绝。
4. `_validate_dependencies()` 对根节点和**每个**包条目检查
   `dependencies`：可省略，出现时必须是对象、键值均为字符串，且声明的包必须
   已安装（悬空依赖拒绝）。

关键时序：**整份校验先于任何查询包名检查**。`main()` 先完整跑完
`load_lockfile()`，成功后才调用 `find_path()`；因此即使结构错误（含重复
对象键）位于根节点不可达的条目上，输入也整体失败，不会先报包不存在。

## 阶段二：起点与目标匹配

`find_path(root_deps, packages_map, target, source=None)`（`lockfile.py`）：

- `target` 不在 `packages_map` 中 → 抛 `NotFoundError(target)`。
- `source` 为 `None`（省略 `--from`）：起点集合为 `sorted(root_deps)`，
  即从虚拟根 `$root` 查起。虚拟根不是图节点、不入队、不参与 BFS；BFS 在
  纯安装包图上完成、`_reconstruct_path()` 重建出路径**之后**，才由
  `find_path()` 在路径前补上常量 `ROOT`（值为 `"$root"`）作为首元素。
- `source` 给定：先检查 `source` 是否已安装，未安装抛
  `NotFoundError(source)`；已安装则以 `[source]` 为**唯一**起点（单元素无需
  排序），路径从 `source` 开始，不加 `$root` 标记。起点是否能从根节点到达
  不作要求，一律沿其自身 `dependencies` 查询。

包名匹配规则：起点与目标都按**区分大小写的完整包名**匹配；作用域包以
`@scope/name` 整体作为一个包名匹配、在路径中也是单个元素，不拆分。
`--from` 的字面值没有任何特殊含义——`--from '$root'` 是按普通安装包名
`$root` 查找；该包未安装时同样 `NOT_FOUND`（退出码 1），不是虚拟根的别名。

## 阶段三：共用遍历与路径选择

三个函数分层协作，全部在 `lockfile.py`：

### `_bfs_parents(starts, packages_map, target=None)`

唯一维护遍历与裁决规则的 BFS，单包查询与 SBOM 路径导出共用：

- **起点**：按给定的 `starts` 顺序播种（调用方负责排序：默认查询传
  `sorted(root_deps)`，`--from` 传单元素列表）。每个起点写入
  `parent[起点] = None` 并入队；播种时即去重，已在表中的起点不重复入队。
- **前驱**：`parent[node]` 记录节点 `node` **首次被发现（首次入队）**时的
  前驱；之后再经其他路径遇到 `node` 直接跳过，前驱永不改写。
- **扩展**：节点出队后，对其 `packages_map[node]["deps"]` 做
  `sorted(deps)`，按完整包名 Unicode 码点升序逐个检查，未在 `parent` 中的
  邻接才记录前驱并入队。只沿 `dependencies` 连边，不解析版本范围。
- **停止位置**：每次节点**出队**时先判断 `node == target`，命中即
  `break`——该判断位于邻接扩展之前，因此目标出队后它自身的邻接（含指向
  前驱的循环回边、自环）在本次查询中**不再扩展**，不能把停止后的扩展写成
  已经发生的步骤。`target=None` 时不提前停止，遍历全部可达节点（SBOM 一次
  算出所有包的前驱）。
- **终止性**：每个节点至多入队一次，自环与循环边的终点发现时往往已在
  `parent` 中，故不会重复入队，队列必然排空（或在目标出队时提前停止）。

边数优先与等长裁决都由此实现：BFS 按层扩展保证**边数最少**；起点同层按
字典序播种、每个节点的邻接 `sorted(deps)` 后扩展，使等长路径中**包名序列
的 Unicode 码点字典序最小**者首次发现目标并写死前驱。结果只取决于图关系，
与锁文件条目书写顺序、`dependencies` 键的声明顺序无关。

### `_reconstruct_path(parent, target)`

目标必须在 `parent` 中。自 `target` 出发，沿 `parent` 链逐个前置回溯到前驱
为 `None` 的起点，再 `reverse()` 一次，得到「起点 → 目标」的包名列表；只
回溯一次，长度等于路径边数。

### `_shortest_path(starts, packages_map, target)`

单包查询的薄封装：先调 `_bfs_parents(starts, packages_map, target)` 取前驱
表（目标出队即停止，不必遍历全图）；`target` 不在表中（起点集合无法到达
它）返回 `[]`；否则返回 `_reconstruct_path(parent, target)`。

### 边界结果

- 目标已安装但起点（集合）无法到达它 → `_shortest_path()` 返回空路径 `[]`，
  `find_path()` 原样返回 `[]`（成功，退出 0），默认查询不在前面补根标记。
- 起点等于目标：`--from x` 查 `x` 时 `x` 作为起点播种（前驱为 `None`），
  首次出队即命中、不扩展邻接，重建为 `["x"]`，不要求存在自环。默认查询中
  目标本身就是根直接依赖时同理，结果为 `["$root", 目标]`，即使它在
  `sorted(root_deps)` 中排在最后（播种时前驱已固定为 `None`，经其他根依赖
  的再发现不会改写）。

## 与 `sbom --with-paths` 路径一致的原因

`sbom_document(..., with_paths=True)` 通过 `_build_root_paths(root_deps,
packages_map)` 取路径，后者就是以 `sorted(root_deps)` 为起点、
`target=None` 调用同一个 `_bfs_parents()` 做一次全量遍历，再对表中每个包
调用同一个 `_reconstruct_path()` 并前置 `[ROOT]`；根不可达的包不在前驱表
中，由 `sbom_document()` 以 `paths.get(name, [])` 补成 `[]`。播种顺序、
首次前驱、边数优先与等长字典序裁决、重建方式与根标记位置与省略 `--from`
的 why 完全相同，因此每个可达组件的 `path` 与默认 why 结果逐条一致
（`tests/test_shared_path_rule.py` 同时锁定两个入口）。差别只在停止策略：
why 对单个目标出队即停，SBOM 一次遍历算出全部包。

## 阶段四：UTF-8 编码、字节写出与失败出口

成功时 `main()` 先调用 `_encode_result(result)`（`__main__.py`）：

```python
(json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
```

即**先在内存中完成 JSON 序列化并整份编码为 UTF-8 字节，再写出**：
`json.dumps(..., ensure_ascii=False)` 产生文本并追加恰好一个换行，
`.encode("utf-8")` 严格编码，成功后才执行 `sys.stdout.buffer.write(payload)`
与 `flush()`。因此成功时：标准输出是仅含 `name`、`path` 两个键的单个 JSON
对象（`name` 为查询名原样字符串，`path` 为路径数组），末尾恰有一个换行，
标准错误为空，退出码 0；全程只读，不写回锁文件。

孤立代理码点（如 JSON 转义 `"2.0-\ud83f"` 解码出的 U+D83F）文件字节仍是
合法 UTF-8、解析也接受，Python 字符串可以持有，但无法再编码为严格
UTF-8：**当且仅当实际结果文本含此类码点时**，`_encode_result()` 抛
`UnicodeEncodeError`，`main()` 捕获后按输入错误处理。由于整份字节在编码
成功后才触碰标准输出，失败时不会吐出部分 JSON。why 的结果只含包名与路径、
不含版本与其他元数据，所以未输出的版本或元数据含孤立代理码点不影响 why：
仓库夹具 `surrogate-lock.json` 中 `beta` 的版本为 `"2.0-\ud83f"`，
`python -m depinventory why surrogate-lock.json beta` 仍退出 0，输出
`{"name": "beta", "path": ["$root", "alpha", "beta"]}`；而输出版本的
`list`、`sbom` 在同一夹具上退出 2。

所有失败出口标准输出均为空，不输出部分结果或堆栈，标准错误恰为下表文本加
一个换行：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 起点或目标未安装（含 `--from '$root'` 而无此包） | `find_path()` 抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行 |
| 文件不可读、非法 UTF-8、JSON 损坏（含重复对象键、裸常量）、悬空依赖、不支持的结构 | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行 |
| 结果文本含孤立代理码点，UTF-8 编码失败 | `_encode_result()` 抛 `UnicodeEncodeError`，`main()` 捕获 | 2 | `INPUT_ERROR` 加换行 |

`main()` 中加载阶段的 `try/except` 先于查询阶段，故同一命令上输入无效与包
名不存在同时成立时报 `INPUT_ERROR`（整份校验优先）；查询阶段另有捕获一切
未预期异常的兜底，同样只输出 `INPUT_ERROR`、退出 2。

## 示例：sample-lock.json 的两次查询

`sample-lock.json` 中：根节点仅声明 `alpha`；`alpha` 依赖 `beta`；`beta`
依赖 `leaf`，`leaf` 又依赖 `beta`（二者构成循环）；`orphan` 依赖 `leaf`
但不被根节点引用（根不可达）；`isolated` 无任何依赖。

```sh
$ python -m depinventory why sample-lock.json leaf
{"name": "leaf", "path": ["$root", "alpha", "beta", "leaf"]}
$ python -m depinventory why sample-lock.json leaf --from orphan
{"name": "leaf", "path": ["orphan", "leaf"]}
```

两条命令都以退出码 0 结束，标准错误为空，输出末尾恰有一个换行。

- **第一条（默认起点）**：起点集合为 `sorted(root_deps)` 即 `["alpha"]`，
  `parent["alpha"] = None`。`alpha` 出队（非目标）后按序扩展，`beta`
  首次入队，`parent["beta"] = "alpha"`；`beta` 出队后 `leaf` 首次入队，
  `parent["leaf"] = "beta"`；`leaf` **出队时即命中目标并停止**，停止位于
  邻接扩展之前，所以 `leaf → beta` 这条循环回边在本次查询中不会被扩展。
  `_reconstruct_path()` 自 `leaf` 回溯得 `["alpha", "beta", "leaf"]`，
  `find_path()` 在重建之后补上根标记，得到
  `["$root", "alpha", "beta", "leaf"]`。
- **第二条（`--from orphan`）**：`orphan` 已安装（根不可达不影响），成为
  唯一起点且前驱为 `None`；`orphan` 出队扩展后 `leaf` 首次入队，
  `parent["leaf"] = "orphan"`；`leaf` 出队即停，重建为
  `["orphan", "leaf"]`，不含 `$root` 标记。

两次查询路径不同，正是因为起点与前驱不同：默认查询从根声明出发，必须穿过
`alpha`、`beta`；`--from` 查询从指定包出发，只走它自己的依赖边。循环不致死
循环的原因是 `parent` 表让每个节点只入队一次——即便遍历确实经过回边
（例如目标是其他包、或 SBOM 的全量遍历），回边终点也早已在表中。

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
  `EXPECTED_LEAF_PATH`）。作用域包以完整名参与码点比较（`"@"` 为
  U+0040，先于小写字母），`tests/test_shared_path_rule.py` 中
  `$root/@scope/pkg/beta/leaf` 据此在等长路径中胜出。
- 声明顺序不影响结果：`test_declaration_order_swap_keeps_result_identical`
  递归交换所有对象的键书写顺序后，接口与命令行结果保持不变。

## 开销说明

单次 why 查询（不含加载与校验）的成本来自几处明确的位置，不能笼统称为线性：

- `find_path()` 默认查询对 `root_deps` 排序一次：O(k log k)，k 为根直接
  依赖数；`--from` 单起点无需排序。
- `_bfs_parents()` 的队列主体为 O(V + E)（V 为已安装包数，E 为依赖边
  数），但**每个被扩展的节点出队时对其 `deps` 排序**，排序总量为
  O(Σ dᵢ log dᵢ)（dᵢ 为各包直接依赖数），是查询的主要额外开销。给定
  target 时目标出队即停，只处理停止前出队的节点与邻接，不必遍历全图；
  `target=None`（SBOM 路径导出）则遍历全部根可达节点一次。
- 命中后的路径回溯与反转各为 O(路径长度)，总量不超过 O(V)。
- 辅助存储 O(V)：`parent` 表与队列每节点至多一份，不携带路径副本。

加载阶段 `load_lockfile()` 的读取、JSON 解析与整份校验随文件大小线性增长，
与查询相互独立。

## 不变量

- 工具只读本地输入文件，不联网、不改写任何输入（含查询失败与成功两种路径）。
- 本文档不改动产品代码、公开接口（`depinventory/__init__.py` 的导出）、
  README、SBOM.md 及任何样例锁文件，全部命令的既有行为保持不变。
