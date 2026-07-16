"""Ramulator2 media system — cycle-accurate DRAM simulation.

Integrates Ramulator2 v2.1 via its nanobind Python bindings.
Accepts a Ramulator2-native YAML config file for DRAM, controller,
scheduler, addr mapper, and refresh policy configuration.

The LoadStoreTrace frontend is auto-injected with the generated trace file.

Requires: cd media/ramulator_wrapper && pip install -e .
"""

import math
import os
import uuid
import logging
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory_request import MemoryRequest

from .base_media import BaseMediaSystem
from .media_config import MediaConfig
from .media_request import MediaRequest
from .media_metrics import MediaMetrics

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Default YAML (PascalCase, compatible with Ramulator2 example_config.yaml)
# ------------------------------------------------------------------

FALLBACK_YAML = """\
MemorySystem:
  impl: GenericDRAM
  clock_ratio: 1
  ChannelMapper:
    impl: CacheLineInterleave
  Controllers:
    - impl: GenericDDR
      Scheduler:
        impl: FRFCFS
      RowPolicy:
        impl: Open
      AddrMapper:
        impl: RoBaRaCoCh
      RefreshManager:
        impl: NoRefresh
      DRAM:
        impl: DDR5
        org:
          preset: DDR5_16Gb_x8
        timing:
          preset: DDR5_4800AN
"""


