"""ic-wiki — command-line access to the brain wiki tools (manual sessions)."""
import argparse
import os
import sys

from ironclaude.wiki_tools import WikiTools


def _resolve_wiki_dir(brain):
    brain_cwd = brain or os.environ.get("IC_BRAIN_CWD") or "~/.ironclaude/brain"
    return os.path.join(os.path.expanduser(brain_cwd), "wiki")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ic-wiki", description="Brain wiki tools")
    parser.add_argument("--brain", help="Brain dir (default: $IC_BRAIN_CWD or ~/.ironclaude/brain)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("write", help="Create or update a page")
    pw.add_argument("page")
    pw.add_argument("title")
    pw.add_argument("content")

    pd = sub.add_parser("delete", help="Delete a page")
    pd.add_argument("page")

    pq = sub.add_parser("query", help="Search pages by keywords")
    pq.add_argument("keywords")
    pq.add_argument("--limit", type=int, default=20)

    pl = sub.add_parser("log", help="Append a changelog entry")
    pl.add_argument("entry")

    args = parser.parse_args(argv)
    wiki = WikiTools(_resolve_wiki_dir(args.brain))

    if args.cmd == "write":
        print(wiki.wiki_write(args.page, args.title, args.content))
    elif args.cmd == "delete":
        print(wiki.wiki_delete(args.page))
    elif args.cmd == "query":
        print(wiki.wiki_query(args.keywords, args.limit))
    elif args.cmd == "log":
        print(wiki.wiki_log(args.entry))
    return 0


if __name__ == "__main__":
    sys.exit(main())
