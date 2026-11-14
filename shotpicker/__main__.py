import sys

from .cli import COMMANDS

if len(sys.argv) > 1 and sys.argv[1] in (*COMMANDS, "-h", "--help"):
    from .cli import main as cli_main

    sys.exit(cli_main(sys.argv[1:]))

from .app import main  # noqa: E402

main()