class RamulatorMediaSystem(BaseMediaSystem):
    """Cycle-accurate DRAM simulation backend using Ramulator2.

    YAML config format (Ramulator2-native):

        Frontend:
          clock_ratio: 4

        MemorySystem:
          impl: GenericDRAM
          clock_ratio: 1
          ChannelMapper:
            impl: CacheLineInterleave
          Controllers:
            - impl: GenericDDR
              Scheduler:      {impl: FRFCFS}
              RowPolicy:      {impl: Open}
              AddrMapper:     {impl: RoBaRaCoCh}
              RefreshManager:  {impl: NoRefresh}
              DRAM:
                impl: DDR5
                org:    {preset: DDR5_16Gb_x8}
                timing: {preset: DDR5_4800AN}

    Usage:
        config = MediaConfig(
            media_type=MediaSystemBackend.RAMULATOR,
            config_path="ramulator_config.yaml",
            scale_factor=1.0 / (1.2 * 10**9),
        )
        sys = RamulatorMediaSystem(config)
        metrics = sys.handler_mem_request(mem_req_list)
    """

    def __init__(self, config: MediaConfig):
        super().__init__(config)
        self._tx_bytes = config.granularity  # fallback
        self._io_frequency_mhz: float = 0.0  # auto-derived
        self._yaml_text = FALLBACK_YAML
        self._init_ramulator()

    def _init_ramulator(self):
        """Load YAML and derive tx_bytes from DRAM spec."""
        try:
            import yaml
            import ramulator  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "ramulator Python package is not installed. To install:\n"
                "  git submodule update --init media/ramulator_wrapper/ramulator2\n"
                "  pip install -e media/ramulator_wrapper/ramulator2\n"
                "If the C++ extension has not been built, build it first:\n"
                "  cmake -S media/ramulator_wrapper/ramulator2 -B media/ramulator_wrapper/ramulator2/build \\\n"
                "    -DCMAKE_BUILD_TYPE=Release -DRAMULATOR_PYTHON_BINDINGS=ON -DCMAKE_CXX_COMPILER=g++-14\n"
                "  cmake --build media/ramulator_wrapper/ramulator2/build -j$(sysctl -n hw.ncpu)"
            ) from e

        if self.config.config_path:
            if not os.path.isfile(self.config.config_path):
                raise FileNotFoundError(
                    f"Ramulator2 config file not found: {self.config.config_path}"
                )
            with open(self.config.config_path) as f:
                self._yaml_text = f.read()
            logger.info("Ramulator2 config loaded from %s", self.config.config_path)

        cfg = yaml.safe_load(self._yaml_text)
        dram_cfg = _find_dram(cfg)
        # tx_bytes via Ramulator2's C++ formula:
        #   get_tx_bytes() = internal_prefetch_size * channel_width / 8
        dram = _build_dram(dram_cfg)
        org, timing = dram.resolve()
        self._tx_bytes = dram.internal_prefetch_size * org["channel_width"] // 8

        # Auto-derive cycle frequency from DRAM spec.
        # cycle_frequency_mhz = rate / 2 * tick_multiplier
        #   rate: data rate in MT/s or Mbps (e.g., DDR5-4800 → 4800)
        #   tick_multiplier: ticks per tCK (1 for DDR, 2 for HBM3 half-CK)
        self._io_frequency_mhz = timing["rate"] / 2 * dram.tick_multiplier

        logger.info("Ramulator2 ready (tx_bytes=%d, cycle_freq=%.0f MHz).",
                    self._tx_bytes, self._io_frequency_mhz)

    # ------------------------------------------------------------------
    # Media request decomposition
    # ------------------------------------------------------------------

    def create_media_requests(
        self, mem_req_list: List["MemoryRequest"]
    ) -> List[MediaRequest]:
        """Decompose MemoryRequests into tx_bytes-sized MediaRequests."""
        g = self._tx_bytes
        result: List[MediaRequest] = []
        for mem_req in mem_req_list:
            obj = mem_req.memory_object
            # Align start down to tx boundary; cover the full range
            start = (obj.addr // g) * g
            end = obj.addr + obj.size
            num = math.ceil((end - start) / g)
            for i in range(num):
                result.append(MediaRequest(
                    addr=start + i * g,
                    req_type=obj.req_type.to_media_req_type(),
                ))
        return result

    # ------------------------------------------------------------------
    # Trace file
    # ------------------------------------------------------------------

    def _write_trace_file(self, media_reqs: List[MediaRequest], path: str) -> None:
        """Write LoadStoreTrace-format file: LD/ST <hex_addr>."""
        with open(path, "w") as f:
            for mr in media_reqs:
                f.write(f"{'LD' if mr.req_type == 0 else 'ST'} 0x{mr.addr:x}\n")

    # ------------------------------------------------------------------
    # Simulation
    # ------------------------------------------------------------------

    def handler_mem_request(
        self, mem_req_list: List["MemoryRequest"]
    ) -> MediaMetrics:
        """Run DRAM simulation and return metrics."""
        media_reqs = self.create_media_requests(mem_req_list)

        num_read = sum(1 for mr in media_reqs if mr.req_type == 0)
        num_write = len(media_reqs) - num_read

        cycles = self._run_sim(media_reqs) if media_reqs else 0

        metrics = MediaMetrics(
            num_read_requests=num_read,
            num_write_requests=num_write,
            num_other_requests=0,
            cycles=cycles,
            num_media_reqs=len(media_reqs),
            time=cycles / (self._io_frequency_mhz * 1e6) if self._io_frequency_mhz > 0 else 0.0,
        )
        self.system_metrics.update_from_media(metrics)
        return metrics

    def _run_sim(self, media_reqs: List[MediaRequest]) -> int:
        """Build components from YAML, run simulation, return cycles."""
        import yaml
        import ramulator

        # Write trace to .cache/ with random filename for concurrency safety
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), ".cache")
        os.makedirs(cache_dir, exist_ok=True)
        trace_path = os.path.join(cache_dir, f"trace_{uuid.uuid4().hex}.txt")
        self._write_trace_file(media_reqs, trace_path)

        try:
            cfg = yaml.safe_load(self._yaml_text)
            ms = cfg["MemorySystem"]

            # --- DRAM (presets + optional overrides) ---
            dram = _build_dram(_find_dram(cfg))

            # --- Controllers ---
            controllers = []
            ctrl_list = ms.get("Controllers") or ms.get("Controller")
            if isinstance(ctrl_list, dict):
                ctrl_list = [ctrl_list]
            elif not ctrl_list:
                ctrl_list = [{}]
            for c_cfg in ctrl_list:
                ctrl_cls = getattr(ramulator.controller, c_cfg.get("impl", "GenericDDR"))

                # Resolve sub-component classes from YAML or use defaults
                sched = _create_component(ramulator.scheduler, c_cfg.get("Scheduler", {}), "FRFCFS")
                rp = _create_component(ramulator.row_policy, c_cfg.get("RowPolicy", {}), "Open")
                am = _create_component(ramulator.addr_mapper, c_cfg.get("AddrMapper", {}), "RoBaRaCoCh")
                rm = _create_component(ramulator.refresh_manager, c_cfg.get("RefreshManager", {}), "NoRefresh")

                controllers.append(ctrl_cls(
                    dram=dram, scheduler=sched, row_policy=rp,
                    addr_mapper=am, refresh_manager=rm,
                ))

            # --- ChannelMapper ---
            cm_cfg = ms.get("ChannelMapper", {"impl": "CacheLineInterleave"})
            cm = _create_component(ramulator.channel_mapper, cm_cfg, "CacheLineInterleave")

            # --- MemorySystem ---
            ms_cls_name = ms.get("impl", "GenericDRAM")
            ms_cls = getattr(ramulator.memory_system, ms_cls_name)
            memory_system = ms_cls(
                clock_ratio=ms.get("clock_ratio", 1),
                controllers=controllers,
                channel_mapper=cm,
            )

            # --- Frontend (always LoadStoreTrace) ---
            frontend = ramulator.frontend.LoadStoreTrace(
                clock_ratio=cfg.get("Frontend", {}).get("clock_ratio", 1),
                path=trace_path,
            )

            # Run
            sim = ramulator.Simulation(frontend=frontend, memory_system=memory_system)
            sim.run()
            ctrl_stats = sim.stats["memory_system"]["controller"]
            if isinstance(ctrl_stats, list):
                # Multi-controller: parallel channels, take max cycles
                return max(int(cs.get("cycles", 0)) for cs in ctrl_stats)
            return int(ctrl_stats.get("cycles", 0))

        finally:
            try:
                os.remove(trace_path)
            except OSError:
                pass


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _create_component(module, cfg: dict, default_impl: str):
    """Create a Ramulator2 component from a config dict.

    Example: _create_component(ramulator.scheduler, {"impl": "FRFCFS"}, "FRFCFS")
    """
    cls = getattr(module, cfg.get("impl", default_impl))
    return cls()


