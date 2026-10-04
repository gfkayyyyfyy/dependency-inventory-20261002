# 全部上游查询（ancestors）流程说明

本文说明 `python -m depinventory ancestors <lockfile> <name> [--reachable]`
从**读取本地锁文件到输出 JSON** 的完整数据流，并用文件名与函数名定位源码：

- 参数解析、`--reachable` 筛选与命令行输出在 `depinventory/__main__.py`
  （`_build_parser()`、`main()`）；
- 加载校验 `load_lockfile()`、目标匹配与上游计算 `find_ancestors()`、
  `_traverse_related()`，以及可达集合 `reachable_names()`，都在
  `depinventory/lockfile.py`；
- `find_ancestors` 经 `depinventory/__init__.py` 导出，是公开接口；
  `--reachable` 的筛选只发生在 `main()` 中，公开函数的输入输出不变。

行为约定与 README、LOCKFILE.md 一致；本文只解释现有行为，不改变任何产品
代码、公开接口（`depinventory/__init__.py` 的导出）、README、其他说明文档与
样例文件。工具只读本地输入文件，不安装或执行任何依赖，不联网，也不改写
输入；其余命令的行为保持不变。

## 总览：从命令行到 JSON 输出

一次 ancestors 查询严格按以下顺序经过五个阶段——**整份校验 → 目标存在性判断
→ 上游计算 →（可选）根可达筛选 → JSON 输出**，前一阶段失败就不会进入后一
阶段：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `ancestors`
   子命令，两个位置参数依次为锁文件路径 `lockfile` 与按区分大小写的完整包名
   查询的 `name`，另有 `store_true` 的 `--reachable` 开关（省略时
   `args.reachable` 为 `False`）。`main(argv=None)` 经 `argparse` 解析后
   得到 `args.command == "ancestors"`、`args.lockfile`、`args.name`。
2. **本地加载与整份校验**：`main()` 调用 `lockfile.py` 的
   `load_lockfile(args.lockfile)`，成功则得到
   `root_deps, packages_map = (根依赖名列表, {包名: {"version", "deps"}})`；
   任何读取、解析或结构问题都在此处抛 `InputError`，命令以退出码 2 结束。
3. **目标匹配与上游计算**：`main()` 把 `root_deps`、`packages_map`、
   `args.name` 原样传给公开函数
   `find_ancestors(root_deps, packages_map, target)`；函数先做唯一一次
   目标检查——`target not in packages_map` 即抛 `NotFoundError`（退出码
   1），通过后再遍历**整份清单**算出全部上游。
4. **根可达筛选（仅在带 `--reachable` 时）**：`main()` 调用
   `reachable_names(root_deps, packages_map)` 取根可达集合，只对上一步返回
   的每个上游成员做成员判断；`args.name` 不参与筛选，原样保留。省略该选项时
   本阶段整体跳过，`find_ancestors()` 的结果不经任何改动。
5. **JSON 输出**：`main()` 把查询名与上游列表包装成仅含 `name`、
   `ancestors` 两个字段的对象，用 `json.dumps(result, ensure_ascii=False)`
   序列化，加一个换行写入标准输出并返回 0。

```text
命令行：python -m depinventory ancestors sample-lock.json leaf [--reachable]
                                   │  __main__.py: _build_parser() 解析参数
                                   ▼
load_lockfile(path)                lockfile.py（读取 → JSON 解析 → 整份结构校验）
   返回 (root_deps, packages_map)  │  失败抛 InputError → 退出码 2
                                   ▼
find_ancestors(root_deps, packages_map, args.name)   lockfile.py（公开接口）
   ├─ 目标匹配：target 不在 packages_map → NotFoundError → 退出码 1
   └─ 扫描全部已安装包构建反向邻接，_traverse_related() 沿反向边 BFS
                                   ▼
--reachable？─ 否 ───────────────► 上游列表原样使用
            └ 是：reachable_names() 求根可达集合，逐个筛选上游成员
                                   ▼
main() 包装：{"name": args.name, "ancestors": 列表}
                                   ▼
stdout：完整 JSON + 一个换行；退出码 0；stderr 为空
```

