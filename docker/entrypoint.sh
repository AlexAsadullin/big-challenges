#!/bin/sh
set -e

# First start: build the 10k-book catalog; fall back to the small sample if the download fails
if [ ! -f "$CSV_PATH" ]; then
    echo "No catalog at $CSV_PATH, building it..."
    python /app/scripts/build_dataset.py "$CSV_PATH" \
        || { echo "Dataset download failed, using the 40-book sample"; python /app/scripts/make_sample_csv.py "$CSV_PATH"; }
fi

exec "$@"
