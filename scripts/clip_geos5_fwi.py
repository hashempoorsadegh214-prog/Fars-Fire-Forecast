
#!/usr/bin/env python3

from pathlib import Path
import sys

import numpy as np
from netCDF4 import Dataset
import rasterio
from rasterio.transform import from_origin
from rasterio.mask import mask
import geopandas as gpd


# ============================================================
# SETTINGS
# ============================================================

NETCDF_FILE = Path(
    "data/raw/fwi/FWI.GEOS-5.Daily.Default.2026091700.20260920.nc"
)

FARS_GEOJSON = Path("fars.geojson")

OUTPUT_DIR = Path("data/processed/fwi")

OUTPUT_FILE = OUTPUT_DIR / "FWI_GEOS5_Fars_2026-09-20.tif"

VARIABLE_NAME = "GEOS-5_FWI"

CRS = "EPSG:4326"

NODATA = -9999.0


# ============================================================
# START
# ============================================================

print("=" * 70)
print("GEOS-5 FWI -> CLIPPED GEOTIFF")
print("=" * 70)

print(f"NetCDF : {NETCDF_FILE}")
print(f"Boundary: {FARS_GEOJSON}")
print(f"Output : {OUTPUT_FILE}")
print()


# ============================================================
# CHECK INPUT FILES
# ============================================================

if not NETCDF_FILE.exists():
    print("ERROR: NetCDF file does not exist.")
    print(NETCDF_FILE)
    sys.exit(1)

if not FARS_GEOJSON.exists():
    print("ERROR: fars.geojson does not exist.")
    print(FARS_GEOJSON)
    sys.exit(1)

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# READ NETCDF
# ============================================================

print("Reading NetCDF...")

with Dataset(NETCDF_FILE, "r") as nc:

    if VARIABLE_NAME not in nc.variables:
        print(f"ERROR: Variable '{VARIABLE_NAME}' not found.")

        print("Available variables:")
        for name in nc.variables.keys():
            print(f"  - {name}")

        sys.exit(1)

    if "lat" not in nc.variables:
        print("ERROR: lat variable not found.")
        sys.exit(1)

    if "lon" not in nc.variables:
        print("ERROR: lon variable not found.")
        sys.exit(1)

    lat = np.asarray(
        nc.variables["lat"][:],
        dtype=np.float64
    )

    lon = np.asarray(
        nc.variables["lon"][:],
        dtype=np.float64
    )

    fwi_var = nc.variables[VARIABLE_NAME]

    print(f"Variable  : {VARIABLE_NAME}")
    print(f"Dimensions: {fwi_var.dimensions}")
    print(f"Latitude  : {lat.size} cells")
    print(f"Longitude : {lon.size} cells")

    # --------------------------------------------------------
    # READ DATA
    # --------------------------------------------------------

    raw = fwi_var[:]

    print(f"Original array shape: {raw.shape}")

    # Convert masked array safely
    if np.ma.isMaskedArray(raw):
        data = raw.filled(np.nan).astype(np.float32)
    else:
        data = np.asarray(raw, dtype=np.float32)

    # --------------------------------------------------------
    # REMOVE TIME DIMENSION
    # --------------------------------------------------------

    if data.ndim == 3:

        if data.shape[0] != 1:
            print(
                "ERROR: Expected exactly one time slice."
            )
            print(f"Shape: {data.shape}")
            sys.exit(1)

        data = data[0, :, :]

    elif data.ndim == 2:

        pass

    else:

        print("ERROR: Unexpected FWI array dimensions.")
        print(f"Shape: {data.shape}")
        sys.exit(1)

    print(f"After time removal: {data.shape}")

    # --------------------------------------------------------
    # VERIFY DIMENSIONS
    # --------------------------------------------------------

    expected_shape = (lat.size, lon.size)

    if data.shape != expected_shape:

        print("ERROR: FWI array shape does not match lat/lon.")

        print(f"Expected: {expected_shape}")
        print(f"Actual  : {data.shape}")

        sys.exit(1)

    # --------------------------------------------------------
    # HANDLE FILL VALUE WITHOUT missing_value WARNING
    # --------------------------------------------------------

    fill_value = None

    if hasattr(fwi_var, "_FillValue"):

        try:
            fill_value = float(fwi_var._FillValue)
        except Exception:
            fill_value = None

    if fill_value is not None:

        print(f"FillValue: {fill_value}")

        data[data == fill_value] = np.nan

    # --------------------------------------------------------
    # VALID DATA
    # --------------------------------------------------------

    valid = np.isfinite(data)

    if not np.any(valid):
        print("ERROR: No valid FWI pixels found.")
        sys.exit(1)

    print()
    print("FWI statistics:")
    print(f"  Minimum: {float(np.nanmin(data)):.4f}")
    print(f"  Maximum: {float(np.nanmax(data)):.4f}")
    print(f"  Mean   : {float(np.nanmean(data)):.4f}")

    # --------------------------------------------------------
    # GRID RESOLUTION
    # --------------------------------------------------------

    if lat.size < 2 or lon.size < 2:
        print("ERROR: Invalid grid.")
        sys.exit(1)

    dy = float(np.median(np.abs(np.diff(lat))))
    dx = float(np.median(np.abs(np.diff(lon))))

    print()
    print(f"Latitude spacing : {dy}")
    print(f"Longitude spacing: {dx}")

    # --------------------------------------------------------
    # LONGITUDE ORDER
    # --------------------------------------------------------

    if lon[0] > lon[-1]:

        print("Reversing longitude direction.")

        lon = lon[::-1]

        data = data[:, ::-1]

    # --------------------------------------------------------
    # LATITUDE ORDER
    # --------------------------------------------------------

    if lat[0] > lat[-1]:

        print("Latitude is north -> south.")

        # Already compatible with GeoTIFF row order.
        # No flip required.

    else:

        print("Latitude is south -> north.")

        # GeoTIFF requires row 0 at northern edge.
        data = data[::-1, :]

    # --------------------------------------------------------
    # CALCULATE TRANSFORM
    # --------------------------------------------------------

    west = float(lon[0] - dx / 2.0)

    north = float(
        max(lat[0], lat[-1]) + dy / 2.0
    )

    transform = from_origin(
        west,
        north,
        dx,
        dy
    )

    print()
    print("Global raster:")
    print(f"  Width : {data.shape[1]}")
    print(f"  Height: {data.shape[0]}")
    print(f"  West  : {west}")
    print(f"  North : {north}")
    print(f"  dx    : {dx}")
    print(f"  dy    : {dy}")


