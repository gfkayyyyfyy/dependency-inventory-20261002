"""命令行入口：python -m depinventory list|why <lockfile> [包名]。"""

import argparse
import json
import sys

from .lockfile import InputError, NotFoundError, find_path, list_items, load_lockfile

_INPUT_ERROR = "INPUT_ERROR"
_NOT_FOUND = "NOT_FOUND"


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="depinventory",
        description="列出 npm 锁文件中的已安装包，或查询包的依赖来源路径。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="列出全部已安装条目")
    list_parser.add_argument("lockfile", help="package-lock.json 路径")

    why_parser = subparsers.add_parser("why", help="查询某包自根节点的最短依赖路径")
    why_parser.add_argument("lockfile", help="package-lock.json 路径")
    why_parser.add_argument("name", help="按区分大小写的完整包名查询")

    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
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
        else:
            path = find_path(root_deps, packages_map, args.name)
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
