# 直接上游查询（parents）流程说明

本文说明 `python -m depinventory parents <lockfile> <name> [--reachable]` 从
**读取本地锁文件到输出 JSON** 的完整数据流，并用文件名与函数名定位源码，
便于核对哪些已安装包在自身 `dependencies` 中直接声明了查询目标：

- 参数解析、根可达筛选与命令行输出在 `depinventory/__main__.py`
  （`_build_parser()`、`main()`）；
- 加载校验 `load_lockfile()`、直接上游计算 `find_parents()`、可达集合
  `reachable_names()` 都在 `depinventory/lockfile.py`；
- `find_parents` 经 `depinventory/__init__.py` 导出，是公开接口。

行为约定与 README、LOCKFILE.md 一致；本文只解释现有行为，不改变任何产品
代码、公开接口（`depinventory/__init__.py` 的导出）、README、其他说明文档与
样例文件。工具只读本地输入文件，不安装或执行任何依赖，不联网，也不改写
输入；其余命令的行为保持不变。

## 总览：从命令行到 JSON 输出

一次 parents 查询依次经过五个阶段：

1. **参数解析**：`__main__.py` 的 `_build_parser()` 定义 `parents`
   子命令，两个位置参数依次为锁文件路径 `lockfile` 与按区分大小写的完整包名
   查询的 `name`，另有可选开关 `--reachable`。`main(argv=None)` 中
   `argparse` 解析后得到 `args.command == "parents"`、`args.lockfile`、
   `args.name`、`args.reachable`。
2. **本地加载与整份校验**：`main()` 调用 `lockfile.py` 的
   `load_lockfile(args.lockfile)`，成功则得到
   `root_deps, packages_map = (根依赖名列表, {包名: {"version", "deps"}})`；
   任何读取、解析或结构问题都在此处抛 `InputError`。
3. **目标匹配与直接上游计算**：`main()` 把 `root_deps`、`packages_map`、
   `args.name` 原样传给公开函数 `find_parents(root_deps, packages_map,
   target)`；函数先做唯一一次目标检查——`target not in packages_map` 即抛
   `NotFoundError`，随后对**整份清单**做一次成员扫描，返回
   `{"direct": 是否根声明, "parents": [排好序的包名]}`。
4. **可选的根可达筛选**：仅当给出 `--reachable` 时，`main()` 调用
   `reachable_names(root_deps, packages_map)` 求出自根节点沿 `dependencies`
   可达的包名集合，把不在集合中的 parents 成员剔除；`name` 与 `direct`
   两个字段不受影响。
5. **JSON 输出**：`main()` 把结果包装成仅含 `name`、`direct`、`parents`
   三个字段的对象，用 `json.dumps(result, ensure_ascii=False)` 序列化，加一个
   换行写入标准输出并返回 0。

```text
命令行：python -m depinventory parents parents-lock.json beta [--reachable]
                                   │  __main__.py: _build_parser() 解析参数
                                   ▼
load_lockfile(path)                lockfile.py（读取 → JSON 解析 → 整份结构校验）
   返回 (root_deps, packages_map)  │  失败抛 InputError
                                   ▼
find_parents(root_deps, packages_map, args.name)       lockfile.py（公开接口）
   ├─ 目标匹配：target 不在 packages_map → NotFoundError
   └─ 整份扫描：target in info["deps"] 的包名集合 → {"direct", "parents"}
                                   ▼
--reachable 时：reachable_names(root_deps, packages_map) 求可达集合，
               只保留可达的 parents 成员；name 与 direct 原样保留
                                   ▼
main() 包装：{"name": args.name, "direct": ..., "parents": 列表}
                                   ▼
stdout：完整 JSON + 一个换行；退出码 0；stderr 为空
```

## 加载数据如何进入公开 `find_parents` 接口

`load_lockfile(path)`（`lockfile.py`）整份校验通过后返回两个数据结构，它们是
全部命令共用的唯一数据源：

- **`root_deps`**：字符串列表，取自锁文件根节点（`packages` 中空串 `""`
  条目）`dependencies` 的键，按声明顺序保留；根节点省略 `dependencies` 时为
  `[]`。在 parents 查询中它只用于两件事：`find_parents` 里判断
  `direct`（`target in root_deps`），以及 `--reachable` 时作为
  `reachable_names()` 的遍历起点。
