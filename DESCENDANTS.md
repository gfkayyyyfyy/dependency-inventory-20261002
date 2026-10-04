# 全部下游查询（descendants）流程说明

本文说明 `python -m depinventory descendants <lockfile> <name>` 从**读取本地
锁文件到输出 JSON** 的完整数据流，并用文件名与函数名定位源码，便于核对一个
已安装包沿 `dependencies` 带入的直接与传递依赖：

- 参数解析与命令行输出在 `depinventory/__main__.py`（`_build_parser()`、
  `main()`）；
- 加载校验 `load_lockfile()`、目标匹配与依赖展开 `find_descendants()` 及
  `_traverse_related()` 都在 `depinventory/lockfile.py`；
- `find_descendants` 经 `depinventory/__init__.py` 导出，是公开接口。

行为约定与 README、LOCKFILE.md 一致；本文只解释现有行为，不改变任何产品
代码、公开接口（`depinventory/__init__.py` 的导出）、README、其他说明文档与
样例文件。工具只读本地输入文件，不安装或执行任何依赖，不联网，也不改写
输入；其余命令的行为保持不变。

## 总览：从命令行到 JSON 输出

一次 descendants 查询依次经过五个阶段：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `descendants`
   子命令，两个位置参数依次为锁文件路径 `lockfile` 与按区分大小写的完整包名
   查询的 `name`；该子命令没有 `--reachable`、`--from` 等选项。
   `main(argv=None)` 中 `argparse` 解析后得到 `args.command == "descendants"`、
   `args.lockfile`、`args.name`。
2. **本地加载与整份校验**：`main()` 调用 `lockfile.py` 的
   `load_lockfile(args.lockfile)`，成功则得到
   `root_deps, packages_map = (根依赖名列表, {包名: {"version", "deps"}})`；
   任何读取、解析或结构问题都在此处抛 `InputError`。
3. **目标匹配**：`main()` 把 `root_deps`、`packages_map`、`args.name` 原样
   传给公开函数 `find_descendants(root_deps, packages_map, target)`；函数先做
   唯一一次目标检查——`target not in packages_map` 即抛 `NotFoundError`。
4. **依赖展开**：`find_descendants()` 调用同文件的 `_traverse_related()`，以
   各包自身的 `dependencies`（`packages_map[name]["deps"]`）为正向邻接做
   广度优先展开，返回排好序的下游包名列表。
5. **JSON 输出**：`main()` 把列表与查询名包装成仅含 `name`、`descendants`
   两个字段的对象，用 `json.dumps(result, ensure_ascii=False)` 序列化，加一个
   换行写入标准输出并返回 0。

```text
命令行：python -m depinventory descendants sample-lock.json alpha
                                   │  __main__.py: _build_parser() 解析参数
                                   ▼
load_lockfile(path)                lockfile.py（读取 → JSON 解析 → 整份结构校验）
   返回 (root_deps, packages_map)  │  失败抛 InputError
                                   ▼
find_descendants(root_deps, packages_map, args.name)   lockfile.py（公开接口）
   ├─ 目标匹配：target 不在 packages_map → NotFoundError
   └─ _traverse_related(target, 正向邻接=dependencies) → 排序后的包名列表
                                   ▼
main() 包装：{"name": args.name, "descendants": 列表}
                                   ▼
stdout：完整 JSON + 一个换行；退出码 0；stderr 为空
```

## 加载数据如何进入公开 `find_descendants` 接口

`load_lockfile(path)`（`lockfile.py`）整份校验通过后返回两个数据结构，它们是
全部命令共用的唯一数据源：

- **`root_deps`**：字符串列表，取自锁文件根节点（`packages` 中空串 `""`
  条目）`dependencies` 的键，按声明顺序保留；根节点省略 `dependencies` 时为
  `[]`。
