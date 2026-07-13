cmake_minimum_required(VERSION 3.14)
project(MQSimPybind VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release" CACHE STRING "" FORCE)
endif()
message(STATUS "MQSim pybind11 build type: ${CMAKE_BUILD_TYPE}")

# ------------------------------------------------------------------
# Paths — MQSim source is a submodule, treated as read-only
# ------------------------------------------------------------------
set(MQSIM_SRC_DIR ${CMAKE_CURRENT_SOURCE_DIR}/MQSim/src)

# ------------------------------------------------------------------
# Python + pybind11
# ------------------------------------------------------------------
find_package(Python 3.10 REQUIRED COMPONENTS Interpreter Development.Module)

include(FetchContent)
message(STATUS "Configuring pybind11...")
FetchContent_Declare(
    pybind11
    GIT_REPOSITORY https://github.com/pybind/pybind11.git
    GIT_TAG        v2.12.0
    SOURCE_DIR     ${CMAKE_CURRENT_SOURCE_DIR}/ext/pybind11
)
FetchContent_MakeAvailable(pybind11)
message(STATUS "Done configuring pybind11.")

# ------------------------------------------------------------------
# MQSim source files (glob from MQSim/src, excluding main.cpp)
# ------------------------------------------------------------------
file(GLOB_RECURSE MQSIM_SOURCES
    ${MQSIM_SRC_DIR}/exec/*.cpp
    ${MQSIM_SRC_DIR}/host/*.cpp
    ${MQSIM_SRC_DIR}/nvm_chip/*.cpp
    ${MQSIM_SRC_DIR}/nvm_chip/flash_memory/*.cpp
    ${MQSIM_SRC_DIR}/sim/*.cpp
    ${MQSIM_SRC_DIR}/ssd/*.cpp
    ${MQSIM_SRC_DIR}/utils/*.cpp
)

# ------------------------------------------------------------------
# MQSim static library
# ------------------------------------------------------------------
add_library(mqsim_lib STATIC ${MQSIM_SOURCES})
target_include_directories(mqsim_lib PUBLIC
    ${MQSIM_SRC_DIR}
    ${MQSIM_SRC_DIR}/exec
    ${MQSIM_SRC_DIR}/host
    ${MQSIM_SRC_DIR}/nvm_chip
    ${MQSIM_SRC_DIR}/nvm_chip/flash_memory
    ${MQSIM_SRC_DIR}/sim
    ${MQSIM_SRC_DIR}/ssd
    ${MQSIM_SRC_DIR}/utils
)

# ------------------------------------------------------------------
# pybind11 module (_mqsim)
# ------------------------------------------------------------------
pybind11_add_module(_mqsim mqsim_pybind.cpp)
target_link_libraries(_mqsim PRIVATE mqsim_lib)
target_include_directories(_mqsim PRIVATE
    ${MQSIM_SRC_DIR}
    ${MQSIM_SRC_DIR}/exec
    ${MQSIM_SRC_DIR}/host
    ${MQSIM_SRC_DIR}/nvm_chip
    ${MQSIM_SRC_DIR}/nvm_chip/flash_memory
    ${MQSIM_SRC_DIR}/sim
    ${MQSIM_SRC_DIR}/ssd
    ${MQSIM_SRC_DIR}/utils
)

# Output _mqsim.so/.pyd next to the pymqsim Python package
set_target_properties(_mqsim PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/pymqsim
)
