cmake_minimum_required(VERSION 3.16)
project(sortedindex C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# CFLAGS arrives from the validator, which owns the optimization level and the
# sanitizer selection. A patch that changes the flags it is measured under is
# manipulating the measurement, not optimizing the program.
if(DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")
endif()
if(DEFINED ENV{LDFLAGS})
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")
endif()

add_library(sortedindex STATIC src/sortedindex.c src/keycmp.c)
target_include_directories(sortedindex PUBLIC include PRIVATE src)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE sortedindex)

add_executable(test_sortedindex tests/test_sortedindex.c)
target_link_libraries(test_sortedindex PRIVATE sortedindex)

add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE sortedindex)

enable_testing()
add_test(NAME unit COMMAND test_sortedindex)