- **`packages_map`**：字典，键为 `_entry_name()` 从平铺安装路径
  `node_modules/name`、`node_modules/@scope/name` 识别出的完整包名，值为
  `{"version": 版本字符串, "deps": [依赖包名...]}`；`deps` 只保留依赖条目的
  **键（包名）**，`"*"`、`"^1.0.0"` 等版本范围值在校验后即丢弃。根项目不是
  其中条目。

`find_descendants` 在 `depinventory/__init__.py` 的 `__all__` 中导出，与
`load_lockfile` 同为公开接口，可直接以
`find_descendants(root_deps, packages_map, target)` 调用；命令行路径只是由
`main()` 先加载再把这两个返回值位置传入。注意 `root_deps` 在该函数内**不
参与展开**，仅为与 `find_parents`、`find_ancestors` 等查询保持一致的签名而
保留——下游永远从查询包自身的 `dependencies` 出发，因此根依赖省略或为空都
不改变结果，根项目也因本就不在 `packages_map` 中而不可能成为下游。

## 阶段一/二：加载与整份校验

加载规则与全部命令共用同一套输入协议，详见 LOCKFILE.md，此处只列与
descendants 相关的要点。`load_lockfile()` 依次：以 UTF-8 文本模式读文件
（`OSError`、`UnicodeDecodeError` 转为 `InputError`）；用
`json.loads(text, parse_constant=_reject_json_constant)` 解析（JSON 损坏或
裸 `NaN`/`Infinity`/`-Infinity` 转为 `InputError`）；校验顶层为对象、
`lockfileVersion` 为整数 3、`packages` 为含空串根节点的对象；`_entry_name()`
识别每个条目的平铺安装路径，拒绝嵌套路径、畸形作用域、重复条目、
`link: true` 与空/非字符串版本；最后 `_validate_dependencies()` 对根节点和
**每一个**包条目检查 `dependencies`（可省略；出现时须为对象、键值均为字符串、
声明的包必须已安装）。

**关系只取自 `dependencies` 这一个字段**：不解析版本范围，也不从
`devDependencies`、`peerDependencies`、`peerDependenciesMeta`、`optionalDependencies`
或任何其他字段补边。

关键时序：**整份校验先于目标检查**。`main()` 先在加载阶段的 `try/except`
中完整跑完 `load_lockfile()`，成功后才在另一个 `try/except` 中调用
`find_descendants()`。因此即使错误位于从根不可达、也与查询包无关的条目上，
加载照样失败，不可达条目的错误不能被跳过；同一命令上「输入无效」与「查询
目标缺失」同时成立时，结果是 `INPUT_ERROR` 而不是 `NOT_FOUND`。

## 阶段三：目标匹配

`find_descendants(root_deps, packages_map, target)`（`lockfile.py`）只做一次
成员判断：

```python
if target not in packages_map:
    raise NotFoundError(target)
```

匹配规则：

- 按**区分大小写的完整包名**精确匹配，`"LEAF"`、`"Alpha"` 与已安装的
  `leaf`、`alpha` 不是同一个包，一律 `NotFoundError`；
- 作用域包以 `@scope/name` **整体**作为一个包名匹配，不按斜杠拆分为作用域
  与名称两段；
- 字面值 `"$root"` 没有任何特殊含义。`ROOT = "$root"` 只是 `why` 输出中虚拟
  根的标记；descendants 不接受虚拟根，查询 `"$root"` 时按普通已安装包名在
  `packages_map` 中查找——只有锁文件里确实安装了名为 `$root` 的包才成功，
  其下游照常沿该包自身的 `dependencies` 展开；
- **起点不要求根可达**：目标只要已安装即可，是否被根节点（传递）声明一律
  不问。

## 阶段四：`_traverse_related()` 的依赖展开

`find_descendants()` 把正向邻接函数
`lambda name: packages_map[name]["deps"]` 传给
`_traverse_related(target, neighbors_of)`。展开规则由后者统一维护
（`find_ancestors()` 复用同一实现，只是传反向邻接）：