## 加载数据如何进入查询

`load_lockfile(path)`（`lockfile.py`）整份校验通过后返回两个数据结构，它们是
全部命令共用的唯一数据源：

- **`root_deps`**：字符串列表，取自锁文件根节点（`packages` 中空串 `""`
  条目）`dependencies` 的键，按声明顺序保留；根节点省略 `dependencies` 时为
  `[]`。ancestors 计算本身不使用它，它只在带 `--reachable` 时作为可达性
  遍历的起点。
- **`packages_map`**：字典，键为 `_entry_name()` 从平铺安装路径
  `node_modules/name`、`node_modules/@scope/name` 识别出的完整包名，值为
  `{"version": 版本字符串, "deps": [依赖包名...]}`；`deps` 只保留依赖条目的
  **键（包名）**，`"*"`、`"^1.0.0"` 等版本范围值在校验后即丢弃。根项目不是
  其中条目。

**关系只取自 `dependencies` 这一个字段**：不解析版本范围，也不从
`devDependencies`、`peerDependencies`、`optionalDependencies` 或任何其他
字段补边。支持范围沿用 README 与 LOCKFILE.md 的约定：UTF-8 严格 JSON、
`lockfileVersion` 为整数 3、平铺的 `node_modules/name` 与
`node_modules/@scope/name` 安装路径、版本为非空字符串；嵌套安装路径与
`link: true` 条目不支持。

## 阶段一/二：整份校验先于目标存在性判断

加载规则与全部命令共用同一套输入协议，详见 LOCKFILE.md，此处只列与
ancestors 相关的要点。`load_lockfile()` 依次：以 UTF-8 文本模式读文件
（`OSError`、`UnicodeDecodeError` 转为 `InputError`）；用
`json.loads(text, parse_constant=_reject_json_constant)` 解析（JSON 损坏或
裸 `NaN`/`Infinity`/`-Infinity` 转为 `InputError`）；校验顶层为对象、
`lockfileVersion` 为整数 3、`packages` 为含空串根节点的对象；`_entry_name()`
识别每个条目的平铺安装路径，拒绝嵌套路径、畸形作用域、重复条目、
`link: true` 与空/非字符串版本；最后 `_validate_dependencies()` 对根节点和
**每一个**包条目检查 `dependencies`（可省略；出现时须为对象、键值均为字符串、
声明的包必须已安装）。

关键时序：**输入错误先于目标存在性判断**。`main()` 先在加载阶段的
`try/except` 中完整跑完 `load_lockfile()`，成功后才在另一个 `try/except`
中调用 `find_ancestors()`，而目标是否安装的检查
（`if target not in packages_map`）位于 `find_ancestors()` 的开头。因此：

- **不可达包的缺失版本或悬空依赖也属于整份输入错误**。校验覆盖 `packages`
  中的每个条目与其每条 `dependencies` 边，不看该条目是否能从根到达；即使
  查询名本身根本未安装，结果也是 `INPUT_ERROR`（退出码 2），不会降级为
  `NOT_FOUND`。
- 只有在整份输入合法时，未安装的查询目标才得到 `NOT_FOUND`（退出码 1）。

## 阶段三：`find_ancestors()` 为何查询全部已安装包

`find_ancestors(root_deps, packages_map, target)`（`lockfile.py`）通过目标
检查后，做两件事：

```python
reverse = {}
for name, info in packages_map.items():
    for dep in info["deps"]:
        reverse.setdefault(dep, []).append(name)
return _traverse_related(target, lambda name: reverse.get(name, ()))
```

1. **遍历 `packages_map` 中的全部已安装包**，为每条 dependencies 边
   `name → dep` 建立反向邻接 `dep → [name]`：反向邻接表回答的是「哪些已安装
   包声明了我」。加载时的整份校验已保证每个 `dep` 都是已安装包名，所以反向
   邻居一定是 `packages_map` 中存在的键。
