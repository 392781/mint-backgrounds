#!/usr/bin/env python3
"""
Check for new mint-backgrounds packages and download/extract new versions.
Designed to run in GitHub Actions with minimal dependencies (just requests).
"""

import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import shutil
import time
import random
from pathlib import Path
from urllib.request import urlopen, urlretrieve, Request
from urllib.error import URLError, HTTPError
from datetime import datetime

BASE_URL = "http://packages.linuxmint.com/pool/main/m"
MIN_SIZE_MB = 13
MIN_SIZE_BYTES = MIN_SIZE_MB * 1024 * 1024
VERSIONS_FILE = "versions.json"
OUTPUT_DIR = "mint-backgrounds"

# Rate limiting settings
REQUEST_DELAY = 0.5  # seconds between requests
MAX_RETRIES = 3
RETRY_DELAY = 5  # seconds to wait before retry

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"


def load_versions() -> dict:
    """Load the versions.json file."""
    if os.path.exists(VERSIONS_FILE):
        with open(VERSIONS_FILE, "r") as f:
            return json.load(f)
    return {"packages": {}, "last_checked": None}


def save_versions(data: dict):
    """Save the versions.json file with summary statistics."""
    data["last_checked"] = datetime.utcnow().isoformat()
    
    # Calculate summary statistics
    data["total_packages"] = len(data.get("packages", {}))
    
    # Count images and calculate total size
    total_images = 0
    total_size_bytes = 0
    image_extensions = {'.jpg', '.jpeg', '.png', '.svg'}
    
    # Mint release names mapped to version numbers
    mint_releases = {
        'katya': 11, 'lisa': 12, 'maya': 13, 'nadia': 14, 'olivia': 15,
        'petra': 16, 'qiana': 17, 'rafaela': 17.1, 'rebecca': 17.2,
        'rosa': 17.3, 'sarah': 18, 'serena': 18.1, 'sonya': 18.2,
        'sylvia': 18.3, 'tara': 19, 'tessa': 19.1, 'tina': 19.2,
        'tricia': 19.3, 'ulyana': 20, 'ulyssa': 20.1, 'uma': 20.2,
        'una': 20.3, 'vanessa': 21, 'vera': 21.1, 'victoria': 21.2,
        'virginia': 21.3, 'wilma': 22
    }
    
    latest_name = None
    latest_version = 0
    
    if os.path.exists(OUTPUT_DIR):
        for item in os.listdir(OUTPUT_DIR):
            item_path = os.path.join(OUTPUT_DIR, item)
            if os.path.isdir(item_path):
                name = item.lower()
                if name in mint_releases and mint_releases[name] > latest_version:
                    latest_version = mint_releases[name]
                    latest_name = item.capitalize()
                
                # Count images in this directory
                for file in os.listdir(item_path):
                    ext = os.path.splitext(file)[1].lower()
                    if ext in image_extensions:
                        total_images += 1
                        filepath = os.path.join(item_path, file)
                        total_size_bytes += os.path.getsize(filepath)
    
    data["total_images"] = total_images
    data["total_size_mb"] = round(total_size_bytes / (1024 * 1024), 1)
    data["latest_mint_release"] = latest_name
    data["latest_mint_version"] = latest_version
    
    with open(VERSIONS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def fetch_page(url: str) -> str:
    """Fetch a URL and return its content with rate limiting and retries."""
    for attempt in range(MAX_RETRIES):
        try:
            # Add delay to avoid rate limiting
            time.sleep(REQUEST_DELAY + random.uniform(0, 0.5))
            
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except HTTPError as e:
            if e.code == 429 or e.code == 503:  # Rate limited or service unavailable
                wait_time = RETRY_DELAY * (attempt + 1)
                print(f"  Rate limited. Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
            else:
                print(f"Error fetching {url}: {e}")
                return ""
        except URLError as e:
            if attempt < MAX_RETRIES - 1:
                wait_time = RETRY_DELAY * (attempt + 1)
                print(f"  Connection error. Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
            else:
                print(f"Error fetching {url}: {e}")
                return ""
    return ""


def get_package_directories() -> list[str]:
    """Get all mint-backgrounds-* directories from the main page."""
    content = fetch_page(f"{BASE_URL}/")
    # Match: href="mint-backgrounds-xxxxx/"
    pattern = r'href="(mint-backgrounds-[^"]+)/"'
    dirs = re.findall(pattern, content)
    return sorted(set(dirs))


def parse_size(size_str: str) -> int:
    """Convert size string like '16.5M' or '500K' to bytes."""
    if not size_str:
        return 0
    
    match = re.match(r"([0-9.]+)([MKG])", size_str)
    if not match:
        return 0
    
    value = float(match.group(1))
    unit = match.group(2)
    
    multipliers = {"K": 1024, "M": 1024**2, "G": 1024**3}
    return int(value * multipliers.get(unit, 1))


def get_tarballs_for_package(package_dir: str) -> list[dict]:
    """Get all tar.gz files for a package directory with their sizes."""
    content = fetch_page(f"{BASE_URL}/{package_dir}/")
    tarballs = []
    
    # Find all .tar.gz files
    tarball_pattern = r'href="([^"]+\.tar\.gz)"'
    tarball_names = re.findall(tarball_pattern, content)
    
    for tarball in set(tarball_names):
        # Try to find the size in the page content
        # Format: filename followed by size like "16.5M"
        size_pattern = rf"{re.escape(tarball)}.*?([0-9.]+[MKG])"
        size_match = re.search(size_pattern, content, re.DOTALL)
        size_str = size_match.group(1) if size_match else "0M"
        size_bytes = parse_size(size_str)
        
        tarballs.append({
            "name": tarball,
            "url": f"{BASE_URL}/{package_dir}/{tarball}",
            "size_bytes": size_bytes,
            "size_str": size_str
        })
    
    return tarballs


def extract_version_info(tarball_name: str) -> tuple[str, str]:
    """
    Extract package name and version from tarball name.
    mint-backgrounds-nadia_1.4.tar.gz -> ('nadia', '1.4')
    mint-backgrounds-xfce_2012.06.21.tar.gz -> ('xfce', '2012.06.21')
    """
    base = tarball_name.replace(".tar.gz", "")
    base = base.replace("mint-backgrounds-", "")
    
    # Split on underscore to get name and version
    if "_" in base:
        parts = base.split("_", 1)
        return parts[0], parts[1]
    return base, "unknown"


def normalize_package_name(name: str) -> str:
    """Normalize package name (combine -extra variants)."""
    return name.replace("-extra", "")


def download_and_extract(tarball_info: dict, output_base: str) -> bool:
    """Download a tarball and extract images from it."""
    tarball_name = tarball_info["name"]
    url = tarball_info["url"]
    
    package_name, version = extract_version_info(tarball_name)
    combined_name = normalize_package_name(package_name)
    
    print(f"  Downloading: {tarball_name} ({tarball_info['size_str']})")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tarball_path = os.path.join(tmpdir, tarball_name)
        extract_dir = os.path.join(tmpdir, "extracted")
        os.makedirs(extract_dir)
        
        try:
            # Add delay before download
            time.sleep(REQUEST_DELAY + random.uniform(0, 0.5))
            
            # Download with User-Agent
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=60) as response:
                with open(tarball_path, 'wb') as f:
                    f.write(response.read())
            
            # Extract
            with tarfile.open(tarball_path, "r:gz") as tar:
                tar.extractall(extract_dir)
            
            # Create output directory
            output_dir = os.path.join(output_base, combined_name)
            os.makedirs(output_dir, exist_ok=True)
            
            # Find and copy images (excluding symlinks and screenshots)
            image_extensions = {".jpg", ".jpeg", ".png", ".svg"}
            for root, dirs, files in os.walk(extract_dir):
                for file in files:
                    file_lower = file.lower()
                    file_path = os.path.join(root, file)
                    
                    # Skip symlinks
                    if os.path.islink(file_path):
                        continue
                    
                    # Check if it's an image
                    if any(file_lower.endswith(ext) for ext in image_extensions):
                        # Skip screenshots
                        if "screenshot" in file_lower:
                            continue
                        shutil.copy2(file_path, os.path.join(output_dir, file))
                    
                    # Copy Credits files with unique names
                    elif "credits" in file_lower:
                        credits_name = f"Credits_{package_name}"
                        shutil.copy2(file_path, os.path.join(output_dir, credits_name))
            
            print(f"    ✓ Extracted to: {output_dir}/")
            return True
            
        except Exception as e:
            print(f"    ✗ Error: {e}")
            return False


def check_and_download():
    """Main function: check for updates and download new packages."""
    print("=== Mint Backgrounds Updater ===\n")
    
    versions = load_versions()
    existing_packages = versions.get("packages", {})
    
    # Get all package directories
    print("Finding mint-backgrounds directories...")
    package_dirs = get_package_directories()
    print(f"Found {len(package_dirs)} package directories\n")
    
    new_packages = []
    updated_packages = []
    
    # Check each package for new/updated tarballs
    print("Checking for new/updated packages...")
    for pkg_dir in package_dirs:
        tarballs = get_tarballs_for_package(pkg_dir)
        
        for tarball in tarballs:
            # Skip small files
            if tarball["size_bytes"] < MIN_SIZE_BYTES:
                continue
            
            tarball_name = tarball["name"]
            package_name, version = extract_version_info(tarball_name)
            
            # Check if we already have this exact version
            pkg_key = f"{package_name}_{version}"
            if pkg_key in existing_packages:
                continue
            
            # This is a new or updated package
            if package_name in [extract_version_info(k)[0] for k in existing_packages]:
                updated_packages.append(tarball)
                print(f"  Updated: {tarball_name}")
            else:
                new_packages.append(tarball)
                print(f"  New: {tarball_name}")
    
    all_new = new_packages + updated_packages
    
    if not all_new:
        print("\nNo new packages found. Everything is up to date!")
        save_versions(versions)
        return False
    
    print(f"\nFound {len(all_new)} new/updated packages to download.\n")
    
    # Create output directory
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    # Download and extract each new package
    print("Downloading and extracting...")
    for tarball in all_new:
        if download_and_extract(tarball, OUTPUT_DIR):
            package_name, version = extract_version_info(tarball["name"])
            pkg_key = f"{package_name}_{version}"
            existing_packages[pkg_key] = {
                "name": tarball["name"],
                "version": version,
                "size": tarball["size_str"],
                "downloaded_at": datetime.utcnow().isoformat()
            }
    
    # Save updated versions
    versions["packages"] = existing_packages
    save_versions(versions)
    
    # Summary
    print(f"\n=== Done! ===")
    print(f"New packages: {len(new_packages)}")
    print(f"Updated packages: {len(updated_packages)}")
    
    return True


def has_updates() -> bool:
    """Check if there are any updates available (without downloading)."""
    versions = load_versions()
    existing_packages = versions.get("packages", {})
    
    package_dirs = get_package_directories()
    
    for pkg_dir in package_dirs:
        tarballs = get_tarballs_for_package(pkg_dir)
        
        for tarball in tarballs:
            if tarball["size_bytes"] < MIN_SIZE_BYTES:
                continue
            
            package_name, version = extract_version_info(tarball["name"])
            pkg_key = f"{package_name}_{version}"
            
            if pkg_key not in existing_packages:
                return True
    
    return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--check-only":
        # Just check for updates, don't download
        if has_updates():
            print("Updates available")
            sys.exit(0)
        else:
            print("No updates")
            sys.exit(0)
    else:
        # Check and download
        had_updates = check_and_download()
        sys.exit(0)