1. `seen = {target}`：查询包在遍历开始前就标记为已见。
2. `queue = deque(neighbors_of(target))`：队列直接由目标的直接依赖播种，
   因此结果中的每个成员都**至少经过一条边**——目标自身永远不算自己的下游，
   即使它经自环或循环再次到达，重新入队时也会因已在 `seen` 中被跳过。
3. 循环出队：已见过的节点跳过；新节点加入 `seen`、记入成员列表，并把它的
   `dependencies` 扩展进队列。
4. 返回 `sorted(members)`：最终顺序由 Python 对完整包名的字符串排序决定，
   即按 **Unicode 码点升序**，与邻接声明顺序、条目书写顺序、实际遍历入队
   顺序都无关。

由此得到的集合语义：

- **直接依赖与传递依赖都保留**：直接出现在目标 `dependencies` 中的包，与
  逐层展开后才能到达的包，进入同一个结果；
- **多条路径汇合不重复**：同一包无论是被直接声明还是经多条分支/循环到达，
  集合语义保证只出现一次；
- **自环与循环正常结束**：`seen` 保证每个节点至多扩展一次，队列必然排空，
  不会无限循环；循环中除目标自身外的可达成员照常保留；
- **孤立包返回空数组**：目标已安装但没有 `dependencies`（或其依赖闭包中除
  自身外没有别的包，例如仅有自环）时成功返回 `[]`，这不是错误。

遍历只读：不修改 `root_deps`、`packages_map` 及其中的 `deps` 列表。

## 阶段五：包装为 `name` / `descendants` 两字段 JSON

`main()` 中的成功分支为：

```python
elif args.command == "descendants":
    result = {
        "name": args.name,
        "descendants": find_descendants(root_deps, packages_map, args.name),
    }
...
sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
return 0
```

- 对象只有 `name`、`descendants` 两个键，键的书写顺序即上述插入顺序；
- `name` **原样回显命令行查询名**，不做大小写规范化，也不回查改写；即使
  查询名是 `@scope/pkg`、`$root` 这样的字面值也逐字符保留；
- `descendants` 就是 `_traverse_related()` 返回的字符串列表（可能为空）；
- `json.dumps(..., ensure_ascii=False)` 不转义非 ASCII 字符，标准输出末尾
  恰有一个换行；成功时标准错误为空，退出码为 0。

## 示例：sample-lock.json 的两次查询

`sample-lock.json` 的相关边为：根节点只声明 `alpha`；`alpha → beta`；
`beta → leaf`；`leaf → beta`（beta 与 leaf 构成二节点循环）；
`orphan → leaf`，但 `orphan` 不被根节点或任何已安装包引用（从根不可达）；
另有 `isolated` 无依赖。

在**项目根目录**执行：

```sh
$ python -m depinventory descendants sample-lock.json alpha
{"name": "alpha", "descendants": ["beta", "leaf"]}
$ python -m depinventory descendants sample-lock.json orphan
{"name": "orphan", "descendants": ["beta", "leaf"]}
```

两次都以退出码 0 结束，标准错误为空，标准输出为完整 JSON 加一个换行。

### 第一条：alpha

1. `seen = {"alpha"}`，队列播种 `alpha` 的直接依赖：`["beta"]`。
2. 出队 `beta`（未见过）→ 记入成员，扩展其依赖 `["leaf"]`。
3. 出队 `leaf`（未见过）→ 记入成员，扩展其依赖 `["beta"]`；`beta` 已在
   `seen`，跳过。
4. 队列排空，成员排序：`["beta", "leaf"]`。

### 第二条：orphan

1. `seen = {"orphan"}`，队列播种 `orphan` 的直接依赖：`["leaf"]`。
2. 出队 `leaf`（未见过）→ 记入成员，扩展 `["beta"]`。
3. 出队 `beta`（未见过）→ 记入成员，扩展 `["leaf"]`；`leaf` 已见过，跳过。
4. 队列排空，成员排序：`["beta", "leaf"]`。

