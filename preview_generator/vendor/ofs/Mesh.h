#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

namespace ofs::sg {

class Mesh {
    uint32_t vao{};
    uint32_t vbo{};
    uint32_t ebo{};
    int32_t indexCount{};

    static Mesh uploadToGPU(const float* vertices, size_t vertexCount, const uint32_t* indices, size_t indexCount);

  public:
    Mesh() = default;
    ~Mesh();

    Mesh(const Mesh &) = delete;
    Mesh &operator=(const Mesh &) = delete;
    Mesh(Mesh &&) noexcept;
    Mesh &operator=(Mesh &&) noexcept;

    [[nodiscard]] static Mesh cube();
    [[nodiscard]] static Mesh fromData(const std::vector<float>& interleavedVertices, const std::vector<uint32_t>& indices);
    void draw() const;
};

} // namespace ofs::sg
