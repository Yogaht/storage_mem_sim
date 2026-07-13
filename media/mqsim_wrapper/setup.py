"""Build the MQSim pybind11 extension and install the Python wrapper.

This setup script:
1. Builds the _mqsim pybind11 extension via CMake
2. Installs the 'pymqsim' Python package

Usage:
    git submodule update --init media/mqsim_wrapper/MQSim
    cd media/mqsim_wrapper
    pip install -e .
"""

import os
import subprocess
import sys
from setuptools import setup, find_packages
from setuptools.command.build_ext import build_ext

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
MQSIM_ROOT = os.path.join(CURRENT_DIR, "MQSim")


class MQSimBuildExt(build_ext):
    """Build _mqsim pybind11 extension via CMake.

    CMakeLists.txt and mqsim_pybind.cpp live in this directory
    (media/mqsim_wrapper/).  MQSim C++ source is under MQSim/src/
    and is referenced read-only by the build.
    """

    def run(self):
        mqsim_src = os.path.join(MQSIM_ROOT, "src")
        if not os.path.isdir(mqsim_src):
            print(
                "[mqsim_wrapper] MQSim source not found at MQSim/src.\n"
                "Initialize the submodule:\n"
                "  git submodule update --init media/mqsim_wrapper/MQSim",
                file=sys.stderr,
            )
            return

        cmake_lists = os.path.join(CURRENT_DIR, "CMakeLists.txt")
        if not os.path.isfile(cmake_lists):
            print(f"[mqsim_wrapper] CMakeLists.txt not found at {cmake_lists}",
                  file=sys.stderr)
            return

        build_dir = os.path.join(CURRENT_DIR, "build")
        os.makedirs(build_dir, exist_ok=True)

        # CMake configure — -S points to THIS directory (wrapper), not MQSim/
        cmake_args = [
            "cmake", "-S", CURRENT_DIR, "-B", build_dir,
            "-DCMAKE_BUILD_TYPE=Release",
        ]
        print(f"[mqsim_wrapper] Configuring: {' '.join(cmake_args)}")
        subprocess.check_call(cmake_args)

        # CMake build
        build_args = [
            "cmake", "--build", build_dir,
            "--config", "Release",
            "-j", str(os.cpu_count() or 4),
        ]
        print(f"[mqsim_wrapper] Building: {' '.join(build_args)}")
        subprocess.check_call(build_args)

        # Verify output
        pymqsim_dir = os.path.join(CURRENT_DIR, "pymqsim")
        so_name = "_mqsim*" + (".pyd" if sys.platform == "win32" else ".so")
        import glob
        candidates = glob.glob(os.path.join(pymqsim_dir, so_name))
        if candidates:
            print(f"[mqsim_wrapper] _mqsim built: {candidates[0]}")
        else:
            candidates = glob.glob(os.path.join(build_dir, so_name))
            if candidates:
                print(f"[mqsim_wrapper] _mqsim built: {candidates[0]}")
            else:
                print("[mqsim_wrapper] WARNING: _mqsim extension not found "
                      "after build — native binding will not be available.",
                      file=sys.stderr)


# Read long description
readme_path = os.path.join(CURRENT_DIR, "..", "..", "README.md")
long_description = ""
if os.path.isfile(readme_path):
    with open(readme_path, encoding="utf-8") as f:
        long_description = f.read()

setup(
    name="mqsim_wrapper",
    version="0.3.0",
    author="MemEngine",
    license="MIT",
    description="Python wrapper for MQSim SSD simulator with native pybind11 bindings",
    long_description=long_description,
    long_description_content_type="text/markdown",
    packages=find_packages(where=".", include=["pymqsim", "pymqsim.*"]),
    package_dir={"": "."},
    cmdclass={"build_ext": MQSimBuildExt},
    zip_safe=False,
    python_requires=">=3.10",
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: System :: Hardware",
    ],
)
