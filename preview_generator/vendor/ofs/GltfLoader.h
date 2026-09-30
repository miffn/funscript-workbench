#pragma once

#include "Mesh.h"
#include "SceneGraph.h"

#include <cstddef>
#include <glm/glm.hpp>
#include <memory>
#include <string>
#include <vector>

namespace ofs::sg {

struct GltfScene
{
    std::vector<std::unique_ptr<Mesh>> meshes;
    std::vector<SceneNode*> roots; // top-level nodes owned by the SceneGraph, not this struct
    glm::vec3 boundsMin{0.0f};
    glm::vec3 boundsMax{0.0f};
    bool hasBounds = false;
};

[[nodiscard]] std::unique_ptr<GltfScene> loadGltf(SceneGraph& graph, const std::string& path);
[[nodiscard]] std::unique_ptr<GltfScene> loadGltfFromMemory(SceneGraph& graph, const unsigned char* data, size_t size,
    const std::string& label);

} // namespace ofs::sg
