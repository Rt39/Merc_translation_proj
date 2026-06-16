# Merc Storia launcher — CMake build.
#
# Build (MSVC, typical):
#     cd launcher
#     cmake -S . -B build -A x64
#     cmake --build build --config Release
#
# Build (MinGW / Ninja, headless):
#     cmake -S . -B build -G "MinGW Makefiles" -DCMAKE_BUILD_TYPE=Release
#     cmake --build build
#
# Test:
#     cd build && ctest -C Release --output-on-failure
#
# Output:
#     build/launcher.exe          drop-in replacement for メルストM.exe
#     build/test_junction.exe     standalone junction tester
#
# Why a stand-alone CMake project (not just a build.cmd)?
#   - portable across compilers (MSVC, MinGW, Clang-cl)
#   - encodes the no-CRT-redist / no-console / UTF-8 source requirements
#     once, declaratively
#   - lets CI / packagers add this directory as a sub-project

cmake_minimum_required(VERSION 3.20)

# Note: the path containing Japanese characters at packager time is the LIVE
# install folder, not the source folder. The project lives wherever you check
# the repo out — ASCII paths are fine.
project(mercstoria_launcher LANGUAGES C RC)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# UTF-8 sources matter because both .c files embed Japanese L"..." literals
# (game folder name + Japanese filenames). MSVC needs /utf-8 to interpret the
# source bytes as UTF-8 and re-encode the L"" string into UTF-16 correctly.
if (MSVC)
    add_compile_options(/utf-8 /W3 /GS- /MP)
    # Statically link the CRT so the shipped exe needs no msvcrt redistributable.
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
else()
    # MinGW / Clang.
    add_compile_options(-Wall -finput-charset=UTF-8 -fexec-charset=UTF-16LE -municode)
    add_link_options(-static -municode)
endif()

# ----------------------------------------------------------------------------
# Shared junction library
# ----------------------------------------------------------------------------
add_library(junction STATIC src/junction.c)
target_include_directories(junction PUBLIC src)
target_link_libraries(junction PUBLIC kernel32)

# ----------------------------------------------------------------------------
# launcher.exe — GUI subsystem, no console flash on double-click
# ----------------------------------------------------------------------------
add_executable(launcher WIN32 src/launcher.c res/launcher.rc)
set_target_properties(launcher PROPERTIES
    OUTPUT_NAME "launcher"
    WIN32_EXECUTABLE TRUE
)
target_link_libraries(launcher PRIVATE junction user32 advapi32)
if (MSVC)
    target_link_options(launcher PRIVATE
        /SUBSYSTEM:WINDOWS
        /ENTRY:wWinMainCRTStartup
    )
endif()

# ----------------------------------------------------------------------------
# test_junction.exe — CONSOLE subsystem, runs from CMD/PowerShell
# ----------------------------------------------------------------------------
add_executable(test_junction test/test_junction.c)
target_link_libraries(test_junction PRIVATE junction)
if (MSVC)
    target_link_options(test_junction PRIVATE
        /SUBSYSTEM:CONSOLE
        /ENTRY:wmainCRTStartup
    )
endif()

# ----------------------------------------------------------------------------
# Tests (ctest)
# ----------------------------------------------------------------------------
enable_testing()
add_test(
    NAME junction_creation
    COMMAND ${CMAKE_COMMAND}
        -DTEST_EXE=$<TARGET_FILE:test_junction>
        -P ${CMAKE_CURRENT_SOURCE_DIR}/cmake/RunJunctionTest.cmake
)
