cmake_minimum_required(VERSION 3.16)
project(mstats C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# CFLAGS arrives from the validator, which owns the optimization level and the
# sanitizer selection. The build definition must not override it: a patch that
# changes the flags it is measured under is manipulating the measurement, not
# optimizing the program.
if(DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")
endif()
if(DEFINED ENV{LDFLAGS})
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")
endif()

# Three translation units. A candidate is free to restructure across all of
# them; the glob it must stay inside is src/** and include/**.
add_library(mstats STATIC src/matrix.c src/vector.c src/stats.c)
target_include_directories(mstats PUBLIC include)
target_link_libraries(mstats PUBLIC m)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE mstats)

add_executable(test_mstats tests/test_mstats.c)
target_link_libraries(test_mstats PRIVATE mstats)

add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE mstats)

enable_testing()
add_test(NAME unit COMMAND test_mstats)
