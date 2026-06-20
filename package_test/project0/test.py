import traceback
import torch.nn as nn # type: ignore


def green(s):
    return '\033[1;32m%s\033[m' % s


def yellow(s):
    return '\033[1;33m%s\033[m' % s


def red(s):
    return '\033[1;31m%s\033[m' % s


def log(*m):
    print(" ".join(map(str, m)))


def log_exit(*m):
    log(red("ERROR:"), *m)
    exit(1)


def check_numpy():
    try:
        import numpy # type: ignore
        log(green("PASS"), "NumPy installed")
    except ModuleNotFoundError:
        log(red("FAIL"), "NumPy not installed")


def check_scipy():
    try:
        import scipy # type: ignore
        log(green("PASS"), "SciPy installed")
    except ModuleNotFoundError:
        log(red("FAIL"), "SciPy not installed")


def check_matplotlib():
    try:
        import matplotlib # type: ignore
        log(green("PASS"), "matplotlib installed")
    except ModuleNotFoundError:
        log(red("FAIL"), "matplotlib not installed")


def check_torch():
    try:
        import torch # type: ignore
        log(green("PASS"), "PyTorch installed")
    except ModuleNotFoundError:
        log(red("FAIL"), "PyTorch not installed")


def check_tqdm():
    try:
        import tqdm # type: ignore
        log(green("PASS"), "tqdm installed")
    except ModuleNotFoundError:
        log(red("FAIL"), "tqdm not installed")


def main():
    try:
        check_numpy()
        check_scipy()
        check_matplotlib()
        check_torch()
        check_tqdm()
    except Exception:
        log_exit(traceback.format_exc())


if __name__ == "__main__":
    main()
