# SPDX-License-Identifier: GPL-3.0-or-later
cmake_minimum_required(VERSION 3.20)
project(spatial_companion_qml_smoke LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
find_package(Qt6 6.6 REQUIRED COMPONENTS Quick Qml Test DBus)
find_package(KF6I18n 6.8 REQUIRED)

set(SPATIAL_COMPANION_QML_DIR "" CACHE PATH "Patched Companion contents/ui directory")
foreach(card SpatialControls PlaybackControls LiveAudioControls)
    if(NOT EXISTS "${SPATIAL_COMPANION_QML_DIR}/${card}.qml")
        message(FATAL_ERROR "Missing patched Companion component: ${card}.qml")
    endif()
endforeach()

add_executable(spatial-qml-smoke qml-smoke.cpp)
target_link_libraries(spatial-qml-smoke PRIVATE
    Qt6::Quick Qt6::Qml Qt6::Test Qt6::DBus KF6::I18nQml)
target_compile_definitions(spatial-qml-smoke PRIVATE
    SPATIAL_COMPANION_QML_DIR="${SPATIAL_COMPANION_QML_DIR}")