2. 调用与 descendants 共用的 `_traverse_related(target, neighbors_of)`，把
   反向邻接函数 `lambda name: reverse.get(name, ())` 传入，自目标沿反向边
   广度优先遍历。沿反向边访问到的每个包，沿正向 `dependencies` 都有一条至少
   一条边的路径到达目标，这正是「上游（祖先）」的定义。

**为什么必须扫描全部已安装包，而不能只看根可达的包**：ancestors 的语义是
「沿自身 `dependencies` 经过至少一条边（直接与传递展开，不限直接边）能到达
目标的全部已安装包」，资格只取决于声明关系，与该包是否挂在根依赖链上无关。
锁文件里完全可能存在已安装、却不被根节点（传递）引用的包；这样的包只要声明
了目标或声明了能到达目标的包，就是目标的合法上游。若构建反向邻接时预先按根
可达性裁剪，就会漏掉这些上游——这也正是不带 `--reachable` 与带
`--reachable` 两次查询的区别所在（见下文示例）。根可达性是**输出阶段的筛选
条件**，不是上游关系本身的一部分。

`root_deps` 在该函数内不参与计算，仅为与 `find_parents()`、
`find_descendants()` 等查询保持一致的签名而保留；根项目本就不是
`packages_map` 的条目，天然不可能成为祖先。函数不修改 `root_deps`、
`packages_map` 及其中的 `deps` 列表。

### `_traverse_related()` 的遍历规则

反向遍历的去重、循环终止与排除规则由 `_traverse_related()` 统一维护：

1. `seen = {target}`：查询包在遍历开始前就标记为已见。
2. `queue = deque(neighbors_of(target))`：队列直接由目标的**直接反向邻居**
   （直接声明目标的包）播种，因此结果中的每个成员都与目标间隔至少一条边；
   直接上游与逐层反向展开得到的传递上游进入同一个结果。
3. 循环出队：已见过的节点跳过；新节点加入 `seen`、记入成员列表，再把它的
   反向邻居扩展进队列。
4. 返回 `sorted(members)`：最终顺序由 Python 对完整包名的字符串排序决定，
   即按 **Unicode 码点升序**排列并去重，与条目书写顺序、依赖声明顺序、实际
   遍历入队顺序都无关。作用域包以 `@scope/name` 整体参与排序（`@` 为
   U+0040，排在小写字母前）。

由此固定的边界语义：

- **根项目始终排除**：它不在 `packages_map` 中，反向邻接里不会出现它。
- **目标自身始终排除**：即使目标经自环或循环能沿 dependencies 回到自身
  （例如 `leaf → beta → leaf`），遍历时目标已预置在 `seen` 中，重新入队即
  被跳过；循环中的其他成员照常保留。
- **多条路径汇合不重复**：同一包经多条分支或循环到达只出现一次。
- **没有其他上游的目标成功返回 `[]`**：只被根节点声明、仅有自环、或完全
  孤立的已安装包都属于这种情况，这不是错误。
- 匹配按**区分大小写的完整包名**进行；字面值 `"$root"` 没有特殊含义，按
  普通已安装包名查找（`ROOT = "$root"` 只是 `why` 输出里虚拟根的标记）。

## 阶段四：`--reachable` 只筛选上游成员

带 `--reachable` 时，`main()` 在拿到 `find_ancestors()` 的完整上游列表后
执行：

```python
keep = reachable_names(root_deps, packages_map)
ancestors = [name for name in ancestors if name in keep]
```

`reachable_names()` 自根节点 `dependencies` 出发做正向 BFS，返回沿
`dependencies` 可达的包名集合（可达性规则与 `list --reachable` 相同：不解析
版本范围，不从其他字段补边；`seen` 集合保证自环与循环正常结束，与根断开的
包整体排除）。在此基础上：

- **筛选只检查每个上游成员是否在根可达集合中**，不重新计算上游关系，也不
  改变排序与去重；一个保留成员同时满足「沿自身 `dependencies` 至少一条边
  到达目标」与「自根节点沿 `dependencies` 可达」。