- **`packages_map`**：字典，键为 `_entry_name()` 从平铺安装路径
  `node_modules/name`、`node_modules/@scope/name` 识别出的完整包名，值为
  `{"version": 版本字符串, "deps": [依赖包名...]}`；`deps` 只保留依赖条目的
  **键（包名）**，`"*"`、`"^1.0.0"` 等版本范围值在校验后即丢弃。根项目不是
  其中条目——这正是「根项目不进入 parents」的结构性原因。

`find_parents` 在 `depinventory/__init__.py` 的 `__all__` 中导出，与
`load_lockfile` 同为公开接口，可直接以
`find_parents(root_deps, packages_map, target)` 调用；命令行路径只是由
`main()` 先加载再把这两个返回值位置传入。公开函数与命令行的分工是：
**公开函数始终面向整份清单**返回 `{"direct", "parents"}`，不做任何可达性
筛选；`--reachable` 是纯粹的命令行选项，由 `main()` 在拿到结果后用
`reachable_names()` 过滤 `parents` 成员，函数本身语义不变。

## 阶段一/二：加载与整份校验

加载规则与全部命令共用同一套输入协议，详见 LOCKFILE.md，此处只列与
parents 相关的要点。`load_lockfile()` 依次：以 UTF-8 文本模式读文件
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
`find_parents()`。因此即使错误位于从根不可达、也与查询包无关的条目上——
例如不可达包缺失版本、声明悬空依赖、`dependencies` 为 `null`——加载照样
失败，不可达条目的错误不能被跳过；同一命令上「输入无效」与「查询目标缺失」
同时成立时，结果是 `INPUT_ERROR` 而不是 `NOT_FOUND`。`--reachable` 也不放宽
这一顺序：筛选发生在加载成功之后，不可达包的校验错误同样以 `INPUT_ERROR`
失败。

## 阶段三：目标匹配

`find_parents(root_deps, packages_map, target)`（`lockfile.py`）只做一次
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
  根的标记；parents 不接受虚拟根，查询 `"$root"` 时按普通已安装包名在
  `packages_map` 中查找——只有锁文件里确实安装了名为 `$root` 的包才成功；
- **目标不要求根可达**：已安装但从根不可达的包照常作为目标，其直接上游照常
  计算。

## 阶段四：`find_parents()` 的直接声明者扫描

`find_parents()` 不做任何图遍历，只对**整份 `packages_map`** 做一次成员
扫描：

```python
parents = {
    name
    for name, info in packages_map.items()
    if target in info["deps"]
}
return {"direct": target in root_deps, "parents": sorted(parents)}
```

逐条对应语义：

- **`direct` 只表示根声明**：`direct` 为真当且仅当 `target` 出现在根节点
  `dependencies` 的键中（即 `root_deps`）。它与其他包是否声明目标无关，
  也不表示「可达」——根直接声明的包必然可达，但 `direct` 记录的是声明关系
  本身。
- **根项目不进入 parents**：扫描只遍历 `packages_map`，根项目不在其中，
  因此即使根节点声明了目标，根项目也不会成为 parents 成员——它只体现为
  `direct: true`。仅根声明、无其他包声明的目标得到
  `{"direct": true, "parents": []}`。
- **只取直接边，不展开更远祖先**：`parents` 是「自身 `dependencies` 直接
  声明 target 的包」，声明者的声明者（及更远的祖先）一律不列出。需要传递
  上游时用 `ancestors` 命令（见 ANCESTORS.md）。
- **查询覆盖整份清单**：扫描不看可达性，与根断开的包（如 `orphan`）只要
  直接声明目标，默认（不加 `--reachable`）就必须列入结果。是否只保留根可达
  的直接上游是 `--reachable` 选项的职责，由 `main()` 在下一阶段完成。
- **排序与去重**：集合推导天然去重——多个包各自声明同一目标时每个声明者
  只出现一次（同一包的 `deps` 在加载后已是键列表，本就不会重复）；`sorted()`
  按完整包名的 **Unicode 码点升序**排列，区分大小写，作用域包名作为整体
  参与比较（`@` 码点 0x40 排在字母之前），与条目书写顺序、依赖声明顺序
  无关。
