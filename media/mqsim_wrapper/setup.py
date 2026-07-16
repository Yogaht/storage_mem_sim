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
import sysconfig
from setuptools import setup, find_packages
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop

try:
    from setuptools.command.editable_wheel import editable_wheel
except ImportError:  # pragma: no cover - old setuptools fallback
    editable_wheel = None

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
MQSIM_ROOT = os.path.join(CURRENT_DIR, "MQSim")
_BUILT = False


def _python_cmake_args():
    """Force CMake to use the Python interpreter running pip."""
    include_dir = sysconfig.get_path("include")
    python_h = os.path.join(include_dir or "", "Python.h")
    if not include_dir or not os.path.isfile(python_h):
        raise RuntimeError(
            "Python development headers were not found for the Python running pip:\n"
            f"  executable: {sys.executable}\n"
            f"  include:    {include_dir}\n"
            "Install the matching development package, for example:\n"
            "  sudo apt-get install python3-dev\n"
            "or, for a specific interpreter:\n"
            "  sudo apt-get install python3.11-dev"
        )
    return [
        f"-DPython_EXECUTABLE={sys.executable}",
        f"-DPython_INCLUDE_DIR={include_dir}",
        "-DPython_FIND_STRATEGY=LOCATION",
        "-DPython_FIND_VIRTUALENV=FIRST",
    ]


def _build_mqsim():
    """Build _mqsim pybind11 extension via CMake.

    CMakeLists.txt and mqsim_pybind.cpp live in this directory
    (media/mqsim_wrapper/).  MQSim C++ source is under MQSim/src/
    and is referenced read-only by the build.
    """
    global _BUILT
    if _BUILT:
        return

    mqsim_src = os.path.join(MQSIM_ROOT, "src")
    if not os.path.isdir(mqsim_src):
        raise RuntimeError(
            "[mqsim_wrapper] MQSim source not found at MQSim/src.\n"
            "Initialize the submodule:\n"
            "  git submodule update --init media/mqsim_wrapper/MQSim"
        )

    cmake_lists = os.path.join(CURRENT_DIR, "CMakeLists.txt")
    if not os.path.isfile(cmake_lists):
        raise RuntimeError(
            f"[mqsim_wrapper] CMakeLists.txt not found at {cmake_lists}"
        )

    build_dir = os.path.join(CURRENT_DIR, "build")
    os.makedirs(build_dir, exist_ok=True)

    cmake_args = [
        "cmake",
        "-S",
        CURRENT_DIR,
        "-B",
        build_dir,
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    cmake_args.extend(_python_cmake_args())
    print(f"[mqsim_wrapper] Configuring: {' '.join(cmake_args)}")
    subprocess.check_call(cmake_args)

    build_args = [
        "cmake",
        "--build",
        build_dir,
        "--config",
        "Release",
        "-j",
        str(os.cpu_count() or 4),
    ]
    print(f"[mqsim_wrapper] Building: {' '.join(build_args)}")
    subprocess.check_call(build_args)

    pymqsim_dir = os.path.join(CURRENT_DIR, "pymqsim")
    so_name = "_mqsim*" + (".pyd" if sys.platform == "win32" else ".so")
    import glob

    candidates = glob.glob(os.path.join(pymqsim_dir, so_name))
    if candidates:
        print(f"[mqsim_wrapper] _mqsim built: {candidates[0]}")
        _BUILT = True
        return

    candidates = glob.glob(os.path.join(build_dir, so_name))
    if candidates:
        print(f"[mqsim_wrapper] _mqsim built: {candidates[0]}")
        _BUILT = True
        return

    raise RuntimeError(
        "[mqsim_wrapper] _mqsim extension not found after build."
    )


class MQSimBuildExt(build_ext):
    """Build _mqsim pybind11 extension via CMake."""

    def run(self):
        _build_mqsim()
        super().run()


class MQSimBuildPy(build_py):
    def run(self):
        _build_mqsim()
        super().run()


class MQSimDevelop(develop):
    def run(self):
        _build_mqsim()
        super().run()


cmdclass = {
    "build_ext": MQSimBuildExt,
    "build_py": MQSimBuildPy,
    "develop": MQSimDevelop,
}


if editable_wheel is not None:

    class MQSimEditableWheel(editable_wheel):
        def run(self):
            _build_mqsim()
            super().run()

    cmdclass["editable_wheel"] = MQSimEditableWheel


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
    cmdclass=cmdclass,
    package_data={"pymqsim": ["_mqsim*.so", "_mqsim*.pyd", "_mqsim*.dll"]},
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
