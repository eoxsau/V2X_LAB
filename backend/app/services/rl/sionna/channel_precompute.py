"""
Sionna Offline Channel Map Pre-computation.

For each Korean training region:
  1. Load building geometry (GeoJSON from PostGIS or cache)
  2. Construct a Sionna RT (ray-tracing) scene with buildings as meshes
  3. Place virtual TX at each BS position
  4. Run OFDM channel coefficient simulation on a 2D grid
  5. Compute SINR_dB at each grid point from channel matrices
  6. Save as .npy: (lat_grid, lng_grid, sinr_db_per_bs)

Output format per region (saved to data/sionna_maps/<osm_id>.npz):
  lat_grid  : float32 [H]       — row latitudes
  lng_grid  : float32 [W]       — column longitudes
  sinr_db   : float32 [N_bs, H, W]  — SINR_dB per BS

Usage (on A100):
    from app.services.rl.sionna.channel_precompute import SionnaPrecomputer
    pc = SionnaPrecomputer(gpu_id=0)
    pc.run_all_regions(train_region_ids)

Reference:
  [1] Hoydis, J. et al., "Sionna: An Open-Source Library for Next-Generation
      Physical Layer Research", arXiv:2203.11189, 2022.
  [2] 3GPP TR 38.901 V16.1.0 (2019-12): "Study on channel model for
      frequencies from 0.5 to 100 GHz", §7.4 (ray-based models).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import numpy as np

# ── Paths ─────────────────────────────────────────────────────────────────────
_BASE = Path(__file__).parent.parent.parent.parent.parent  # backend/
_MAP_DIR = _BASE / "data" / "sionna_maps"

# ── Grid resolution ───────────────────────────────────────────────────────────
_GRID_RES_M = 10.0     # 10-metre resolution grid (≈ half a lane width)

# ── Sionna simulation parameters ──────────────────────────────────────────────
_CARRIER_FREQ_HZ  = 3.5e9      # 5G NR n78 band (3.5 GHz)
_TX_POWER_DBM     = 46.0       # BS antenna power (3GPP TR 38.901 §A.2.1)
_TX_HEIGHT_M      = 25.0       # BS antenna height (rooftop)
_NOISE_FIGURE_DB  = 7.0        # UE noise figure
_BANDWIDTH_HZ     = 100e6      # 100 MHz (5G NR FR1 max)
_N_RX_ANTENNAS    = 1          # single-antenna UE

try:
    import sionna
    import tensorflow as tf
    import sionna.rt as srt
    _SIONNA_AVAILABLE = True
except ImportError:
    _SIONNA_AVAILABLE = False


class SionnaPrecomputer:
    """
    Offline Sionna ray-tracing channel map generator.

    Requires:
        pip install sionna tensorflow
        CUDA-capable GPU (A100 recommended)

    Parameters
    ----------
    gpu_id : int
        GPU index to use (0 on single-GPU machines).
    map_dir : Path | None
        Output directory. Default: data/sionna_maps/.
    """

    def __init__(
        self,
        gpu_id: int = 0,
        map_dir: Optional[Path] = None,
        grid_res_m: float = _GRID_RES_M,
    ) -> None:
        if not _SIONNA_AVAILABLE:
            raise ImportError(
                "Sionna requires:\n"
                "  pip install sionna tensorflow\n"
                "  NVIDIA GPU with CUDA 11.8+"
            )
        self._map_dir = map_dir or _MAP_DIR
        self._map_dir.mkdir(parents=True, exist_ok=True)
        self._grid_res_m = grid_res_m

        # Pin to one GPU to avoid TF multi-GPU issues
        gpus = tf.config.list_physical_devices("GPU")
        if gpus:
            tf.config.set_visible_devices(gpus[gpu_id], "GPU")
            tf.config.experimental.set_memory_growth(gpus[gpu_id], True)

    # ── Public ────────────────────────────────────────────────────────────────
    def run_all_regions(
        self,
        region_tasks: list[dict],   # list of {osm_id, bbox, buildings_geojson, bs_positions}
        overwrite: bool = False,
        verbose: bool = True,
    ) -> None:
        """
        Pre-compute channel maps for all training regions.

        Parameters
        ----------
        region_tasks : list[dict]
            Each dict must have:
              osm_id          : int
              bbox            : {s, n, w, e}
              buildings_geojson: str  (GeoJSON FeatureCollection)
              bs_positions    : list[{id, lat, lng}]
        """
        total = len(region_tasks)
        for i, rt in enumerate(region_tasks):
            osm_id = rt["osm_id"]
            out_path = self._map_dir / f"{osm_id}.npz"
            if out_path.exists() and not overwrite:
                if verbose:
                    print(f"[Sionna] {i+1}/{total} osm_id={osm_id} — exists, skipping")
                continue
            try:
                self._compute_region(rt, out_path, verbose=verbose)
                if verbose:
                    print(f"[Sionna] {i+1}/{total} osm_id={osm_id} — saved {out_path.name}")
            except Exception as e:
                print(f"[Sionna] {i+1}/{total} osm_id={osm_id} — ERROR: {e}")

    def compute_one(
        self,
        bbox: dict,
        buildings_geojson: str,
        bs_positions: list[dict],
    ) -> dict:
        """
        Compute channel map for a single bbox + building set.

        Returns dict with lat_grid, lng_grid, sinr_db [N_bs, H, W].
        """
        lat_grid, lng_grid = self._make_grid(bbox)
        scene = self._build_scene(bbox, buildings_geojson, bs_positions)
        sinr_db = self._run_simulation(scene, lat_grid, lng_grid, bs_positions)
        return {
            "lat_grid": lat_grid,
            "lng_grid": lng_grid,
            "sinr_db":  sinr_db,
        }

    # ── Internal ──────────────────────────────────────────────────────────────
    def _compute_region(self, rt: dict, out_path: Path, verbose: bool = True) -> None:
        result = self.compute_one(
            bbox=rt["bbox"],
            buildings_geojson=rt.get("buildings_geojson", "{}"),
            bs_positions=rt.get("bs_positions", []),
        )
        np.savez_compressed(
            out_path,
            lat_grid=result["lat_grid"].astype(np.float32),
            lng_grid=result["lng_grid"].astype(np.float32),
            sinr_db=result["sinr_db"].astype(np.float32),
            meta=json.dumps({
                "osm_id": rt.get("osm_id"),
                "carrier_freq_hz": _CARRIER_FREQ_HZ,
                "grid_res_m": self._grid_res_m,
            }),
        )

    def _make_grid(self, bbox: dict) -> tuple[np.ndarray, np.ndarray]:
        """Create lat/lng grid with _GRID_RES_M spacing."""
        deg_per_m_lat = 1.0 / 111_320.0
        deg_per_m_lng = 1.0 / (111_320.0 * np.cos(np.radians(
            (bbox["s"] + bbox["n"]) / 2
        )))
        lat_step = deg_per_m_lat * self._grid_res_m
        lng_step = deg_per_m_lng * self._grid_res_m
        lat_grid = np.arange(bbox["s"], bbox["n"], lat_step, dtype=np.float32)
        lng_grid = np.arange(bbox["w"], bbox["e"], lng_step, dtype=np.float32)
        return lat_grid, lng_grid

    def _build_scene(
        self,
        bbox: dict,
        buildings_geojson: str,
        bs_positions: list[dict],
    ) -> "srt.Scene":
        """
        Create a Sionna ray-tracing scene from building polygons.

        Buildings are extruded to their actual height (or a default of 12 m).
        Each BS is a single-element antenna array at _TX_HEIGHT_M.
        """
        scene = srt.Scene()
        scene.frequency = _CARRIER_FREQ_HZ

        # Load buildings from GeoJSON and add as mesh objects
        try:
            geojson = json.loads(buildings_geojson)
            features = geojson.get("features", [])
        except (json.JSONDecodeError, AttributeError):
            features = []

        # Origin point for local Cartesian coordinates
        lat0 = (bbox["s"] + bbox["n"]) / 2
        lng0 = (bbox["w"] + bbox["e"]) / 2

        for feat in features:
            props = feat.get("properties", {})
            geom  = feat.get("geometry", {})
            height_m = float(props.get("height") or props.get("building:height") or 12.0)
            if geom.get("type") == "Polygon":
                coords = geom["coordinates"][0]
                self._add_building_to_scene(scene, coords, height_m, lat0, lng0)

        # Add BS transmitters
        for i, bs in enumerate(bs_positions):
            x, y = self._latlong_to_xy(bs["lat"], bs["lng"], lat0, lng0)
            scene.add(srt.Transmitter(
                name=f"bs_{i}",
                position=[x, y, _TX_HEIGHT_M],
                orientation=[0, 0, 0],
            ))

        return scene

    def _add_building_to_scene(
        self,
        scene: "srt.Scene",
        coords: list,
        height_m: float,
        lat0: float,
        lng0: float,
    ) -> None:
        """Extrude a polygon footprint into a 3-D mesh and add to scene."""
        import tensorflow as tf

        verts = []
        for lng, lat in coords:
            x, y = self._latlong_to_xy(lat, lng, lat0, lng0)
            verts.append([x, y])

        if len(verts) < 3:
            return

        # Bottom and top rings
        bottom = [[v[0], v[1], 0.0] for v in verts]
        top    = [[v[0], v[1], height_m] for v in verts]

        vertices = bottom + top
        n = len(verts)
        faces = []
        # Side faces
        for i in range(n - 1):
            faces.append([i, i + 1, i + n])
            faces.append([i + 1, i + 1 + n, i + n])
        # Roof (fan triangulation — approximate for convex polygons)
        for i in range(1, n - 1):
            faces.append([n, n + i, n + i + 1])

        mesh = srt.Mesh(
            vertices=tf.constant(vertices, dtype=tf.float32),
            indices=tf.constant(faces, dtype=tf.int32),
            material=srt.ITUMaterial("concrete"),
        )
        scene.add(mesh)

    def _run_simulation(
        self,
        scene: "srt.Scene",
        lat_grid: np.ndarray,
        lng_grid: np.ndarray,
        bs_positions: list[dict],
    ) -> np.ndarray:
        """
        Run ray tracing and compute SINR_dB at each grid point.

        Returns sinr_db [N_bs, H, W].
        """
        import tensorflow as tf

        H, W = len(lat_grid), len(lng_grid)
        N_bs = len(bs_positions)
        sinr_db = np.full((N_bs, H, W), -100.0, dtype=np.float32)

        lat0 = (lat_grid[0] + lat_grid[-1]) / 2
        lng0 = (lng_grid[0] + lng_grid[-1]) / 2

        # Build receiver grid
        rx_positions = []
        for h in range(H):
            for w in range(W):
                x, y = self._latlong_to_xy(float(lat_grid[h]), float(lng_grid[w]), lat0, lng0)
                rx_positions.append([x, y, 1.5])  # UE height 1.5m

        rx_pos_tf = tf.constant(rx_positions, dtype=tf.float32)

        # Run coverage map (Sionna built-in)
        try:
            coverage = scene.coverage_map(
                rx_orientation=[0, 0, 0],
                max_depth=5,
                diffraction=True,
                scattering=True,
                edge_diffraction=True,
                num_samples=int(1e6),
                cm_center=[0.0, 0.0, 0.0],
                cm_orientation=[0.0, 0.0, 0.0],
                cm_size=[
                    float(lng_grid[-1] - lng_grid[0]) * 111_320,
                    float(lat_grid[-1] - lat_grid[0]) * 111_320,
                ],
                cm_cell_size=[self._grid_res_m, self._grid_res_m],
            )

            path_gain_db = 10.0 * np.log10(np.maximum(
                coverage.as_tensor().numpy()[0], 1e-20
            ))  # [N_bs, H, W]

            tx_power_w = 10 ** ((_TX_POWER_DBM - 30) / 10)
            noise_w = 10 ** ((_NOISE_FIGURE_DB - 30) / 10) * 1.38e-23 * 290 * _BANDWIDTH_HZ

            for b in range(N_bs):
                # Simple single-BS SINR (interference from other BSs at 10 dB below)
                signal_w = tx_power_w * 10 ** (path_gain_db[b] / 10)
                # Aggregate interference from other BSs
                interference_w = sum(
                    tx_power_w * 10 ** (path_gain_db[ob] / 10) * 0.1
                    for ob in range(N_bs) if ob != b
                )
                sinr = signal_w / (interference_w + noise_w)
                sinr_db[b] = 10.0 * np.log10(np.maximum(sinr, 1e-10))

        except Exception as e:
            print(f"  [Sionna] coverage_map error: {e} — using free-space fallback")
            sinr_db = self._freespace_fallback(lat_grid, lng_grid, bs_positions)

        return sinr_db

    def _freespace_fallback(
        self,
        lat_grid: np.ndarray,
        lng_grid: np.ndarray,
        bs_positions: list[dict],
    ) -> np.ndarray:
        """Free-space path loss when Sionna simulation fails."""
        import math as _math
        H, W = len(lat_grid), len(lng_grid)
        N_bs = len(bs_positions)
        sinr_db = np.zeros((N_bs, H, W), dtype=np.float32)
        c = 3e8
        wl = c / _CARRIER_FREQ_HZ
        pl0 = 20 * _math.log10(4 * _math.pi / wl)

        for b, bs in enumerate(bs_positions):
            for h in range(H):
                for w in range(W):
                    dlat = (float(lat_grid[h]) - bs["lat"]) * 111_320
                    dlng = (float(lng_grid[w]) - bs["lng"]) * 111_320
                    d = max(_math.sqrt(dlat**2 + dlng**2), 1.0)
                    pl = pl0 + 20 * _math.log10(d)
                    sinr_db[b, h, w] = _TX_POWER_DBM - pl - _NOISE_FIGURE_DB - 10 * _math.log10(_BANDWIDTH_HZ)
        return sinr_db

    @staticmethod
    def _latlong_to_xy(
        lat: float, lng: float, lat0: float, lng0: float
    ) -> tuple[float, float]:
        """Convert lat/lng to local Cartesian X/Y (metres)."""
        x = (lng - lng0) * 111_320 * np.cos(np.radians(lat0))
        y = (lat - lat0) * 111_320
        return float(x), float(y)
