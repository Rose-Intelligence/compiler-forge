cmake_minimum_required(VERSION 3.16)
project(rangesum C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# CFLAGS/LDFLAGS arrive from the validator, which owns the optimization level and
# the sanitizer selection. A patch that changes them is manipulating the
# measurement, not optimizing the program.
if(DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")
endif()
if(DEFINED ENV{LDFLAGS})
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")
endif()

add_library(rangesum STATIC src/rangesum.c src/accumulate.c)
target_include_directories(rangesum PUBLIC include PRIVATE src)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE rangesum)

add_executable(test_rangesum tests/test_rangesum.c)
target_link_libraries(test_rangesum PRIVATE rangesum)

add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE rangesum)

enable_testing()
add_test(NAME unit COMMAND test_rangesum)