- **自声明保留目标自身**：`target` 的 `deps` 含 `target`（自环）时，
  `target in info["deps"]` 对它自己同样成立，目标进入自己的 `parents`。
  这与 `ancestors`/`descendants` 排除目标自身的约定不同：parents 只回答
  「谁声明了它」，自环包确实声明了自己。
- **循环不会引入更远上游**：循环（如 `alpha ↔ charlie`）只是各自
  `dependencies` 中的直接声明，扫描逐条判断，无需遍历，自然正常结束；
  循环边上的对端是目标的直接上游则列出，循环中更远的节点不会被带入。
- **无直接声明者返回空数组**：目标已安装但没有任何包声明它时成功返回
  `{"direct": ..., "parents": []}`，这不是错误。

函数只读：不修改 `root_deps`、`packages_map` 及其中的 `deps` 列表。

## 阶段四（可选）：`--reachable` 的根可达筛选

给出 `--reachable` 时，`main()` 在拿到 `find_parents` 的结果后执行：

```python
keep = reachable_names(root_deps, packages_map)
parents = [name for name in parents if name in keep]
```

`reachable_names()`（`lockfile.py`）从 `root_deps` 出发沿各包的
`dependencies` 做广度优先遍历，返回自根节点可达的包名集合（不含根节点）；
`seen` 集合保证自环与循环正常结束，与根断开的包（包括断开的循环）整体不在
集合中。筛选规则：

- **只检查每个 parents 成员是否根可达**：保留的直接上游既直接声明了目标，
  又能自根节点沿 `dependencies` 到达；`name` 字段仍原样保留查询名，
  `direct` 仍表示根 `dependencies` 是否声明目标，两者都不受筛选影响；
- **不改变目标存在性判断**：目标是否根可达与筛选无关——已安装但根不可达
  的目标照常成功；此时它自身若声明自己（自环）也会因不可达被筛掉，带选项
  成功返回 `direct: false`、空 `parents`；
- **根 `dependencies` 省略或为空时可达集合为空**：任何直接上游都被筛掉，
  带选项成功返回空 `parents`（`direct` 必为 `false`，因为根没有声明任何包）；
- **不放宽整份校验**：筛选在加载成功之后才发生，不可达包的缺失版本、悬空
  依赖等结构错误仍以 `INPUT_ERROR` 失败，即使目标不存在也不会变成
  `NOT_FOUND`。

省略 `--reachable` 时本阶段整体跳过，`find_parents` 与命令行为与不设该
选项的既有语义完全一致。

## 阶段五：包装为 `name` / `direct` / `parents` 三字段 JSON

`main()` 中的成功分支为：

```python
elif args.command == "parents":
    found = find_parents(root_deps, packages_map, args.name)
    parents = found["parents"]
    if args.reachable:
        keep = reachable_names(root_deps, packages_map)
        parents = [name for name in parents if name in keep]
    result = {
        "name": args.name,
        "direct": found["direct"],
        "parents": parents,
    }
...
sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
return 0
```

- 对象只有 `name`、`direct`、`parents` 三个键，键的书写顺序即上述插入顺序；
- `name` **原样回显命令行查询名**，不做大小写规范化，也不回查改写；即使
  查询名是 `@scope/pkg`、`$root` 这样的字面值也逐字符保留；
- `direct` 原样取自 `find_parents` 的返回值，筛选不改写；
- `parents` 就是 `find_parents` 返回、再经可选筛选的字符串列表（可能为空）；
- `json.dumps(..., ensure_ascii=False)` 不转义非 ASCII 字符，标准输出末尾
  恰有一个换行；成功时标准错误为空，退出码为 0。

## 示例：parents-lock.json 的两次查询

`parents-lock.json` 的结构为：根节点只声明 `alpha`；`alpha → beta`；
`orphan → beta`，但 `orphan` 不被根节点或任何已安装包引用（从根不可达）；
`beta` 无依赖。

在**项目根目录**执行：

```sh
$ python -m depinventory parents parents-lock.json beta
{"name": "beta", "direct": false, "parents": ["alpha", "orphan"]}
$ python -m depinventory parents parents-lock.json beta --reachable
{"name": "beta", "direct": false, "parents": ["alpha"]}
```

