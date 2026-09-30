#include "SceneGraph.h"
#include <algorithm>
#include <glad/gl.h>

namespace ofs::sg {

SceneNode *SceneGraph::createNode(SceneNode *parent) {
    auto node = std::make_unique<SceneNode>();
    SceneNode *ptr = node.get();
    if (parent != nullptr) {
        ptr->parent = parent;
        parent->children.push_back(ptr);
    }
    nodes_.push_back(std::move(node));
    return ptr;
}

void SceneGraph::setParent(SceneNode *node, SceneNode *parent) {
    if (node == nullptr || node == parent || node->parent == parent)
        return;

    if (node->parent != nullptr) {
        auto &siblings = node->parent->children;
        siblings.erase(std::remove(siblings.begin(), siblings.end(), node), siblings.end());
    }

    node->parent = parent;
    if (parent != nullptr)
        parent->children.push_back(node);
}

void SceneGraph::destroyNode(SceneNode *node) {
    // Recurse over a snapshot: each child's destroyNode erases itself from node->children (through its
    // parent pointer below), which would invalidate a live range-for over the same vector.
    const std::vector<SceneNode *> children = node->children;
    for (SceneNode *child : children)
        destroyNode(child);

    if (node->parent != nullptr) {
        auto &siblings = node->parent->children;
        siblings.erase(std::remove(siblings.begin(), siblings.end(), node), siblings.end());
    }

    nodes_.erase(std::remove_if(nodes_.begin(), nodes_.end(),
                     [node](const std::unique_ptr<SceneNode> &n) { return n.get() == node; }),
        nodes_.end());
}

void SceneGraph::updateRecursive(SceneNode *node, const glm::mat4 &parentWorld) {
    node->worldMatrix = parentWorld * node->localTransform.localMatrix();
    for (SceneNode *child : node->children)
        updateRecursive(child, node->worldMatrix);
}

void SceneGraph::updateTransforms() {
    for (const auto &node : nodes_) {
        if (node->parent == nullptr)
            updateRecursive(node.get(), glm::mat4(1.0f));
    }
}

void SceneGraph::render(const glm::mat4 &view, const glm::mat4 &proj) {
    // ImGui's GL3 backend leaves face culling disabled; enable it for the solid model so back faces
    // don't bleed through and muddy the shading. Save/restore because the host callback that wraps this
    // render only restores viewport/scissor/depth state, not cull state.
    const GLboolean prevCull = glIsEnabled(GL_CULL_FACE);
    const GLboolean prevDepth = glIsEnabled(GL_DEPTH_TEST);
    GLint prevDepthFunc = GL_LESS;
    glGetIntegerv(GL_DEPTH_FUNC, &prevDepthFunc);
    GLint prevFrontFace = GL_CCW;
    glGetIntegerv(GL_FRONT_FACE, &prevFrontFace);
    glEnable(GL_CULL_FACE);
    glCullFace(GL_BACK);
    glFrontFace(GL_CCW); // glTF winding

    shader.use();
    const glm::vec3 eye = glm::vec3(glm::inverse(view)[3]);
    shader.setEye(eye);
    shader.setRefDist(glm::length(eye)); // all simulator cameras target the origin, so |eye| is eye->focus
    glEnable(GL_DEPTH_TEST);
    for (const auto &node : nodes_) {
        if (node->mesh == nullptr)
            continue;
        const auto mvp = proj * view * node->worldMatrix;
        shader.setMVP(mvp);
        shader.setModel(node->worldMatrix);
        shader.setColor(node->color);
        node->mesh->draw();
    }

    glFrontFace(static_cast<GLenum>(prevFrontFace));
    if (prevCull == GL_FALSE)
        glDisable(GL_CULL_FACE);
    glDepthFunc(static_cast<GLenum>(prevDepthFunc));
    if (prevDepth == GL_FALSE)
        glDisable(GL_DEPTH_TEST);
    else
        glEnable(GL_DEPTH_TEST);
}

} // namespace ofs::sg
