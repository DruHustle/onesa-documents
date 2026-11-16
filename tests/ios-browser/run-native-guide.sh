#!/usr/bin/env bash
# Retry only the simulator's pre-test accessibility startup failure.
set -euo pipefail
work=$1
device=$2

run_test() {
  local attempt=$1
  xcodebuild test -project "$work/host/InstalledClient.xcodeproj" -scheme InstalledClient \
    -destination "platform=iOS Simulator,id=$device" -parallel-testing-enabled NO \
    CODE_SIGNING_ALLOWED=YES CODE_SIGN_IDENTITY=- -derivedDataPath "$work/build" \
    -resultBundlePath "$work/results-$attempt.xcresult" 2>&1 | tee "$work/xcode-$attempt.log"
}

if run_test 1; then
  exit 0
else
  status=$?
fi

if ! grep -Fq 'Failed to initialize for UI testing: Error Domain=XCTDaemonErrorDomain Code=18 "Timed out waiting for AX loaded notification"' "$work/xcode-1.log" ||
   grep -qE 'PUBLIC_GUIDE_NATIVE_|Test Case .* started' "$work/xcode-1.log"; then
  exit "$status"
fi

xcrun simctl shutdown "$device"
xcrun simctl boot "$device"
xcrun simctl bootstatus "$device" -b
xcrun simctl openurl "$device" https://docs.onesa.eu/
run_test 2
