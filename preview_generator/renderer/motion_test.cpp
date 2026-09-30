#include "motion.h"
#include <cmath>
#include <iostream>
#include <stdexcept>
void near(float a, float b) { if (std::abs(a-b) > 0.0001f) throw std::runtime_error("motion mismatch"); }
int main() {
    const auto neutral = preview::evaluate({.5f,.5f,.5f,.5f,.5f,.5f});
    near(neutral.position.x,0); near(neutral.position.y,0); near(neutral.position.z,0); near(neutral.rotation.w,1);
    const auto position = preview::evaluate({1,1,0,.5f,.5f,.5f});
    near(position.position.x,.5f); near(position.position.y,1); near(position.position.z,-.5f);
    // Pitch has OFS's reversed direction: 0 -> +60 degrees, 100 -> -60.
    const auto pitch = preview::evaluate({.5f,.5f,.5f,.5f,.5f,0});
    const auto up = pitch.rotation * glm::vec3{0,1,0};
    near(up.y,.5f); near(up.z,std::sqrt(3.f)/2.f);
    // All three rotations combine in the OFS order, rather than overwriting each other.
    const auto composite = preview::evaluate({.5f,.5f,.5f,1,1,0});
    const auto expected = glm::angleAxis(glm::radians(-30.f),glm::vec3{0,0,1}) *
        glm::angleAxis(glm::radians(60.f),glm::vec3{1,0,0}) *
        glm::angleAxis(glm::radians(135.f),glm::vec3{0,1,0});
    near(composite.rotation.x,expected.x); near(composite.rotation.y,expected.y);
    near(composite.rotation.z,expected.z); near(composite.rotation.w,expected.w);
    std::cout << "six-axis OFS motion matches\n";
}