### 结果为何相同，循环为何正常结束且各出现一次

两条路径遍历的是**同一个子图**：`beta ↔ leaf`。`alpha` 从循环的 `beta` 一侧
进入（alpha → beta → leaf），`orphan` 从 `leaf` 一侧进入
（orphan → leaf → beta）；展开取的是「沿 dependencies 至少一条边可达的全部
包」这一与进入方向无关的集合，且起点自身都不在该子图中，所以两者的下游集合
相同，排序后同为 `["beta", "leaf"]`。两次结果的 `name` 分别原样保留
`alpha` 与 `orphan`，故两个 JSON 对象并不完全相同。

`beta → leaf → beta` 的循环不会导致死循环或重复：`beta` 第一次出队时即进入
`seen`，随后 `leaf` 再次把 `beta` 放入队列，轮到它时因命中 `seen` 被直接
跳过；`leaf` 同理。每个节点至多扩展一次，beta 与 leaf 在结果中各出现一次。

还要注意 `orphan` 虽从根不可达，descendants 也不要求起点根可达——它沿自身
的 `dependencies` 一样展开到 leaf、beta；根只声明 `alpha` 这一事实对本查询
没有任何影响。

## 失败出口与流约定

失败时标准输出一律为空，不输出部分结果或堆栈：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 合法输入中目标未安装（含大小写不匹配、字面 `$root` 未安装） | `find_descendants()` 抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行（`NOT_FOUND\n`） |
| 文件不可读、非 UTF-8、JSON 损坏（含裸 `NaN`/`Infinity`/`-Infinity`）、违反既有结构规则（版本、安装路径、`link: true`、`dependencies` 类型或悬空依赖等） | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行（`INPUT_ERROR\n`） |

- 整份校验先于目标检查：输入无效时无论查询名是否已安装，都是退出码 2；
- 成功：退出码 0，标准错误为空，标准输出为完整 JSON 加恰好一个换行；
- `argparse` 自身的用法错误（缺参数、未知子命令等）沿用其默认行为，与上述
  查询语义无关，本文不改变它。

## 规则与现有测试的对应

下列规则均由现有测试固定，位置在 `tests/`：

