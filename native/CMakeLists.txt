cmake_minimum_required(VERSION 3.20)
project(quicksdf_core LANGUAGES CXX)

add_library(quicksdf_core SHARED quicksdf_core.cpp)
target_compile_features(quicksdf_core PRIVATE cxx_std_17)
if(MSVC)
  target_compile_options(quicksdf_core PRIVATE /O2 /W4 /permissive- /EHsc /Brepro)
  target_link_options(quicksdf_core PRIVATE /Brepro)
endif()
set_target_properties(quicksdf_core PROPERTIES
  RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
  RUNTIME_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin"
  LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
  LIBRARY_OUTPUT_DIRECTORY_RELEASE "${CMAKE_BINARY_DIR}/bin"
  ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)
