#pragma once

#include <algorithm>
#include <cmath>

namespace OFS_EmbeddedSimulator3DMath
{
inline float PitchAngleDegrees(float normalizedPitch, float maximumAngleDegrees) noexcept
{
	const float pitch = std::clamp(normalizedPitch, 0.f, 1.f);
	const float range = std::clamp(std::abs(maximumAngleDegrees), 0.f, 90.f);
	return range + (-range - range) * pitch;
}

inline float GeometricTravelPercent(float deltaX, float deltaY, float deltaZ,
	float fullTravelDistance) noexcept
{
	const float referenceDistance = std::max(std::abs(fullTravelDistance), 0.0001f);
	const float distance = std::sqrt(deltaX * deltaX + deltaY * deltaY + deltaZ * deltaZ);
	return (distance / referenceDistance) * 100.f;
}
}
