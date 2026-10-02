"""命令行入口：python -m depinventory list|why|diff|sbom <lockfile> [参数]。"""

import argparse
import json
import sys

from .lockfile import (
    InputError,
    NotFoundError,
    diff_items,
    find_path,
    list_items,
    load_lockfile,
    sbom_document,
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

    diff_parser = subparsers.add_parser("diff", help="比较两份清单的版本差异")
    diff_parser.add_argument("before", help="旧清单 package-lock.json 路径")
    diff_parser.add_argument("after", help="新清单 package-lock.json 路径")

    sbom_parser = subparsers.add_parser(
        "sbom", help="导出简化 SBOM（产品自有格式，不声明符合其他 SBOM 标准）"
    )
    sbom_parser.add_argument("lockfile", help="package-lock.json 路径")

    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "diff":
            _, before_map = load_lockfile(args.before)
            _, after_map = load_lockfile(args.after)
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
            result = list_items(root_deps, packages_map)
        elif args.command == "diff":
            result = diff_items(before_map, after_map)
        elif args.command == "sbom":
            result = sbom_document(root_deps, packages_map)
        else:
            path = find_path(root_deps, packages_map, args.name, args.source)
            result = {"name": args.name, "path": path}
    except NotFoundError:
        print(_NOT_FOUND, file=sys.stderr)
        return 1
    except Exception:
        print(_INPUT_ERROR, file=sys.stderr)
        return 2

    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
