"""Build the Ramulator2 C++ extension.

Installation (two steps):
    pip install media/ramulator_wrapper/                # 1. cmake build
    pip install -e media/ramulator_wrapper/ramulator2   # 2. install Python pkg
"""

import os
import subprocess
from setuptools import setup
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
RAMULATOR_ROOT = os.path.join(CURRENT_DIR, "ramulator2")


def _build_ramulator():
    """Run CMake configure + build."""
    build_dir = os.path.join(RAMULATOR_ROOT, "build")

    cmake_args = [
        "cmake", "-S", RAMULATOR_ROOT, "-B", build_dir,
        "-DCMAKE_BUILD_TYPE=Release",
        "-DRAMULATOR_PYTHON_BINDINGS=ON",
    ]
    gxx_path = "/opt/homebrew/bin/g++-14"
    if os.path.exists(gxx_path):
        cmake_args.append(f"-DCMAKE_CXX_COMPILER={gxx_path}")

    print(f"[ramulator_wrapper] Configuring ...")
    subprocess.check_call(cmake_args)

    cpu_count = os.cpu_count() or 4
    build_args = ["cmake", "--build", build_dir, "-j", str(cpu_count)]
    print(f"[ramulator_wrapper] Building C++ extension ...")
    subprocess.check_call(build_args)


class RamulatorBuildExt(build_ext):
    def run(self):
        _build_ramulator()
        super().run()


class RamulatorBuildPy(build_py):
    def run(self):
        _build_ramulator()
        super().run()


class RamulatorDevelop(develop):
    def run(self):
        _build_ramulator()
        super().run()


setup(
    name="ramulator_wrapper",
    version="0.1.0",
    author="MemEngine",
    description="Build the Ramulator2 C++ extension via CMake",
    cmdclass={
        "build_ext": RamulatorBuildExt,
        "build_py": RamulatorBuildPy,
        "develop": RamulatorDevelop,
    },
    zip_safe=False,
    python_requires=">=3.10",
)