- **查询名原样保留，不受筛选影响**：输出的 `name` 始终是 `args.name`
  逐字符回显，既不做大小写规范化，也不要求目标本身根可达。
- **已安装但根不可达的目标**带选项时仍成功：它的上游若也都不可达，结果就是
  空 `ancestors`；目标自身可达与否从不单独判断。
- **根 `dependencies` 省略或为空**时，根可达集合为空，任何上游都通不过
  筛选，成功返回空 `ancestors`。
- 筛选不放宽整份校验：`reachable_names()` 在加载成功之后才运行，不可达包的
  结构错误此前已令整份输入失败。

省略 `--reachable` 时不调用 `reachable_names()`，`find_ancestors()` 的结果
（含根不可达的上游）原样进入输出。

## 阶段五：包装为 `name` / `ancestors` 两字段 JSON

`main()` 中的成功分支为：

```python
ancestors = find_ancestors(root_deps, packages_map, args.name)
if args.reachable:
    keep = reachable_names(root_deps, packages_map)
    ancestors = [name for name in ancestors if name in keep]
result = {"name": args.name, "ancestors": ancestors}
...
sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
return 0
```

- 对象只有 `name`、`ancestors` 两个键，键的书写顺序即上述插入顺序；
- `name` 原样回显命令行查询名（即使是 `@scope/pkg`、`$root` 这样的字面值）；
- `ancestors` 是排好序、去重后的字符串列表（可能为空）；
- `json.dumps(..., ensure_ascii=False)` 不转义非 ASCII 字符，标准输出末尾
  恰有一个换行；成功时标准错误为空，退出码为 0。

## 示例：sample-lock.json 的两次查询

`sample-lock.json` 的依赖边为：根节点只声明 `alpha`；`alpha → beta`；
`beta → leaf`；`leaf → beta`（beta 与 leaf 构成二节点循环）；
`orphan → leaf`，但 `orphan` 不被根节点或任何已安装包引用（从根不可达）；
另有 `isolated` 已安装但无任何依赖。

在**项目根目录**执行：

```sh
$ python -m depinventory ancestors sample-lock.json leaf
{"name": "leaf", "ancestors": ["alpha", "beta", "orphan"]}
$ python -m depinventory ancestors sample-lock.json leaf --reachable
{"name": "leaf", "ancestors": ["alpha", "beta"]}
```

两次都以退出码 0 结束，标准错误为空，标准输出为完整 JSON 加一个换行。

### 不带选项：根不可达的 orphan 也是上游

1. 整份校验通过后，`find_ancestors()` 扫描**全部五个已安装包**构建反向邻接：
   `leaf ← [beta, orphan]`、`beta ← [alpha, leaf]`、`alpha ← []`
   （alpha 只被根节点声明，根项目不在 `packages_map` 中）、
   `orphan ← []`、`isolated ← []`。
2. `seen = {"leaf"}`，队列播种 leaf 的直接反向邻居 `beta`、`orphan`。
3. 出队 `beta` → 新成员，扩展其反向邻居 `alpha`、`leaf`：`alpha` 入队；
   **`leaf` 已在 `seen` 中被跳过——beta↔leaf 循环不会把目标自身计入结果**。
4. 出队 `orphan` → 新成员，它没有反向邻居。
5. 出队 `alpha` → 新成员；根项目不是已安装包，没有反向邻居可扩展。
6. 成员为 beta、orphan、alpha，按完整包名 Unicode 码点升序排列为
   `["alpha", "beta", "orphan"]`。

`orphan` 虽与根依赖链完全断开，但它直接声明了 `leaf`：上游资格只看
dependencies 声明关系，所以不带选项时它必须出现。`leaf` 自己声明 `beta`、
客观上沿 `leaf → beta → leaf` 能经两条边回到自身，但「目标自身始终排除」，
故结果里没有 `leaf`。

### 追加 `--reachable`：orphan 被筛掉，leaf 自身仍排除

