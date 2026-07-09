"""Build and install the Ramulator2 Python package.

Usage:
    cd media/ramulator_wrapper
    pip install -e .
"""

import os
import subprocess
import sys
from setuptools import setup
from setuptools.command.build_ext import build_ext

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
RAMULATOR_ROOT = os.path.join(CURRENT_DIR, "ramulator2")


class RamulatorBuildExt(build_ext):
    """Build the Ramulator2 C++ extension via CMake, then install the Python package."""

    def run(self):
        build_dir = os.path.join(RAMULATOR_ROOT, "build")

        # Step 1: CMake configure
        cmake_args = [
            "cmake",
            "-S", RAMULATOR_ROOT,
            "-B", build_dir,
            "-DCMAKE_BUILD_TYPE=Release",
            "-DRAMULATOR_PYTHON_BINDINGS=ON",
        ]
        gxx_path = "/opt/homebrew/bin/g++-14"
        if os.path.exists(gxx_path):
            cmake_args.append(f"-DCMAKE_CXX_COMPILER={gxx_path}")

        print(f"[ramulator_wrapper] Configuring: cmake ...")
        subprocess.check_call(cmake_args)

        # Step 2: CMake build
        cpu_count = os.cpu_count() or 4
        build_args = ["cmake", "--build", build_dir, "-j", str(cpu_count)]
        print(f"[ramulator_wrapper] Building C++ extension ...")
        subprocess.check_call(build_args)

        # Step 3: Install the ramulator Python package (editable)
        pip_args = [sys.executable, "-m", "pip", "install", "-e", RAMULATOR_ROOT]
        print(f"[ramulator_wrapper] Installing ramulator Python package ...")
        subprocess.check_call(pip_args)


setup(
    name="ramulator_wrapper",
    version="0.1.0",
    author="MemEngine",
    description="Wrapper to build and install Ramulator2 with Python bindings",
    cmdclass={"build_ext": RamulatorBuildExt},
    zip_safe=False,
    python_requires=">=3.10",
)