| 规则 | 测试位置 |
| --- | --- |
| 本文两条验收命令的完整标准输出、退出码、空 stderr | `tests/test_descendants.py`：`DescendantsAcceptance.test_sample_alpha_descendants`、`test_sample_orphan_descendants` |
| 直接 + 传递依赖，按 Unicode 码点升序而非遍历顺序 | `tests/test_descendants.py`：`DescendantsApi.test_transitive_descendants_sorted_by_unicode_code_point` |
| 查询包经自环或循环再次到达也被排除；循环中其他成员保留 | `tests/test_descendants.py`：`test_cycle_members_are_kept_target_excluded`、`test_self_loop_does_not_add_target_itself`；`tests/test_descendants_combinatorial.py` 中每图每查询的 `assertNotIn(start, actual)` |
| 孤立/无依赖目标成功返回空数组 | `tests/test_descendants.py`：`test_no_dependencies_returns_empty`、`DescendantsCli.test_empty_descendants_object` |
| 起点根不可达仍沿自身依赖展开 | `tests/test_descendants.py`：`test_unreachable_start_still_expands_own_deps`（及验收用例 orphan） |
| 根依赖省略或为空不改变下游 | `tests/test_descendants.py`：`test_root_deps_absent_or_empty_does_not_change_result`；`tests/test_descendants_combinatorial.py` 对每张图分别用 `[]` 与 `["alpha"]` 两种根依赖核对 |
| 多条路径汇合不重复 | `tests/test_descendants_combinatorial.py`：`len(actual) == len(set(actual))`；菱形与分支图见 `test_expected_shapes_on_key_graphs` 的 `disconnected-diamond`、`cycle-and-branch` |
| 自环、循环、分支、断开关系的全量闭包核对 | `tests/test_descendants_combinatorial.py`：`DescendantsCombinatorial.test_all_edge_combinations`（9 条候选边的 512 种组合 × 两种根依赖 × 两种声明顺序 × 三个查询包，期望由独立的 `reference_descendants()` 不动点计算） |
| 结果与条目/依赖声明顺序无关 | `tests/test_descendants.py`：`test_declaration_order_swap_keeps_result_identical`；组合测试的 `swapped` 顺序 |
| 区分大小写的完整包名匹配 | `tests/test_descendants.py`：`test_case_sensitive_full_name_only`、`DescendantsCli.test_case_mismatch_not_found` |
| 字面值 `$root` 按普通已安装包处理 | `tests/test_descendants.py`：`test_literal_root_package_is_normal_package`、`DescendantsCli.test_literal_root_target_is_normal_package` |
| 目标缺失抛 `NotFoundError`；退出码 1、stderr 为 `NOT_FOUND\n`、stdout 为空 | `tests/test_descendants.py`：`test_missing_target_raises_not_found`、`DescendantsCli.test_missing_target_not_found` |
| 整份校验先于目标检查；悬空依赖优先于未安装目标报错 | `tests/test_descendants.py`：`DescendantsInputErrors.test_invalid_input_beats_missing_query_target` |
| 不可达条目的结构错误不能跳过（含 `dependencies: null`、嵌套路径、`link: true`、JSON 损坏、文件不可读、非法 UTF-8） | `tests/test_descendants.py`：`DescendantsInputErrors` 类其余用例（`test_structure_error_in_unreachable_entry_rejects_whole_file`、`test_nested_path_and_link_still_rejected`、`test_corrupt_json_is_input_error`、`test_unreadable_file_is_input_error`、`test_bad_utf8_is_input_error_even_for_missing_target`） |
| 输出仅含 `name`、`descendants` 两字段且 `name` 原样回显 | `tests/test_descendants.py`：`DescendantsCli.assertDescendantsObject`、`test_name_echoed_verbatim`、`test_multi_descendant_object` |
| 遍历不修改加载数据 | `tests/test_descendants.py`：`test_does_not_mutate_loaded_data`；组合测试每轮的 `packages_map`/`root_deps` 快照断言 |
| 内存数据约定与 `load_lockfile()` 返回逐字段一致 | `tests/test_descendants_combinatorial.py`：`DescendantsLockfileConvention` |
| 接入 descendants 后其余命令保持不变 | `tests/test_descendants.py`：`ExistingCommandsUnchanged`（`why`、`ancestors` 冒烟） |

## 不变量

- 下游只沿各包自身的 `dependencies` 展开：不解析版本范围，不从其他字段补边；
  起点不要求根可达，根依赖省略或为空不改变其下游。
- 下游是「经过至少一条边可达的其他已安装包」集合：查询包自身（即使经自环或
  循环再次到达）与根项目始终排除；孤立包返回空数组；多路径汇合无重复；结果
  按完整包名的 Unicode 码点升序排列，而非遍历顺序。
- 完整包名区分大小写，作用域名整体匹配，字面值 `$root` 表示普通已安装包。
- 整份加载校验先于目标检查；目标缺失为退出码 1 + `NOT_FOUND\n`，加载失败为
  退出码 2 + `INPUT_ERROR\n`，失败时标准输出为空；成功为退出码 0、标准错误
  为空、标准输出为完整 JSON 加一个换行。
- 工具只读本地输入文件，不联网、不安装或执行依赖、不改写输入；其余命令行为
  保持不变。
- 本文档不改动产品代码、公开接口、README、LOCKFILE.md、WHY.md、SBOM.md、
  DIFF.md 及任何样例锁文件。