1. `reachable_names()` 自根依赖 `["alpha"]` 正向遍历：`alpha → beta →
   leaf`，leaf 再声明的 `beta` 已见过，循环正常结束；根可达集合为
   `{alpha, beta, leaf}`。`orphan`、`isolated` 不在其中。
2. 对完整上游 `["alpha", "beta", "orphan"]` 逐名做成员判断：`alpha`、
   `beta` 保留；**`orphan` 根不可达，被筛掉**。
3. 查询名 `leaf` 不参与筛选，输出仍为 `"name": "leaf"`；而 `leaf` 本就不在
   上游列表中（目标自身排除先于筛选），根可达集合包含它也不会让它进入
   `ancestors`。
4. 结果为 `["alpha", "beta"]`。

两次输出的差异只来自阶段四的成员筛选：根依赖链
`$root → alpha → beta → leaf` 上的 alpha、beta 留下；脱离该链却同样指向
leaf 的 orphan 落选。

## 失败出口与流约定

失败时标准输出一律为空，不输出部分结果或堆栈：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 合法输入中目标未安装（含大小写不匹配、字面 `$root` 未安装）；带不带 `--reachable` 相同 | `find_ancestors()` 抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行（`NOT_FOUND\n`） |
| 文件不可读、非 UTF-8、JSON 损坏（含裸 `NaN`/`Infinity`/`-Infinity`）、违反既有结构规则（缺失/空版本、安装路径、`link: true`、`dependencies` 类型或悬空依赖等），**包括位于根不可达条目上的同类错误** | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行（`INPUT_ERROR\n`） |

- 整份校验先于目标存在性判断：输入无效时无论查询名是否已安装，都是退出码
  2 + `INPUT_ERROR\n`；不可达包的缺失版本或悬空依赖即使伴随一个不存在的
  查询目标，也不会变成 `NOT_FOUND`。
- 成功：退出码 0，标准错误为空，标准输出为完整 JSON 对象加恰好一个换行。
- `main()` 另有兜底 `except Exception`：任何未预期失败同样以
  `INPUT_ERROR\n`、退出码 2 结束，不泄露堆栈。
- `argparse` 自身的用法错误（缺参数、未知子命令等）沿用其默认行为，与上述
  查询语义无关，本文不改变它。

## 规则与现有测试的对应

下列规则均由现有测试固定，位置在 `tests/`：

