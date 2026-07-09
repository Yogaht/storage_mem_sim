"""Build the MQSim SSD simulator binary.

This setup script compiles the MQSim C++ binary from source using make.
The resulting MQSim binary is placed in the MQSim submodule directory.

NOTE: Python bindings for MQSim are not yet implemented. The mqsim_media_system
module currently calls the MQSim binary via subprocess as a fallback.

Usage:
    cd media/mqsim_wrapper
    pip install -e .
"""

import os
import subprocess
import sys
from setuptools import setup
from setuptools.command.build_ext import build_ext

CURRENT_DIR = os.path.abspath(os.path.dirname(__file__))
MQSIM_ROOT = os.path.join(CURRENT_DIR, "MQSim")


class MQSimBuildExt(build_ext):
    """Build the MQSim C++ binary via make."""

    def run(self):
        # Use g++-14 if available
        env = os.environ.copy()
        gxx_path = "/opt/homebrew/bin/g++-14"
        if os.path.exists(gxx_path):
            env["CXX"] = gxx_path

        cpu_count = os.cpu_count() or 4
        make_args = ["make", "-C", MQSIM_ROOT, "-j", str(cpu_count)]
        print(f"[mqsim_wrapper] Building: {' '.join(make_args)}")
        subprocess.check_call(make_args, env=env)

        # Verify binary exists
        binary = os.path.join(MQSIM_ROOT, "MQSim")
        if os.path.isfile(binary):
            print(f"[mqsim_wrapper] MQSim binary built: {binary}")
        else:
            print("[mqsim_wrapper] WARNING: MQSim binary not found after build")


setup(
    name="mqsim_wrapper",
    version="0.1.0",
    author="MemEngine",
    description="Wrapper to build MQSim SSD simulator (Python bindings TBD)",
    cmdclass={"build_ext": MQSimBuildExt},
    zip_safe=False,
    python_requires=">=3.10",
)
