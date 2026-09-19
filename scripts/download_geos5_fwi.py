#!/usr/bin/env python3
"""
FARS-GEOS5-FWI
Download NASA GEOS-5 forecast FWI NetCDF for Fars Province.

NASA structure:

https://portal.nccs.nasa.gov/datashare/GlobalFWI/
    v2.0/
        fwiCalcs.GEOS-5/
            Default/
                GEOS-5/
                    YYYY/
                        FORECAST_RUN/
                            FWI.GEOS-5.Daily.Default.FORECAST_RUN.YYYYMMDD.nc

Example:

2026/
    2026091700/
        FWI.GEOS-5.Daily.Default.2026091700.20260918.nc
        FWI.GEOS-5.Daily.Default.2026091700.20260919.nc
        FWI.GEOS-5.Daily.Default.2026091700.20260920.nc

Input:
    fars.geojson

Output:
    data/raw/fwi/FWI.GEOS-5.Daily.Default.FORECAST_RUN.YYYYMMDD.nc
"""

from pathlib import Path
from datetime import datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin
import os
import json
from zoneinfo import ZoneInfo
import re
import sys

import requests


# ============================================================
# SETTINGS
# ============================================================

NASA_BASE = (
    "https://portal.nccs.nasa.gov/datashare/GlobalFWI/"
    "v2.0/fwiCalcs.GEOS-5/Default/GEOS-5/"
)

FARS_GEOJSON = Path("fars.geojson")

OUTPUT_DIR = Path("data/raw/fwi")

HEADERS = {
    "User-Agent": "FARS-GEOS5-FWI/2.0"
}

TIMEOUT = 180


# ============================================================
# NASA DIRECTORY LINK PARSER
# ============================================================

class LinkParser(HTMLParser):
    """
    Extract href links from a NASA directory listing.
    """

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):

        if tag.lower() != "a":
            return

        attributes = dict(attrs)

        href = attributes.get("href")

        if href:
            self.links.append(href)


# ============================================================
# HTTP GET
# ============================================================

def get_url(url):
    """
    Download a NASA page/file response.
    """

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=TIMEOUT
        )

    except requests.RequestException as exc:

        print()
        print("ERROR: NASA request failed.")
        print(exc)

        sys.exit(1)

    return response


# ============================================================
# GET TARGET DATE
# ============================================================

def get_target_date():
    """
    Get target date.

    Priority:
        1. TARGET_DATE environment variable
        2. Tomorrow

    Expected format:
        YYYY-MM-DD
    """

    target = os.environ.get("TARGET_DATE")

    if target:

        try:

            date = datetime.strptime(
                target,
                "%Y-%m-%d"
            ).date()

            return date

        except ValueError:

            print()
            print(
                "ERROR: TARGET_DATE must use YYYY-MM-DD."
            )

            sys.exit(1)

    # --------------------------------------------------------
    # Default = tomorrow
    # --------------------------------------------------------

    return datetime.now(ZoneInfo("Asia/Tehran")).date() + timedelta(days=1)


# ============================================================
# CHECK FARS BOUNDARY
# ============================================================

def check_fars_boundary():
    """
    Verify that fars.geojson exists.

    The boundary will be used in the next processing
    stage for clipping the GEOS-5 raster to Fars.
    """

    print()
    print("=" * 80)
    print("CHECKING FARS BOUNDARY")
    print("=" * 80)

    if not FARS_GEOJSON.exists():

        print()
        print("ERROR: fars.geojson was not found.")
        print()
        print("Expected:")
        print(FARS_GEOJSON.resolve())

        sys.exit(1)

    if FARS_GEOJSON.stat().st_size == 0:

        print()
        print("ERROR: fars.geojson is empty.")

        sys.exit(1)

    print()
    print("Fars boundary:")
    print(FARS_GEOJSON)

    print()
    print(
        f"File size: "
        f"{FARS_GEOJSON.stat().st_size:,} bytes"
    )


# ============================================================
# READ NASA DIRECTORY
# ============================================================

def get_directory_links(url):
    """
    Read a NASA directory listing and return absolute links.
    """

    print()
    print("Checking NASA directory:")
    print(url)

    response = get_url(url)
    if response.status_code == 404:
        response.close()
        return []

    print(
        f"HTTP STATUS: {response.status_code}"
    )

    if response.status_code != 200:

        print()
        print(
            "ERROR: NASA directory is not available."
        )

        print()
        print(
            f"URL: {url}"
        )

        response.close()

        sys.exit(1)

    parser = LinkParser()

    parser.feed(
        response.text
    )

    response.close()

    links = []

    seen = set()

    for href in parser.links:

        full_url = urljoin(
            url,
            href
        )

        if not full_url.startswith(url):

            continue

        if full_url in seen:

            continue

        seen.add(full_url)

        links.append(full_url)

    return links


# ============================================================
# FIND FORECAST RUNS
# ============================================================