两次都以退出码 0 结束，标准错误为空，标准输出为完整 JSON 加一个换行。

### 第一条：不加选项，orphan 为何在结果中

`find_parents` 扫描整份 `packages_map`：`alpha` 的 `deps` 为 `["beta"]`，
`orphan` 的 `deps` 为 `["beta"]`，两者都命中 `target in info["deps"]`；
`beta` 无依赖，不命中。集合 `{"alpha", "orphan"}` 排序后得
`["alpha", "orphan"]`。根节点的 `dependencies` 只有 `alpha`，不含 `beta`，
故 `direct` 为 `false`。`orphan` 从根不可达，但默认查询覆盖整份清单——
它直接声明 `beta`，是名副其实的直接上游，故列入结果。

### 第二条：--reachable 为何筛掉 orphan

`reachable_names()` 从根依赖 `["alpha"]` 出发：`alpha → beta`，可达集合为
`{alpha, beta}`。`orphan` 不被根节点或任何可达包引用，不在集合中，被
`[name for name in parents if name in keep]` 剔除；`alpha` 可达，保留。
`name` 与 `direct` 不受筛选影响，仍分别为 `"beta"` 与 `false`——`direct`
看的是根 `dependencies` 是否声明 `beta`，与可达性无关。

## 失败出口与流约定

失败时标准输出一律为空，不输出部分结果或堆栈：

| 情形 | 抛出位置 | 退出码 | 标准错误 |
| --- | --- | --- | --- |
| 合法输入中目标未安装（含大小写不匹配、字面 `$root` 未安装） | `find_parents()` 抛 `NotFoundError` | 1 | `NOT_FOUND` 加换行（`NOT_FOUND\n`） |
| 文件不可读、非 UTF-8、JSON 损坏（含裸 `NaN`/`Infinity`/`-Infinity`）、违反既有结构规则（版本、安装路径、`link: true`、`dependencies` 类型或悬空依赖等） | `load_lockfile()` 抛 `InputError` | 2 | `INPUT_ERROR` 加换行（`INPUT_ERROR\n`） |

- 整份校验先于目标检查：输入无效时无论查询名是否已安装，都是退出码 2；
  不可达包的缺失版本或悬空依赖同样属于整份输入错误，即使目标不存在也不能
  变为 `NOT_FOUND`；
- 成功：退出码 0，标准错误为空，标准输出为完整 JSON 加恰好一个换行；
- `argparse` 自身的用法错误（缺参数、未知子命令等）沿用其默认行为，与上述
  查询语义无关，本文不改变它。

## 规则与现有测试的对应

下列规则均由现有测试固定，位置在 `tests/`：

