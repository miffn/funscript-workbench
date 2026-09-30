static ofs::sg::Mesh CreateGodotCylinderMesh(int segments = 96)
{
    constexpr float radius = kEmbeddedCylinderRadius;
    constexpr float halfHeight = kEmbeddedCylinderHalfHeight;
    constexpr float twoPi = 6.28318530717958647692f;
    std::vector<float> vertices;
    std::vector<uint32_t> indices;

    auto addVertex = [&](float x, float y, float z, float nx, float ny, float nz) -> uint32_t {
        vertices.insert(vertices.end(), { x, y, z, nx, ny, nz, 1.f, 1.f, 1.f });
        return static_cast<uint32_t>(vertices.size() / 9 - 1);
    };

    for(int i = 0; i <= segments; i++)
    {
        const float a = (static_cast<float>(i) / static_cast<float>(segments)) * twoPi;
        const float x = std::cos(a) * radius;
        const float z = std::sin(a) * radius;
        const float nx = std::cos(a);
        const float nz = std::sin(a);
        addVertex(x, halfHeight, z, nx, 0.f, nz);
        addVertex(x, -halfHeight, z, nx, 0.f, nz);
    }
    for(int i = 0; i < segments; i++)
    {
        const uint32_t top0 = static_cast<uint32_t>(i * 2);
        const uint32_t bot0 = top0 + 1;
        const uint32_t top1 = top0 + 2;
        const uint32_t bot1 = top0 + 3;
        indices.insert(indices.end(), { top0, bot1, bot0, top0, top1, bot1 });
    }

    const uint32_t topCenter = addVertex(0.f, halfHeight, 0.f, 0.f, 1.f, 0.f);
    const uint32_t bottomCenter = addVertex(0.f, -halfHeight, 0.f, 0.f, -1.f, 0.f);
    for(int i = 0; i <= segments; i++)
    {
        const float a = (static_cast<float>(i) / static_cast<float>(segments)) * twoPi;
        addVertex(std::cos(a) * radius, halfHeight, std::sin(a) * radius, 0.f, 1.f, 0.f);
        addVertex(std::cos(a) * radius, -halfHeight, std::sin(a) * radius, 0.f, -1.f, 0.f);
    }
    const uint32_t capStart = topCenter + 2;
    for(int i = 0; i < segments; i++)
    {
        const uint32_t top0 = capStart + static_cast<uint32_t>(i * 2);
        const uint32_t bot0 = top0 + 1;
        const uint32_t top1 = top0 + 2;
        const uint32_t bot1 = top0 + 3;
        indices.insert(indices.end(), { topCenter, top1, top0, bottomCenter, bot0, bot1 });
    }

    return ofs::sg::Mesh::fromData(vertices, indices);
}


