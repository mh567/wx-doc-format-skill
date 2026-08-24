from __future__ import annotations

import os
import sys


os.environ["WX_DOC_FORMAT_OFFLINE"] = "1"
os.environ["WX_DOC_FORMAT_RELEASE"] = "1"

def run() -> None:
    try:
        from main import main
        main()
    except SystemExit:
        raise
    except BaseException:
        print("WXDF-E-UNEXPECTED", file=sys.stderr)
        raise SystemExit(70) from None


if __name__ == "__main__":
    run()
