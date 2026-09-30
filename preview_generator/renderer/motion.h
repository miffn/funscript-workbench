#pragma once
// Motion mapping extracted from OFS_EmbeddedSimulator3D::updateModelTransform.
#include "OFS_EmbeddedSimulator3DMath.h"
#include <array>
#include <algorithm>
#include <glm/gtc/quaternion.hpp>
namespace preview {
using Axes = std::array<float, 6>; // stroke, surge, sway, twist, roll, pitch
struct Motion {
    glm::vec3 position;
    glm::quat rotation;
};
inline Motion evaluate(const Axes& a, float pitchRange = 60.f) {
    const glm::vec3 position{0.5f - a[2], -1.f + 2.f*a[0], 0.5f - a[1]};
    const float pitch = glm::radians(OFS_EmbeddedSimulator3DMath::PitchAngleDegrees(a[5], pitchRange));
    const float roll = glm::radians(-30.f + 60.f*a[4]);
    const float twist = glm::radians(-135.f + 270.f*a[3]);
    const auto rotation = glm::angleAxis(-roll, glm::vec3{0,0,1}) *
                          glm::angleAxis(pitch, glm::vec3{1,0,0}) *
                          glm::angleAxis(twist, glm::vec3{0,1,0});
    return {position, rotation};
}
}
