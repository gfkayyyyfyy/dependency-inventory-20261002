"""命令行入口：python -m depinventory list|why|parents|ancestors|descendants|diff|sbom <lockfile> [参数]。"""

import argparse
import json
import sys

from .lockfile import (
    InputError,
    NotFoundError,
    diff_items,
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
        "省略时比较全部已安装条目。",
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

    # --reachable 与 --unreachable 互斥：在读取任何文件前按输入错误拒绝。
    if (
        args.command == "list"
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
            if args.reachable:
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
            if args.reachable:
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

    # 先把整份 JSON 文档编码为 UTF-8 再写入：结果字符串中若含 JSON 转义
    # 出的孤立代理码点（无法按 UTF-8 编码），json.dumps 本身不会报错，
    # 直接写入 TextIO 会在输出中途抛 UnicodeEncodeError——此时部分字节
    # 可能已经落盘。这里先对完整文档编码：编码失败则一个字节都不输出，
    # 统一按输入错误处理。判断范围是实际结果文本：未参与结果的元数据、
    # 被筛选排除的版本字符串即使含孤立代理，只要不在文档中就不影响输出。
    # 正常 Unicode（中文、合法代理对解码出的补充平面字符）原样保留，
    # ensure_ascii=False 维持既有成功输出形态。
    try:
        payload = (json.dumps(result, ensure_ascii=False) + "\n").encode("utf-8")
    except UnicodeEncodeError:
        print(_INPUT_ERROR, file=sys.stderr)
        return 2
    sys.stdout.buffer.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
