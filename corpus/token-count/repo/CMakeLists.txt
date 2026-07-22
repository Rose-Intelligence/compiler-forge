cmake_minimum_required(VERSION 3.16)
project(tokencount C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# The validator owns the optimization level and the sanitizer selection; the
# build definition must not override what arrives in the environment.
if(DEFINED ENV{CFLAGS})
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} $ENV{CFLAGS}")
endif()
if(DEFINED ENV{LDFLAGS})
    set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} $ENV{LDFLAGS}")
endif()

add_library(tokencount STATIC src/tokencount.c)
target_include_directories(tokencount PUBLIC include)

add_executable(bench bench/bench.c)
target_link_libraries(bench PRIVATE tokencount)

add_executable(test_tokencount tests/test_tokencount.c)
target_link_libraries(test_tokencount PRIVATE tokencount)

add_executable(differential tests/differential.c)
target_link_libraries(differential PRIVATE tokencount)

enable_testing()
add_test(NAME unit COMMAND test_tokencount)
