cmake_minimum_required(VERSION 3.20)
project(ofs_preview_renderer VERSION 0.2.0 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(OpenGL REQUIRED COMPONENTS EGL OpenGL)
find_package(glm REQUIRED)
add_library(ofs_scene STATIC
  vendor/ofs/Mesh.cpp vendor/ofs/SceneGraph.cpp vendor/ofs/SceneShader.cpp
  vendor/ofs/Shader.cpp vendor/ofs/GltfLoader.cpp vendor/glad/gl.c)
target_include_directories(ofs_scene PUBLIC vendor/ofs vendor/glad/include)
target_compile_definitions(ofs_scene PUBLIC GLM_FORCE_CTOR_INIT)
target_link_libraries(ofs_scene PUBLIC glm::glm)
add_executable(ofs-preview-renderer renderer/main.cpp)
add_library(preview_imgui STATIC vendor/imgui/imgui.cpp vendor/imgui/imgui_draw.cpp
  vendor/imgui/imgui_tables.cpp vendor/imgui/imgui_widgets.cpp
  vendor/imgui/backends/imgui_impl_opengl3.cpp)
target_include_directories(preview_imgui PUBLIC vendor/imgui vendor/imgui/backends)
target_link_libraries(preview_imgui PRIVATE ${CMAKE_DL_LIBS})
target_link_libraries(ofs-preview-renderer PRIVATE ofs_scene preview_imgui OpenGL::EGL OpenGL::OpenGL)
target_compile_options(ofs-preview-renderer PRIVATE -Wall -Wextra -Wpedantic)
enable_testing()
add_executable(motion-test renderer/motion_test.cpp)
target_link_libraries(motion-test PRIVATE ofs_scene)
add_test(NAME six-axis-motion COMMAND motion-test)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
add_test(NAME headless-renderer COMMAND ${Python3_EXECUTABLE}
  ${CMAKE_CURRENT_SOURCE_DIR}/renderer/verify_renderer.py
  $<TARGET_FILE:ofs-preview-renderer>
  ${CMAKE_CURRENT_SOURCE_DIR}/models/blonde-chibi-simulator-ofs.glb)
set_tests_properties(headless-renderer PROPERTIES TIMEOUT 60)
