#pragma once
// Standalone replacements for OFS application logging and UTF-8 file I/O.
#include <cstdio>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <vector>
#define LOGF_ERROR(...) do { std::fprintf(stderr, __VA_ARGS__); std::fputc('\n', stderr); } while (0)
#define LOGF_INFO(...) LOGF_ERROR(__VA_ARGS__)
namespace Util {
inline std::ptrdiff_t ReadFile(const char* path, std::vector<uint8_t>& bytes) {
    std::ifstream input(path, std::ios::binary);
    if (!input) return -1;
    bytes.assign(std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>());
    return static_cast<std::ptrdiff_t>(bytes.size());
}
}
