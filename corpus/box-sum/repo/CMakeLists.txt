cmake_minimum_required(VERSION 3.16)
project(boxsum C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")
endif()
if(DEFINED ENV{LDFLAGS})
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")
endif()

add_library(boxsum STATIC src/boxsum.c src/cell.c)
target_include_directories(boxsum PUBLIC include PRIVATE src)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE boxsum)
add_executable(test_boxsum tests/test_boxsum.c)
target_link_libraries(test_boxsum PRIVATE boxsum)
add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE boxsum)

enable_testing()
add_test(NAME unit COMMAND test_boxsum)
