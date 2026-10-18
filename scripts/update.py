#!/usr/bin/env python3
import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from greekpipe.config import load_settings  # noqa: E402
from greekpipe.llm import LLMAuthError  # noqa: E402
from greekpipe.update_runner import FetchError, needs_commit, run  # noqa: E402

PATHS = ["state", "media", "content"]
ATTRIBUTION = (
    "🤖 Generated with [Claude Code](https://claude.com/claude-code)\n"
    "Co-Authored-By: Anthropic Claude <noreply@anthropic.com>"
)


def push(root: Path) -> None:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout

    if not needs_commit(git("status", "--porcelain", "--", *PATHS)):
        print("Изменений нет, коммит не нужен")
        return
    git("add", *PATHS)
    git("commit", "-m", f"content: update {date.today().isoformat()}\n\n{ATTRIBUTION}")
    git("push")


def main() -> int:
    parser = argparse.ArgumentParser(description="Обновить сайт: Telegram → разборы → словарь → сборка")
    parser.add_argument("--no-fetch", action="store_true", help="не забирать новые сообщения")
    parser.add_argument("--limit", type=int, help="максимум элементов на этап (разборы, пачки слов)")
    parser.add_argument("--push", action="store_true", help="закоммитить и запушить изменения")
    parser.add_argument("--dry-run", action="store_true", help="только пересобрать сайт, без Telegram и LLM")
    args = parser.parse_args()

    settings = load_settings()
    try:
        result = run(settings, fetch=not args.no_fetch, limit=args.limit, dry_run=args.dry_run)
    except LLMAuthError:
        print("OpenRouter: ошибка авторизации/кредитов")
        return 3
    except FetchError as exc:
        print(f"Telegram: {exc}")
        return 2
    print(result.summary)
    if args.push:
        push(settings.root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
