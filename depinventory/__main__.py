"""命令行入口：python -m depinventory list|why|parents|ancestors|descendants|diff|sbom <lockfile> [参数]。"""

import argparse
import json
import sys

from .lockfile import (
    InputError,
    NotFoundError,
    diff_items,
    direct_items,
    find_ancestors,
    find_descendants,
    find_path,
    find_parents,
    list_items,
    load_lockfile,
    reachable_items,
    reachable_names,
    sbom_document,
    unreachable_items,
)

_INPUT_ERROR = "INPUT_ERROR"
_NOT_FOUND = "NOT_FOUND"


def _encode_result(result):
    """把成功结果序列化为待写出的 UTF-8 字节（单个 JSON 文档加末尾换行）。

    JSON 转义文本可解码出孤立 Unicode 代理码点（如 "2.0-\\ud83f"）：
    Python 字符串允许持有它们，json.dumps(ensure_ascii=False) 也照常产出
    文本，但严格 UTF-8 编码无法表示，encode 抛 UnicodeEncodeError。合法
    代理对在 JSON 解析阶段已合并为补充平面字符（如 U+1F600），中文与其余
    正常 Unicode 同样正常编码。调用方据此按输入错误处理，且因整份字节
    在此一次成型、之后才写 stdout，失败时标准输出不会出现部分清单。
    """

    return (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="depinventory",
        description="列出 npm 锁文件中的已安装包，查询依赖来源路径，或比较两份清单。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出全部已安装条目")
    list_parser.add_argument("lockfile", help="package-lock.json 路径")
    list_parser.add_argument(
        "--reachable",
        action="store_true",
        help="只列出自根节点沿 dependencies 可达的条目；省略时输出完整清单。",
    )
    list_parser.add_argument(
        "--unreachable",
        action="store_true",
        help="只列出自根节点沿 dependencies 不可达的条目；"
        "与 --reachable 互斥，同时出现按输入错误处理。",
    )
    list_parser.add_argument(
        "--direct",
        action="store_true",
        help="只列出根节点 dependencies 直接声明的条目（direct 均为 true）；"
        "与 --reachable/--unreachable 组合时取两种筛选的交集。",
    )

    why_parser = subparsers.add_parser(
        "why", help="查询目标包自根节点（或指定起点包）的最短依赖路径"
    )
    why_parser.add_argument("lockfile", help="package-lock.json 路径")
    why_parser.add_argument("name", help="按区分大小写的完整包名查询")
    why_parser.add_argument(
        "--from",
        dest="source",
        metavar="包名",
        default=None,
        help="以指定已安装包为查询起点；省略时自根节点 $root 查起。"
        "按区分大小写的完整包名匹配，字面值 $root 仍按普通包名查找。",
    )

    parents_parser = subparsers.add_parser(
        "parents", help="查询目标包的全部直接上游（哪些已安装包声明了它）"
    )
    parents_parser.add_argument("lockfile", help="package-lock.json 路径")
    parents_parser.add_argument("name", help="按区分大小写的完整包名查询")
    parents_parser.add_argument(
        "--reachable",
        action="store_true",
        help="parents 只保留自根节点沿 dependencies 可达的直接上游；"
        "省略时列出整份清单中的全部直接上游。",
    )

    ancestors_parser = subparsers.add_parser(
        "ancestors", help="查询目标包的全部上游（沿 dependencies 能到达它的包）"
    )
    ancestors_parser.add_argument("lockfile", help="package-lock.json 路径")
    ancestors_parser.add_argument("name", help="按区分大小写的完整包名查询")
    ancestors_parser.add_argument(
        "--reachable",
        action="store_true",
        help="ancestors 只保留自根节点沿 dependencies 可达的上游；"
        "省略时列出整份清单中的全部上游。",
    )

    descendants_parser = subparsers.add_parser(
        "descendants", help="查询目标包的全部下游（它沿 dependencies 带入的包）"
    )
    descendants_parser.add_argument("lockfile", help="package-lock.json 路径")
    descendants_parser.add_argument("name", help="按区分大小写的完整包名查询")

    diff_parser = subparsers.add_parser("diff", help="比较两份清单的版本差异")
    diff_parser.add_argument("before", help="旧清单 package-lock.json 路径")
    diff_parser.add_argument("after", help="新清单 package-lock.json 路径")
    diff_parser.add_argument(
        "--reachable",
        action="store_true",
        help="只比较两份清单各自自根节点沿 dependencies 可达的条目；"
        "省略时比较全部已安装条目。与 --unreachable 互斥，同时出现按"
        "输入错误处理。",
    )
    diff_parser.add_argument(
        "--unreachable",
        action="store_true",
        help="只比较两份清单各自已安装但自根节点沿 dependencies 不可达"
        "的条目（与 list --unreachable 同一口径）；根依赖省略或为空时"
        "该侧全部安装包进入比较。与 --reachable 互斥，同时出现按输入"
        "错误处理。",
    )
    diff_parser.add_argument(
        "--direct",
        action="store_true",
        help="只比较两份清单各自根节点 dependencies 直接声明的条目；"
        "与 --reachable 同时出现时结果与只用 --direct 一致；"
        "与 --unreachable 同时出现时取交集，结果恒为空。",
    )

    sbom_parser = subparsers.add_parser(
        "sbom", help="导出简化 SBOM（产品自有格式，非标准 SBOM）"
    )
    sbom_parser.add_argument("lockfile", help="package-lock.json 路径")
    sbom_parser.add_argument(
        "--reachable",
        action="store_true",
        help="只导出自根节点沿 dependencies 可达的组件；省略时导出全部已安装包。",
    )
    sbom_parser.add_argument(
        "--with-paths",
        dest="with_paths",
        action="store_true",
        help="为每个组件附加 path：自 $root 到该包的最短 dependencies 路径"
        "（与省略 --from 的 why 查询一致）；根不可达的组件 path 为 []。"
        "省略时组件字段保持原样。可与 --reachable 组合，仅为筛选后的组件附加路径。",
    )
    sbom_parser.add_argument(
        "--with-dependencies",
        dest="with_dependencies",
        action="store_true",
        help="为每个组件附加 dependencies：该包条目直接声明的完整包名数组，"
        "按 Unicode 码点升序去重，不展开传递依赖；声明省略或为空时为 []。"
        "省略时组件字段保持原样。可与 --reachable、--with-paths 组合。",
    )
    sbom_parser.add_argument(
        "--with-purl",
        dest="with_purl",
        action="store_true",
        help="为每个组件附加 purl：pkg:npm/包名@版本（作用域包为 "
        "pkg:npm/作用域/包名@版本），名称片段与版本按 UTF-8 字节百分号编码；"
        "只取原始包名与版本，不解析版本范围。省略时组件字段保持原样。"
        "可与 --reachable、--with-paths、--with-dependencies 组合。",
    )

    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    # --reachable 与 --unreachable 互斥：在读取任何文件前按输入错误拒绝，
    # 即使还带 --direct 也如此（list 与 diff 两条子命令同口径）。
    if (
        args.command in ("list", "diff")
        and getattr(args, "reachable", False)
        and getattr(args, "unreachable", False)
    ):
        print(_INPUT_ERROR, file=sys.stderr)
        return 2

    try:
        if args.command == "diff":
            before_root, before_map = load_lockfile(args.before)
            after_root, after_map = load_lockfile(args.after)
        else:
            root_deps, packages_map = load_lockfile(args.lockfile)
    except InputError:
        print(_INPUT_ERROR, file=sys.stderr)
        return 2
    except Exception:
        # 兜底：任何未预期的失败也不得输出堆栈或部分结果。
        print(_INPUT_ERROR, file=sys.stderr)
        return 2

    try:
        if args.command == "list":
            if args.direct:
                # 与可达性筛选取交集：根直接声明的包必然可达，故 --reachable
                # 不改变结果；不可达集合与直接依赖不相交，故 --unreachable
                # 时交集为空。
                if args.unreachable:
                    result = []
                else:
                    result = direct_items(root_deps, packages_map)
            elif args.reachable:
                result = reachable_items(root_deps, packages_map)
            elif args.unreachable:
                result = unreachable_items(root_deps, packages_map)
            else:
                result = list_items(root_deps, packages_map)
        elif args.command == "sbom":
            result = sbom_document(
                root_deps,
                packages_map,
                reachable=args.reachable,
                with_paths=args.with_paths,
                with_dependencies=args.with_dependencies,
                with_purl=args.with_purl,
            )
        elif args.command == "diff":
            if args.direct:
                # 两侧各自先确定根节点 dependencies 直接声明的已安装包，
                # 再与可达性筛选取交集：直接声明的包必然可达，故与
                # --reachable 同时出现时结果与只用 --direct 一致；
                # 不可达集合与直接声明集合不相交，故与 --unreachable
                # 同时出现时两侧比较集合均为空（合法输入返回 []）。
                # 校验已在加载时整份完成，未进入比较范围的条目
                # （传递依赖、未引用包）的错误同样导致失败。
                if args.unreachable:
                    before_keep = set()
                    after_keep = set()
                else:
                    before_keep = set(before_root)
                    after_keep = set(after_root)
                before_map = {
                    name: info
                    for name, info in before_map.items()
                    if name in before_keep
                }
                after_map = {
                    name: info
                    for name, info in after_map.items()
                    if name in after_keep
                }
            elif args.reachable:
                # 两份清单各自从根节点 dependencies 出发确定可达集合，
                # 只保留可达条目后再比较；校验已在加载时整份完成，
                # 不可达包的错误同样导致失败。
                before_keep = reachable_names(before_root, before_map)
                after_keep = reachable_names(after_root, after_map)
                before_map = {
                    name: info
                    for name, info in before_map.items()
                    if name in before_keep
                }
                after_map = {
                    name: info
                    for name, info in after_map.items()
                    if name in after_keep
                }
            elif args.unreachable:
                # 与 list --unreachable 同一口径：每侧取自身全部已安装包
                # 中不属于该侧根可达集合的条目（根项目不在 packages_map，
                # 天然不参与）。根 dependencies 省略或为空时该侧可达集合
                # 为空，全部安装包进入比较；与根断开的自环和循环保留。
                # 关系仍只取 dependencies，不解析版本范围，不从其他字段
                # 补边。校验已在加载时整份完成，可达包的错误同样导致失败。
                before_reachable = reachable_names(before_root, before_map)
                after_reachable = reachable_names(after_root, after_map)
                before_keep = {
                    name for name in before_map if name not in before_reachable
                }
                after_keep = {
                    name for name in after_map if name not in after_reachable
                }
                before_map = {
                    name: info
                    for name, info in before_map.items()
                    if name in before_keep
                }
                after_map = {
                    name: info
                    for name, info in after_map.items()
                    if name in after_keep
                }
            result = diff_items(before_map, after_map)
        elif args.command == "ancestors":
            ancestors = find_ancestors(root_deps, packages_map, args.name)
            if args.reachable:
                # 只按 ancestors 成员筛选：保留自根节点沿 dependencies
                # 可达的上游；name 仍原样保留查询名，不受筛选影响。
                # 校验已在加载时整份完成，不可达包的错误同样导致失败。
                keep = reachable_names(root_deps, packages_map)
                ancestors = [name for name in ancestors if name in keep]
            result = {
                "name": args.name,
                "ancestors": ancestors,
            }
        elif args.command == "descendants":
            result = {
                "name": args.name,
                "descendants": find_descendants(root_deps, packages_map, args.name),
            }
        elif args.command == "parents":
            found = find_parents(root_deps, packages_map, args.name)
            parents = found["parents"]
            if args.reachable:
                # 只按 parents 成员筛选：保留自根节点沿 dependencies 可达
                # 的直接上游；direct 仍表示根 dependencies 是否声明目标，
                # 不受筛选影响。校验已在加载时整份完成，不可达包的错误
                # 同样导致失败。
                keep = reachable_names(root_deps, packages_map)
                parents = [name for name in parents if name in keep]
            result = {
                "name": args.name,
                "direct": found["direct"],
                "parents": parents,
            }
        else:
            path = find_path(root_deps, packages_map, args.name, args.source)
            result = {"name": args.name, "path": path}
    except NotFoundError:
        print(_NOT_FOUND, file=sys.stderr)
        return 1
    except Exception:
        print(_INPUT_ERROR, file=sys.stderr)
        return 2

    try:
        # 先在内存中完成序列化与 UTF-8 编码：实际输出文本含孤立代理码点
        # 时在此抛 UnicodeEncodeError，按输入错误处理，且尚未写过任何
        # 标准输出字节，故不会吐出部分 JSON 或堆栈。只检查实际结果文本；
        # 未参与结果的元数据、被筛选排除的版本字符串不影响能正常输出的
        # 结果。load_lockfile 与各分析函数的返回值/异常语义不变。
        payload = _encode_result(result)
    except UnicodeEncodeError:
        print(_INPUT_ERROR, file=sys.stderr)
        return 2

    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
