import platform
import shutil


def doctor():
    print()
    print("========== NUTTZ DOCTOR ==========")
    print()

    print("Python :", platform.python_version())
    print("OS     :", platform.system())

    docker = shutil.which("docker")

    if docker:
        print("Docker : Installed")
    else:
        print("Docker : Missing")

    print()
    print("System OK")
