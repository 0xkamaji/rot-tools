#!/usr/bin/env python3

############################################################
# Author: Kamaji aka Deadhand aka gh0st aka scraps_dad
# Creation Date: 08.10.26
# Description:
# This is my little rotten AI helper bot - rotbot. 
# He manages signalrot and rotten signals in his domain
############################################################

from rotbot.cli.parser import parse_args
from rotbot.commands.git import PUSH_CANCELLED


def main():
    args = parse_args()
    if not hasattr(args, "func"):
        from rotbot.session.interactive import run_interactive

        return run_interactive()
    result = args.func(args)

    if result is PUSH_CANCELLED:
        return 0
    return result if type(result) is int else 2
    

if __name__ == "__main__":
    raise SystemExit(main())