def _find_dram(cfg: dict) -> dict:
    """Extract DRAM config from the YAML tree.

    DRAM may be at MemorySystem.DRAM or inside Controllers[0].DRAM.
    """
    ms = cfg.get("MemorySystem", {})
    # Check Controllers[0].DRAM first
    for c_key in ("Controllers",):
        c_list = ms.get(c_key, [])
        if isinstance(c_list, list) and c_list:
            dram = c_list[0].get("DRAM")
            if dram:
                return dram
        elif isinstance(c_list, dict):
            dram = c_list.get("DRAM")
            if dram:
                return dram
    # Fallback: MemorySystem.DRAM
    return ms.get("DRAM", {})


def _build_dram(dram_cfg: dict):
    """Create a DRAM component from config dict (presets + optional overrides).

    Keys in org/timing besides "preset" are passed as **kwargs to the DRAM
    constructor. The "channel" key is skipped — it is a system-level setting,
    not a DRAM parameter (configure via Controllers instead).
    """
    import ramulator
    dram_cls = getattr(ramulator.dram, dram_cfg["impl"])
    overrides = {}
    for section in ("org", "timing"):
        for k, v in dram_cfg.get(section, {}).items():
            if k not in ("preset", "channel"):
                overrides[k] = v
    return dram_cls(
        org_preset=dram_cfg["org"]["preset"],
        timing_preset=dram_cfg["timing"]["preset"],
        **overrides,
    )
