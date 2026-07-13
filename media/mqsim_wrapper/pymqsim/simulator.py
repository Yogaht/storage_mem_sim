"""MQSim simulation engine  (runs the simulator, returns results).

Primary path: native pybind11 _mqsim module.
Fallback:    subprocess call to MQSim binary.

This module ONLY runs the simulation — trace generation and workload
XML are handled by trace.py and workload.py respectively.
"""

import os
import logging
import subprocess
import tempfile
from typing import Optional

from .output import MQSimResult, parse_mqsim_output

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 300

# ------------------------------------------------------------------
# Native pybind11 binding
# ------------------------------------------------------------------

_native_module = None


def _get_native():
    global _native_module
    if _native_module is None:
        try:
            from . import _mqsim  # type: ignore[import]
            _native_module = _mqsim
            logger.info("Using native _mqsim pybind11 binding.")
        except ImportError:
            logger.debug("_mqsim pybind11 module not available; "
                         "will use subprocess fallback.")
    return _native_module


# ------------------------------------------------------------------
# Binary auto-detection
# ------------------------------------------------------------------

def _find_mqsim_binary(explicit_path: Optional[str] = None) -> str:
    if explicit_path:
        expanded = os.path.expanduser(explicit_path)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return os.path.abspath(expanded)

    bundled = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "MQSim", "MQSim"))
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return os.path.abspath(bundled)

    import shutil
    found = shutil.which("MQSim")
    if found:
        return found

    searched = [explicit_path or "(not given)", bundled, "$PATH"]
    raise FileNotFoundError(
        "MQSim executable not found. Searched:\n"
        + "\n".join(f"  - {s}" for s in searched)
        + "\n\nBuild with:\n"
        "  git submodule update --init media/mqsim_wrapper/MQSim\n"
        "  cd media/mqsim_wrapper && pip install -e ."
    )


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def check_mqsim_available(mqsim_binary: Optional[str] = None) -> bool:
    if _get_native() is not None:
        return True
    try:
        _find_mqsim_binary(mqsim_binary)
        return True
    except FileNotFoundError:
        return False


def run_simulation(
    trace_path: str,
    ssd_config_path: str,
    workload_xml_path: str,
    *,
    output_dir: Optional[str] = None,
    timeout_sec: int = _DEFAULT_TIMEOUT,
    mqsim_binary: Optional[str] = None,
) -> MQSimResult:
    """Run a single MQSim simulation and return parsed results.

    Args:
        trace_path:       Path to the MQSim trace file.
        ssd_config_path:  Path to ssdconfig.xml.
        workload_xml_path: Path to workload XML  (pre-built by caller).
        output_dir:       Working directory.  Default: temp dir.
        timeout_sec:      Subprocess timeout.
        mqsim_binary:     Explicit binary path (subprocess fallback).

    Returns:
        MQSimResult.

    Raises:
        FileNotFoundError:  Config / workload not found, or no engine.
        RuntimeError:       Simulation failed.
    """
    for label, p in [("SSD config", ssd_config_path),
                     ("workload XML", workload_xml_path)]:
        if not os.path.isfile(p):
            raise FileNotFoundError(f"{label} not found: {p}")

    cleanup_dir = False
    if output_dir is None:
        output_dir = tempfile.mkdtemp(prefix="mqsim_run_")
        cleanup_dir = True
    else:
        os.makedirs(output_dir, exist_ok=True)

    ssd_local = os.path.join(output_dir, "ssdconfig.xml")
    print(f"[MQSim] output dir: {os.path.abspath(output_dir)}")

    try:
        _copy_file(ssd_config_path, ssd_local)

        native = _get_native()
        if native is not None:
            # --- native pybind11 ---
            logger.info("Running MQSim via native pybind11 binding.")
            if hasattr(native, 'run_with_stats'):
                stats = native.run_with_stats(
                    ssd_local, workload_xml_path, output_dir)
                logger.info(
                    "MQSim flow: generated=%s serviced=%s "
                    "dev_resp=%s ns e2e=%s ns",
                    stats.get("generated_request_count", "?"),
                    stats.get("serviced_request_count", "?"),
                    stats.get("device_response_time_ns", "?"),
                    stats.get("end_to_end_request_delay_ns", "?"),
                )
                ok = stats is not None
            else:
                ok = native.run(ssd_local, workload_xml_path, output_dir)
            if not ok:
                raise RuntimeError("MQSim pybind11 simulation failed.")
        else:
            # --- subprocess fallback ---
            binary = _find_mqsim_binary(mqsim_binary)
            cmd = [binary, "-i", ssd_local, "-w", workload_xml_path]
            logger.info("Running MQSim (subprocess): %s", " ".join(cmd))
            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=timeout_sec, cwd=output_dir,
            )
            if result.returncode != 0:
                stderr = result.stderr[-1000:] if result.stderr else "(none)"
                stdout = result.stdout[-1000:] if result.stdout else "(none)"
                raise RuntimeError(
                    f"MQSim exited with code {result.returncode}\n"
                    f"STDERR: {stderr}\nSTDOUT: {stdout}"
                )

        # Parse output XML
        output_xml = _find_output_xml(output_dir)
        if output_xml is None:
            logger.warning("No workload_scenario_N.xml in %s", output_dir)
            return MQSimResult()

        print(f"[MQSim] result file: {os.path.abspath(output_xml)}")
        return parse_mqsim_output(output_xml)

    except subprocess.TimeoutExpired:
        logger.error("MQSim timed out after %d s", timeout_sec)
        raise

    finally:
        if cleanup_dir:
            import shutil
            shutil.rmtree(output_dir, ignore_errors=True)
        else:
            # Only clean up the copied SSD config; workload + result
            # are owned by the caller.
            if os.path.isfile(ssd_local):
                try:
                    os.remove(ssd_local)
                except OSError:
                    pass


# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------

def _copy_file(src: str, dst: str) -> None:
    d = os.path.dirname(dst)
    if d:
        os.makedirs(d, exist_ok=True)
    import shutil
    shutil.copy2(src, dst)


def _find_output_xml(output_dir: str) -> Optional[str]:
    for i in range(1, 9):
        path = os.path.join(output_dir, f"workload_scenario_{i}.xml")
        if os.path.isfile(path):
            return path
    return None