| 规则 | 测试位置 |
| --- | --- |
| 本文两条验收命令的完整标准输出、退出码、空 stderr | `tests/test_parents_reachable.py`：`ParentsReachableAcceptance.test_without_option_lists_all_parents`、`test_with_reachable_filters_to_root_chain` |
| 多个直接上游按 Unicode 码点升序而非声明顺序 | `tests/test_parents.py`：`ParentsApi.test_all_direct_upstreams_sorted_by_unicode_code_point` |
| `direct` 与 `parents` 可同时成立（根声明 + 其他包声明） | `tests/test_parents.py`：`test_direct_true_and_parents_coexist`、`ParentsCli.test_direct_with_parents` |
| 仅根声明时 `direct` 为真、根项目不进入 parents | `tests/test_parents.py`：`test_root_only_declaration_does_not_add_root_member` |
| 只取直接边，不展开更远祖先 | `tests/test_parents.py`：`test_only_direct_edges_no_transitive_ancestors` |
| 根不可达的包仍是直接上游（默认不筛选） | `tests/test_parents.py`：`test_unreachable_packages_are_still_parents` |
| 自环保留目标自身（含不可达自环） | `tests/test_parents.py`：`test_self_loop_on_unreachable_package_keeps_itself`、`ParentsCli.test_unreachable_self_loop_via_cli` |
| 作用域包整体匹配；区分大小写 | `tests/test_parents.py`：`test_scoped_target_matched_as_full_name`、`test_case_sensitive_full_name_only`、`ParentsCli.test_case_mismatch_not_found` |
| 字面值 `$root` 按普通已安装包处理 | `tests/test_parents.py`：`ParentsCli.test_literal_root_target_is_normal_package` |
| 结果与条目/依赖声明顺序无关 | `tests/test_parents.py`：`test_declaration_order_swap_keeps_result_identical` |
| 无直接上游的已安装目标成功返回空数组 | `tests/test_parents.py`：`ParentsAcceptance.test_new_gamma_installed_but_has_no_upstream` |
| `--reachable` 只筛选 parents 成员，name 与 direct 不变 | `tests/test_parents_reachable.py`：`test_unreachable_parents_filtered_out`、`test_direct_unaffected_by_filter` |
| 可达自环的目标带选项保留自身 | `tests/test_parents_reachable.py`：`test_reachable_self_loop_keeps_target_itself` |
| 已安装但根不可达的目标带选项成功返回空 parents、direct 为 false | `tests/test_parents_reachable.py`：`test_unreachable_self_loop_target_returns_empty_parents`、`test_literal_root_package_unreachable_filtered` |
| 省略 `--reachable` 时 `find_parents` 行为不变 | `tests/test_parents_reachable.py`：`FindParentsUnchanged` |
| 目标缺失抛 `NotFoundError`；退出码 1、stderr 为 `NOT_FOUND\n`、stdout 为空 | `tests/test_parents.py`：`test_missing_target_raises_not_found`、`ParentsCli.test_missing_target_not_found`；`tests/test_parents_reachable.py`：`test_missing_target_still_not_found` |
| 整份校验先于目标检查；悬空依赖优先于未安装目标报错 | `tests/test_parents.py`：`ParentsInputErrors.test_invalid_input_beats_missing_query_target`；`tests/test_parents_reachable.py`：`test_unreachable_dangling_dependency_still_rejects_whole_file` |
| 不可达条目的结构错误不能跳过（含 `dependencies: null`、嵌套路径、`link: true`、JSON 损坏、文件不可读、非法 UTF-8） | `tests/test_parents.py`：`ParentsInputErrors` 类其余用例；`tests/test_parents_reachable.py`：`test_unreachable_entry_error_still_rejects_whole_file` |
| 输出仅含 `name`、`direct`、`parents` 三字段且 `name` 原样回显 | `tests/test_parents.py`：`ParentsCli.assertParentsObject`、`test_name_echoed_verbatim`、`test_multi_parent_object` |
| 查询不修改加载数据 | `tests/test_parents.py`：`test_does_not_mutate_loaded_data` |
| 接入 parents 后其余命令保持不变 | `tests/test_parents.py`：`ExistingCommandsUnchanged`（`why`、`list` 冒烟） |

## 不变量

- 直接上游只依据各包的 `dependencies` 计算：不解析版本范围，不从其他字段
  补边；默认覆盖整份清单，根不可达的声明者同样是直接上游。
- `direct` 仅表示根节点 `dependencies` 是否声明目标；根项目不进入
  `parents`，仅根声明时 `parents` 为空数组。
- `parents` 是「自身 `dependencies` 直接声明目标的已安装包」集合：只取
  直接边，不展开更远祖先；自声明保留目标自身；循环无需遍历即可正常结束，
  不会引入更远上游；结果按完整包名的 Unicode 码点升序排列并去重，区分
  大小写，作用域名整体匹配，字面值 `$root` 按普通已安装包处理。
- `--reachable` 只按 parents 成员筛选：保留自根节点沿 `dependencies` 可达
  的直接上游；`name` 与 `direct` 原样保留；已安装但根不可达的目标带选项
  成功返回空 `parents`、`direct` 为 `false`。
- 整份加载校验先于目标检查；目标缺失为退出码 1 + `NOT_FOUND\n`，加载失败
  为退出码 2 + `INPUT_ERROR\n`，失败时标准输出为空且无堆栈；成功为退出码
  0、标准错误为空、标准输出为完整 JSON 加一个换行。
- 说明仅覆盖当前 npm v3 平铺结构；工具只读本地输入文件，不联网、不安装或
  执行依赖、不改写输入；其余命令行为保持不变。
- 本文档不改动产品代码、公开接口、README、LOCKFILE.md、WHY.md、SBOM.md、
  DIFF.md、ANCESTORS.md、DESCENDANTS.md 及任何样例锁文件。
