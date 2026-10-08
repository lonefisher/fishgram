cmake_minimum_required(VERSION 3.25)
project(FishGramUpdateTests LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded")
string(REPLACE "/DNDEBUG" "" CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE}")
find_package(Qt5 REQUIRED COMPONENTS Core)
add_executable(feed_tests
    "${SOURCE_ROOT}/Telegram/Tests/fishgram_update/feed_tests.cpp"
    "${SOURCE_ROOT}/Telegram/SourceFiles/core/fishgram_update_feed.cpp")
target_include_directories(feed_tests PRIVATE "${SOURCE_ROOT}/Telegram/SourceFiles")
target_link_libraries(feed_tests PRIVATE Qt5::Core)
target_compile_options(feed_tests PRIVATE /W4 /WX /UNDEBUG)
set(OPENSSL_ROOT "${DEPENDENCY_ROOT}/Libraries/win64/openssl3")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/update_keys_data.h" "#pragma once\nnamespace Core::Updates::details {}\n")
add_executable(verify_tests
    "${SOURCE_ROOT}/Telegram/SourceFiles/tests/test_update_verify.cpp"
    "${SOURCE_ROOT}/Telegram/SourceFiles/core/update_verify.cpp"
    "${SOURCE_ROOT}/Telegram/SourceFiles/core/update_keys.cpp")
target_include_directories(verify_tests PRIVATE "${SOURCE_ROOT}/Telegram/SourceFiles" "${OPENSSL_ROOT}/include" "${CMAKE_CURRENT_BINARY_DIR}")
target_link_libraries(verify_tests PRIVATE Qt5::Core "${OPENSSL_ROOT}/out/libcrypto.lib" crypt32 ws2_32)
target_compile_options(verify_tests PRIVATE /W4 /WX /UNDEBUG)
add_executable(packer_tests
    "${SOURCE_ROOT}/Telegram/SourceFiles/_other/packer.cpp"
    "${SOURCE_ROOT}/Telegram/SourceFiles/core/update_verify.cpp")
target_include_directories(packer_tests PRIVATE "${SOURCE_ROOT}/Telegram/SourceFiles" "${OPENSSL_ROOT}/include"
    "${DEPENDENCY_ROOT}/Libraries/win64/lzma/C" "${DEPENDENCY_ROOT}/Libraries/win64/zlib")
target_link_libraries(packer_tests PRIVATE Qt5::Core "${OPENSSL_ROOT}/out/libcrypto.lib"
    "${DEPENDENCY_ROOT}/Libraries/win64/lzma/C/Util/LzmaLib/x64/Release/LzmaLib.lib" crypt32 ws2_32)
target_compile_options(packer_tests PRIVATE /W4 /WX /wd4996 /wd4100 /wd4244 /wd4267)
add_executable(package_verify
    "${SOURCE_ROOT}/Telegram/Tests/fishgram_update/package_verify.cpp"
    "${SOURCE_ROOT}/Telegram/SourceFiles/core/update_verify.cpp")
target_include_directories(package_verify PRIVATE "${SOURCE_ROOT}/Telegram/SourceFiles"
    "${OPENSSL_ROOT}/include" "${DEPENDENCY_ROOT}/Libraries/win64/lzma/C")
target_link_libraries(package_verify PRIVATE Qt5::Core "${OPENSSL_ROOT}/out/libcrypto.lib"
    "${DEPENDENCY_ROOT}/Libraries/win64/lzma/C/Util/LzmaLib/x64/Release/LzmaLib.lib" crypt32 ws2_32)
target_compile_options(package_verify PRIVATE /W4 /WX /UNDEBUG)
add_executable(updater_compile WIN32
    "${SOURCE_ROOT}/Telegram/SourceFiles/_other/updater_win.cpp"
    "${SOURCE_ROOT}/Telegram/lib_base/base/platform/win/base_windows_safe_library.cpp")
target_include_directories(updater_compile PRIVATE "${SOURCE_ROOT}/Telegram/SourceFiles"
    "${SOURCE_ROOT}/Telegram/lib_base" "${SOURCE_ROOT}/Telegram/ThirdParty/GSL/include")
target_compile_definitions(updater_compile PRIVATE FISHGRAM_BASE_VERSION=7002009 FISHGRAM_REVISION=9
    TDESKTOP_UPDATE_CHANNEL=0 NOMINMAX UNICODE _UNICODE)
target_compile_options(updater_compile PRIVATE /W4 /WX /wd4100 /wd4244 /wd4267 /wd4996 /wd4456 /wd4505)
target_link_libraries(updater_compile PRIVATE Qt5::Core dbghelp shlwapi shell32 ole32 advapi32 user32 uuid)
set_source_files_properties("${SOURCE_ROOT}/Telegram/lib_base/base/platform/win/base_windows_safe_library.cpp"
    PROPERTIES COMPILE_OPTIONS /wd4459)
if(TEST_TRUST_DIRECTORY)
    file(MAKE_DIRECTORY "${CMAKE_CURRENT_BINARY_DIR}/embedded")
    execute_process(COMMAND "${CMAKE_COMMAND}"
        "-Droot_pem=${TEST_TRUST_DIRECTORY}/root-public.pem"
        "-Dmanifest=${TEST_TRUST_DIRECTORY}/manifest.min.json"
        "-Dmanifest_sig=${TEST_TRUST_DIRECTORY}/manifest.sig"
        "-Doutput=${CMAKE_CURRENT_BINARY_DIR}/embedded/update_keys_data.h"
        -P "${SOURCE_ROOT}/Telegram/cmake/update_keys_header.cmake" COMMAND_ERROR_IS_FATAL ANY)
    add_executable(verify_embedded
        "${SOURCE_ROOT}/Telegram/SourceFiles/tests/test_update_verify.cpp"
        "${SOURCE_ROOT}/Telegram/SourceFiles/core/update_verify.cpp"
        "${SOURCE_ROOT}/Telegram/SourceFiles/core/update_keys.cpp")
    target_include_directories(verify_embedded PRIVATE "${SOURCE_ROOT}/Telegram/SourceFiles"
        "${OPENSSL_ROOT}/include" "${CMAKE_CURRENT_BINARY_DIR}/embedded")
    target_compile_definitions(verify_embedded PRIVATE FISHGRAM_UPDATE_TRUST_CONFIGURED=1)
    target_link_libraries(verify_embedded PRIVATE Qt5::Core "${OPENSSL_ROOT}/out/libcrypto.lib" crypt32 ws2_32)
    target_compile_options(verify_embedded PRIVATE /W4 /WX /UNDEBUG)
endif()