def find_forecast_runs(year):
    """
    Find NASA GEOS-5 forecast-run directories.

    Example:

        2026091700/
        2026091600/
        2026091500/

    Only directories matching:

        YYYYMMDDHH

    are accepted.
    """

    year_url = (
        f"{NASA_BASE}"
        f"{year}/"
    )

    print()
    print("=" * 80)
    print("SEARCHING NASA FORECAST RUNS")
    print("=" * 80)

    print()
    print("Year:")
    print(year)

    print()
    print("NASA year directory:")
    print(year_url)

    links = get_directory_links(
        year_url
    )

    forecast_runs = []

    pattern = re.compile(
        rf"^{year}\d{{6}}$"
    )

    for url in links:

        name = url.rstrip("/").split("/")[-1]

        if not pattern.match(name):

            continue

        if not url.endswith("/"):

            continue

        forecast_runs.append(
            (
                name,
                url
            )
        )

    # --------------------------------------------------------
    # Newest run first
    # --------------------------------------------------------

    forecast_runs.sort(
        key=lambda item: item[0],
        reverse=True
    )

    print()
    print(
        f"Forecast runs found: "
        f"{len(forecast_runs)}"
    )

    return forecast_runs


# ============================================================
# FIND FILE FOR TARGET DATE
# ============================================================

def find_forecast_file(
    target_date,
    forecast_runs
):
    """
    Search forecast runs from newest to oldest and find
    the NetCDF file for the requested target date.

    Example target:

        2026-09-19

    Expected filename:

        FWI.GEOS-5.Daily.Default.
        2026091700.
        20260919.nc
    """

    target_string = target_date.strftime(
        "%Y%m%d"
    )

    print()
    print("=" * 80)
    print("SEARCHING FOR TARGET FORECAST")
    print("=" * 80)

    print()
    print("Target date:")
    print(target_date)

    print()
    print("Target YYYYMMDD:")
    print(target_string)

    # --------------------------------------------------------
    # Search newest forecast run first
    # --------------------------------------------------------

    for run_name, run_url in forecast_runs:

        print()
        print("-" * 80)
        print("Checking forecast run:")
        print(run_name)

        links = get_directory_links(
            run_url
        )

        expected_filename = (
            "FWI.GEOS-5.Daily.Default."
            f"{run_name}."
            f"{target_string}.nc"
        )

        expected_url = urljoin(
            run_url,
            expected_filename
        )

        # ----------------------------------------------------
        # First check exact filename
        # ----------------------------------------------------

        for url in links:

            filename = (
                url.rstrip("/")
                .split("/")[-1]
            )

            if filename == expected_filename:

                print()
                print(
                    "TARGET FORECAST FOUND"
                )

                print()
                print(
                    f"Forecast run: {run_name}"
                )

                print(
                    f"Filename: {filename}"
                )

                print()
                print(
                    "URL:"
                )

                print(
                    url
                )

                return (
                    url,
                    filename,
                    run_name
                )

        # ----------------------------------------------------
        # Diagnostic search
        # ----------------------------------------------------

        candidates = []

        for url in links:

            filename = (
                url.rstrip("/")
                .split("/")[-1]
            )

            if (
                filename.lower().endswith(".nc")
                and target_string in filename
            ):

                candidates.append(
                    url
                )

        if candidates:

            print()
            print(
                "Target-date NetCDF candidates:"
            )

            for candidate in candidates:

                print(
                    f"  {candidate}"
                )

    # --------------------------------------------------------
    # Nothing found
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("ERROR: TARGET FORECAST NOT FOUND")
    print("=" * 80)

    print()
    print(
        f"Target date: {target_date}"
    )

    print()
    print(
        "NASA forecast runs were checked,"
        " but no matching NetCDF file was found."
    )

    print()
    print(
        "No random or alternative NASA path will be used."
    )

    sys.exit(1)


# ============================================================
# DOWNLOAD FILE
# ============================================================

