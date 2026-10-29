#!/bin/sh
# Builds Fizzy.app into mac/build/. Needs Xcode (not just the command line tools).
set -e
cd "$(dirname "$0")"
xcodebuild -quiet -project Fizzy.xcodeproj -scheme Fizzy -configuration Release \
    -destination "generic/platform=macOS" -derivedDataPath build/DerivedData build
rm -rf build/Fizzy.app
cp -R build/DerivedData/Build/Products/Release/Fizzy.app build/Fizzy.app
echo "Built $(pwd)/build/Fizzy.app"