| 规则 | 测试位置 |
| --- | --- |
| 本文两条验收命令的完整标准输出、退出码、空 stderr | `tests/test_ancestors.py`：`AncestorsAcceptance.test_sample_leaf_ancestors`；`tests/test_ancestors_reachable.py`：`AncestorsReachableAcceptance.test_without_option_lists_all_ancestors`、`test_with_reachable_filters_to_root_chain` |
| 直接 + 传递上游，按 Unicode 码点升序而非遍历/声明顺序 | `tests/test_ancestors.py`：`AncestorsApi.test_transitive_ancestors_sorted_by_unicode_code_point`、`test_declaration_order_swap_keeps_result_identical` |
| 循环中能到达目标的成员保留，目标自身排除；自环不加入目标 | `tests/test_ancestors.py`：`test_cycle_members_that_reach_target_are_kept`、`test_self_loop_does_not_add_target_itself`；`tests/test_ancestors_reachable.py`：`test_cycle_members_reachable_from_root_are_kept` |
| 根不可达包仍属上游（含不可达自环、字面 `$root` 包） | `tests/test_ancestors.py`：`test_unreachable_packages_are_still_ancestors`、`test_unreachable_self_loop_target_returns_empty`、`test_scoped_and_literal_root_targets` |
| 根项目不作为祖先；仅根声明的目标返回空数组 | `tests/test_ancestors.py`：`test_root_only_declaration_yields_empty` |
| 区分大小写的完整包名匹配；目标缺失抛 `NotFoundError` | `tests/test_ancestors.py`：`test_case_sensitive_full_name_only`、`test_missing_target_raises_not_found`、`AncestorsCli.test_missing_target_not_found`、`test_case_mismatch_not_found` |
| 输出仅含 `name`、`ancestors` 两字段且 `name` 原样回显 | `tests/test_ancestors.py`：`AncestorsCli.assertAncestorsObject`、`test_multi_ancestor_object`、`test_empty_ancestors_object`、`test_literal_root_target_is_normal_package`、`test_name_echoed_verbatim`；`tests/test_ancestors_reachable.py`：`test_name_echoed_verbatim_with_option` |
| `--reachable` 筛掉根不可达上游（orphan、outer、字面 `$root` 包），保留可达的传递与循环上游 | `tests/test_ancestors_reachable.py`：`AncestorsReachableCli.test_unreachable_ancestors_filtered_out`、`test_without_option_keeps_unreachable_ancestors` |
| 已安装但根不可达的目标、可达自环目标，带选项成功返回空数组 | `tests/test_ancestors_reachable.py`：`test_unreachable_target_returns_empty_ancestors`、`test_unreachable_self_loop_target_returns_empty`、`test_literal_root_package_unreachable_filtered` |
| 根 `dependencies` 省略或为空时带选项成功返回空数组 | `tests/test_ancestors_reachable.py`：`test_empty_or_missing_root_dependencies_yield_empty` |
| 带选项时目标未安装仍是退出码 1 + `NOT_FOUND\n` | `tests/test_ancestors_reachable.py`：`test_missing_target_still_not_found` |
| 整份校验先于目标检查；不可达条目的结构错误/悬空依赖令整份失败，即使目标不存在也不是 `NOT_FOUND` | `tests/test_ancestors.py`：`AncestorsInputErrors`（`test_invalid_input_beats_missing_query_target`、`test_structure_error_in_unreachable_entry_rejects_whole_file`、`test_nested_path_and_link_still_rejected`、`test_corrupt_json_is_input_error`、`test_unreadable_file_is_input_error`、`test_bad_utf8_is_input_error_even_for_missing_target`）；`tests/test_ancestors_reachable.py`：`test_unreachable_entry_error_still_rejects_whole_file`、`test_unreachable_dangling_dependency_still_rejects_whole_file` |
| 遍历不修改加载数据 | `tests/test_ancestors.py`：`AncestorsApi.test_does_not_mutate_loaded_data` |
| `--reachable` 不改变公开函数 `find_ancestors` 的行为 | `tests/test_ancestors_reachable.py`：`FindAncestorsUnchanged` |
| 接入 ancestors 后其余命令保持不变 | `tests/test_ancestors.py`：`ExistingCommandsUnchanged`（`why`、`parents` 冒烟） |

## 支持范围与不变量

- 仅支持 npm `package-lock.json` 的 **v3 平铺结构**（`lockfileVersion` 为
  整数 3，包条目键为 `node_modules/name` 或 `node_modules/@scope/name`）；
  嵌套安装路径与 `link: true` 条目不在支持范围。
- 上游关系只取根节点与包条目的 `dependencies`：不解析版本范围，不从其他
  字段补边。
- 上游是「沿自身 `dependencies` 经过至少一条边能到达目标的其他已安装包」
  集合：直接与传递关系都保留，根项目与目标自身始终排除，多路径汇合无重复；
  结果按完整包名的 Unicode 码点升序排列。不带选项时覆盖整份清单（含根不可
  达包），带 `--reachable` 时只额外保留自根节点可达的成员，查询名始终原样
  保留。
- 整份加载校验先于目标存在性判断：目标缺失为退出码 1 + `NOT_FOUND\n`，
  加载失败（含不可达包的缺失版本或悬空依赖）为退出码 2 + `INPUT_ERROR\n`；
  失败时标准输出为空，不输出部分结果或堆栈。成功为退出码 0、标准错误为空、
  标准输出为完整 JSON 对象加一个换行。
- 工具保持**离线、只读**：不联网，不安装、升级或执行锁文件中的任何依赖，
  不改写输入文件，仅使用 Python 标准库。
- 本文档不改动产品代码、公开接口、README、LOCKFILE.md、WHY.md、SBOM.md、
  DIFF.md、DESCENDANTS.md 及任何样例锁文件。
