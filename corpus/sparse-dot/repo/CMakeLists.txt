cmake_minimum_required(VERSION 3.16)
project(sparsedot C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")
endif()
if(DEFINED ENV{LDFLAGS})
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")
endif()

add_library(sparsedot STATIC src/sparsedot.c src/madd.c)
target_include_directories(sparsedot PUBLIC include PRIVATE src)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE sparsedot)
add_executable(test_sparsedot tests/test_sparsedot.c)
target_link_libraries(test_sparsedot PRIVATE sparsedot)
add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE sparsedot)

enable_testing()
add_test(NAME unit COMMAND test_sparsedot)