# ============================================================
# TEMPORARY GLOBAL TIFF
# ============================================================

TEMP_FILE = OUTPUT_DIR / "_temp_geos5_fwi_global.tif"

print()
print("Writing temporary global GeoTIFF...")

write_data = np.where(
    np.isfinite(data),
    data,
    NODATA
).astype(np.float32)

profile = {
    "driver": "GTiff",
    "height": write_data.shape[0],
    "width": write_data.shape[1],
    "count": 1,
    "dtype": "float32",
    "crs": CRS,
    "transform": transform,
    "nodata": NODATA,
    "compress": "deflate",
    "predictor": 3,
    "tiled": True,
    "BIGTIFF": "IF_SAFER",
}

with rasterio.open(
    TEMP_FILE,
    "w",
    **profile
) as dst:

    dst.write(write_data, 1)

print(f"Temporary TIFF created: {TEMP_FILE}")


# ============================================================
# READ FARS BOUNDARY
# ============================================================

print()
print("Reading Fars boundary...")

fars = gpd.read_file(FARS_GEOJSON)

if fars.empty:
    print("ERROR: fars.geojson is empty.")
    sys.exit(1)

print(f"Features: {len(fars)}")

if fars.crs is None:

    print("WARNING: Boundary CRS is undefined.")
    print("Assuming EPSG:4326.")

    fars = fars.set_crs(CRS)

elif fars.crs.to_string() != CRS:

    print(
        f"Reprojecting boundary "
        f"from {fars.crs} to {CRS}"
    )

    fars = fars.to_crs(CRS)


geometries = []

for geom in fars.geometry:

    if geom is not None and not geom.is_empty:
        geometries.append(geom.__geo_interface__)


if not geometries:

    print("ERROR: No valid geometries found.")
    sys.exit(1)


# ============================================================
# CLIP
# ============================================================

print()
print("Clipping raster to Fars...")

with rasterio.open(TEMP_FILE) as src:

    clipped, clipped_transform = mask(
        src,
        geometries,
        crop=True,
        nodata=NODATA
    )

    clipped_profile = src.profile.copy()

    clipped_profile.update(
        {
            "height": clipped.shape[1],
            "width": clipped.shape[2],
            "transform": clipped_transform,
            "nodata": NODATA,
            "compress": "deflate",
            "predictor": 3,
            "tiled": True,
            "BIGTIFF": "IF_SAFER",
        }
    )

    with rasterio.open(
        OUTPUT_FILE,
        "w",
        **clipped_profile
    ) as dst:

        dst.write(clipped.astype(np.float32))


# ============================================================
# DELETE TEMP FILE
# ============================================================

if TEMP_FILE.exists():
    TEMP_FILE.unlink()


# ============================================================
# VERIFY FINAL TIFF
# ============================================================

print()
print("Verifying final TIFF...")

with rasterio.open(OUTPUT_FILE) as src:

    result = src.read(1)

    valid = (
        np.isfinite(result)
        & (result != src.nodata)
    )

    if not np.any(valid):

        print(
            "ERROR: Final TIFF contains "
            "no valid pixels."
        )

        sys.exit(1)

    print()
    print("=" * 70)
    print("SUCCESS")
    print("=" * 70)

    print(f"Output : {OUTPUT_FILE}")
    print(f"CRS    : {src.crs}")
    print(f"Width  : {src.width}")
    print(f"Height : {src.height}")
    print(f"Bounds : {src.bounds}")
    print(f"NoData : {src.nodata}")

    print()
    print("Final FWI statistics:")

    print(
        f"  Minimum: "
        f"{float(result[valid].min()):.4f}"
    )

    print(
        f"  Maximum: "
        f"{float(result[valid].max()):.4f}"
    )

    print(
        f"  Mean   : "
        f"{float(result[valid].mean()):.4f}"
    )

print()
print("Clipped GEOS-5 FWI GeoTIFF is ready.")
