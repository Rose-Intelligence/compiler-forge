cmake_minimum_required(VERSION 3.16)
project(csvcut C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")
endif()
if(DEFINED ENV{LDFLAGS})
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")
endif()

add_library(csvcut STATIC src/csvcut.c src/scan.c)
target_include_directories(csvcut PUBLIC include PRIVATE src)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE csvcut)

add_executable(test_csvcut tests/test_csvcut.c)
target_link_libraries(test_csvcut PRIVATE csvcut)

add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE csvcut)

enable_testing()
add_test(NAME unit COMMAND test_csvcut)
