cmake_minimum_required(VERSION 3.16)
project(csvsplit C)

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

add_library(csvsplit STATIC src/csvsplit.c)
target_include_directories(csvsplit PUBLIC include)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE csvsplit)

add_executable(test_csvsplit tests/test_csvsplit.c)
target_link_libraries(test_csvsplit PRIVATE csvsplit)

# The differential harness: reads a case from stdin and reports every observable
# the equivalence contract declares.
add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE csvsplit)

enable_testing()
add_test(NAME unit COMMAND test_csvsplit)
