#!/bin/bash

BASE_URL="http://packages.linuxmint.com/pool/main/m"
MIN_SIZE=13631488  # 13 MB in bytes

echo "=== Mint Backgrounds Downloader ==="
echo ""

# Step 1: Get all mint-backgrounds-* directories from the main page
echo "Finding mint-backgrounds directories..."
dirs=$(curl -s "$BASE_URL/" | ggrep -oE 'href="(mint-backgrounds-[^"]+)/"' | sed 's/href="//;s/\/"$//' | sort -u)

# Step 2: For each directory, find tar.gz files > 13MB and download them
download_large_tarballs() {
  local dir="$1"
  local page_content
  page_content=$(curl -s "$BASE_URL/$dir/")
  
  # Parse each .tar.gz line - format: filename followed by size like "16.5M"
  echo "$page_content" | ggrep -oE '[^"]+\.tar\.gz' | sort -u | while read -r tarball; do
    # Get file size from the page (look for the size after the filename)
    size_str=$(echo "$page_content" | ggrep -A1 "$tarball" | ggrep -oE '[0-9]+\.[0-9]+[MK]|[0-9]+[MK]' | head -1)
    
    # Convert size to bytes for comparison
    if [[ "$size_str" =~ ([0-9.]+)M ]]; then
      size_mb="${BASH_REMATCH[1]}"
      size_bytes=$(echo "$size_mb * 1048576" | bc | cut -d. -f1)
    elif [[ "$size_str" =~ ([0-9.]+)K ]]; then
      size_kb="${BASH_REMATCH[1]}"
      size_bytes=$(echo "$size_kb * 1024" | bc | cut -d. -f1)
    else
      size_bytes=0
    fi
    
    # Download if >= 13 MB
    if [ "$size_bytes" -ge "$MIN_SIZE" ]; then
      echo "  Downloading: $tarball ($size_str)"
      wget -q -c "$BASE_URL/$dir/$tarball"
    fi
  done
}

export -f download_large_tarballs
export BASE_URL MIN_SIZE

echo "Downloading tarballs > 13MB in parallel..."
echo "$dirs" | xargs -P 16 -I {} bash -c 'download_large_tarballs "$@"' _ {}

echo ""
echo "Download complete. Found $(ls -1 mint-backgrounds-*.tar.gz 2>/dev/null | wc -l | tr -d ' ') tarballs."

# Step 3: Create output directory
mkdir -p extracted_images

# Step 4: Extract images from each tarball in parallel
extract_tarball() {
  local tarball="$1"
  
  # Get package name: mint-backgrounds-xfce_2012.06.21.tar.gz -> xfce
  base_name=$(basename "$tarball" .tar.gz)
  package_name=$(echo "$base_name" | sed 's/^mint-backgrounds-//; s/_[0-9.].*$//')
  
  # Combine -extra variants into main directory (nadia-extra -> nadia)
  combined_name=$(echo "$package_name" | sed 's/-extra$//')
  
  echo "Processing: $base_name -> $combined_name"
  
  # Create temporary extraction directory
  temp_dir=$(mktemp -d)
  
  # Extract tarball
  tar -xzf "$tarball" -C "$temp_dir"
  
  # Create output directory
  mkdir -p "extracted_images/$combined_name"
  
  # Find and copy image files (excluding symlinks and screenshots)
  find "$temp_dir" \
    -type f \
    \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.svg" \) \
    ! -iname "*screenshot*" \
    -exec cp {} "extracted_images/$combined_name/" \;
  
  # Find and copy Credits files (rename to avoid overwrites)
  find "$temp_dir" \
    -type f \
    -iname "*Credits*" \
    -exec cp {} "extracted_images/$combined_name/Credits_$package_name" \;
  
  # Cleanup
  rm -rf "$temp_dir"
  
  echo "  ✓ Extracted to: extracted_images/$combined_name/"
}

export -f extract_tarball

echo ""
echo "Extracting images in parallel..."
find . -maxdepth 1 -name "mint-backgrounds-*.tar.gz" -print0 | xargs -0 -P 4 -I {} bash -c 'extract_tarball "$@"' _ {}

# Step 5: Cleanup tarballs
rm -f mint-backgrounds-*.tar.gz
echo ""
echo "Cleaned up tarballs!"

# Summary
echo ""
echo "=== Done! ==="
echo "Extracted directories:"
ls -1 extracted_images/ 2>/dev/null | wc -l | tr -d ' '
echo "Total images:"
find extracted_images -type f \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.png" -o -iname "*.svg" \) 2>/dev/null | wc -l | tr -d ' '
