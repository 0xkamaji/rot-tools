############################################################
# Author: Kamaji aka Deadhand aka gh0st aka scraps_dad
# Creation Date: 08.10.26
# Description:
# This is my little rotten AI helper bot - rotbot. 
# He manages signalrot and rotten signals in his domain
############################################################

from gui import rot_say
from parser import create_parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    args.func(args)
    

if __name__ == "__main__":
    main()
