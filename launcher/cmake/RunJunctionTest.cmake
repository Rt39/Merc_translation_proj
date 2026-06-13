# ctest driver: create a temp dir, run test_junction, verify the link is a
# reparse point that resolves to the target, then clean up.

if (NOT TEST_EXE)
    message(FATAL_ERROR "TEST_EXE not set")
endif()

set(scratch "$ENV{TEMP}/mercstoria_junction_test")
file(REMOVE_RECURSE "${scratch}")
file(MAKE_DIRECTORY "${scratch}/target")
file(WRITE "${scratch}/target/marker.txt" "junction test marker\n")

set(link "${scratch}/link")
file(REMOVE_RECURSE "${link}")

# Use file(TO_NATIVE_PATH) so paths on Windows are passed with backslashes.
file(TO_NATIVE_PATH "${link}" link_native)
file(TO_NATIVE_PATH "${scratch}/target" target_native)

execute_process(
    COMMAND "${TEST_EXE}" "${link_native}" "${target_native}"
    RESULT_VARIABLE rv
    OUTPUT_VARIABLE out
    ERROR_VARIABLE  err
)
message(STATUS "test_junction stdout: ${out}")
message(STATUS "test_junction stderr: ${err}")
if (NOT rv EQUAL 0)
    message(FATAL_ERROR "test_junction returned ${rv}")
endif()

# Resolution check: read marker through the junction.
if (NOT EXISTS "${link}/marker.txt")
    message(FATAL_ERROR "junction did not resolve to target (no marker.txt)")
endif()

# Clean up — remove the junction itself first (RemoveDirectory unlinks reparse
# points without touching their target), then the target dir.
file(REMOVE_RECURSE "${scratch}")
message(STATUS "junction_creation OK")