def download_file(
    url,
    output_path
):
    """
    Download NASA NetCDF file safely.
    """

    print()
    print("=" * 80)
    print("DOWNLOADING NASA GEOS-5 FWI")
    print("=" * 80)

    print()
    print("URL:")
    print(url)

    print()
    print("OUTPUT:")
    print(output_path)

    print()

    try:

        response = requests.get(
            url,
            headers=HEADERS,
            stream=True,
            timeout=TIMEOUT
        )

    except requests.RequestException as exc:

        print()
        print(
            "ERROR: NASA download request failed."
        )

        print(exc)

        sys.exit(1)

    print(
        f"HTTP STATUS: "
        f"{response.status_code}"
    )

    if response.status_code != 200:

        print()
        print(
            "ERROR: NASA file is not available."
        )

        print()
        print(
            f"Requested URL: {url}"
        )

        response.close()

        sys.exit(1)

    content_type = response.headers.get(
        "Content-Type",
        ""
    )

    content_length = response.headers.get(
        "Content-Length"
    )

    print(
        f"Content-Type: {content_type}"
    )

    if content_length:

        try:

            print(
                "Content-Length: "
                f"{int(content_length):,} bytes"
            )

        except ValueError:

            print(
                f"Content-Length: {content_length}"
            )

    # --------------------------------------------------------
    # Prepare output directory
    # --------------------------------------------------------

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    temporary_path = output_path.with_suffix(
        output_path.suffix + ".part"
    )

    # --------------------------------------------------------
    # Download
    # --------------------------------------------------------

    try:

        with temporary_path.open(
            "wb"
        ) as file:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if chunk:

                    file.write(
                        chunk
                    )

        response.close()

        # ----------------------------------------------------
        # Verify temporary file
        # ----------------------------------------------------

        if not temporary_path.exists():

            print()
            print(
                "ERROR: Temporary file was not created."
            )

            sys.exit(1)

        size = temporary_path.stat().st_size

        if size == 0:

            temporary_path.unlink(
                missing_ok=True
            )

            print()
            print(
                "ERROR: Downloaded file is empty."
            )

            sys.exit(1)

        # ----------------------------------------------------
        # Move into final location
        # ----------------------------------------------------

        temporary_path.replace(
            output_path
        )

    except Exception as exc:

        response.close()

        temporary_path.unlink(
            missing_ok=True
        )

        print()
        print(
            "ERROR while saving NASA file:"
        )

        print(exc)

        sys.exit(1)

    print()
    print(
        "DOWNLOAD SUCCESSFUL"
    )

    print()
    print(
        f"File size: {size:,} bytes"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 80)
    print("FARS-GEOS5-FWI")
    print("NASA GEOS-5 FORECAST FWI DOWNLOAD")
    print("=" * 80)

    # --------------------------------------------------------
    # Check Fars boundary
    # --------------------------------------------------------

    check_fars_boundary()

    # --------------------------------------------------------
    # Target date
    # --------------------------------------------------------

    target_date = get_target_date()

    print()
    print("=" * 80)
    print("TARGET DATE")
    print("=" * 80)

    print()
    print(
        f"Target date: {target_date}"
    )

    # --------------------------------------------------------
    # Find forecast runs
    # --------------------------------------------------------

    forecast_runs = []
    for year in sorted({target_date.year, (target_date - timedelta(days=10)).year}, reverse=True):
        forecast_runs.extend(find_forecast_runs(year))
    forecast_runs = sorted((r for r in forecast_runs if
        target_date - timedelta(days=10) <= datetime.strptime(r[0], "%Y%m%d%H").date() <= target_date), reverse=True)[:20]

    # --------------------------------------------------------
    # Find exact forecast file
    # --------------------------------------------------------

    (
        nasa_url,
        filename,
        forecast_run
    ) = find_forecast_file(
        target_date,
        forecast_runs
    )

    # --------------------------------------------------------
    # Output
    # --------------------------------------------------------

    output_path = (
        OUTPUT_DIR /
        filename
    )

    print()
    print("=" * 80)
    print("SELECTED NASA FORECAST")
    print("=" * 80)

    print()
    print(
        f"Target date: {target_date}"
    )

    print(
        f"Forecast run: {forecast_run}"
    )

    print()
    print(
        "NASA URL:"
    )

    print(
        nasa_url
    )

    print()
    print(
        "Output:"
    )

    print(
        output_path
    )

    # --------------------------------------------------------
    # Avoid duplicate download
    # --------------------------------------------------------

    download_file(nasa_url, output_path)
    from netCDF4 import Dataset
    with Dataset(output_path) as nc:
        if "GEOS-5_FWI" not in nc.variables:
            raise ValueError("Downloaded file has no GEOS-5_FWI variable")
    (OUTPUT_DIR / "LATEST.json").write_text(json.dumps({
        "date": target_date.isoformat(), "netcdf": output_path.as_posix(),
        "forecast_run": forecast_run, "source_url": nasa_url
    }, indent=2) + "\n")

    # --------------------------------------------------------
    # Final verification
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("FINAL VERIFICATION")
    print("=" * 80)

    if not output_path.exists():

        print()
        print(
            "ERROR: Output file does not exist."
        )

        sys.exit(1)

    final_size = output_path.stat().st_size

    if final_size == 0:

        print()
        print(
            "ERROR: Output file is empty."
        )

        sys.exit(1)

    print()
    print(
        "NASA GEOS-5 FORECAST FWI FILE READY"
    )

    print()
    print(
        f"Date: {target_date}"
    )

    print(
        f"Forecast run: {forecast_run}"
    )

    print(
        f"File: {output_path}"
    )

    print(
        f"Size: {final_size:,} bytes"
    )

    print()
    print(
        "Fars boundary available:"
    )

    print(
        FARS_GEOJSON
    )

    print()
    print("=" * 80)
    print("DOWNLOAD COMPLETE")
    print("=" * 80)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
