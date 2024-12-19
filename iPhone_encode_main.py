import os
import sys
from pathlib import Path

from loguru import logger

from main import get_args
from scripts.controllers.Appends import Modules
from scripts.controllers.encode_starter import PhoneEncodeStarter

logger.remove()
if __debug__:  # python実行の際に-OをつけるとFalse、じゃなければTrue
    logger.add(sys.stderr, level="DEBUG")
else:
    logger.add(sys.stderr, level="INFO")


def main():
    Modules.update()
    args = get_args()
    if __debug__:
        args.processes = 1
        target = r"Z:\encode\iPhone\audiobook"
        os.chdir(target)
        args.audio_only = True
        args.move_raw_file = True
        args.processes = 1
    logger.debug(args)
    phone_encode_starter = PhoneEncodeStarter(Path.cwd(), args=args)
    phone_encode_starter.process_multi_file()
    phone_encode_starter.post_actions()


if __name__ == "__main__":
    main()
